# Siteline — project memory for Claude Code

## What we're building
Siteline tells a healthcare practice where to open its next office. The practice fills out an intake form. An
agent reasons about what makes a new office succeed for *this* practice, finds public data to measure each factor
(including data that isn't in our stock layer), and asks the user to approve the data plan. Deterministic Python
then scores every H3 hexagon in Houston and estimates net-new patients with a Huff model. CoStar returns real
spaces in the best zones, and a critic verifies every number before the top 10 is shown with citations.

HackPrinceton Fall 2026. Code freeze **Sun Nov 15, 6:00 am ET**; Devpost deadline 8:00 am ET.
Demo region: Houston (Harris, Fort Bend, Montgomery counties). Stretch for Peri judges: Jacksonville zones only.
Full design: `docs/DESIGN.md`. Each folder has its own `CLAUDE.md` — read it before working there.

## Rules that never bend
1. **The LLM decides WHAT to compute; Python computes EVERY number.** No LLM-written number reaches the UI or the
   report unless it traces to a stored feature value or model output.
2. **`contracts/models.py` is the single source of truth** for every shape that crosses a service boundary and for
   the YAML in `packs/`, `catalog/`, `evals/`. Only Archit's session edits `contracts/` (via `/contract-change`).
   Anyone else who needs a change: stop, and put the proposed change in your summary for the human.
3. **Edit only the folders your human owns** (table below). Read anything.
4. **Fake data first.** Every endpoint works with `FIXTURE_MODE=1` using files in `fixtures/` before the real
   implementation exists. Swapping in real data must not change any shape.
5. **Secrets and data.** Never read or print `.env`. Never commit keys, raw CoStar/Vexcel/Matterport responses, or
   anything under `data/`. Downloads are cached in `data/cache/` (gitignored).
6. **No patient data, ever.** Public, aggregate data only. Every feature carries a `provenance_url`.
7. **No scraping listing sites** (LoopNet, Crexi, etc.). Official APIs and open data only.
8. **Git:** small commits on your own branch. Never push to `main`, never force-push.
9. **Before saying "done":** run the tests and show the output.

## Ownership
| Person | Focus | Branch | Folders they edit |
|---|---|---|---|
| Archit | AI agents, contracts, infra | `agent` | `contracts/`, `services/agent/`, `infra/`, `scripts/`, `Makefile`, `docker-compose.yml`, `README.md`, `docs/DESIGN.md`, `fixtures/events.jsonl`, `fixtures/report.json`, `fixtures/zones.json`, `fixtures/golden/`, `fixtures/validate.py` |
| Gaurav | Web app | `web` | `web/` |
| Parth | Geo engine | `geo` | `services/geo/`, `connectors/census.py`, `connectors/cms.py`, `fixtures/fake_grid.json`, `fixtures/make_fake_grid.py` |
| Jayan | Discovery data, real estate, customer | `sites` | `services/sites/`, all other files in `connectors/`, `catalog/`, `packs/`, `evals/`, `fixtures/sites.json`, `docs/devpost.md` |

At the start of a session the human says who they are. If they don't, ask before editing anything.

## Architecture
```
web (React + Vite, :5173)
  └─ HTTP + SSE ─> agent-api (FastAPI + deepagents, :8000) ── Gemini API (Pro + Flash)
                     ├─ HTTP ─> geo-service   (FastAPI, :8001)  grid, features, scoring, Huff, ingest
                     └─ HTTP ─> sites-service (FastAPI, :8002)  CoStar MCP, Vexcel, Matterport
```
- Service URLs come from env: `GEO_URL`, `SITES_URL`, `AGENT_URL`; the web app uses `VITE_AGENT_URL`.
- Python code is imported from the repo root as packages: `contracts`, `services.agent`, `services.geo`,
  `services.sites`, `connectors`. Run things from the repo root with `PYTHONPATH=.`.
- Docker builds use the repo root as context (`docker-compose.yml`). Deploy: one EC2 instance behind Caddy.

## Milestones (Eastern time)
| Milestone | By | Demo works when |
|---|---|---|
| Setup | Sat 12:30 am | Contracts, fixtures and stub services run; UI shows the fake grid; CoStar smoke test done |
| P0 Houston stock map | Sat 4 am | Real Houston grid colored by fixed orthopedic weights from real Census + CMS data |
| P1 Agent reasons | Sat 2 pm | Intake → factors → approval → map → net-new, streamed live, stock data only |
| P2 Finds data + spaces | Sat 8 pm | Pediatric dentist run finds METRO bus stops live, ingests them, returns a verified CoStar top 10 |
| P3 Verify + deploy | Sun 2 am | Public URL on EC2; golden runs replay offline; Vexcel images on cards |
| P4 Polish | Sun 6 am | README, Devpost, video done; freeze |

Each milestone ends with: merge to `main`, `make e2e`, tag (`p0`, `p1`, ...). If anything breaks later, we demo the last tag.

## Commands
- `make types` — export JSON Schema from contracts and regenerate `web/src/types/`
- `make fixtures` — regenerate the fake grid and validate every fixture against the contracts
- `make run-agent` / `make run-geo` / `make run-sites` / `make run-web` — run one service locally
- `make dev` — `docker compose up --build`
- `make test` — all Python tests; web: `cd web && npm run typecheck && npm test`
- `make e2e` — one replay run and one live run end to end (exists from P1)

## Conventions
- Python 3.11+, FastAPI, Pydantic v2 (import shapes from `contracts.models`, never redefine them), async `httpx`,
  pytest. Type hints everywhere. One module per concern. No notebooks.
- Every service exposes `GET /health` returning `contracts.models.Health`.
- Every service reads `FIXTURE_MODE` (default `1`) and serves fixture data when it is on.
- TypeScript strict; React function components; contract types come only from `web/src/types/` (generated).
- Log every external call (URL, status, bytes). Cache external downloads under `data/cache/<source>/`.
- Downloads: domain allowlist, 200 MB cap, 60 s timeout.

## Custom commands and agents
- `/milestone <P0..P4>` — check your folders against a milestone's done criteria
- `/handoff` — write a short status note for team chat
- `/contract-change <what>` — Archit only
- Subagent `boundary-reviewer` — run before every merge to `main`
