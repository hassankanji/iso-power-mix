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
src/pipeline.py       orchestrator: incremental pull -> gap repair (ISO retry, then EIA) -> US48
src/db.py             DuckDB storage. THE .duckdb FILE IS NOT COMMITTED - it is rebuilt
                      from docs/data/iso_daily_<year>.json on every fresh checkout
src/export.py         DB -> docs/data/*.json (per-year files, snapshot, meta) + interpolation
docs/                 static dashboard (vanilla JS + vendored Chart.js, no build step)
.github/workflows/    daily.yml (13:00 UTC cron + dispatch w/ iso|start|end inputs),
                      backfill-gaps.yml (keyless EIA bulk), reconcile.yml (EIA audit)
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
6. **Missing credentials are "skipped", never "failed".** Real failures and
   staleness >8 days exit non-zero so the Actions run goes red and emails
   the owner. Don't break this contract — it's the monitoring.

## Connector contract

Each module defines `ISO`, `EARLIEST_DATE`, `REQUIRES_AUTH`,
`fetch_range(start, end) -> DataFrame[date, iso, fuel_category,
generation_mwh]`, optionally `REQUIRED_ENV` (skip-without-creds),
`GAP_REPAIR_ENV`, `LOOKBACK_DAYS`. Raise on unrecoverable errors; return
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
- Numbers: trigger reconcile.yml (ratios vs EIA should be ~0.95-1.05) and
  sanity-check magnitudes: MISO ~650, PJM ~840, ERCOT ~490, SPP ~300,
  CAISO ~210, NYISO ~130, ISONE ~105 TWh/yr.
- Workflow dispatches accept a branch ref and commit results back to that
  branch — use this to run backfills before merging.

## Conventions

- Work on a `claude/...` branch; the user merges PRs to main themselves.
  Never push to main directly. Data commits from workflow runs land on
  whatever branch was dispatched — sync/rebase before pushing local work.
- Rich module docstrings carry the live-verified source knowledge (URL
  shapes, quirks, retention windows). Update them when a source changes;
  they are the institutional memory of this repo.
- No test suite; verification is reconciliation + magnitude checks + the
  dashboard smoke test above.
