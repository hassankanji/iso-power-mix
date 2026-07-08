"""CAISO connector.

Uses CAISO's public "Today's Outlook" history endpoint, which serves one CSV
per calendar day of 5-minute interval generation-by-fuel-type snapshots. No
auth required. This is the same source used by open-source clients such as
gridstatus and pyiso, and is preferred over the OASIS API because OASIS does
not cleanly expose actual generation-by-fuel-type history.

    https://www.caiso.com/outlook/history/{YYYYMMDD}/fuelsource.csv

Native CAISO columns have changed casing over the years (e.g. "Small Hydro"
vs "Small hydro", "Natural Gas" vs "Natural gas"), so the category map is
matched case-insensitively against the fetched columns rather than assuming
one exact spelling.
"""
from __future__ import annotations

import io
import time
from datetime import date, timedelta

import pandas as pd

from src.connectors.base import finalize, http_session, wide_to_daily_mwh

ISO = "CAISO"
EARLIEST_DATE = date(2019, 1, 1)  # verified live: 2019-01-01 returns data, 2017-01-01 404s
REQUIRES_AUTH = False

_URL_TEMPLATE = "https://www.caiso.com/outlook/history/{ymd}/fuelsource.csv"
_REQUEST_DELAY_SECONDS = 0.3

# Canonical bucket <- native CAISO column (matched case-insensitively).
_CATEGORY_MAP_LOWER = {
    "solar": "solar",
    "wind": "wind",
    "geothermal": "other_renewables",
    "biomass": "other_renewables",
    "biogas": "other_renewables",
    "small hydro": "hydro",
    "large hydro": "hydro",
    "natural gas": "natural_gas",
    "nuclear": "nuclear",
    "coal": "coal",
    "batteries": "storage",
    "imports": "imports_other",
    "other": "imports_other",
}


def _fetch_day(session, day: date) -> pd.DataFrame | None:
    """Fetch and parse a single day's fuelsource.csv. Returns None if the day
    simply has no data published yet (e.g. today, or a 404 for a not-yet-posted
    date)."""
    url = _URL_TEMPLATE.format(ymd=day.strftime("%Y%m%d"))
    resp = session.get(url, timeout=30)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    text = resp.text
    if not text or not text.strip():
        return None

    raw = pd.read_csv(io.StringIO(text))
    if raw.empty or "Time" not in raw.columns:
        return None

    # Build a case-insensitive category map keyed to this day's actual columns.
    category_map: dict[str, str] = {}
    for col in raw.columns:
        canon = _CATEGORY_MAP_LOWER.get(col.strip().lower())
        if canon is not None:
            category_map[col] = canon
    if not category_map:
        return None

    date_series = pd.Series([day] * len(raw))
    daily = wide_to_daily_mwh(raw, date_series, category_map)
    return daily


def fetch_range(start_date: date, end_date: date) -> pd.DataFrame:
    """Fetch daily generation-by-fuel-type MWh for CAISO over [start_date, end_date].

    Loops one HTTP call per calendar day (this endpoint has no range/query
    parameters - one CSV per day). Days that are not yet published (404, or
    empty response) are skipped rather than raising. Network failures other
    than "not published yet" propagate as exceptions per the connector
    contract.
    """
    session = http_session()
    frames: list[pd.DataFrame] = []

    day = start_date
    first = True
    while day <= end_date:
        if not first:
            time.sleep(_REQUEST_DELAY_SECONDS)
        first = False

        daily = _fetch_day(session, day)
        if daily is not None and not daily.empty:
            frames.append(daily)

        day += timedelta(days=1)

    if not frames:
        return finalize(pd.DataFrame(columns=["date", "fuel_category", "generation_mwh"]), ISO)

    combined = pd.concat(frames, ignore_index=True)
    return finalize(combined, ISO)
