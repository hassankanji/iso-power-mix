#!/usr/bin/env python
"""One-off diagnostic: how does EIA-930 actually report storage, and how fresh
is the hourly feed?

Run it on a GitHub runner (workflow_dispatch, see .github/workflows/
diagnose-storage.yml) - api.eia.gov is not reachable from the dev sandbox.

It answers three questions the dashboard depends on:

1. Sign convention. Are BAT/PS values net (negative while charging) or
   discharge-only? Printed as positive-sum / negative-sum / net per day.
2. Route agreement. Does /daily-fuel-type-data equal the sum of
   /fuel-type-data hourly values for the same local day? The pipeline uses
   the daily route for the US48 overlay, the Live tab uses hourly - if they
   disagree on storage, that alone explains the dashboard mismatch.
3. Freshness. What is the newest hour EIA has for US48 right now, and how
   far behind the wall clock is it?
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
STORAGE_CODES = ("BAT", "PS")
RESPONDENTS = ["US48", "CISO", "ERCO", "MISO", "PJM", "SWPP", "NYIS", "ISNE"]
DAYS = 5


def get_rows(session, route, params):
    key = os.environ["EIA_API_KEY"]
    rows, offset = [], 0
    while True:
        page = dict(params, api_key=key, length=5000, offset=offset)
        resp = session.get(f"{API_BASE}/{route}/data/", params=page, timeout=60)
        resp.raise_for_status()
        data = ((resp.json() or {}).get("response") or {}).get("data") or []
        rows.extend(data)
        if len(data) < 5000:
            return rows
        offset += 5000


def hourly(session, respondent, start, end):
    return get_rows(
        session,
        "fuel-type-data",
        {
            "frequency": "hourly",
            "data[0]": "value",
            "facets[respondent][]": respondent,
            "start": f"{start.isoformat()}T00",
            "end": f"{end.isoformat()}T23",
        },
    )


def daily(session, respondent, timezone, start, end):
    return get_rows(
        session,
        "daily-fuel-type-data",
        {
            "frequency": "daily",
            "data[0]": "value",
            "facets[respondent][]": respondent,
            "facets[timezone][]": timezone,
            "start": start.isoformat(),
            "end": end.isoformat(),
        },
    )


def split_sums(values):
    """(positive sum, negative sum, net) - the three numbers that separate a
    net series from a discharge-only one."""
    pos = sum(v for v in values if v > 0)
    neg = sum(v for v in values if v < 0)
    return pos, neg, pos + neg


def main():
    load_dotenv()
    if not os.environ.get("EIA_API_KEY"):
        sys.exit("EIA_API_KEY not set")
    session = http_session()
    end = dt.date.today()
    start = end - dt.timedelta(days=DAYS)

    print("=" * 78)
    print(f"1+2. STORAGE SIGN CONVENTION AND ROUTE AGREEMENT  ({start} .. {end})")
    print("=" * 78)
    for respondent in RESPONDENTS:
        try:
            rows = hourly(session, respondent, start, end)
        except Exception as e:
            print(f"\n[{respondent}] hourly route failed: {e}")
            continue
        by_code_day = collections.defaultdict(list)
        codes_seen = collections.Counter()
        for r in rows:
            code = str(r.get("fueltype", "")).upper()
            codes_seen[code] += 1
            try:
                v = float(r["value"])
            except (TypeError, ValueError, KeyError):
                continue
            by_code_day[(code, str(r["period"])[:10])].append(v)

        print(f"\n[{respondent}] fuel codes present: {sorted(codes_seen)}")
        for code in STORAGE_CODES:
            days = sorted({d for c, d in by_code_day if c == code})
            if not days:
                print(f"  {code}: not reported")
                continue
            print(f"  {code} (hourly, UTC days) — MWh")
            print(f"    {'day':<12}{'n':>4}{'neg hrs':>9}{'pos sum':>12}{'neg sum':>12}{'net':>12}")
            for d in days:
                vals = by_code_day[(code, d)]
                pos, neg, net = split_sums(vals)
                nneg = sum(1 for v in vals if v < 0)
                print(f"    {d:<12}{len(vals):>4}{nneg:>9}{pos:>12,.0f}{neg:>12,.0f}{net:>12,.0f}")

        # Same window through the daily route, for the storage codes only.
        tz = "Eastern" if respondent in ("US48", "PJM", "MISO", "NYIS", "ISNE") else (
            "Pacific" if respondent == "CISO" else "Central"
        )
        try:
            drows = daily(session, respondent, tz, start, end)
        except Exception as e:
            print(f"  daily route failed: {e}")
            continue
        dstore = collections.defaultdict(dict)
        for r in drows:
            code = str(r.get("fueltype", "")).upper()
            if code not in STORAGE_CODES:
                continue
            try:
                dstore[str(r["period"])[:10]][code] = float(r["value"])
            except (TypeError, ValueError, KeyError):
                continue
        if dstore:
            print(f"  daily route (timezone={tz}) — MWh per day")
            for d in sorted(dstore):
                print(f"    {d:<12}" + "  ".join(f"{c}={v:,.0f}" for c, v in sorted(dstore[d].items())))
        else:
            print(f"  daily route (timezone={tz}): no storage rows")

    print()
    print("=" * 78)
    print("3. FRESHNESS OF THE HOURLY US48 FEED")
    print("=" * 78)
    rows = hourly(session, "US48", end - dt.timedelta(days=2), end)
    by_period = collections.defaultdict(set)
    for r in rows:
        by_period[str(r["period"])].add(str(r.get("fueltype", "")).upper())
    now = dt.datetime.now(dt.timezone.utc)
    print(f"now (UTC):      {now:%Y-%m-%dT%H}:{now:%M}")
    print(f"periods found:  {len(by_period)}")
    full = max((len(v) for v in by_period.values()), default=0)
    print(f"max fuel codes in any hour: {full}")
    print(f"\n{'period (UTC)':<16}{'codes':>7}{'hours behind now':>19}")
    for p in sorted(by_period, reverse=True)[:12]:
        stamp = dt.datetime.strptime(p, "%Y-%m-%dT%H").replace(tzinfo=dt.timezone.utc)
        behind = (now - stamp).total_seconds() / 3600
        flag = "" if len(by_period[p]) == full else "  <- partial hour"
        print(f"{p:<16}{len(by_period[p]):>7}{behind:>19.1f}{flag}")


if __name__ == "__main__":
    main()
