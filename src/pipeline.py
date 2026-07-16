"""Orchestrates per-ISO fetch -> normalize -> upsert.

Backfills from each connector's EARLIEST_DATE the first time an ISO has no
data yet; pulls incrementally after that. One ISO's failure is caught and
logged, never blocking the others.

Incremental runs deliberately overlap a trailing lookback window rather than
starting strictly at (latest stored date + 1). Upserts make the overlap
idempotent, and it self-heals three failure patterns observed in production:
transient per-day fetch errors that would otherwise become permanent silent
gaps (ISO-NE 429s), sources that publish in arrears and fill earlier days
late (ERCOT's current-year file - which is why ERCOT overrides the lookback
to 45 days, so settlement data replaces its live-dashboard placeholder rows
once published), and sources whose freshest day is initially sparse and
firms up a day later (MISO's "yesterday" feed).

ISOs whose credentials aren't configured are reported as
"skipped_no_credentials" rather than "failed", so a missing PJM key (still
waiting on PJM to approve the account) doesn't page anyone every morning.

After the incremental pull, a bounded gap-repair pass retries dates that are
missing inside an ISO's covered span (see src/gaps.py). Each run retries at
most GAP_REPAIR_MAX_DAYS_PER_RUN days per ISO, newest gaps first, and a date
the source has failed to produce MAX_ATTEMPTS times is marked permanently
unavailable and never retried again.
"""
from __future__ import annotations

import datetime as dt
import os
import traceback

from src.connectors import CONNECTORS
from src.db import connect, latest_date_for_iso, log_ingestion, upsert_generation
from src.gaps import (
    MAX_ATTEMPTS,
    dates_to_ranges,
    find_missing_dates,
    load_state,
    save_state,
)

LOOKBACK_DAYS = 7
GAP_REPAIR_MAX_DAYS_PER_RUN = 30


def _missing_env(names: list[str]) -> list[str]:
    return [v for v in names if not os.environ.get(v)]


def run_all(
    isos: list[str] | None = None,
    start_override: dt.date | None = None,
    end_override: dt.date | None = None,
) -> dict[str, str]:
    conn = connect()
    end_date = end_override or (dt.date.today() - dt.timedelta(days=1))
    targets = isos or list(CONNECTORS.keys())
    results: dict[str, str] = {}

    for iso in targets:
        connector = CONNECTORS[iso]

        missing = _missing_env(getattr(connector, "REQUIRED_ENV", []))
        if missing:
            print(f"[{iso}] skipped: credentials not configured ({', '.join(missing)}) - see README")
            log_ingestion(conn, iso, end_date, "skipped_no_credentials", 0, f"missing env: {missing}")
            results[iso] = "skipped_no_credentials"
            continue

        latest = latest_date_for_iso(conn, iso)
        lookback = getattr(connector, "LOOKBACK_DAYS", LOOKBACK_DAYS)

        if start_override:
            start_date = start_override
        elif latest is None:
            start_date = connector.EARLIEST_DATE
        else:
            start_date = min(
                latest + dt.timedelta(days=1),
                end_date - dt.timedelta(days=lookback - 1),
            )
            start_date = max(start_date, connector.EARLIEST_DATE)

        if start_date > end_date:
            print(f"[{iso}] up to date (latest={latest}), nothing to fetch")
            results[iso] = "up_to_date"
            continue

        print(f"[{iso}] fetching {start_date} .. {end_date}")
        try:
            df = connector.fetch_range(start_date, end_date)
            rows = upsert_generation(conn, df)
            log_ingestion(conn, iso, end_date, "success", rows, "")
            print(f"[{iso}] wrote {rows} rows")
            results[iso] = "success"
        except Exception as e:
            log_ingestion(conn, iso, end_date, "failed", 0, str(e))
            print(f"[{iso}] FAILED: {e}")
            traceback.print_exc()
            results[iso] = "failed"

    # Gap repair only makes sense for a normal incremental run - an explicit
    # --start/--end run already IS a manual repair of that window.
    if start_override is None and end_override is None:
        repair_gaps(conn, targets, results)

    conn.close()
    return results


def repair_gaps(conn, targets: list[str], results: dict[str, str]) -> None:
    """Retry dates missing inside each ISO's covered span, bounded per run,
    and persist attempt counts + a summary to docs/data/gaps.json."""
    state = load_state()
    attempts: dict = state.setdefault("attempts", {})
    missing_by_iso: dict[str, list[dt.date]] = {}

    for iso in CONNECTORS:
        missing_by_iso[iso] = find_missing_dates(conn, iso)

    for iso in targets:
        connector = CONNECTORS[iso]
        missing = missing_by_iso.get(iso, [])
        if not missing:
            continue
        # Don't burn retry attempts when this ISO couldn't fetch at all today,
        # or when the env vars its historical path needs aren't set.
        if results.get(iso) in ("failed", "skipped_no_credentials"):
            continue
        repair_env = _missing_env(getattr(connector, "GAP_REPAIR_ENV", []))
        if repair_env:
            print(
                f"[{iso}] {len(missing)} gap day(s) exist but repairing them needs "
                f"{', '.join(repair_env)} - skipping gap repair (see README)"
            )
            continue

        iso_attempts: dict = attempts.setdefault(iso, {})
        # Dates still inside the connector's lookback window are re-fetched by
        # every normal incremental run anyway (e.g. ERCOT's last 45 days,
        # which legitimately stay empty until the settlement workbook lands) -
        # retrying them here would just burn attempts toward a false
        # "permanently unavailable" verdict.
        lookback_edge = dt.date.today() - dt.timedelta(days=getattr(connector, "LOOKBACK_DAYS", LOOKBACK_DAYS))
        candidates = [
            d
            for d in missing
            if d < lookback_edge and iso_attempts.get(d.isoformat(), 0) < MAX_ATTEMPTS
        ]
        given_up = sum(1 for d in missing if iso_attempts.get(d.isoformat(), 0) >= MAX_ATTEMPTS)
        # Newest gaps matter most to a daily reader - retry those first.
        candidates = sorted(sorted(candidates, reverse=True)[:GAP_REPAIR_MAX_DAYS_PER_RUN])
        if not candidates:
            if given_up:
                print(f"[{iso}] {given_up} gap day(s) remain but are marked unavailable at the source (no more retries)")
            continue

        print(f"[{iso}] gap repair: retrying {len(candidates)} missing day(s)")
        attempted_ok: set[dt.date] = set()
        for r_start, r_end in dates_to_ranges(candidates):
            try:
                df = connector.fetch_range(r_start, r_end)
                upsert_generation(conn, df)
                attempted_ok.update(
                    r_start + dt.timedelta(days=i) for i in range((r_end - r_start).days + 1)
                )
            except Exception as e:
                print(f"[{iso}] gap repair {r_start}..{r_end} failed ({e}) - will retry next run")

        still_missing = set(find_missing_dates(conn, iso))
        filled = [d for d in candidates if d not in still_missing]
        if filled:
            print(f"[{iso}] gap repair filled {len(filled)} day(s): {', '.join(str(d) for d in filled)}")
        # Count an attempt only where the source responded and still had
        # nothing - a network error shouldn't move a date toward "given up".
        for d in attempted_ok:
            key = d.isoformat()
            if d in still_missing:
                iso_attempts[key] = iso_attempts.get(key, 0) + 1
            else:
                iso_attempts.pop(key, None)
        missing_by_iso[iso] = sorted(still_missing)

    # Drop stale attempt counters for dates that are no longer missing
    # (e.g. filled by the EIA-930 backfill script).
    for iso, iso_attempts in attempts.items():
        currently_missing = {d.isoformat() for d in missing_by_iso.get(iso, [])}
        for key in [k for k in iso_attempts if k not in currently_missing]:
            iso_attempts.pop(key)

    save_state(state, missing_by_iso)
