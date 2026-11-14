# Siteline — handoff for Claude Code

Put this file at `docs/HANDOFF.md` in the repo. It is the latest word: where it conflicts with anything else in the
repo (kit `CLAUDE.md` files, `prompts/*.md`, `docs/DESIGN.md`, the build-plan PDF), **this file wins**. Update the
other files to match when a task below says so.

First message to Claude Code:
```
I'm Archit. Read docs/HANDOFF.md completely, then CLAUDE.md and docs/DESIGN.md. HANDOFF.md overrides anything that
conflicts with it. Tell me in a few lines what is already done in the repo (check git log and the files), what
conflicts you found, and then start Task 2.
```

---

## 1. Where we are
- Repo: `github.com/archit2901/siteline` (public), created from `siteline-claude-kit.zip`.
- Already run in Claude Code (Task 1 below): ownership update, fake grid, and the Setup section of `prompts/archit.md`.
  Check what actually got finished before moving on.
- Real Houston data has arrived (section 6). The fake grid is now only a stopgap.
- Hackathon: HackPrinceton Fall 2026. **Code freeze Sun Nov 15, 6:00 am ET. Devpost deadline 8:00 am. Judging 9:30 am.**

## 2. What Siteline is
> **Update Nov 14:** Siteline is for ANY business in Houston, and the Devpost main track is **Business + Enterprise**
> (not Healthcare). Healthcare practices remain the deepest preset; Dr. Deshmukh's practice is the customer story.

Siteline tells any business where in Houston to open its next location: a laundromat, a tutoring center, a coffee
shop or a medical practice. The owner describes the business and its customers. An agent works out who the customers
are, who competes and what makes a new location succeed for *this* business, finds public data to measure each
factor (including data that isn't in our stock layer, such as OpenStreetMap competitors or CDC health measures), and
asks the user to approve the data plan. Deterministic Python scores every H3 hexagon in Houston and estimates the
customers each site would win (Huff model) after competitors and the owner's other locations take their share. Real
CoStar spaces in the best zones are ranked with rent per customer won, and a critic verifies every number before the
top 10 is shown with citations.

Customer: Dr. Rahul Deshmukh, orthopedic surgeon at Southeast Orthopedic Specialists (SOS), a multi-office private
practice in Jacksonville, FL. Demo region: Houston (Harris, Fort Bend, Montgomery). Stretch for Peri judges:
Jacksonville zones only (no CoStar there).

**The rule that never bends:** the LLM decides *what* to compute; Python computes *every* number. No LLM-written number
reaches the UI unless it traces to a stored feature value or model output.

## 3. Team
> **Update Nov 14:** Gaurav is out. Archit builds `web/` himself in his Claude Code session and owns every folder.
> Where this file still mentions Gaurav, read it as Archit's own web work.

| Person | Uses Claude Code | Does |
|---|---|---|
| Archit | Yes (Max) | Everything except `web/`: agents, contracts, data/geo, sites, infra, README |
| Gaurav | Yes | `web/` only |
| Parth | No | Research, finding and downloading public datasets, checking data |
| Jayan | No | CoStar Discord questions, Dr. Deshmukh (Peri), Devpost draft, dataset URLs |
Parth and Jayan send files and URLs to Archit, who commits them.

## 4. Decisions made (all final unless marked)
1. **Agents first, data later.** Agents run against swappable backends: `AGENT_BACKEND=mock` (fake, from fixtures),
   `local` (real Houston files in `data/raw/`), `http` (geo-service and sites-service). Shapes never change.
2. **LLM provider: Amazon Nova on Amazon Bedrock, paid with the team's $200 AWS credits.** No Claude API credits exist.
   - `MODEL_SMART` = strongest Nova model in the Bedrock catalog (orchestrator, factor-analyst, critic).
   - `MODEL_FAST` = `us.amazon.nova-2-lite-v1:0` (dataset-scouts, site-scout, eval judging).
   - Fallbacks, dev only: free Gemini key (low limits), OpenRouter free models (about 50 requests/day, ~1 full run).
   - Claude on Bedrock **only if** Billing → Credits shows the credits cover AWS Marketplace / third-party Bedrock
     models (third-party models are billed through AWS Marketplace). Otherwise do not call it.
   - Using Nova on Bedrock + hosting on EC2 strengthens the Amazon track.
3. **Two agent modes.** `AGENT_MODE=planned` (full deepagents planning) and `AGENT_MODE=scripted` (fixed step order in
   code; the model only does factor reasoning, dataset choice and claim selection). Use scripted if Nova's multi-step
   tool calling is unreliable. Both keep the deepagents orchestrator.
4. **Five agents, not eight:** site-strategist (orchestrator), factor-analyst, dataset-scout (one per uncovered factor,
   parallel), site-scout, analyst-critic. Profiler, data-engineer and market-modeler are plain Python tools.
5. **All math lives in geo code** (geo-service, or `LocalGeo` in the agent until geo-service exists). New endpoint
   `POST /rank` returns net-new, captured, cannibalized, site quality, final score and stability per site.
6. **Final score:** `F_j = α·pct(Δ_j) + (1−α)·(0.6·S_zone(j) + 0.4·Q_j)`, α = 0.7. The zone-score term is what makes
   the weight sliders re-rank sites (net-new does not depend on the weights). Sensitivity recomputes S and F per draw.
7. **Automatic ingestion** of new datasets: supported for Houston's common formats (CSV/JSON with lat/lon, GeoJSON,
   Socrata, ArcGIS feature layers, tract/ZIP/county tables) behind human approval. Anything else (PDF, messy
   spreadsheets) shows as "found, not ingested". Never eval or exec strings from a spec.
8. **Distances:** straight-line. The grid already has 5 km / 10 km ring sums; use them instead of drive-time radii.
   (OpenRouteService is cut.)
9. **CoStar coverage is inner Harris only** (section 6). The map scores all three counties; site ranking only uses top
   zones that have a clinic-fit listing within 2 km. Pitch point: "strong zones with no listings yet".
10. **Live demo practice (recommended, confirm with Archit):** the orthopedic group (Dr. Deshmukh's specialty), because
    current data covers it fully except arthritis rates. Use the pediatric dentist only if NPPES dentists and METRO
    bus stops are ingested.
11. **Cuts:** Photon iMessage; OpenRouteService; arbitrary ingestion; Vexcel parking counts (parking now comes from
    CoStar property data). Matterport is back in: 1,734 CoStar properties have Matterport model ids.
12. **Tracks:** Business + Enterprise (main, changed from Healthcare on Nov 14), CoStar, LangChain deepagents, Peri,
    Amazon, ChatOverflow leaderboard.
13. **Free-tier hygiene still applies:** LLM response cache, per-run call budget, token/cost logging, golden runs for
    the demo so it never depends on a live API.

## 5. Architecture
```
web (React + Vite, :5173)
  └─ HTTP + SSE ─> agent-api (FastAPI + deepagents, :8000) ── Amazon Bedrock (Nova)
                     ├─ GeoClient   : mock | local (data/raw) | http (geo-service :8001)
                     └─ SitesClient : mock | local (data/raw CoStar files) | http (sites-service :8002)
```
- Shapes: `contracts/models.py` (Pydantic v2) is the single source of truth. Archit edits it via `/contract-change`.
- Env: `AGENT_BACKEND`, `AGENT_MODE`, `LLM_PROVIDER`, `MODEL_SMART`, `MODEL_FAST`, `AWS_REGION=us-east-1`,
  `LLM_MAX_CALLS_PER_RUN`, `LLM_CACHE`, `DEMO_AUTO_APPROVE_SECONDS`, `FIXTURE_MODE`, `GEO_URL`, `SITES_URL`,
  `VITE_AGENT_URL`, `TAVILY_API_KEY` (optional), `LANGSMITH_*`.
- AWS credentials: default credential chain only (local profile now, EC2 instance role later). Never in code.
- Event payload conventions for SSE are in `services/agent/CLAUDE.md` (the web reducer depends on them).

## 6. Real data (received)
Place these in `data/raw/` (gitignored). Rename the GeoJSONs as shown.
| File | Rows | Notes |
|---|---|---|
| `houston_h3_r8.parquet` / `.csv` | 11,720 cells × 99 cols | All 3 counties. Age bands, insurance counts and shares, Spanish/LEP, income, growth, clinicians per specialty (`n_<specialty>`, `*_within_5km`, `*_within_10km`), hospitals, 4 CoStar-derived columns, `cell_lat`, `cell_lon` |
| `houston_tracts.parquet` (+ `FILE_8341.geojson` → `houston_tracts.geojson`) | 1,381 | Polygons + same attributes |
| `houston_clinicians.parquet` / `.csv` | 58,193 rows / 30,019 clinicians | `npi`, `primary_specialty`, `specialty_group`, `lat`, `lon`, `geocode_quality` |
| `houston_hospitals.parquet` (+ `FILE_0011.geojson` → `houston_hospitals.geojson`) | 69 | type, ER, rating |
| `houston_costar_properties.parquet` (+ `FILE_3415.geojson` → `houston_costar_properties.geojson`) | 1,758 | `property_id`, type, class, parking, Matterport ids, lease roll-ups, `is_clinic_candidate_type` |
| `houston_costar_lease_spaces.parquet` / `.csv` | 2,431 | One row per active space: `space_id`, `property_id`, `floor`, `sqft_min/max`, `rent_low/high_usd_sf_yr`, `space_use`, `listing_type`, `is_lease_pending` |
| `README.md` → `data/raw/README.md` | | Build notes and caveats from whoever prepared the data |
Python needs `pyarrow` and `h3` for these.

**Quality check results:** keys unique; all WGS84; no Census sentinel values; no negative counts; population totals
match exactly between tracts and grid (6,416,502); age bands sum to totals in every tract. Good to build on.

**CoStar coverage:** 1,753 of 1,758 properties are in Harris, 5 in Fort Bend, 0 in Montgomery; 77% are apartments.
Clinic-fit spaces (Office / OfficeMedical / Medical / Retail, 2,000–10,000 sq ft, coworking excluded): **772 spaces in
152 buildings** (142 first floor, 112 medical). Those buildings are within 5 km of 20% of grid cells (49% of people).
CoStar's lease data answers the availability question: real active listings with rent on 62%.

**Issues to handle in code:**
- Grid undercounts clinicians vs the clinician file (grid: 939 orthopedic / 50,377 all; file: ~1,015 / 53,600 unique
  locations). Likely points not landing in a cell. Recompute counts from `houston_clinicians` or ask the data builder.
- Exclude `listing_type == "PropertyCoworking"` (483 spaces, median $138/sq ft/yr).
- `floor` is words ("FirstFloor", "SecondFloor", ...) — parse to int.
- 69 lease rows repeat (property_id, suite, floor); 24 property addresses repeat — de-duplicate.
- `rba_sqft` missing for 79% of properties — use lease-space sqft.
- Some listings on market since 2000 — show the date or filter.
- Income top-coded at $250,001; growth ranges −43% to +243% (percentile normalization handles it); 154 empty cells;
  17% of clinician rows are ZIP-approximate (fine for 5–10 km counts); ring sums stop at the county edge.
- Only 39 dentists in the CMS file — pediatric dentistry needs NPPES.

**Still missing (the "agent finds data" datasets):** METRO bus stops (GTFS) and CDC PLACES tract measures (arthritis,
kidney disease, diabetes, high blood pressure). Small public downloads; Parth can fetch them. Optional: NPPES pediatric
dentists, CMS dialysis facilities, OSM schools/gyms. Vexcel imagery is not in the data (ask CoStar whether the kit
gives Vexcel access).

**Data rules:** the repo is public. Never commit CoStar data or anything derived from it (including the grid's
`n_costar_*`, `n_lease_spaces`, `n_medical_lease_spaces` columns) unless CoStar says it's OK. Census and CMS data are
public. Ask CoStar what may appear in screenshots and the video.

**Stock feature ids = the grid's column names.** Map the old pack ids:
| Old id (packs / docs) | Use |
|---|---|
| `pop_total_density` | `pop_density_km2` |
| `pop_45plus_density` | `pop_45_64` + `pop_65p` (or their `_within_5km` sums for reach) |
| `ortho_supply_20m` | `n_orthopedics_within_10km` (−1 for orthopedics, +1 for physical therapy) |
| `primary_care_15m` | `n_primary_care_within_5km` (+1, referral base) |
| `ins_medicare_share` / `ins_medicaid_share` / `ins_private_share` | `share_any_medicare` / `share_any_medicaid` / `share_any_private` |
| `income_median`, `pop_growth` | `median_hh_income`, `pop_growth_pct` |
| language (was agent-found) | `share_spanish`, `share_lep` — now stock |
| hospitals | `n_hospitals_within_5km` |
| dialysis proxy | `n_nephrology_within_10km` |

## 7. Ordered tasks (paste one at a time; run `/milestone`, commit and `/handoff` after each)

### Task 1 — done already (verify)
Ownership update in `CLAUDE.md`, `fixtures/make_fake_grid.py` + `fake_grid.json` on branch `setup-grid`, and the Setup
section of `prompts/archit.md` (agent-api replay, fixtures, validate.py).

### Task 2 — reconcile the repo with this handoff + LLM provider
```
Do Task 2 from docs/HANDOFF.md:
1. Update CLAUDE.md, services/agent/CLAUDE.md, docs/DESIGN.md, .env.example and prompts/archit.md so they match
   HANDOFF.md sections 3–5 (Nova on Bedrock, no Gemini Pro/Flash or Claude API defaults, AGENT_BACKEND mock|local|http,
   AGENT_MODE planned|scripted, team ownership). Keep everything else.
2. services/agent/llm.py: one model factory. LLM_PROVIDER=bedrock (default) uses langchain-aws ChatBedrockConverse,
   AWS_REGION=us-east-1, default credential chain. MODEL_SMART = strongest Nova model in my Bedrock catalog (check
   and tell me the id); MODEL_FAST = us.amazon.nova-2-lite-v1:0. Optional providers: gemini, openrouter (dev only).
   Per-role overrides. Rate limiter, retries with backoff, temperature 0 for factor-analyst/critic/evals.
3. LLM response cache (SQLite in runs/), per-run call budget (LLM_MAX_CALLS_PER_RUN, default 40), token and
   estimated-cost logging in tool_result events and runs.db.
4. Check whether Nova 2's built-in web grounding works through ChatBedrockConverse for web_search; else Tavily.
Commit.
```

### Task 3 — real data in, mocks become local backends
```
Do Task 3 from docs/HANDOFF.md. The real files are in data/raw/ (see HANDOFF section 6).
1. fixtures/make_grid_fixture.py: build fixtures/grid.json from data/raw/houston_h3_r8.parquet with h3, cell_lat,
   cell_lon and only the ~25 Census/CMS columns the packs use (no CoStar-derived columns). Replace fake_grid.json
   everywhere it's referenced; update fixtures/README.md. Tell me when Gaurav can pull it.
2. Feature ids = grid column names. Rewrite packs/healthcare.base.yaml and healthcare.orthopedics.yaml using the
   mapping table in HANDOFF section 6; add physical_therapy, dialysis and pediatric_dentistry packs the same way.
   Update the feature table in services/geo/CLAUDE.md.
3. services/agent/tools: GeoClient and SitesClient interfaces (contracts shapes only) with Mock*, Local* and Http*
   implementations chosen by AGENT_BACKEND.
   LocalGeo: list features, score, rank, sensitivity on the real grid with the docs/DESIGN.md math and decision 6 in
   HANDOFF; recompute clinician counts from houston_clinicians if the grid undercounts.
   LocalSites: lease spaces joined to properties -> SiteCandidate (id=space_id, sqft=sqft_max, rent_psf=rent_low,
   floor parsed from words, available = not is_lease_pending, parking from the property, building_class,
   matterport_id = first model id, submarket), excluding coworking, de-duplicated on (property_id, suite, floor),
   filtered by Constraints; only zones with a clinic-fit listing within 2 km are searched.
4. Tests: tract/grid totals match; every lease space joins a property; coworking excluded; floors parse; LocalGeo and
   LocalSites return valid contract shapes; same input gives same output.
Commit. Never commit anything from data/.
```

### Task 4 — factor-analyst
```
Do the P0 section of prompts/archit.md with these changes from docs/HANDOFF.md: models come from services/agent/llm.py
(Nova), AGENT_BACKEND=local, and run the cheap factor-analyst evals in both AGENT_MODE=planned and scripted.
Iterate the prompt until orthopedic_group and pediatric_dentist pass every theme and sign check. Show the eval table
and tell me whether Nova's tool calling held up.
```

### Task 5 — full agent pipeline
```
Build the full pipeline from the P1 and P2 sections of prompts/archit.md, on AGENT_BACKEND=local, with the changes in
docs/HANDOFF.md: orchestrator + factor-analyst + parallel dataset-scouts + site-scout + analyst-critic; both approvals
(interrupts + /approve); run store, events.jsonl, SSE per the payload table; /rescore (geo score + rank, no LLM);
verify_claims; LangSmith tracing; claim text built from Python templates with stored values, the model only chooses
and orders claims. scripts/e2e.py: one live orthopedic-group run end to end; check event order and artifacts.
Done when a live run streams todos -> factors -> scouts -> approvals -> map -> top 10 real CoStar spaces with
verified=true. Show the trace summary, tokens and cost. Commit.
```
After Task 5, tell Gaurav the agent API is live so he can start his P1.

### Task 6 — the datasets the agent finds live
```
Do Task 6 from docs/HANDOFF.md, using the specs in connectors/CLAUDE.md and the ingest section of
services/geo/CLAUDE.md:
1. connectors/gtfs.py (METRO stops from the GTFS zip I put in data/raw/ or its official URL) and connectors/places.py
   (CDC PLACES tract measures: arthritis, chronic kidney disease, diabetes, high blood pressure).
2. Generic ingest (profile + ingest -> ValidationReport) available to LocalGeo, with the allowlist, 200 MB cap,
   60 s timeout, filters via filter_column/filter_values only.
3. Add both sources to catalog/houston.yaml with their real official URLs (I'll paste them; never guess).
4. Make the orthopedic run's scout find and ingest CDC PLACES arthritis live; make the pediatric dentist run's scout
   find METRO stops. Optional if time: connectors/nppes.py for pediatric dentists.
Commit.
```

### Task 7 — safety net, deploy, README
```
Do the P3 and P4 sections of prompts/archit.md with these changes: record golden runs (orthopedic_group, and
pediatric_dentist if its data exists); stability badges via rank; Matterport viewer links on site cards' data
(verify the URL format with the CoStar kit docs); deploy to EC2 with an IAM instance role that allows Bedrock (no AWS
keys on the box), Caddy for HTTPS, docker-compose.prod.yml; README.md with a mermaid architecture diagram, how AI is
used (Claude Code wrote the code; Amazon Nova on Bedrock runs inside the product), data sources, and track notes for
CoStar, LangChain deepagents, Peri and Amazon. Commit.
```

### Optional — Jacksonville for Peri
Zones-only run for Duval (12031), St. Johns (12109), Clay (12019) using the same Census/CMS pipeline. Needs
Jacksonville data from Parth.

## 8. Message for Gaurav (obsolete since Nov 14: Archit builds the web app)
> Accept the GitHub invite to archit2901/siteline, then:
> ```bash
> git clone https://github.com/archit2901/siteline.git && cd siteline
> git switch -c web
> cp .env.example .env      # keep VITE_FIXTURE_MODE=1 and VITE_AGENT_URL=http://localhost:8000
> claude
> ```
> First message to your Claude:
> ```
> I'm Gaurav. I own web/ only. Read CLAUDE.md, web/CLAUDE.md and prompts/gaurav.md, then do the Setup section.
> Stop when its done criteria pass.
> ```
> When I say the real grid fixture (`fixtures/grid.json`) is on main, `git pull origin main` and use it instead of
> fake_grid.json. Then say `Read prompts/gaurav.md and do the P0 section` (then P1, P2...). Start P1 when I tell you
> the agent API is live. Show "candidate building" when `available` is null, a Matterport link when `matterport_id`
> exists, and a source link on every number. After each section run `/milestone`, commit, `/handoff`, send me the note.

## 9. Human tasks for Parth and Jayan
- **Jayan:** ask CoStar on Discord: Vexcel access in the kit? What may appear in screenshots/video? Can derived counts
  be in a public repo? Message Dr. Deshmukh: what he'd type in the intake form, which factors a generic tool would miss,
  permission to quote him, and a results call around 8 pm Saturday. Save the Peri evidence. Draft the Devpost
  (disclose if Dr. Deshmukh is a relative). No patient data, ever.
- **Parth:** download METRO GTFS and CDC PLACES (tract level) and send Archit the files plus the official URLs; ask the
  data builder about the clinician undercount; optional: NPPES pediatric dentists, Jacksonville tracts.

## 10. Timeline (Eastern)
| Milestone | Target | Done when |
|---|---|---|
| P0 | Sat 4 am | Real Houston map + factor-analyst passing evals |
| P1 | Sat 2 pm | Live run streams end to end on local backends |
| P2 | Sat 8 pm | Scout finds and ingests a dataset live; verified top 10 real CoStar spaces |
| P3 | Sun 2 am | EC2 deploy, golden runs replay offline |
| P4 | Sun 6 am | README, Devpost, video; freeze |
Behind schedule? Keep the order and cut in this order: Jacksonville, Matterport links, stability badges, pediatric
dentist demo. Never cut: factor list on screen, one live-found dataset, Huff net-new, CoStar top 10.

## 11. Rules
- Public GitHub repo created after 8 pm Nov 13; all project code written this weekend (open-source libraries fine).
- Exactly one main track (Healthcare). Devpost before 8 am Nov 15 with Discord usernames. Optional video ≤ 2 min.
  In-person demo ≤ 2 min, Sunday 9:30 am–2:30 pm; stay until three main-track judges have seen it.
- MLH Code of Conduct. No patient data. No scraping listing sites. Keys only in `.env`. AWS budget alert at $50.
- README states which parts were written with Claude Code.

## 12. Two-minute demo
Run the laptop with agent-api (`make run-agent`, AGENT_BACKEND=local) and the web app; open the LangSmith project in a
second tab. Recorded runs replay with the same approvals if the network or Bedrock fails; Offline demo needs no server.

| Time | On screen | Say |
|---|---|---|
| 0:00 | Start screen | Generic tools treat "retail" or "healthcare" as one category; a laundromat and an orthopedic group share no customers or competitors. Dr. Deshmukh's quote. |
| 0:15 | Live agent: type "A laundromat for apartment renters without in-unit washers" in the chat | Any business, in your own words; it asks only for what's missing. |
| 0:25 | The run line fills; a scout branch pulls laundromats from OpenStreetMap | The profiler chose renter households and a neighborhood trip; scouts find data our stock layer lacks. |
| 0:40 | Approve the data plan, then the weights (reasons under each factor) | Nothing is downloaded or scored without your approval; the weights are tailored to this business. |
| 0:55 | Results: ranked CoStar spaces, map with the catchment ring, "Stays top 10" column | Real spaces for lease, renter households won after competitors, rent per household won; every number verified. The top spaces hold no matter how you weigh the factors: 200 alternative weightings, still top 10. |
| 1:10 | Click a row: the flyer (sources, 3D tour); Ask tab: "Which has the most parking?" | Answers come only from the verified report. |
| 1:25 | Recorded run: Orthopedic group | Dr. Deshmukh's practice: a scout finds CDC PLACES arthritis; different factors, different map, patients instead of households. |
| 1:40 | Trace button (LangSmith), run meter | Every step runs on Amazon Nova through Bedrock; the meter shows calls and cost; the trace shows the parallel scouts. |
| 1:50 | | Next: every US metro. |

Per judge: CoStar → ranked spaces, flyer, Matterport tour; Peri → Dr. Deshmukh's practice (SOS) as the customer story;
LangChain → the LangSmith trace (run the server with AGENT_MODE=planned to show the deepagents/LangGraph planner);
Amazon → Nova on Bedrock, run meter, no AWS keys in code. (Nov 14: no EC2 — the hackathon AWS account allows Bedrock
only; Jacksonville is not built.)

## 13. Open questions
- Which Nova model is the strongest available on the account (for MODEL_SMART)?
- Do the AWS credits list Amazon Bedrock as eligible? (They should for Amazon's own models.)
- Does the CoStar kit include Vexcel access, and what can be shown publicly?
- Is the orthopedic group the live demo practice? (Recommended.)
- What explains the grid's clinician undercount?
