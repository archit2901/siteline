# fixtures/ — fake data everyone builds against (no real or licensed data here)

| File | Owner | Shape (contracts/models.py) | Used by |
|---|---|---|---|
| make_grid_fixture.py, grid.json | Archit | list of `{h3, cell_lat, cell_lon, county, <grid columns>...}`: the real Houston H3 res-8 grid (11,720 cells), public Census / CMS columns only, built from `data/raw/houston_h3_r8.parquet` | MockGeo (`AGENT_BACKEND=mock`), web map, agent fixtures |
| make_agent_fixtures.py | Archit | builds events.jsonl, zones.json, report.json from grid.json with the real scoring math | `make fixtures` |
| events.jsonl | Archit | one `RunEvent` per line: a fake pediatric-dentist run | agent replay, web replay |
| zones.json | Archit | `ScoreResponse` over all 11,720 real cells | agent artifacts, web map |
| report.json | Archit | `Report` with 10 recommendations (invented sites; real stock values; real math) | agent artifacts, web cards |
| sites.json | Archit | list of `SiteCandidate` (fake values, CoStar-like fields) | sites fixture mode, web cards |
| (moved) data/golden/<name>.jsonl (+ <name>/) | Archit | recorded real runs for offline replay: `scripts/record_golden.py`; under data/ because they hold CoStar spaces | demo safety net |
| validate.py | Archit | validates every file above against the contracts | `make fixtures` |

Never put CoStar data or anything derived from it in this folder (the repo is public). Fixture sites are invented.
