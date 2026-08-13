"""EIA Hourly Electric Grid Monitor (Form EIA-930) API client.

EIA independently collects hourly net generation by energy source for every
U.S. balancing authority (July 2018 onward) and publishes it through the
open API (https://www.eia.gov/opendata/, free key, EIA_API_KEY env var).
This module is used three ways:

1. Automatic gap filling (src/pipeline.py): any interior missing day from
   2018-07-01 on that the ISO's own source cannot produce is filled with
   real EIA measurements - same quantity, independently metered.
2. The US48 lower-48 national reference series (iso='US48' rows), shown as
   an overlay on the dashboard's National Trend so the tracked-ISO total
   can be read against the true national total.
3. The reconciliation report (scripts/reconcile_eia.py): per-ISO comparison
   of our connector-derived daily totals against EIA's, to catch unit and
   aggregation errors.

Routes: prefers /electricity/rto/daily-fuel-type-data (daily MWh, exactly
our grain; faceted by timezone) and falls back to /electricity/rto/
fuel-type-data (hourly MWh, UTC periods) if the daily route ever changes -
hourly values are summed into local days using each BA's fixed standard-time
offset (the 1-hour DST error this ignores misallocates <0.2% of a day's
energy between two adjacent days, twice a year).

EIA fuel codes are coarser than some ISO feeds: petroleum (OIL) and
unknown (UNK) land in imports_other, pumped storage may be inside WAT
(hydro) depending on vintage, and battery storage (BAT) only exists in
recent years. Codes are mapped defensively; anything unrecognized goes to
imports_other rather than being dropped.
"""
from __future__ import annotations

import datetime as dt
import os
import time

import pandas as pd

from src.connectors.base import http_session

API_BASE = "https://api.eia.gov/v2/electricity/rto"
EIA_EARLIEST = dt.date(2018, 7, 1)
_PAGE_SIZE = 5000
_REQUEST_DELAY_SECONDS = 0.25

# Our ISO code -> EIA-930 respondent (balancing authority) code, plus the
# US48 lower-48 aggregate. Timezones are the BA's EIA-930 reporting zone
# (used to pick one row-set on the daily route and as the fixed offset on
# the hourly fallback).
RESPONDENTS = {
    "CAISO": ("CISO", "Pacific", -8),
    "ERCOT": ("ERCO", "Central", -6),
    "ISONE": ("ISNE", "Eastern", -5),
    "MISO": ("MISO", "Eastern", -5),  # MISO reports in EST year-round
    "NYISO": ("NYIS", "Eastern", -5),
    "PJM": ("PJM", "Eastern", -5),
    "SPP": ("SWPP", "Central", -6),
    "US48": ("US48", "Eastern", -5),
}

# Codes and official names verified against the live US48 feed 2026-08-07
# (scripts/diagnose_storage.py section 4). OES/UES/WNB were absent from this
# map before that and were silently landing in imports_other - UES ("Unknown
# energy storage") alone carried -28 GWh over a five-day window, i.e. real
# charging energy booked as "other".
_FUELTYPE_TO_CANONICAL = {
    "COL": "coal",
    "NG": "natural_gas",
    "NUC": "nuclear",
    "WAT": "hydro",
    "SUN": "solar",
    "WND": "wind",
    "BAT": "battery",   # Battery storage
    "PS": "hydro",      # Pumped storage - hydro, same as every ISO feed files it
    "OES": "battery",   # Other energy storage
    "UES": "battery",   # Unknown energy storage
    "GEO": "other_renewables",
    "BIO": "other_renewables",
    "SNB": "solar",     # Solar with integrated battery storage
    "WNB": "wind",      # Wind with integrated battery storage
    "OIL": "imports_other",
    "OTH": "imports_other",
    "UNK": "imports_other",
}

# Every code EIA-930 uses for a storage RESOURCE. All of them are net of
# charging, so all of them have to be clipped per hour rather than per day
# (see src/schema.py) - including PS, which lands in hydro but is still a
# machine that consumes energy half the time.
_STORAGE_CODES = ["BAT", "PS", "OES", "UES"]

# Where each of those codes belongs once clipped. PS is pumped hydro: the ISO
# feeds all report it inside their hydro column, and it is 43% of EIA's
# storage-coded discharge, so leaving it in the battery bucket made EIA's
# battery number incomparable with every ISO's (measured 2026-08-13,
# scripts/diagnose_storage.py section 3).
_STORAGE_CODE_BUCKET = {code: _FUELTYPE_TO_CANONICAL[code] for code in _STORAGE_CODES}

# EIA-930 publishes balancing authorities AND rolled-up regions through the
# same `respondent` facet, so an unfiltered query returns the country several
# times over. These are the aggregates; everything else is a real BA.
AGGREGATE_RESPONDENTS = {
    "US48", "CAL", "CAR", "CENT", "FLA", "MIDA", "MIDW",
    "NE", "NW", "NY", "SE", "SW", "TEN", "TEX",
}

# Respondents that report no battery series to EIA-930 at all. Their fleets
# are missing from the national figure entirely - CISO's most of all, the
# largest in the country - which is why the national battery line is a floor,
# not a total. Verified 2026-08-13; revisit if EIA starts publishing them.
NO_BATTERY_SERIES_AT_EIA = ("CISO", "PJM", "NYIS")


def has_key() -> bool:
    return bool(os.environ.get("EIA_API_KEY"))


def _bucket(code: str) -> str:
    return _FUELTYPE_TO_CANONICAL.get(str(code).strip().upper(), "imports_other")


def _get_pages(session, route: str, params: dict) -> list[dict]:
    """Paginated GET against an EIA v2 data route. Raises on HTTP errors so
    callers can decide whether to fall back or skip."""
    key = os.environ.get("EIA_API_KEY")
    if not key:
        raise RuntimeError("EIA_API_KEY not set")
    rows: list[dict] = []
    offset = 0
    while True:
        page_params = dict(params, api_key=key, length=_PAGE_SIZE, offset=offset)
        resp = session.get(f"{API_BASE}/{route}/data/", params=page_params, timeout=60)
        resp.raise_for_status()
        payload = resp.json()
        page = ((payload or {}).get("response") or {}).get("data") or []
        rows.extend(page)
        if len(page) < _PAGE_SIZE:
            return rows
        offset += _PAGE_SIZE
        time.sleep(_REQUEST_DELAY_SECONDS)


def _daily_route(session, respondent: str, timezone: str, start: dt.date, end: dt.date) -> pd.DataFrame:
    rows = _get_pages(
        session,
        "daily-fuel-type-data",
        {
            "frequency": "daily",
            "data[0]": "value",
            "facets[respondent][]": respondent,
            "facets[timezone][]": timezone,
            "start": start.isoformat(),
            "end": end.isoformat(),
        },
    )
    if not rows:
        return pd.DataFrame(columns=["date", "fuel_category", "generation_mwh"])
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["period"], errors="coerce").dt.date
    df["mwh"] = pd.to_numeric(df["value"], errors="coerce")
    df = df.dropna(subset=["date", "mwh"])
    # Every storage code is dropped here and rebuilt from the hourly route by
    # the caller. A daily value is already netted over the day, and net cannot
    # be turned back into discharge. That applies to PS as much as to BAT:
    # PS lands in hydro, but taking it from this route would quietly add a
    # net-convention number to a gross-generation bucket.
    df = df[~df["fueltype"].astype(str).str.strip().str.upper().isin(_STORAGE_CODES)]
    if df.empty:
        return pd.DataFrame(columns=_EMPTY)
    df["fuel_category"] = df["fueltype"].map(_bucket)
    # Values are already daily MWh; multiple EIA codes sharing a canonical
    # bucket are simply summed (energy sums - no averaging trap here).
    out = df.groupby(["date", "fuel_category"], as_index=False)["mwh"].sum()
    return out.rename(columns={"mwh": "generation_mwh"})


_EMPTY = ["date", "fuel_category", "generation_mwh"]


def _storage_rows(session, start: dt.date, end: dt.date, respondent: str | None) -> pd.DataFrame:
    """Raw hourly storage-code readings, padded a day each side so the
    UTC->local shift doesn't clip boundary hours. `respondent=None` asks for
    every respondent at once (the national path)."""
    params = {
        "frequency": "hourly",
        "data[0]": "value",
        "facets[fueltype][]": _STORAGE_CODES,  # requests repeats the key per code
        "start": f"{(start - dt.timedelta(days=1)).isoformat()}T00",
        "end": f"{(end + dt.timedelta(days=1)).isoformat()}T23",
    }
    if respondent is not None:
        params["facets[respondent][]"] = respondent
    rows = _get_pages(session, "fuel-type-data", params)
    if not rows:
        return pd.DataFrame(columns=["respondent", "period", "code", "mwh"])
    df = pd.DataFrame(rows)
    df["code"] = df["fueltype"].astype(str).str.strip().str.upper()
    # Belt and braces: honour the facet client-side too, so a route that ever
    # ignores it cannot fold non-storage fuels into these numbers.
    df = df[df["code"].isin(_STORAGE_CODES)]
    if df.empty:
        return pd.DataFrame(columns=["respondent", "period", "code", "mwh"])
    df["mwh"] = pd.to_numeric(df["value"], errors="coerce")
    df["respondent"] = df["respondent"].astype(str).str.strip().str.upper()
    return df.dropna(subset=["mwh"])[["respondent", "period", "code", "mwh"]]


def _clip_to_daily(df: pd.DataFrame, utc_offset: int, start: dt.date, end: dt.date) -> pd.DataFrame:
    """Turn raw hourly storage readings into daily discharge per canonical
    bucket, clipping each (respondent, bucket, hour) at zero first.

    The grouping key is what makes this correct. Clipping per HOUR is what
    separates discharge from net. Clipping per RESPONDENT is what stops one
    BA's charging from cancelling another's discharge - the 21% national
    undercount in src/schema.py. Clipping per BUCKET is what keeps a pumping
    hour at Bath County from erasing a discharging hour of PJM batteries,
    since the two are now different fuels."""
    if df.empty:
        return pd.DataFrame(columns=_EMPTY)
    out = df.copy()
    out["fuel_category"] = out["code"].map(_STORAGE_CODE_BUCKET)
    ts = pd.to_datetime(out["period"], format="%Y-%m-%dT%H", errors="coerce")
    out["date"] = (ts + pd.Timedelta(hours=utc_offset)).dt.date
    out = out.dropna(subset=["date"])
    out = out[(out["date"] >= start) & (out["date"] <= end)]
    if out.empty:
        return pd.DataFrame(columns=_EMPTY)
    per_hour = out.groupby(
        ["respondent", "fuel_category", "date", "period"], as_index=False
    )["mwh"].sum()
    per_hour["mwh"] = per_hour["mwh"].clip(lower=0)
    daily = per_hour.groupby(["date", "fuel_category"], as_index=False)["mwh"].sum()
    return daily.rename(columns={"mwh": "generation_mwh"})[_EMPTY]


def _storage_daily(session, respondent: str, utc_offset: int, start: dt.date, end: dt.date) -> pd.DataFrame:
    """Daily battery discharge and pumped-hydro discharge for one respondent.

    The daily route hands back storage already netted over the day, and a net
    daily total cannot be turned back into discharge - so these are the two
    buckets we always rebuild from the hourly route. Faceting to the storage
    codes keeps it to a few hundred rows a day, small next to the daily route
    it supplements. Returns an empty frame when the respondent reports no
    storage at all (CISO, PJM and NYIS do not)."""
    return _clip_to_daily(_storage_rows(session, start, end, respondent), utc_offset, start, end)


def _national_storage_daily(session, utc_offset: int, start: dt.date, end: dt.date) -> pd.DataFrame:
    """Daily national battery and pumped-hydro discharge, summed from every
    balancing authority's OWN clipped series rather than read off EIA's
    pre-summed US48 one.

    EIA's US48 row is the country already added together, so clipping it
    charges the whole nation's charging against the whole nation's discharge
    and books only the difference - 21% of real discharge deleted (measured
    2026-08-13, scripts/diagnose_storage.py section 2). Summing per-BA
    discharge is the same quantity every ISO row in the database already
    holds, which is the only way the national line and the ISO lines can be
    read against each other.

    Local days use the US48 series' own Eastern offset for every BA, so a
    western BA's late-evening discharge can land on the following national
    day. That misallocates at most one hour of one fleet between two adjacent
    days and never changes a total - the same trade the hourly fallback route
    already makes for DST."""
    df = _storage_rows(session, start, end, None)
    if df.empty:
        return pd.DataFrame(columns=_EMPTY)
    df = df[~df["respondent"].isin(AGGREGATE_RESPONDENTS)]
    return _clip_to_daily(df, utc_offset, start, end)


def _hourly_route(session, respondent: str, utc_offset: int, start: dt.date, end: dt.date) -> pd.DataFrame:
    # Pad one day each side so the UTC->local shift doesn't clip boundary hours.
    rows = _get_pages(
        session,
        "fuel-type-data",
        {
            "frequency": "hourly",
            "data[0]": "value",
            "facets[respondent][]": respondent,
            "start": f"{(start - dt.timedelta(days=1)).isoformat()}T00",
            "end": f"{(end + dt.timedelta(days=1)).isoformat()}T23",
        },
    )
    if not rows:
        return pd.DataFrame(columns=["date", "fuel_category", "generation_mwh"])
    df = pd.DataFrame(rows)
    ts = pd.to_datetime(df["period"], format="%Y-%m-%dT%H", errors="coerce")
    df["date"] = (ts + pd.Timedelta(hours=utc_offset)).dt.date
    df["mwh"] = pd.to_numeric(df["value"], errors="coerce")
    df["code"] = df["fueltype"].astype(str).str.strip().str.upper()
    df = df.dropna(subset=["date", "mwh"])
    df = df[(df["date"] >= start) & (df["date"] <= end)]
    # Storage codes are discharge-only, so their hours are clipped before they
    # are summed into a day - every other bucket is already non-negative. The
    # clipped result is then merged back in, which is how PS reaches hydro
    # without carrying its pumping hours along.
    is_storage = df["code"].isin(_STORAGE_CODES)
    plain = df[~is_storage].copy()
    plain["fuel_category"] = plain["fueltype"].map(_bucket)
    clipped = _clip_to_daily(
        df.loc[is_storage, ["period", "code", "mwh"]].assign(respondent=respondent),
        utc_offset,
        start,
        end,
    )
    out = pd.concat([plain[_EMPTY[:2] + ["mwh"]].rename(columns={"mwh": "generation_mwh"}), clipped], ignore_index=True)
    return out.groupby(["date", "fuel_category"], as_index=False)["generation_mwh"].sum()


def fetch_battery_daily(iso: str, start: dt.date, end: dt.date) -> pd.DataFrame:
    """Daily battery discharge alone, for an ISO whose own feed cannot report
    it (ERCOT's settlement workbook has no storage column; SPP's and ISO-NE's
    feeds have no storage series at all).

    Only the battery bucket comes back. The same query also yields pumped
    hydro, and it is deliberately discarded here: these ISOs DO report hydro,
    with pumped storage already inside it, so adding EIA's PS would double
    count. This is the cheap route - one faceted hourly call - because the
    caller wants one bucket, not a whole mix."""
    respondent, _timezone, utc_offset = RESPONDENTS[iso]
    start = max(start, EIA_EARLIEST)
    if end < start:
        return pd.DataFrame(columns=_EMPTY)
    out = _storage_daily(http_session(), respondent, utc_offset, start, end)
    return out[out["fuel_category"] == "battery"].reset_index(drop=True)


def fetch_daily(iso: str, start: dt.date, end: dt.date) -> pd.DataFrame:
    """Daily MWh by canonical fuel bucket for one of our ISO codes (or
    'US48') over [start, end], from EIA-930. Returns an empty frame (with a
    printed warning) when EIA has nothing for the range; raises on auth/
    network errors so callers can distinguish outage from absence."""
    respondent, timezone, utc_offset = RESPONDENTS[iso]
    start = max(start, EIA_EARLIEST)
    if end < start:
        return pd.DataFrame(columns=["date", "fuel_category", "generation_mwh"])

    session = http_session()
    try:
        out = _daily_route(session, respondent, timezone, start, end)
        if not out.empty:
            # _daily_route has already dropped every storage code, because a
            # daily value is netted over the day and net cannot be turned back
            # into discharge. Rebuild them from the hourly route. If that call
            # fails, leave both buckets short rather than keep a number on the
            # wrong definition - a missing bucket is honest, a mixed one is
            # not, and hydro missing its pumped share is visible while hydro
            # carrying a net-convention PS figure is not.
            try:
                storage = (
                    _national_storage_daily(session, utc_offset, start, end)
                    if iso == "US48"
                    else _storage_daily(session, respondent, utc_offset, start, end)
                )
            except Exception as e:
                print(f"EIA: storage rebuild failed for {respondent} ({e}); omitting battery and pumped hydro")
                storage = None
            if storage is not None and not storage.empty:
                out = pd.concat([out, storage], ignore_index=True)
                out = out.groupby(["date", "fuel_category"], as_index=False)["generation_mwh"].sum()
            return out
        print(f"EIA: daily route empty for {respondent} {start}..{end}; trying hourly route")
    except Exception as e:
        print(f"EIA: daily route failed for {respondent} ({e}); trying hourly route")
    return _hourly_route(session, respondent, utc_offset, start, end)
