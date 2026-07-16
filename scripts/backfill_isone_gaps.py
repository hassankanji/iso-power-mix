#!/usr/bin/env python
"""One-off: fill the specific ISONE date gaps left by the rate-limited first
GitHub Actions run, instead of re-fetching the whole 2008-2026 range."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import pandas as pd
from dotenv import load_dotenv

from src.connectors import isone
from src.db import connect, log_ingestion, upsert_generation

load_dotenv()


def find_gaps(conn) -> list[tuple]:
    df = conn.execute("SELECT DISTINCT date FROM generation WHERE iso='ISONE' ORDER BY date").df()
    present = set(pd.to_datetime(df["date"]).dt.date)
    all_days = pd.date_range(isone.EARLIEST_DATE, pd.Timestamp.today().date() - pd.Timedelta(days=1), freq="D").date
    missing = [d for d in all_days if d not in present]
    if not missing:
        return []
    gaps = []
    start = prev = missing[0]
    for d in missing[1:]:
        if (d - prev).days > 1:
            gaps.append((start, prev))
            start = d
        prev = d
    gaps.append((start, prev))
    return gaps


def main():
    conn = connect()
    gaps = find_gaps(conn)
    print(f"Found {len(gaps)} gap range(s)")
    for i, (start, end) in enumerate(gaps, 1):
        print(f"[{i}/{len(gaps)}] fetching {start} .. {end}")
        try:
            df = isone.fetch_range(start, end)
            rows = upsert_generation(conn, df)
            log_ingestion(conn, "ISONE", end, "success", rows, f"gap-fill {start}..{end}")
            print(f"  wrote {rows} rows")
        except Exception as e:
            log_ingestion(conn, "ISONE", end, "failed", 0, str(e))
            print(f"  FAILED: {e}")

    remaining = find_gaps(conn)
    print(f"\nRemaining gaps after this run: {len(remaining)}")
    for g in remaining:
        print(" ", g)
    conn.close()


if __name__ == "__main__":
    main()
