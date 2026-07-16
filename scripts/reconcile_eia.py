#!/usr/bin/env python
"""Reconcile our connector-derived daily totals against EIA-930.

EIA independently meters every balancing authority, so systematic
disagreement here means a unit/aggregation bug on our side (this check is
what caught the Market/Self averaging bug that halved SPP) or a coverage
difference worth documenting. Run in GitHub Actions (needs EIA_API_KEY):

    python scripts/reconcile_eia.py            # last 30 full days
    python scripts/reconcile_eia.py --days 90

Prints a per-ISO table and writes docs/data/reconciliation.json. Ratios
near 1.00 are good; persistent deviation beyond a few percent deserves
investigation (behind-the-meter treatment, imports, category coverage).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from src import eia
from src.db import connect
from src.schema import ISO_CODES

OUT_PATH = pathlib.Path(__file__).resolve().parent.parent / "docs" / "data" / "reconciliation.json"


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=30, help="Window of trailing full days to compare")
    args = parser.parse_args()

    if not eia.has_key():
        print("EIA_API_KEY not set - cannot reconcile. Add it as a GitHub Actions secret (see README).")
        sys.exit(1)

    end = dt.date.today() - dt.timedelta(days=2)  # EIA's freshest day can be incomplete
    start = end - dt.timedelta(days=args.days - 1)

    conn = connect()
    report = {
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "isos": {},
    }

    print(f"\nReconciliation vs EIA-930, {start} .. {end} (daily totals, GWh/day)\n")
    print(f"{'ISO':6} {'ours':>9} {'EIA':>9} {'ratio':>7}  verdict")
    for iso in ISO_CODES:
        ours_rows = conn.execute(
            "SELECT date, SUM(generation_mwh) FROM generation WHERE iso = ? AND date BETWEEN ? AND ? GROUP BY date",
            [iso, start, end],
        ).fetchall()
        if not ours_rows:
            print(f"{iso:6} {'-':>9} {'-':>9} {'-':>7}  no local data (skipped)")
            continue
        try:
            eia_df = eia.fetch_daily(iso, start, end)
        except Exception as e:
            print(f"{iso:6} EIA fetch failed: {e}")
            continue
        if eia_df.empty:
            print(f"{iso:6} EIA returned no data")
            continue

        ours_by_date = {r[0]: r[1] for r in ours_rows}
        eia_by_date = eia_df.groupby("date")["generation_mwh"].sum().to_dict()
        common = sorted(set(ours_by_date) & set(eia_by_date))
        if not common:
            print(f"{iso:6} no overlapping days")
            continue
        ours_avg = sum(ours_by_date[d] for d in common) / len(common) / 1000
        eia_avg = sum(eia_by_date[d] for d in common) / len(common) / 1000
        ratio = ours_avg / eia_avg if eia_avg else float("nan")
        verdict = "OK" if 0.95 <= ratio <= 1.05 else ("check" if 0.85 <= ratio <= 1.15 else "INVESTIGATE")
        print(f"{iso:6} {ours_avg:9.0f} {eia_avg:9.0f} {ratio:7.3f}  {verdict}")
        report["isos"][iso] = {
            "days_compared": len(common),
            "ours_avg_gwh_per_day": round(ours_avg, 1),
            "eia_avg_gwh_per_day": round(eia_avg, 1),
            "ratio": round(ratio, 4),
            "verdict": verdict,
        }

    conn.close()
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_PATH, "w") as f:
        json.dump(report, f, indent=1)
    print(f"\nWrote {OUT_PATH}")


if __name__ == "__main__":
    main()
