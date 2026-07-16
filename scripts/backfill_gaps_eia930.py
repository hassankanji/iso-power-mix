#!/usr/bin/env python
"""Fill remaining source-side gap days with real measured data from EIA-930.

EIA's Hourly Electric Grid Monitor (Form EIA-930) publishes hourly net
generation by energy source for every U.S. balancing authority from
2018-07-01 onward, downloadable as keyless six-month bulk CSVs:

    https://www.eia.gov/electricity/gridmonitor/sixMonthFiles/EIA930_BALANCE_<YEAR>_<Jan_Jun|Jul_Dec>.csv

This is independent measurement of the same quantity our ISO connectors
pull, so it's a legitimate fallback for days the ISO's own archive has holes
(MISO's LGI API is missing scattered days; CAISO's history CSV lacks two).
Caveats, also documented in the README:
  - EIA-930 starts 2018-07-01; older gaps can't be filled this way.
  - Category detail is coarser (no battery-storage split; petroleum and
    unknown sources land in imports_other; pumped storage is inside hydro),
    so a filled day's mix is slightly bucketed differently than neighbors.
  - Values are BA-level net generation and may differ a few percent from
    the ISO's own accounting.

Usage (needs ~100-200MB download per six-month file touched; run via the
"Backfill gaps" GitHub Actions workflow or locally):

    python scripts/backfill_gaps_eia930.py            # fill all fillable gaps
    python scripts/backfill_gaps_eia930.py --iso MISO # one ISO only
    python scripts/backfill_gaps_eia930.py --dry-run  # show what would be fetched
"""
from __future__ import annotations

import argparse
import datetime as dt
import io
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import pandas as pd

from src.connectors.base import http_session
from src.db import connect, log_ingestion, upsert_generation
from src.export import export_all
from src.gaps import find_missing_dates, load_state, save_state
from src.schema import ISO_CODES

EIA930_START = dt.date(2018, 7, 1)
_URL_TEMPLATE = "https://www.eia.gov/electricity/gridmonitor/sixMonthFiles/EIA930_BALANCE_{year}_{half}.csv"

# Our ISO code -> EIA-930 balancing authority code.
_BA_CODES = {
    "CAISO": "CISO",
    "ERCOT": "ERCO",
    "ISONE": "ISNE",
    "MISO": "MISO",
    "NYISO": "NYIS",
    "PJM": "PJM",
    "SPP": "SWPP",
}

_FUEL_COL_RE = re.compile(r"^Net Generation \(MW\) from (.+?)( \(Adjusted\)| \(Imputed\))?$")


def _canon_fuel(eia_fuel: str) -> str:
    key = eia_fuel.lower()
    if "natural gas" in key:
        return "natural_gas"
    if "coal" in key:
        return "coal"
    if "nuclear" in key:
        return "nuclear"
    if "hydro" in key:
        return "hydro"
    if "solar" in key:
        return "solar"
    if "wind" in key:
        return "wind"
    if "battery" in key or ("storage" in key and "hydro" not in key):
        return "storage"
    if "geothermal" in key or "biomass" in key:
        return "other_renewables"
    return "imports_other"  # petroleum, other, unknown


def _half_file_for(day: dt.date) -> tuple[int, str]:
    return day.year, ("Jan_Jun" if day.month <= 6 else "Jul_Dec")


def _fuel_columns(columns: list[str]) -> dict[str, str]:
    """Pick one source column per EIA fuel, preferring the (Adjusted) variant
    when both raw and adjusted exist."""
    chosen: dict[str, tuple[int, str]] = {}
    for col in columns:
        m = _FUEL_COL_RE.match(col.strip())
        if not m:
            continue
        fuel, suffix = m.group(1), (m.group(2) or "")
        rank = 1 if "Adjusted" in suffix else 0
        if fuel not in chosen or rank > chosen[fuel][0]:
            chosen[fuel] = (rank, col)
    return {fuel: col for fuel, (rank, col) in chosen.items()}


def _daily_from_eia(df: pd.DataFrame, wanted_dates: set[dt.date]) -> pd.DataFrame:
    date_col = "Data Date"
    df = df.copy()
    df["date"] = pd.to_datetime(df[date_col], errors="coerce").dt.date
    df = df[df["date"].isin(wanted_dates)]
    if df.empty:
        return pd.DataFrame(columns=["date", "fuel_category", "generation_mwh"])

    fuel_cols = _fuel_columns(list(df.columns))
    parts = []
    for fuel, col in fuel_cols.items():
        mw = pd.to_numeric(df[col].astype(str).str.replace(",", ""), errors="coerce")
        parts.append(pd.DataFrame({"date": df["date"], "fuel_category": _canon_fuel(fuel), "mw": mw}))
    combined = pd.concat(parts, ignore_index=True).dropna(subset=["mw"])
    # Several EIA fuels can share one canonical bucket - sum per row-group
    # first, then average hours and scale to daily MWh (mean MW * 24).
    per_hour = combined.groupby(["date", "fuel_category"], as_index=False)["mw"].mean()
    per_hour["generation_mwh"] = per_hour["mw"] * 24
    return per_hour[["date", "fuel_category", "generation_mwh"]]


def main() -> None:
    parser = argparse.ArgumentParser(description="Fill gap days from EIA-930 bulk files.")
    parser.add_argument("--iso", help="Comma-separated ISO codes (default: all with gaps)")
    parser.add_argument("--dry-run", action="store_true", help="Only report what would be fetched")
    args = parser.parse_args()
    targets = args.iso.split(",") if args.iso else ISO_CODES

    conn = connect()
    # (year, half) -> {iso: [dates]}
    needed: dict[tuple[int, str], dict[str, list[dt.date]]] = {}
    for iso in targets:
        fillable = [d for d in find_missing_dates(conn, iso) if d >= EIA930_START]
        for d in fillable:
            needed.setdefault(_half_file_for(d), {}).setdefault(iso, []).append(d)
        if fillable:
            print(f"[{iso}] {len(fillable)} gap day(s) fillable from EIA-930: {fillable[0]} .. {fillable[-1]}")

    if not needed:
        print("No fillable gaps (either no gaps, or all predate EIA-930 coverage starting 2018-07-01).")
        return
    if args.dry_run:
        for (year, half), by_iso in sorted(needed.items()):
            print(f"Would download EIA930_BALANCE_{year}_{half}.csv for", {k: len(v) for k, v in by_iso.items()})
        return

    session = http_session()
    total_filled = 0
    for (year, half), by_iso in sorted(needed.items()):
        url = _URL_TEMPLATE.format(year=year, half=half)
        print(f"Downloading {url} ...")
        resp = session.get(url, timeout=600)
        if resp.status_code != 200:
            print(f"  HTTP {resp.status_code} - skipping this file")
            continue

        for iso, dates in by_iso.items():
            ba = _BA_CODES[iso]
            frames = []
            for chunk in pd.read_csv(io.BytesIO(resp.content), chunksize=200_000, low_memory=False):
                if "Balancing Authority" not in chunk.columns:
                    print("  Unexpected file format (no 'Balancing Authority' column) - aborting this file")
                    frames = []
                    break
                frames.append(chunk[chunk["Balancing Authority"] == ba])
            if not frames:
                continue
            ba_rows = pd.concat(frames, ignore_index=True)
            daily = _daily_from_eia(ba_rows, set(dates))
            if daily.empty:
                print(f"  [{iso}] EIA-930 also has no data for these dates")
                continue
            daily["iso"] = iso
            rows = upsert_generation(conn, daily[["date", "iso", "fuel_category", "generation_mwh"]])
            filled_days = sorted(daily["date"].unique())
            print(f"  [{iso}] filled {len(filled_days)} day(s) ({rows} rows) from EIA-930")
            log_ingestion(conn, iso, dt.date.today(), "eia930_backfill", rows, f"filled {len(filled_days)} gap day(s)")
            total_filled += len(filled_days)

    # Refresh gap state + dashboard exports to reflect the fills.
    state = load_state()
    missing_by_iso = {iso: find_missing_dates(conn, iso) for iso in ISO_CODES}
    for iso, iso_attempts in state.get("attempts", {}).items():
        current = {d.isoformat() for d in missing_by_iso.get(iso, [])}
        for key in [k for k in iso_attempts if k not in current]:
            iso_attempts.pop(key)
    save_state(state, missing_by_iso)
    export_all(conn)
    conn.close()
    print(f"\nDone: filled {total_filled} gap day(s) total from EIA-930.")


if __name__ == "__main__":
    main()
