"""Gap detection and repair state.

A "gap" is a date strictly inside an ISO's covered span (between its earliest
and latest stored dates) with no rows at all. Gaps come from source-side
holes (MISO's LGI API has days that 404/500, CAISO has a couple of days whose
history CSV never existed) or transient fetch failures during a backfill.

Repair state lives in docs/data/gaps.json (committed to the repo, so it
survives the fresh checkout every GitHub Actions run starts from). Each
missing date carries an attempt counter; once a date has been retried
MAX_ATTEMPTS times without the source ever producing data, it is treated as
permanently unavailable at the source and no longer retried. The dashboard
and README both read this file, so remaining holes are always visible rather
than silently baked into the charts.
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib

GAPS_PATH = pathlib.Path(__file__).resolve().parent.parent / "docs" / "data" / "gaps.json"
MAX_ATTEMPTS = 5


def find_missing_dates(conn, iso: str) -> list[dt.date]:
    """Dates with zero rows strictly inside [earliest, latest] for this ISO."""
    rows = conn.execute(
        """
        WITH span AS (SELECT MIN(date) AS f, MAX(date) AS l FROM generation WHERE iso = ?),
        cal AS (
            SELECT UNNEST(generate_series(span.f, span.l, INTERVAL 1 DAY))::DATE AS date
            FROM span WHERE span.f IS NOT NULL
        )
        SELECT cal.date FROM cal
        LEFT JOIN (SELECT DISTINCT date FROM generation WHERE iso = ?) have USING (date)
        WHERE have.date IS NULL
        ORDER BY cal.date
        """,
        [iso, iso],
    ).fetchall()
    return [r[0] for r in rows]


def load_state() -> dict:
    if GAPS_PATH.exists():
        with open(GAPS_PATH) as f:
            return json.load(f)
    return {"attempts": {}}


def save_state(state: dict, missing_by_iso: dict[str, list[dt.date]]) -> None:
    """Persist attempt counters plus a human/dashboard-readable summary of
    every currently-missing date and whether it's still being retried."""
    state["updated"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    state["summary"] = {
        iso: {
            "missing_days": len(dates),
            "given_up": sorted(
                d.isoformat()
                for d in dates
                if state["attempts"].get(iso, {}).get(d.isoformat(), 0) >= MAX_ATTEMPTS
            ),
            "still_retrying": sorted(
                d.isoformat()
                for d in dates
                if state["attempts"].get(iso, {}).get(d.isoformat(), 0) < MAX_ATTEMPTS
            ),
        }
        for iso, dates in missing_by_iso.items()
        if dates
    }
    GAPS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(GAPS_PATH, "w") as f:
        json.dump(state, f, indent=1, sort_keys=True)


def patch_state(mutate) -> None:
    """Apply an in-place edit to the persisted state, leaving the summary
    block alone. For passes that run after save_state and only need to
    remember a little bookkeeping of their own - the EIA bucket fill's record
    of how far back EIA's coverage actually reaches, so it stops asking about
    years that predate a respondent's battery series."""
    state = load_state()
    mutate(state)
    GAPS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(GAPS_PATH, "w") as f:
        json.dump(state, f, indent=1, sort_keys=True)


def dates_to_ranges(dates: list[dt.date]) -> list[tuple[dt.date, dt.date]]:
    """Compress a sorted date list into inclusive contiguous ranges."""
    ranges: list[tuple[dt.date, dt.date]] = []
    if not dates:
        return ranges
    start = prev = dates[0]
    for d in dates[1:]:
        if (d - prev).days == 1:
            prev = d
        else:
            ranges.append((start, prev))
            start = prev = d
    ranges.append((start, prev))
    return ranges
