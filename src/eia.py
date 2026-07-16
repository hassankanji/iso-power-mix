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

_FUELTYPE_TO_CANONICAL = {
    "COL": "coal",
    "NG": "natural_gas",
    "NUC": "nuclear",
    "WAT": "hydro",
    "SUN": "solar",
    "WND": "wind",
    "BAT": "storage",
    "PS": "storage",
    "GEO": "other_renewables",
    "BIO": "other_renewables",
    "SNB": "solar",
    "OIL": "imports_other",
    "OTH": "imports_other",
    "UNK": "imports_other",
}


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
    df["fuel_category"] = df["fueltype"].map(_bucket)
    # Values are already daily MWh; multiple EIA codes sharing a canonical
    # bucket are simply summed (energy sums - no averaging trap here).
    out = df.groupby(["date", "fuel_category"], as_index=False)["mwh"].sum()
    return out.rename(columns={"mwh": "generation_mwh"})


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
    df = df.dropna(subset=["date", "mwh"])
    df = df[(df["date"] >= start) & (df["date"] <= end)]
    df["fuel_category"] = df["fueltype"].map(_bucket)
    out = df.groupby(["date", "fuel_category"], as_index=False)["mwh"].sum()
    return out.rename(columns={"mwh": "generation_mwh"})


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
            return out
        print(f"EIA: daily route empty for {respondent} {start}..{end}; trying hourly route")
    except Exception as e:
        print(f"EIA: daily route failed for {respondent} ({e}); trying hourly route")
    return _hourly_route(session, respondent, utc_offset, start, end)
