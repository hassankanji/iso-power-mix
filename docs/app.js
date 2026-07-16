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

function pivotNational(rows, usRows, startDate, endDate, asPct, showUs48) {
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
  // glance. Hidden in % mode (shares of the tracked total wouldn't compare).
  if (showUs48 && !asPct && usRows.length > 0) {
    const usTotals = {};
    for (const r of usRows) {
      if (r.date < startDate || r.date > endDate) continue;
      usTotals[r.date] = (usTotals[r.date] || 0) + r.mwh;
    }
    if (Object.keys(usTotals).length > 0) {
      datasets.push({
        label: "US lower-48 total (EIA)",
        data: dates.map(d => (d in usTotals ? usTotals[d] / 1000 : null)),
        type: "line",
        borderColor: "#93a0b8",
        backgroundColor: "#93a0b8",
        borderDash: [6, 4],
        borderWidth: 1.5,
        fill: false,
        pointRadius: 0,
        spanGaps: true,
        stack: "us48-reference",
      });
    }
  }
  return { dates, datasets };
}

function pivotByIso(rows, startDate, endDate) {
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

// One line per ISO for a single fuel. Missing (date, iso) pairs become null
// so the line visibly bridges gaps (spanGaps) instead of plunging to zero.
function pivotFuelComparison(rows, cat, startDate, endDate, asPct, smooth) {
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
    datasets.push({
      label: iso,
      data: smooth ? movingAverage(data, 7) : data,
      borderColor: isoColorFor(iso),
      backgroundColor: isoColorFor(iso),
      fill: false,
      pointRadius: 0,
      borderWidth: 1.8,
      spanGaps: true,
    });
  }
  return { dates, datasets };
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
        legend: { labels: { color: "#e8ebf1", boxWidth: 12 } },
        tooltip: {
          callbacks: {
            label: (ctx) => `${ctx.dataset.label}: ${fmt(ctx.parsed.y)}${unit}`,
            footer: asPct ? undefined : (items) => {
              const total = items.reduce((s, i) => s + i.parsed.y, 0);
              return `Total: ${fmt(total)} GWh`;
            },
          },
        },
      },
    },
  };
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
        legend: { labels: { color: "#e8ebf1", boxWidth: 12 } },
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

let nationalChart, fuelChart, byIsoChart, isoChart;

function renderNational(rows, usRows, start, end, asPct, showUs48) {
  const { dates, datasets } = pivotNational(rows, usRows, start, end, asPct, showUs48);
  if (nationalChart) nationalChart.destroy();
  nationalChart = new Chart(document.getElementById("national-chart"),
    stackedAreaConfig(dates, datasets, "GWh / day (national)", asPct));
}

function renderFuel(rows, cat, start, end, asPct, smooth) {
  const { dates, datasets } = pivotFuelComparison(rows, cat, start, end, asPct, smooth);
  if (fuelChart) fuelChart.destroy();
  const yLabel = asPct ? `% of each ISO's daily total` : `GWh / day of ${LABELS[cat]}`;
  fuelChart = new Chart(document.getElementById("fuel-chart"), multiLineConfig(dates, datasets, yLabel, asPct));
}

function renderByIso(rows, start, end) {
  const { dates, datasets } = pivotByIso(rows, start, end);
  if (byIsoChart) byIsoChart.destroy();
  byIsoChart = new Chart(document.getElementById("byiso-chart"), stackedAreaConfig(dates, datasets, "GWh / day (by ISO)", false));
}

function renderIso(rows, iso, start, end, asPct) {
  const { dates, datasets } = pivotSingleIso(rows, iso, start, end, asPct);
  if (isoChart) isoChart.destroy();
  isoChart = new Chart(document.getElementById("iso-chart"), stackedAreaConfig(dates, datasets, `GWh / day (${iso})`, asPct));
}

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
      <span class="value">${pct.toFixed(1)}%</span>
    `;
    container.appendChild(row);
  }
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

function setupTabs() {
  document.querySelectorAll(".tab-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("active"));
      document.querySelectorAll(".view").forEach(v => v.classList.remove("active"));
      btn.classList.add("active");
      document.getElementById(`view-${btn.dataset.view}`).classList.add("active");
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
    (isosPresent.includes("PJM") ? "" : " PJM is not included yet (awaiting API-key approval), so absolute totals understate the U.S. east.") +
    (usRows.length > 0 ? " The dashed line is EIA's independently-measured lower-48 total - the space between it and the stack is PJM plus the non-ISO regions (the utility-run Southeast, Northwest and Southwest)." : "");

  // National trend
  const nStart = document.getElementById("national-start");
  const nEnd = document.getElementById("national-end");
  const nPct = document.getElementById("national-pct");
  const nUs48 = document.getElementById("national-us48");
  if (usRows.length > 0) document.getElementById("national-us48-wrap").hidden = false;
  initDateInputs(nStart, nEnd, range);
  const refreshNational = () =>
    renderNational(rows, usRows, nStart.value, nEnd.value, nPct.checked, nUs48.checked);
  nStart.addEventListener("change", refreshNational);
  nEnd.addEventListener("change", refreshNational);
  nPct.addEventListener("change", refreshNational);
  nUs48.addEventListener("change", refreshNational);
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
  initDateInputs(fStart, fEnd, range);
  const refreshFuel = () =>
    renderFuel(rows, fuelSelect.value, fStart.value, fEnd.value, fPct.checked, fSmooth.checked);
  for (const el of [fuelSelect, fStart, fEnd, fSmooth, fPct]) el.addEventListener("change", refreshFuel);
  setupRangePresets("fuel-presets", fStart, fEnd, refreshFuel, range, "1Y");
  refreshFuel();

  // National by ISO
  const bStart = document.getElementById("byiso-start");
  const bEnd = document.getElementById("byiso-end");
  initDateInputs(bStart, bEnd, range);
  const refreshByIso = () => renderByIso(rows, bStart.value, bEnd.value);
  bStart.addEventListener("change", refreshByIso);
  bEnd.addEventListener("change", refreshByIso);
  setupRangePresets("byiso-presets", bStart, bEnd, refreshByIso, range, "1Y");
  refreshByIso();

  // Per-ISO breakdown
  const isoSelect = document.getElementById("iso-select");
  isoSelect.innerHTML = isosPresent.map(i => `<option value="${i}">${i}</option>`).join("");
  const iStart = document.getElementById("iso-start");
  const iEnd = document.getElementById("iso-end");
  const iPct = document.getElementById("iso-pct");
  initDateInputs(iStart, iEnd, range);
  const refreshIso = () => renderIso(rows, isoSelect.value, iStart.value, iEnd.value, iPct.checked);
  for (const el of [isoSelect, iStart, iEnd, iPct]) el.addEventListener("change", refreshIso);
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
