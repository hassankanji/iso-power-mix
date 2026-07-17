#!/usr/bin/env python
"""CLI entrypoint for the daily/backfill pipeline.

Usage:
    python scripts/run_pipeline.py                       # incremental run, all ISOs
    python scripts/run_pipeline.py --iso CAISO,ERCOT      # only these ISOs
    python scripts/run_pipeline.py --start 2019-01-01     # force a specific start date (e.g. backfill)
    python scripts/run_pipeline.py --end 2024-01-31       # force a specific end date
"""
import argparse
import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from src.export import export_all
from src.pipeline import run_all
from src.schema import ISO_CODES


def parse_date(s: str) -> dt.date:
    return dt.datetime.strptime(s, "%Y-%m-%d").date()


def main():
    load_dotenv()
    parser = argparse.ArgumentParser(description="Pull ISO generation-mix data and upsert into DuckDB.")
    parser.add_argument("--iso", help=f"Comma-separated ISO codes to run (default: all). Choices: {', '.join(ISO_CODES)}")
    parser.add_argument("--start", type=parse_date, help="Override start date (YYYY-MM-DD)")
    parser.add_argument("--end", type=parse_date, help="Override end date (YYYY-MM-DD), default yesterday")
    parser.add_argument("--no-export", action="store_true", help="Skip regenerating docs/data/*.json after the pull")
    parser.add_argument("--storage-backfill", action="store_true",
                        help="One-time: re-source every ISO's storage category from EIA net battery over full history, then export")
    args = parser.parse_args()

    if args.storage_backfill:
        from src.db import connect
        from src.pipeline import sync_storage_from_eia
        from src.schema import ISO_CODES as _ISO_CODES
        conn = connect()
        sync_storage_from_eia(conn, _ISO_CODES, dt.date.today() - dt.timedelta(days=1), full=True)
        conn.close()
        export_all()
        print("\nStorage backfill from EIA complete.")
        return

    isos = args.iso.split(",") if args.iso else None
    results = run_all(isos=isos, start_override=args.start, end_override=args.end)

    if not args.no_export:
        export_all()

    skipped = [iso for iso, status in results.items() if status == "skipped_no_credentials"]
    if skipped:
        print(f"\nSkipped (credentials not configured, see README): {skipped}")

    failed = [iso for iso, status in results.items() if status == "failed"]

    # Staleness alarm: an ISO can rot without ever "failing" (source quietly
    # returns nothing). On routine incremental runs, treat any ISO that ran
    # but is more than STALE_ALARM_DAYS behind as a failure so the GitHub
    # Actions job goes red and sends a notification email.
    STALE_ALARM_DAYS = 8
    if args.start is None and args.end is None:
        from src.db import connect, latest_date_for_iso

        conn = connect()
        today = dt.date.today()
        for iso, status in results.items():
            if status == "skipped_no_credentials":
                continue
            latest = latest_date_for_iso(conn, iso)
            if latest is not None and (today - latest).days > STALE_ALARM_DAYS:
                print(f"[{iso}] STALE: newest stored day is {latest} ({(today - latest).days} days old)")
                if iso not in failed:
                    failed.append(iso)
        conn.close()

    if failed:
        print(f"\nCompleted with failures/stale data: {failed}")
        sys.exit(1)
    print("\nAll ISOs completed successfully.")


if __name__ == "__main__":
    main()
