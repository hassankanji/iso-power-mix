"""PJM connector.

Uses PJM's Data Miner 2 REST API "Generation by Fuel Type" feed, which
reports hourly generation (MW) by native fuel type for resources under PJM
direction:

    https://api.pjm.com/api/v1/gen_by_fuel

Auth: requires a subscription key issued via PJM's API Portal
(https://apiportal.pjm.com/), sent as the `Ocp-Apim-Subscription-Key` HTTP
header (never as a query param, to avoid leaking it into logs/URLs). Read
from the PJM_SUBSCRIPTION_KEY environment variable; if unset, fetch_range
raises RuntimeError immediately (fail-fast auth error, not a per-day skip).

Rate limits: non-member accounts are capped at roughly 6 requests/minute
(member accounts get 600/min). Since we can't assume the caller has PJM
membership, this connector sleeps at least 10 seconds between every request
after the first.

Pagination: each response is capped at rowCount<=50000 rows with startRow-based
paging. PJM's total-row-count signal is not fully confirmed from public docs
(some integrations report an `X-TotalRows` response header, others a
`TotalRows`/`totalRows` field in the JSON body), so this connector treats
either as an optional early-exit hint and *also* falls back to a page-size
stopping rule (a page shorter than the requested rowCount is the last page).
Whichever signal actually shows up, pagination terminates correctly.

Time-range filtering: `datetime_beginning_ept` accepts a
"mm-dd-yyyy HH:MM to mm-dd-yyyy HH:MM" range string, and a single request
window is capped at 366 days, so multi-year fetches are chunked via
chunk_date_range(..., max_days=366).

Fuel-type mapping: PJM's Data Miner API guide does not enumerate the exact
`fuel_type` strings returned by this specific feed. _NATIVE_TO_CANONICAL below
is built defensively from the fuel categories PJM shows across its public
dashboards and other open-source PJM API clients (Coal, Gas, Natural Gas,
Nuclear, Hydro, Multiple Fuels, Oil, Other, Other Renewables, Solar, Wind,
Storage, Wind Solar, Black Liquor). Any unrecognized value safely falls back
to "imports_other" instead of being silently dropped by pandas .map(). This
mapping is UNVERIFIED against a live API response and should be revisited
once real credentials are available to inspect actual data.
"""
from __future__ import annotations

import os
import time
from datetime import date

import pandas as pd
import requests

from src.connectors.base import (
    chunk_date_range,
    finalize,
    http_session,
    long_to_daily_mwh,
)
from src.schema import CANONICAL_CATEGORIES

ISO = "PJM"
EARLIEST_DATE = date(2015, 1, 1)  # conservative default; gen_by_fuel's exact archive start is not documented publicly
REQUIRES_AUTH = True
# Pipeline pre-checks these and reports "skipped_no_credentials" instead of
# attempting a fetch that would just 401.
REQUIRED_ENV = ["PJM_SUBSCRIPTION_KEY"]

_BASE_URL = "https://api.pjm.com/api/v1/gen_by_fuel"
_MAX_ROWS_PER_REQUEST = 50000
_MAX_DAYS_PER_REQUEST = 366
_REQUEST_DELAY_SECONDS = 10.0  # stay safely under the ~6 req/min non-member rate limit

# Raw fuel_type strings (lower-cased, stripped) -> canonical bucket.
# UNVERIFIED against a live API response -- see module docstring.
_NATIVE_TO_CANONICAL: dict[str, str] = {
    "coal": "coal",
    "gas": "natural_gas",
    "natural gas": "natural_gas",
    "nuclear": "nuclear",
    "hydro": "hydro",
    "solar": "solar",
    "wind": "wind",
    "wind solar": "wind",  # combined bucket seen in some PJM views; approximated as wind
    "storage": "storage",
    "other renewables": "other_renewables",
    "black liquor": "other_renewables",  # biomass byproduct fuel
    "multiple fuels": "imports_other",
    "oil": "imports_other",
    "other": "imports_other",
}


def _bucket_for(raw_fuel_type) -> str:
    key = str(raw_fuel_type).strip().lower()
    return _NATIVE_TO_CANONICAL.get(key, "imports_other")


def _require_key() -> str:
    key = os.environ.get("PJM_SUBSCRIPTION_KEY")
    if not key:
        raise RuntimeError(
            "PJM_SUBSCRIPTION_KEY not set -- register at https://apiportal.pjm.com/ (see README)"
        )
    return key


def _fetch_page(
    session: requests.Session, key: str, ept_range: str, start_row: int
) -> tuple[list[dict], int | None]:
    """Fetch one page of rows. Returns (rows, total_rows_hint_or_None)."""
    resp = session.get(
        _BASE_URL,
        headers={"Ocp-Apim-Subscription-Key": key},
        params={
            "rowCount": _MAX_ROWS_PER_REQUEST,
            "startRow": start_row,
            "datetime_beginning_ept": ept_range,
            "format": "json",
        },
        timeout=60,
    )
    resp.raise_for_status()

    total_rows: int | None = None
    header_val = resp.headers.get("X-TotalRows")
    if header_val is not None:
        try:
            total_rows = int(header_val)
        except (TypeError, ValueError):
            total_rows = None

    payload = resp.json()
    rows: list[dict] = []
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        for items_key in ("items", "data", "rows", "Items", "Rows"):
            candidate = payload.get(items_key)
            if isinstance(candidate, list):
                rows = candidate
                break
        if total_rows is None:
            for count_key in ("totalRows", "TotalRows", "total_rows"):
                if count_key in payload:
                    try:
                        total_rows = int(payload[count_key])
                    except (TypeError, ValueError):
                        total_rows = None
                    break

    return rows, total_rows


def _fetch_chunk(
    session: requests.Session,
    key: str,
    chunk_start: date,
    chunk_end: date,
    first_request: bool,
) -> tuple[pd.DataFrame, bool]:
    """Fetch and aggregate all rows for one <=366-day window. Returns
    (daily_df, still_first_request) so the caller can track the global
    "have we made any request yet" state across chunks."""
    ept_range = "{} 00:00 to {} 23:59".format(
        chunk_start.strftime("%m-%d-%Y"), chunk_end.strftime("%m-%d-%Y")
    )

    all_rows: list[dict] = []
    total_rows_hint: int | None = None
    start_row = 1
    while True:
        if not first_request:
            time.sleep(_REQUEST_DELAY_SECONDS)
        first_request = False

        page_rows, hint = _fetch_page(session, key, ept_range, start_row)
        if hint is not None:
            total_rows_hint = hint
        if not page_rows:
            break

        all_rows.extend(page_rows)
        start_row += _MAX_ROWS_PER_REQUEST

        if total_rows_hint is not None and len(all_rows) >= total_rows_hint:
            break
        if len(page_rows) < _MAX_ROWS_PER_REQUEST:
            break

    if not all_rows:
        return pd.DataFrame(columns=["date", "fuel_category", "generation_mwh"]), first_request

    df = pd.DataFrame(all_rows)
    if "fuel_type" not in df.columns or "mw" not in df.columns:
        return pd.DataFrame(columns=["date", "fuel_category", "generation_mwh"]), first_request

    ts_col = "datetime_beginning_ept" if "datetime_beginning_ept" in df.columns else "datetime_beginning_utc"
    timestamps = pd.to_datetime(df[ts_col], errors="coerce")
    date_series = timestamps.dt.date

    # Pass the RAW native fuel column: several PJM natives share one bucket
    # (Gas/Natural Gas/Multiple Fuels -> natural_gas), and long_to_daily_mwh
    # must average each native separately before summing them (pre-bucketing
    # here would pool their readings into one mean, undercounting the bucket
    # by the number of natives sharing it).
    daily = long_to_daily_mwh(
        df,
        date_series,
        native_fuel_col="fuel_type",
        mw_col="mw",
        category_map=_bucket_for,
    )
    return daily, first_request


def fetch_range(start_date: date, end_date: date) -> pd.DataFrame:
    """Fetch daily generation-by-fuel-type MWh for PJM over [start_date, end_date].

    Raises RuntimeError immediately if PJM_SUBSCRIPTION_KEY is not set.
    Otherwise chunks the range into <=366-day windows, paginates each window
    with rowCount=50000, sleeps between requests to respect PJM's non-member
    rate limit, maps native fuel_type strings into the 9 canonical buckets
    (unrecognized values fall back to imports_other), and aggregates to daily
    MWh via mean(MW) * 24h.
    """
    key = _require_key()
    session = http_session()

    frames: list[pd.DataFrame] = []
    first_request = True
    for chunk_start, chunk_end in chunk_date_range(start_date, end_date, _MAX_DAYS_PER_REQUEST):
        daily, first_request = _fetch_chunk(session, key, chunk_start, chunk_end, first_request)
        if not daily.empty:
            frames.append(daily)

    if not frames:
        return finalize(pd.DataFrame(columns=["date", "fuel_category", "generation_mwh"]), ISO)

    combined = pd.concat(frames, ignore_index=True)
    return finalize(combined, ISO)
