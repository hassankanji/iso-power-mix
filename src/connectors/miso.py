"""MISO connector.

MISO's legacy public "Generation Fuel Mix" / "Historical Generation Fuel Mix"
reports (misoenergy.org Market Reports) were officially discontinued
December 12, 2025. This connector does not use them.

Two data paths:

1. Registered "LGI" (Load, Generation & Interchange) API path, gated behind
   the MISO_API_KEY env var. Live-verified (with a real subscription key)
   endpoint:
       https://apim.misoenergy.org/lgi/v1/real-time/{date}/generation/fuel-type
   Auth: `Ocp-Apim-Subscription-Key: <key>` header (NOT a Bearer token, despite
   the 401 error text mentioning "Bearer Token" - verified live that the
   subscription-key header is what actually works).

   Omitting the `region` query param (rather than passing region=MISO, which
   404s - there is no aggregate "MISO" region in the actual data) returns one
   row per hour per sub-region (NORTH, CENTRAL, SOUTH - confirmed live, no
   overlapping/duplicate region present), each with a `fuelTypes` dict:
   {coal, gas, nuclear, water, wind, solar, other, storage}, in MW. Unlike the
   no-auth path below, hydro ("water") IS broken out separately here.

   To get the true MISO-wide fuel mix: sum NORTH+CENTRAL+SOUTH per hour per
   fuel type first, THEN aggregate across the day's ~24 hourly readings via
   long_to_daily_mwh's mean*24 (averaging across hours only, never across
   regions, which would silently divide the true total by 3).

   Live-verified historical depth: 2014-01-01 onward returns real data;
   2013-06-01 and earlier return HTTP 500 consistently. EARLIEST_DATE is set
   accordingly. "Today" is not yet available via this endpoint (MISO
   publishes real-time generation "at 7am EST the day after"), so today's
   date is always fetched via the no-auth path below instead, even when a
   key is present.

2. No-auth "current window" path (always available, zero setup). MISO's
   public REST fuel mix API serves only Today / Yesterday / Latest
   snapshots:
       https://public-api.misoenergy.org/api/FuelMix/Today
       https://public-api.misoenergy.org/api/FuelMix/Yesterday
   Verified live: "Yesterday" actually spans from yesterday 00:00 through
   the current interval today (overlaps "Today"), so both are fetched,
   concatenated, and de-duplicated on (INTERVALEST, CATEGORY). Confirmed
   native CATEGORY values: Coal, Natural Gas, Nuclear, Wind, Solar, Battery
   Storage, Imports, Other. Per MISO's own docs, "Other" here also folds in
   Hydro, Pumped Storage, Diesel, Demand Response and some external/waste
   resources - hydro is NOT separable from this particular feed (unlike the
   LGI path above, which does break hydro out via "water").

   Also confirmed live: the "yesterday" portion of the Yesterday response is
   frequently sparse/incomplete on MISO's side (e.g. only "Imports" populated
   for an entire prior day). This connector reports whatever MISO's API
   actually returned rather than trying to paper over it.

Every network call in both paths is wrapped so failures are caught, a clear
warning is printed, and that date is skipped rather than crashing fetch_range.
"""
from __future__ import annotations

import os
import time
from datetime import date, timedelta

import pandas as pd

from src.connectors.base import finalize, http_session, long_to_daily_mwh
from src.schema import CANONICAL_CATEGORIES

ISO = "MISO"
EARLIEST_DATE = date(2014, 1, 1)  # live-verified against the real LGI endpoint; earlier dates 500
REQUIRES_AUTH = True  # only for full history via LGI - today/yesterday works with no auth at all

_FUELMIX_BASE = "https://public-api.misoenergy.org/api/FuelMix"
_LGI_BASE = "https://apim.misoenergy.org/lgi/v1"
_LGI_REQUEST_DELAY_SECONDS = 0.65  # MISO Data Exchange rate limit is ~100 calls/min

_IDENTITY_CATEGORY_MAP = {c: c for c in CANONICAL_CATEGORIES}

# Canonical bucket <- native category. Matched case-insensitively. Anything
# unrecognized falls back to "imports_other" via .get(..., "imports_other")
# below - NOT via pandas .map(), which would silently drop unmapped rows.
_NATIVE_TO_CANONICAL = {
    # LGI `fuelTypes` keys (lowercase)
    "coal": "coal",
    "gas": "natural_gas",
    "nuclear": "nuclear",
    "water": "hydro",
    "wind": "wind",
    "solar": "solar",
    "other": "imports_other",
    "storage": "storage",
    # no-auth FuelMix `CATEGORY` values
    "natural gas": "natural_gas",
    "battery storage": "storage",
    "imports": "imports_other",
}


def _bucket(native_category) -> str:
    return _NATIVE_TO_CANONICAL.get(str(native_category).strip().lower(), "imports_other")


# ---------------------------------------------------------------------------
# Path 1: registered LGI real-time generation-by-fuel-type endpoint
# ---------------------------------------------------------------------------


def _fetch_lgi_day(session, day: date, api_key: str) -> pd.DataFrame | None:
    """Fetch one day of MISO-wide generation-by-fuel-type from the LGI API.
    Returns None (with a printed warning) on any failure or missing data, so
    the caller can skip the date rather than crash fetch_range."""
    url = f"{_LGI_BASE}/real-time/{day.isoformat()}/generation/fuel-type"
    try:
        resp = session.get(url, headers={"Ocp-Apim-Subscription-Key": api_key}, timeout=30)
    except Exception as e:
        print(f"MISO: LGI request failed for {day} ({e}) -- skipping")
        return None

    if resp.status_code == 404:
        return None  # genuinely no data published for this date
    if resp.status_code >= 500:
        print(f"MISO: LGI server error for {day} ({resp.status_code}) -- skipping")
        return None
    try:
        resp.raise_for_status()
        payload = resp.json()
    except Exception as e:
        print(f"MISO: LGI unexpected response for {day} ({e}) -- skipping")
        return None

    rows = (payload or {}).get("data", [])
    if not rows:
        return None

    records = []
    for row in rows:
        interval_value = (row.get("timeInterval") or {}).get("value")
        for native_cat, mw in (row.get("fuelTypes") or {}).items():
            records.append({"date": day, "interval": interval_value, "category": native_cat, "mw": mw})
    if not records:
        return None

    df = pd.DataFrame(records)
    # Sum the 3 sub-regions (NORTH/CENTRAL/SOUTH) together per hour+category
    # BEFORE averaging across hours - averaging across regions instead would
    # silently divide the true MISO-wide total by 3.
    region_summed = df.groupby(["date", "interval", "category"], as_index=False)["mw"].sum()
    region_summed["_bucket"] = region_summed["category"].apply(_bucket)

    return long_to_daily_mwh(
        region_summed,
        region_summed["date"],
        native_fuel_col="_bucket",
        mw_col="mw",
        category_map=_IDENTITY_CATEGORY_MAP,
    )


# ---------------------------------------------------------------------------
# Path 2: no-auth Today/Yesterday snapshot
# ---------------------------------------------------------------------------


def _parse_fuelmix_json(payload: dict) -> pd.DataFrame:
    rows = (payload or {}).get("Fuel", {}).get("Type", [])
    if not rows:
        return pd.DataFrame(columns=["interval_dt", "category", "mw"])
    df = pd.DataFrame(rows)
    if "INTERVALEST" not in df.columns or "CATEGORY" not in df.columns or "ACT" not in df.columns:
        return pd.DataFrame(columns=["interval_dt", "category", "mw"])
    df["interval_dt"] = pd.to_datetime(df["INTERVALEST"], format="%Y-%m-%d %I:%M:%S %p", errors="coerce")
    df["mw"] = pd.to_numeric(df["ACT"], errors="coerce")
    df = df.dropna(subset=["interval_dt"])
    return df[["interval_dt", "CATEGORY", "mw"]].rename(columns={"CATEGORY": "category"})


def _fetch_current_window(session) -> pd.DataFrame:
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
    return combined.drop_duplicates(subset=["interval_dt", "category"])


def _daily_from_current_window(combined: pd.DataFrame, start_date: date, end_date: date) -> pd.DataFrame:
    if combined.empty:
        return pd.DataFrame(columns=["date", "fuel_category", "generation_mwh"])
    combined = combined.copy()
    combined["date"] = combined["interval_dt"].dt.date
    combined = combined[(combined["date"] >= start_date) & (combined["date"] <= end_date)]
    if combined.empty:
        return pd.DataFrame(columns=["date", "fuel_category", "generation_mwh"])
    combined["_bucket"] = combined["category"].apply(_bucket)
    return long_to_daily_mwh(
        combined, combined["date"], native_fuel_col="_bucket", mw_col="mw", category_map=_IDENTITY_CATEGORY_MAP
    )


# ---------------------------------------------------------------------------


def fetch_range(start_date: date, end_date: date) -> pd.DataFrame:
    """Fetch daily generation-by-fuel-type MWh for MISO over [start_date, end_date].

    "Today" always goes through the no-auth current-window path (LGI's
    real-time data isn't published until 7am EST the next day). Every other
    date in range goes through the registered LGI API if MISO_API_KEY is
    set (live-verified back to 2014-01-01); otherwise only "yesterday" is
    additionally covered via the no-auth path, and anything older than that
    is skipped with a printed warning. Never raises just because part of the
    range has no available source for it.
    """
    if end_date < start_date:
        raise ValueError("end_date must be >= start_date")

    today = date.today()
    yesterday = today - timedelta(days=1)
    api_key = os.environ.get("MISO_API_KEY")

    session = http_session()
    frames: list[pd.DataFrame] = []

    # "Today" (and, if no API key, "yesterday" too) via the no-auth path.
    if start_date <= today <= end_date or (not api_key and start_date <= yesterday <= end_date):
        combined = _fetch_current_window(session)
        daily = _daily_from_current_window(combined, start_date, end_date)
        if not daily.empty:
            frames.append(daily)

    if api_key:
        n_days = (end_date - start_date).days + 1
        lgi_frames = []
        first = True
        for i in range(n_days):
            d = start_date + timedelta(days=i)
            if d == today:
                continue  # not published via LGI yet; already covered above
            if not first:
                time.sleep(_LGI_REQUEST_DELAY_SECONDS)
            first = False
            parsed = _fetch_lgi_day(session, d, api_key)
            if parsed is not None and not parsed.empty:
                lgi_frames.append(parsed)
        if lgi_frames:
            frames.append(pd.concat(lgi_frames, ignore_index=True))
    elif start_date < yesterday:
        print(
            f"MISO: no MISO_API_KEY set -- dates before {yesterday} are unavailable "
            "(only today/yesterday work without registration). See README."
        )

    if not frames:
        return finalize(pd.DataFrame(columns=["date", "fuel_category", "generation_mwh"]), ISO)

    combined_daily = pd.concat(frames, ignore_index=True)
    # Both paths can independently produce a row for the same date/category
    # (e.g. "today" partially covered by both LGI's stale attempt and the
    # no-auth path) - keep the larger of any duplicate values rather than
    # silently summing or averaging them into a wrong total.
    combined_daily = (
        combined_daily.groupby(["date", "fuel_category"], as_index=False)["generation_mwh"].max()
    )
    return finalize(combined_daily, ISO)
