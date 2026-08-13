"""Canonical schema shared by every connector, the pipeline, and the dashboard export.

THE BATTERY BUCKET IS BATTERY DISCHARGE ONLY.

Two rules, each of which took a wrong number on the dashboard to find:

1. DISCHARGE, NOT NET. Every bucket here is gross generation, and `battery`
   is no exception: it is energy sent OUT of the fleet. Charging is load, not
   generation, and is excluded. This is not a stylistic choice - it is the
   only definition all our sources can express. Measured against EIA-930
   (scripts/diagnose_storage.py):

     net (charging shows as negative hours)  ERCOT, MISO, CAISO's own feed,
                                             EIA's ERCO and MISO respondents
     discharge only (never negative)         PJM, MISO's own feed, EIA's SWPP
                                             and ISNE respondents
     no series at all                        SPP, NYISO, ISO-NE's own feeds,
                                             EIA's CISO, PJM and NYIS

   A net series can always be reduced to discharge by dropping the charging
   intervals; a discharge-only series can never be turned back into a net
   one. So discharge is the common denominator, and it is the one that makes
   a stacked generation mix add up. Clipping happens at the source's native
   interval, NEVER on a daily total (see src/connectors/base.py and
   src/eia.py) - flooring a day's net at zero reports a day of heavy cycling
   as zero.

   The same rule has a second edge that is easy to miss: clipping an
   ALREADY-SUMMED series is not the same as summing clipped ones. An hour
   where CAISO discharges 4 GW while the rest of the country charges 5 GW
   nets to -1 GW and books zero, deleting CAISO's 4 GWh. Measured on
   2026-08-13, clipping EIA's pre-summed US48 series lost 21% of national
   discharge against summing each balancing authority's own clipped figure
   (79.0 vs 100.0 GWh/day). So the national series is built per-BA and then
   added, never clipped in aggregate.

2. BATTERIES, NOT PUMPED HYDRO. Every ISO feed we read folds pumped storage
   into its hydro column. EIA has a separate PS code, and it is not small -
   43% of EIA's storage-coded discharge on the measurement above. Filing the
   same asset under `battery` when EIA answers and under `hydro` when the ISO
   answers made the two incomparable, so EIA's PS is mapped to hydro (clipped
   hourly first, since pumping is load) and `battery` means batteries.

WHAT THE NATIONAL BATTERY LINE IS NOT. CAISO, PJM and NYISO report no
battery series to EIA-930 at all, so EIA's national figure omits the largest
battery fleet in the country. It is a floor, not a total, and the dashboard
says so wherever it is drawn. This is a reporting gap at the source, not
something we can net out.
"""

# The 9 normalized fuel buckets every ISO's native categories must map into.
CANONICAL_CATEGORIES = [
    "natural_gas",
    "coal",
    "nuclear",
    "hydro",
    "wind",
    "solar",
    "other_renewables",
    "battery",
    "imports_other",
]

# Committed exports written before the bucket was renamed carry the old name.
# db.py applies this when rebuilding from them and export.py rewrites every
# year file, so a single export run migrates the whole dataset - but the
# alias stays, because a checkout of an older commit must still load.
LEGACY_CATEGORY_ALIASES = {"storage": "battery"}


def canonical_category(name: str) -> str:
    return LEGACY_CATEGORY_ALIASES.get(name, name)


ISO_CODES = ["CAISO", "PJM", "ERCOT", "MISO", "SPP", "NYISO", "ISONE"]

GENERATION_COLUMNS = ["date", "iso", "fuel_category", "generation_mwh"]
