#!/usr/bin/env python
"""Is genuinely live U.S. generation data reachable, and by whom?

Run on a GitHub runner (reconcile.yml, mode=diagnose-live) - the dev sandbox
cannot reach eia.gov or the ISO hosts.

The Hourly tab reads EIA-930's US48 fuel-type series, which runs ~15 hours
behind. This measures the alternatives and, crucially, whether a STATIC page
could use them: the dashboard is GitHub Pages with no server, so a source is
only usable from the browser if it sends an Access-Control-Allow-Origin header
that permits our origin. A source without CORS is still usable, but only via a
scheduled server-side fetch that commits the result.

Prints, for each candidate:
  freshness  - how far behind the wall clock its newest reading is
  CORS       - whether a browser on hassankanji.github.io may read it directly

MEASURED 2026-08-11 12:16 UTC (run 31490371750) - the answer to "can we get
live data", kept here so nobody re-derives it:

  source                        freshness      CORS
  EIA fuel-type-data US48       9.3 h behind   YES (*)
  EIA region-data US48 (NG)     9.3 h behind   YES (*)
  CAISO current fuelsource.csv  6 MIN behind   no
  ERCOT dashboard fuel-mix      5 MIN behind   no (ACAO: https://mis.ercot.com)
  MISO public FuelMix           near real-time YES (*)
  NYISO rtfuelmix               16 MIN behind  no
  SPP GenMix365                 daily file     no

Two conclusions:

1. EIA is not the way to get live data, and there is no fresher EIA route to
   switch to - region-data (total net generation, no fuel split) lags exactly
   as much as the by-fuel series, so even a live national TOTAL is out. EIA
   is a settlement-grade aggregator, not a real-time feed.

2. The ISOs themselves ARE live - 5 to 16 minutes - and they are where the
   gas burn a trader cares about actually happens. But only MISO sends a
   permissive CORS header, so a static GitHub Pages site cannot read the
   others from the browser. Using them needs a scheduled job that fetches
   server-side and commits the result, which caps freshness at the cron
   cadence. Note GitHub's scheduler routinely runs crons 30-90 minutes late
   (see CLAUDE.md), so an Actions-based collector realistically delivers
   15-60 minute data, not 5-minute data.
"""
import datetime as dt
import io
import json
import os
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from src.connectors.base import http_session

ORIGIN = "https://hassankanji.github.io"
EIA_BASE = "https://api.eia.gov/v2/electricity/rto"


def now_utc():
    return dt.datetime.now(dt.timezone.utc)


def hours_behind(stamp):
    if stamp is None:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=dt.timezone.utc)
    return (now_utc() - stamp).total_seconds() / 3600


def cors_verdict(resp):
    """What a browser would be allowed to do with this response."""
    acao = resp.headers.get("access-control-allow-origin")
    if acao is None:
        return "NO  (no ACAO header - browser fetch blocked)"
    if acao == "*" or acao == ORIGIN:
        return f"YES (ACAO: {acao})"
    return f"NO  (ACAO: {acao} - not our origin)"


def report(name, note, resp, stamp):
    hb = hours_behind(stamp)
    lag = "unknown" if hb is None else (
        f"{hb*60:.0f} min behind" if hb < 2 else f"{hb:.1f} h behind"
    )
    print(f"\n  {name}")
    print(f"    status    {resp.status_code if resp is not None else 'n/a'}")
    print(f"    newest    {stamp if stamp else 'could not parse'}  ({lag})")
    print(f"    CORS      {cors_verdict(resp) if resp is not None else 'n/a'}")
    if note:
        print(f"    note      {note}")


def eia_newest(session, route, extra):
    key = os.environ["EIA_API_KEY"]
    params = {
        "api_key": key,
        "frequency": "hourly",
        "data[0]": "value",
        "facets[respondent][]": "US48",
        "start": (now_utc() - dt.timedelta(days=3)).strftime("%Y-%m-%dT%H"),
        "sort[0][column]": "period",
        "sort[0][direction]": "desc",
        "length": 500,
        **extra,
    }
    resp = session.get(f"{EIA_BASE}/{route}/data/", params=params,
                       headers={"Origin": ORIGIN}, timeout=60)
    rows = ((resp.json() or {}).get("response") or {}).get("data") or []
    stamp = None
    if rows:
        newest = max(str(r["period"]) for r in rows)
        stamp = dt.datetime.strptime(newest, "%Y-%m-%dT%H").replace(tzinfo=dt.timezone.utc)
    return resp, stamp, rows


def main():
    load_dotenv()
    session = http_session()
    print("=" * 78)
    print(f"HOW LIVE CAN WE GET?   (clock: {now_utc():%Y-%m-%d %H:%M} UTC)")
    print("=" * 78)

    # ---------------------------------------------------------------- EIA
    print("\n--- EIA-930, the only free NATIONAL aggregator ---")
    if os.environ.get("EIA_API_KEY"):
        try:
            resp, stamp, rows = eia_newest(session, "fuel-type-data", {})
            report("EIA fuel-type-data (US48) - what the Hourly tab uses today",
                   f"{len(rows)} rows; this is the by-fuel split", resp, stamp)
        except Exception as e:
            print(f"  fuel-type-data failed: {e}")
        try:
            # region-data carries demand / total net generation / interchange -
            # no fuel split, but EIA publishes it on a different schedule.
            resp, stamp, rows = eia_newest(session, "region-data", {"facets[type][]": "NG"})
            report("EIA region-data (US48, type=NG) - total net generation, NO fuel split",
                   f"{len(rows)} rows; if this is fresher, a live TOTAL is possible "
                   "even when the fuel split is not", resp, stamp)
        except Exception as e:
            print(f"  region-data failed: {e}")
    else:
        print("  EIA_API_KEY not set - skipping")

    # ------------------------------------------------- ISO real-time feeds
    print("\n\n--- ISO real-time fuel mix (no auth). ~65% of U.S. generation ---")

    def get(url, **kw):
        return session.get(url, headers={"Origin": ORIGIN}, timeout=45, **kw)

    # CAISO: 5-minute fuel source for the current day
    try:
        r = get("https://www.caiso.com/outlook/current/fuelsource.csv")
        stamp = None
        if r.ok:
            lines = [l for l in r.text.strip().splitlines() if l.strip()]
            last = lines[-1].split(",")
            # first column is local time "HH:MM"; date is today in Pacific
            m = re.match(r"^(\d{1,2}):(\d{2})$", last[0].strip())
            if m:
                pac = now_utc() - dt.timedelta(hours=7)
                stamp = pac.replace(hour=int(m.group(1)), minute=int(m.group(2)),
                                    second=0, microsecond=0) + dt.timedelta(hours=7)
        report("CAISO current fuelsource.csv", f"{len(r.text)} bytes, 5-min resolution", r, stamp)
    except Exception as e:
        print(f"\n  CAISO failed: {e}")

    # ERCOT: dashboard fuel-mix JSON (5-minute telemetry)
    try:
        r = get("https://www.ercot.com/api/1/services/read/dashboards/fuel-mix.json")
        stamp = None
        if r.ok:
            data = r.json()
            stamps = re.findall(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", json.dumps(data)[:400000])
            if stamps:
                stamp = dt.datetime.strptime(max(stamps), "%Y-%m-%d %H:%M:%S") + dt.timedelta(hours=5)
        report("ERCOT dashboard fuel-mix.json", "5-min telemetry; timestamps are CPT (+5 to UTC)", r, stamp)
    except Exception as e:
        print(f"\n  ERCOT failed: {e}")

    # MISO: public no-auth fuel mix
    try:
        r = get("https://public-api.misoenergy.org/api/FuelMix?returnType=json")
        stamp = None
        if r.ok:
            body = r.json()
            m = re.search(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}", json.dumps(body)[:200000])
            if m:
                stamp = dt.datetime.strptime(m.group(0).replace("T", " "), "%Y-%m-%d %H:%M") + dt.timedelta(hours=5)
        report("MISO public FuelMix", "no auth; near real-time", r, stamp)
    except Exception as e:
        print(f"\n  MISO failed: {e}")

    # NYISO: real-time fuel mix CSV for today
    try:
        ymd = (now_utc() - dt.timedelta(hours=4)).strftime("%Y%m%d")
        r = get(f"http://mis.nyiso.com/public/csv/rtfuelmix/{ymd}rtfuelmix.csv")
        stamp = None
        if r.ok:
            lines = [l for l in r.text.strip().splitlines() if l.strip()]
            m = re.match(r'^"?(\d{2}/\d{2}/\d{4} \d{2}:\d{2}:\d{2})', lines[-1])
            if m:
                stamp = dt.datetime.strptime(m.group(1), "%m/%d/%Y %H:%M:%S") + dt.timedelta(hours=4)
        report("NYISO rtfuelmix CSV", "5-min; timestamps are EPT (+4 to UTC)", r, stamp)
    except Exception as e:
        print(f"\n  NYISO failed: {e}")

    # SPP: the rolling file the connector already uses
    try:
        r = get("https://portal.spp.org/file-browser-api/download/generation-mix-historical?path=/SPP/GenMix365_SPP.csv")
        stamp = None
        if r.ok:
            lines = [l for l in r.text.strip().splitlines() if l.strip()]
            m = re.match(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", lines[-1])
            if m:
                stamp = dt.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
        report("SPP GenMix365", "GMT timestamps; this is the daily file, not a live feed", r, stamp)
    except Exception as e:
        print(f"\n  SPP failed: {e}")

    print("\n\n" + "=" * 78)
    print("READ THIS AS: freshness says how live the data IS.")
    print("CORS says whether the static Pages site can read it WITHOUT a server.")
    print("A 'NO' on CORS does not rule a source out - it means a scheduled")
    print("Actions job must fetch it and commit the result instead.")
    print("=" * 78)


if __name__ == "__main__":
    main()
