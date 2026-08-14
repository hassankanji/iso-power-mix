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

The comparison runs PER FUEL as well as on the total, because a total-only
check is blind to exactly the bug that motivated this file. A bucket can be
wrong by a factor of two, or missing entirely, and still leave the total
within a percent if the bucket is small - which is how the battery bucket
carried a net-convention figure for weeks, and how pumped storage sat in the
wrong bucket without moving any ISO's ratio at all. Small buckets are where
definition errors hide, and the total is precisely the statistic that hides
them.

A ratio is not always a defect. Some are known and permanent - CAISO's own
feed reports imports that EIA's net generation excludes, and the ISOs report
no battery series where EIA does or vice versa - so EXPECTED below carries
the ones we have already explained, and the verdict says "expected" rather
than crying wolf. Anything not on that list and outside tolerance is a real
finding.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from src import eia
from src.db import connect
from src.schema import ISO_CODES

OUT_PATH = pathlib.Path(__file__).resolve().parent.parent / "docs" / "data" / "reconciliation.json"

# Buckets small enough that a ratio is noise rather than signal. Below this
# many GWh/day on BOTH sides, a 40% disagreement can be two plants.
MIN_GWH_PER_DAY = 5.0

# (iso, fuel) pairs whose disagreement is understood and documented, with the
# reason. These print as "expected" so a real regression is not lost among
# them. Anything here should be traceable to a docstring or the README.
EXPECTED = {
    ("CAISO", None): "CAISO's feed includes imports; EIA net generation does not",
    ("CAISO", "battery"): "EIA's CISO reports no battery series at all - ours is CAISO's own",
    ("PJM", "battery"): "EIA's PJM reports no battery series at all - ours is PJM's own",
    ("NYISO", "battery"): "neither source reports a battery series for NYISO",
    ("ISONE", None): "ISO-NE's feed excludes some behind-the-meter solar EIA counts",
    ("MISO", "hydro"): "MISO's no-auth feed folds hydro into Other",
}


def verdict_for(iso: str, fuel: str | None, ratio: float, ours: float, theirs: float) -> str:
    """One place decides, so the one-sided and both-sided paths cannot drift.

    Order matters. A documented difference is never a finding. Below
    MIN_GWH_PER_DAY on both sides nothing is worth reading - NYISO stores an
    explicit coal zero and EIA has no coal row for it at all, which is a
    difference of nothing from nothing. And a ratio across a sign change is
    not a ratio: EIA's imports_other goes negative when a region is a net
    exporter, and dividing by it produced "-5.807, INVESTIGATE" for what is
    just an interchange convention.
    """
    if (iso, fuel) in EXPECTED:
        return "expected"
    if max(abs(ours), abs(theirs)) < MIN_GWH_PER_DAY:
        return "too small to judge"
    if ours < 0 or theirs < 0:
        return "opposite signs - not a ratio"
    if 0.95 <= ratio <= 1.05:
        return "OK"
    return "check" if 0.85 <= ratio <= 1.15 else "INVESTIGATE"


# A ratio against a zero denominator is NaN, and `json.dump` writes that as a
# bare `NaN` token - which Python reads back happily and every strict JSON
# parser rejects, `JSON.parse` included. The file is published on the Pages
# site, so it has to be JSON that any reader can load: non-finite becomes null.
def json_ratio(ratio: float) -> float | None:
    return round(ratio, 4) if math.isfinite(ratio) else None


def compare(ours: dict, theirs: dict) -> tuple[float, float, float, int] | None:
    """Mean GWh/day on each side over the days both have, plus the ratio."""
    common = sorted(set(ours) & set(theirs))
    if not common:
        return None
    ours_avg = sum(ours[d] for d in common) / len(common) / 1000
    theirs_avg = sum(theirs[d] for d in common) / len(common) / 1000
    ratio = ours_avg / theirs_avg if theirs_avg else float("nan")
    return ours_avg, theirs_avg, ratio, len(common)


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

    print(f"\nReconciliation vs EIA-930, {start} .. {end} (GWh/day)\n")
    print(f"{'ISO':6} {'bucket':>18} {'ours':>9} {'EIA':>9} {'ratio':>7}  verdict")
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
        totals = compare(ours_by_date, eia_by_date)
        if totals is None:
            print(f"{iso:6} no overlapping days")
            continue
        ours_avg, eia_avg, ratio, n_days = totals
        verdict = verdict_for(iso, None, ratio, ours_avg, eia_avg)
        note = EXPECTED.get((iso, None), "")
        print(f"{iso:6} {'(total)':>18} {ours_avg:9.0f} {eia_avg:9.0f} {ratio:7.3f}  {verdict}"
              + (f"  - {note}" if note else ""))
        entry = {
            "days_compared": n_days,
            "ours_avg_gwh_per_day": round(ours_avg, 1),
            "eia_avg_gwh_per_day": round(eia_avg, 1),
            "ratio": json_ratio(ratio),
            "verdict": verdict,
            "fuels": {},
        }
        if note:
            entry["note"] = note

        # Per fuel. A bucket present on one side and absent on the other is
        # reported as its own finding - that is a definition mismatch, and it
        # never shows up as a ratio because there is nothing to divide.
        ours_fuel = conn.execute(
            """
            SELECT fuel_category, date, SUM(generation_mwh) FROM generation
            WHERE iso = ? AND date BETWEEN ? AND ? GROUP BY fuel_category, date
            """,
            [iso, start, end],
        ).fetchall()
        ours_by_fuel: dict[str, dict] = {}
        for fuel, day, mwh in ours_fuel:
            ours_by_fuel.setdefault(fuel, {})[day] = mwh
        eia_by_fuel = {
            fuel: g.set_index("date")["generation_mwh"].to_dict()
            for fuel, g in eia_df.groupby("fuel_category")
        }

        for fuel in sorted(set(ours_by_fuel) | set(eia_by_fuel)):
            pair = compare(ours_by_fuel.get(fuel, {}), eia_by_fuel.get(fuel, {}))
            f_note = EXPECTED.get((iso, fuel), "")
            if pair is None:
                # One side has the bucket and the other does not - a
                # definition mismatch, which never shows up as a ratio
                # because there is nothing to divide.
                side = "ours only" if fuel in ours_by_fuel else "EIA only"
                mine = sum(ours_by_fuel.get(fuel, {}).values()) / max(n_days, 1) / 1000
                eias = sum(eia_by_fuel.get(fuel, {}).values()) / max(n_days, 1) / 1000
                f_verdict = verdict_for(iso, fuel, float("nan"), mine, eias)
                if f_verdict not in ("expected", "too small to judge"):
                    f_verdict = "one-sided"
                print(f"{'':6} {fuel:>18} {mine:9.1f} {eias:9.1f} {'-':>7}  {f_verdict} ({side})"
                      + (f"  - {f_note}" if f_note else ""))
                entry["fuels"][fuel] = {
                    "ours_avg_gwh_per_day": round(mine, 2),
                    "eia_avg_gwh_per_day": round(eias, 2),
                    "verdict": f_verdict,
                    **({"note": f_note} if f_note else {}),
                }
                continue
            f_ours, f_eia, f_ratio, f_days = pair
            f_verdict = verdict_for(iso, fuel, f_ratio, f_ours, f_eia)
            print(f"{'':6} {fuel:>18} {f_ours:9.1f} {f_eia:9.1f} {f_ratio:7.3f}  {f_verdict}"
                  + (f"  - {f_note}" if f_note else ""))
            entry["fuels"][fuel] = {
                "days_compared": f_days,
                "ours_avg_gwh_per_day": round(f_ours, 2),
                "eia_avg_gwh_per_day": round(f_eia, 2),
                "ratio": json_ratio(f_ratio),
                "verdict": f_verdict,
                **({"note": f_note} if f_note else {}),
            }
        report["isos"][iso] = entry
        print()

    conn.close()
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_PATH, "w") as f:
        json.dump(report, f, indent=1)
    print(f"\nWrote {OUT_PATH}")


if __name__ == "__main__":
    main()
