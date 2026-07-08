"""SPP connector.

Uses SPP's public "Generation Mix Historical" file browser, which serves
plain CSVs with no auth required:

    https://portal.spp.org/file-browser-api/download/generation-mix-historical?path=/{FILENAME}.csv

Two file shapes are used:

- Annual archives, ``GenMix_{year}.csv`` (2011-present), one per *closed*
  calendar year. These are only created once a year finishes, so the
  current calendar year has no annual archive yet (confirmed live:
  ``GenMix_2026.csv`` 404s while it's still 2026).
- A rolling 365-day file, ``SPP/GenMix365_SPP.csv``, confirmed live to be
  continuously updated through the current moment. This is used for the
  current calendar year (and as a fallback if an expected annual archive
  for a very recent past year hasn't been published yet).

Both are 5-minute-interval snapshots with a GMT/UTC timestamp column. The
date portion of that GMT timestamp is used as-is for the ``date`` column
(not converted to SPP local time) - an accepted simplification per the
connector spec.

SPP's native column layout has changed several times over the years:
- 2011-2015ish: one plain MW column per fuel type
  (``GMTTime, Coal, Diesel Fuel Oil, Hydro, Natural Gas, Nuclear, Solar,
  Waste Disposal Services, Wind`` - Solar/Waste Disposal Services columns
  are *present but entirely blank* in the earliest years, e.g. 2011, since
  those fuel types weren't tracked yet; fetch_range drops any native column
  that's all-NaN for the fetched slice before aggregating, so these don't
  leak through as NaN generation_mwh rows).
- 2016-2017: transitional, only Coal split into "Coal Market"/"Coal Self",
  everything else still a single column, plus new "Waste Heat"/"Other"
  columns.
- 2018-present (including the rolling 365-day file): every fuel type split
  into "<Fuel> Market" and "<Fuel> Self" columns (self-committed vs
  market-committed generation), timestamp column renamed
  "GMT MKT Interval". Column names sometimes carry stray leading/trailing
  whitespace (e.g. " Coal Market"), so column names are stripped after
  read. No battery/storage column has been observed in any year fetched
  during development; the category map below still lists plausible
  battery/storage native names so the connector picks them up automatically
  if SPP ever adds one (wide_to_daily_mwh silently skips native columns
  that aren't present).

wide_to_daily_mwh sums multiple native columns that map to the same
canonical bucket before averaging, so listing both the "Market" and "Self"
split columns per fuel is sufficient - no manual pre-summing needed.
"""
from __future__ import annotations

import io
from datetime import date

import pandas as pd

from src.connectors.base import finalize, http_session, wide_to_daily_mwh

ISO = "SPP"
EARLIEST_DATE = date(2011, 1, 1)
REQUIRES_AUTH = False

_DOWNLOAD_BASE = (
    "https://portal.spp.org/file-browser-api/download/generation-mix-historical?path=/{path}"
)
_ANNUAL_PATH = "GenMix_{year}.csv"
_ROLLING_365_PATH = "SPP/GenMix365_SPP.csv"

# Candidate GMT timestamp column names across file eras.
_TIME_COL_CANDIDATES = ("GMT MKT Interval", "GMTTime")

# Canonical bucket <- native SPP column (matched after stripping whitespace).
_CATEGORY_MAP = {
    "Coal": "coal",
    "Coal Market": "coal",
    "Coal Self": "coal",
    "Natural Gas": "natural_gas",
    "Natural Gas Market": "natural_gas",
    "Natural Gas Self": "natural_gas",
    "Gas Market": "natural_gas",
    "Gas Self": "natural_gas",
    "Nuclear": "nuclear",
    "Nuclear Market": "nuclear",
    "Nuclear Self": "nuclear",
    "Hydro": "hydro",
    "Hydro Market": "hydro",
    "Hydro Self": "hydro",
    "Solar": "solar",
    "Solar Market": "solar",
    "Solar Self": "solar",
    "Wind": "wind",
    "Wind Market": "wind",
    "Wind Self": "wind",
    "Waste Disposal Services": "other_renewables",
    "Waste Disposal Services Market": "other_renewables",
    "Waste Disposal Services Self": "other_renewables",
    "Waste Heat": "other_renewables",
    "Waste Heat Market": "other_renewables",
    "Waste Heat Self": "other_renewables",
    "Diesel Fuel Oil": "imports_other",
    "Diesel Fuel Oil Market": "imports_other",
    "Diesel Fuel Oil Self": "imports_other",
    "Other": "imports_other",
    "Other Market": "imports_other",
    "Other Self": "imports_other",
    "Battery": "storage",
    "Battery Market": "storage",
    "Battery Self": "storage",
    "Storage": "storage",
    "Storage Market": "storage",
    "Storage Self": "storage",
}


def _download_csv(session, path: str) -> pd.DataFrame | None:
    """Download and parse a CSV from the generation-mix-historical file
    browser. Returns None if the file doesn't exist yet (404) or is empty."""
    url = _DOWNLOAD_BASE.format(path=path)
    resp = session.get(url, timeout=120)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    text = resp.text
    if not text or not text.strip():
        return None
    df = pd.read_csv(io.StringIO(text))
    df.columns = [c.strip() for c in df.columns]
    if "BAA" in df.columns:
        # The rolling file is scoped to /SPP/ already, but filter
        # defensively in case other BAAs (e.g. SWPW) ever appear in it.
        df = df[df["BAA"].astype(str).str.strip().str.upper() == "SPP"]
    return df


def _filter_to_range(df: pd.DataFrame, start_date: date, end_date: date):
    """Parse the GMT timestamp column and slice to [start_date, end_date].

    Returns (chunk, date_series) with a matching, reset 0..N-1 index (required
    since wide_to_daily_mwh aligns its `date_series` argument to the
    DataFrame by index), or None if there's no usable timestamp column or no
    rows fall in range.
    """
    time_col = next((c for c in _TIME_COL_CANDIDATES if c in df.columns), None)
    if time_col is None:
        return None

    ts = pd.to_datetime(df[time_col], errors="coerce", utc=True)
    valid = ts.notna()
    if not valid.any():
        return None
    df = df.loc[valid]
    day = ts.loc[valid].dt.date

    mask = (day >= start_date) & (day <= end_date)
    if not mask.any():
        return None

    chunk = df.loc[mask].reset_index(drop=True)
    date_series = pd.Series(day.loc[mask].values)
    return chunk, date_series


def fetch_range(start_date: date, end_date: date) -> pd.DataFrame:
    """Fetch daily generation-by-fuel-type MWh for SPP over [start_date, end_date].

    Loops one HTTP call per calendar year covered by the range: past years
    use the closed annual archive (GenMix_{year}.csv); the current calendar
    year uses the rolling 365-day file, since SPP doesn't publish an annual
    archive until the year closes. If an expected past-year archive 404s
    (e.g. a just-turned-over year SPP hasn't archived yet), falls back to
    the rolling file too. Years with nothing usable are skipped rather than
    raising. Network failures other than "not published yet" (404) propagate
    as exceptions per the connector contract.
    """
    session = http_session()
    frames: list[pd.DataFrame] = []
    current_year = date.today().year

    for year in range(start_date.year, end_date.year + 1):
        year_start = max(start_date, date(year, 1, 1))
        year_end = min(end_date, date(year, 12, 31))

        if year == current_year:
            df = _download_csv(session, _ROLLING_365_PATH)
        else:
            df = _download_csv(session, _ANNUAL_PATH.format(year=year))
            if df is None:
                df = _download_csv(session, _ROLLING_365_PATH)
        if df is None:
            continue

        sliced = _filter_to_range(df, year_start, year_end)
        if sliced is None:
            continue
        chunk, date_series = sliced

        # Some older years (e.g. 2011) have Solar / Waste Disposal Services
        # columns present but entirely blank (those fuel types weren't
        # tracked yet) rather than absent. wide_to_daily_mwh only skips
        # columns that are missing outright, so an all-NaN column would
        # otherwise average to NaN and leak into the output - exclude those
        # here.
        category_map = {
            c: canon
            for c, canon in _CATEGORY_MAP.items()
            if c in chunk.columns and chunk[c].notna().any()
        }
        if not category_map:
            continue

        daily = wide_to_daily_mwh(chunk, date_series, category_map)
        if not daily.empty:
            frames.append(daily)

    if not frames:
        return finalize(pd.DataFrame(columns=["date", "fuel_category", "generation_mwh"]), ISO)

    combined = pd.concat(frames, ignore_index=True)
    return finalize(combined, ISO)
