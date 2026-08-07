const CATEGORIES = [
  "natural_gas", "coal", "nuclear", "hydro", "wind",
  "solar", "other_renewables", "storage", "imports_other",
];

const LABELS = {
  natural_gas: "Natural Gas", coal: "Coal", nuclear: "Nuclear", hydro: "Hydro",
  wind: "Wind", solar: "Solar", other_renewables: "Other Renewables",
  storage: "Storage", imports_other: "Imports / Other",
};

const ALL_ISOS = ["CAISO", "PJM", "ERCOT", "MISO", "SPP", "NYISO", "ISONE"];

function colorFor(cat) {
  return getComputedStyle(document.documentElement).getPropertyValue(`--c-${cat}`).trim();
}

function isoColorFor(iso) {
  return getComputedStyle(document.documentElement).getPropertyValue(`--iso-${iso}`).trim() || "#95a5a6";
}

async function loadJSON(name, optional = false) {
  const res = await fetch(`data/${name}`);
  if (!res.ok) {
    if (optional) return null;
    throw new Error(`Failed to load ${name}: ${res.status}`);
  }
  return res.json();
}

function uniqueSorted(arr) {
  return [...new Set(arr)].sort();
}

// ---------------------------------------------------------------------------
// Data: rows are decoded from the compact per-year files
// (docs/data/iso_daily_<year>.json, rows = [date, iso, fuel_index, mwh(, est)])
// into {date, iso, cat, mwh, est} objects once at load.

async function loadAllRows(meta) {
  const payloads = await Promise.all(meta.years.map(y => loadJSON(`iso_daily_${y}.json`)));
  const rows = [];
  for (const payload of payloads) {
    const cats = payload.fuel_categories || CATEGORIES;
    for (const r of payload.rows) {
      rows.push({ date: r[0], iso: r[1], cat: cats[r[2]], mwh: r[3], est: r.length > 4 && r[4] ? 1 : 0 });
    }
  }
  return rows;
}

// ---------------------------------------------------------------------------
// Pivots. All values are converted MWh -> GWh for display.

function pivotNational(rows, usRows, startDate, endDate, asPct) {
  const byDateCat = {};
  const totals = {};
  for (const r of rows) {
    if (r.date < startDate || r.date > endDate) continue;
    byDateCat[`${r.date}|${r.cat}`] = (byDateCat[`${r.date}|${r.cat}`] || 0) + r.mwh;
    totals[r.date] = (totals[r.date] || 0) + r.mwh;
  }
  const dates = uniqueSorted(Object.keys(totals));
  const datasets = CATEGORIES.map(cat => ({
    label: LABELS[cat],
    data: dates.map(d => {
      const v = byDateCat[`${d}|${cat}`] || 0;
      return asPct ? (totals[d] > 0 ? (v / totals[d]) * 100 : 0) : v / 1000;
    }),
    backgroundColor: colorFor(cat),
    borderColor: colorFor(cat),
    fill: true,
    stack: "mix",
    pointRadius: 0,
    borderWidth: 1,
  }));

  // EIA lower-48 national total as an unstacked dashed reference line, so
  // "how much of the U.S. grid do the tracked ISOs cover" is readable at a
  // glance. Omitted in % mode (shares of the tracked total wouldn't compare).
  // Turning it off is the legend checkbox's job - a second control for the
  // same series just gave two switches that disagreed.
  if (!asPct) {
    const overlay = us48OverlayDataset(usRows, dates, startDate, endDate, rec => rec.total / 1000);
    if (overlay) datasets.push(overlay);
  }
  return { dates, datasets };
}

// Builds the dashed, unstacked US-lower-48 reference line dataset (or null
// if there's nothing to draw). `valueFor` maps a US48 date-total map entry
// to the plotted value, letting each view scale it appropriately.
function us48OverlayDataset(usRows, dates, startDate, endDate, valueFor) {
  if (usRows.length === 0) return null;
  const usTotals = {};
  for (const r of usRows) {
    if (r.date < startDate || r.date > endDate) continue;
    const rec = usTotals[r.date] || (usTotals[r.date] = { total: 0, byCat: {} });
    rec.total += r.mwh;
    rec.byCat[r.cat] = (rec.byCat[r.cat] || 0) + r.mwh;
  }
  if (Object.keys(usTotals).length === 0) return null;
  return {
    label: "US lower-48 total (EIA)",
    data: dates.map(d => (d in usTotals ? valueFor(usTotals[d]) : null)),
    type: "line",
    borderColor: "#93a0b8",
    backgroundColor: "#93a0b8",
    borderDash: [6, 4],
    borderWidth: 1.5,
    fill: false,
    pointRadius: 0,
    spanGaps: true,
    stack: "us48-reference",
  };
}

function pivotByIso(rows, usRows, startDate, endDate) {
  const byDateIso = {};
  const dateSet = new Set();
  const isoSet = new Set();
  for (const r of rows) {
    if (r.date < startDate || r.date > endDate) continue;
    byDateIso[`${r.date}|${r.iso}`] = (byDateIso[`${r.date}|${r.iso}`] || 0) + r.mwh;
    dateSet.add(r.date);
    isoSet.add(r.iso);
  }
  const dates = [...dateSet].sort();
  const isos = [...isoSet].sort();
  const datasets = isos.map(iso => ({
    label: iso,
    data: dates.map(d => (byDateIso[`${d}|${iso}`] || 0) / 1000),
    backgroundColor: isoColorFor(iso),
    borderColor: isoColorFor(iso),
    fill: true,
    stack: "mix",
    pointRadius: 0,
    borderWidth: 1,
  }));
  const overlay = us48OverlayDataset(usRows, dates, startDate, endDate, rec => rec.total / 1000);
  if (overlay) datasets.push(overlay);
  return { dates, datasets };
}

function pivotSingleIso(rows, iso, startDate, endDate, asPct) {
  const byDateCat = {};
  const totals = {};
  for (const r of rows) {
    if (r.iso !== iso || r.date < startDate || r.date > endDate) continue;
    byDateCat[`${r.date}|${r.cat}`] = (byDateCat[`${r.date}|${r.cat}`] || 0) + r.mwh;
    totals[r.date] = (totals[r.date] || 0) + r.mwh;
  }
  const dates = uniqueSorted(Object.keys(totals));
  const datasets = CATEGORIES.map(cat => ({
    label: LABELS[cat],
    data: dates.map(d => {
      const v = byDateCat[`${d}|${cat}`] || 0;
      return asPct ? (totals[d] > 0 ? (v / totals[d]) * 100 : 0) : v / 1000;
    }),
    backgroundColor: colorFor(cat),
    borderColor: colorFor(cat),
    fill: true,
    stack: "mix",
    pointRadius: 0,
    borderWidth: 1,
  }));
  return { dates, datasets };
}

// One line per ISO for a single fuel, plus the US48 national line for that
// fuel (total national gas burn, etc.). Missing (date, iso) pairs become
// null so lines visibly bridge gaps (spanGaps) instead of plunging to zero.
// `stacked` switches the per-ISO series from independent lines to a stacked
// band (national gas burn, split by market). Stacking cannot express "this
// ISO had no data that day", so on that path the nulls become zeros - the
// same convention the National Trend stack already uses.
function pivotFuelComparison(rows, usRows, cat, startDate, endDate, asPct, smooth, stacked) {
  const value = {};      // `${date}|${iso}` -> mwh of selected fuel
  const isoTotals = {};  // `${date}|${iso}` -> mwh across all fuels
  const dateSet = new Set();
  for (const r of rows) {
    if (r.date < startDate || r.date > endDate) continue;
    const key = `${r.date}|${r.iso}`;
    isoTotals[key] = (isoTotals[key] || 0) + r.mwh;
    if (r.cat === cat) value[key] = (value[key] || 0) + r.mwh;
    dateSet.add(r.date);
  }
  const dates = [...dateSet].sort();
  const datasets = [];
  for (const iso of ALL_ISOS) {
    let any = false;
    const data = dates.map(d => {
      const key = `${d}|${iso}`;
      if (!(key in isoTotals)) return null; // ISO has no data that day at all
      const v = value[key] || 0;
      if (v !== 0) any = true;
      return asPct ? (isoTotals[key] > 0 ? (v / isoTotals[key]) * 100 : 0) : v / 1000;
    });
    if (!any) continue; // e.g. storage in ISOs whose reports have no storage column
    const series = smooth ? movingAverage(data, 7) : data;
    datasets.push({
      label: iso,
      data: stacked ? series.map(v => (v === null ? 0 : v)) : series,
      borderColor: isoColorFor(iso),
      backgroundColor: isoColorFor(iso),
      fill: !!stacked,
      pointRadius: 0,
      borderWidth: stacked ? 1 : 1.8,
      spanGaps: true,
      ...(stacked ? { stack: "mix" } : {}),
    });
  }
  const overlay = us48OverlayDataset(usRows, dates, startDate, endDate, rec => {
    const v = rec.byCat[cat] || 0;
    return asPct ? (rec.total > 0 ? (v / rec.total) * 100 : 0) : v / 1000;
  });
  if (overlay) {
    if (smooth) overlay.data = movingAverage(overlay.data, 7);
    datasets.push(overlay);
  }
  return { dates, datasets };
}

// Every stackable view can also be drawn as plain lines: stacking answers
// "what did the total look like and who contributed", lines answer "where is
// this one series going" without the neighbours below it moving the baseline.
// The pivots emit one dataset shape and this adapts it, so the toggle never
// needs a second pivot. The US48 reference line is already unstacked - leave
// it alone.
function applyChartType(datasets, chartType) {
  if (chartType !== "lines") return datasets;
  return datasets.map(ds => (
    ds.stack === "us48-reference" ? ds : { ...ds, fill: false, stack: undefined, borderWidth: 1.8, spanGaps: true }
  ));
}

// Trailing n-day mean; nulls are skipped inside the window and preserved
// where the day itself has no data.
function movingAverage(data, n) {
  const out = new Array(data.length).fill(null);
  for (let i = 0; i < data.length; i++) {
    if (data[i] === null) continue;
    let sum = 0, count = 0;
    for (let j = Math.max(0, i - n + 1); j <= i; j++) {
      if (data[j] !== null) { sum += data[j]; count++; }
    }
    out[i] = count > 0 ? sum / count : null;
  }
  return out;
}

// ---------------------------------------------------------------------------
// Chart configs

// Bloomberg-style behavior: for short windows (<= 14 days) a stacked area
// chart of daily data degenerates into a sliver, so switch to stacked bars.
const BAR_CHART_MAX_DAYS = 14;

function fmt(v) {
  return v >= 100 ? Math.round(v).toLocaleString() : v.toLocaleString(undefined, { maximumFractionDigits: 1 });
}

function stackedAreaConfig(dates, datasets, yLabel, asPct) {
  const useBars = dates.length <= BAR_CHART_MAX_DAYS;
  const unit = asPct ? "%" : " GWh";
  return {
    type: useBars ? "bar" : "line",
    data: { labels: dates, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false, // multi-year daily series can exceed 7k points per dataset
      interaction: { mode: "index", intersect: false },
      scales: {
        x: { stacked: useBars, ticks: { color: "#93a0b8", maxTicksLimit: 12 }, grid: { color: "#232b3d" } },
        y: {
          stacked: true,
          max: asPct ? 100 : undefined,
          ticks: { color: "#93a0b8" },
          grid: { color: "#232b3d" },
          title: { display: true, text: asPct ? "% of total" : yLabel, color: "#93a0b8" },
        },
      },
      plugins: {
        legend: { display: false }, // replaced by the checkbox legend (buildLegend)
        tooltip: {
          callbacks: {
            label: (ctx) => `${ctx.dataset.label}: ${fmt(ctx.parsed.y)}${unit}`,
            footer: asPct ? undefined : (items) => {
              const total = items
                .filter(i => i.dataset.stack !== "us48-reference")
                .reduce((s, i) => s + i.parsed.y, 0);
              return `Total: ${fmt(total)} GWh`;
            },
          },
        },
      },
    },
  };
}

// Picks the config for a view whose datasets can be drawn either way. Call
// it with datasets already passed through applyChartType.
function mixChartConfig(dates, datasets, yLabel, asPct, chartType) {
  return chartType === "lines"
    ? multiLineConfig(dates, datasets, yLabel, asPct)
    : stackedAreaConfig(dates, datasets, yLabel, asPct);
}

function multiLineConfig(dates, datasets, yLabel, asPct) {
  const unit = asPct ? "%" : " GWh";
  return {
    type: "line",
    data: { labels: dates, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      interaction: { mode: "index", intersect: false },
      scales: {
        x: { ticks: { color: "#93a0b8", maxTicksLimit: 12 }, grid: { color: "#232b3d" } },
        y: {
          ticks: { color: "#93a0b8" },
          grid: { color: "#232b3d" },
          title: { display: true, text: yLabel, color: "#93a0b8" },
          beginAtZero: true,
        },
      },
      plugins: {
        legend: { display: false }, // replaced by the checkbox legend (buildLegend)
        tooltip: {
          itemSort: (a, b) => b.parsed.y - a.parsed.y,
          callbacks: { label: (ctx) => `${ctx.dataset.label}: ${fmt(ctx.parsed.y)}${unit}` },
        },
      },
    },
  };
}

// ---------------------------------------------------------------------------
// Range presets

const RANGE_PRESETS = [
  { label: "1D", days: 1 },
  { label: "1W", days: 7 },
  { label: "1M", days: 30 },
  { label: "3M", days: 91 },
  { label: "6M", days: 182 },
  { label: "1Y", days: 365 },
  { label: "5Y", days: 1826 },
  { label: "Max", days: null },
];

function addDays(isoDate, n) {
  const d = new Date(isoDate + "T00:00:00Z");
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

// Renders the preset pill buttons; clicking anchors the window to the most
// recent available date. Manually editing either date clears the highlight.
function setupRangePresets(containerId, startInput, endInput, refresh, range, defaultLabel) {
  const container = document.getElementById(containerId);
  const apply = (p) => {
    endInput.value = range.max;
    if (p.days === null) {
      startInput.value = range.min;
    } else {
      const start = addDays(range.max, -(p.days - 1));
      startInput.value = start < range.min ? range.min : start;
    }
  };
  for (const p of RANGE_PRESETS) {
    const btn = document.createElement("button");
    btn.className = "preset-btn";
    btn.textContent = p.label;
    btn.addEventListener("click", () => {
      apply(p);
      container.querySelectorAll(".preset-btn").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      refresh();
    });
    if (p.label === defaultLabel) {
      btn.classList.add("active");
      apply(p);
    }
    container.appendChild(btn);
  }
  for (const input of [startInput, endInput]) {
    input.addEventListener("change", () =>
      container.querySelectorAll(".preset-btn").forEach(b => b.classList.remove("active"))
    );
  }
}

function initDateInputs(startInput, endInput, range) {
  startInput.min = endInput.min = range.min;
  startInput.max = endInput.max = range.max;
  startInput.value = range.min;
  endInput.value = range.max;
}

// ---------------------------------------------------------------------------
// Views

// ---------------------------------------------------------------------------
// Checkbox legend: replaces Chart.js's click-a-label-to-hide legend (which
// reads as static text) with explicit include/exclude checkboxes. Hidden
// series are remembered per view, surviving range/toggle changes that
// rebuild the chart.

const hiddenSeries = { national: new Set(), fuel: new Set(), byiso: new Set(), iso: new Set(), live: new Set() };

function buildLegend(viewKey, chart) {
  const container = document.getElementById(`${viewKey}-legend`);
  if (!container) return;
  container.innerHTML = "";
  const hidden = hiddenSeries[viewKey];
  chart.data.datasets.forEach((ds, i) => {
    if (hidden.has(ds.label)) chart.setDatasetVisibility(i, false);
    const label = document.createElement("label");
    label.className = "legend-check";
    const box = document.createElement("input");
    box.type = "checkbox";
    box.checked = !hidden.has(ds.label);
    box.addEventListener("change", () => {
      box.checked ? hidden.delete(ds.label) : hidden.add(ds.label);
      chart.setDatasetVisibility(i, box.checked);
      chart.update();
    });
    const swatch = document.createElement("span");
    swatch.className = "swatch";
    swatch.style.background = ds.borderColor || ds.backgroundColor;
    if (ds.borderDash) swatch.classList.add("dashed");
    label.append(box, swatch, document.createTextNode(ds.label));
    container.appendChild(label);
  });
  chart.update();
}

let nationalChart, fuelChart, byIsoChart, isoChart;

function renderNational(rows, usRows, start, end, asPct, chartType) {
  const { dates, datasets } = pivotNational(rows, usRows, start, end, asPct);
  if (nationalChart) nationalChart.destroy();
  nationalChart = new Chart(document.getElementById("national-chart"),
    mixChartConfig(dates, applyChartType(datasets, chartType), "GWh / day (national)", asPct, chartType));
  buildLegend("national", nationalChart);
}

function renderFuel(rows, usRows, cat, start, end, asPct, smooth, chartType) {
  const { dates, datasets } = pivotFuelComparison(rows, usRows, cat, start, end, asPct, smooth, chartType === "stacked");
  if (fuelChart) fuelChart.destroy();
  const yLabel = asPct ? `% of each area's daily total` : `GWh / day of ${LABELS[cat]}`;
  fuelChart = new Chart(document.getElementById("fuel-chart"),
    mixChartConfig(dates, applyChartType(datasets, chartType), yLabel, asPct, chartType));
  buildLegend("fuel", fuelChart);
}

function renderByIso(rows, usRows, start, end, chartType) {
  const { dates, datasets } = pivotByIso(rows, usRows, start, end);
  if (byIsoChart) byIsoChart.destroy();
  byIsoChart = new Chart(document.getElementById("byiso-chart"),
    mixChartConfig(dates, applyChartType(datasets, chartType), "GWh / day (by ISO)", false, chartType));
  buildLegend("byiso", byIsoChart);
}

function renderIso(rows, iso, start, end, asPct, chartType) {
  const { dates, datasets } = pivotSingleIso(rows, iso, start, end, asPct);
  if (isoChart) isoChart.destroy();
  isoChart = new Chart(document.getElementById("iso-chart"),
    mixChartConfig(dates, applyChartType(datasets, chartType), `GWh / day (${iso})`, asPct, chartType));
  buildLegend("iso", isoChart);
}

// A snapshot day's energy, in the unit a trader reads it in: GWh for one
// ISO-day, TWh once the national total gets into the thousands.
function fmtEnergy(mwh) {
  const gwh = mwh / 1000;
  if (Math.abs(gwh) >= 1000) return `${(gwh / 1000).toLocaleString(undefined, { maximumFractionDigits: 2 })} TWh`;
  if (Math.abs(gwh) >= 10) return `${Math.round(gwh).toLocaleString()} GWh`;
  return `${gwh.toLocaleString(undefined, { maximumFractionDigits: 1 })} GWh`;
}

// Bars are sized by share of the day's total; the number that matters to a
// gas trader is the volume, so that leads and the share follows it.
function renderBarRows(container, mix, totalOverride) {
  container.innerHTML = "";
  const total = totalOverride ?? mix.reduce((s, m) => s + m.generation_mwh, 0);
  const sorted = [...mix].sort((a, b) => b.generation_mwh - a.generation_mwh);
  for (const m of sorted) {
    const pct = total > 0 ? (m.generation_mwh / total) * 100 : 0;
    const row = document.createElement("div");
    row.className = "bar-row";
    row.innerHTML = `
      <span class="label">${LABELS[m.fuel_category] || m.fuel_category}</span>
      <span class="bar-track"><span class="bar-fill" style="width:${Math.max(pct, 0)}%;background:${colorFor(m.fuel_category)}"></span></span>
      <span class="value">${fmtEnergy(m.generation_mwh)}</span>
      <span class="value pct">${pct.toFixed(1)}%</span>
    `;
    container.appendChild(row);
  }
  const totalRow = document.createElement("div");
  totalRow.className = "bar-row total-row";
  totalRow.innerHTML = `
    <span class="label">Total</span>
    <span class="bar-track"></span>
    <span class="value">${fmtEnergy(total)}</span>
    <span class="value pct">100%</span>
  `;
  container.appendChild(totalRow);
}

function renderSnapshotNationalBars(mix) {
  renderBarRows(document.getElementById("snapshot-national-bars"), mix);
}

function renderSnapshotGrid(byIso) {
  const grid = document.getElementById("snapshot-grid");
  grid.innerHTML = "";
  for (const iso of Object.keys(byIso).sort()) {
    const { as_of, mix, preliminary } = byIso[iso];
    const card = document.createElement("div");
    card.className = "snapshot-card";
    const prelim = preliminary ? ' <span class="prelim" title="Live-telemetry values; replaced by settlement-quality data automatically">preliminary</span>' : "";
    card.innerHTML = `<h3>${iso}</h3><div class="as-of">as of ${as_of}${prelim}</div>`;
    const barsContainer = document.createElement("div");
    card.appendChild(barsContainer);
    grid.appendChild(card);
    renderBarRows(barsContainer, mix);
  }
}

function renderHealthTable(isoStats, gaps) {
  const tbody = document.querySelector("#health-table tbody");
  tbody.innerHTML = "";
  const maxLatest = isoStats.reduce((m, s) => (s.latest > m ? s.latest : m), "0000-00-00");
  const summary = (gaps && gaps.summary) || {};
  for (const s of isoStats) {
    const tr = document.createElement("tr");
    const daysBehind = (new Date(maxLatest) - new Date(s.latest)) / 86400000;
    if (daysBehind > 3) tr.className = "stale";
    const gapDays = summary[s.iso] ? summary[s.iso].missing_days : 0;
    tr.innerHTML = `<td>${s.iso}</td><td>${s.earliest}</td><td>${s.latest}</td><td>${s.days_covered}</td><td>${gapDays || "-"}</td>`;
    tbody.appendChild(tr);
  }
  const missingIsos = ALL_ISOS.filter(iso => !isoStats.some(s => s.iso === iso));
  for (const iso of missingIsos) {
    const tr = document.createElement("tr");
    tr.className = "stale";
    tr.innerHTML = `<td>${iso}</td><td colspan="4">no data yet (awaiting API credentials - see README)</td>`;
    tbody.appendChild(tr);
  }
}

function renderStatusChips(isoStats, meta) {
  const container = document.getElementById("status-chips");
  container.innerHTML = "";
  const chip = (text, cls) => {
    const el = document.createElement("span");
    el.className = `chip ${cls}`;
    el.textContent = text;
    container.appendChild(el);
  };
  const missingIsos = ALL_ISOS.filter(iso => !isoStats.some(s => s.iso === iso));
  for (const iso of missingIsos) chip(`${iso} not included yet (awaiting API key)`, "warn");
  const maxLatest = isoStats.reduce((m, s) => (s.latest > m ? s.latest : m), "0000-00-00");
  for (const s of isoStats) {
    const behind = Math.round((new Date(maxLatest) - new Date(s.latest)) / 86400000);
    if (behind > 3) chip(`${s.iso} data ${behind} days behind`, "warn");
  }
  const interp = meta.interpolated_days || {};
  const nInterp = Object.values(interp).reduce((s, days) => s + days.length, 0);
  if (nInterp > 0) chip(`${nInterp} missing source days estimated by interpolation`, "info");
}

// ---------------------------------------------------------------------------
// Hourly (EIA-930) tab
//
// Everything else on this dashboard is settled *daily* data. This tab reads
// EIA-930's hourly fuel-type series for respondent US48 - EIA's own lower-48
// aggregate, one number per fuel per hour - which is what the other tabs
// cannot show: the intraday shape.
//
// It is NOT real time, and this tab no longer claims to be. Measured on a
// runner 2026-08-07: the newest published hour was 14.7 hours old, and every
// hour in the window carried all 16 fuel codes, so the lag is EIA's
// publication schedule for the fuel-type breakdown, not a partial-hour
// artifact. (EIA's demand/interchange series is far fresher; the by-fuel
// split is not.) Budget 12-18 hours and show the reader the real number.
//
// It is fetched from the browser on demand (the Refresh button) rather than
// polled or baked into the daily export: no extra Actions minutes, no commits,
// and nothing fetched at all unless a reader opens this tab.
//
// Key handling: the site ships its own EIA key in data/live_key.json, written
// from the EIA_API_KEY Actions secret at publish time (never typed into the
// source - see .github/workflows/daily.yml), so a reader needs no key of
// their own. It is a published key by construction: GitHub Pages is static
// and has no server to hide one behind. That is an accepted trade - an EIA
// key is free, grants nothing but read access to public EIA data, and is
// rotated by re-running the workflow. A reader can still override it with
// their own key (localStorage), which is what the setup box offers when the
// shared key is rejected or rate-limited.

const EIA_LIVE_URL = "https://api.eia.gov/v2/electricity/rto/fuel-type-data/data/";
const LIVE_HOURS = 48;
const LIVE_KEY_STORE = "isoPowerMix.eiaApiKey";
const LIVE_CACHE_STORE = "isoPowerMix.liveCache.v1";
const LIVE_CACHE_MAX_AGE_MS = 30 * 60 * 1000; // auto-fetch on open only if staler

// Same mapping as src/eia.py's _FUELTYPE_TO_CANONICAL - keep the two in sync.
// Anything unrecognized buckets to imports_other rather than being dropped.
// OES/UES/WNB were missing here and in eia.py until the US48 code dump on
// 2026-08-07; UES is "Unknown energy storage" and was being read as "other".
const EIA_FUEL_TO_CANONICAL = {
  COL: "coal", NG: "natural_gas", NUC: "nuclear", WAT: "hydro", SUN: "solar",
  SNB: "solar", WND: "wind", WNB: "wind",
  BAT: "storage", PS: "storage", OES: "storage", UES: "storage",
  GEO: "other_renewables", BIO: "other_renewables",
  OIL: "imports_other", OTH: "imports_other", UNK: "imports_other",
};

// The site's own key, loaded once from data/live_key.json. Empty string means
// "loaded, and there isn't one" (a fork with no EIA_API_KEY secret); null
// means "not loaded yet".
let sharedLiveKey = null;

async function loadSharedLiveKey() {
  const payload = await loadJSON("live_key.json", true);
  sharedLiveKey = (payload && payload.eia_api_key) || "";
  return sharedLiveKey;
}

function ownLiveKey() {
  try { return localStorage.getItem(LIVE_KEY_STORE) || ""; } catch { return ""; }
}

// A reader's own key wins over the shared one - that is the whole point of
// the override when the shared key is rate-limited.
function liveKey() {
  return ownLiveKey() || sharedLiveKey || "";
}

function readLiveCache() {
  try { return JSON.parse(localStorage.getItem(LIVE_CACHE_STORE) || "null"); } catch { return null; }
}

function writeLiveCache(payload) {
  try { localStorage.setItem(LIVE_CACHE_STORE, JSON.stringify(payload)); } catch { /* private mode, quota */ }
}

// EIA hourly periods are UTC hour stamps ("2026-08-06T15"); shown in the
// reader's own timezone, since "is it peak yet" is a local question.
function formatLivePeriod(period) {
  const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2})$/.exec(period);
  if (!m) return period;
  const d = new Date(Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4]));
  return d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric" });
}

function periodToDate(period) {
  const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2})$/.exec(period);
  return m ? new Date(Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4])) : null;
}

// EIA hour stamps are UTC, and so is this - `start` must be in the API's own
// clock or the window slides by the reader's offset.
function utcHourStamp(msFromNow) {
  return new Date(Date.now() + msFromNow).toISOString().slice(0, 13);
}

// An hourly MWh value over a one-hour period *is* the average MW for that
// hour, so these are read as power (GW) rather than energy.
//
// `start` is not optional in practice: an unbounded sort-by-period-desc query
// makes EIA scan the whole hourly history and it answers 503 after ~35s
// (measured). Bounding the window to the last couple of days keeps it quick.
async function fetchLiveHours(apiKey) {
  const params = new URLSearchParams({
    api_key: apiKey,
    frequency: "hourly",
    "data[0]": "value",
    "facets[respondent][]": "US48",
    start: utcHourStamp(-(LIVE_HOURS + 12) * 3600 * 1000),
    "sort[0][column]": "period",
    "sort[0][direction]": "desc",
    length: String((LIVE_HOURS + 12) * 16), // ~10-13 fuel codes per hour, with headroom
    offset: "0",
  });
  let res;
  try {
    res = await fetch(`${EIA_LIVE_URL}?${params}`, { cache: "no-store" });
  } catch (e) {
    throw new Error(`Could not reach api.eia.gov (${e.message}). Check the connection, or whether a browser extension is blocking the request.`);
  }
  if (res.status === 403 || res.status === 401 || res.status === 429) {
    const e = new Error(
      res.status === 429
        ? "EIA is rate-limiting this site's API key. Paste your own free key below to keep going."
        : "EIA rejected this site's API key. Paste your own free key below to keep going."
    );
    e.keyProblem = true; // surfaces the bring-your-own-key box
    throw e;
  }
  if (res.status === 503 || res.status === 504) {
    throw new Error("EIA's API is not responding right now (503). This happens periodically - press Refresh again in a minute.");
  }
  if (!res.ok) throw new Error(`EIA API returned ${res.status} ${res.statusText}.`);
  let payload;
  try {
    payload = await res.json();
  } catch {
    throw new Error("EIA returned a non-JSON response (usually its edge cache erroring). Press Refresh again in a minute.");
  }
  const data = ((payload || {}).response || {}).data || [];
  if (data.length === 0) throw new Error("EIA returned no rows for US48 - the feed may be briefly down.");
  return data;
}

// Rolls the raw rows into {period -> {cat -> MW}}, newest first. The newest
// hour is dropped when it reports fewer fuel codes than the hours behind it:
// EIA publishes an hour incrementally, and a half-reported hour would read as
// a sudden national outage.
function rollLiveHours(rows) {
  const byPeriod = new Map();
  for (const r of rows) {
    const mw = Number(r.value);
    if (!Number.isFinite(mw)) continue;
    const cat = EIA_FUEL_TO_CANONICAL[String(r.fueltype || "").toUpperCase()] || "imports_other";
    if (!byPeriod.has(r.period)) byPeriod.set(r.period, { period: r.period, mix: {}, codes: new Set() });
    const rec = byPeriod.get(r.period);
    rec.mix[cat] = (rec.mix[cat] || 0) + mw;
    rec.codes.add(r.fueltype);
  }
  const hours = [...byPeriod.values()].sort((a, b) => (a.period < b.period ? 1 : -1));
  const fullCount = Math.max(...hours.map(h => h.codes.size));
  while (hours.length > 1 && hours[0].codes.size < fullCount) hours.shift();
  // Storage is discharge-only everywhere in this project (see src/schema.py):
  // an hour of net charging is an hour of zero storage generation, not a
  // negative slice in a stacked mix.
  for (const h of hours) {
    if (h.mix.storage < 0) h.mix.storage = 0;
  }
  // The request asks for more than LIVE_HOURS so the window is still full
  // after the unpublished tail is dropped; the surplus is trimmed here.
  return hours.slice(0, LIVE_HOURS)
    .map(h => ({ period: h.period, mix: h.mix, total: Object.values(h.mix).reduce((s, v) => s + v, 0) }));
}

function fmtPower(mw) {
  const gw = mw / 1000;
  if (Math.abs(gw) >= 10) return `${gw.toLocaleString(undefined, { maximumFractionDigits: 1 })} GW`;
  return `${gw.toLocaleString(undefined, { maximumFractionDigits: 2 })} GW`;
}

// Per-fuel bars for the newest hour. The right-hand column is the change
// against the same hour yesterday - the comparison that strips out the daily
// load and solar shape, so what's left is weather, outages and switching.
function renderLiveBars(hours) {
  const container = document.getElementById("live-bars");
  container.innerHTML = "";
  const now = hours[0];
  const yesterday = hours.find(h => {
    const a = periodToDate(now.period), b = periodToDate(h.period);
    return a && b && Math.round((a - b) / 3600000) === 24;
  });
  const sorted = CATEGORIES
    .map(cat => ({ cat, mw: now.mix[cat] || 0 }))
    .filter(d => d.mw !== 0)
    .sort((a, b) => b.mw - a.mw);
  for (const { cat, mw } of sorted) {
    const pct = now.total > 0 ? (mw / now.total) * 100 : 0;
    let delta = "";
    if (yesterday && cat in yesterday.mix) {
      const d = mw - yesterday.mix[cat];
      const cls = d >= 0 ? "up" : "down";
      delta = `<span class="value delta ${cls}" title="vs the same hour yesterday">${d >= 0 ? "+" : "-"}${fmtPower(Math.abs(d))}</span>`;
    } else {
      delta = `<span class="value delta"></span>`;
    }
    const row = document.createElement("div");
    row.className = "bar-row";
    row.innerHTML = `
      <span class="label">${LABELS[cat] || cat}</span>
      <span class="bar-track"><span class="bar-fill" style="width:${Math.max(pct, 0)}%;background:${colorFor(cat)}"></span></span>
      <span class="value">${fmtPower(mw)}</span>
      <span class="value pct">${pct.toFixed(1)}%</span>
      ${delta}
    `;
    container.appendChild(row);
  }
}

let liveChart;

function renderLiveChart(hours, chartType) {
  const ordered = [...hours].reverse(); // oldest -> newest for the x axis
  const labels = ordered.map(h => formatLivePeriod(h.period));
  const datasets = CATEGORIES
    .filter(cat => ordered.some(h => (h.mix[cat] || 0) !== 0))
    .map(cat => ({
      label: LABELS[cat],
      data: ordered.map(h => (h.mix[cat] || 0) / 1000),
      backgroundColor: colorFor(cat),
      borderColor: colorFor(cat),
      fill: true,
      stack: "mix",
      pointRadius: 0,
      borderWidth: 1,
    }));
  if (liveChart) liveChart.destroy();
  liveChart = new Chart(document.getElementById("live-chart"),
    mixChartConfig(labels, applyChartType(datasets, chartType), "GW (US lower-48)", false, chartType));
  buildLegend("live", liveChart);
}

// "14 hours behind" is the number that stops a reader treating this as live,
// so it sits next to the headline rather than in the fine print.
function fmtLag(period) {
  const stamp = periodToDate(period);
  if (!stamp) return "";
  const hours = (Date.now() - stamp.getTime()) / 3600000;
  if (hours < 1.5) return " - about an hour behind";
  if (hours < 48) return ` - ${Math.round(hours)} hours behind`;
  return ` - ${Math.round(hours / 24)} days behind`;
}

function renderLive(hours, fetchedAt, chartType) {
  const now = hours[0];
  document.getElementById("live-total").textContent = fmtPower(now.total);
  document.getElementById("live-asof").textContent =
    `hour ending ${formatLivePeriod(now.period)}${fmtLag(now.period)}`;
  renderLiveBars(hours);
  renderLiveChart(hours, chartType);
  document.getElementById("live-panels").hidden = false;
}

function setupLiveTab() {
  const refreshBtn = document.getElementById("live-refresh");
  const statusEl = document.getElementById("live-status");
  const errorEl = document.getElementById("live-error");
  const setupEl = document.getElementById("live-setup");
  const keyInput = document.getElementById("live-key");
  const saveBtn = document.getElementById("live-key-save");
  const forgetBtn = document.getElementById("live-key-clear");
  const typeSelect = document.getElementById("live-type");

  let hours = null;      // newest-first rolled hours from the last successful fetch
  let fetchedAt = null;
  let inFlight = false;

  const showError = (msg) => {
    errorEl.textContent = msg;
    errorEl.hidden = !msg;
  };

  // The setup box is a fallback now, not a gate: it appears only when there
  // is no usable key at all, or when the shared one has just been refused.
  const syncKeyUi = (keyProblem = false) => {
    const key = liveKey();
    setupEl.hidden = !!key && !keyProblem;
    forgetBtn.hidden = !ownLiveKey();
    refreshBtn.disabled = !key;
    if (!key && sharedLiveKey !== null) {
      statusEl.textContent = "No EIA API key available - add one below to enable this tab";
    }
  };

  // Resolves once data/live_key.json has been consulted. Everything that
  // needs a key waits on it, so the tab never decides "no key" prematurely.
  const keyReady = loadSharedLiveKey().catch(() => { sharedLiveKey = ""; }).then(() => syncKeyUi());

  const refresh = async () => {
    await keyReady;
    const key = liveKey();
    if (!key || inFlight) return;
    inFlight = true;
    refreshBtn.disabled = true;
    statusEl.textContent = "Fetching from EIA...";
    showError("");
    try {
      const rolled = rollLiveHours(await fetchLiveHours(key));
      hours = rolled;
      fetchedAt = Date.now();
      writeLiveCache({ hours: rolled, fetchedAt });
      renderLive(hours, fetchedAt, typeSelect.value);
      statusEl.textContent = `Updated ${new Date(fetchedAt).toLocaleTimeString()}`;
    } catch (e) {
      showError(e.message);
      if (e.keyProblem) syncKeyUi(true);
      statusEl.textContent = hours ? "Showing the last successful pull" : "No data loaded";
    } finally {
      inFlight = false;
      refreshBtn.disabled = !liveKey();
    }
  };

  saveBtn.addEventListener("click", () => {
    const key = keyInput.value.trim();
    if (!key) return;
    try { localStorage.setItem(LIVE_KEY_STORE, key); } catch { showError("This browser blocked localStorage, so the key can't be saved."); }
    keyInput.value = "";
    syncKeyUi();
    refresh();
  });
  keyInput.addEventListener("keydown", (e) => { if (e.key === "Enter") saveBtn.click(); });

  // Dropping a personal key falls back to the site's own rather than
  // emptying the tab, so this is now an undo, not a teardown.
  forgetBtn.addEventListener("click", () => {
    try { localStorage.removeItem(LIVE_KEY_STORE); } catch { /* ignore */ }
    showError("");
    syncKeyUi();
    if (liveKey()) refresh();
  });

  refreshBtn.addEventListener("click", refresh);
  typeSelect.addEventListener("change", () => { if (hours) renderLiveChart(hours, typeSelect.value); });

  // Last pull is redrawn immediately so the tab is never blank, then a fetch
  // fires only if it has gone stale - one automatic request per visit at most.
  const cached = readLiveCache();
  if (cached && cached.hours && cached.hours.length > 0) {
    hours = cached.hours;
    fetchedAt = cached.fetchedAt;
    renderLive(hours, fetchedAt, typeSelect.value);
    statusEl.textContent = `Last pull ${new Date(fetchedAt).toLocaleString()}`;
  }
  syncKeyUi();

  let autoFetched = false;
  return async () => { // called when the tab is opened
    await keyReady;
    if (autoFetched || !liveKey()) return;
    autoFetched = true;
    if (!fetchedAt || Date.now() - fetchedAt > LIVE_CACHE_MAX_AGE_MS) refresh();
  };
}

function setupTabs(onOpen = {}) {
  document.querySelectorAll(".tab-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("active"));
      document.querySelectorAll(".view").forEach(v => v.classList.remove("active"));
      btn.classList.add("active");
      document.getElementById(`view-${btn.dataset.view}`).classList.add("active");
      if (onOpen[btn.dataset.view]) onOpen[btn.dataset.view]();
    });
  });
}

async function main() {
  // The live tab is wired first and stands alone: it reads EIA directly, so
  // it still works if the committed exports below fail to load.
  setupTabs({ live: setupLiveTab() });

  const meta = await loadJSON("meta.json");
  const [allRows, snapshot, gaps] = await Promise.all([
    loadAllRows(meta),
    loadJSON("latest_snapshot.json"),
    loadJSON("gaps.json", true),
  ]);
  // iso='US48' rows are EIA's lower-48 national reference - overlay only,
  // never part of ISO sums or selectors.
  const rows = allRows.filter(r => r.iso !== "US48");
  const usRows = allRows.filter(r => r.iso === "US48");
  const range = meta.date_range;

  document.getElementById("subtitle").textContent =
    `Data ${range.min} to ${range.max} - last updated ${new Date(meta.generated_at).toLocaleString()}`;
  renderStatusChips(meta.iso_stats, meta);

  const isosPresent = uniqueSorted(rows.map(r => r.iso));
  document.getElementById("national-note").textContent =
    `National = sum of ${isosPresent.join(", ")}.` +
    (isosPresent.includes("PJM") ? "" : " PJM is not included yet, so absolute totals understate the U.S. east.") +
    (usRows.length > 0 ? " The dashed line is EIA's independently-measured lower-48 total - the space between it and the stack is the non-ISO regions (the utility-run Southeast, Northwest and Southwest, about a third of U.S. generation)." : "");

  // National trend
  const nStart = document.getElementById("national-start");
  const nEnd = document.getElementById("national-end");
  const nPct = document.getElementById("national-pct");
  const nType = document.getElementById("national-type");
  initDateInputs(nStart, nEnd, range);
  const refreshNational = () =>
    renderNational(rows, usRows, nStart.value, nEnd.value, nPct.checked, nType.value);
  for (const el of [nStart, nEnd, nPct, nType]) el.addEventListener("change", refreshNational);
  setupRangePresets("national-presets", nStart, nEnd, refreshNational, range, "1Y");
  refreshNational();

  // Fuel comparison
  const fuelSelect = document.getElementById("fuel-select");
  fuelSelect.innerHTML = CATEGORIES.map(c => `<option value="${c}">${LABELS[c]}</option>`).join("");
  fuelSelect.value = "natural_gas";
  const fStart = document.getElementById("fuel-start");
  const fEnd = document.getElementById("fuel-end");
  const fSmooth = document.getElementById("fuel-smooth");
  const fPct = document.getElementById("fuel-pct");
  const fType = document.getElementById("fuel-type");
  initDateInputs(fStart, fEnd, range);
  const refreshFuel = () => {
    // Shares of each ISO's own total can't be stacked - seven such shares
    // sum to whatever they like, not to 100.
    const stacked = fType.value === "stacked";
    fPct.disabled = stacked;
    fPct.parentElement.title = stacked ? "Not available on the stacked view - per-ISO shares don't add up to a national share" : "";
    renderFuel(rows, usRows, fuelSelect.value, fStart.value, fEnd.value, fPct.checked && !stacked, fSmooth.checked, fType.value);
  };
  for (const el of [fuelSelect, fStart, fEnd, fSmooth, fPct, fType]) el.addEventListener("change", refreshFuel);
  setupRangePresets("fuel-presets", fStart, fEnd, refreshFuel, range, "1Y");
  refreshFuel();

  // National by ISO
  const bStart = document.getElementById("byiso-start");
  const bEnd = document.getElementById("byiso-end");
  const bType = document.getElementById("byiso-type");
  initDateInputs(bStart, bEnd, range);
  const refreshByIso = () => renderByIso(rows, usRows, bStart.value, bEnd.value, bType.value);
  for (const el of [bStart, bEnd, bType]) el.addEventListener("change", refreshByIso);
  setupRangePresets("byiso-presets", bStart, bEnd, refreshByIso, range, "1Y");
  refreshByIso();

  // Per-ISO breakdown
  const isoSelect = document.getElementById("iso-select");
  isoSelect.innerHTML = isosPresent.map(i => `<option value="${i}">${i}</option>`).join("");
  const iStart = document.getElementById("iso-start");
  const iEnd = document.getElementById("iso-end");
  const iPct = document.getElementById("iso-pct");
  const iType = document.getElementById("iso-type");
  initDateInputs(iStart, iEnd, range);
  const refreshIso = () => renderIso(rows, isoSelect.value, iStart.value, iEnd.value, iPct.checked, iType.value);
  for (const el of [isoSelect, iStart, iEnd, iPct, iType]) el.addEventListener("change", refreshIso);
  setupRangePresets("iso-presets", iStart, iEnd, refreshIso, range, "1Y");
  refreshIso();

  // Latest snapshot
  renderSnapshotNationalBars(snapshot.national);
  renderSnapshotGrid(snapshot.by_iso);
  renderHealthTable(meta.iso_stats, gaps);
  const gapsNote = document.getElementById("gaps-note");
  if (gaps && gaps.summary && Object.keys(gaps.summary).length > 0) {
    const parts = Object.entries(gaps.summary).map(([iso, g]) =>
      `${iso}: ${g.missing_days} day(s)${g.still_retrying.length ? ` (${g.still_retrying.length} still being retried automatically)` : ""}`);
    gapsNote.textContent = `Unfilled source gaps - ${parts.join("; ")}. Charts bridge these with straight-line estimates; the "Backfill gaps" GitHub Action can fill 2018+ gaps with real EIA-930 data.`;
  }

  const interp = meta.interpolated_days || {};
  const nInterp = Object.values(interp).reduce((s, days) => s + days.length, 0);
  if (nInterp > 0) {
    document.getElementById("footer-quality").textContent =
      `${nInterp} day(s) missing at the source are shown as straight-line interpolations so charts don't show fake dips. See the README's Known Limitations for details.`;
  }
}

main().catch(err => {
  document.getElementById("subtitle").textContent = `Failed to load dashboard data: ${err.message}`;
  console.error(err);
});
