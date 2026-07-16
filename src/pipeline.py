"""Orchestrates per-ISO fetch -> normalize -> upsert.

Backfills from each connector's EARLIEST_DATE the first time an ISO has no
data yet; pulls incrementally after that. One ISO's failure is caught and
logged, never blocking the others.

Incremental runs deliberately overlap the last LOOKBACK_DAYS rather than
starting strictly at (latest stored date + 1). Upserts make the overlap
idempotent, and it self-heals three failure patterns observed in production:
transient per-day fetch errors that would otherwise become permanent silent
gaps (ISO-NE 429s), sources that publish in arrears and fill earlier days
late (ERCOT's current-year file), and sources whose freshest day is
initially sparse and firms up a day later (MISO's "yesterday" feed).
"""
from __future__ import annotations

import datetime as dt
import traceback

from src.connectors import CONNECTORS
from src.db import connect, latest_date_for_iso, log_ingestion, upsert_generation

LOOKBACK_DAYS = 7


def run_all(
    isos: list[str] | None = None,
    start_override: dt.date | None = None,
    end_override: dt.date | None = None,
) -> dict[str, str]:
    conn = connect()
    end_date = end_override or (dt.date.today() - dt.timedelta(days=1))
    targets = isos or list(CONNECTORS.keys())
    results: dict[str, str] = {}

    for iso in targets:
        connector = CONNECTORS[iso]
        latest = latest_date_for_iso(conn, iso)

        if start_override:
            start_date = start_override
        elif latest is None:
            start_date = connector.EARLIEST_DATE
        else:
            start_date = min(
                latest + dt.timedelta(days=1),
                end_date - dt.timedelta(days=LOOKBACK_DAYS - 1),
            )
            start_date = max(start_date, connector.EARLIEST_DATE)

        if start_date > end_date:
            print(f"[{iso}] up to date (latest={latest}), nothing to fetch")
            results[iso] = "up_to_date"
            continue

        print(f"[{iso}] fetching {start_date} .. {end_date}")
        try:
            df = connector.fetch_range(start_date, end_date)
            rows = upsert_generation(conn, df)
            log_ingestion(conn, iso, end_date, "success", rows, "")
            print(f"[{iso}] wrote {rows} rows")
            results[iso] = "success"
        except Exception as e:
            log_ingestion(conn, iso, end_date, "failed", 0, str(e))
            print(f"[{iso}] FAILED: {e}")
            traceback.print_exc()
            results[iso] = "failed"

    conn.close()
    return results
