# U.S. ISO Generation Mix Tracker

Daily electricity generation by fuel type for all 7 U.S. ISOs/RTOs, built for quickly reading fundamental supply shifts — especially natural gas burn — without digging through seven different websites.

**Dashboard:** https://hassankanji.github.io/iso-power-mix/
**Coverage:** CAISO, PJM, ERCOT, MISO, SPP, NYISO, ISO-NE, plus EIA's US-lower-48 total as a national reference
**Fuel buckets:** natural gas, coal, nuclear, hydro, wind, solar, other renewables, storage, imports/other

---

## How updates work

- GitHub Actions pulls fresh data **twice a day** — a morning run targeting **~9am ET** and an early-afternoon refresh. (GitHub's scheduler routinely starts cron jobs 30–90 minutes late; the schedule is set early to compensate, so "by mid-morning ET" is the honest promise.)
- Each run updates the site **through yesterday** for every ISO (ERCOT and MISO also get a partial today).
- Updates happen entirely on GitHub's servers and publish to the same URL for everyone. Your laptop is never involved — just share the link.
- The dashboard header shows the exact last-updated time and flags anything stale; the Latest Snapshot tab has a per-ISO freshness table.

## Data sources, in order of preference

1. **Each ISO's own published data** is always primary — it's the freshest and most detailed. Details per ISO:

| ISO | History | Source | Notes |
|---|---|---|---|
| CAISO | 2019 → | Today's Outlook daily CSVs | includes imports (~13% of its total) |
| PJM | 2016 → | Data Miner 2 API (key) | verified against PJM's published mix |
| ERCOT | 2007 → | settlement XLSX + live dashboard feed | see below |
| MISO | 2014 → | Data Exchange LGI API (key) + public feed | |
| SPP | 2011 → | GenMix archive CSVs | |
| NYISO | Dec 2015 → | P-63 real-time fuel mix CSVs | |
| ISO-NE | Jun 2018 → | Web Services API (login) | API holds nothing older |

2. **EIA-930 fills holes.** Every balancing authority — including each ISO — reports its hourly generation by fuel to the U.S. Energy Information Administration (Form EIA-930). When an ISO's own source is missing a day the pipeline can't recover, it's filled with **EIA's measurement of that same ISO** (respondent `ERCO` *is* ERCOT, `MISO` is MISO, etc. — same grid, independently collected). EIA data exists from July 2018 onward. Filled days are real measured data, not estimates.

3. **Interpolation is the last resort**, only for holes ≤10 days that nothing else can fill, only in the charts (flagged in the data files, never stored), and disclosed in the header. Currently there are **zero missing days**, so nothing is interpolated.

### How ERCOT works (three tiers of freshness)

ERCOT publishes its official fuel mix in settlement-quality XLSX workbooks that run **1–4 weeks behind**. To stay current anyway:

- **Yesterday/today:** pulled from ERCOT's live Fuel Mix dashboard feed (the same numbers on ercot.com). These days are tagged *preliminary* on the snapshot tab.
- **The in-between window** (after the dashboard's ~2-day reach, before the workbook publishes): filled with EIA-930's measurement of ERCOT.
- **Settlement data replaces both automatically** when ERCOT publishes the workbook — the pipeline re-fetches a 45-day trailing window every run for exactly this purpose.

So ERCOT is always complete to yesterday; recent weeks firm up from preliminary/EIA values to settlement values on their own. (The `api.ercot.com` API key is unused: that API exposes only wind/solar actuals, not the full mix, and requires an additional login-based token.)

## The dashboard

1. **National Trend** — stacked daily generation by fuel across all 7 ISOs, with EIA's lower-48 total as a dashed overlay. "% of total" switches to mix share (best for spotting gas↔coal displacement).
2. **Fuel Comparison** — pick a fuel (defaults to Natural Gas): one line per ISO plus the US48 national line, 7-day smoothing on by default, "% of each area's total" to normalize market size away. *The US48 gas line is total U.S. gas burn.*
3. **National by ISO** — each ISO's total contribution, stacked, with the same US48 overlay.
4. **Per-ISO Breakdown** — full fuel stack for one ISO.
5. **Latest Snapshot** — every ISO's most recent day as sorted % bars, freshness table, and gap status.

Every chart has checkbox legends (untick to exclude a series), Bloomberg-style range presets (1D→Max, default 1Y), and free date pickers. Windows under ~2 weeks render as bars.

### Reading the national picture

The 7 ISOs cover about **65%** of U.S. generation (~8,600 of ~13,200 GWh/day in summer 2026). The other ~35% is real generation in regions that never joined an ISO — the utility-run Southeast (Southern Co., TVA, Duke's Carolinas, all of Florida), Northwest, and Southwest. That's why the dashed EIA line sits above the stack. Because the Southeast is heavily gas-fired, the ISOs capture only ~63% of national gas burn — use the US48 line on the Fuel Comparison tab when you want the true national number.

## Data quality

Independently audited against EIA-930's federal measurements (30 days ending 2026-07-14, GWh/day):

| ISO | ours | EIA | ratio |
|---|---|---|---|
| PJM | 2,740 | 2,741 | 1.000 |
| ERCOT | 1,620 | 1,620 | 1.000 |
| MISO | 2,011 | 2,008 | 1.002 |
| SPP | 957 | 957 | 1.000 |
| NYISO | 405 | 404 | 1.003 |
| CAISO | 628 | 498 | 1.262* |
| ISO-NE | 319 | 343 | 0.929* |

\* Structural, not errors: CAISO's public feed includes imports and gross-vs-net metering differences (trends and mix are unaffected — just don't compare its absolute level to EIA's); ISO-NE's API covers market-metered units, while EIA also estimates small non-market resources. The "Reconcile vs EIA-930" workflow re-runs this audit on demand.

Known simplifications: NYISO "Dual Fuel" units (oil-or-gas capable) count as gas, so severe winter oil-switching days overstate NYISO gas somewhat; SPP's day boundary is GMT (a few hours off the others); ERCOT's report has no battery column (batteries hide in "other"); MISO's most recent day can briefly show hydro folded into "other" until its detailed feed catches up next morning.

## Setup (already done for this repo — for reference/rebuilding)

GitHub Actions secrets (Settings → Secrets and variables → Actions): `PJM_SUBSCRIPTION_KEY`, `MISO_API_KEY`, `ISONE_USERNAME`/`ISONE_PASSWORD`, `EIA_API_KEY`. All are free registrations: [PJM](https://apiportal.pjm.com/) (non-members email accountmanager@pjm.com for approval), [MISO Data Exchange](https://data-exchange.misoenergy.org/) (LGI API subscription), [ISO-NE ISO Express](https://www.iso-ne.com/participate/support/web-services-data), [EIA](https://www.eia.gov/opendata/). CAISO/ERCOT/SPP/NYISO need no registration. A missing secret never breaks the run — the affected feature just reports "skipped".

## Running locally (optional, for development only)

```bash
python -m venv venv && source venv/bin/activate   # venv\Scripts\activate on Windows
pip install -r requirements.txt
cp .env.example .env                              # add keys if you have them
python scripts/run_pipeline.py                    # pull latest + repair gaps + export
cd docs && python -m http.server 8420             # dashboard at localhost:8420
```

The database file is rebuilt automatically from the committed JSON files — nothing to download or restore.

## If it ever stops updating

- **Site shows stale data mid-morning:** most likely GitHub delayed the scheduled run — check the repo's Actions tab; you can also press "Run workflow" on *Daily ISO generation-mix pull* to update immediately.
- **A run is red:** open its log; the failing ISO and reason are printed plainly. One ISO failing never blocks the others, whatever succeeded is still published, and GitHub emails you on failures. Runs also go red if any ISO's data ages past 8 days — that's deliberate monitoring, not a crash.
- **After ~60 days of zero repo activity**, GitHub pauses cron schedules; re-enable with one click under Actions. (Daily data commits normally count as activity, so this only matters if updates were already broken for two months.)
- Manual tools in the Actions tab: **Daily pull** (with optional ISO/date-range inputs for re-backfills), **Backfill gaps from EIA-930**, **Reconcile vs EIA-930**.
