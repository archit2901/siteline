"""Build general-business stock features for Houston cells from official open data.

  PYTHONPATH=. python scripts/build_general_stock.py

Sources (key-free, cached in data/cache/):
- ACS 2024 5-year table-based summary files (www2.census.gov): B25003 tenure, B25024 units in structure,
  B08201 vehicles available, B11001 households.
- LEHD LODES8 workplace area characteristics (lehd.ces.census.gov), Texas, 2022: jobs by census block.
Tract values go to H3 cells like the existing grid: counts split evenly across a tract's cells (tracts with no cell
centroid go to the cell nearest the tract centroid), shares carried over. Ring sums use the grid's k=6 (≈5 km)
rings. Writes data/derived/general_stock.parquet (cells × new columns).
"""

import csv
import gzip
import json
import logging
import math
from collections import defaultdict
from pathlib import Path

import h3
import httpx
import pyarrow as pa
import pyarrow.parquet as pq

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("build_general_stock")

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
CACHE = ROOT / "data" / "cache"
OUT = ROOT / "data" / "derived" / "general_stock.parquet"
ACS = "https://www2.census.gov/programs-surveys/acs/summary_file/2024/table-based-SF/data/5YRData/acsdt5y2024-{t}.dat"
LODES = "https://lehd.ces.census.gov/data/lodes/LODES8/tx/wac/tx_wac_S000_JT00_2022.csv.gz"
COUNTIES = ("48201", "48157", "48339")  # Harris, Fort Bend, Montgomery
MAX_BYTES = 200 * 1024 * 1024
RING_K = 6
PROVENANCE = {
    "acs": "https://www.census.gov/programs-surveys/acs/data/summary-file.html",
    "lodes": "https://lehd.ces.census.gov/data/",
}


def download(url: str, dest: Path) -> Path:
    if dest.exists():
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    size = 0
    with httpx.stream("GET", url, timeout=60, follow_redirects=True, headers={"User-Agent": "Siteline/0.1"}) as resp:
        resp.raise_for_status()
        with dest.with_suffix(".part").open("wb") as f:
            for chunk in resp.iter_bytes():
                size += len(chunk)
                if size > MAX_BYTES:
                    raise RuntimeError(f"{url} exceeds 200 MB")
                f.write(chunk)
    dest.with_suffix(".part").rename(dest)
    log.info("GET %s status=%s bytes=%s", url, resp.status_code, size)
    return dest


def acs_table(table: str) -> dict[str, dict[str, float]]:
    """Houston tract rows of one ACS table: GEOID (11 digits) -> {column: value}."""
    path = download(ACS.format(t=table), CACHE / "acs" / f"acsdt5y2024-{table}.dat")
    rows: dict[str, dict[str, float]] = {}
    with path.open(newline="") as f:
        for row in csv.DictReader(f, delimiter="|"):
            geo = row["GEO_ID"]
            if not geo.startswith("1400000US") or not geo[9:14] in COUNTIES:
                continue
            rows[geo[9:]] = {k: float(v) for k, v in row.items() if "_E" in k and v not in ("", "null")}
    log.info("%s: %s Houston tracts", table, len(rows))
    return rows


def lodes_jobs() -> dict[str, float]:
    """Jobs by tract (all jobs, workplace geography)."""
    path = download(LODES, CACHE / "lodes" / "tx_wac_S000_JT00_2022.csv.gz")
    jobs: dict[str, float] = defaultdict(float)
    with gzip.open(path, "rt", newline="") as f:
        for row in csv.DictReader(f):
            block = row["w_geocode"]
            if block[:5] in COUNTIES:
                jobs[block[:11]] += float(row["C000"])
    log.info("LODES: %s Houston tracts, %s jobs", len(jobs), int(sum(jobs.values())))
    return jobs


def tract_centroids() -> dict[str, tuple[float, float]]:
    """Mean of polygon vertices per tract (good enough to place tracts that contain no cell centroid)."""
    data = json.loads((RAW / "houston_tracts.geojson").read_text())
    out = {}
    for feat in data["features"]:
        geom = feat["geometry"]
        rings = geom["coordinates"] if geom["type"] == "Polygon" else [r for p in geom["coordinates"] for r in p]
        pts = [pt for ring in rings for pt in ring]
        out[feat["properties"]["GEOID"]] = (sum(p[1] for p in pts) / len(pts), sum(p[0] for p in pts) / len(pts))
    return out


def main() -> None:
    grid = pq.read_table(RAW / "houston_h3_r8.parquet", columns=["h3", "GEOID", "cell_lat", "cell_lon"]).to_pylist()
    cells = [r["h3"] for r in grid]
    index = {c: i for i, c in enumerate(cells)}
    by_tract: dict[str, list[int]] = defaultdict(list)
    for i, r in enumerate(grid):
        by_tract[r["GEOID"]].append(i)

    tenure, units, vehicles, households = (acs_table(t) for t in ("b25003", "b25024", "b08201", "b11001"))
    jobs = lodes_jobs()
    centroids = tract_centroids()

    # Tracts without a cell centroid: attach to the nearest cell so no households or jobs are lost.
    tracts = set(tenure) | set(jobs)
    for geoid in sorted(tracts - set(by_tract)):
        if geoid in centroids:
            lat, lon = centroids[geoid]
            nearest = min(range(len(grid)), key=lambda i: (grid[i]["cell_lat"] - lat) ** 2 + (grid[i]["cell_lon"] - lon) ** 2)
            by_tract[geoid].append(nearest)

    n = len(cells)
    counts = {k: [0.0] * n for k in ("households", "renter_households", "no_vehicle_households", "jobs")}
    shares = {k: [math.nan] * n for k in ("share_renter", "share_multifamily", "share_no_vehicle")}
    for geoid, idx in by_tract.items():
        t, u, v, hh = tenure.get(geoid, {}), units.get(geoid, {}), vehicles.get(geoid, {}), households.get(geoid, {})
        occupied, renters = t.get("B25003_E001", 0.0), t.get("B25003_E003", 0.0)
        total_units = u.get("B25024_E001", 0.0)
        multi = sum(u.get(f"B25024_E{k:03d}", 0.0) for k in (6, 7, 8, 9))  # 5+ units in the structure
        veh_total, no_vehicle = v.get("B08201_E001", 0.0), v.get("B08201_E002", 0.0)
        tract_counts = {"households": hh.get("B11001_E001", 0.0), "renter_households": renters,
                        "no_vehicle_households": no_vehicle, "jobs": jobs.get(geoid, 0.0)}
        tract_shares = {"share_renter": renters / occupied if occupied else math.nan,
                        "share_multifamily": multi / total_units if total_units else math.nan,
                        "share_no_vehicle": no_vehicle / veh_total if veh_total else math.nan}
        home = [i for i in idx if grid[i]["GEOID"] == geoid] or idx
        for i in idx:  # counts split evenly; shares only on the tract's own cells
            for k, val in tract_counts.items():
                counts[k][i] += val / len(idx)
        for i in home:
            for k, val in tract_shares.items():
                shares[k][i] = val

    columns: dict[str, list[float]] = {**counts, **shares}
    for k in ("households", "renter_households", "no_vehicle_households", "jobs"):
        ring = []
        for c in cells:
            ring.append(sum(counts[k][index[x]] for x in h3.grid_disk(c, RING_K) if x in index))
        columns[f"{k}_within_5km"] = ring
    area = [h3.cell_area(c, "km^2") for c in cells]
    columns["jobs_density_km2"] = [j / a for j, a in zip(counts["jobs"], area)]

    OUT.parent.mkdir(parents=True, exist_ok=True)
    table = pa.table({"h3": cells, **{k: pa.array(v, pa.float64()) for k, v in columns.items()}})
    pq.write_table(table, OUT)
    totals = {k: round(sum(counts[k])) for k in counts}
    log.info("wrote %s: %s cells x %s columns; totals %s", OUT.relative_to(ROOT), n, len(columns), totals)


if __name__ == "__main__":
    main()
