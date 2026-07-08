"""Exports the DuckDB `generation` table to static JSON files consumed by the
GitHub Pages dashboard (docs/data/). Re-run after every pipeline run."""
from __future__ import annotations

import datetime as dt
import json
import pathlib

import duckdb
import pandas as pd

from src.db import connect

OUT_DIR = pathlib.Path(__file__).resolve().parent.parent / "docs" / "data"


def _write_json(name: str, payload) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUT_DIR / name, "w") as f:
        json.dump(payload, f, default=str, separators=(",", ":"))


def _stringify_dates(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    df = df.copy()
    for col in cols:
        df[col] = pd.to_datetime(df[col]).dt.strftime("%Y-%m-%d")
    return df


def export_all(conn: duckdb.DuckDBPyConnection | None = None) -> None:
    own_conn = conn is None
    conn = conn or connect()

    national_daily = conn.execute(
        """
        SELECT date, fuel_category, SUM(generation_mwh) AS generation_mwh
        FROM generation
        GROUP BY date, fuel_category
        ORDER BY date, fuel_category
        """
    ).df()
    national_daily = _stringify_dates(national_daily, ["date"])
    _write_json("national_daily.json", national_daily.to_dict(orient="records"))

    iso_daily = conn.execute(
        """
        SELECT date, iso, fuel_category, generation_mwh
        FROM generation
        ORDER BY date, iso, fuel_category
        """
    ).df()
    iso_daily = _stringify_dates(iso_daily, ["date"])
    _write_json("iso_daily.json", iso_daily.to_dict(orient="records"))

    per_iso_latest = conn.execute(
        """
        SELECT iso, MAX(date) AS as_of
        FROM generation
        GROUP BY iso
        """
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
        GROUP BY iso
        ORDER BY iso
        """
    ).df()
    iso_stats = _stringify_dates(iso_stats, ["earliest", "latest"])
    recent_log = conn.execute(
        """
        SELECT iso, run_date, run_timestamp, status, rows_written, message
        FROM ingestion_log
        ORDER BY run_timestamp DESC
        LIMIT 100
        """
    ).df()
    recent_log = _stringify_dates(recent_log, ["run_date"])
    recent_log["run_timestamp"] = recent_log["run_timestamp"].astype(str)

    _write_json(
        "meta.json",
        {
            "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "date_range": {"min": str(date_range[0]), "max": str(date_range[1])},
            "iso_stats": iso_stats.to_dict(orient="records"),
            "recent_ingestion_log": recent_log.to_dict(orient="records"),
        },
    )

    if own_conn:
        conn.close()

    print(f"Exported dashboard data to {OUT_DIR}")


if __name__ == "__main__":
    export_all()
