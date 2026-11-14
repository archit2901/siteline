# Siteline — project memory for Claude Code

## What we're building
Siteline tells a healthcare practice where to open its next office. The practice fills out an intake form. An
agent reasons about what makes a new office succeed for *this* practice, finds public data to measure each factor
(including data that isn't in our stock layer), and asks the user to approve the data plan. Deterministic Python
then scores every H3 hexagon in Houston and estimates net-new patients with a Huff model. CoStar returns real
spaces in the best zones, and a critic verifies every number before the top 10 is shown with citations.

HackPrinceton Fall 2026. Code freeze **Sun Nov 15, 6:00 am ET**; Devpost deadline 8:00 am ET; judging 9:30 am.
Demo region: Houston (Harris, Fort Bend, Montgomery counties). Stretch for Peri judges: Jacksonville zones only.
**`docs/HANDOFF.md` is the latest word: where anything conflicts with it, HANDOFF wins.** Full design:
`docs/DESIGN.md`. Each folder has its own `CLAUDE.md` — read it before working there.

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
   anything under `data/`. The repo is public: never commit CoStar data or anything derived from it (including the
   grid's `n_costar_*`, `n_lease_spaces`, `n_medical_lease_spaces` columns) unless CoStar says it's OK. AWS
   credentials come from the default credential chain only (local profile, later the EC2 instance role), never from
   code or `.env`. Downloads are cached in `data/cache/` (gitignored); real input files live in `data/raw/`.
6. **No patient data, ever.** Public, aggregate data only. Every feature carries a `provenance_url`.
7. **No scraping listing sites** (LoopNet, Crexi, etc.). Official APIs and open data only.
8. **Git:** small commits on your own branch. Never push to `main`, never force-push.
9. **Before saying "done":** run the tests and show the output.

## Ownership
| Person | Uses Claude Code | Does | Branch | Folders they edit |
|---|---|---|---|---|
| Archit | Yes (only Claude Code user) | Everything: agents, contracts, data/geo, sites, web app, infra, README | `agent` | everything |
| Parth | No | Research, finding and downloading public datasets, checking data | — | — |
| Jayan | No | CoStar Discord questions, Dr. Deshmukh (Peri), Devpost draft, dataset URLs | — | — |

Parth and Jayan send files and URLs to Archit, who commits them.

At the start of a session the human says who they are. If they don't, ask before editing anything.

## Architecture
```
web (React + Vite, :5173)
  └─ HTTP + SSE ─> agent-api (FastAPI + deepagents, :8000) ── Amazon Bedrock (Nova)
                     ├─ GeoClient   : mock | local (data/raw) | http (geo-service :8001)
                     └─ SitesClient : mock | local (data/raw CoStar files) | http (sites-service :8002)
```
- **LLM:** Amazon Nova on Amazon Bedrock (team AWS credits), built only through `services/agent/llm.py`.
  `MODEL_SMART` (strongest Nova model on the account) for site-strategist, factor-analyst, analyst-critic;
  `MODEL_FAST` = `us.amazon.nova-2-lite-v1:0` for dataset-scouts, site-scout and eval judging. Gemini and
  OpenRouter free tiers are dev-only fallbacks (`LLM_PROVIDER`). No Claude API; Claude on Bedrock only if the
  credits cover AWS Marketplace models.
- **Backends:** `AGENT_BACKEND=mock` (fixtures), `local` (real Houston files in `data/raw/`, math in `LocalGeo`),
  `http` (geo-service, sites-service). Shapes never change between backends.
- **Agent modes:** `AGENT_MODE=planned` (full deepagents planning) or `scripted` (fixed step order in code; the model
  only reasons about factors, chooses datasets and selects claims). Both keep the deepagents orchestrator.
- Env: `AGENT_BACKEND`, `AGENT_MODE`, `LLM_PROVIDER`, `MODEL_SMART`, `MODEL_FAST`, `AWS_REGION=us-east-1`,
  `LLM_MAX_CALLS_PER_RUN`, `LLM_CACHE`, `DEMO_AUTO_APPROVE_SECONDS`, `FIXTURE_MODE`, `GEO_URL`, `SITES_URL`,
  `VITE_AGENT_URL`, `TAVILY_API_KEY` (optional), `LANGSMITH_*`.
- Python code is imported from the repo root as packages: `contracts`, `services.agent`, `services.geo`,
  `services.sites`, `connectors`. Run things from the repo root with `PYTHONPATH=.`.
- Docker builds use the repo root as context (`docker-compose.yml`). Deploy: one EC2 instance behind Caddy.

## Milestones (Eastern time)
| Milestone | By | Demo works when |
|---|---|---|
| Setup | done | Contracts, fixtures and stub services run; UI shows the fake grid |
| P0 | Sat 4 am | Real Houston map + factor-analyst passing evals |
| P1 | Sat 2 pm | Live run streams end to end on local backends |
| P2 | Sat 8 pm | Scout finds and ingests a dataset live; verified top 10 real CoStar spaces |
| P3 | Sun 2 am | EC2 deploy, golden runs replay offline |
| P4 | Sun 6 am | README, Devpost, video; freeze |

Live demo practice: the orthopedic group (recommended in HANDOFF; confirm with Archit). Behind schedule? Cut in this
order: Jacksonville, Matterport links, stability badges, pediatric dentist demo. Never cut: factor list on screen, one
live-found dataset, Huff net-new, CoStar top 10.

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
