# Archit — prompts to paste into Claude Code

> **docs/HANDOFF.md section 7 is the current task list and overrides this file.** The blocks below are the detailed
> specs those tasks point to, updated for Nova on Bedrock, `AGENT_BACKEND=mock|local|http` and
> `AGENT_MODE=planned|scripted`.

Start Claude Code in the repo root on branch `agent`. Paste one block at a time. After each block:
`/milestone <name>` → commit → `/handoff` → post the note in team chat.
Optional second session for infra/scripts: `git worktree add ../siteline-infra -b infra` and run Claude there.

**Human tasks (not for Claude):** keep AWS credentials fresh in the default profile (Bedrock model access, budget
alert at $50; check Billing → Credits for Bedrock eligibility); create the LangSmith project; at P3, the AWS console
steps in infra/README.md.
**Merging:** you merge to `main` in your own terminal (Claude can't push to main): geo → sites → agent → web,
then `make e2e`, then `git tag pN && git push --tags`.

---

## Setup (now → 12:30 am)
```
I'm Archit. Read CLAUDE.md, docs/DESIGN.md, contracts/models.py, fixtures/README.md and services/agent/CLAUDE.md.
Goal: Setup milestone — a runnable agent-api in fixture mode plus the shared fixtures everyone builds against.

1. Run `PYTHONPATH=. python -m contracts.export` and fix anything that fails in contracts/ (keep shapes the same).
2. services/agent/requirements.txt and Dockerfile (python:3.11-slim; build context = repo root; copy contracts/,
   connectors/, services/agent/, catalog/, packs/, evals/, fixtures/; CMD uvicorn services.agent.app:app --host 0.0.0.0 --port 8000).
3. fixtures/validate.py: validate every fixture against contracts (events.jsonl → RunEvent per line, zones.json →
   ScoreResponse, report.json → Report, sites.json → list[SiteCandidate], fake_grid.json → list of objects with an
   "h3" key, packs/*.yaml → StockPack, catalog/houston.yaml → Catalog, evals/*.yaml → EvalCase). Skip missing files
   with a warning. Exit non-zero on any validation error.
4. fixtures/events.jsonl: a realistic fake run (~40 events) for evals/pediatric_dentist.yaml following the payload
   table in services/agent/CLAUDE.md: todos; 9 factors (5 stock-covered); 4 scouts in parallel (agent names
   "dataset-scout:<factor_id>") with one retry; a data_plan approval; a weights approval; file events for
   zones.json and report.json; done. Timestamps 1–3 s apart.
5. fixtures/zones.json (valid ScoreResponse; use h3 ids from fixtures/fake_grid.json if present, otherwise generate
   valid res-8 cells near Houston with the h3 library) and fixtures/report.json (valid Report, 10 recommendations,
   fake but plausible values, every claim has evidence_refs, verified=true).
6. services/agent/app.py: GET /health (Health); POST /runs (RunCreate → RunCreated; replay streams
   fixtures/events.jsonl or fixtures/golden/<golden>.jsonl with gaps capped at 1.5 s; live returns 501 for now);
   GET /runs/{id}/events as SSE (sse-starlette, one RunEvent JSON per message, history first for late subscribers);
   GET /runs/{id}/artifacts/{name} serving fixtures in replay; CORS for http://localhost:5173.
7. Tests: replay streams every event in order and each validates; fixtures/validate.py passes.

Run the export, validate.py and the tests; show the output. Commit in small steps on branch `agent`.
Do not edit web/, services/geo/, services/sites/, connectors/, catalog/, packs/ or evals/.
```

## P0 (→ Sat 4 am): factor-analyst
```
I'm Archit. Milestone P0 for services/agent. Re-read services/agent/CLAUDE.md, the agent workflow and worked
examples in docs/DESIGN.md, packs/ and evals/.

1. Check the installed deepagents version and read its README/docs. Before writing graph.py, tell me the exact
   create_deep_agent parameters, subagent dict keys, how interrupts are configured and how to resume them.
2. prompts/factor_analyst.md and prompts/strategist.md as described in services/agent/CLAUDE.md. Few-shot the
   factor-analyst with the 4 worked examples (including the opposite sign of nearby orthopedic surgeons for
   orthopedics vs physical therapy).
3. tools/profile.py (load_profile: validate; pick the pack from specialty → "healthcare.<specialty>", fall back to
   healthcare.base; geocode offices only when lat/lon are missing using the Census single-address geocoder) and
   tools/geo.py (list_stock_features, score_grid) calling GeoClient (AGENT_BACKEND=local on data/raw).
4. graph.py: build_agent(run_id) with the orchestrator and the factor-analyst subagent; MODEL_SMART for both through
   services/agent/llm.py. Support AGENT_MODE=planned and scripted.
5. services/agent/evals/run_evals.py: for each evals/*.yaml run only the factor-analyst, validate list[Factor],
   judge each must_include theme with the "judge" role (MODEL_FAST, temperature 0; yes/no + matching factor id),
   check sign_checks, print a table and an overall score.
6. Iterate on the prompt until orthopedic_group and pediatric_dentist pass every theme and sign check.

Show the eval table. Commit.
```

## P1 (→ Sat 2 pm): live run, stock data only
```
I'm Archit. Milestone P1. geo-service /score and /rank should be live at GEO_URL; if not, keep using
AGENT_BACKEND=local (LocalGeo / LocalSites on data/raw) and tell me.

1. tools/geo.py: score_grid (writes runs/{id}/zones.json), rank_sites (geo /rank; writes ranked.json);
   tools/weights.py weights_from_priorities (high 3, medium 2, low 1, normalized; directions from the factors).
2. Until CoStar is wired, rank pseudo-sites at the centroids of the top zones (source="fixture",
   address "Zone <h3>").
3. Approvals: interrupt_on for register_features and score_grid with a checkpointer. Emit an "approval" event
   carrying an ApprovalRequest; POST /runs/{id}/approve (ApproveRequest) resumes the graph (approve, edit with
   edited_spec / kept_factor_ids, reject). DEMO_AUTO_APPROVE_SECONDS > 0 auto-approves.
4. runs.py: SQLite runs table (id, created_at, mode, status, profile_json); append every event to
   runs/{id}/events.jsonl and broadcast to SSE subscribers.
5. stream.py: stream the graph with subgraphs on; map chunks to RunEvents per the payload table (todos from
   write_todos, factors from /factors.json, files from artifact writes).
6. POST /runs/{id}/rescore: geo /score then /rank with the new spec → RescoreResponse. No LLM call.
7. LangSmith tracing from env (LANGSMITH_TRACING, LANGSMITH_API_KEY, LANGSMITH_PROJECT).
8. scripts/e2e.py: run a replay and a live run for evals/orthopedic_group.yaml against the running services; assert
   the event order (todo → factor → approval → file zones.json → done) and that artifacts validate.

Done when a live stock-data run streams end to end into Gaurav's UI. Run tests and `make e2e`; show output. Commit.
```

## P2 (→ Sat 8 pm): scouts, real spaces, critic
```
I'm Archit. Milestone P2. Read catalog/houston.yaml and the ingest endpoints in services/geo/CLAUDE.md.

1. tools/catalog.py catalog_search over Catalog; tools/search.py web_search (Nova web grounding through
   ChatBedrockConverse `bind_tools(["nova_grounding"])` if it works, otherwise Tavily) filtered to the allowlist in
   docs/DESIGN.md, returning titles and URLs only.
2. tools/geo.py preview_dataset (geo /features/profile) and register_features (geo /features/ingest per approved
   FeatureSpec; returns ValidationReports; updates each factor's status to found / proxy / dropped and emits factor events).
3. dataset-scout subagent + prompts/dataset_scout.md (MODEL_FAST): one per uncovered factor, all spawned in a single
   orchestrator step so they run in parallel; catalog first, then web search; preview before proposing; ≤3 tries,
   then a proxy, then drop with a caveat; writes /candidates/{factor_id}.json; never downloads anything itself.
4. site-scout subagent (MODEL_FAST): tools/sites.py search_sites (SITES_URL /sites/search with the top 15 zones and the
   profile constraints) and site_checks; replace the P1 pseudo-sites with real candidates.
5. analyst-critic + prompts/critic.md + tools/verify.py verify_claims exactly as specified in services/agent/CLAUDE.md.
   Report.verified must be true before "done"; the critic rewrites up to 2 times, then drops unmatched claims with a caveat.
6. Extend run_evals.py with an optional full-pipeline run for pediatric_dentist when services are live.

Done when the pediatric dentist run finds METRO bus stops live (catalog or web search → preview → approval → ingest),
ranks real CoStar sites and ends with verified=true. Show a trace summary and the evals. Commit.
```

## P3 (→ Sun 2 am): safety net and deploy
```
I'm Archit. Milestone P3.

1. scripts/record_golden.py <run_id> <name>: copy runs/<id>/events.jsonl and its artifacts to
   fixtures/golden/<name>.jsonl and fixtures/golden/<name>/; replay with golden=<name> serves those artifacts.
   Verify replay works with only agent-api running.
2. Stability badges: call /rank with with_stability=true and fill Recommendation.stability.
3. infra/: Caddyfile (serve the web production build at /, `handle_path /api/*` → agent:8000 with streaming-friendly
   proxying for SSE), docker-compose.prod.yml (web built statically, served by Caddy), ec2-bootstrap.sh (Ubuntu:
   install Docker + compose plugin, clone the repo, expect .env to be copied in, compose up -d), infra/README.md
   with the manual AWS steps (t3.large, IAM instance role allowing Bedrock Converse and bedrock:InvokeTool for
   nova_grounding — no AWS keys on the box, security group 22/80/443, Elastic IP, $50 budget alert) and the deploy
   commands.
4. scripts/cache_to_s3.sh: upload data/regions/*.duckdb and data/cache/ to an S3 bucket given as an argument.

Show the deploy checklist. Commit.
```

## P4 (→ Sun 6 am): no new features
```
I'm Archit. P4 — no new features. 1) Fix the bugs from the last e2e run. 2) Write README.md at the repo root:
what Siteline is, a mermaid architecture diagram, how to run (fixture mode and live), data sources with links taken
from catalog/houston.yaml and pack provenance, how AI was used (which parts were written with Claude Code, which
models run inside the product), notes for the CoStar, LangChain deepagents, Peri and Amazon tracks. 3) Make sure the
LangSmith project has a clean trace of a full run to show LangChain judges.
```
