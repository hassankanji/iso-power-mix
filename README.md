# U.S. ISO Generation Mix Tracker

Daily electricity generation by fuel type for all 7 U.S. ISOs/RTOs, built for quickly reading fundamental supply shifts — especially natural gas burn — without digging through seven different websites.

**Dashboard:** https://hassankanji.github.io/iso-power-mix/
**Coverage:** CAISO, PJM, ERCOT, MISO, SPP, NYISO, ISO-NE, plus EIA's US-lower-48 total as a national reference
**Fuel buckets:** natural gas, coal, nuclear, hydro, wind, solar, other renewables, battery, imports/other
**Battery means discharge**, never net of charging, and never pumped hydro — see [What "battery" means](#what-battery-means)

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
5. **Latest Snapshot** — three levels of the same day, each in **GWh/TWh with the share and the day-over-day change alongside**: EIA's whole lower-48 total, the seven tracked ISOs summed, then a card per ISO. Plus the freshness table and gap status. See below.

Every chart has a **Stacked / Lines** switch: stacked answers "what was the total and who contributed", lines answer "where is this one series going" without the series below it moving the baseline. Also checkbox legends (untick to exclude a series), Bloomberg-style range presets (1D→Max, default 1Y), and free date pickers. Stacked windows under ~2 weeks render as bars.

### The Latest Snapshot tab

Three nested views of the most recent day, each carrying a **change against the previous day** next to the value (hover it for the percentage):

- **U.S. lower-48 total (EIA)** — the whole country as EIA-930 measures it, including the utility-run Southeast, Northwest and Southwest that no ISO covers. This runs on EIA's clock, which is slower than the ISOs', so its day is often one behind theirs; the heading always says which day it is.
- **Tracked-ISO mix** — the seven ISOs this site pulls directly, summed. Roughly two-thirds of the EIA total above.
- **Per-ISO cards** — each market on its own, compared against the day before *its own* as-of date, since the ISOs publish on different delays.

The tracked-ISO change sums each ISO's previous day the same way its total sums each ISO's latest day. If any one ISO lacks that previous day the change is left blank rather than showing a partial sum against a full one, which would read as a national drop that never happened.

### Why there's no live view

There was an Hourly (EIA-930) tab. It was removed: it could never be live, and a not-live "live" tab is worse than none.

Measured from a runner on 2026-08-11 (`scripts/diagnose_live.py`), EIA's US48 by-fuel series was **9.3 hours behind**, and `region-data` — the total-net-generation series with no fuel split — was behind by *exactly the same* 9.3 hours. There is no fresher EIA endpoint to switch to; EIA is a settlement-grade aggregator, not a real-time feed.

Genuinely live data does exist, just not nationally. The ISOs publish their own fuel mix **5–16 minutes** behind (ERCOT 5 min, CAISO 6 min, NYISO 16 min, MISO near real-time). Two things stand in the way of putting that on this site: only MISO sends a CORS header a static page may read, and the only server available here is GitHub Actions, whose scheduler routinely runs crons 30–90 minutes late. An Actions-based collector would realistically deliver 15–60 minute data. Truly 5-minute data needs an always-on host or a CORS proxy.

### What "battery" means

**Battery here is energy sent *out* of the fleet. Charging is excluded** — it is load, not generation. So the battery line is always ≥ 0, and it is *not* the net battery position. **Pumped hydro is not in this bucket**; it is in Hydro.

Three separate things had to be settled to make this number mean one thing, and each was found by a number on the dashboard that could not be true.

#### 1. Discharge, not net

Forced by the sources disagreeing. Measured against EIA-930 on 2026-08-13 (`scripts/diagnose_storage.py`):

| Convention | Sources |
|---|---|
| Net of charging (goes negative) | ERCOT, MISO and CAISO's own feeds; EIA's `ERCO` and `MISO` respondents |
| Discharge only (never negative) | PJM and MISO's own feeds; EIA's `SWPP` and `ISNE` respondents |
| No series at all | SPP, NYISO and ISO-NE's own feeds; EIA's `CISO`, `PJM` and `NYIS` respondents |

Discharge won because it is the only definition every source can express: a net series can always drop its charging intervals, but a discharge-only series can never be reconstructed into a net one. It is also the only one that makes a stacked generation mix add up, since every other bucket is gross generation too.

Clipping happens on each source's **native interval** (5-minute, 15-minute or hourly), never on a daily total — flooring a day's *net* at zero would report a day of heavy cycling as no generation at all. For EIA that means the battery bucket is rebuilt from the hourly route even though most fuels come from the daily one.

#### 2. Summed per region, not clipped nationally

The same rule has an edge that is easy to miss. Clipping a series that is **already summed** is not the same as summing clipped ones: an hour where CAISO discharges 4 GW while the rest of the country charges 5 GW nets to −1 GW and books **zero**, deleting CAISO's 4 GWh outright.

EIA's `US48` row is exactly such a pre-summed series, and the national line was being read off it. Measured over 2026-08-06 → 08-12:

| Method | National battery discharge |
|---|---|
| Clip EIA's `US48` aggregate (what we did) | 79.0 GWh/day |
| Sum each balancing authority's own clipped discharge | **100.0 GWh/day** |

**21% of real discharge was being deleted.** The national series is now built per balancing authority and then added.

#### 3. Batteries, not pumped hydro

Every ISO feed folds pumped storage into its hydro column. EIA breaks it out as a separate `PS` code, and it is not a rounding error — **43% of EIA's storage-coded discharge** (265 of 620 GWh over that week). So the same asset was filed under "storage" when EIA answered and under "hydro" when the ISO answered, which made the two incomparable by construction. EIA's `PS` now goes to Hydro, clipped hourly first since pumping is load, and the bucket is named for what is actually left in it.

#### What is still missing, and cannot be fixed here

**CAISO, PJM and NYISO report no battery series to EIA-930 at all.** So EIA's lower-48 battery figure omits the largest battery fleet in the country, and the national line on the Fuel Comparison tab runs *below* CAISO's own line rather than above it. That is a reporting gap at the source, not a measurement — the dashboard labels it as a floor rather than a total. Where an ISO's own feed has no battery column but EIA does (ERCOT, SPP, ISO-NE), the pipeline fills the bucket from EIA; EIA's `ERCO` averaged 24.9 GWh/day against 25.0 from ERCOT's live dashboard over the same week, so the splice does not show.

If you want the net battery position, this dashboard is not currently the place to read it.

#### The trade this makes, stated plainly

Counting discharge as generation means a daily total slightly double-counts energy — once when gas or solar made it, again when a battery gives it back. It is 5.5% of CAISO's 2025 total (12.5 of 225.6 TWh), well under 1% elsewhere. Net accounting avoids that double-count but is unavailable from over half our sources, so it was not an option. If you are reconciling totals to the megawatt-hour, subtract the battery bucket; for reading fuel mix and gas burn, which is what this site is for, it does not matter. Switching CAISO to discharge is also why its annual total moved from ~210 to ~226 TWh.

#### Repaired, not papered over

An earlier version of this guard **clamped** negative daily totals to zero. That was worse than the leak it was catching: zero is a measurement, so 39 ISO-days of bad data became 39 days of "the batteries did nothing", and nothing ever revisited them — a date with a zero in it does not look missing to any repair pass. It is what put a cliff in ERCOT's line, flat zero for its entire history and then 25 GWh/day the moment the clipping fix shipped. The guard now **drops** the row instead, leaving an honest gap that the EIA bucket fill comes back for, and `scripts/migrate_battery_bucket.py` removed the rows the old one wrote.

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

Known simplifications: NYISO "Dual Fuel" units (oil-or-gas capable) count as gas, so severe winter oil-switching days overstate NYISO gas somewhat; SPP's day boundary is GMT (a few hours off the others); ERCOT's settlement report has no battery column, so ERCOT's battery figures come from EIA's `ERCO` respondent; MISO's most recent day can briefly show hydro folded into "other" until its detailed feed catches up next morning.

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
