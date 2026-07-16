"""Exports the DuckDB `generation` table to static JSON files consumed by the
GitHub Pages dashboard (docs/data/). Re-run after every pipeline run.

Layout (chosen so the repo stays small even with daily commits - see the
README's Sustainability section):

  iso_daily_<YEAR>.json   one file per calendar year, compact rows
                          [date, iso, fuel_index, mwh(, 1 if estimated)].
                          Past years' files are byte-identical run to run,
                          so the daily commit only ever rewrites the
                          current-year file (a few hundred KB) instead of
                          one ~19MB blob.
  latest_snapshot.json    latest available day per ISO + national rollup.
  meta.json               date range, per-ISO coverage stats, years list,
                          interpolated-day list, recent ingestion log.
  gaps.json               written by src/gaps.py (gap-repair state), not here.

The national fuel-type series is NOT exported separately - it is derived
client-side by summing the per-ISO rows, which guarantees the two national
views can never disagree and halves the download.

Interpolation: source-side holes (see gaps.json) up to MAX_INTERP_GAP_DAYS
long are filled with straight-line interpolated rows so a missing MISO day
doesn't render as a fake dip in the stacked national charts. Interpolated
rows are flagged (5th element = 1), kept OUT of the database (src/db.py
skips them when rebuilding), and re-derived fresh on every export - if the
gap-repair pass later fills a date with real data, the estimate disappears
automatically.
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib

import duckdb
import pandas as pd

from src.db import connect
from src.schema import CANONICAL_CATEGORIES

OUT_DIR = pathlib.Path(__file__).resolve().parent.parent / "docs" / "data"
MAX_INTERP_GAP_DAYS = 10

_CAT_INDEX = {c: i for i, c in enumerate(CANONICAL_CATEGORIES)}


def _write_json(name: str, payload) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUT_DIR / name, "w") as f:
        json.dump(payload, f, default=str, separators=(",", ":"))


def _interpolated_rows(conn) -> pd.DataFrame:
    """Straight-line estimates for interior per-ISO holes of up to
    MAX_INTERP_GAP_DAYS consecutive days. Returns rows shaped like the
    generation table plus an `est` column."""
    from src.gaps import dates_to_ranges, find_missing_dates

    isos = [r[0] for r in conn.execute("SELECT DISTINCT iso FROM generation WHERE iso != 'US48'").fetchall()]
    frames = []
    for iso in isos:
        missing = find_missing_dates(conn, iso)
        for gap_start, gap_end in dates_to_ranges(missing):
            gap_len = (gap_end - gap_start).days + 1
            if gap_len > MAX_INTERP_GAP_DAYS:
                continue
            before, after = gap_start - dt.timedelta(days=1), gap_end + dt.timedelta(days=1)
            bounds = conn.execute(
                """
                SELECT date, fuel_category, generation_mwh FROM generation
                WHERE iso = ? AND date IN (?, ?)
                """,
                [iso, before, after],
            ).df()
            if bounds.empty:
                continue
            by_cat = {
                cat: {pd.Timestamp(r["date"]).date(): r["generation_mwh"] for _, r in g.iterrows()}
                for cat, g in bounds.groupby("fuel_category")
            }
            for cat, vals in by_cat.items():
                v0 = vals.get(before, 0.0)
                v1 = vals.get(after, 0.0)
                for i in range(gap_len):
                    day = gap_start + dt.timedelta(days=i)
                    frac = (i + 1) / (gap_len + 1)
                    frames.append(
                        {
                            "date": day,
                            "iso": iso,
                            "fuel_category": cat,
                            "generation_mwh": v0 + (v1 - v0) * frac,
                            "est": 1,
                        }
                    )
    if not frames:
        return pd.DataFrame(columns=["date", "iso", "fuel_category", "generation_mwh", "est"])
    return pd.DataFrame(frames)


def export_all(conn: duckdb.DuckDBPyConnection | None = None) -> None:
    own_conn = conn is None
    conn = conn or connect()

    real = conn.execute(
        """
        SELECT date, iso, fuel_category, generation_mwh
        FROM generation
        ORDER BY date, iso, fuel_category
        """
    ).df()
    real["est"] = 0
    estimated = _interpolated_rows(conn)
    combined = pd.concat([real, estimated], ignore_index=True)
    combined["date"] = pd.to_datetime(combined["date"]).dt.strftime("%Y-%m-%d")
    combined = combined.sort_values(["date", "iso", "fuel_category"])

    years = sorted(combined["date"].str.slice(0, 4).unique())
    for year in years:
        chunk = combined[combined["date"].str.startswith(year)]
        rows = []
        for _, r in chunk.iterrows():
            row = [r["date"], r["iso"], _CAT_INDEX[r["fuel_category"]], round(r["generation_mwh"])]
            if r["est"]:
                row.append(1)
            rows.append(row)
        _write_json(
            f"iso_daily_{year}.json",
            {
                "columns": ["date", "iso", "fuel_index", "mwh", "est"],
                "fuel_categories": CANONICAL_CATEGORIES,
                "rows": rows,
            },
        )

    # iso='US48' is the EIA national reference overlay - it belongs in the
    # year files (the dashboard reads it for the National Trend overlay) but
    # not in the per-ISO snapshot or freshness stats, where it would read as
    # a double-counted "ISO".
    per_iso_latest = conn.execute(
        "SELECT iso, MAX(date) AS as_of FROM generation WHERE iso != 'US48' GROUP BY iso"
    ).df()

    snapshot_by_iso = {}
    national_snapshot: dict[str, float] = {}
    for _, row in per_iso_latest.iterrows():
        iso, as_of = row["iso"], row["as_of"]
        day = conn.execute(
            "SELECT fuel_category, generation_mwh FROM generation WHERE iso = ? AND date = ?",
            [iso, as_of],
        ).df()
        snapshot_by_iso[iso] = {
            "as_of": pd.to_datetime(as_of).strftime("%Y-%m-%d"),
            "mix": day.to_dict(orient="records"),
        }
        for _, r in day.iterrows():
            national_snapshot[r["fuel_category"]] = national_snapshot.get(r["fuel_category"], 0.0) + r["generation_mwh"]

    _write_json(
        "latest_snapshot.json",
        {
            "national": [{"fuel_category": k, "generation_mwh": v} for k, v in national_snapshot.items()],
            "by_iso": snapshot_by_iso,
        },
    )

    date_range = conn.execute("SELECT MIN(date), MAX(date) FROM generation").fetchone()
    iso_stats = conn.execute(
        """
        SELECT iso, MIN(date) AS earliest, MAX(date) AS latest, COUNT(DISTINCT date) AS days_covered
        FROM generation
        WHERE iso != 'US48'
        GROUP BY iso
        ORDER BY iso
        """
    ).df()
    for col in ("earliest", "latest"):
        iso_stats[col] = pd.to_datetime(iso_stats[col]).dt.strftime("%Y-%m-%d")

    interpolated_by_iso = (
        estimated.groupby("iso")["date"].apply(lambda s: sorted({str(d) for d in s})).to_dict()
        if not estimated.empty
        else {}
    )

    recent_log = conn.execute(
        """
        SELECT iso, run_date, run_timestamp, status, rows_written, message
        FROM ingestion_log
        ORDER BY run_timestamp DESC
        LIMIT 100
        """
    ).df()
    recent_log["run_date"] = pd.to_datetime(recent_log["run_date"]).dt.strftime("%Y-%m-%d")
    recent_log["run_timestamp"] = recent_log["run_timestamp"].astype(str)

    _write_json(
        "meta.json",
        {
            "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "date_range": {"min": str(date_range[0]), "max": str(date_range[1])},
            "years": years,
            "fuel_categories": CANONICAL_CATEGORIES,
            "iso_stats": iso_stats.to_dict(orient="records"),
            "interpolated_days": interpolated_by_iso,
            "recent_ingestion_log": recent_log.to_dict(orient="records"),
        },
    )

    if own_conn:
        conn.close()

    print(f"Exported dashboard data to {OUT_DIR} ({len(years)} year files)")


if __name__ == "__main__":
    export_all()
