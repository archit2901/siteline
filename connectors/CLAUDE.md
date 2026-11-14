# connectors — data source readers (owners: Parth for census.py and cms.py; Jayan for everything else)

A connector knows how to read one source that the generic ingest in services/geo can't read on its own (or that we
want pre-built for speed). geo-service calls connectors when building a region or when a FeatureSpec's
`source_id` names a connector.

## Interface (every connector module)
```python
ID = "gtfs.metro"                      # connector id used in packs/ and FeatureSpec.source_id
PROVENANCE_URL = "https://..."          # the official page for the source (fill in from the real site)
def fetch(region: "RegionConfig", cache_dir: pathlib.Path) -> pandas.DataFrame: ...
```
Return one of two standard shapes:
- **Tabular:** a geography column named `geoid` (11-digit tract), `zcta` (5-digit) or `county_fips` (5-digit),
  plus numeric value columns with snake_case names.
- **Points:** `lat`, `lon`, `name`, `category`, plus optional `attractiveness` and extra columns.
Cache raw downloads in `cache_dir` (under `data/cache/<connector>/`, gitignored). Respect API page limits, back off on
errors, log URL and bytes. No API keys in code: read them from env.

## Connectors to build
| Module | ID(s) | Owner | Source | Output |
|---|---|---|---|---|
| census.py | `acs.B01001`, `acs.B27010`, `acs.B19013`, `acs.B16001` | Parth | Census Data API (ACS 5-year) + TIGER/Line 2020 tracts + Gazetteer ZCTA centroids | tabular by `geoid` (+ geometry helpers for geo) |
| cms.py | `cms.doctors` | Parth | CMS Doctors and Clinicians national downloadable file, filtered to the region; geocoded | points, `category` = primary specialty |
| places.py | `places.cdc` | Jayan | CDC PLACES tract-level measures (arthritis, chronic kidney disease, diabetes, high blood pressure — check the measure ids in the PLACES data dictionary) via its Socrata API | tabular by `geoid` |
| gtfs.py | `gtfs.metro` | Jayan | Houston METRO GTFS static feed (zip) → `stops.txt` | points, `category` = "bus_stop" |
| nppes.py | `nppes.registry` | Jayan | NPPES NPI Registry API, by taxonomy (e.g. pediatric dentistry) and city/state for the region | points (geocode addresses or use ZCTA centroid) |
| osm.py | `osm.overpass` | Jayan | OpenStreetMap Overpass API: schools, fitness centres, sports centres inside the region bbox | points, `category` = OSM tag |
| cms_dialysis.py | `cms.dialysis` | Jayan | CMS dialysis facility listing (Provider Data Catalog) | points, `attractiveness` = number of stations if present |

Find the official URLs from the publishers' sites; don't invent endpoints. Put each URL in `PROVENANCE_URL` and in
`catalog/houston.yaml`.

## Tests
`connectors/tests/` with tiny **synthetic** samples (a few made-up rows in the real column layout) so tests run
offline. Never commit real downloaded files.
