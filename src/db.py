"""DuckDB storage layer: schema init and idempotent upserts."""
import pathlib

import duckdb
import pandas as pd

from src.schema import GENERATION_COLUMNS

DB_PATH = pathlib.Path(__file__).resolve().parent.parent / "data" / "power_mix.duckdb"

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
    return conn


def upsert_generation(conn: duckdb.DuckDBPyConnection, df: pd.DataFrame) -> int:
    """Idempotently write rows to `generation`, replacing any existing rows
    for the same (date, iso, fuel_category) key. Safe to re-run."""
    if df.empty:
        return 0

    df = df[GENERATION_COLUMNS].copy()
    df["date"] = pd.to_datetime(df["date"]).dt.date

    conn.register("_incoming", df)
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
