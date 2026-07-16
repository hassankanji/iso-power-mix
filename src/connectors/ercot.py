"""ERCOT connector.

Two complementary no-auth sources:

1. The "Fuel Mix Report" XLSX workbooks (settlement-quality data, published
   at https://www.ercot.com/gridinfo/generation) - full history back to
   2007, but the current-year workbook is published in arrears, typically
   1-4 weeks behind (a month's sheet stays empty until ERCOT posts it, then
   fills in all at once).

2. The public Fuel Mix dashboard JSON feed behind
   https://www.ercot.com/gridmktinfo/dashboards/fuelmix:
       https://www.ercot.com/api/1/services/read/dashboards/fuel-mix.json
   This is the "daily values on ERCOT's website". It carries 5-minute
   telemetered generation by fuel for the current day and the previous
   day(s) only - no history - so it can't replace the XLSX, but pulling it
   every day keeps ERCOT current to within ~1 day instead of weeks behind.

Rows from the two sources differ slightly (telemetry vs settlement), so the
XLSX always wins wherever both cover a date: fetch_range takes dashboard
rows only for dates the XLSX doesn't have yet, and the pipeline's ERCOT
lookback window (LOOKBACK_DAYS below) re-fetches the last ~45 days on every
run so freshly-published settlement data replaces the earlier dashboard
values in the database.

This module deliberately does NOT use the newer api.ercot.com, which per
ERCOT staff only exposes Wind/Solar rather than the full fuel mix.

The generation page publishes three kinds of downloads, and the exact CDN
paths (which embed the posting date, e.g. ``/files/docs/2026/02/09/...``)
roll forward unpredictably, so this module fetches the page live and
regex-scrapes the real href values rather than hardcoding a URL:
  - current-year XLSX, e.g. ``IntGenbyFuel2026.xlsx``
  - prior-year XLSX, e.g. ``IntGenbyFuel2025.xlsx``
  - a historical archive zip, ``FuelMixReport_PreviousYears.zip``, holding
    one workbook per year for 2007-2024 (``IntGenByFuel2007.xls`` ...
    ``IntGenByFuel2024.xlsx`` - capitalization of "by" is inconsistent
    across years, and years 2007-2015 are legacy binary .xls).

Two distinct sheet layouts were found by inspecting real downloads:
  - 2017-present ("new" format): each month is its own sheet with columns
    ``Date, Fuel (or "Fuel Type"), Settlement Type, Total, <96 interval
    columns labeled by time-of-day>``. One row per date+fuel.
  - 2007-2016 ("old" format): each month is its own sheet with columns
    ``<Date-Fuel key>, Total (or "Daily MWH"), <interval columns>``, where
    the first column is a combined string like ``"12/01/07 - Coal"`` or
    ``"01/01/15_Biomass"`` (the date is always the first 8 characters,
    MM/DD/YY; the separator between date and fuel name varies by year).

Both layouts include a "Total"/"Daily MWH" column. Despite the report
calling the 96 per-day columns "15-minute settlement intervals" (which
reads like instantaneous MW snapshots), they are actually already
quarter-hour ENERGY (MWh generated in that specific 15-minute interval):
sum(the 96 interval values) was cross-checked against ERCOT's own
published "Energy, GWh" year-to-date summary sheet (data_Summary_1 in the
2026/2025 workbooks; the "20XX Summary"/"Fuel Totals" sheet in older
workbooks) and matches to the reported precision, both for a partial-year
sum (Gas-CC and Wind in the 2026 workbook) and a full calendar year (Coal
in 2015). So each interval value must be divided by its 0.25h duration -
i.e. multiplied by 4 - to get an equivalent average MW before handing it
to base.long_to_daily_mwh's mean(MW) * 24 formula; that conversion
reproduces ERCOT's official daily MWh exactly. (Naively treating the raw
interval values as already-MW and averaging them undercounts every day's
generation by exactly 4x.)

Native fuel categories observed across 2007-2026: Biomass, Coal, Gas,
Gas-CC (and Gas_CC/other Gas* variants in some years), Hydro, Nuclear,
Other, Solar, WSL (Settlement-Only distributed generation, present from
~2022 on), Wind, plus legacy abbreviations Oth/Wnd in 2007. No dedicated
battery/storage native column was ever observed in this report (even in
2024-2026), despite ERCOT's real fuel mix including significant battery
capacity by 2023+ - it appears to be folded into "Other"/"WSL" upstream.
Storage synonyms are still mapped defensively below in case ERCOT adds a
dedicated column later. Any unrecognized native column falls back to
imports_other rather than being silently dropped.

Years 2007-2015 are legacy binary .xls files, which pandas can only read
via the optional ``xlrd`` package. It is not in this project's
requirements.txt (not installed by default); if it's unavailable, those
years are skipped gracefully (per the "part of the range isn't available"
contract) rather than failing the whole call. Install ``xlrd>=2.0.1`` to
enable them.
"""
from __future__ import annotations

import io
import re
import zipfile
from datetime import date, timedelta

import pandas as pd

from src.connectors.base import finalize, http_session, long_to_daily_mwh
from src.schema import CANONICAL_CATEGORIES

ISO = "ERCOT"
EARLIEST_DATE = date(2007, 1, 1)
REQUIRES_AUTH = False
# Re-fetch this many trailing days every incremental run (overrides the
# pipeline default of 7): once the dashboard feed keeps ERCOT current to
# ~1 day, the XLSX needs a window this wide to overwrite those preliminary
# dashboard rows with settlement data when ERCOT publishes it in arrears.
LOOKBACK_DAYS = 45

_GENERATION_PAGE_URL = "https://www.ercot.com/gridinfo/generation"
_DASHBOARD_FUELMIX_URL = "https://www.ercot.com/api/1/services/read/dashboards/fuel-mix.json"
_XLSX_LINK_RE = re.compile(
    r"https://www\.ercot\.com/files/docs/\S*?IntGenbyFuel(\d{4})\.xlsx",
    re.IGNORECASE,
)
_ZIP_LINK_RE = re.compile(
    r"https://www\.ercot\.com/files/docs/\S*?FuelMixReport_PreviousYears\.zip",
    re.IGNORECASE,
)

# Identity map so we can reuse base.long_to_daily_mwh after resolving native
# fuel names to canonical buckets ourselves (see _canon_fuel) - lets us apply
# a case/separator-insensitive fallback-to-imports_other policy instead of
# the helper's default of silently dropping unmapped rows.
_IDENTITY_CATEGORY_MAP = {c: c for c in CANONICAL_CATEGORIES}

_DIRECT_FUEL_MAP = {
    # Dashboard-feed names (the XLSX uses "Gas"/"Gas-CC", caught by the
    # startswith("gas") rule below - but the dashboard says "Natural Gas",
    # which does NOT start with "gas", and "Coal and Lignite").
    "naturalgas": "natural_gas",
    "coalandlignite": "coal",
    "coal": "coal",
    "nuclear": "nuclear",
    "hydro": "hydro",
    "wind": "wind",
    "wnd": "wind",
    "solar": "solar",
    "biomass": "other_renewables",
    "powerstorage": "storage",
    "battery": "storage",
    "energystorage": "storage",
    "esr": "storage",
    "other": "imports_other",
    "oth": "imports_other",
    "wsl": "imports_other",
}


def _canon_fuel(native: object) -> str:
    """Map a native ERCOT fuel label to one of the 9 canonical buckets,
    case/separator-insensitively. Any "Gas"-prefixed label (Gas, Gas-CC,
    Gas_CC, Gas-CT, ...) is natural_gas. Anything unrecognized falls back
    to imports_other rather than being dropped."""
    key = str(native).strip().lower().replace(" ", "").replace("_", "").replace("-", "")
    if key.startswith("gas"):
        return "natural_gas"
    return _DIRECT_FUEL_MAP.get(key, "imports_other")


def _discover_sources(session) -> tuple[dict[int, str], str | None]:
    """Fetch the live generation page and scrape real download URLs.
    Returns ({year: loose_xlsx_url}, archive_zip_url_or_None)."""
    resp = session.get(_GENERATION_PAGE_URL, timeout=30)
    resp.raise_for_status()
    html = resp.text

    loose_urls: dict[int, str] = {}
    for m in _XLSX_LINK_RE.finditer(html):
        loose_urls[int(m.group(1))] = m.group(0)

    zip_match = _ZIP_LINK_RE.search(html)
    archive_url = zip_match.group(0) if zip_match else None

    return loose_urls, archive_url


def _melt_new_format(df: pd.DataFrame, fuel_col: str) -> pd.DataFrame | None:
    """2017-present layout: Date, Fuel(/"Fuel Type"), Settlement Type, Total,
    <interval columns>. One row per date+fuel already."""
    settlement_col = next((c for c in df.columns if str(c).strip() == "Settlement Type"), None)
    meta_cols = {"Date", fuel_col}
    if settlement_col is not None:
        meta_cols.add(settlement_col)
    interval_cols = [c for c in df.columns if c not in meta_cols and str(c).strip().lower() != "total"]
    if not interval_cols:
        return None

    melted = df.melt(id_vars=["Date", fuel_col], value_vars=interval_cols, var_name="interval", value_name="mw")
    melted["date"] = pd.to_datetime(melted["Date"], errors="coerce").dt.date
    melted["fuel"] = melted[fuel_col].astype(str)
    melted = melted.dropna(subset=["date"])
    if melted.empty:
        return None
    return melted[["date", "fuel", "interval", "mw"]]


def _melt_old_format(df: pd.DataFrame) -> pd.DataFrame | None:
    """2007-2016 layout: a combined "<MM/DD/YY><sep><Fuel>" key column,
    a Total/Daily MWH column (ignored - see module docstring), then interval
    columns. Date is always the first 8 characters of the key."""
    cols = list(df.columns)
    if len(cols) < 3:
        return None
    key_col, interval_cols = cols[0], cols[2:]

    key = df[key_col].astype(str)
    parsed_date = pd.to_datetime(key.str.slice(0, 8), format="%m/%d/%y", errors="coerce")
    if parsed_date.isna().all():
        # Not a real per-date sheet (e.g. a yearly "Fuel Totals" summary
        # sheet) - skip rather than raise.
        return None
    fuel = key.str.slice(8).str.strip(" -_")

    melted = df[interval_cols].copy()
    melted["date"] = parsed_date.dt.date
    melted["fuel"] = fuel
    melted = melted.dropna(subset=["date"])
    if melted.empty:
        return None

    melted = melted.melt(id_vars=["date", "fuel"], value_vars=interval_cols, var_name="interval", value_name="mw")
    return melted[["date", "fuel", "interval", "mw"]]


def _sheet_to_long(df: pd.DataFrame) -> pd.DataFrame | None:
    """Detect which of the two layouts a sheet uses and melt it into
    long-format [date, fuel, interval, mw]. Returns None for non-data
    sheets (Disclaimer, Summary, yearly totals, empty not-yet-published
    months)."""
    if df is None or df.empty:
        return None
    cols = df.columns
    fuel_col = "Fuel" if "Fuel" in cols else ("Fuel Type" if "Fuel Type" in cols else None)
    if "Date" in cols and fuel_col:
        return _melt_new_format(df, fuel_col)
    return _melt_old_format(df)


def _process_workbook(xl: pd.ExcelFile) -> pd.DataFrame:
    """Melt every data sheet in a workbook into one long [date, fuel,
    interval, mw] frame, skipping sheets that aren't per-date fuel data
    (disclaimers, summaries, not-yet-published months) or that fail to
    parse."""
    frames: list[pd.DataFrame] = []
    for sheet_name in xl.sheet_names:
        try:
            df = xl.parse(sheet_name)
        except Exception:
            continue
        melted = _sheet_to_long(df)
        if melted is not None and not melted.empty:
            frames.append(melted)
    if not frames:
        return pd.DataFrame(columns=["date", "fuel", "interval", "mw"])
    return pd.concat(frames, ignore_index=True)


def _daily_from_long(long_df: pd.DataFrame) -> pd.DataFrame:
    """Resolve native fuel names to canonical buckets, then aggregate to
    daily MWh via base.long_to_daily_mwh (mean(MW) * 24).

    Several native fuels can share one canonical bucket (Gas + Gas-CC ->
    natural_gas; Other + WSL -> imports_other). base.long_to_daily_mwh
    groups by (date, fuel_category) and takes a plain mean, so if both
    native fuels' per-interval rows were handed to it directly for the
    same date+category it would average the two fuels together instead of
    summing them - silently halving the combined total (verified against
    ERCOT's own published annual totals while building this connector: Gas
    + Gas-CC undercounted by ~2x until this fix). To avoid that, native
    fuels sharing a category are summed per (date, interval) here first, so
    each date+category reaches long_to_daily_mwh with exactly one row per
    interval - at that point its mean-then-*24 is exactly what's wanted.
    """
    if long_df.empty:
        return pd.DataFrame(columns=["date", "fuel_category", "generation_mwh"])
    working = long_df.copy()
    working["category"] = working["fuel"].map(_canon_fuel)
    # Native interval values are quarter-hour MWh, not instantaneous MW (see
    # module docstring for the cross-check proving this). Converting to an
    # equivalent average MW (energy / 0.25h = energy * 4) lets
    # long_to_daily_mwh's mean(MW) * 24 formula reproduce ERCOT's true daily
    # MWh instead of undercounting by 4x.
    working["mw"] = pd.to_numeric(working["mw"], errors="coerce") * 4
    # sort=False: "interval" labels can be plain strings in some sheets and
    # datetime.time objects in others (see module docstring), which aren't
    # mutually orderable - grouping doesn't need them sorted, only hashable.
    per_interval = working.groupby(["date", "category", "interval"], as_index=False, sort=False)["mw"].sum()
    return long_to_daily_mwh(per_interval, per_interval["date"], "category", "mw", _IDENTITY_CATEGORY_MAP)


def _fetch_dashboard_recent(session) -> pd.DataFrame:
    """Fetch ERCOT's public fuel-mix dashboard JSON (current + previous
    day(s), 5-minute MW telemetry) and aggregate it to daily MWh.

    The feed's shape (observed via ERCOT's own dashboard and open-source
    clients) is {"data": {"YYYY-MM-DD": {"<timestamp>": {"<Fuel>": {"gen":
    MW, ...}, ...}, ...}}}, where cells are occasionally plain numbers
    instead of {"gen": ...} dicts. Parsed defensively: anything
    unrecognized is skipped with a warning and an empty frame is returned,
    so a dashboard format change degrades ERCOT back to XLSX-only freshness
    instead of failing the whole connector.
    """
    empty = pd.DataFrame(columns=["date", "fuel_category", "generation_mwh"])
    try:
        resp = session.get(_DASHBOARD_FUELMIX_URL, timeout=30)
        resp.raise_for_status()
        payload = resp.json()
    except Exception as e:
        print(f"ERCOT: dashboard fuel-mix fetch failed ({e}) -- continuing with XLSX only")
        return empty

    days = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(days, dict):
        print("ERCOT: dashboard fuel-mix JSON shape unrecognized -- continuing with XLSX only")
        return empty

    records = []
    for day_key, intervals in days.items():
        try:
            day = date.fromisoformat(str(day_key)[:10])
        except ValueError:
            continue
        if not isinstance(intervals, dict):
            continue
        for ts, fuels in intervals.items():
            if not isinstance(fuels, dict):
                continue
            for fuel, cell in fuels.items():
                mw = cell.get("gen") if isinstance(cell, dict) else cell
                if isinstance(mw, (int, float)):
                    records.append({"date": day, "interval": ts, "fuel": fuel, "mw": mw})
    if not records:
        print("ERCOT: dashboard fuel-mix JSON contained no readable intervals -- continuing with XLSX only")
        return empty

    df = pd.DataFrame(records)
    df["category"] = df["fuel"].map(_canon_fuel)
    # Several native fuels can share one canonical bucket - sum them per
    # interval before the mean-across-intervals, same as _daily_from_long.
    per_interval = df.groupby(["date", "category", "interval"], as_index=False, sort=False)["mw"].sum()
    return long_to_daily_mwh(per_interval, per_interval["date"], "category", "mw", _IDENTITY_CATEGORY_MAP)


def fetch_range(start_date: date, end_date: date) -> pd.DataFrame:
    """Fetch daily generation-by-fuel-type MWh for ERCOT over [start_date, end_date].

    Discovers real download URLs from the live generation page, then for
    each calendar year touched by the range downloads either the loose
    current/prior-year XLSX or (once) the historical archive zip and
    extracts that year's workbook from it. Years with no available source
    (not yet published, or - for 2007-2015 - missing the optional `xlrd`
    dependency needed to read legacy .xls) are skipped rather than raising.
    Network failures propagate per the connector contract.

    If the requested range reaches within 3 days of today, the public
    fuel-mix dashboard feed is also fetched, and its daily values fill any
    date in range the XLSX hasn't published yet (XLSX rows always win).
    """
    session = http_session()
    loose_urls, archive_url = _discover_sources(session)
    if not loose_urls and archive_url is None:
        raise RuntimeError(
            "Could not find any ERCOT fuel-mix download links on "
            f"{_GENERATION_PAGE_URL}; the page layout may have changed."
        )

    frames: list[pd.DataFrame] = []
    archive_zip: zipfile.ZipFile | None = None
    archive_fetch_attempted = False

    for year in range(start_date.year, end_date.year + 1):
        long_df: pd.DataFrame | None = None

        if year in loose_urls:
            resp = session.get(loose_urls[year], timeout=60)
            resp.raise_for_status()
            xl = pd.ExcelFile(io.BytesIO(resp.content))
            long_df = _process_workbook(xl)
        elif archive_url is not None:
            if archive_zip is None and not archive_fetch_attempted:
                archive_fetch_attempted = True
                resp = session.get(archive_url, timeout=180)
                resp.raise_for_status()
                archive_zip = zipfile.ZipFile(io.BytesIO(resp.content))
            if archive_zip is not None:
                member = next(
                    (n for n in archive_zip.namelist() if f"intgenbyfuel{year}" in n.lower()),
                    None,
                )
                if member is not None:
                    try:
                        xl = pd.ExcelFile(io.BytesIO(archive_zip.read(member)))
                    except ImportError:
                        # Legacy .xls (pre-2016) needs the optional `xlrd`
                        # package; skip this year rather than failing the
                        # whole range.
                        xl = None
                    if xl is not None:
                        long_df = _process_workbook(xl)

        if long_df is not None and not long_df.empty:
            frames.append(long_df)

    if frames:
        combined_long = pd.concat(frames, ignore_index=True)
        daily = _daily_from_long(combined_long)
        if not daily.empty:
            mask = (daily["date"] >= start_date) & (daily["date"] <= end_date)
            daily = daily.loc[mask].reset_index(drop=True)
    else:
        daily = pd.DataFrame(columns=["date", "fuel_category", "generation_mwh"])

    # Top up the XLSX's publication lag from the live dashboard feed, but
    # only for dates the settlement workbook hasn't published yet.
    if end_date >= date.today() - timedelta(days=3):
        dashboard_daily = _fetch_dashboard_recent(session)
        if not dashboard_daily.empty:
            in_range = (dashboard_daily["date"] >= start_date) & (dashboard_daily["date"] <= end_date)
            dashboard_daily = dashboard_daily.loc[in_range]
            already_settled = set(daily["date"]) if not daily.empty else set()
            dashboard_daily = dashboard_daily.loc[~dashboard_daily["date"].isin(already_settled)]
            if not dashboard_daily.empty:
                filled = sorted(dashboard_daily["date"].unique())
                print(f"ERCOT: filled {len(filled)} not-yet-settled day(s) from the live dashboard feed ({filled[0]} .. {filled[-1]})")
                daily = pd.concat([daily, dashboard_daily], ignore_index=True)

    return finalize(daily, ISO)
