# fixtures/ — fake data everyone builds against (no real or licensed data here)

| File | Owner | Shape (contracts/models.py) | Used by |
|---|---|---|---|
| make_fake_grid.py, fake_grid.json | Parth | list of `{h3, lat, lon, <feature ids>...}` for a rough Houston box | geo fixture mode, web map, agent fixtures |
| events.jsonl | Archit | one `RunEvent` per line: a full fake pediatric-dentist run | agent replay, web replay |
| zones.json | Archit | `ScoreResponse` | agent artifacts, web map |
| report.json | Archit | `Report` with 10 recommendations | agent artifacts, web cards |
| sites.json | Jayan | list of `SiteCandidate` (fake values, CoStar-like fields) | sites fixture mode, web cards |
| golden/<name>.jsonl (+ golden/<name>/) | Archit | recorded real runs for offline replay (P3) | demo safety net |
| validate.py | Archit | validates every file above against the contracts | `make fixtures` |
