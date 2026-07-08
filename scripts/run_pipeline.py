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
    args = parser.parse_args()

    isos = args.iso.split(",") if args.iso else None
    results = run_all(isos=isos, start_override=args.start, end_override=args.end)

    if not args.no_export:
        export_all()

    failed = [iso for iso, status in results.items() if status == "failed"]
    if failed:
        print(f"\nCompleted with failures: {failed}")
        sys.exit(1)
    print("\nAll ISOs completed successfully.")


if __name__ == "__main__":
    main()
