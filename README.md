# U.S. ISO Generation Mix Tracker

Daily electricity generation by fuel type for all 7 U.S. ISOs/RTOs, built for quickly reading fundamental supply shifts — especially natural gas burn — without digging through seven different websites.

**Dashboard:** https://hassankanji.github.io/iso-power-mix/
**Coverage:** CAISO, PJM, ERCOT, MISO, SPP, NYISO, ISO-NE, plus EIA's US-lower-48 total as a national reference
**Fuel buckets:** natural gas, coal, nuclear, hydro, wind, solar, other renewables, battery, imports/other
**Battery means discharge**, never net of charging, and never pumped hydro — see [What "battery" means](#what-battery-means)

**If you were sent this link and just want to read the data:** open the dashboard above — it needs no account, no login, and nothing installed. The [Reading the dashboard](https://hassankanji.github.io/iso-power-mix/guide.html) page explains each tab in a couple of minutes. Everything below is the how-it-works detail behind those numbers.

---

## Reading the dashboard

Five tabs, each answering a different question:

1. **National Trend** — daily generation by fuel across all 7 ISOs, with EIA's lower-48 total as a dashed overlay. "% of total" switches to mix share (best for spotting gas↔coal displacement).
2. **Fuel Comparison** — pick a fuel (defaults to Natural Gas): one line per ISO plus the US48 national line, 7-day smoothing on by default, "% of each area's total" to normalize market size away. *The US48 gas line is total U.S. gas burn* — it dwarfs the individual ISOs, so untick it in the legend when you want to compare markets against each other. Switched to **Stacked**, the same view becomes total ISO gas burn split by market.
3. **National by ISO** — each ISO's total contribution, with the same US48 overlay.
4. **Per-ISO Breakdown** — full fuel mix for one ISO.
5. **Latest Snapshot** — the most recent day at three levels, with change columns. See below.

Every chart has a **Stacked / Lines** switch: stacked answers "what was the total and who contributed", lines answer "where is this one series going" without the series below it moving the baseline. Also checkbox legends (untick to exclude a series), Bloomberg-style range presets (1D→Max, default 1Y), and free date pickers. Stacked windows under ~2 weeks render as bars.

### The Latest Snapshot tab

Three nested views of the most recent day — EIA's whole lower-48, the seven tracked ISOs summed, then a card per ISO — each fuel showing its output, its share of the day, and how it has changed.

**Two controls sit above them:**

- **Change vs** — compare against the previous day, a week, a month, a year earlier, or all four at once.
- **Change shown as** — **Energy (GWh)** or **Share of mix (pp)**.

That second toggle is the one worth knowing about. Load lifts every fuel at once: on a hot day every energy column is green and on a mild day every column is red, neither of which tells you who actually gained ground. Switch to share and the columns show percentage points of the mix — gas at 42.1% today against 39.8% a year ago reads **+2.3 pp** — so displacement shows up as one bucket moving against another. Hover any cell for both underlying values. The Total row keeps reporting the load itself, as a percent, since a total's share of itself is always 100%.

With **All four** selected in share mode you get the trajectory in one glance: SPP wind at +26.1 pp against last year, ERCOT wind +19.1 pp, and so on.

Each area is compared against **its own** as-of date shifted back, not a fixed calendar date, because the ISOs publish on different delays. The tracked-ISO change sums each ISO's previous day the same way its total sums each ISO's latest day; if any one ISO lacks that day the change is left blank rather than showing a partial sum against a full one, which would read as a national drop that never happened.

An empty change cell always means "nothing to compare against" — never zero. An absent row and a zero row mean different things and the data keeps them apart.

Below the cards: the per-ISO freshness table and gap status.

## How updates work

- GitHub Actions pulls fresh data **twice a day** — a morning run targeting **~9am ET** and an early-afternoon refresh. (GitHub's scheduler routinely starts cron jobs 30–90 minutes late; the schedule is set early to compensate, so "by mid-morning ET" is the honest promise.)
- Each run updates the site **through yesterday** for every ISO (ERCOT and MISO also get a partial today).
- Updates happen entirely on GitHub's servers and publish to the same URL for everyone. Nobody's laptop is involved — just share the link.
- **The site says so when it goes stale.** The header shows the exact last-updated time, and if the newest data is more than 3 days old a banner appears saying how old it is; past 8 days it turns red and points at the Actions tab. That threshold matches the one that turns the daily job red, so the site and the pipeline agree on what "too old" means. On a normal day there is no banner and the newest day is yesterday.

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

The 7 ISOs cover about **65%** of U.S. generation (~9,600 of ~14,500 GWh/day in summer 2026). The other ~35% is real generation in regions that never joined an ISO — the utility-run Southeast (Southern Co., TVA, Duke's Carolinas, all of Florida), Northwest, and Southwest. That's why the dashed EIA line sits above the stack. Because the Southeast is heavily gas-fired, the ISOs capture only ~63% of national gas burn — use the US48 line on the Fuel Comparison tab when you want the true national number.

## Data quality

Independently audited against EIA-930's federal measurements (30 days ending 2026-08-12, GWh/day):

| ISO | ours | EIA | ratio |
|---|---|---|---|
| PJM | 2,801 | 2,801 | 1.000 |
| SPP | 1,071 | 1,067 | 1.004 |
| ERCOT | 1,721 | 1,726 | 0.997 |
| MISO | 2,139 | 2,146 | 0.997 |
| NYISO | 421 | 423 | 0.994 |
| CAISO | 807 | 674 | 1.198\* |
| ISO-NE | 335 | 358 | 0.935\* |

\* Structural, not errors: CAISO's public feed includes imports and gross-vs-net metering differences (trends and mix are unaffected — just don't compare its absolute level to EIA's); ISO-NE's API covers market-metered units, while EIA also estimates small non-market resources.

The audit runs **per fuel as well as on the total**, because a total-only check is blind to the bug that motivated it: a small bucket can be wrong by a factor of two, or missing entirely, without moving the total by a percent — which is exactly how the battery bucket carried the wrong convention for weeks. The remaining per-fuel flags are all bucketing differences rather than measurement disagreements: EIA files some resources under `OTH` that the ISOs itemize, so "imports/other" and "other renewables" trade places between the two. The full report is `docs/data/reconciliation.json`, refreshed by the "Reconcile vs EIA-930" workflow on demand.

Known simplifications: NYISO "Dual Fuel" units (oil-or-gas capable) count as gas, so severe winter oil-switching days overstate NYISO gas somewhat; SPP's day boundary is GMT (a few hours off the others); ERCOT's settlement report has no battery column, so ERCOT's battery figures come from EIA's `ERCO` respondent; MISO's most recent day can briefly show hydro folded into "other" until its detailed feed catches up next morning.

## Sharing this

The dashboard is public: **https://hassankanji.github.io/iso-power-mix/** works for anyone with the link, with no GitHub account and nothing to install. That link is the thing to send.

- The **[Reading the dashboard](https://hassankanji.github.io/iso-power-mix/guide.html)** page (`docs/guide.html`) is the same orientation as this README's first section, hosted on the site itself — so a reader gets the explanation without opening a code repository. It is linked from the dashboard header.
- This README is the canonical document; the guide page is deliberately a subset of it, covering what a reader needs and none of the maintenance detail. When behaviour changes, update this file first.
- The repo is public, so a repo link works too — it just puts a reader in front of a codebase rather than an explanation.

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

There is no unit-test suite (the data is verified by reconciliation against EIA), but `python scripts/check_dashboard.py` is the dashboard's smoke test: it drives a real browser through every control combination on every tab — 110 of them — and fails on any console error, any chart that renders empty, and any width from 320px up where the page scrolls sideways. Run it after touching anything in `docs/`. `--url https://hassankanji.github.io/iso-power-mix` points it at the deployed site instead.

## If it ever stops updating

The site is designed to keep running unattended, and to be honest when it isn't. In order of likelihood:

- **Site shows stale data mid-morning:** most likely GitHub delayed the scheduled run — check the repo's Actions tab; you can also press "Run workflow" on *Daily ISO generation-mix pull* to update immediately.
- **A run is red:** open its log; the failing ISO and reason are printed plainly. One ISO failing never blocks the others, whatever succeeded is still published, and GitHub emails the repo owner on failures. Runs also go red if any ISO's data ages past 8 days — that's deliberate monitoring, not a crash.
- **A source credential lapses** (PJM/MISO/ISO-NE keys): that ISO alone goes red and stops updating while the other six carry on. Re-register the key and update the Actions secret; no code change is needed.
- **A GitHub Actions deprecation:** the runner currently warns that `actions/checkout@v4` and `actions/setup-python@v5` target Node 20, and runs them on Node 24 anyway. It is only a warning today; if GitHub eventually drops the shim, both runs fail at the setup step (an obvious, loud failure with nothing to do with the data). Bumping the two action versions in `.github/workflows/` is the whole fix.
- **After ~60 days of zero repo activity**, GitHub pauses cron schedules; re-enable with one click under Actions. (Daily data commits normally count as activity, so this only matters if updates were already broken for two months — the case worth knowing about if nobody is watching the repo for a stretch.)
- **Nobody is watching:** failure emails go to the repo owner only. If the site should be monitored while the owner is away, add a teammate as a repo collaborator — they'll get the same Actions notifications. Failing that, the staleness banner on the dashboard is the backstop: it tells any reader, without anyone having to check GitHub at all.

Manual tools in the Actions tab: **Daily pull** (with optional ISO/date-range inputs for re-backfills), **Backfill gaps from EIA-930**, **Reconcile vs EIA-930**.

## How the repo is laid out

```
src/connectors/*.py   one module per ISO; docstrings carry the source quirks
src/eia.py            EIA-930 client (gap fill, US48 overlay, reconciliation)
src/pipeline.py       orchestrator: pull → gap repair → EIA fill → US48
src/db.py             DuckDB storage (the .duckdb file is NOT committed —
                      it is rebuilt from docs/data/iso_daily_<year>.json)
src/export.py         DB → docs/data/*.json
docs/                 the dashboard: vanilla JS, vendored Chart.js, no build
                      step, no network calls beyond its own data files
.github/workflows/    daily.yml, backfill-gaps.yml, reconcile.yml
```

The committed per-year JSON files are the durable dataset — past years are byte-stable, so only the current year's file changes daily and the repo stays small. A fresh checkout rebuilds the database from them exactly; nothing else is needed to reproduce the site.
