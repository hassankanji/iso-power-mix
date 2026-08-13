#!/usr/bin/env python
"""Diagnostic: why does EIA's US48 battery number sit *below* the sum of the
ISOs we track?

Run it on a GitHub runner (reconcile.yml, mode=diagnose-storage) - api.eia.gov
is not reachable from the dev sandbox.

The dashboard shows US48 battery discharge at ~59 GWh/day while CAISO alone is
~45 and ERCOT alone is ~25. A national total cannot be smaller than the sum of
its parts, so one of these is wrong. Two candidate explanations, and this
script measures both:

A. CLIPPING AN AGGREGATE IS NOT THE SUM OF CLIPPED PARTS. We build every
   storage figure as sum_over_intervals(max(net, 0)). Applied to a single BA
   that is the fleet's discharge energy. Applied to EIA's pre-summed US48
   series it is something else entirely: an hour where CAISO discharges 4 GW
   while the rest of the country charges 5 GW nets to -1 GW and books ZERO,
   silently deleting CAISO's 4 GWh. sum_BA(clip) >= clip(sum_BA) always, and
   the gap is exactly the discharge that happens during nationally-charging
   hours. Section 2 measures the gap.

B. PUMPED STORAGE IS BUCKETED DIFFERENTLY BY EIA AND BY THE ISOs. EIA has a
   dedicated PS code that we map to the storage bucket; the ISO feeds fold
   pumped hydro into their hydro column. So the same asset lands in different
   buckets depending on the source, which no amount of clipping fixes.
   Section 3 sizes PS against BAT.

Also checks (section 4) whether the respondents whose own feeds carry no
storage series at all - SWPP, NYIS, ISNE - have one at EIA, i.e. whether the
flat-zero lines on the dashboard are real zeros or just silence.
"""
import collections
import datetime as dt
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from src.connectors.base import http_session

API_BASE = "https://api.eia.gov/v2/electricity/rto"

# Every code EIA-930 uses for an energy-storage resource.
STORAGE_CODES = ["BAT", "PS", "OES", "UES"]

# EIA-930 publishes balancing authorities AND rolled-up regions through the
# same `respondent` facet. Summing everything would double-count the country
# several times over, so the aggregates are named here and excluded; whatever
# else appears is a real BA. (Section 1 prints anything unrecognised so a new
# aggregate code can't quietly slip into the sum.)
AGGREGATE_RESPONDENTS = {
    "US48", "CAL", "CAR", "CENT", "FLA", "MIDA", "MIDW",
    "NE", "NW", "NY", "SE", "SW", "TEN", "TEX",
}

# Our ISO -> EIA respondent, for the side-by-side in section 4.
OUR_RESPONDENTS = {
    "CAISO": "CISO", "ERCOT": "ERCO", "MISO": "MISO", "PJM": "PJM",
    "SPP": "SWPP", "NYISO": "NYIS", "ISONE": "ISNE",
}

DAYS = 7


def get_rows(session, route, params):
    key = os.environ["EIA_API_KEY"]
    rows, offset = [], 0
    while True:
        page = dict(params, api_key=key, length=5000, offset=offset)
        resp = session.get(f"{API_BASE}/{route}/data/", params=page, timeout=90)
        resp.raise_for_status()
        data = ((resp.json() or {}).get("response") or {}).get("data") or []
        rows.extend(data)
        if len(data) < 5000:
            return rows
        offset += 5000


def storage_hourly(session, start, end, respondent=None):
    """Every storage-code hourly reading in the window, for one respondent or
    (respondent=None) for all of them at once. Always bounded by `start` - an
    unbounded query makes EIA scan the whole history and 503 (see CLAUDE.md)."""
    params = {
        "frequency": "hourly",
        "data[0]": "value",
        "facets[fueltype][]": STORAGE_CODES,
        "start": f"{start.isoformat()}T00",
        "end": f"{end.isoformat()}T23",
    }
    if respondent:
        params["facets[respondent][]"] = respondent
    return get_rows(session, "fuel-type-data", params)


def parse(rows):
    """-> list of (respondent, period, code, mwh), numeric values only."""
    out = []
    for r in rows:
        try:
            v = float(r["value"])
        except (TypeError, ValueError, KeyError):
            continue
        out.append((
            str(r.get("respondent", "")).upper(),
            str(r.get("period", "")),
            str(r.get("fueltype", "")).upper(),
            v,
        ))
    return out


def discharge(records):
    """The number the pipeline stores: storage codes summed within each hour,
    that hour clipped at zero, then summed. One fleet, one net, per hour."""
    per_hour = collections.defaultdict(float)
    for _, period, _, v in records:
        per_hour[period] += v
    return sum(max(v, 0.0) for v in per_hour.values())


def net(records):
    return sum(v for _, _, _, v in records)


def main():
    load_dotenv()
    if not os.environ.get("EIA_API_KEY"):
        sys.exit("EIA_API_KEY not set")
    session = http_session()
    end = dt.date.today() - dt.timedelta(days=1)
    start = end - dt.timedelta(days=DAYS - 1)
    win = f"{start} .. {end}"

    print("=" * 78)
    print(f"1. WHO REPORTS STORAGE TO EIA-930 AT ALL   ({win})")
    print("=" * 78)
    all_rows = parse(storage_hourly(session, start, end))
    print(f"{len(all_rows):,} hourly storage readings across all respondents\n")

    by_respondent = collections.defaultdict(list)
    for rec in all_rows:
        by_respondent[rec[0]].append(rec)

    bas = sorted(r for r in by_respondent if r not in AGGREGATE_RESPONDENTS)
    aggregates = sorted(r for r in by_respondent if r in AGGREGATE_RESPONDENTS)
    print(f"balancing authorities reporting storage ({len(bas)}): {', '.join(bas)}")
    print(f"aggregates present ({len(aggregates)}): {', '.join(aggregates)}")
    print("\nIf a code above looks like a region rather than a BA, add it to")
    print("AGGREGATE_RESPONDENTS - it would otherwise double-count in section 2.")

    print(f"\n{'BA':<8}{'codes':<22}{'discharge GWh':>15}{'net GWh':>12}{'neg hrs':>9}")
    for ba in bas:
        recs = by_respondent[ba]
        codes = sorted({c for _, _, c, _ in recs})
        per_hour = collections.defaultdict(float)
        for _, period, _, v in recs:
            per_hour[period] += v
        nneg = sum(1 for v in per_hour.values() if v < 0)
        print(f"{ba:<8}{','.join(codes):<22}{discharge(recs)/1000:>15,.1f}"
              f"{net(recs)/1000:>12,.1f}{nneg:>9}")

    print()
    print("=" * 78)
    print("2. HYPOTHESIS A: clip(sum of BAs) vs sum of clip(each BA)")
    print("=" * 78)
    ba_records = [rec for rec in all_rows if rec[0] not in AGGREGATE_RESPONDENTS]
    sum_of_clipped = sum(discharge(by_respondent[ba]) for ba in bas)

    us48_recs = by_respondent.get("US48", [])
    clip_of_us48 = discharge(us48_recs)
    # Also clip the BA rows pooled by hour, to separate "EIA's US48 series is
    # its own thing" from "clipping an aggregate loses discharge".
    clip_of_ba_sum = discharge(ba_records)

    print(f"sum over BAs of each BA's clipped discharge   {sum_of_clipped/1000:>12,.1f} GWh")
    print(f"clip of all BAs pooled per hour               {clip_of_ba_sum/1000:>12,.1f} GWh")
    print(f"clip of EIA's own US48 series                 {clip_of_us48/1000:>12,.1f} GWh")
    if sum_of_clipped:
        lost = sum_of_clipped - clip_of_ba_sum
        print(f"\ndischarge deleted by clipping the aggregate:  {lost/1000:>12,.1f} GWh"
              f"  ({lost/sum_of_clipped*100:.1f}% of the true total)")
    print(f"\nper day: sum-of-parts {sum_of_clipped/1000/DAYS:,.1f} GWh/day vs "
          f"US48-clipped {clip_of_us48/1000/DAYS:,.1f} GWh/day")
    print("\nIf the sum-of-parts is materially larger, the US48 storage bucket")
    print("must be rebuilt as a sum of per-BA discharge, not a clipped aggregate.")

    print()
    print("=" * 78)
    print("3. HYPOTHESIS B: how much of the bucket is pumped storage, not battery")
    print("=" * 78)
    print(f"{'code':<6}{'BAs':>5}{'discharge GWh':>16}{'net GWh':>12}")
    for code in STORAGE_CODES:
        recs = [rec for rec in ba_records if rec[2] == code]
        if not recs:
            print(f"{code:<6}{0:>5}{'-':>16}{'-':>12}")
            continue
        n_bas = len({rec[0] for rec in recs})
        print(f"{code:<6}{n_bas:>5}{discharge(recs)/1000:>16,.1f}{net(recs)/1000:>12,.1f}")
    print("\nPS is pumped hydro. Every ISO feed we read folds pumped hydro into")
    print("its hydro column, so PS in the storage bucket is the same asset filed")
    print("under two different names depending on which source answered.")

    print()
    print("=" * 78)
    print("4. THE ISOs WHOSE OWN FEEDS CARRY NO STORAGE SERIES")
    print("=" * 78)
    print("Flat zero on the dashboard means one of two very different things:")
    print("the batteries did nothing, or nobody asked. This says which.\n")
    print(f"{'our ISO':<9}{'EIA BA':<9}{'codes':<22}{'discharge GWh/day':>19}")
    for iso, respondent in OUR_RESPONDENTS.items():
        recs = by_respondent.get(respondent, [])
        if not recs:
            print(f"{iso:<9}{respondent:<9}{'(no storage rows)':<22}{'-':>19}")
            continue
        codes = sorted({c for _, _, c, _ in recs})
        print(f"{iso:<9}{respondent:<9}{','.join(codes):<22}"
              f"{discharge(recs)/1000/DAYS:>19,.1f}")

    print()
    print("=" * 78)
    print("5. ERCOT CROSS-CHECK: EIA's ERCO vs our dashboard-derived number")
    print("=" * 78)
    print("We currently get ERCOT storage only from the live fuel-mix dashboard")
    print("(~25 GWh/day); the settlement XLSX has no storage column, so all of")
    print("ERCOT's history reads as zero. If ERCO agrees with the dashboard,")
    print("EIA can supply the missing history.\n")
    erco = by_respondent.get("ERCO", [])
    if erco:
        per_day = collections.defaultdict(list)
        for rec in erco:
            # Local ERCOT day; -6 is close enough to separate days for a
            # magnitude check.
            stamp = dt.datetime.strptime(rec[1], "%Y-%m-%dT%H") - dt.timedelta(hours=6)
            per_day[stamp.date()].append(rec)
        print(f"{'local day':<12}{'discharge GWh':>15}{'net GWh':>12}")
        for day in sorted(per_day):
            print(f"{str(day):<12}{discharge(per_day[day])/1000:>15,.1f}"
                  f"{net(per_day[day])/1000:>12,.1f}")
    else:
        print("ERCO reports no storage codes at all in this window.")


if __name__ == "__main__":
    main()
