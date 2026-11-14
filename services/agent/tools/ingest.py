"""Generic ingestion: an approved FeatureSpec -> one value per H3 cell (+ a ValidationReport).

Readers: Socrata (server-side county filter), ArcGIS FeatureServer/MapServer layers (Houston envelope, paged),
GeoJSON, CSV/JSON with lat/lon, tract-keyed tables. Every fetch goes through allowlist.safe_request (allowlist,
public DNS, peer check, size cap). Filters come only from filter_column/filter_values; nothing in a spec is ever
evaluated. Aggregation: tract tables join on the cell's tract GEOID; points become per-cell counts, ring counts
(count_within) or distance to the nearest point.
"""

import asyncio
import csv
import hashlib
import io
import json
import logging
import math
import re
import time
from dataclasses import dataclass, field
from itertools import islice
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import h3
import httpx
import numpy as np

from contracts.models import DatasetPreview, FeatureSpec, PreviewRequest, ValidationReport
from services.agent.tools import geomath
from services.agent.tools.allowlist import BlockedURL, safe_request

log = logging.getLogger(__name__)
COUNTIES = ("48201", "48157", "48339")
BBOX = (29.2, -96.3, 30.8, -94.6)  # lat_min, lon_min, lat_max, lon_max around the three counties
MAX_BYTES = 200 * 1024 * 1024
MAX_ROWS = 500_000
MAX_POINTS = 50_000
PAIR_BUDGET = 5_000_000  # cells × points per distance chunk
PREVIEW_BYTES = 2 * 1024 * 1024
HEADERS = {"User-Agent": "Siteline/0.1 (HackPrinceton; public data research)"}
IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
SOCRATA_ID = re.compile(r"/(?:d|resource|dataset/[^/]+)/([a-z0-9]{4}-[a-z0-9]{4})")
LAT_NAMES = ("lat", "latitude", "y", "stop_lat", "_lat")
LON_NAMES = ("lon", "lng", "long", "longitude", "x", "stop_lon", "_lon")
TRACT_HINTS = ("tractfips", "tract_fips", "geoid", "locationid", "tract", "fips", "census_tract")
KM_PER_RING_STEP = 0.83  # ~ H3 res-8 cell spacing; k=6 ≈ 5 km
OVERPASS = "https://overpass-api.de/api/interpreter"
OSM_TAG = re.compile(r"^[a-z_:]{1,30}=[A-Za-z0-9_ ;:-]{1,40}$")
CACHE = Path(__file__).resolve().parents[3] / "data" / "cache" / "osm"
CACHE_TTL_SECONDS = 24 * 3600
CACHE_MAX_BYTES = 20 * 1024 * 1024
_QUERY = re.compile(r'^\[out:json\]\[timeout:50\];\((?:nwr\["[a-z_:]{1,30}"="[A-Za-z0-9_ ;:-]{1,40}"\]\([-0-9.,]+\);){1,6}\);out center tags;$')


def is_canonical_overpass(url: str) -> bool:
    """Only queries in the exact form overpass_url() builds (validated tags, Houston box) are fetched or cached."""
    parsed = httpx.URL(url)
    return parsed.host == "overpass-api.de" and parsed.path == "/api/interpreter" and \
        bool(_QUERY.match(parsed.params.get("data", ""))) and set(parsed.params.keys()) == {"data"}


def overpass_url(tags: list[str]) -> str:
    """GET URL for every OSM node/way/relation with any of the tags inside the Houston box (tags validated)."""
    clean = [t for t in tags if OSM_TAG.match(t)][:6]
    if not clean:
        raise ValueError("no valid OSM tags")
    bbox = f"{BBOX[0]},{BBOX[1]},{BBOX[2]},{BBOX[3]}"
    parts = "".join(f'nwr["{k}"="{v}"]({bbox});' for k, v in (t.split("=", 1) for t in clean))
    return str(httpx.URL(OVERPASS, params={"data": f"[out:json][timeout:50];({parts});out center tags;"}))


async def _overpass_rows(client: httpx.AsyncClient, url: str, max_rows: int = MAX_ROWS) -> Rows:
    """Overpass elements -> rows with _lat/_lon (way/relation centers). Cached; retried with backoff when busy."""
    if not is_canonical_overpass(url):
        raise ValueError("only Siteline-built OpenStreetMap queries are accepted")
    CACHE.mkdir(parents=True, exist_ok=True)
    cached = CACHE / f"{hashlib.sha1(url.encode()).hexdigest()}.json"
    if cached.exists() and time.time() - cached.stat().st_mtime < CACHE_TTL_SECONDS:
        data = json.loads(cached.read_text())
    else:
        delay, data = 2.0, None
        for _ in range(4):
            resp = await safe_request(client, "GET", url, timeout=60, max_bytes=MAX_BYTES)
            log.info("GET overpass status=%s bytes=%s", resp.status_code, len(resp.content))
            if resp.status_code in (429, 502, 503, 504):
                await asyncio.sleep(delay)
                delay *= 2
                continue
            resp.raise_for_status()
            data = resp.json()
            if len(resp.content) <= CACHE_MAX_BYTES:  # atomic write so a crash never leaves a half file
                tmp = cached.with_suffix(".tmp")
                tmp.write_text(json.dumps(data))
                tmp.replace(cached)
            break
        if data is None:
            raise ValueError("OpenStreetMap Overpass is busy; try again in a minute")
    rows = []
    for el in data.get("elements", [])[:max_rows]:
        lat = el.get("lat", (el.get("center") or {}).get("lat"))
        lon = el.get("lon", (el.get("center") or {}).get("lon"))
        if lat is not None and lon is not None:
            rows.append({"_lat": lat, "_lon": lon, "name": (el.get("tags") or {}).get("name", ""),
                         **{k: v for k, v in (el.get("tags") or {}).items() if k in ("shop", "amenity", "leisure", "office")}})
    return Rows(rows, "json", [f"{len(rows)} OpenStreetMap places"])


@dataclass
class Rows:
    rows: list[dict[str, Any]]
    format: str
    notes: list[str] = field(default_factory=list)


def detect_format(url: str, fmt: str | None) -> str:
    """The URL decides when it clearly names a service; otherwise the declared format, then the file extension."""
    path = urlparse(url).path.lower()
    if re.search(r"/(featureserver|mapserver)/\d+/?$", path):
        return "arcgis"
    if SOCRATA_ID.search(url):
        return "socrata"
    if fmt:
        return fmt
    if path.endswith(".geojson"):
        return "geojson"
    return "csv" if path.endswith(".csv") else "json"


def _where_counties(column: str) -> str:
    return " OR ".join(f"starts_with({column}, '{c}')" for c in COUNTIES)


async def _get(client: httpx.AsyncClient, url: str, params: dict[str, Any] | None = None, cap: int = MAX_BYTES) -> httpx.Response:
    full = str(httpx.URL(url, params=params)) if params else url
    resp = await safe_request(client, "GET", full, timeout=60, max_bytes=cap)
    log.info("GET %s status=%s bytes=%s", full[:200], resp.status_code, len(resp.content))
    resp.raise_for_status()
    return resp


def _features_to_rows(features: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for feat in features:
        row = dict(feat.get("properties") or {})
        geom = feat.get("geometry") or {}
        coords = geom.get("coordinates")
        if geom.get("type") == "Point" and coords:
            row["_lon"], row["_lat"] = float(coords[0]), float(coords[1])
        elif coords:  # use the first vertex of lines/polygons as a location
            flat = coords
            while isinstance(flat, list) and flat and isinstance(flat[0], list):
                flat = flat[0]
            if isinstance(flat, list) and len(flat) >= 2:
                row["_lon"], row["_lat"] = float(flat[0]), float(flat[1])
        rows.append(row)
    return rows


async def fetch_rows(url: str, fmt: str, *, tract_key: str | None = None, preview: bool = False) -> Rows:
    if fmt in ("gtfs", "census_api"):
        raise ValueError(f"{fmt} sources need a connector, not the generic reader")
    cap = PREVIEW_BYTES if preview else MAX_BYTES
    async with httpx.AsyncClient(headers=HEADERS, trust_env=False) as client:
        if urlparse(url).hostname == "overpass-api.de":
            return await _overpass_rows(client, url, 1000 if preview else MAX_ROWS)
        if fmt == "socrata":
            m = SOCRATA_ID.search(url)
            if not m:
                raise ValueError("not a Socrata dataset URL")
            endpoint = f"https://{urlparse(url).hostname}/resource/{m.group(1)}.json"
            params: dict[str, Any] = {"$limit": 200 if preview else 50000}
            notes = []
            if tract_key and IDENT.match(tract_key):
                params["$where"] = _where_counties(tract_key)
                notes.append(f"filtered server-side to Houston counties on {tract_key}")
            data = (await _get(client, endpoint, params, cap)).json()
            return Rows(data if isinstance(data, list) else [], "socrata", notes)
        if fmt == "arcgis":
            base = url.rstrip("/")
            rows: list[dict[str, Any]] = []
            offset, page = 0, 200 if preview else 2000
            while True:
                params = {"where": "1=1", "outFields": "*", "f": "geojson", "outSR": 4326, "inSR": 4326,
                          "geometryType": "esriGeometryEnvelope", "spatialRel": "esriSpatialRelIntersects",
                          "geometry": f"{BBOX[1]},{BBOX[0]},{BBOX[3]},{BBOX[2]}",
                          "resultOffset": offset, "resultRecordCount": page}
                data = (await _get(client, f"{base}/query", params, cap)).json()
                batch = _features_to_rows(data.get("features", []))
                rows += batch
                # Servers cap pages at their own maxRecordCount (often 1,000), which can be below `page`, so a short
                # page does not mean the last page: advance by what came back and stop on an empty page.
                if preview or not batch or len(rows) > 200_000:
                    break
                offset += len(batch)
            return Rows(rows, "arcgis", ["limited to the Houston envelope"])
        resp = await _get(client, url, None, cap)
        text = resp.text
        if fmt == "geojson" or text.lstrip().startswith('{"type"') or '"FeatureCollection"' in text[:500]:
            return Rows(_features_to_rows(json.loads(text).get("features", [])), "geojson")
        if fmt == "csv" or not text.lstrip().startswith(("[", "{")):
            reader = csv.DictReader(io.StringIO(text))
            return Rows([dict(r) for r in islice(reader, 1000 if preview else MAX_ROWS)], "csv")
        data = json.loads(text)
        rows = data if isinstance(data, list) else data.get("data", [])
        return Rows(rows[:MAX_ROWS], "json")


def guess_geo(rows: list[dict[str, Any]]) -> tuple[str | None, str | None]:
    """(geo_key, geo_level): "lat,lon" for points, else a tract / county column."""
    if not rows:
        return None, None
    cols = {c.lower(): c for c in rows[0]}
    sample = rows[: min(50, len(rows))]

    def plausible(lat_col: str, lon_col: str) -> bool:
        """Most sampled rows hold real WGS84 coordinates (attribute columns can be zero, projected or empty)."""
        pairs = [(_to_float(r.get(lat_col)), _to_float(r.get(lon_col))) for r in sample]
        good = [1 for la, lo in pairs if la is not None and lo is not None and la != 0 and lo != 0
                and -90 <= la <= 90 and -180 <= lo <= 180]
        return len(good) >= max(1, len(pairs) // 2)

    # Coordinates taken from each feature's geometry (_lat/_lon, requested in WGS84) beat same-named attribute columns.
    lats = [cols[n] for n in ("_lat", *LAT_NAMES) if n in cols]
    lons = [cols[n] for n in ("_lon", *LON_NAMES) if n in cols]
    for lat, lon in zip(lats, lons):
        if plausible(lat, lon):
            return f"{lat},{lon}", "point"
    for hint in TRACT_HINTS:
        for low, col in cols.items():
            if hint in low and all(re.fullmatch(r"\d{11}", str(r.get(col, ""))) for r in sample if r.get(col)):
                return col, "tract"
    for low, col in cols.items():
        if "county" in low and all(re.fullmatch(r"\d{5}", str(r.get(col, ""))) for r in sample if r.get(col)):
            return col, "county"
    return None, None


async def preview(request: PreviewRequest) -> DatasetPreview:
    url = str(request.url)
    fmt = detect_format(url, request.format)
    try:
        first = await fetch_rows(url, fmt, preview=True)
        key, level = guess_geo(first.rows)
        # Re-fetch tract-keyed Socrata data with the county filter so the sample is from Houston.
        if fmt == "socrata" and level == "tract" and key:
            first = await fetch_rows(url, fmt, tract_key=key, preview=True)
    except (BlockedURL, httpx.HTTPError, ValueError, json.JSONDecodeError) as exc:
        return DatasetPreview(url=url, format=fmt, columns=[], ingestible=False, reason=f"could not read: {str(exc)[:200]}")  # type: ignore[arg-type]
    rows = first.rows
    columns = list(rows[0].keys()) if rows else []
    reason = None
    if not rows:
        reason = "no rows in Houston"
    elif key is None:
        reason = "no geography column (lat/lon, tract GEOID or county FIPS) found"
    elif level == "tract" and not any(str(r.get(key, ""))[:5] in COUNTIES for r in rows):
        reason = "no Houston-area tracts in the sample"
    return DatasetPreview(
        url=url, format=fmt, columns=columns[:200], sample_rows=[{k: r.get(k) for k in columns[:25]} for r in rows[:5]],  # type: ignore[arg-type]
        row_count=len(rows), geo_key_guess=key, geo_level_guess=level, ingestible=reason is None, reason=reason,  # type: ignore[arg-type]
    )


def _to_float(value: Any) -> float | None:
    try:
        x = float(str(value).replace(",", ""))
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def _apply_filter(rows: list[dict[str, Any]], spec: FeatureSpec) -> list[dict[str, Any]]:
    if not spec.filter_column or not spec.filter_values:
        return rows
    wanted = {v.strip().lower() for v in spec.filter_values if v.strip()}
    col = spec.filter_column
    exact = [r for r in rows if str(r.get(col, "")).strip().lower() in wanted]
    if exact or not wanted:
        return exact
    # Model-written values often differ in format from the data ("9-12" vs "09-12", "High" vs "High School"):
    # fall back to containment, still only within the chosen category column.
    return [r for r in rows if any(w in str(r.get(col, "")).lower() for w in wanted)]


def ring_k(spec: FeatureSpec) -> int:
    """radius_min is minutes in the contract; straight-line ring steps at the Huff speed (1 min ≈ 0.72 km)."""
    minutes = spec.radius_min or 7.0
    km = minutes * geomath.SPEED_KMH / 60 / geomath.DETOUR
    return max(1, round(km / KM_PER_RING_STEP))


def aggregate(rows: list[dict[str, Any]], spec: FeatureSpec, grid: geomath.Grid) -> tuple[np.ndarray, np.ndarray | None, list[str]]:
    """(cell values, per-cell point counts for Huff or None, notes)."""
    n = len(grid)
    notes: list[str] = []
    if spec.geo_level == "point":
        lat_col, _, lon_col = spec.geo_key.partition(",")
        pts = [(la, lo) for r in rows if (la := _to_float(r.get(lat_col))) is not None
               and (lo := _to_float(r.get(lon_col))) is not None
               and BBOX[0] <= la <= BBOX[2] and BBOX[1] <= lo <= BBOX[3]]
        counts = np.zeros(n)
        for la, lo in pts:
            i = grid.cell_of(la, lo)
            if i is not None:
                counts[i] += 1
        if len(pts) > MAX_POINTS:
            raise ValueError(f"{len(pts)} points is more than the {MAX_POINTS} this build maps")
        notes.append(f"{len(pts)} points in the Houston area, {int(counts.sum())} inside grid cells")
        if spec.agg == "distance_to_nearest":
            if not pts:
                return np.full(n, np.nan), counts, notes
            plat, plon = np.array([p[0] for p in pts]), np.array([p[1] for p in pts])
            out = np.empty(n)
            step = max(1, PAIR_BUDGET // len(pts))  # bound memory: cells × points per chunk
            for start in range(0, n, step):
                sl = slice(start, start + step)
                out[sl] = geomath.pairwise_km(grid.lat[sl], grid.lon[sl], plat, plon).min(axis=1)
            notes.append("distance to the nearest point in km (straight line)")
            return out, counts, notes
        if spec.agg == "density":
            area = np.array([h3.cell_area(c, "km^2") for c in grid.h3])
            return counts / area, counts, notes
        k = ring_k(spec)
        ring = np.array([sum(counts[grid.index[x]] for x in h3.grid_disk(c, k) if x in grid.index) for c in grid.h3])
        notes.append(f"points within about {k * KM_PER_RING_STEP:.0f} km")
        return ring, counts, notes

    if grid.tracts is None:
        raise ValueError("this grid has no tract ids; tract tables cannot be joined")
    by_key: dict[str, list[float]] = {}
    for r in rows:
        key, val = str(r.get(spec.geo_key, "")).strip(), _to_float(r.get(spec.value_column or ""))
        if val is None or not key:
            continue
        if spec.geo_level == "county":
            key = key[-5:]
        by_key.setdefault(key, []).append(val)
    tract_value = {k: (sum(v) if spec.agg == "sum" else sum(v) / len(v)) for k, v in by_key.items()}
    out = np.full(n, np.nan)
    if spec.geo_level == "tract":
        cells_per_tract: dict[str, int] = {}
        for t in grid.tracts:
            cells_per_tract[t] = cells_per_tract.get(t, 0) + 1
        for i, t in enumerate(grid.tracts):
            if t in tract_value:  # rates carry over; counts split evenly across the tract's cells
                out[i] = tract_value[t] / cells_per_tract[t] if spec.agg == "sum" else tract_value[t]
    else:
        for i, t in enumerate(grid.tracts):
            if t[:5] in tract_value:
                out[i] = tract_value[t[:5]]
    notes.append(f"{len(tract_value)} {spec.geo_level} values joined")
    return out, None, notes


def validate(values: np.ndarray, spec: FeatureSpec, rows_read: int, notes: list[str], point_count: float | None) -> ValidationReport:
    ok_vals = values[~np.isnan(values)]
    coverage = float(len(ok_vals) / len(values)) if len(values) else 0.0
    ok = (point_count or 0) >= 5 if spec.geo_level == "point" else coverage >= 0.8
    if spec.id.startswith("partner_") and spec.agg == "distance_to_nearest":  # named partners (services/agent/partners.py):
        ok = (point_count or 0) >= len(spec.filter_values) and coverage >= 0.8  # every named place found, all cells
    if not ok:
        notes.append("too few points inside Houston" if spec.geo_level == "point" else "covers less than 80% of cells")
    return ValidationReport(
        feature_id=spec.id, ok=ok, coverage_pct=round(coverage, 4), null_pct=round(1 - coverage, 4),
        min=float(ok_vals.min()) if len(ok_vals) else None, max=float(ok_vals.max()) if len(ok_vals) else None,
        mean=float(ok_vals.mean()) if len(ok_vals) else None, rows_read=rows_read, notes=notes,
    )


async def ingest(spec: FeatureSpec, grid: geomath.Grid) -> tuple[ValidationReport, np.ndarray | None, np.ndarray | None]:
    """(report, cell values, per-cell point counts). Values are None when the dataset could not be read."""
    if spec.source_url is None:
        return ValidationReport(feature_id=spec.id, ok=False, coverage_pct=0, null_pct=1, notes=["no source_url"]), None, None
    url = str(spec.source_url)
    fmt = detect_format(url, spec.format)
    try:
        fetched = await fetch_rows(url, fmt, tract_key=spec.geo_key if spec.geo_level == "tract" else None)
        rows = _apply_filter(fetched.rows, spec)
        values, counts, notes = aggregate(rows, spec, grid)
    except (BlockedURL, httpx.HTTPError, ValueError, json.JSONDecodeError) as exc:
        return ValidationReport(feature_id=spec.id, ok=False, coverage_pct=0, null_pct=1,
                                notes=[f"could not ingest: {str(exc)[:200]}"]), None, None
    report = validate(values, spec, len(rows), fetched.notes + notes, None if counts is None else float(counts.sum()))
    return report, values, counts
