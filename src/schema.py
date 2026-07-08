"""Canonical schema shared by every connector, the pipeline, and the dashboard export."""

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
