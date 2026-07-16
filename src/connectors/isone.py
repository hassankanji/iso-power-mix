"""ISO-NE connector.

Uses the ISO Express web services API's genfuelmix endpoint, which serves one
JSON document per calendar day of 5-minute generation-by-fuel-type snapshots:

    GET https://webservices.iso-ne.com/api/v1.1/genfuelmix/day/{YYYYMMDD}.json

Auth is HTTP Basic Auth using a free self-serve ISO Express account
(username/password), sent over HTTPS on every request. Register at
https://www.iso-ne.com/isoexpress/ -- credentials are read from the
ISONE_USERNAME / ISONE_PASSWORD environment variables.

This endpoint has no date-range/query parameters, so fetch_range loops one
HTTP request per calendar day. A first-request 401 is treated as a fatal
credential failure (raised immediately, no point burning a multi-year
backfill's worth of retries on bad creds); subsequent per-day failures deep
into a backfill (404, empty payload, transient error) are warned-and-skipped
so one bad day doesn't abort the whole range.
"""
from __future__ import annotations

import os
import time
import warnings
from datetime import date, timedelta

import pandas as pd

from src.connectors.base import finalize, http_session, long_to_daily_mwh

ISO = "ISONE"
# Live-verified 2026-07: the genfuelmix API returns 200 with an EMPTY payload
# for dates before 2018-06-30 (the public "since 2008" claim applies to ISO
# Express CSV reports, not this API). This may be a rolling ~8-year retention
# window - if backfills start coming up empty at the old end, re-probe the
# boundary rather than assuming data loss.
EARLIEST_DATE = date(2018, 6, 30)
REQUIRES_AUTH = True
# Pipeline pre-checks these and reports "skipped_no_credentials" instead of
# attempting a fetch that would just 401.
REQUIRED_ENV = ["ISONE_USERNAME", "ISONE_PASSWORD"]

_URL_TEMPLATE = "https://webservices.iso-ne.com/api/v1.1/genfuelmix/day/{ymd}.json"
_REQUEST_DELAY_SECONDS = 0.4
# Live-verified during a real multi-year backfill: ISO-NE rate-limits (429)
# hard under sustained request volume, and once triggered it tends to cascade
# (consecutive days keep failing) unless given a real cooldown - a flat
# per-request delay alone wasn't enough. Back off escalating amounts of time
# after each 429-caused failure, and reset once a request succeeds again.
_RATE_LIMIT_COOLDOWN_SECONDS = 8.0
_RATE_LIMIT_COOLDOWN_CAP_SECONDS = 90.0

# Canonical bucket <- native ISO-NE FuelCategory. Anything not listed here
# (including values not yet seen, e.g. future new categories) falls back to
# "imports_other" via the NATIVE_TO_CANONICAL.get(..., "imports_other") lookup
# done in _fetch_day below -- NOT via this dict's .map(), which would silently
# drop unmapped rows instead of bucketing them.
NATIVE_TO_CANONICAL = {
    "Coal": "coal",
    "Hydro": "hydro",
    "Natural Gas": "natural_gas",
    "Nuclear": "nuclear",
    "Solar": "solar",
    "Wind": "wind",
    "Wood": "other_renewables",
    "Refuse": "other_renewables",
    "Landfill Gas": "other_renewables",
    "Oil": "imports_other",
    "Other": "imports_other",
}


def _get_credentials() -> tuple[str, str]:
    username = os.environ.get("ISONE_USERNAME")
    password = os.environ.get("ISONE_PASSWORD")
    if not username or not password:
        raise RuntimeError(
            "ISONE_USERNAME/ISONE_PASSWORD not set -- register a free ISO "
            "Express account at iso-ne.com (see README)"
        )
    return username, password


def _fetch_day(session, day: date, auth: tuple[str, str]) -> pd.DataFrame | None:
    """Fetch and parse a single day's genfuelmix JSON. Returns None if the day
    simply has no data published yet (404 or empty payload)."""
    url = _URL_TEMPLATE.format(ymd=day.strftime("%Y%m%d"))
    resp = session.get(url, auth=auth, timeout=30)

    if resp.status_code == 401:
        raise RuntimeError(
            "ISO-NE API returned 401 Unauthorized -- check ISONE_USERNAME/"
            "ISONE_PASSWORD are correct for your ISO Express account"
        )
    if resp.status_code == 404:
        return None
    resp.raise_for_status()

    try:
        payload = resp.json()
    except ValueError:
        return None
    if not payload:
        return None

    records = (payload.get("GenFuelMixes") or {}).get("GenFuelMix") or []
    if not records:
        return None

    raw = pd.DataFrame.from_records(records)
    if raw.empty or "FuelCategory" not in raw.columns or "GenMw" not in raw.columns:
        return None

    # Pass the RAW native fuel column: Wood/Refuse/Landfill Gas share the
    # other_renewables bucket (and Oil/Other share imports_other), and
    # long_to_daily_mwh must average each native separately before summing
    # them (pre-bucketing here would pool their readings into one mean,
    # cutting the combined bucket to a fraction of its true value).
    date_series = pd.Series([day] * len(raw))
    daily = long_to_daily_mwh(
        raw,
        date_series,
        native_fuel_col="FuelCategory",
        mw_col="GenMw",
        category_map=lambda x: NATIVE_TO_CANONICAL.get(str(x).strip(), "imports_other"),
    )
    return daily


def fetch_range(start_date: date, end_date: date) -> pd.DataFrame:
    """Fetch daily generation-by-fuel-type MWh for ISO-NE over [start_date, end_date].

    Loops one HTTP call per calendar day (this endpoint has no range/query
    parameters -- one JSON document per day). Fails fast on a 401 from the
    very first request (bad credentials). Individual days that are not yet
    published, or that error transiently later in a long backfill, are
    skipped with a warning rather than aborting the whole range.
    """
    username, password = _get_credentials()
    auth = (username, password)
    session = http_session()
    frames: list[pd.DataFrame] = []

    day = start_date
    first = True
    consecutive_rate_limits = 0
    while day <= end_date:
        if not first:
            time.sleep(_REQUEST_DELAY_SECONDS)

        try:
            daily = _fetch_day(session, day, auth)
        except RuntimeError:
            # 401 = bad/revoked credentials. Fatal wherever it happens --
            # no point burning thousands of doomed requests. (_fetch_day
            # raises RuntimeError only for 401.)
            raise
        except Exception as exc:  # noqa: BLE001 - per-day resilience
            # Everything else - including a 429 on the very FIRST request -
            # is transient and must not abort the range. (Live-verified: a
            # first-request 429 previously killed entire gap-fill ranges.)
            warnings.warn(f"ISO-NE: skipping {day.isoformat()} due to error: {exc}")
            daily = None
            if "429" in str(exc):
                consecutive_rate_limits += 1
                cooldown = min(
                    _RATE_LIMIT_COOLDOWN_SECONDS * consecutive_rate_limits,
                    _RATE_LIMIT_COOLDOWN_CAP_SECONDS,
                )
                warnings.warn(f"ISO-NE: rate limited, cooling down {cooldown:.0f}s before continuing")
                time.sleep(cooldown)
        first = False

        if daily is not None and not daily.empty:
            frames.append(daily)
            consecutive_rate_limits = 0

        day += timedelta(days=1)

    if not frames:
        return finalize(pd.DataFrame(columns=["date", "fuel_category", "generation_mwh"]), ISO)

    combined = pd.concat(frames, ignore_index=True)
    return finalize(combined, ISO)
