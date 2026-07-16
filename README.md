# U.S. ISO Generation Mix Tracker

Daily electricity generation-by-fuel-type data pulled directly from the 7 major U.S. ISOs/RTOs, normalized into a shared schema, stored in DuckDB, and visualized in a static dashboard built for quickly reading fundamental supply shifts (especially natural gas burn).

**Dashboard:** https://hassankanji.github.io/iso-power-mix/
**ISOs covered:** CAISO, ERCOT, MISO, SPP, NYISO, ISO-NE — plus PJM once its API key is approved (see [PJM status](#pjm-status))
**Fuel buckets:** `natural_gas`, `coal`, `nuclear`, `hydro`, `wind`, `solar`, `other_renewables`, `storage`, `imports_other`

---

## How updates work (read this if you're sharing the link)

- A GitHub Actions job runs **every day at 13:00 UTC** — that's **9am ET during daylight-saving time (roughly mid-March to early November) and 8am ET in winter**. GitHub sometimes delays scheduled jobs by 15–30 minutes under load, so treat it as "by mid-morning ET", not 9:00 sharp.
- The job pulls each ISO's newest data (normally **through yesterday**; ERCOT/MISO also get a partial "today"), commits the refreshed JSON files to this repo, and GitHub Pages automatically redeploys the site.
- **The update happens entirely on GitHub's servers.** Your laptop does not need to be on, and nothing about the update is local: everyone who opens https://hassankanji.github.io/iso-power-mix/ — you, a trader you sent the link to, anyone — sees the same freshly updated site. Sending the link is all you need to do.
- The header of the dashboard shows the exact "last updated" timestamp and flags any ISO whose data is lagging, so a reader can always tell how fresh what they're looking at is.

---

## Data status (as of 2026-07-16)

| ISO | History starts | Freshness | Auth | Gaps |
|---|---|---|---|---|
| **CAISO** | 2019-01-01 | daily (≤1 day behind) | none | none (2 source holes auto-repaired 2026-07-16) |
| **ERCOT** | 2007-01-01 | daily via live dashboard feed; settlement data replaces it ~1-4 weeks later | none | none (July arrears window filled from EIA-930) |
| **MISO** | 2014-01-01 | daily (≤1 day behind) | key needed for history only | 45 days in 2014-2017 — see [MISO gaps](#miso-gaps) |
| **SPP** | 2011-01-01 | daily | none | none |
| **NYISO** | 2015-12-09 | daily | none | none |
| **ISO-NE** | 2018-06-30* | daily | username/password | none |
| **PJM** | (2015-01-01 once keyed) | **no data yet** | subscription key | n/a |
| *US48 (EIA)* | 2018-07-01 | ~1-2 days behind | EIA key | reference overlay, not an ISO |

\* ISO-NE's Web Services API returns empty payloads before 2018-06-30 (live-verified; the public "since 2008" claim applies to ISO Express CSV reports, not this API) — possibly a rolling ~8-year retention window.

### ERCOT: yes, ERCOT publishes daily values — we now use them

ERCOT publishes the full fuel-mix history only in monthly-in-arrears XLSX workbooks ([ercot.com/gridinfo/generation](https://www.ercot.com/gridinfo/generation)), which is why ERCOT used to trail the other ISOs by 2–4 weeks. But the daily values you can see on ERCOT's website come from its public **Fuel Mix dashboard** ([ercot.com/gridmktinfo/dashboards/fuelmix](https://www.ercot.com/gridmktinfo/dashboards/fuelmix)), whose JSON feed (`/api/1/services/read/dashboards/fuel-mix.json`) serves 5-minute generation by fuel for the current and previous day with no auth. The connector now pulls **both**: the dashboard feed keeps ERCOT current to within ~1 day, and when the settlement workbook is published those preliminary dashboard days are automatically overwritten with settlement-quality numbers (the pipeline re-fetches a 45-day trailing window every run for exactly this purpose). The two sources typically agree within a couple of percent (telemetry vs. settlement). Note: dates between the XLSX's publication edge and when this feature went live (early July 2026) were filled with real EIA-930 data and will be overwritten by ERCOT settlement values when the workbook lands.

**About the ERCOT Public API key (api.ercot.com):** investigated and set aside for now, for two reasons. First, that API has no full generation-by-fuel-type product — per ERCOT staff and its EMIL catalog, only wind and solar actuals are exposed; the full fuel mix lives in the dashboard feed and XLSX workbooks we already use. Second, the subscription key alone isn't sufficient to call it — every request also needs a bearer token obtained from ERCOT's B2C sign-in with the account's username/password. If ERCOT ever publishes a real fuel-mix API product, the key is ready to use.

### EIA integration — how the EIA API is used (and how it isn't)

> **Setup note:** the automatic gap-filler, US48 overlay, and reconciliation all need the **`EIA_API_KEY` GitHub Actions secret** (Settings → Secrets and variables → Actions). Until it's added they skip silently — gaps are instead filled by the keyless "Backfill gaps from EIA-930" workflow (bulk files, same corrected data), and the US48 overlay simply doesn't appear.

EIA's Hourly Electric Grid Monitor (Form EIA-930) independently meters hourly generation by fuel for every U.S. balancing authority from 2018-07-01 on, exposed through the [EIA open-data API](https://www.eia.gov/opendata/) (free key → **`EIA_API_KEY` Actions secret**). After weighing "replace the ISOs with EIA" against "use EIA alongside them", the design is:

1. **ISOs stay the primary source.** They're fresher (EIA lags 1-2+ days), reach much further back (ERCOT 2007, SPP 2011 vs EIA's mid-2018), and carry finer fuel detail (EIA has no geothermal/biomass split for CAISO, no battery column before ~2023).
2. **EIA is the automatic gap-filler.** Every daily run, any interior missing day (2018-07+) that the ISO's own source can't produce gets filled with EIA's measured values — this is how ERCOT's July 2026 arrears window and MISO's 2022+ holes were filled with real data instead of interpolation.
3. **EIA's `US48` series is the national reference overlay** on the National Trend tab (dashed line): sum-of-tracked-ISOs vs the true lower-48 total, at a glance.
4. **EIA is the auditor.** The "Reconcile vs EIA-930" workflow compares our per-ISO daily totals against EIA's independent measurement and writes `docs/data/reconciliation.json`. This check is what caught a major aggregation bug (see [Logic check](#logic-check--verification-history)).

**Why is our total so much lower than EIA's national number?** Two reasons, both structural: (a) PJM — the largest U.S. market, ~2,200 GWh/day — is pending its API key; (b) roughly a third of U.S. generation isn't in any ISO at all: the vertically-integrated utility Southeast (Southern Co., TVA, Florida...), Northwest, and Southwest. Tracked ISOs ≈ 5,300 GWh/day; add PJM ≈ 7,500; EIA lower-48 total ≈ 11,500+. The dashed overlay makes this coverage gap visible instead of confusing.

### MISO gaps

MISO's historical data comes from its Data Exchange LGI API (the legacy public "Historical Generation Fuel Mix" market reports were discontinued December 12, 2025). The LGI backfill (2014→present) came back with **85 missing days** where the API itself returns nothing — holes on MISO's side, not fetch failures. Status after the 2026-07-16 repairs:

- **40 days (all the 2022+ holes) are filled with real EIA-930 data** — measured values, not estimates.
- **45 days across 2014–2017 remain** ([`docs/data/gaps.json`](docs/data/gaps.json) lists them; also shown on the Latest Snapshot tab). They predate EIA-930's mid-2018 start and appear to be genuinely unrecoverable from any public source. The daily pipeline keeps retrying them via LGI (up to 5 attempts each) in case MISO ever restores them; meanwhile charts bridge them with straight-line estimates **in the dashboard exports only** (flagged `est` in the JSON, never written to the database, replaced automatically the moment real data arrives). The dashboard footer and header chip disclose how many days are estimated.

Gap-filling is automatic for every ISO: the ISO's own source is retried first, then EIA-930 (2018-07+) as fallback, on every daily run. The manual "Backfill gaps from EIA-930" workflow (keyless bulk files) remains available as a belt-and-suspenders option.

### PJM status

PJM requires a (free) Data Miner 2 subscription key, and for non-member accounts PJM manually approves registrations — the email to `accountmanager@pjm.com` is sent and **pending approval**. Once the key arrives:

1. Add it as the `PJM_SUBSCRIPTION_KEY` secret in repo Settings → Secrets and variables → Actions.
2. The next daily run automatically backfills PJM from 2015-01-01 and it joins every view.

Until then PJM is simply absent (clearly flagged on the dashboard), so "national" totals understate the eastern U.S. Two caveats for the first keyed run, both flagged in `src/connectors/pjm.py`: the fuel-type string mapping and pagination behavior are built from public docs, not verified against live responses — worth a spot-check of the first day of real PJM data against PJM's own dashboard.

---

## Logic check & verification history

A full sanity audit of magnitudes, rankings, and units was run 2026-07-16. Results:

- **Units are correct end to end**: every connector produces daily MWh as `mean(MW over the day's readings) × 24h`; the dashboard divides by 1,000 and labels GWh. ERCOT's totals were verified to reproduce ERCOT's own published annual energy summaries exactly.
- **Size ranking is right**: MISO (~2,000 GWh/day) > ERCOT (~1,600 summer) > CAISO ≈ SPP (~550-700) > NYISO ≈ ISO-NE (~300). Calendar-2025 totals: MISO 648 TWh, ERCOT 487 TWh, ISO-NE 104 TWh — each within a few percent of the ISOs' own published figures.
- **The audit caught a real bug** (fixed 2026-07-16): the shared aggregation helpers *averaged* native fuel types that map into the same canonical bucket instead of *summing* them. Impact before the fix: SPP roughly **halved** from 2018 on (its files split every fuel into "Market"+"Self" columns), NYISO gas undercounted ~2× ("Natural Gas"+"Dual Fuel"), CAISO hydro and other-renewables undercounted (Small/Large hydro; geothermal+biomass+biogas), ISO-NE other-renewables undercounted ~3× (Wood+Refuse+Landfill Gas). MISO (LGI) and ERCOT were unaffected. **History for SPP, CAISO, NYISO and ISO-NE was re-pulled from source with the corrected code.**
- **Ongoing guard**: the "Reconcile vs EIA-930" workflow compares every ISO's daily totals against EIA's independent metering (`docs/data/reconciliation.json`) — run it after any connector change. Expected steady-state ratios are near 1.00, with small structural deviations (CAISO's feed includes imports; EIA measures net generation).

## Known limitations (read before trusting the numbers)

- **Daily MWh methodology**: every connector computes `mean(MW across the day's readings) × 24`. This is robust to each ISO's native sampling interval (5-min/15-min/hourly) and tolerates small intraday dropouts, but a day with large intraday coverage holes can be biased; it will still be internally consistent day-to-day.
- **MISO categories**: the no-auth feed used for today/yesterday folds Hydro, Pumped Storage, Diesel, and Demand Response into "Other" (→ `imports_other`); the LGI history that replaces it a day later breaks hydro out properly. So MISO's *most recent day or two* can briefly show no hydro; it firms up automatically.
- **ERCOT recent days are preliminary** telemetry until the settlement XLSX lands (see above). ERCOT's report also has no battery-storage column (batteries hide inside "Other"), so ERCOT `storage` is empty.
- **EIA-930-filled gap days** (after running the backfill workflow) use slightly coarser buckets: petroleum/unknown → `imports_other`, pumped storage inside `hydro`, no battery split; and BA-level net generation can differ a few percent from the ISO's own accounting.
- **Interpolated days**: unfilled source holes ≤10 days are drawn as straight lines in charts (disclosed in the footer/chips and `meta.json.interpolated_days`); they're estimates, not data.
- **NYISO**: "Dual Fuel" plants (oil or gas capable) are bucketed as `natural_gas`; during severe cold-snap gas curtailments some of that burn is actually oil.
- **SPP**: timestamps are GMT and used as-is for the `date` column, so SPP's day boundary is a few hours off the other ISOs' local-time boundaries.
- **ISO-NE**: history before 2018-06-30 is not available through its API (see the table footnote); if the boundary is a rolling retention window, the oldest ISO-NE days could eventually age out at the source — they'll remain safely stored here regardless.
- **Comparing ISOs of different sizes**: use the Fuel Comparison view's "% of each ISO's total" toggle; absolute GWh comparisons across markets mostly reflect market size.

---

## Dashboard views

1. **National Trend** — stacked daily generation by fuel across all covered ISOs, with EIA's lower-48 total as a dashed reference overlay (toggleable). "% of total" toggle turns it into a mix-share view (best for spotting displacement, e.g. gas ↔ coal switching).
2. **Fuel Comparison** — pick one fuel (defaults to **Natural Gas**) and see one line per ISO. 7-day-average smoothing on by default (raw daily data is dominated by weekend/weather noise); "% of each ISO's total" normalizes market size away. This is the "did something fundamental just change in gas burn, and where" view.
3. **National by ISO** — total stacked generation with one band per ISO; sums to exactly the National Trend total. Coverage holes show as a band dropping out.
4. **Per-ISO Breakdown** — full fuel stack for one chosen ISO, with the same % toggle.
5. **Latest Snapshot** — each ISO's most recent day as sorted percentage bars (tracked-ISO rollup at top), a freshness table (flags anything >3 days stale), and current gap counts.

Charts default to a 1-year window (presets 1D→Max). Under ~2 weeks, stacked views switch to bars.

## Repo structure

```
iso-power-mix/
├── src/
│   ├── connectors/          one module per ISO (+ base.py shared helpers)
│   ├── db.py                DuckDB schema, idempotent upsert, rebuild-from-JSON
│   ├── pipeline.py          orchestrator: incremental pull + automatic gap repair
│   ├── gaps.py              gap detection + retry bookkeeping (docs/data/gaps.json)
│   ├── eia.py               EIA-930 API client (gap fill, US48 overlay, reconciliation)
│   ├── export.py            DB -> docs/data/*.json (per-year files + snapshot + meta)
│   └── schema.py            canonical fuel categories / column names
├── scripts/
│   ├── run_pipeline.py      CLI entrypoint
│   ├── reconcile_eia.py     per-ISO totals vs EIA-930 sanity report
│   └── backfill_gaps_eia930.py   fills 2018+ gap days from EIA-930 bulk files (keyless)
├── data/power_mix.duckdb    local working DB - NOT committed (rebuilt from docs/data)
├── docs/                    static dashboard (GitHub Pages serves /docs on main)
│   ├── index.html, app.js, style.css
│   ├── vendor/chart.umd.min.js   vendored Chart.js (no CDN dependency)
│   └── data/                iso_daily_<year>.json, latest_snapshot.json, meta.json, gaps.json
└── .github/workflows/
    ├── daily.yml            the 13:00 UTC daily pull (+ manual re-backfill inputs)
    ├── reconcile.yml        manual EIA-930 reconciliation report
    └── backfill-gaps.yml    manual EIA-930 bulk gap filler
```

The committed per-year JSON files are the durable copy of the dataset; the DuckDB file is a derived local artifact that `src/db.py` rebuilds from them automatically on any fresh checkout (including every Actions run). Past years' files never change, so the daily commit only rewrites the small current-year file.

## API registration

CAISO, ERCOT, SPP, and NYISO need **no registration**. The other three:

### PJM
1. Sign up at https://apiportal.pjm.com/ (or via https://accountmanager.pjm.com/accountmanager/pages/public/new-user.jsf). Email verification link expires in 4 hours.
2. Non-members: email `accountmanager@pjm.com` confirming internal, non-commercial use, then wait for approval. **← currently here**
3. Once approved: apiportal.pjm.com → Profile → Subscriptions → copy the Primary key → add as the `PJM_SUBSCRIPTION_KEY` Actions secret (and `.env` locally).
   Non-member accounts are rate-limited (~6 req/min); the connector paces itself accordingly.

### MISO
1. Create a free account at https://data-exchange.misoenergy.org/ and subscribe to the **Load, Generation & Interchange (LGI)** API family.
2. Add the key as `MISO_API_KEY`. Without it, MISO still updates daily (today/yesterday feed) — the key is only needed for history/gap repair.

### ISO-NE
1. Create a free ISO Express account (https://www.iso-ne.com/participate/support/web-services-data).
2. The username/password work directly as HTTP Basic Auth: `ISONE_USERNAME` / `ISONE_PASSWORD`.

### EIA
1. Get a free key at https://www.eia.gov/opendata/ (instant, email only).
2. Add it as the `EIA_API_KEY` Actions secret. It powers the automatic gap-filler, the US48 national overlay, and the reconciliation workflow — all of which quietly skip when the key is absent.

**GitHub Actions secrets** (Settings → Secrets and variables → Actions): `PJM_SUBSCRIPTION_KEY`, `MISO_API_KEY`, `ISONE_USERNAME`, `ISONE_PASSWORD`, `EIA_API_KEY`. Missing secrets never break the run — the affected features are reported as "skipped".

## Running locally

```bash
cd iso-power-mix
python -m venv venv
venv\Scripts\activate          # Windows  (source venv/bin/activate on Mac/Linux)
pip install -r requirements.txt
copy .env.example .env         # then fill in PJM/MISO/ISONE credentials if you have them

python scripts/run_pipeline.py                 # incremental pull, all ISOs + gap repair
python scripts/run_pipeline.py --iso CAISO     # just one ISO
python scripts/run_pipeline.py --start 2020-01-01 --end 2020-12-31   # force a window
python scripts/backfill_gaps_eia930.py --dry-run   # what would the EIA-930 filler do

cd docs && python -m http.server 8420          # view the dashboard at localhost:8420
```

Running locally is never required for the website to update — it's only for development. Every run upserts idempotently (re-running a range replaces rather than duplicates) and one ISO's failure never blocks the others.

## Sustainability — what keeps this working, and what to check if it stops

Designed to run unattended:

- **Repo size stays bounded.** The DB binary is no longer committed (a ~19MB binary committed daily would have grown the repo by ~7GB/year and eventually made GitHub unhappy). Daily commits now touch a few hundred KB of JSON whose diffs compress well.
- **No external runtime dependencies.** Chart.js is vendored into `docs/vendor/`; the site is fully static and keeps working even if every data source goes down (it just stops getting fresher).
- **Failures are loud.** A real fetch failure, or any ISO going >8 days stale, makes the daily job exit non-zero → the Actions run goes red → GitHub emails the repo owner. Missing credentials are *not* treated as failures, so the pending PJM key doesn't cause daily noise. Whatever data did succeed is still committed.
- **Self-healing.** Every run re-fetches a trailing window (7 days; 45 for ERCOT), so transient misses and late-published data fix themselves; the gap-repair pass mops up older holes.
- **Things that still need a human** (rare):
  - GitHub **disables cron schedules in repos with no activity for 60 days**. The daily data commits normally count as activity, but if the workflow is ever paused/broken for 60+ days, re-enable it with one click under Actions → "Daily ISO generation-mix pull".
  - If a red run's log shows an ISO changed its website/API format, the connector needs a code fix — each connector fails independently and logs exactly what broke.
  - Actions minutes: public repos get them free/unlimited; if this repo is ever made private, the ~5-10 min/day usage fits in the free tier comfortably.

## Data verification notes

The numbers were cross-checked while building: ERCOT daily/annual MWh reproduce ERCOT's own published summary sheets exactly (including a subtle unit trap in their 15-minute interval columns, documented in `src/connectors/ercot.py`); MISO's LGI regional data is summed NORTH+CENTRAL+SOUTH before daily aggregation (documented in `src/connectors/miso.py`); the National-by-ISO and National-Trend views are derived from the same rows so they can never disagree. The `est`-flagged rows in the JSON exports and `meta.json.interpolated_days` make every estimated value identifiable programmatically.
