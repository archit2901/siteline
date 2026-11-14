# services/agent — agent-api (owner: Archit)

FastAPI service on :8000 that runs the deepagents orchestrator and streams every step to the web app.
It plans and explains. It never computes scores (geo-service does all math), never downloads datasets,
never calls CoStar directly.

## Layout
```
services/agent/
  llm.py           the only model factory: provider, model per role, rate limit, retries, cache, budget, cost
  app.py           FastAPI: /health, POST /runs, GET /runs/{id}/events (SSE), POST /runs/{id}/approve,
                   POST /runs/{id}/rescore (geo /score + /rank with the new spec, no LLM),
                   GET /runs/{id}/artifacts/{name}
  graph.py         build_agent(run_id) -> create_deep_agent(...)
  prompts/         strategist.md, factor_analyst.md, dataset_scout.md, site_scout.md, critic.md
  tools/           profile.py, geo.py, sites.py, catalog.py, search.py, weights.py, verify.py (LLM tools)
    backends.py    get_geo() / get_sites() from AGENT_BACKEND
    geo_client.py  GeoClient: MockGeo (fixtures/grid.json), LocalGeo (data/raw parquet), HttpGeo (GEO_URL)
    geomath.py     ALL scoring math (normalize, zone score, demand, Huff net-new, quality, final, stability)
    sites_client.py SitesClient: MockSites (zone pseudo-sites), LocalSites (CoStar files), HttpSites (SITES_URL)
    packs.py       pack loading with `extends`; stock feature catalog -> RegionFeature
  runs.py          run store (SQLite in runs/runs.db), events.jsonl writer, replay
  stream.py        LangGraph stream chunk -> contracts.models.RunEvent
  evals/run_evals.py   runs evals/*.yaml through the factor-analyst and scores them
  tests/
  requirements.txt, Dockerfile
```
Run: `make run-agent` (from the repo root). Tests: `PYTHONPATH=. pytest -q services/agent`.

## Dependencies
fastapi, uvicorn[standard], sse-starlette, httpx, pydantic>=2, pyyaml, boto3, langchain-core, langchain-aws,
deepagents, langgraph, langsmith, (optional) tavily-python. Dev-only fallbacks: langchain-google-genai,
langchain-openai (OpenRouter).
**deepagents changes quickly.** Before writing graph.py, check the installed version (`pip show deepagents`) and read
its README/docs for the exact names: `create_deep_agent(model=..., tools=..., system_prompt=..., subagents=[...],
interrupt_on={...}, checkpointer=..., backend=...)`, subagent dict keys (`name`, `description`, `system_prompt`,
`tools`, `model`), and how to resume an interrupt (`Command(resume=...)` with the same `thread_id`).

## Models (`llm.py`, never build a chat model anywhere else)
`get_chat_model(role, meter)` with role in orchestrator | factor_analyst | dataset_scout | site_scout | critic | judge.
- `LLM_PROVIDER=bedrock` (default): `ChatBedrockConverse`, `AWS_REGION=us-east-1`, default AWS credential chain.
  `MODEL_SMART` (strongest Nova on the account; default `us.amazon.nova-pro-v1:0`) for orchestrator, factor_analyst, critic; `MODEL_FAST`
  (default `us.amazon.nova-2-lite-v1:0`) for dataset_scout, site_scout, judge. `gemini` / `openrouter` are dev-only.
- Per-role overrides: `MODEL_<ROLE>`, `LLM_PROVIDER_<ROLE>`. Temperature 0 for factor_analyst, critic, judge.
- Shared per-provider rate limiter (`LLM_RPS`), provider retries with backoff (`LLM_MAX_RETRIES`).
- `LLM_CACHE=1`: SQLite response cache in `runs/llm_cache.db`; cache hits are free and not counted.
- `UsageMeter(run_id)`: per-run call budget (`LLM_MAX_CALLS_PER_RUN`, default 40, raises `LLMBudgetExceeded`),
  tokens and estimated cost per call in `runs/runs.db` table `llm_calls`; `meter.snapshot()` goes into
  tool_result payloads as `usage`.
- `scripts/check_bedrock.py` checks credentials, which Nova models answer, and Nova web grounding.
Never hard-code model ids outside `llm.py` defaults.

## Backends and modes
- `AGENT_BACKEND=mock` (fixtures) | `local` (real Houston files in `data/raw/`; `LocalGeo` does all the math from
  docs/DESIGN.md, `LocalSites` reads the CoStar files) | `http` (geo-service, sites-service). Same contract shapes.
- `AGENT_MODE=planned` (deepagents plans with write_todos/task) | `scripted` (fixed step order in code; the model only
  reasons about factors, chooses datasets and selects/orders claims). Use scripted if Nova's multi-step tool calling
  is unreliable. Both keep the deepagents orchestrator and emit the same events.

## Agents
| Agent | Job | Tools | Model | Writes (run filesystem) |
|---|---|---|---|---|
| site-strategist (orchestrator) | Plan with write_todos, delegate with task, run scouts in parallel, two approvals, re-plan on failure | write_todos, task, file tools, load_profile, list_stock_features, register_features*, weights_from_priorities, score_grid*, rank_sites | SMART | /plan.md |
| factor-analyst | 6–12 Factors with why, proxy, direction, priority, stock_covered | list_stock_features, web_search | SMART | /factors.json |
| dataset-scout (one per uncovered factor) | Find + preview a Houston dataset for ONE factor; ≤3 tries, then proxy, then drop with caveat | catalog_search, web_search, preview_dataset | FAST | /candidates/{factor_id}.json |
| site-scout | CoStar spaces in top zones + checks | search_sites, site_checks | FAST | /sites.json |
| analyst-critic | Rank top 10, choose and order Claims (text from Python templates with stored values), verify | read_file, verify_claims | SMART | /report.json |
Five agents only. Profiler, data-engineer and market-modeler are plain Python tools.
`*` = interrupt for human approval. Use a checkpointer (required for interrupts) and the FilesystemBackend rooted at
`runs/{run_id}/`. `DEMO_AUTO_APPROVE_SECONDS>0` auto-approves after that many seconds.

## Tools (thin wrappers; all shapes from contracts.models)
| Tool | Calls | Returns |
|---|---|---|
| load_profile | local + Census geocoder for office addresses | PracticeProfile with lat/lon and stock_pack |
| list_stock_features | GeoClient.list_features (geo GET /regions/{id}/features) | list[RegionFeature] |
| catalog_search(query) | reads catalog/houston.yaml (Catalog) | matching CatalogEntry list |
| web_search(query) | Nova web grounding (`bind_tools(["nova_grounding"])`) or Tavily; filter to the allowlist in docs/DESIGN.md | titles + URLs only |
| preview_dataset(url, format?) | geo POST /features/profile (PreviewRequest) | DatasetPreview |
| register_features(specs)* | geo POST /features/ingest per spec (IngestRequest) | list[ValidationReport] |
| weights_from_priorities(factors) | local: high 3, medium 2, low 1, normalized | ScoringSpec |
| score_grid(spec)* | geo POST /score (ScoreRequest) | ScoreResponse; also write zones.json artifact |
| rank_sites(candidates) | geo POST /rank (RankRequest) | list[RankedSite]; also write ranked.json artifact |
| search_sites(zones) | sites POST /sites/search | list[SiteCandidate] |
| site_checks(site_id) | sites POST /sites/{id}/checks | SiteCheckResult |
| verify_claims(report) | local | ok + list of unmatched numbers |

Geo and sites tools call `GeoClient` / `SitesClient` (backend from `AGENT_BACKEND`), never a service URL directly.
When `geomath.competitor_column(pack, ...)` is None (e.g. pediatric dentistry before NPPES is ingested), every
recommendation must carry `geomath.NO_COMPETITOR_CAVEAT`.
Tool outputs given to the LLM should be short summaries (top rows, counts); store full results as run artifacts.

## Events (RunEvent.payload conventions — the web reducer depends on these)
| type | payload |
|---|---|
| todo | `{"todos": [{"content": str, "status": "pending"\|"in_progress"\|"completed"}]}` |
| factor | a Factor (upsert by id; status changes stream as new factor events) |
| tool_call | `{"tool": str, "args": {...short...}, "factor_id"?: str}` |
| tool_result | `{"tool": str, "summary": str, "factor_id"?: str, "ok": bool, "usage"?: {calls, cache_hits, input_tokens, output_tokens, cost_usd, budget}}` |
| file | `{"name": "factors.json"\|"zones.json"\|"sites.json"\|"report.json", "path": str}` |
| approval | an ApprovalRequest |
| done | `{"report": "report.json"}` |
| error | `{"message": str, "recoverable": bool}` |
`agent` is the subagent name; scouts use `dataset-scout:<factor_id>`. Every event is appended to
`runs/{run_id}/events.jsonl` and pushed over SSE. Stream the graph with subgraphs on and map each chunk.

## Replay
`mode="replay"` streams a JSONL file (`fixtures/events.jsonl`, or `fixtures/golden/<golden>.jsonl`) with the
original gaps capped at 1.5 s. Artifacts for replay runs come from `fixtures/` (or `fixtures/golden/<name>/`).
`scripts/record_golden.py <run_id> <name>` copies a finished live run into `fixtures/golden/`.

## verify_claims
Extract every number in each Claim's text (ints, decimals, percents, "$" amounts). Each must match, within rounding
(2% relative or ±1 on integers), a value referenced by that claim's `evidence_refs`:
`feature:<id>@<h3>` → stored feature value; `model:net_new@<site_id>` etc. → model output in run artifacts;
`site:<field>@<site_id>` → SiteCandidate field. Any miss → `Report.verified=false`, the critic must rewrite.

## Prompts (prompts/*.md)
- factor_analyst.md: input = PracticeProfile + stock feature list + pack factor_hints; output = JSON list of Factor
  only. Each factor needs a proxy measurable per Houston neighborhood; no bare "population"/"income". Few-shot with
  the 4 worked examples in docs/DESIGN.md, including the opposite sign of nearby orthopedic surgeons for
  orthopedics vs physical therapy. Validate output with `TypeAdapter(list[Factor])`; on failure, retry once with
  the validation error.
- strategist.md: the run steps from docs/DESIGN.md as a checklist; always write_todos first; spawn one scout per
  uncovered factor in a single step so they run in parallel; never state numbers yourself.
- dataset_scout.md: catalog first, then web search; prefer tract or point data; propose one FeatureSpec; call
  preview_dataset before proposing; if not ingestible, mark candidate `found_not_ingested` with the reason.
- critic.md: write 3–5 Claims per site; every number must have an evidence_ref; call verify_claims; fix and retry.

## Milestones
- **Setup:** app.py with /health, POST /runs (replay works, live returns 501), SSE events, artifacts from fixtures,
  CORS for http://localhost:5173; fixtures/events.jsonl, report.json, zones.json; fixtures/validate.py; Dockerfile.
  Done when the web app can stream the fixture run from this API.
- **P0:** graph.py with orchestrator + factor-analyst against geo stubs; evals runner for factor-analyst only.
  Done when factors are specific for the orthopedic group and pediatric dentist evals.
- **P1:** real geo tools, weights_from_priorities, both interrupts, /approve, /rescore, SQLite run store,
  events.jsonl per run, LangSmith tracing, scripts/e2e.py. Until CoStar is wired (P2), rank pseudo-sites at the
  centroids of the top zones (`source="fixture"`, address "Zone <h3>") so net-new shows per zone.
  Done when a live stock-data run streams end to end.
- **P2:** parallel dataset-scouts, catalog_search, web_search with allowlist, preview → register_features → ingest,
  site-scout, analyst-critic, verify_claims, full evals. Done when the pediatric dentist run finds METRO stops live
  and ends with a verified CoStar top 10.
- **P3:** golden runs recorded and replayable offline; stability badges from /rank; deploy files in infra/.
- **P4:** bug fixes only; README architecture + AI-use notes.
