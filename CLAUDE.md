# CLAUDE.md — iso-power-mix

Daily U.S. electricity generation-by-fuel tracker for a natural-gas trader.
Pulls from the 7 ISOs/RTOs + EIA, normalizes to 9 fuel buckets, stores in
DuckDB, publishes a static dashboard via GitHub Pages
(https://hassankanji.github.io/iso-power-mix/). The README is the canonical
user-facing doc — keep it updated whenever behavior or data status changes.

## Architecture (data flows left to right)

```
src/connectors/{caiso,pjm,ercot,miso,spp,nyiso,isone}.py   one module per ISO
src/eia.py            EIA-930 API client: gap-fill fallback, US48 overlay, reconciliation
src/pipeline.py       orchestrator: incremental pull -> gap repair (ISO retry, then EIA)
                      -> EIA fill for buckets a source can't report -> US48
src/db.py             DuckDB storage. THE .duckdb FILE IS NOT COMMITTED - it is rebuilt
                      from docs/data/iso_daily_<year>.json on every fresh checkout
src/export.py         DB -> docs/data/*.json (per-year files, snapshot, meta) + interpolation
docs/                 static dashboard (vanilla JS + vendored Chart.js, no build step)
                      It makes NO network calls at runtime beyond its own
                      data/*.json - there was an Hourly tab that called
                      api.eia.gov from the browser, removed 2026-08-11 because
                      EIA's by-fuel feed is ~9h behind and can never be live
                      (measurements in scripts/diagnose_live.py). That also
                      retired docs/data/live_key.json and the workflow step
                      that published the EIA key, so no secret ships with the
                      site. Don't reintroduce a browser-side key without
                      re-reading that decision.
.github/workflows/    daily.yml (12:00 + 16:30 UTC crons - set ~90 min early because
                      GitHub delays cron starts; dispatch w/ iso|start|end|us48_start),
                      backfill-gaps.yml (keyless EIA bulk), reconcile.yml (EIA audit,
                      plus mode=diagnose-storage - the way to ask api.eia.gov
                      anything, since the sandbox cannot reach it)
```

The committed per-year JSON exports are the durable dataset (grain:
date/iso/fuel/mwh, compact rows `[date, iso, fuel_index, mwh(, 1 if
estimated)]`). Past years' files are byte-stable; only the current-year file
changes daily — this keeps the repo small forever. Never commit
`data/power_mix.duckdb` or any secret.

## Critical invariants (each guards against a bug that actually happened)

1. **Daily energy = mean(MW over the day's readings) × 24.** Robust to any
   native sampling interval. ERCOT's XLSX interval columns are 15-min *MWh*,
   not MW — they're ×4'd first (see ercot.py docstring for the cross-check).
2. **Never pool-average native fuels that share a canonical bucket — sum
   them.** `long_to_daily_mwh` must receive the RAW native fuel column (it
   averages per-native, maps, then sums); `wide_to_daily_mwh` sums shared
   columns per row. Violating this halved SPP (Market+Self columns) and
   NYISO gas (Natural Gas+Dual Fuel) for weeks before reconciliation caught
   it. If you touch aggregation, run the reconcile workflow afterward.
3. **Upserts are idempotent** on (date, iso, fuel_category); re-running any
   range replaces rather than duplicates. Incremental runs deliberately
   overlap a lookback window (7 days; ERCOT 45 so settlement XLSX data
   overwrites its preliminary dashboard-telemetry days).
4. **ISO sources are primary; EIA-930 fills only missing days** (2018-07+),
   never overwrites ISO data. `iso='US48'` rows are EIA's lower-48 national
   reference — excluded from snapshots/stats/ISO views, drawn only as the
   dashed National Trend overlay (see the `WHERE iso != 'US48'` filters in
   export.py and the rows/usRows split in app.js).
5. **Interpolated rows (est flag) are exports-only**: derived fresh each
   export for interior gaps ≤10 days, skipped by the DB rebuild, replaced
   automatically when real data arrives.
6. **EIA API queries must be bounded by `start`.** An unbounded
   sort-by-period-desc hourly query makes EIA scan the whole history and
   answer 503 after ~35s (measured 2026-08-06, via a runner - the sandbox
   can't reach eia.gov). Every call site in `src/eia.py` passes an explicit
   window.
7. **Missing credentials are "skipped", never "failed"; unreachable hosts
   are "failed_unreachable", also never fatal on their own.** Real failures
   (anything where the host answered and we couldn't use it) and staleness
   >8 days exit non-zero so the Actions run goes red and emails the owner.
   A connect/read timeout is not a defect and the next run's lookback
   re-fetches the same days — one six-minute SPP outage reddening two runs
   is what motivated the split. Don't break this contract — it's the
   monitoring, and its value is entirely in not crying wolf.

8. **The `battery` bucket is battery discharge only, clipped at the source's
   native interval, and never contains pumped hydro.** Sources disagree:
   ERCOT/MISO/CAISO feeds and EIA's ERCO/MISO are net of charging, PJM and
   EIA's SWPP/ISNE are discharge-only, SPP/NYISO/ISONE and EIA's
   CISO/PJM/NYIS have no series at all. Three rules, each from a real bug:
   - Never clip a daily total — that reports heavy cycling as zero. EIA's
     storage codes therefore come from the hourly route while other fuels
     come from the daily one (`_storage_daily`).
   - Never clip an already-summed series. `clip(sum of BAs)` deleted 21% of
     national discharge vs `sum of clip(each BA)`; US48 uses
     `_national_storage_daily`, which sums per respondent.
   - EIA's `PS` maps to **hydro**, not battery — every ISO feed already
     files pumped storage there, and it was 43% of EIA's storage codes.
   Never clamp a bad row to 0; **drop** it. A zero is a measurement, so a
   clamp writes "the batteries did nothing" and no repair pass ever returns
   (that is what put the cliff in ERCOT's line). Full reasoning in
   src/schema.py's docstring and the README.

9. **EIA's national battery figure is a floor, not a total** — CISO, PJM and
   NYIS report no battery series to EIA-930, so the largest fleet in the
   country is absent from it. Do not "fix" the national line sitting below
   CAISO's; say so instead (the dashboard does). Connectors whose own source
   cannot carry a bucket declare `EIA_BACKED_CATEGORIES` and the pipeline
   fills it from EIA on dates the ISO left empty (ERCOT, SPP, ISONE).

## Connector contract

Each module defines `ISO`, `EARLIEST_DATE`, `REQUIRES_AUTH`,
`fetch_range(start, end) -> DataFrame[date, iso, fuel_category,
generation_mwh]`, optionally `REQUIRED_ENV` (skip-without-creds),
`GAP_REPAIR_ENV`, `LOOKBACK_DAYS`, `EIA_BACKED_CATEGORIES` (buckets this
source structurally cannot report). Raise on unrecoverable errors; return
empty (not raise) when data simply isn't published yet. Unrecognized native
fuels bucket to `imports_other` — never silently drop.

Per-ISO traps (details in each module's docstring): ERCOT has two sources
with DIFFERENT fuel spellings ("Gas"/"Coal" in XLSX vs "Natural Gas"/"Coal
and Lignite" in the dashboard feed); MISO LGI returns 3 sub-regions that
must be summed before averaging, and its no-auth feed folds hydro into
Other; SPP timestamps are GMT; NYISO Dual Fuel counts as gas; PJM's fuel
strings were unverified until the key arrived — check reconciliation after
any PJM change; ISO-NE rate-limits hard (429 cascades, escalating cooldown).

## Secrets (GitHub Actions; local .env for dev)

`PJM_SUBSCRIPTION_KEY`, `MISO_API_KEY`, `ISONE_USERNAME`, `ISONE_PASSWORD`,
`EIA_API_KEY`. An `ERCOT_API_KEY` exists but is unused — api.ercot.com has
no full fuel-mix product and needs a B2C bearer token besides the key.

## How to verify changes

- `python scripts/run_pipeline.py --iso CAISO` (needs open network — the
  Claude sandbox blocks ISO hosts; use workflow_dispatch on a branch and
  read the run logs via the GitHub MCP tools instead).
- Dashboard: `cd docs && python -m http.server 8420`, drive with Playwright
  (chromium at /opt/pw-browsers/chromium), screenshot every tab.
- Numbers: trigger reconcile.yml - it compares PER FUEL as well as on the
  total, because a small bucket can be wrong by 2x without moving the total
  (which is how the battery bucket went unnoticed). Ratios ~0.95-1.05, and
  sanity-check magnitudes (2025 actuals): MISO ~660, PJM ~875, ERCOT ~485,
  SPP ~300, CAISO ~225, NYISO ~133, ISONE ~108 TWh/yr. CAISO moved up from
  ~210 when storage became discharge-only — its battery discharge is 5.5%
  of its total, and it used to be subtracted as net.
- Workflow dispatches accept a branch ref and commit results back to that
  branch — use this to run backfills before merging.
- To ask api.eia.gov a question (sign conventions, fuel codes, freshness),
  write it into `scripts/diagnose_storage.py` and dispatch reconcile.yml
  with `mode=diagnose-storage`. A NEW workflow file cannot be dispatched
  until it is on the default branch, which is why this rides an existing
  one — don't waste a cycle rediscovering that.

## Conventions

- Work on a `claude/...` branch; the user merges PRs to main themselves.
  Never push to main directly. Data commits from workflow runs land on
  whatever branch was dispatched — sync/rebase before pushing local work.
- Rich module docstrings carry the live-verified source knowledge (URL
  shapes, quirks, retention windows). Update them when a source changes;
  they are the institutional memory of this repo.
- No test suite; verification is reconciliation + magnitude checks + the
  dashboard smoke test above.
