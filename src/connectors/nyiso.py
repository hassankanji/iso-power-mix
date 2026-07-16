"""NYISO connector.

Uses NYISO's public "P-63" Real-Time Fuel Mix report, served as CSV from
the MIS (Market Information System) archive. No auth required.

    Loose daily file (last ~2 weeks, incl. today):
        http://mis.nyiso.com/public/csv/rtfuelmix/{YYYYMMDD}rtfuelmix.csv
    Monthly bulk zip (one member CSV per day of that month; also kept
    up to date incrementally for the current, in-progress month):
        http://mis.nyiso.com/public/csv/rtfuelmix/{YYYYMM}01rtfuelmix_csv.zip
    Index page (used only for research, not fetched here):
        http://mis.nyiso.com/public/P-63list.htm

Live-verified columns (03/2024 zip and a current loose daily file):
    Time Stamp, Time Zone, Fuel Category, Gen MW
e.g. "03/01/2024 00:05:00,EST,Dual Fuel,3720.0"

Live-verified native `Fuel Category` values: Dual Fuel, Natural Gas,
Nuclear, Other Fossil Fuels, Other Renewables, Wind, Hydro. There is no
native Solar breakout in this feed.

Strategy: for each calendar month touching [start_date, end_date], try the
monthly zip first (it is available and incrementally updated even for the
current, still-in-progress month, per live testing). Any requested day not
present among the zip's members (e.g. a brand-new month with no zip yet,
or a day rolled out of the zip lag window) is fetched individually as a
loose daily CSV, tolerating 404 ("not published yet") by skipping.
"""
from __future__ import annotations

import io
import time
import zipfile
from calendar import monthrange
from datetime import date, timedelta

import pandas as pd

from src.connectors.base import finalize, http_session, long_to_daily_mwh

ISO = "NYISO"
EARLIEST_DATE = date(2015, 12, 9)  # live-verified: NYISO's Dec 2015 archive starts on the 9th, not the 1st
REQUIRES_AUTH = False

_BASE_URL = "http://mis.nyiso.com/public/csv/rtfuelmix/"
_ZIP_TEMPLATE = _BASE_URL + "{ym}01rtfuelmix_csv.zip"
_DAILY_TEMPLATE = _BASE_URL + "{ymd}rtfuelmix.csv"

# NYISO renamed the value column from "Gen MWh" (files through ~2017) to
# "Gen MW" (current files) without changing what it measures - both are the
# same MW-like 5-min reading, just relabeled. _parse_csv_text normalizes
# either spelling down to "Gen MW".
_REQUIRED_COLUMNS = ["Time Stamp", "Time Zone", "Fuel Category"]
_VALUE_COLUMN_ALIASES = ["Gen MW", "Gen MWh"]

# Native NYISO "Fuel Category" -> canonical bucket. Dual Fuel generators can
# burn either oil or gas; bucketed here as natural_gas as a simplification.
# Anything not listed (including "Other Fossil Fuels") falls back to
# imports_other via the .get(..., "imports_other") pattern below - this is
# what protects against long_to_daily_mwh's silent-drop-on-unmapped-key
# behavior.
_NATIVE_TO_CANONICAL = {
    "Natural Gas": "natural_gas",
    "Dual Fuel": "natural_gas",
    "Nuclear": "nuclear",
    "Hydro": "hydro",
    "Wind": "wind",
    "Other Renewables": "other_renewables",
    "Other Fossil Fuels": "imports_other",
}



def _parse_csv_text(text: str) -> pd.DataFrame | None:
    if not text or not text.strip():
        return None
    raw = pd.read_csv(io.StringIO(text))
    if raw.empty or not set(_REQUIRED_COLUMNS).issubset(raw.columns):
        return None
    value_col = next((c for c in _VALUE_COLUMN_ALIASES if c in raw.columns), None)
    if value_col is None:
        return None
    if value_col != "Gen MW":
        raw = raw.rename(columns={value_col: "Gen MW"})
    return raw


def _month_bounds(year: int, month: int) -> tuple[date, date]:
    last_day = monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last_day)


def _iter_months(start_date: date, end_date: date):
    year, month = start_date.year, start_date.month
    while (year, month) <= (end_date.year, end_date.month):
        yield year, month
        if month == 12:
            year, month = year + 1, 1
        else:
            month += 1


def _fetch_month_zip(session, year: int, month: int) -> dict[date, pd.DataFrame]:
    """Fetch the monthly bulk zip and return {date: raw_df} for each member
    CSV it contains. Returns an empty dict if the zip isn't available
    (404) or is malformed. A malformed body during a fast multi-year backfill
    is often transient throttling rather than a genuinely missing file, so
    this retries once after a short pause before giving up on the month."""
    url = _ZIP_TEMPLATE.format(ym=f"{year:04d}{month:02d}")

    for attempt in range(2):
        resp = session.get(url, timeout=60)
        if resp.status_code == 404:
            return {}
        resp.raise_for_status()
        try:
            zf = zipfile.ZipFile(io.BytesIO(resp.content))
            break
        except zipfile.BadZipFile:
            if attempt == 0:
                time.sleep(2.0)
                continue
            return {}

    out: dict[date, pd.DataFrame] = {}
    for name in zf.namelist():
        stem = name[:8]
        if not (stem.isdigit() and len(stem) == 8):
            continue
        try:
            day = date(int(stem[0:4]), int(stem[4:6]), int(stem[6:8]))
        except ValueError:
            continue
        with zf.open(name) as fh:
            text = fh.read().decode("utf-8", errors="replace")
        raw = _parse_csv_text(text)
        if raw is not None:
            out[day] = raw
    return out


def _fetch_day_csv(session, day: date) -> pd.DataFrame | None:
    """Fetch a single loose daily CSV. Returns None if not published yet
    (404) or empty."""
    url = _DAILY_TEMPLATE.format(ymd=day.strftime("%Y%m%d"))
    resp = session.get(url, timeout=30)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return _parse_csv_text(resp.text)


def fetch_range(start_date: date, end_date: date) -> pd.DataFrame:
    """Fetch daily generation-by-fuel-type MWh for NYISO over
    [start_date, end_date] (inclusive).

    For each touched calendar month, prefers the monthly bulk zip (one
    HTTP call covers the whole month, including the current in-progress
    month, per live testing). Any requested day missing from the zip is
    fetched individually as a loose daily CSV; a 404 there means the day
    simply isn't published yet and is skipped rather than raising. Other
    HTTP/network failures propagate per the connector contract.
    """
    session = http_session()
    day_frames: dict[date, pd.DataFrame] = {}

    for year, month in _iter_months(start_date, end_date):
        month_first, month_last = _month_bounds(year, month)
        range_start = max(month_first, start_date)
        range_end = min(month_last, end_date)

        zip_days = _fetch_month_zip(session, year, month)
        for day, raw in zip_days.items():
            if range_start <= day <= range_end:
                day_frames[day] = raw

        # Loose daily files only exist for roughly the last couple of weeks,
        # so only bother with the one-request-per-day fallback for recent
        # months - for an old month whose zip failed, 30 individual 404s
        # would just add load (and backfill request volume) for nothing.
        month_is_recent = (date.today() - month_last).days < 30
        if month_is_recent:
            cur = range_start
            while cur <= range_end:
                if cur not in day_frames:
                    raw = _fetch_day_csv(session, cur)
                    if raw is not None:
                        day_frames[cur] = raw
                cur += timedelta(days=1)

        time.sleep(0.15)

    empty = finalize(pd.DataFrame(columns=["date", "fuel_category", "generation_mwh"]), ISO)
    if not day_frames:
        return empty

    combined = pd.concat(day_frames.values(), ignore_index=True)

    # Dedupe the DST fall-back repeated local hour (same "Time Stamp" text
    # can appear once under EDT and once under EST) so it doesn't get
    # double-weighted in the mean(MW)*24 estimate.
    combined = combined.drop_duplicates(subset=["Time Stamp", "Fuel Category"], keep="first")

    # Older files (through ~2017) omit seconds ("01/01/2016 00:05"); current
    # files include them ("03/01/2024 00:05:00"). Deliberately NOT using
    # pd.to_datetime(..., errors="coerce") with no format: pandas guesses a
    # single fixed format from an early sample and applies it to the whole
    # column, which silently turned the back half of a multi-format backfill
    # into NaT (verified: 87% of rows lost this way on a real 2016 range).
    # Try both known formats explicitly instead.
    ts = pd.to_datetime(combined["Time Stamp"], format="%m/%d/%Y %H:%M:%S", errors="coerce")
    still_missing = ts.isna()
    if still_missing.any():
        ts.loc[still_missing] = pd.to_datetime(
            combined.loc[still_missing, "Time Stamp"], format="%m/%d/%Y %H:%M", errors="coerce"
        )
    combined = combined.loc[ts.notna()].copy()
    ts = ts.loc[ts.notna()]
    date_series = ts.dt.date

    # Restrict strictly to the requested range (zip members can spill a
    # partial day at the boundary; loose files are already 1-per-day).
    in_range = (date_series >= start_date) & (date_series <= end_date)
    combined = combined.loc[in_range]
    date_series = date_series.loc[in_range]

    if combined.empty:
        return empty

    # Pass the RAW native fuel column: "Natural Gas" and "Dual Fuel" share
    # the natural_gas bucket, and long_to_daily_mwh must average each
    # native separately before summing them (pre-bucketing here would pool
    # their readings into one mean, halving reported gas).
    daily = long_to_daily_mwh(
        combined,
        date_series,
        native_fuel_col="Fuel Category",
        mw_col="Gen MW",
        category_map=lambda x: _NATIVE_TO_CANONICAL.get(str(x).strip(), "imports_other"),
    )
    if daily.empty:
        return empty

    return finalize(daily, ISO)
