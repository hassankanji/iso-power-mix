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

from src import eia
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
EIA_FILL_MAX_DAYS_PER_RUN = 120
US48_LOOKBACK_DAYS = 7


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
            # Clamp so one --start covering several ISOs doesn't burn
            # thousands of doomed requests on dates before an ISO existed.
            start_date = max(start_override, connector.EARLIEST_DATE)
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
        update_us48_reference(conn, end_date)
        sync_storage_from_eia(conn, targets, end_date)

    conn.close()
    return results


STORAGE_LOOKBACK_DAYS = 10


def sync_storage_from_eia(conn, targets, end_date: dt.date, full: bool = False) -> None:
    """Override each ISO's `storage` category with EIA's net battery figure.

    The ISOs report storage on incompatible conventions: CAISO and ERCOT
    publish true NET output (negative while the fleet charges), while MISO
    and PJM's public generation feeds only include discharge (so they read
    small-positive and never negative). For a generation-mix stack that must
    sum to net generation, NET is the correct convention - counting battery
    discharge as fresh generation double-counts energy already tallied when
    it was first produced. EIA-930 reports net battery per balancing
    authority on one consistent convention, so sourcing storage from EIA
    makes every ISO agree.

    Incremental by default (re-syncs the trailing window, since EIA revises
    recent days); pass full=True for the one-time history backfill. Upserts
    only the storage rows, leaving every other fuel from the ISO's own feed.
    Silently skipped without an EIA key - storage then keeps native values.
    """
    if not eia.has_key():
        return
    isos = [i for i in targets if i in eia.RESPONDENTS and i != "US48"]
    start = eia.EIA_EARLIEST if full else max(eia.EIA_EARLIEST, end_date - dt.timedelta(days=STORAGE_LOOKBACK_DAYS - 1))
    for iso in isos:
        try:
            total = 0
            cur = start
            while cur <= end_date:
                chunk_end = min(cur + dt.timedelta(days=182), end_date)
                df = eia.fetch_daily(iso, cur, chunk_end)
                storage = df[df["fuel_category"] == "storage"].copy() if not df.empty else df
                if not storage.empty:
                    storage["iso"] = iso
                    total += upsert_generation(conn, storage[["date", "iso", "fuel_category", "generation_mwh"]])
                cur = chunk_end + dt.timedelta(days=1)
            if total:
                avg = conn.execute(
                    "SELECT AVG(generation_mwh) FROM generation WHERE iso=? AND fuel_category='storage' "
                    "AND date > ?", [iso, end_date - dt.timedelta(days=30)],
                ).fetchone()[0]
                print(f"[{iso}] storage synced from EIA: {total} rows (last-30d avg {avg:.0f} MWh/day)")
        except Exception as e:
            print(f"[{iso}] storage sync from EIA failed ({e}) - keeping native storage")


def update_us48_reference(conn, end_date: dt.date) -> None:
    """Maintain the iso='US48' lower-48 national reference series from EIA.
    First run backfills from EIA's 2018-07-01 start; after that it's an
    incremental pull with a small overlap (EIA revises recent days). Skipped
    silently without an EIA key - it's an overlay, never load-bearing."""
    if not eia.has_key():
        print("[US48] skipped: EIA_API_KEY not set (national reference overlay stays absent - see README)")
        return
    latest = latest_date_for_iso(conn, "US48")
    start = eia.EIA_EARLIEST if latest is None else min(
        latest + dt.timedelta(days=1), end_date - dt.timedelta(days=US48_LOOKBACK_DAYS - 1)
    )
    if start > end_date:
        print("[US48] reference series up to date")
        return
    try:
        # Chunked so the first-ever backfill (2018 -> today) stays within
        # polite request sizes; incremental runs are a single small chunk.
        total = 0
        cur = start
        while cur <= end_date:
            chunk_end = min(cur + dt.timedelta(days=182), end_date)
            df = eia.fetch_daily("US48", cur, chunk_end)
            if not df.empty:
                df["iso"] = "US48"
                total += upsert_generation(conn, df[["date", "iso", "fuel_category", "generation_mwh"]])
            cur = chunk_end + dt.timedelta(days=1)
        log_ingestion(conn, "US48", end_date, "success", total, "EIA lower-48 reference")
        print(f"[US48] wrote {total} reference rows ({start} .. {end_date})")
    except Exception as e:
        # Never let the reference overlay fail the run.
        log_ingestion(conn, "US48", end_date, "failed", 0, str(e))
        print(f"[US48] reference update failed ({e}) - continuing")


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

    # Second line of defense: whatever the ISO's own source still can't
    # produce, fill with real EIA-930 measurements (2018-07-01 onward).
    # Runs regardless of connector credentials - EIA needs only its own key.
    if eia.has_key():
        for iso in targets:
            if iso not in eia.RESPONDENTS or iso == "US48":
                continue
            fillable = [d for d in missing_by_iso.get(iso, []) if d >= eia.EIA_EARLIEST]
            fillable = fillable[-EIA_FILL_MAX_DAYS_PER_RUN:]
            if not fillable:
                continue
            filled_total = 0
            for r_start, r_end in dates_to_ranges(fillable):
                try:
                    df = eia.fetch_daily(iso, r_start, r_end)
                except Exception as e:
                    print(f"[{iso}] EIA gap fill {r_start}..{r_end} failed ({e}) - will retry next run")
                    continue
                if df.empty:
                    continue
                # Only fill days that are actually missing - never overwrite
                # the ISO's own data with EIA's.
                df = df[df["date"].isin(set(fillable))]
                if df.empty:
                    continue
                df["iso"] = iso
                filled_total += upsert_generation(conn, df[["date", "iso", "fuel_category", "generation_mwh"]])
            if filled_total:
                still = find_missing_dates(conn, iso)
                n_filled = len(missing_by_iso[iso]) - len(still)
                print(f"[{iso}] EIA gap fill: {n_filled} day(s) filled with EIA-930 data ({filled_total} rows)")
                log_ingestion(conn, iso, dt.date.today(), "eia_gap_fill", filled_total, f"filled {n_filled} day(s)")
                missing_by_iso[iso] = still

    # Drop stale attempt counters for dates that are no longer missing
    # (e.g. filled by EIA).
    for iso, iso_attempts in attempts.items():
        currently_missing = {d.isoformat() for d in missing_by_iso.get(iso, [])}
        for key in [k for k in iso_attempts if k not in currently_missing]:
            iso_attempts.pop(key)

    save_state(state, missing_by_iso)
