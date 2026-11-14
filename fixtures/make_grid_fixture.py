"""Build fixtures/grid.json from the real Houston grid (data/raw/houston_h3_r8.parquet).

Keeps h3, cell_lat, cell_lon, county and only the public Census / CMS columns the packs and the scoring use.
CoStar-derived columns (n_costar_*, n_lease_spaces, n_medical_lease_spaces) are never copied: the repo is public.
Run: PYTHONPATH=. python fixtures/make_grid_fixture.py
"""

import json
import math
import sys
from pathlib import Path

import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "data" / "raw" / "houston_h3_r8.parquet"
GENERAL = ROOT / "data" / "derived" / "general_stock.parquet"  # public ACS + LODES (scripts/build_general_stock.py)
OUT = Path(__file__).parent / "grid.json"

KEYS = ["h3", "GEOID", "cell_lat", "cell_lon", "county"]
# ACS 5-year (Census) and CMS Doctors and Clinicians / Hospital General Information columns only.
COLUMNS = [
    # population and demand bands
    "pop_total", "pop_u18", "pop_18_44", "pop_45_64", "pop_65p", "pop_density_km2", "pop_growth_pct",
    "pop_total_within_5km", "pop_u18_within_5km", "pop_45_64_within_5km", "pop_65p_within_5km",
    # payers (shares of residents) and income
    "share_any_medicare", "share_any_medicaid", "share_any_private", "share_uninsured", "medicaid_u19_within_5km",
    "median_hh_income",
    # language
    "share_spanish", "share_lep",
    # clinician supply (per cell, used as Huff competitors) and ring sums
    "n_orthopedics", "n_primary_care", "n_pediatrics", "n_nephrology", "n_physical_therapy",
    "n_orthopedics_within_10km", "n_primary_care_within_5km", "n_pediatrics_within_5km",
    "n_nephrology_within_10km", "n_physical_therapy_within_5km", "n_hospitals_within_5km",
]
FORBIDDEN_PREFIXES = ("n_costar", "n_lease_spaces", "n_medical_lease_spaces")


def clean(key: str, value: object) -> object:
    """Round to what the map and the math need: coordinates 6 decimals, shares 4, everything else 1."""
    if not isinstance(value, float):
        return value
    if math.isnan(value):
        return None
    if key in ("cell_lat", "cell_lon"):
        return round(value, 6)
    return round(value, 4 if key.startswith("share_") else 1)


def main() -> int:
    if not SOURCE.exists():
        print(f"missing {SOURCE.relative_to(ROOT)}; put the real data in data/raw/ (docs/HANDOFF.md section 6)")
        return 1
    assert not any(c.startswith(FORBIDDEN_PREFIXES) for c in COLUMNS), "CoStar-derived column in the fixture"
    table = pq.read_table(SOURCE, columns=KEYS + COLUMNS)
    rows = [{k: clean(k, v) for k, v in row.items()} for row in table.to_pylist()]
    if GENERAL.exists():
        extra = {r["h3"]: r for r in pq.read_table(GENERAL).to_pylist()}
        for row in rows:
            row.update({k: clean(k, v) for k, v in extra.get(row["h3"], {}).items() if k != "h3"})
    rows.sort(key=lambda r: str(r["h3"]))
    OUT.write_text(json.dumps(rows, separators=(",", ":")) + "\n")
    print(f"wrote {len(rows)} cells x {len(rows[0])} columns to {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
