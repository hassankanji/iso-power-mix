"""Shared helpers every ISO connector builds on.

Connector contract: each module in this package must define
    ISO: str                                   - canonical ISO code, matches src.schema.ISO_CODES
    EARLIEST_DATE: datetime.date                - earliest date reliable data exists
    REQUIRES_AUTH: bool                         - True if env-var credentials are required
    fetch_range(start_date, end_date) -> pd.DataFrame with columns
        [date, iso, fuel_category, generation_mwh]  (see src.schema.GENERATION_COLUMNS)

fetch_range must raise on unrecoverable errors (network failure, auth failure) -
the pipeline runner is responsible for catching per-ISO failures so one ISO's
outage never blocks the others. It should return an empty DataFrame (not raise)
when the ISO simply has no data yet for part of the requested range (e.g. today
not published yet).
"""
from __future__ import annotations

import datetime as dt
from typing import Iterator

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


def http_session(retries: int = 3, backoff: float = 1.0) -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=retries,
        backoff_factor=backoff,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "POST"],
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def chunk_date_range(
    start: dt.date, end: dt.date, max_days: int
) -> Iterator[tuple[dt.date, dt.date]]:
    """Split [start, end] (inclusive) into consecutive chunks of at most max_days."""
    cur = start
    step = dt.timedelta(days=max_days - 1)
    one_day = dt.timedelta(days=1)
    while cur <= end:
        chunk_end = min(cur + step, end)
        yield cur, chunk_end
        cur = chunk_end + one_day


HOURS_PER_DAY = 24


def wide_to_daily_mwh(
    df: pd.DataFrame,
    date_series: pd.Series,
    category_map: dict[str, str],
) -> pd.DataFrame:
    """Convert a wide table (one MW column per native fuel type, one row per
    sub-daily interval/snapshot) into long-format daily MWh per canonical
    category. Daily energy is estimated as mean(MW) * 24h, which is robust
    regardless of the ISO's native sampling interval (5-min, 15-min, hourly)
    and tolerates small gaps in the intraday series. Columns not present in
    `category_map` are ignored; multiple native columns mapping to the same
    canonical category are summed per row before averaging."""
    parts = []
    for native_col, canon in category_map.items():
        if native_col not in df.columns:
            continue
        mw = pd.to_numeric(df[native_col], errors="coerce")
        parts.append(pd.DataFrame({"date": date_series, "fuel_category": canon, "mw": mw}))
    if not parts:
        return pd.DataFrame(columns=["date", "fuel_category", "generation_mwh"])
    combined = pd.concat(parts, ignore_index=True)
    combined = combined.groupby(["date", "fuel_category"], as_index=False)["mw"].mean()
    combined["generation_mwh"] = combined["mw"] * HOURS_PER_DAY
    return combined[["date", "fuel_category", "generation_mwh"]]


def long_to_daily_mwh(
    df: pd.DataFrame,
    date_series: pd.Series,
    native_fuel_col: str,
    mw_col: str,
    category_map: dict[str, str],
) -> pd.DataFrame:
    """Convert a long table (one row per timestamp+native fuel type) into
    long-format daily MWh per canonical category, using mean(MW) * 24h."""
    mapped = df[native_fuel_col].map(category_map)
    tmp = pd.DataFrame(
        {
            "date": date_series,
            "fuel_category": mapped,
            "mw": pd.to_numeric(df[mw_col], errors="coerce"),
        }
    ).dropna(subset=["fuel_category"])
    tmp = tmp.groupby(["date", "fuel_category"], as_index=False)["mw"].mean()
    tmp["generation_mwh"] = tmp["mw"] * HOURS_PER_DAY
    return tmp[["date", "fuel_category", "generation_mwh"]]


def finalize(df_daily: pd.DataFrame, iso: str) -> pd.DataFrame:
    """Attach the iso column and enforce the canonical column order."""
    if df_daily.empty:
        return pd.DataFrame(columns=["date", "iso", "fuel_category", "generation_mwh"])
    out = df_daily.copy()
    out["iso"] = iso
    return out[["date", "iso", "fuel_category", "generation_mwh"]]
