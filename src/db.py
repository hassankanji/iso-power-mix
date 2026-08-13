"""DuckDB storage layer: schema init, idempotent upserts, and rebuild.

The .duckdb file itself is NOT committed to git (committing a ~19MB binary
daily would grow the repo by gigabytes per year). Instead the committed
per-year JSON exports in docs/data/ are the durable copy of the data - they
hold the exact same grain as the `generation` table - and connect()
transparently rebuilds the database from them whenever the local file is
missing or empty (every fresh checkout, including each GitHub Actions run).
Interpolated rows (est flag set; see src/export.py) are estimates, not
source data, so the rebuild skips them and the next export re-derives them.
"""
import glob
import json
import pathlib

import duckdb
import pandas as pd

from src.schema import CANONICAL_CATEGORIES, GENERATION_COLUMNS, canonical_category

DB_PATH = pathlib.Path(__file__).resolve().parent.parent / "data" / "power_mix.duckdb"
EXPORT_DIR = pathlib.Path(__file__).resolve().parent.parent / "docs" / "data"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS generation (
    date DATE NOT NULL,
    iso VARCHAR NOT NULL,
    fuel_category VARCHAR NOT NULL,
    generation_mwh DOUBLE NOT NULL,
    PRIMARY KEY (date, iso, fuel_category)
);

CREATE TABLE IF NOT EXISTS ingestion_log (
    iso VARCHAR NOT NULL,
    run_date DATE NOT NULL,
    run_timestamp TIMESTAMP NOT NULL,
    status VARCHAR NOT NULL,
    rows_written INTEGER,
    message VARCHAR
);
"""


def connect() -> duckdb.DuckDBPyConnection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = duckdb.connect(str(DB_PATH))
    conn.execute(SCHEMA_SQL)
    if conn.execute("SELECT COUNT(*) FROM generation").fetchone()[0] == 0:
        _rebuild_from_exports(conn)
    return conn


def _drop_negative_battery(df: pd.DataFrame, source: str) -> pd.DataFrame:
    """Last-resort guard on the battery-is-discharge invariant (src/schema.py).

    Producers clip battery output on the source's native interval, which is
    the only place the true discharge figure is recoverable. This does NOT
    replace that. It exists because a negative daily battery total is, by
    definition, net-convention data that leaked past a producer, and a
    negative slice silently breaks every stacked chart and share calculation
    downstream.

    It DROPS the row rather than clamping it to zero. Clamping was the
    original behaviour and it was worse than the disease: a zero is a
    measurement, so 34 ERCOT days of leaked net data became 34 days of
    "the batteries did nothing", which no later pass would ever revisit
    because the date no longer looked missing. That is what put a cliff in
    ERCOT's battery line - flat zero for its whole history, then 25 GWh/day
    the moment the clipping fix shipped. An absent row is honest, reads as a
    gap on the chart, and stays eligible for the EIA bucket fill.

    A warning here on freshly pulled data means a producer needs fixing, not
    that this guard is doing its job.
    """
    negative = (df["fuel_category"] == "battery") & (df["generation_mwh"] < 0)
    n = int(negative.sum())
    if n:
        worst = df.loc[negative, "generation_mwh"].min()
        isos = sorted(df.loc[negative, "iso"].unique())
        print(
            f"battery floor ({source}): dropped {n} negative battery row(s) "
            f"for {', '.join(isos)} (largest {worst:,.0f} MWh). Battery output is "
            f"discharge-only - see src/schema.py. If this fires on a fresh pull, "
            f"that connector is not clipping."
        )
        df = df.loc[~negative]
    return df


def _rebuild_from_exports(conn: duckdb.DuckDBPyConnection) -> None:
    """Repopulate an empty `generation` table from the committed per-year
    JSON exports. No-op if none exist (true first run)."""
    year_files = sorted(glob.glob(str(EXPORT_DIR / "iso_daily_*.json")))
    if not year_files:
        return
    frames = []
    for path in year_files:
        with open(path) as f:
            payload = json.load(f)
        # canonical_category maps the pre-rename `storage` bucket onto
        # `battery`, so a year file written before the rename still loads.
        cats = [canonical_category(c) for c in payload.get("fuel_categories", CANONICAL_CATEGORIES)]
        rows = [
            (r[0], r[1], cats[r[2]], r[3])
            for r in payload.get("rows", [])
            if len(r) < 5 or not r[4]  # skip interpolated (est) rows
        ]
        if rows:
            frames.append(pd.DataFrame(rows, columns=GENERATION_COLUMNS))
    if not frames:
        return
    df = pd.concat(frames, ignore_index=True)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df = _drop_negative_battery(df, "rebuild from committed exports")
    conn.register("_rebuild", df)
    try:
        conn.execute("INSERT INTO generation SELECT date, iso, fuel_category, generation_mwh FROM _rebuild")
    finally:
        conn.unregister("_rebuild")
    print(f"Rebuilt generation table from {len(year_files)} committed year file(s): {len(df)} rows")


def upsert_generation(conn: duckdb.DuckDBPyConnection, df: pd.DataFrame) -> int:
    """Idempotently write rows to `generation`, replacing any existing rows
    for the same (date, iso, fuel_category) key. Safe to re-run."""
    if df.empty:
        return 0

    df = df[GENERATION_COLUMNS].copy()
    df["date"] = pd.to_datetime(df["date"]).dt.date

    before = len(df)
    df = df.dropna(subset=["generation_mwh"])
    dropped = before - len(df)
    if dropped:
        print(f"upsert_generation: dropped {dropped} row(s) with no valid generation_mwh reading")
    if df.empty:
        return 0
    df = _drop_negative_battery(df, "upsert")

    conn.register("_incoming", df)
    try:
        conn.execute("BEGIN TRANSACTION")
        conn.execute(
            """
            DELETE FROM generation
            WHERE EXISTS (
                SELECT 1 FROM _incoming i
                WHERE generation.date = i.date
                  AND generation.iso = i.iso
                  AND generation.fuel_category = i.fuel_category
            )
            """
        )
        conn.execute("INSERT INTO generation SELECT * FROM _incoming")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.unregister("_incoming")
    return len(df)


def log_ingestion(
    conn: duckdb.DuckDBPyConnection,
    iso: str,
    run_date,
    status: str,
    rows_written: int = 0,
    message: str = "",
) -> None:
    conn.execute(
        """
        INSERT INTO ingestion_log (iso, run_date, run_timestamp, status, rows_written, message)
        VALUES (?, ?, now(), ?, ?, ?)
        """,
        [iso, run_date, status, rows_written, message],
    )


def latest_date_for_iso(conn: duckdb.DuckDBPyConnection, iso: str):
    """Return the most recent date already stored for an ISO, or None if empty."""
    result = conn.execute(
        "SELECT MAX(date) FROM generation WHERE iso = ?", [iso]
    ).fetchone()
    return result[0] if result else None
