"""MISO connector.

MISO is the messiest ISO in this project -- this module is built around
graceful degradation rather than a single clean data source.

MISO's legacy public "Generation Fuel Mix" / "Historical Generation Fuel Mix"
reports (misoenergy.org Market Reports) were officially discontinued
December 12, 2025 (confirmed via MISO's own "MISO Data Exchange Information"
deck). Today's date in this project is well after that, so this connector
does not attempt to use them at all.

Two data paths are implemented, with graceful degradation between them:

1. No-auth "current window" path (REQUIRES_AUTH-free, always works, zero
   setup). MISO's public REST fuel mix API serves only Today / Yesterday /
   Latest snapshots -- it does NOT support arbitrary historical date-range
   queries:
       https://public-api.misoenergy.org/api/FuelMix/Today
       https://public-api.misoenergy.org/api/FuelMix/Yesterday
   This is the same source used by the open-source `gridstatus` library.
   Verified live on 2026-07-07: each response is a 5-minute-interval long
   table shaped like
       {"RefId": "...", "TotalMW": "...",
        "Fuel": {"Type": [{"INTERVALEST": "2026-07-07 12:00:00 AM",
                            "CATEGORY": "Coal", "ACT": "31393",
                            "FUEL_CATEGORY": "Coal  (31,393 MW)"}, ...]}}
   Also confirmed live: "Yesterday" is not strictly "yesterday only" -- it
   actually returns rows spanning from yesterday 00:00 through the current
   interval today, i.e. it overlaps "Today". So both endpoints are fetched,
   concatenated, de-duplicated on (INTERVALEST, CATEGORY), and each row's
   own INTERVALEST timestamp (not the endpoint name) determines which
   calendar date it belongs to.

   Confirmed live native CATEGORY values: Coal, Natural Gas, Nuclear, Wind,
   Solar, Battery Storage, Imports, Other. Per MISO's own public
   documentation, the "Other" bucket is known to also fold in Hydro, Pumped
   Storage Hydro, Diesel, Demand Response and some external/waste resources
   -- MISO does not cleanly break Hydro out of this feed. This connector
   maps "Other" -> imports_other (NOT hydro), since there is no reliable way
   to disambiguate from this feed alone. This is a documented limitation,
   not a bug: MISO's hydro generation is effectively invisible to this
   connector via the no-auth path.

   ANOTHER observed quirk (verified live 2026-07-07, not a parsing bug on
   our end): the "yesterday" calendar day's portion of the Yesterday
   response is frequently sparse/incomplete on MISO's side -- e.g. only the
   "Imports" category was populated for all 288 five-minute intervals of the
   prior day, while every category was present for today. This connector
   does not attempt to paper over this (there's no reliable way to backfill
   the missing categories from this feed); it simply reports whatever MISO's
   API actually returned. Treat "yesterday" numbers from this path as
   best-effort/possibly-partial, and "today" numbers as the more complete of
   the two.

2. Best-effort *registered* historical path, gated behind the MISO_API_KEY
   env var. MISO's newer "Data Exchange" LGI (Load, Generation & Interchange)
   API family has base URL https://apim.misoenergy.org/lgi/v1/ and requires
   a free MISO account plus a separate Data Exchange API subscription
   (rate limits ~100 calls/min / 24,000/day).

   *** THE EXACT RESOURCE PATH FOR HISTORICAL FUEL-MIX-BY-DATE IS UNVERIFIED.
   *** This was researched (WebSearch/WebFetch against data-exchange.
   *** misoenergy.org) but the actual API spec lives behind an authenticated
   *** portal (data-exchange.misoenergy.org/apis) that could not be inspected
   *** without a registered account. _LGI_CANDIDATE_PATHS below is a
   *** best-guess list, and _parse_lgi_payload() is a best-guess parser that
   *** assumes a response shape similar to the public FuelMix JSON. Both will
   *** likely need correction once the user has real Data Exchange
   *** credentials -- treat this whole code path as a scaffold, not a
   *** verified implementation.

   Every call in this path is wrapped in try/except: any failure (404, other
   HTTP error, timeout, unexpected payload shape) is caught, a clear warning
   is printed, and that date is skipped rather than crashing fetch_range.

3. Dates outside today/yesterday when MISO_API_KEY is NOT set are skipped
   with a clear printed warning ("MISO: no historical data source available
   for {date} without MISO_API_KEY -- see README"). fetch_range never raises
   just because a data source isn't available for part of the range -- it
   returns whatever it could get (possibly just today/yesterday, possibly
   empty for a purely-historical request with no key).

EARLIEST_DATE is set conservatively to a hardcoded 2025-12-01 (shortly before
the legacy reports were discontinued), since reliable deep backfill isn't
available without registration, and even the registered LGI path's true
historical depth/coverage is unverified. Adjust once real credentials exist.
"""
from __future__ import annotations

import os
import time
from datetime import date, timedelta

import pandas as pd

from src.connectors.base import finalize, http_session, long_to_daily_mwh
from src.schema import CANONICAL_CATEGORIES

ISO = "MISO"
EARLIEST_DATE = date(2025, 12, 1)  # conservative; see module docstring
REQUIRES_AUTH = True  # only for the historical/registered path -- today/yesterday needs no auth

_FUELMIX_BASE = "https://public-api.misoenergy.org/api/FuelMix"
_LGI_BASE = "https://apim.misoenergy.org/lgi/v1"

# Best-guess resource paths for a per-date historical fuel-mix resource under
# the LGI API. UNVERIFIED -- see module docstring. Tried in order; first one
# that returns a parseable response wins.
_LGI_CANDIDATE_PATHS = [
    "fuelmix",
    "generationfuelmix",
    "genfuelmix",
]

_LGI_REQUEST_DELAY_SECONDS = 0.3

# Canonical bucket <- native MISO CATEGORY value (matched case-insensitively).
# Anything not listed here (including any unrecognized/new category MISO adds)
# falls back to "imports_other" via the .get(..., "imports_other") pattern
# below -- NOT via pandas .map(), which would silently drop unmapped rows.
NATIVE_TO_CANONICAL = {
    "coal": "coal",
    "natural gas": "natural_gas",
    "nuclear": "nuclear",
    "wind": "wind",
    "solar": "solar",
    "battery storage": "storage",
    "imports": "imports_other",
    "other": "imports_other",  # NOTE: also folds in hydro/pumped-storage/diesel/DR -- see docstring
}


def _bucket(native_category) -> str:
    return NATIVE_TO_CANONICAL.get(str(native_category).strip().lower(), "imports_other")


def _parse_fuelmix_json(payload: dict) -> pd.DataFrame:
    """Parse a FuelMix/Today or FuelMix/Yesterday JSON payload into a long
    table of (interval_dt, category, mw). Returns an empty frame (not None)
    on any unexpected shape."""
    rows = (payload or {}).get("Fuel", {}).get("Type", [])
    if not rows:
        return pd.DataFrame(columns=["interval_dt", "category", "mw"])
    df = pd.DataFrame(rows)
    if "INTERVALEST" not in df.columns or "CATEGORY" not in df.columns or "ACT" not in df.columns:
        return pd.DataFrame(columns=["interval_dt", "category", "mw"])
    df["interval_dt"] = pd.to_datetime(
        df["INTERVALEST"], format="%Y-%m-%d %I:%M:%S %p", errors="coerce"
    )
    df["mw"] = pd.to_numeric(df["ACT"], errors="coerce")
    df = df.dropna(subset=["interval_dt"])
    return df[["interval_dt", "CATEGORY", "mw"]].rename(columns={"CATEGORY": "category"})


def _fetch_current_window(session) -> pd.DataFrame:
    """Fetch and combine FuelMix/Today + FuelMix/Yesterday. These two
    endpoints overlap (Yesterday spans from yesterday 00:00 through the
    current interval today), so rows are de-duplicated on
    (interval_dt, category) after concatenation."""
    frames = []
    for name in ("Today", "Yesterday"):
        url = f"{_FUELMIX_BASE}/{name}"
        try:
            resp = session.get(url, timeout=30)
            resp.raise_for_status()
            payload = resp.json()
        except Exception as e:
            print(f"MISO: no-auth FuelMix/{name} fetch failed ({e})")
            continue
        parsed = _parse_fuelmix_json(payload)
        if not parsed.empty:
            frames.append(parsed)

    if not frames:
        return pd.DataFrame(columns=["interval_dt", "category", "mw"])

    combined = pd.concat(frames, ignore_index=True)
    combined = combined.drop_duplicates(subset=["interval_dt", "category"])
    return combined


def _daily_from_current_window(
    combined: pd.DataFrame, start_date: date, end_date: date
) -> pd.DataFrame:
    """Bucket + aggregate the combined Today/Yesterday interval table into
    daily MWh per canonical category, restricted to [start_date, end_date]."""
    if combined.empty:
        return pd.DataFrame(columns=["date", "fuel_category", "generation_mwh"])

    combined = combined.copy()
    combined["date"] = combined["interval_dt"].dt.date
    combined = combined[(combined["date"] >= start_date) & (combined["date"] <= end_date)]
    if combined.empty:
        return pd.DataFrame(columns=["date", "fuel_category", "generation_mwh"])

    # Do our own fallback mapping before calling long_to_daily_mwh, since its
    # pandas .map(category_map) silently drops unmapped native values.
    combined["_bucket"] = combined["category"].apply(_bucket)

    return long_to_daily_mwh(
        combined,
        combined["date"],
        native_fuel_col="_bucket",
        mw_col="mw",
        category_map={c: c for c in CANONICAL_CATEGORIES},
    )


def _parse_lgi_payload(payload, day: date) -> pd.DataFrame | None:
    """Best-guess parser for a historical LGI fuel-mix response.

    UNVERIFIED: assumes a shape either matching the public FuelMix JSON
    ({"Fuel": {"Type": [...]}}) or a generic {"data": [...]} / {"value": [...]}
    / bare-list shape with category+MW fields under one of a few plausible
    names. This almost certainly needs correction once a real response is
    available -- see module docstring. Returns None (not an exception) on
    anything that doesn't look parseable, so the caller can cleanly skip
    the date.
    """
    try:
        rows = None
        if isinstance(payload, dict):
            if "Fuel" in payload:
                rows = payload.get("Fuel", {}).get("Type", [])
            elif "data" in payload:
                rows = payload["data"]
            elif "value" in payload:
                rows = payload["value"]
        elif isinstance(payload, list):
            rows = payload

        if not rows:
            return None

        df = pd.DataFrame(rows)
        cat_col = next(
            (c for c in df.columns if str(c).upper() in ("CATEGORY", "FUELTYPE", "FUEL_TYPE")),
            None,
        )
        mw_col = next(
            (c for c in df.columns if str(c).upper() in ("ACT", "MW", "VALUE", "GENMW")),
            None,
        )
        if cat_col is None or mw_col is None:
            return None

        df["_bucket"] = df[cat_col].apply(_bucket)
        df["_mw"] = pd.to_numeric(df[mw_col], errors="coerce")
        date_series = pd.Series([day] * len(df))

        daily = long_to_daily_mwh(
            df,
            date_series,
            native_fuel_col="_bucket",
            mw_col="_mw",
            category_map={c: c for c in CANONICAL_CATEGORIES},
        )
        return daily
    except Exception:
        return None


def _fetch_lgi_day(session, day: date, api_key: str) -> pd.DataFrame | None:
    """Best-effort attempt to fetch one day of historical fuel-mix data from
    the registered LGI API. Tries each candidate resource path in turn;
    catches and warns on any failure; returns None if nothing worked so the
    caller can skip the date rather than crash fetch_range."""
    headers = {"Ocp-Apim-Subscription-Key": api_key}
    ymd = day.strftime("%Y-%m-%d")

    for path in _LGI_CANDIDATE_PATHS:
        url = f"{_LGI_BASE}/{path}"
        try:
            resp = session.get(url, headers=headers, params={"date": ymd}, timeout=30)
            if resp.status_code == 404:
                continue
            resp.raise_for_status()
            payload = resp.json()
        except Exception as e:
            print(f"MISO: historical LGI fetch failed for {day} via '{path}' ({e})")
            continue

        parsed = _parse_lgi_payload(payload, day)
        if parsed is not None and not parsed.empty:
            return parsed

    print(
        f"MISO: no historical LGI data source available for {day} "
        "(unverified endpoint, all candidate paths failed) -- skipping"
    )
    return None


def fetch_range(start_date: date, end_date: date) -> pd.DataFrame:
    """Fetch daily generation-by-fuel-type MWh for MISO over [start_date, end_date].

    Graceful degradation:
      - today/yesterday (relative to date.today() at call time): always
        attempted via the no-auth FuelMix/Today + FuelMix/Yesterday endpoints.
      - other dates in range: attempted via the registered LGI API only if
        MISO_API_KEY is set (best-effort, unverified -- see module docstring);
        otherwise skipped with a printed warning.
    Never raises just because part of the range has no available data source;
    only genuine network/auth failures on paths actually attempted propagate.
    """
    if end_date < start_date:
        raise ValueError("end_date must be >= start_date")

    today = date.today()
    yesterday = today - timedelta(days=1)

    session = http_session()
    frames: list[pd.DataFrame] = []

    # 1. No-auth current-window path.
    if start_date <= today and end_date >= yesterday:
        combined = _fetch_current_window(session)
        daily = _daily_from_current_window(combined, start_date, end_date)
        if not daily.empty:
            frames.append(daily)

    # 2. Remaining dates: best-effort registered path, or skip with a warning.
    covered = {today, yesterday}
    n_days = (end_date - start_date).days + 1
    remaining_days = [
        start_date + timedelta(days=i)
        for i in range(n_days)
        if (start_date + timedelta(days=i)) not in covered
    ]

    if remaining_days:
        api_key = os.environ.get("MISO_API_KEY")
        if api_key:
            lgi_frames = []
            first = True
            for d in remaining_days:
                if not first:
                    time.sleep(_LGI_REQUEST_DELAY_SECONDS)
                first = False
                parsed = _fetch_lgi_day(session, d, api_key)
                if parsed is not None and not parsed.empty:
                    lgi_frames.append(parsed)
            if lgi_frames:
                frames.append(pd.concat(lgi_frames, ignore_index=True))
        else:
            for d in remaining_days:
                print(
                    f"MISO: no historical data source available for {d} "
                    "without MISO_API_KEY -- see README"
                )

    if not frames:
        return finalize(pd.DataFrame(columns=["date", "fuel_category", "generation_mwh"]), ISO)

    combined_daily = pd.concat(frames, ignore_index=True)
    return finalize(combined_daily, ISO)
