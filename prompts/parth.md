# Parth — prompts to paste into Claude Code (or another coding assistant)

> **Obsolete (see docs/HANDOFF.md section 3):** Parth and Jayan no longer use Claude Code; Archit owns this work.
> Kept for reference only.

Start in the repo root on branch `geo`. Paste one block at a time. After each block:
`/milestone <name>` → commit → `/handoff` → post the note in team chat.
Not using Claude Code? Start the block with "Read CLAUDE.md, services/geo/CLAUDE.md and connectors/CLAUDE.md first."

**Human tasks (not for Claude):** sign up for a free Census API key and put it in `.env` as CENSUS_API_KEY.
**Push the fake grid first** — Gaurav and Archit are waiting on `fixtures/fake_grid.json`.

---

## Setup (now → 12:30 am)
```
I'm Parth. Setup milestone. Read CLAUDE.md, services/geo/CLAUDE.md, connectors/CLAUDE.md and contracts/models.py.

1. FIRST, within 30 minutes: fixtures/make_fake_grid.py (h3 v4) — res-8 cells covering lon -95.85..-94.95,
   lat 29.45..30.20, random.seed(42); each row has h3, lat, lon and plausible fake values for every stock feature id
   in services/geo/CLAUDE.md and the packs (pop_* counts and densities, ins_* shares, income_median, pop_growth,
   primary_care_15m, ortho_supply_20m, pop_45plus_density, pop_total_density). Write fixtures/fake_grid.json.
   Make the values spatially smooth (e.g. blend a few random centers) so the map looks realistic.
   Commit and tell me to push right away.
2. services/geo/app.py with every endpoint in the table, answering in FIXTURE_MODE from fake_grid.json with valid
   contract shapes. Implement the real math (percentile normalization, zone score, Huff, rank) on the fake values
   now — the same code will run on real data later.
3. requirements.txt and Dockerfile (python:3.11-slim, repo-root context, uvicorn services.geo.app:app on 8001).
4. Tests that every endpoint returns a valid contract shape in fixture mode.

Commit on branch `geo`.
```

## P0 (→ Sat 4 am): real Houston stock map
```
I'm Parth. Milestone P0.

1. connectors/census.py: TIGER/Line 2020 tracts for Texas filtered to counties 201, 157, 339; ACS 5-year B01001,
   B27010, B19013 by tract (latest vintage, CENSUS_API_KEY); Gazetteer ZCTA centroids. Cache in data/cache/census/.
2. connectors/cms.py: CMS Doctors and Clinicians filtered to the region (read in chunks or use a state filter),
   unique addresses geocoded with the Census batch geocoder (≤10,000 per batch), ZCTA-centroid fallback; cache.
3. services/geo/build_region.py: `python -m services.geo.build_region hou` → data/regions/hou.duckdb with cells,
   crosswalk, stock features, CMS points and feature_meta (schema in services/geo/CLAUDE.md).
4. Live mode (FIXTURE_MODE=0): /regions/hou/grid, /regions/hou/features, /score read the DuckDB.
5. scripts are Archit's folder, so put a small checker at services/geo/check_top.py that prints the top 10 cells for
   fixed orthopedic weights (pop_45plus_density 0.5, ortho_supply_20m 0.3, primary_care_15m 0.2) with their values.

Done when Gaurav's map renders real Houston scores from /score. Show the top-10 printout. Commit.
```

## P1 (→ Sat 2 pm): the math
```
I'm Parth. Milestone P1. Implement exactly the scoring model in docs/DESIGN.md: contributions, demand (pack demand
bands, visit rates, payer share π from B27010 by age), the travel-time rule, Huff with competitors and the practice's
existing offices, net-new split into captured / cannibalized / net-new, site quality, hard-constraint exclusion,
final score, /rank (with_stability) and /sensitivity (seed 42). Pack loading with `extends` and `+` sums only.
Write the tests listed in services/geo/CLAUDE.md. Check that /score takes < 1 s and /rank with stability for
40 candidates takes < 3 s. Show tests and timings. Commit.
```

## P2 (→ Sat 8 pm): automatic ingestion
```
I'm Parth. Milestone P2. Implement /features/profile and /features/ingest exactly as services/geo/CLAUDE.md:
readers for CSV/JSON with lat/lon, GeoJSON, Socrata, ArcGIS FeatureServer layers, and tract / ZIP / county tables;
the domain allowlist from docs/DESIGN.md; 200 MB cap; 60 s timeout; filters only through filter_column /
filter_values; aggregation (mean, sum, density, count_within, distance_to_nearest); ValidationReport; store the
feature and its meta. FeatureSpecs whose source_id names a connector (gtfs.metro, places.cdc, nppes.registry,
osm.overpass, cms.dialysis) call that connector module from connectors/. Test with a small local CSV, then CDC
PLACES arthritis by tract and METRO stops. Give Jayan ready-to-run curl commands for testing datasets. Commit.
```

## P3 (→ Sun 2 am): Jacksonville + speed
```
I'm Parth. Milestone P3. Build region jax (Florida 12; counties 031, 109, 019) with the same pipeline and confirm
/score works for region_id=jax (no CoStar there). Profile /score and /rank and speed them up if needed. List the
data/regions files and their sizes for Archit's S3 cache script. Commit.
```

## P4 (→ Sun 6 am)
```
I'm Parth. P4 — no new features. Write services/geo/factcheck.py: given a report.json, print every number in each
claim next to the stored value its evidence_ref points to, and flag mismatches. Run it on the golden runs.
```
