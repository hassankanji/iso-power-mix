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

# Buckets whose readings are clipped at zero before the daily average, so the
# stored number is generation OUT of the asset and never net of charging.
# See src/schema.py for why discharge-only is the canonical definition.
#
# Clipping MUST happen here, on the native interval readings, not on a daily
# total: a day's net storage is (discharge - charging), and flooring THAT at
# zero would report a day of heavy cycling as zero generation. Clipping each
# interval and then averaging gives exactly the discharge energy, because
# mean(MW) * 24h over N evenly spaced readings is just the interval sum
# rescaled.
DISCHARGE_ONLY_CATEGORIES = {"storage"}


def _clip_discharge_only(mw: pd.Series, canon: str) -> pd.Series:
    return mw.clip(lower=0) if canon in DISCHARGE_ONLY_CATEGORIES else mw


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
    `category_map` are ignored.

    Multiple native columns mapping to the same canonical category (SPP's
    "Coal Market"/"Coal Self", CAISO's "Small hydro"/"Large hydro") are
    SUMMED per row before the across-rows mean. Pooling their rows into one
    mean instead would silently divide the combined total by the number of
    native columns - a bug that halved SPP's history until caught by
    reconciling against EIA totals.

    DISCHARGE_ONLY_CATEGORIES (storage) are clipped at zero per row, AFTER
    the natives sharing the bucket are summed: it is the fleet's net output
    in that interval that decides whether the fleet was discharging, not any
    one column's sign.
    """
    per_canon: dict[str, pd.Series] = {}
    for native_col, canon in category_map.items():
        if native_col not in df.columns:
            continue
        mw = pd.to_numeric(df[native_col], errors="coerce")
        per_canon[canon] = mw if canon not in per_canon else per_canon[canon].add(mw, fill_value=0)
    if not per_canon:
        return pd.DataFrame(columns=["date", "fuel_category", "generation_mwh"])
    per_canon = {canon: _clip_discharge_only(mw, canon) for canon, mw in per_canon.items()}
    parts = [
        pd.DataFrame({"date": date_series, "fuel_category": canon, "mw": mw})
        for canon, mw in per_canon.items()
    ]
    combined = pd.concat(parts, ignore_index=True)
    combined = combined.groupby(["date", "fuel_category"], as_index=False)["mw"].mean()
    combined["generation_mwh"] = combined["mw"] * HOURS_PER_DAY
    return combined[["date", "fuel_category", "generation_mwh"]]


def long_to_daily_mwh(
    df: pd.DataFrame,
    date_series: pd.Series,
    native_fuel_col: str,
    mw_col: str,
    category_map,
) -> pd.DataFrame:
    """Convert a long table (one row per timestamp+native fuel type) into
    long-format daily MWh per canonical category.

    Aggregation order matters: first mean(MW) * 24h per (date, NATIVE fuel),
    then map native -> canonical, then SUM within each (date, canonical)
    bucket. Mapping to canonical before averaging pools distinct native
    fuels' readings into one mean - which divides the true combined total by
    the number of natives sharing the bucket (NYISO's "Natural Gas" + "Dual
    Fuel" halved this way until caught). Callers must therefore pass the RAW
    native fuel column here, not a pre-bucketed one, whenever two natives
    can share a bucket.

    `category_map` is a dict (unmapped natives are dropped) or a callable
    (applied to every native; use for .get(..., fallback) semantics).

    DISCHARGE_ONLY_CATEGORIES (storage) are clipped at zero on the raw
    readings, before any averaging. Unlike the wide path this clips each
    native separately, because a long table gives no timestamp to sum two
    storage natives across first - correct as long as one native carries the
    bucket, which is the case for every long-format source we read (MISO's
    "storage", PJM's "storage").
    """
    tmp = pd.DataFrame(
        {
            "date": date_series,
            "native": df[native_fuel_col],
            "mw": pd.to_numeric(df[mw_col], errors="coerce"),
        }
    )
    bucket = category_map if callable(category_map) else category_map.get
    discharge_only = tmp["native"].map(lambda n: bucket(n) in DISCHARGE_ONLY_CATEGORIES)
    tmp.loc[discharge_only, "mw"] = tmp.loc[discharge_only, "mw"].clip(lower=0)

    per_native = tmp.groupby(["date", "native"], as_index=False)["mw"].mean()
    if callable(category_map):
        per_native["fuel_category"] = per_native["native"].map(category_map)
    else:
        per_native["fuel_category"] = per_native["native"].map(category_map)
        per_native = per_native.dropna(subset=["fuel_category"])
    out = per_native.groupby(["date", "fuel_category"], as_index=False)["mw"].sum()
    out["generation_mwh"] = out["mw"] * HOURS_PER_DAY
    return out[["date", "fuel_category", "generation_mwh"]]


def finalize(df_daily: pd.DataFrame, iso: str) -> pd.DataFrame:
    """Attach the iso column and enforce the canonical column order."""
    if df_daily.empty:
        return pd.DataFrame(columns=["date", "iso", "fuel_category", "generation_mwh"])
    out = df_daily.copy()
    out["iso"] = iso
    return out[["date", "iso", "fuel_category", "generation_mwh"]]
