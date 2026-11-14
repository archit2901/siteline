# services/geo — geo-service (owner: Parth)

FastAPI service on :8001. Turns public data into one value per H3 cell and computes **every number** Siteline shows:
zone scores, demand, Huff net-new patients, sensitivity, and validation reports for new datasets.

## Layout
```
services/geo/
  app.py            FastAPI endpoints (below)
  regions.py        region configs: hou, jax
  build_region.py   CLI: python -m services.geo.build_region hou  -> data/regions/hou.duckdb
  grid.py           H3 res-8 grid for a region
  crosswalk.py      tract / ZCTA / county -> cells
  store.py          DuckDB access; loads feature matrix into numpy at startup
  stock.py          builds stock features from connectors (census, cms)
  score.py          normalize, zone score, demand
  huff.py           travel time, Huff capture, net-new
  sensitivity.py    Dirichlet weight draws, stability
  rank.py           site quality, hard constraints, final score; POST /rank
  ingest/           profile.py, readers.py, aggregate.py, validate.py   (generic automatic ingestion)
  fixture.py        FIXTURE_MODE responses built from fixtures/fake_grid.json
  tests/
  requirements.txt, Dockerfile
```
Run: `make run-geo`. Tests: `PYTHONPATH=. pytest -q services/geo`.
Dependencies: fastapi, uvicorn[standard], httpx, pydantic>=2, duckdb, h3 (v4 API), geopandas, shapely, pyproj,
pandas, pyarrow, numpy, pyyaml.

## Regions
- `hou`: Texas (48) counties Harris 201, Fort Bend 157, Montgomery 339.
- `jax` (P3, Peri stretch): Florida (12) counties Duval 031, St. Johns 109, Clay 019. Same pipeline, no CoStar.
Region build is offline and cached; the API only reads `data/regions/<id>.duckdb`.

## Grid and crosswalks
- Polygons: TIGER/Line 2020 tracts for the state (`tl_2020_<state>_tract.zip`), filtered to the region counties.
- Cells: H3 res 8 covering the union of tract polygons (`h3.geo_to_cells(geom.__geo_interface__, 8)` in h3 v4).
  Store `h3, lat, lon` (cell centroid), `county_fips`, `tract_geoid` (tract containing the centroid).
- Tract → cell: by centroid. Densities and rates carry over; counts are split evenly across the tract's cells.
- ZIP → cell: ZIP Code Tabulation Area centroids from the Census Gazetteer ZCTA file (small, has lat/lon); assign
  each cell its nearest ZCTA centroid. County → cell by FIPS.

## DuckDB schema (data/regions/<id>.duckdb)
- `cells(h3 TEXT PRIMARY KEY, lat DOUBLE, lon DOUBLE, county_fips TEXT, tract_geoid TEXT, zcta TEXT)`
- `features(feature_id TEXT, h3 TEXT, value DOUBLE)`  long format
- `feature_meta(feature_id, label, source, unit, layer, provenance_url, default_direction)`
- `points(feature_id TEXT, lat DOUBLE, lon DOUBLE, name TEXT, category TEXT, attractiveness DOUBLE)`

## Stock features (ids are part of the contract with packs/ and the agent — keep them stable)
| id | Source | Notes |
|---|---|---|
| pop_total, pop_0_17, pop_18_44, pop_45_64, pop_65plus | ACS 5-year B01001 (sum male + female age groups) | counts; also store `<id>_density` per km² |
| ins_medicare_share, ins_medicaid_share, ins_private_share, ins_uninsured_share | ACS 5-year B27010 | shares; keep the per-age-band counts too for demand π |
| income_median | ACS 5-year B19013 | tract value carried to cells |
| pop_growth | ACS 5-year latest vs 5 years earlier | percent change; tracts that changed boundaries get null |
| clinicians by specialty (points) | CMS Doctors and Clinicians file (`connectors/cms.py`) | stored in `points` with category = primary specialty; packs turn them into `count_within` / `distance_to_nearest` features |
ACS: use the latest 5-year vintage on api.census.gov, `get=group(B01001)&for=tract:*&in=state:48&in=county:201`
(check the API examples if the geography syntax errors). Map variables using the API's variables.json labels.
CMS: the national file is large — filter to the state while reading (chunks) or use a state-filtered API query;
geocode unique addresses with the Census batch geocoder (up to 10,000 per batch); fall back to ZCTA centroid.

## Packs → features
`packs/*.yaml` are StockPacks (contracts). A PackFeature's `value` is one stock column or a sum written with `+`
(e.g. `pop_45_64+pop_65plus`) — support only `+`, never evaluate expressions. A specialty pack extends
`healthcare.base`; features with the same id override the base.

## Scoring math (exactly as docs/DESIGN.md)
- Normalize: percentile rank across region cells; direction −1 uses `1 − x̃`. `S_i = Σ w_k c_ik`, in [0, 1].
- Demand: `D_i = π_i · Σ_a P_ia · r_a` with bands and visit rates from the pack (r_a = 1.0 if missing).
  π_i = share of residents in the demand bands whose coverage type the practice accepts (from B27010 by age).
- Travel minutes: haversine km × 1.3 / (35 mph = 56.33 km/h) × 60, floored at 2.
- Huff: β from ScoringSpec (default 2), attractiveness 1 if unknown; competitors from `competitor_feature`
  points within 30 minutes; the practice's existing offices are both in C (before) and in O (after).
- Net-new: as DESIGN.md; return captured, cannibalized and net-new per site.
- Site quality Q, hard-constraint exclusion and final score F: exactly as docs/DESIGN.md, in `rank.py`.
- Sensitivity: `numpy.random.default_rng(42)`, 200 draws of Dirichlet(κ·w); recompute S and F per draw;
  stability = share of draws in which the site is in the top 10.
- Vectorize with numpy. `/score` should return in under 1 s for ~12,700 cells.

## Automatic ingestion (P2)
`POST /features/profile` (PreviewRequest → DatasetPreview): fetch a small sample only.
- CSV / JSON: first ~2 MB. Socrata: `?$limit=1000`. ArcGIS FeatureServer layer:
  `query?where=1%3D1&outFields=*&resultRecordCount=1000&f=geojson`. GeoJSON: first features.
- Guess the geography key: lat/lon column names (lat, latitude, y / lon, lng, longitude, x), GeoJSON Point geometry,
  11-digit tract GEOID, 5-digit ZIP, 5-digit county FIPS. Set `ingestible=false` with a reason otherwise
  (PDF, Excel with merged headers, no geography, wrong region).
`POST /features/ingest` (IngestRequest → ValidationReport): full fetch (200 MB cap, 60 s timeout, domain allowlist
from docs/DESIGN.md), apply `filter_column/filter_values`, aggregate by `agg`:
- tabular tract/zip/county: join via crosswalk; `mean` / `sum` / `density` of `value_column`.
- points: `count_within` (points within `radius_min` travel minutes of the cell centroid), `distance_to_nearest`
  (minutes), `density` (points per km²).
Validation: `coverage_pct` = non-null cells / all cells; `ok` if ≥ 0.8. For point features, `ok` if at least 5 points
fall inside the region. Store the feature + meta on success. **Never eval or exec any string from a spec.**

## Endpoints (all request/response shapes from contracts.models)
| Endpoint | In → Out |
|---|---|
| GET /health | Health |
| GET /regions/{id}/grid | GeoJSON FeatureCollection of cells (properties: h3) |
| GET /regions/{id}/features | list[RegionFeature] |
| POST /score | ScoreRequest → ScoreResponse |
| POST /huff | HuffRequest → list[HuffResult] |
| POST /sensitivity | SensitivityRequest → list[SensitivityResult] |
| POST /rank | RankRequest → list[RankedSite] (the agent's main call; sorted by final_score) |
| POST /features/profile | PreviewRequest → DatasetPreview |
| POST /features/ingest | IngestRequest → ValidationReport |
| GET /features/{id}/value?h3=... | FeatureValue (used by verify_claims) |
With `FIXTURE_MODE=1` every endpoint answers from `fixtures/fake_grid.json` with the same shapes.

## Tests (write them as you go)
normalize is a percentile in [0, 1]; flipping a direction flips the ranking; a cell with more demand-band
population scores higher on demand; adding a competitor next to a site lowers its net-new; a candidate placed on an
existing office has near-zero net-new; stability is in [0, 1]; ingest of a small local CSV with lat/lon produces a
point feature; ingest rejects a URL outside the allowlist.

## Milestones
- **Setup:** `fixtures/make_fake_grid.py` + `fixtures/fake_grid.json` pushed within 30 minutes; app.py with all
  endpoints in fixture mode; Dockerfile (python:3.11-slim, repo-root context, `uvicorn services.geo.app:app --host 0.0.0.0 --port 8001`).
- **P0:** `build_region hou` builds cells, crosswalk, ACS stock features, CMS clinician points; /grid, /features
  and /score live. Done when a real Houston map renders with fixed orthopedic weights.
- **P1:** full scoring (contributions, demand), /huff, /sensitivity, /rank, tests passing.
- **P2:** /features/profile and /features/ingest; CDC PLACES arthritis (tract) and METRO stops (points from
  Jayan's GTFS connector) ingest through the generic path.
- **P3:** `build_region jax`; cache region files to S3 (script in scripts/ is Archit's — send him the paths); speed check.
