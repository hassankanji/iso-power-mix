"""Canonical schema shared by every connector, the pipeline, and the dashboard export.

STORAGE IS DISCHARGE ONLY. Every bucket here is gross generation, and
`storage` is no exception: it is energy sent OUT of batteries and pumped
hydro. Charging is load, not generation, and is excluded.

This is not a stylistic choice - it is the only definition all our sources
can express. Measured against EIA-930 on 2026-08-07 (scripts/
diagnose_storage.py):

  net (charging shows as negative hours)  ERCOT, MISO, CAISO's own feed,
                                          EIA's ERCO and MISO respondents
  discharge only (never negative)         PJM, MISO's own feed, EIA's SWPP
                                          and ISNE respondents
  no storage series at all                SPP, NYISO, ISO-NE's own feeds,
                                          EIA's CISO/PJM/NYIS respondents

A net series can always be reduced to discharge by dropping the charging
intervals; a discharge-only series can never be turned back into a net one.
So discharge is the common denominator, and it is the one that makes a
stacked generation mix add up - mixing the two conventions is what made
EIA's US48 storage read as a large positive while the ISOs sat at or below
zero. Clipping happens at the source's native interval, never on a daily
total (see src/connectors/base.py and src/eia.py).
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
    "storage",
    "imports_other",
]

ISO_CODES = ["CAISO", "PJM", "ERCOT", "MISO", "SPP", "NYISO", "ISONE"]

GENERATION_COLUMNS = ["date", "iso", "fuel_category", "generation_mwh"]
