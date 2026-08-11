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

// Day-over-day change cell. `prev` undefined means the comparison day has no
// data at all for this area - render an empty cell rather than a fabricated
// swing against zero. A fuel missing from a day that DID report is a genuine
// zero, and the callers pass 0 for it.
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

// Bars are sized by share of the day's total; the number that matters to a
// gas trader is the volume, so that leads and the share follows it.
//
// `prev` is an optional {fuel_category -> MWh} map for the day before, which
// adds a change column. It is derived client-side from the same per-year rows
// the charts use, so it can never disagree with them.
function renderBarRows(container, mix, totalOverride, prev, prevLabel) {
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
      ${prev ? deltaCell(m.generation_mwh, prev[m.fuel_category] ?? 0, prevLabel) : ""}
    `;
    container.appendChild(row);
  }
  const totalRow = document.createElement("div");
  totalRow.className = "bar-row total-row";
  const prevTotal = prev ? Object.values(prev).reduce((s, v) => s + v, 0) : undefined;
  totalRow.innerHTML = `
    <span class="label">Total</span>
    <span class="bar-track"></span>
    <span class="value">${fmtEnergy(total)}</span>
    <span class="value pct">100%</span>
    ${prev ? deltaCell(total, prevTotal, prevLabel) : ""}
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
function renderSnapshotUs48(usRows) {
  const heading = document.getElementById("snapshot-us48-heading");
  const note = document.getElementById("snapshot-us48-note");
  const bars = document.getElementById("snapshot-us48-bars");
  if (usRows.length === 0) return;

  const latest = usRows.reduce((m, r) => (r.date > m ? r.date : m), "0000-00-00");
  const prevDate = addDays(latest, -1);
  const byDate = { [latest]: {}, [prevDate]: {} };
  for (const r of usRows) {
    if (r.date === latest || r.date === prevDate) {
      byDate[r.date][r.cat] = (byDate[r.date][r.cat] || 0) + r.mwh;
    }
  }
  const mix = Object.entries(byDate[latest]).map(([fuel_category, generation_mwh]) => ({
    fuel_category,
    generation_mwh,
  }));
  if (mix.length === 0) return;
  const havePrev = Object.keys(byDate[prevDate]).length > 0;

  heading.hidden = false;
  note.hidden = false;
  bars.hidden = false;
  note.textContent =
    `EIA-930's measurement of the entire U.S. lower 48 for ${latest}` +
    (havePrev ? `, with the change against ${prevDate}.` : ".") +
    " EIA publishes on its own schedule, so this day can differ from the per-ISO days below.";
  renderBarRows(bars, mix, undefined, havePrev ? byDate[prevDate] : undefined, prevDate);
}

function renderSnapshotNationalBars(mix, prev, prevLabel) {
  renderBarRows(document.getElementById("snapshot-national-bars"), mix, undefined, prev, prevLabel);
}

function renderSnapshotGrid(byIso, prevMixes) {
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
    const prevDate = addDays(as_of, -1);
    renderBarRows(barsContainer, mix, undefined, prevMixes[`${iso}|${prevDate}`], prevDate);
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
  const storageNote = document.getElementById("fuel-storage-note");
  const refreshFuel = () => {
    // Storage is the one bucket whose definition needs saying out loud - a
    // line that never dips below zero looks like missing data otherwise.
    storageNote.hidden = fuelSelect.value !== "storage";
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
  // Day-over-day change for the snapshot. Each ISO card compares against the
  // day before ITS OWN as_of, since the ISOs publish on different delays.
  //
  // The national rollup sums each ISO's own latest day, so its comparison has
  // to sum each ISO's own previous day the same way. If any one ISO is
  // missing that day, the national change is left blank: a partial sum
  // against a full one would read as a national drop that never happened.
  const isoAsOf = Object.entries(snapshot.by_iso).map(([iso, s]) => [iso, s.as_of]);
  const wantedPrev = new Set(isoAsOf.map(([iso, asOf]) => `${iso}|${addDays(asOf, -1)}`));
  const prevMixes = mixesFor(rows, wantedPrev);

  const prevNational = {};
  let nationalComparable = isoAsOf.length > 0;
  for (const [iso, asOf] of isoAsOf) {
    const prev = prevMixes[`${iso}|${addDays(asOf, -1)}`];
    if (!prev) { nationalComparable = false; break; }
    for (const [cat, mwh] of Object.entries(prev)) {
      prevNational[cat] = (prevNational[cat] || 0) + mwh;
    }
  }
  const nationalPrevLabel = "the previous day for every ISO";

  renderSnapshotUs48(usRows);
  renderSnapshotNationalBars(
    snapshot.national,
    nationalComparable ? prevNational : undefined,
    nationalPrevLabel
  );
  renderSnapshotGrid(snapshot.by_iso, prevMixes);
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
