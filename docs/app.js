const CATEGORIES = [
  "natural_gas", "coal", "nuclear", "hydro", "wind",
  "solar", "other_renewables", "storage", "imports_other",
];

const LABELS = {
  natural_gas: "Natural Gas", coal: "Coal", nuclear: "Nuclear", hydro: "Hydro",
  wind: "Wind", solar: "Solar", other_renewables: "Other Renewables",
  storage: "Storage", imports_other: "Imports / Other",
};

function colorFor(cat) {
  return getComputedStyle(document.documentElement).getPropertyValue(`--c-${cat}`).trim();
}

function isoColorFor(iso) {
  return getComputedStyle(document.documentElement).getPropertyValue(`--iso-${iso}`).trim() || "#95a5a6";
}

async function loadJSON(name) {
  const res = await fetch(`data/${name}`);
  if (!res.ok) throw new Error(`Failed to load ${name}: ${res.status}`);
  return res.json();
}

function uniqueSorted(arr) {
  return [...new Set(arr)].sort();
}

// Pivots long-format rows [{date, fuel_category, generation_mwh}, ...] into
// one Chart.js dataset per category, aligned on the union of dates (missing = 0).
function pivotForStackedArea(rows, startDate, endDate) {
  const filtered = rows.filter(r => r.date >= startDate && r.date <= endDate);
  const dates = uniqueSorted(filtered.map(r => r.date));
  const byDateCat = {};
  for (const r of filtered) {
    byDateCat[`${r.date}|${r.fuel_category}`] = (byDateCat[`${r.date}|${r.fuel_category}`] || 0) + r.generation_mwh;
  }
  const datasets = CATEGORIES.map(cat => ({
    label: LABELS[cat],
    data: dates.map(d => (byDateCat[`${d}|${cat}`] || 0) / 1000), // GWh for readability
    backgroundColor: colorFor(cat),
    borderColor: colorFor(cat),
    fill: true,
    stack: "mix",
    pointRadius: 0,
    borderWidth: 1,
  }));
  return { dates, datasets };
}

function stackedAreaConfig(dates, datasets, yLabel) {
  return {
    type: "line",
    data: { labels: dates, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false, // multi-year daily series can exceed 7k points per dataset - animating that is visibly slow
      interaction: { mode: "index", intersect: false },
      scales: {
        x: { ticks: { color: "#93a0b8", maxTicksLimit: 12 }, grid: { color: "#232b3d" } },
        y: {
          stacked: true,
          ticks: { color: "#93a0b8" },
          grid: { color: "#232b3d" },
          title: { display: true, text: yLabel, color: "#93a0b8" },
        },
      },
      plugins: {
        legend: { labels: { color: "#e8ebf1", boxWidth: 12 } },
        tooltip: { callbacks: { label: (ctx) => `${ctx.dataset.label}: ${ctx.parsed.y.toLocaleString()} GWh` } },
      },
    },
  };
}

// Pivots the full per-ISO rows into one dataset per ISO (summing across fuel
// categories). Because this consumes the same iso_daily rows as the fuel-type
// pivot, the stacked totals of the two views are equal by construction.
function pivotByIso(rows, startDate, endDate) {
  const filtered = rows.filter(r => r.date >= startDate && r.date <= endDate);
  const dates = uniqueSorted(filtered.map(r => r.date));
  const isos = uniqueSorted(filtered.map(r => r.iso));
  const byDateIso = {};
  for (const r of filtered) {
    byDateIso[`${r.date}|${r.iso}`] = (byDateIso[`${r.date}|${r.iso}`] || 0) + r.generation_mwh;
  }
  const datasets = isos.map(iso => ({
    label: iso,
    data: dates.map(d => (byDateIso[`${d}|${iso}`] || 0) / 1000), // GWh
    backgroundColor: isoColorFor(iso),
    borderColor: isoColorFor(iso),
    fill: true,
    stack: "mix",
    pointRadius: 0,
    borderWidth: 1,
  }));
  return { dates, datasets };
}

let nationalChart, isoChart, byIsoChart;

function renderNational(rows, start, end) {
  const { dates, datasets } = pivotForStackedArea(rows, start, end);
  if (nationalChart) nationalChart.destroy();
  nationalChart = new Chart(document.getElementById("national-chart"), stackedAreaConfig(dates, datasets, "GWh / day (national)"));
}

function renderByIso(rows, start, end) {
  const { dates, datasets } = pivotByIso(rows, start, end);
  if (byIsoChart) byIsoChart.destroy();
  byIsoChart = new Chart(document.getElementById("byiso-chart"), stackedAreaConfig(dates, datasets, "GWh / day (by ISO)"));
}

function renderIso(rows, iso, start, end) {
  const filtered = rows.filter(r => r.iso === iso);
  const { dates, datasets } = pivotForStackedArea(filtered, start, end);
  if (isoChart) isoChart.destroy();
  isoChart = new Chart(document.getElementById("iso-chart"), stackedAreaConfig(dates, datasets, `GWh / day (${iso})`));
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

function renderSnapshotNationalChart(mix) {
  const sorted = [...mix].sort((a, b) => CATEGORIES.indexOf(a.fuel_category) - CATEGORIES.indexOf(b.fuel_category));
  const canvas = document.getElementById("snapshot-national-chart");
  new Chart(canvas, {
    type: "doughnut",
    data: {
      labels: sorted.map(m => LABELS[m.fuel_category] || m.fuel_category),
      datasets: [{
        data: sorted.map(m => m.generation_mwh),
        backgroundColor: sorted.map(m => colorFor(m.fuel_category)),
        borderColor: "#171d2b",
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { position: "right", labels: { color: "#e8ebf1", boxWidth: 12 } } },
    },
  });
}

function renderSnapshotGrid(byIso) {
  const grid = document.getElementById("snapshot-grid");
  grid.innerHTML = "";
  for (const iso of Object.keys(byIso).sort()) {
    const { as_of, mix } = byIso[iso];
    const card = document.createElement("div");
    card.className = "snapshot-card";
    card.innerHTML = `<h3>${iso}</h3><div class="as-of">as of ${as_of}</div>`;
    const barsContainer = document.createElement("div");
    card.appendChild(barsContainer);
    grid.appendChild(card);
    renderBarRows(barsContainer, mix);
  }
}

function renderHealthTable(isoStats) {
  const tbody = document.querySelector("#health-table tbody");
  tbody.innerHTML = "";
  const maxLatest = isoStats.reduce((m, s) => (s.latest > m ? s.latest : m), "0000-00-00");
  for (const s of isoStats) {
    const tr = document.createElement("tr");
    const daysBehind = (new Date(maxLatest) - new Date(s.latest)) / 86400000;
    if (daysBehind > 3) tr.className = "stale";
    tr.innerHTML = `<td>${s.iso}</td><td>${s.earliest}</td><td>${s.latest}</td><td>${s.days_covered}</td>`;
    tbody.appendChild(tr);
  }
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

  const [national, isoDaily, snapshot, meta] = await Promise.all([
    loadJSON("national_daily.json"),
    loadJSON("iso_daily.json"),
    loadJSON("latest_snapshot.json"),
    loadJSON("meta.json"),
  ]);

  document.getElementById("subtitle").textContent =
    `Data ${meta.date_range.min} to ${meta.date_range.max} - last updated ${new Date(meta.generated_at).toLocaleString()}`;

  // National trend
  const nStart = document.getElementById("national-start");
  const nEnd = document.getElementById("national-end");
  nStart.min = nEnd.min = meta.date_range.min;
  nStart.max = nEnd.max = meta.date_range.max;
  nStart.value = meta.date_range.min;
  nEnd.value = meta.date_range.max;
  const refreshNational = () => renderNational(national, nStart.value, nEnd.value);
  nStart.addEventListener("change", refreshNational);
  nEnd.addEventListener("change", refreshNational);
  refreshNational();

  // National by ISO
  const bStart = document.getElementById("byiso-start");
  const bEnd = document.getElementById("byiso-end");
  bStart.min = bEnd.min = meta.date_range.min;
  bStart.max = bEnd.max = meta.date_range.max;
  bStart.value = meta.date_range.min;
  bEnd.value = meta.date_range.max;
  const refreshByIso = () => renderByIso(isoDaily, bStart.value, bEnd.value);
  bStart.addEventListener("change", refreshByIso);
  bEnd.addEventListener("change", refreshByIso);
  refreshByIso();

  // Per-ISO breakdown
  const isoSelect = document.getElementById("iso-select");
  const isos = uniqueSorted(isoDaily.map(r => r.iso));
  isoSelect.innerHTML = isos.map(i => `<option value="${i}">${i}</option>`).join("");
  const iStart = document.getElementById("iso-start");
  const iEnd = document.getElementById("iso-end");
  iStart.min = iEnd.min = meta.date_range.min;
  iStart.max = iEnd.max = meta.date_range.max;
  iStart.value = meta.date_range.min;
  iEnd.value = meta.date_range.max;
  const refreshIso = () => renderIso(isoDaily, isoSelect.value, iStart.value, iEnd.value);
  isoSelect.addEventListener("change", refreshIso);
  iStart.addEventListener("change", refreshIso);
  iEnd.addEventListener("change", refreshIso);
  refreshIso();

  // Latest snapshot
  renderSnapshotNationalChart(snapshot.national);
  renderSnapshotGrid(snapshot.by_iso);
  renderHealthTable(meta.iso_stats);
}

main().catch(err => {
  document.getElementById("subtitle").textContent = `Failed to load dashboard data: ${err.message}`;
  console.error(err);
});
