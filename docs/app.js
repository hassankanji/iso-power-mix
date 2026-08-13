const CATEGORIES = [
  "natural_gas", "coal", "nuclear", "hydro", "wind",
  "solar", "other_renewables", "battery", "imports_other",
];

const LABELS = {
  natural_gas: "Natural Gas", coal: "Coal", nuclear: "Nuclear", hydro: "Hydro",
  wind: "Wind", solar: "Solar", other_renewables: "Other Renewables",
  battery: "Battery", imports_other: "Imports / Other",
};

// Year files written before the bucket was renamed carry the old name. One
// export run rewrites them all, but a reader holding a cached copy of an
// older file must still decode. Mirrors src/schema.py's alias table.
const LEGACY_CATEGORIES = { storage: "battery" };

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
    const cats = (payload.fuel_categories || CATEGORIES).map(c => LEGACY_CATEGORIES[c] || c);
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

const SMOOTH_WINDOW = 7;

// One line per ISO for a single fuel, plus the US48 national line for that
// fuel (total national gas burn, etc.). Missing (date, iso) pairs become
// null so lines visibly bridge gaps (spanGaps) instead of plunging to zero.
// `stacked` switches the per-ISO series from independent lines to a stacked
// band (national gas burn, split by market). Stacking cannot express "this
// ISO had no data that day", so on that path the nulls become zeros - the
// same convention the National Trend stack already uses.
//
// Smoothing reads SMOOTH_WINDOW-1 days of run-up BEFORE the requested start
// and drops them again once the means are computed, so every plotted point is
// a true 7-day average. Averaging only what's inside the window instead makes
// the left edge of a short range a 1-, 2-, 3-day mean wearing a 7-day label:
// on a 1W range that was six of the seven points.
function pivotFuelComparison(rows, usRows, cat, startDate, endDate, asPct, smooth, stacked) {
  const runUpStart = smooth ? addDays(startDate, -(SMOOTH_WINDOW - 1)) : startDate;
  const value = {};      // `${date}|${iso}` -> mwh of selected fuel
  const isoTotals = {};  // `${date}|${iso}` -> mwh across all fuels
  const dateSet = new Set();
  for (const r of rows) {
    if (r.date < runUpStart || r.date > endDate) continue;
    const key = `${r.date}|${r.iso}`;
    isoTotals[key] = (isoTotals[key] || 0) + r.mwh;
    if (r.cat === cat) value[key] = (value[key] || 0) + r.mwh;
    dateSet.add(r.date);
  }
  const allDates = [...dateSet].sort();
  const dates = allDates.filter(d => d >= startDate);
  // The run-up is a prefix of allDates, so dropping it is a tail slice.
  const trim = (series) => series.slice(allDates.length - dates.length);

  const datasets = [];
  for (const iso of ALL_ISOS) {
    const data = allDates.map(d => {
      const key = `${d}|${iso}`;
      if (!(key in isoTotals)) return null; // ISO has no data that day at all
      const v = value[key] || 0;
      return asPct ? (isoTotals[key] > 0 ? (v / isoTotals[key]) * 100 : 0) : v / 1000;
    });
    const series = trim(smooth ? movingAverage(data, SMOOTH_WINDOW) : data);
    // e.g. storage in ISOs whose reports have no storage column - judged on
    // what's actually on screen, not on the run-up days nobody sees.
    if (!series.some(v => v !== null && v !== 0)) continue;
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
  const overlay = us48OverlayDataset(usRows, allDates, runUpStart, endDate, rec => {
    const v = rec.byCat[cat] || 0;
    return asPct ? (rec.total > 0 ? (v / rec.total) * 100 : 0) : v / 1000;
  });
  if (overlay) {
    overlay.data = trim(smooth ? movingAverage(overlay.data, SMOOTH_WINDOW) : overlay.data);
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

// Every chart labels the day it is hovering. When a transform sits between
// the stored day and the plotted point - today only the 7-day average - the
// tooltip has to say so, or the same date reads as two different numbers here
// and on the snapshot and looks like a data bug. `note` is that qualifier.
function tooltipTitle(note) {
  return note ? (items) => `${items[0].label} - ${note}` : undefined;
}

function stackedAreaConfig(dates, datasets, yLabel, asPct, note) {
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
            title: tooltipTitle(note),
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
function mixChartConfig(dates, datasets, yLabel, asPct, chartType, note) {
  return chartType === "lines"
    ? multiLineConfig(dates, datasets, yLabel, asPct, note)
    : stackedAreaConfig(dates, datasets, yLabel, asPct, note);
}

function multiLineConfig(dates, datasets, yLabel, asPct, note) {
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
          callbacks: {
            title: tooltipTitle(note),
            label: (ctx) => `${ctx.dataset.label}: ${fmt(ctx.parsed.y)}${unit}`,
          },
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

// Calendar months, not 30-day blocks: "vs last month" means the same day of
// the month to anyone reading a mix, and a 30-day step drifts off it. Clamped
// to the target month's length, so 31 March back one month is 28 February
// rather than rolling forward into March again the way a naive setUTCMonth
// would.
function addMonths(isoDate, n) {
  const d = new Date(isoDate + "T00:00:00Z");
  const day = d.getUTCDate();
  d.setUTCDate(1);
  d.setUTCMonth(d.getUTCMonth() + n);
  const lastOfMonth = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 1, 0)).getUTCDate();
  d.setUTCDate(Math.min(day, lastOfMonth));
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

const hiddenSeries = { national: new Set(), fuel: new Set(), byiso: new Set(), iso: new Set() };

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
  const smoothing = smooth ? ` (${SMOOTH_WINDOW}-day avg)` : "";
  const yLabel = (asPct ? `% of each area's daily total` : `GWh / day of ${LABELS[cat]}`) + smoothing;
  const note = smooth ? `${SMOOTH_WINDOW}-day trailing average` : "";
  fuelChart = new Chart(document.getElementById("fuel-chart"),
    mixChartConfig(dates, applyChartType(datasets, chartType), yLabel, asPct, chartType, note));
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

// Change cell. `prev` undefined means there is nothing to compare against -
// either the comparison day is missing for this area entirely, or it reported
// but carried no row for this fuel. Both render empty rather than as a
// fabricated swing against zero.
//
// The second case is the one worth stating: an absent row and a zero row mean
// different things and the data keeps them apart. PJM stores an explicit 0 for
// battery through 2016 because PJM measured zero; ERCOT stores no battery row
// at all before EIA fills it, because ERCOT's settlement workbook has no such
// column. Reading the absence as zero turned the second case into a +23 GWh
// day-over-day swing that never happened.
function deltaCell(now, prev, prevLabel) {
  if (prev === undefined || prev === null || !Number.isFinite(prev)) {
    return `<span class="value delta"></span>`;
  }
  const d = now - prev;
  const pct = prev !== 0 ? ` (${d >= 0 ? "+" : ""}${((d / Math.abs(prev)) * 100).toFixed(1)}%)` : "";
  // Anything under 50 MWh rounds to zero at this display precision, and
  // "-0 GWh" reads as a rendering bug rather than as "flat".
  if (Math.abs(d) < 50) {
    return `<span class="value delta" title="vs ${prevLabel}${pct}">0 GWh</span>`;
  }
  const cls = d > 0 ? "up" : "down";
  const sign = d > 0 ? "+" : "-";
  return `<span class="value delta ${cls}" title="vs ${prevLabel}${pct}">${sign}${fmtEnergy(Math.abs(d))}</span>`;
}

// The periods a snapshot can be compared against. Day-over-day answers "what
// moved overnight"; the longer ones answer "is this a normal August" - a
// single hot day reads as a spike against yesterday and as nothing at all
// against last year. All four at once is a lot of columns, so the selector
// defaults to one and `all` is opt-in.
const SNAPSHOT_PERIODS = [
  { key: "1D", label: "1D", shift: (d) => addDays(d, -1) },
  { key: "1W", label: "1W", shift: (d) => addDays(d, -7) },
  { key: "1M", label: "1M", shift: (d) => addMonths(d, -1) },
  { key: "1Y", label: "1Y", shift: (d) => addMonths(d, -12) },
];

function selectedPeriods(value) {
  return value === "all" ? SNAPSHOT_PERIODS : SNAPSHOT_PERIODS.filter(p => p.key === value);
}

// Resolves each selected period against one area's as-of date into the
// {label, date, mix} shape renderBarRows wants. `mixes` is keyed `iso|date`.
function comparisonsFor(iso, asOf, periods, mixes) {
  return periods.map(p => {
    const date = p.shift(asOf);
    return { label: p.label, date, mix: mixes[`${iso}|${date}`] };
  });
}

// Bars are sized by share of the day's total; the number that matters to a
// gas trader is the volume, so that leads and the share follows it.
//
// `comparisons` is a list of {label, date, mix} - each adds a change column,
// where `mix` is a {fuel_category -> MWh} map for that comparison day or
// undefined if the area has no data for it. They are derived client-side from
// the same per-year rows the charts use, so they can never disagree.
function renderBarRows(container, mix, comparisons = []) {
  container.innerHTML = "";
  const total = mix.reduce((s, m) => s + m.generation_mwh, 0);
  const deltas = (value, pick) =>
    comparisons.map(c => deltaCell(value, c.mix ? pick(c.mix) : undefined, c.date)).join("");

  // One column is self-explanatory from its tooltip; several are not.
  if (comparisons.length > 1) {
    const head = document.createElement("div");
    head.className = "bar-row head-row";
    head.innerHTML = `
      <span class="label"></span>
      <span class="bar-track"></span>
      <span class="value">Output</span>
      <span class="value pct">Share</span>
      ${comparisons.map(c => `<span class="value delta" title="vs ${c.date}">${c.label}</span>`).join("")}
    `;
    container.appendChild(head);
  }

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
      ${deltas(m.generation_mwh, prev => prev[m.fuel_category])}
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
    ${deltas(total, prev => Object.values(prev).reduce((s, v) => s + v, 0))}
  `;
  container.appendChild(totalRow);
}

// Collects {iso|date -> {cat -> mwh}} for just the (iso, date) pairs asked
// for, in one pass over the row array. Building a full index instead would
// allocate an object per ISO-day across twenty years to answer seven
// questions.
function mixesFor(rows, wanted) {
  const out = {};
  for (const r of rows) {
    const key = `${r.iso}|${r.date}`;
    if (!wanted.has(key)) continue;
    const rec = out[key] || (out[key] = {});
    rec[r.cat] = (rec[r.cat] || 0) + r.mwh;
  }
  return out;
}

// EIA's lower-48 total for its own latest day, with the change against the
// day before it. This is the whole country - the utility-run Southeast,
// Northwest and Southwest included - not the seven tracked ISOs, so it is
// deliberately a separate panel rather than another card in the ISO grid.
//
// It runs on its own clock: EIA publishes later than the ISOs do, so its
// latest day is often a day behind theirs. The heading states which day it
// is instead of implying it lines up with the ISO snapshot.
function renderSnapshotUs48(usRows, periods) {
  const heading = document.getElementById("snapshot-us48-heading");
  const note = document.getElementById("snapshot-us48-note");
  const bars = document.getElementById("snapshot-us48-bars");
  if (usRows.length === 0) return;

  const latest = usRows.reduce((m, r) => (r.date > m ? r.date : m), "0000-00-00");
  const wanted = new Set([`US48|${latest}`, ...periods.map(p => `US48|${p.shift(latest)}`)]);
  const mixes = mixesFor(usRows, wanted);
  const today = mixes[`US48|${latest}`];
  if (!today) return;
  const mix = Object.entries(today).map(([fuel_category, generation_mwh]) => ({
    fuel_category,
    generation_mwh,
  }));

  heading.hidden = false;
  note.hidden = false;
  bars.hidden = false;
  note.textContent =
    `EIA-930's measurement of the entire U.S. lower 48 for ${latest}.` +
    " EIA publishes on its own schedule, so this day can differ from the per-ISO days below.";
  renderBarRows(bars, mix, comparisonsFor("US48", latest, periods, mixes));
}

function renderSnapshotNationalBars(mix, comparisons) {
  renderBarRows(document.getElementById("snapshot-national-bars"), mix, comparisons);
}

function renderSnapshotGrid(byIso, periods, prevMixes) {
  const grid = document.getElementById("snapshot-grid");
  grid.innerHTML = "";
  // Four change columns no longer fit beside a readable bar at the default
  // card width, so the grid widens with the number of columns instead of
  // crushing the track.
  grid.classList.toggle("wide", periods.length > 1);
  for (const iso of Object.keys(byIso).sort()) {
    const { as_of, mix, preliminary } = byIso[iso];
    const card = document.createElement("div");
    card.className = "snapshot-card";
    const prelim = preliminary ? ' <span class="prelim" title="Live-telemetry values; replaced by settlement-quality data automatically">preliminary</span>' : "";
    card.innerHTML = `<h3>${iso}</h3><div class="as-of">as of ${as_of}${prelim}</div>`;
    const barsContainer = document.createElement("div");
    card.appendChild(barsContainer);
    grid.appendChild(card);
    renderBarRows(barsContainer, mix, comparisonsFor(iso, as_of, periods, prevMixes));
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
  setupTabs();

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
  const batteryNote = document.getElementById("fuel-battery-note");
  const refreshFuel = () => {
    // Battery is the one bucket whose definition needs saying out loud - a
    // line that never dips below zero looks like missing data otherwise, and
    // the national reference line is not an upper bound for it.
    batteryNote.hidden = fuelSelect.value !== "battery";
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

  // Latest snapshot.
  // Each ISO card compares against its OWN as_of shifted back, since the ISOs
  // publish on different delays - comparing them all against one calendar
  // date would put a fresh ISO against a stale one.
  //
  // Every period for every ISO is collected in a single pass here, so
  // changing the selector re-renders from memory instead of re-walking twenty
  // years of rows.
  const isoAsOf = Object.entries(snapshot.by_iso).map(([iso, s]) => [iso, s.as_of]);
  const wantedPrev = new Set();
  for (const [iso, asOf] of isoAsOf) {
    for (const p of SNAPSHOT_PERIODS) wantedPrev.add(`${iso}|${p.shift(asOf)}`);
  }
  const prevMixes = mixesFor(rows, wantedPrev);

  // The national rollup sums each ISO's own latest day, so its comparison has
  // to sum each ISO's own shifted day the same way. If any one ISO is missing
  // that day, that period is left blank: a partial sum against a full one
  // would read as a national drop that never happened.
  const nationalComparison = (period) => {
    const summed = {};
    for (const [iso, asOf] of isoAsOf) {
      const prev = prevMixes[`${iso}|${period.shift(asOf)}`];
      if (!prev) return { label: period.label, date: period.shift(isoAsOf[0][1]), mix: undefined };
      for (const [cat, mwh] of Object.entries(prev)) summed[cat] = (summed[cat] || 0) + mwh;
    }
    return {
      label: period.label,
      date: `${period.shift(isoAsOf[0][1])} (each ISO shifted by its own as-of)`,
      mix: isoAsOf.length > 0 ? summed : undefined,
    };
  };

  const periodSelect = document.getElementById("snapshot-period");
  const refreshSnapshot = () => {
    const periods = selectedPeriods(periodSelect.value);
    renderSnapshotUs48(usRows, periods);
    renderSnapshotNationalBars(snapshot.national, periods.map(nationalComparison));
    renderSnapshotGrid(snapshot.by_iso, periods, prevMixes);
  };
  periodSelect.addEventListener("change", refreshSnapshot);
  refreshSnapshot();

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
