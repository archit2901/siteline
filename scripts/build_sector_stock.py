"""Build sector stock features (nonprofit / social services, childcare) for Houston cells from official open data.

  PYTHONPATH=. python scripts/build_sector_stock.py

Sources (key-free, cached in data/cache/):
- ACS 2024 5-year table-based summary files (www2.census.gov):
    C17002 ratio of income to poverty level (people below 100%, 185%, 200% of the poverty line),
    B17020 poverty status by age (children and seniors below poverty),
    B22003 households that received SNAP in the past 12 months,
    B01001 sex by age (children under 5, for childcare).
- USDA ERS Food Access Research Atlas, SNAP-Authorized Retailer Access Map 2025 (2020 tracts, 2025 SNAP retailers,
  2020-24 ACS; gisportal.ers.usda.gov FARA_2025_StraightLine): low-income/low-access tract flags at 1/2 mile and
  1 mile, and the low-vehicle-access flag. (CDC PLACES social-needs measures such as food insecurity would be better,
  but Texas did not field that BRFSS module, so PLACES has none for Houston tracts.)
- HRSA Health Center Service Delivery and Look-Alike Sites (data.hrsa.gov, refreshed daily): active, permanent
  FQHC / Look-Alike clinic sites (not administrative-only, mobile vans or school-based sites, which are counted
  separately) -> n_health_centers and n_school_health_centers per cell.
- ACS uninsured residents (already on the grid as ins_uninsured) -> uninsured_within_5km.
Counts are split evenly across a tract's cells (tracts with no cell centroid go to the nearest cell), shares and
rates carry over, and *_within_5km are k=6 ring sums, all exactly like scripts/build_general_stock.py.
"pop_low_food_access" = residents of cells whose tract USDA flags low-income AND low access (1 mile) to a SNAP retailer.
Writes data/derived/sector_stock.parquet (cells x new columns).
"""

import logging
import math
from collections import defaultdict
from pathlib import Path

import h3
import httpx
import pyarrow as pa
import pyarrow.parquet as pq

from scripts.build_general_stock import RAW, RING_K, acs_table, tract_centroids

log = logging.getLogger("build_sector_stock")
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "derived" / "sector_stock.parquet"
FARA = "https://gisportal.ers.usda.gov/server/rest/services/FARA/FARA_2025_StraightLine/MapServer/4/query"
FARA_FLAGS = {  # USDA flag -> our 0/1 column
    "SD_SRAM_LILATracts_halfAnd10": "lila_half_mile",
    "SD_SRAM_LILATracts_1And10": "lila_1_mile",
    "SD_SRAM_HUNVFlag": "low_vehicle_access",
}
HRSA = "https://data.hrsa.gov/DataDownload/DD_Files/Health_Center_Service_Delivery_and_LookAlike_Sites.csv"
COUNTIES = ("48201", "48157", "48339")
COUNTS = ("poverty_pop", "below_185fpl_pop", "below_200fpl_pop", "children_in_poverty", "seniors_in_poverty",
          "snap_households", "children_under_5", "pop_low_food_access")


def food_access_flags() -> dict[str, dict[str, float]]:
    """Houston 2020 tracts -> USDA low-income/low-access flags (one ArcGIS query; 1,381 rows < the 2,000 page cap)."""
    where = " OR ".join(f"CensusTract20 LIKE '{c}%'" for c in ("48201", "48157", "48339"))
    resp = httpx.get(FARA, timeout=60, headers={"User-Agent": "Siteline/0.1"}, params={
        "where": where, "outFields": ",".join(["CensusTract20", *FARA_FLAGS]), "returnGeometry": "false",
        "resultRecordCount": 2000, "f": "json"})
    resp.raise_for_status()
    out = {str(f["attributes"]["CensusTract20"]): {ours: float(f["attributes"].get(theirs) or 0)
                                                   for theirs, ours in FARA_FLAGS.items()}
           for f in resp.json().get("features", [])}
    log.info("USDA food access: %s Houston tracts, %s low-income/low-access at 1 mile", len(out),
             sum(v["lila_1_mile"] for v in out.values()))
    return out


def health_center_sites() -> list[dict[str, str]]:
    """Active HRSA health center sites in the Houston counties (CSV cached in data/cache/)."""
    import csv
    import io

    cache = ROOT / "data" / "cache" / "hrsa_health_center_sites.csv"
    if not cache.is_file():
        cache.parent.mkdir(parents=True, exist_ok=True)
        resp = httpx.get(HRSA, timeout=120, headers={"User-Agent": "Siteline/0.1"}, follow_redirects=True)
        resp.raise_for_status()
        cache.write_bytes(resp.content)
    rows = csv.DictReader(io.StringIO(cache.read_text(encoding="utf-8-sig")))
    return [r for r in rows if r["State and County Federal Information Processing Standard Code"] in COUNTIES
            and r["Site Status Description"] == "Active"]


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    grid = pq.read_table(RAW / "houston_h3_r8.parquet",
                         columns=["h3", "GEOID", "cell_lat", "cell_lon", "pop_total", "ins_uninsured"]).to_pylist()
    cells = [r["h3"] for r in grid]
    index = {c: i for i, c in enumerate(cells)}
    by_tract: dict[str, list[int]] = defaultdict(list)
    for i, r in enumerate(grid):
        by_tract[r["GEOID"]].append(i)

    ratio, by_age, snap, sex_age = (acs_table(t) for t in ("c17002", "b17020", "b22003", "b01001"))
    places = food_access_flags()
    centroids = tract_centroids()
    for geoid in sorted((set(ratio) | set(snap)) - set(by_tract)):  # tracts without a cell centroid
        if geoid in centroids:
            lat, lon = centroids[geoid]
            nearest = min(range(len(grid)), key=lambda i: (grid[i]["cell_lat"] - lat) ** 2 + (grid[i]["cell_lon"] - lon) ** 2)
            by_tract[geoid].append(nearest)

    n = len(cells)
    counts = {k: [0.0] * n for k in COUNTS if k != "pop_low_food_access"}
    rates = {k: [math.nan] * n for k in ("share_below_poverty", "share_below_185fpl", "share_snap", *FARA_FLAGS.values())}
    for geoid, idx in by_tract.items():
        r, a, s, sa, p = ratio.get(geoid, {}), by_age.get(geoid, {}), snap.get(geoid, {}), sex_age.get(geoid, {}), places.get(geoid, {})
        universe = r.get("C17002_E001", 0.0)
        below100 = r.get("C17002_E002", 0.0) + r.get("C17002_E003", 0.0)
        below185 = below100 + sum(r.get(f"C17002_E00{k}", 0.0) for k in (4, 5, 6))
        below200 = below185 + r.get("C17002_E007", 0.0)
        hh, snap_hh = s.get("B22003_E001", 0.0), s.get("B22003_E002", 0.0)
        tract_counts = {
            "poverty_pop": below100, "below_185fpl_pop": below185, "below_200fpl_pop": below200,
            "children_in_poverty": sum(a.get(f"B17020_E00{k}", 0.0) for k in (3, 4, 5)),
            "seniors_in_poverty": sum(a.get(f"B17020_E00{k}", 0.0) for k in (7, 8, 9)),
            "snap_households": snap_hh,
            "children_under_5": sa.get("B01001_E003", 0.0) + sa.get("B01001_E027", 0.0),
        }
        tract_rates = {"share_below_poverty": below100 / universe if universe else math.nan,
                       "share_below_185fpl": below185 / universe if universe else math.nan,
                       "share_snap": snap_hh / hh if hh else math.nan, **p}
        home = [i for i in idx if grid[i]["GEOID"] == geoid] or idx
        for i in idx:
            for k, v in tract_counts.items():
                counts[k][i] += v / len(idx)
        for i in home:
            for k, v in tract_rates.items():
                rates[k][i] = v

    # Residents of low-income, low-access (1 mile) tracts.
    counts["pop_low_food_access"] = [(g.get("pop_total") or 0.0) * (flag if not math.isnan(flag) else 0.0)
                                     for g, flag in zip(grid, rates["lila_1_mile"])]
    # HRSA health centers: clinic sites vs. school-based sites, by the cell they sit in.
    for k in ("n_health_centers", "n_school_health_centers"):
        counts[k] = [0.0] * n
    sites = health_center_sites()
    for r in sites:
        if r["Health Center Type Description"] == "Administrative" or r["Health Center Location Type Description"] == "Mobile Van":
            continue
        try:
            cell = h3.latlng_to_cell(float(r["Geocoding Artifact Address Primary Y Coordinate"]),
                                     float(r["Geocoding Artifact Address Primary X Coordinate"]), 8)
        except ValueError:
            continue
        if cell in index:
            school = r["Health Center Service Delivery Site Location Setting Description"] == "School"
            counts["n_school_health_centers" if school else "n_health_centers"][index[cell]] += 1
    log.info("HRSA: %s active sites; %s clinic and %s school-based sites on the grid", len(sites),
             round(sum(counts["n_health_centers"])), round(sum(counts["n_school_health_centers"])))
    counts["uninsured"] = [float(g.get("ins_uninsured") or 0.0) for g in grid]

    columns: dict[str, list[float]] = {**counts, **rates}
    for k in (*COUNTS, "n_health_centers", "n_school_health_centers", "uninsured"):
        columns[f"{k}_within_5km"] = [sum(counts[k][index[x]] for x in h3.grid_disk(c, RING_K) if x in index) for c in cells]

    OUT.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.table({"h3": cells, **{k: pa.array(v, pa.float64()) for k, v in columns.items()}}), OUT)
    log.info("wrote %s: %s cells x %s columns; totals %s", OUT.relative_to(ROOT), n, len(columns),
             {k: round(sum(counts[k])) for k in counts})


if __name__ == "__main__":
    main()
