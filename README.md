# U.S. ISO Generation Mix Tracker

Daily electricity generation by fuel type for all 7 U.S. ISOs/RTOs, built for quickly reading fundamental supply shifts — especially natural gas burn — without digging through seven different websites.

**Dashboard:** https://hassankanji.github.io/iso-power-mix/
**Coverage:** CAISO, PJM, ERCOT, MISO, SPP, NYISO, ISO-NE, plus EIA's US-lower-48 total as a national reference
**Fuel buckets:** natural gas, coal, nuclear, hydro, wind, solar, other renewables, storage, imports/other
**Storage means discharge**, never net of charging — see [What "storage" means](#what-storage-means)

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

1. **National Trend** — daily generation by fuel across all 7 ISOs, with EIA's lower-48 total as a dashed overlay. "% of total" switches to mix share (best for spotting gas↔coal displacement).
2. **Fuel Comparison** — pick a fuel (defaults to Natural Gas): one line per ISO plus the US48 national line, 7-day smoothing on by default, "% of each area's total" to normalize market size away. *The US48 gas line is total U.S. gas burn.* Switched to **Stacked**, the same view becomes total ISO gas burn split by market, against that national line.
3. **National by ISO** — each ISO's total contribution, with the same US48 overlay.
4. **Per-ISO Breakdown** — full fuel mix for one ISO.
5. **Latest Snapshot** — every ISO's most recent day, in **GWh/TWh with the share and the day-over-day change alongside**, and a daily total per market, plus the freshness table and gap status. Each ISO is compared against the day before *its own* as-of date, since they publish on different delays; hover the change for the percentage. The national rollup sums each ISO's latest day, so its change sums each ISO's previous day the same way — and is left blank if any one ISO lacks that day, rather than showing a partial sum as a national drop.
6. **Hourly (EIA-930)** — national generation hour by hour rather than day by day. See below.

Every chart has a **Stacked / Lines** switch: stacked answers "what was the total and who contributed", lines answer "where is this one series going" without the series below it moving the baseline. Also checkbox legends (untick to exclude a series), Bloomberg-style range presets (1D→Max, default 1Y), and free date pickers. Stacked windows under ~2 weeks render as bars.

### The Hourly tab

Everything else here is settled *daily* data. This tab reads **EIA-930's hourly lower-48 aggregate** (respondent `US48`) straight from the EIA API in your browser: total generation and the split by fuel for the most recently published hour, plus the last 48 hours as a chart. Values are average MW over the hour, shown as GW (the rest of the site is energy per day, in GWh).

**Why isn't this actually live?** Because EIA isn't. Measured from a runner on 2026-08-11: EIA's US48 by-fuel series was 9.3 hours behind, and `region-data` — the total-net-generation series with no fuel split — was behind by *exactly the same* 9.3 hours, so there is no fresher EIA route to switch to. EIA is a settlement-grade aggregator, not a real-time feed.

Genuinely live data does exist, just not nationally: the ISOs publish their own fuel mix **5–16 minutes** behind (CAISO 6 min, ERCOT 5 min, NYISO 16 min, MISO near real-time). The catch is CORS — only MISO sends a header a static site may read, so using the others requires a scheduled job that fetches server-side and commits the result. See `scripts/diagnose_live.py` for the full measurement and the trade-offs.

There is deliberately **no hour-over-hour or day-over-day comparison here**. Because the newest published hour is ~15 hours old it is almost always a late-evening hour, so comparing it to the same hour yesterday reported solar as collapsed every single time — a real number that read as a signal and wasn't one. Day-over-day change lives on the Latest Snapshot tab, where it compares whole settled days.

**It is not real time**, and it used to claim otherwise. Measured from a runner on 2026-08-07, the newest published hour was **14.7 hours old**, and every hour in the window carried all 16 fuel codes — so that is EIA's publication schedule for the by-fuel breakdown, not a half-written hour being dropped client-side. (EIA's demand and interchange series are far fresher; the fuel split is not.) Budget 12–18 hours. The tab prints the actual lag next to the headline number so you never have to assume.

It fetches **only when you open the tab or press Refresh** — no polling, no Actions minutes, no commits. The last pull is cached in the browser so the tab is never blank.

**No setup:** the site ships its own EIA key, written into `docs/data/live_key.json` from the `EIA_API_KEY` Actions secret by the daily workflow, so readers need no key of their own. That key is public by construction — GitHub Pages is static and has no server to hide one behind — which is an accepted trade: an EIA key is free, grants nothing beyond read access to public EIA data, and rotating it means updating the secret and re-running the workflow. If it is ever rejected or rate-limited, the tab offers a box for your own free [EIA key](https://www.eia.gov/opendata/register.php), kept in your browser's local storage and preferred over the site's until you clear it.

### What "storage" means

**Storage here is energy sent *out* of batteries and pumped hydro. Charging is excluded** — it is load, not generation. So the storage line is always ≥ 0, and it is *not* the net battery position.

This is a deliberate choice, forced by the sources disagreeing with each other. Measured against EIA-930 on 2026-08-07:

| Convention | Sources |
|---|---|
| Net of charging (goes negative) | ERCOT, MISO and CAISO's own feeds; EIA's `ERCO` and `MISO` respondents |
| Discharge only (never negative) | PJM and MISO's own feeds; EIA's `SWPP` and `ISNE` respondents |
| No storage series at all | SPP, NYISO and ISO-NE's own feeds; EIA's `CISO`, `PJM` and `NYIS` respondents |

EIA's `US48` aggregate sums balancing authorities from the first two groups, which is why its storage used to read **+8.4 TWh across 2025** while CAISO — which reports net — sat at **−1.9 TWh**. Neither figure was wrong; they answered different questions and were stacked in one chart.

Discharge won because it is the only definition every source can express: a net series can always drop its charging intervals, but a discharge-only series can never be reconstructed into a net one. It is also the only one that makes a stacked generation mix add up, since every other bucket is gross generation too.

Clipping happens on each source's **native interval** (5-minute, 15-minute or hourly), never on a daily total — flooring a day's *net* at zero would report a day of heavy cycling as no generation at all. For EIA that means the storage bucket is rebuilt from the hourly route even though every other fuel comes from the daily one.

**The trade this makes, stated plainly:** counting discharge as generation means a daily total slightly double-counts energy — once when gas or solar made it, again when a battery gives it back. It is 5.5% of CAISO's 2025 total (12.5 of 225.6 TWh), 0.3% nationally, and under 0.1% everywhere else. Net accounting avoids that double-count but is unavailable from over half our sources, so it was not an option. If you are reconciling totals to the megawatt-hour, subtract the storage bucket; for reading fuel mix and gas burn, which is what this site is for, it does not matter. Switching CAISO to discharge is also why its annual total moved from ~210 to ~226 TWh.

**One known blemish.** 39 ISO-days written before this change (34 ERCOT in Jul–Aug 2026, 5 MISO in Dec 2025) could not be re-derived: ERCOT's settlement workbook had not yet reached those days and MISO's own feed has permanent holes there, so no re-pull could reach the interval data. They are clamped to zero rather than left negative, which understates those days' storage slightly. The ERCOT ones repair themselves into real discharge figures as settlement data lands inside ERCOT's 45-day lookback. A database-level guard enforces the floor on every write and prints a warning when it fires, so a connector that ever regresses to net reporting shows up in the run log instead of silently reappearing as negative slices.

If you want the net battery position, this dashboard is not currently the place to read it.

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
