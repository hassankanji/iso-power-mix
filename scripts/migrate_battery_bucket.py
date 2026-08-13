#!/usr/bin/env python
"""One-off migration: rename the `storage` bucket to `battery` in the
committed exports, and delete the rows the old negative-storage clamp
destroyed.

Runs offline - connect() rebuilds the database from docs/data/*.json and
export_all() writes it straight back, so this needs no network and no
credentials. Kept in the repo as the record of what was done to the data,
alongside the diagnostic that justified it.

WHAT THE CLAMP DID. Until 2026-08-07 a negative daily storage total was
clamped to 0 on its way into the database (src/db.py). The intent was to keep
a net-convention leak from breaking the stacked charts. The effect was worse
than the leak: zero is a measurement, so 39 ISO-days of "we recorded this
wrong" became 39 days of "the batteries did nothing", and no later pass would
revisit them - a date with a zero in it does not look missing to anything.
That is the cliff in ERCOT's battery line the dashboard has been showing: flat
zero for its entire history, then 25 GWh/day from the moment the per-interval
clipping fix shipped.

The 39 victims are known exactly, by diffing the exports either side of the
change (commits d083745 and 9b59eb7):

    ERCOT  2026-07-01 .. 2026-08-05   34 days
    MISO   2025-12-20 .. 2025-12-24    5 days

The whole window goes, not only the rows that came out as zero. A day whose
net happened to land positive was not clamped, so it kept its number - ERCOT
2026-07-06 reads 5.3 GWh and 2026-07-14 reads 4.5 GWh - but that number is
still a net, produced by the same pre-clipping code path over the same days,
against a true discharge nearer 25 GWh. Keeping it because it survived the
clamp would leave the wrong number in place precisely where it looks most
plausible.

They are deleted rather than patched. Deleting restores the only honest state
- "this ISO has no battery figure for this day" - which reads as a gap on the
chart and, unlike a zero, is something the pipeline's EIA bucket fill will
come back for and replace with a real measurement.

The rename needs no explicit step: db.py aliases the old name on the way in
and export.py writes every year file from CANONICAL_CATEGORIES on the way out,
so a load-then-save is the whole migration.
"""
import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from src.db import connect
from src.export import export_all

# (iso, first date, last date) - the windows the pre-clipping code path wrote,
# inclusive. Bounded by the first day each ISO produced a battery row and the
# day the per-interval clipping fix shipped.
CLAMPED = [
    ("ERCOT", dt.date(2026, 7, 1), dt.date(2026, 8, 5)),
    ("MISO", dt.date(2025, 12, 20), dt.date(2025, 12, 24)),
]


def main():
    conn = connect()

    before = conn.execute("SELECT COUNT(*) FROM generation WHERE fuel_category = 'battery'").fetchone()[0]
    print(f"battery rows after rename: {before:,}")

    for iso, start, end in CLAMPED:
        deleted = conn.execute(
            """
            DELETE FROM generation
            WHERE iso = ? AND fuel_category = 'battery' AND date BETWEEN ? AND ?
            """,
            [iso, start, end],
        ).fetchone()[0]
        print(f"{iso} {start}..{end}: deleted {deleted} net-convention battery row(s)")

    export_all(conn)
    conn.close()


if __name__ == "__main__":
    main()
