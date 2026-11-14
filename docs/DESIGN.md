# Siteline design (condensed for Claude Code)

## Pitch
Siteline tells any business where in Houston to open its next location. Generic location tools treat "retail" or
"healthcare" as one category, but a laundromat, a tutoring center, a pediatric dentist and an orthopedic group share
almost no customers or competitors. Siteline's business profiler works out who the customers are, who competes and
what space fits; its agents find data to measure what matters and return the 10 best real spaces with the customers
each would win and the rent per customer, after competitors and the owner's other locations take their share.
Devpost main track: Business + Enterprise. The customer problem comes from Dr. Rahul Deshmukh, an orthopedic surgeon at Southeast Orthopedic
Specialists (SOS), a multi-office private practice in Jacksonville, FL.

Scope this weekend: any business, Houston only (where the CoStar and Matterport data is); healthcare is the deepest
preset (curated packs, CMS clinician supply). Jacksonville
zones-only run as a stretch for Peri judges. `docs/HANDOFF.md` overrides this file where they conflict. Later: every US metro, then other industries by swapping the stock pack.

## Data layers
| Layer | What | Houston examples |
|---|---|---|
| Stock: base | Loaded for every practice, precomputed in `data/raw/houston_h3_r8.parquet` | Population by age, insurance shares, income, growth, Spanish / limited-English households, clinicians by specialty (CMS) with 5 km / 10 km ring sums, hospitals |
| Stock: specialty pack | Small preset per specialty (`packs/`) | Orthopedics: 45+ population, orthopedic surgeon supply. Pediatrics: under-18 population |
| Agent-found | Datasets scouts find to measure this practice's factors | CDC PLACES arthritis / kidney disease / diabetes by tract, METRO bus stops (GTFS), schools and gyms (OSM), dialysis facilities (CMS), pediatric dentists (NPPES) |
| Real estate | Actual spaces | CoStar properties and active lease spaces (inner Harris only), Matterport model ids; parking from CoStar property data |

## Models
Amazon Nova on Amazon Bedrock, built only through `services/agent/llm.py`: `MODEL_SMART` (strongest Nova on the
account) for the orchestrator, factor-analyst and critic; `MODEL_FAST` = Nova 2 Lite for scouts and eval judging.
Response cache, per-run call budget and token/cost logging on every call. Agents run against swappable backends
(`AGENT_BACKEND=mock|local|http`) in `AGENT_MODE=planned|scripted`.

## Agent workflow (one run)
1. **Intake** → `PracticeProfile` (specialty, services, payers, languages, offices, space needs, free text).
2. **Profile** (`load_profile` tool): validate, geocode existing offices, pick the stock pack.
3. **Factor reasoning** (factor-analyst subagent): 6 to 12 `Factor`s, each with why, measurable proxy, direction,
   priority, and whether stock data covers it.
4. **Coverage check**: stock-covered factors map straight to features. Each uncovered factor gets a dataset-scout,
   all in parallel.
5. **Scout + validate**: scout searches `catalog/houston.yaml` then allowlisted web search, previews the dataset via
   geo `/features/profile`, proposes a `FeatureSpec`. Up to 3 tries, then a proxy, then drop with a caveat.
6. **Approval 1 (data plan)**: user approves the factor → dataset plan; `register_features` runs geo
   `/features/ingest` for each approved spec and returns `ValidationReport`s.
7. **Approval 2 (weights)**: `weights_from_priorities` (high 3, medium 2, low 1, normalized); user edits; `score_grid`.
8. **Sites**: site-scout gets CoStar spaces in the top 15 zones that have a clinic-fit listing within 2 km
   (`SitesClient.search`) and checks. CoStar covers inner Harris only, so strong zones without listings are shown as
   "strong zones with no listings yet".
9. **Rank**: `rank_sites` (geo `/rank`): net-new patients, site quality, final score, stability.
10. **Report**: analyst-critic ranks the top 10, writes claims with `evidence_refs`, `verify_claims` must pass.

## Scoring model (geo-service implements; the agent never computes these)
Grid: H3 resolution 8 (~0.74 km² per cell). Tract data goes to each cell via the tract containing the cell
centroid: densities and rates carry over; counts are split evenly across the cells of that tract.

- **Normalize** each feature to a percentile rank across the region's N cells: `x̃_ik = rank_k(x_ik) / N`.
- **Zone score**: `S_i = Σ_k w_k · c_ik` where `c_ik = x̃_ik` if direction is +1 and `1 − x̃_ik` if −1;
  weights sum to 1, so `S_i` is in [0, 1]. `contributions[k] = w_k · c_ik`.
- **Demand** (annual visits from cell i): `D_i = π_i · Σ_a P_ia · r_a`, where `P_ia` is population in demand age band
  a (from the pack), `r_a` the pack's visit rate (1.0 if missing), and `π_i` the share of residents whose coverage
  type the practice accepts (ACS B27010 by age; caveat: plan-level coverage is not public at tract level).
- **Distances are straight-line.** Supply and reach features use the grid's 5 km / 10 km ring sums
  (`*_within_5km`, `*_within_10km`) instead of drive-time radii.
- **Travel time** (Huff only): straight-line distance × 1.3 at 35 mph, in minutes, floored at 2 minutes.
- **Huff capture** of cell i by site j: `P_ij = A_j t_ij^−β / (A_j t_ij^−β + Σ_{m∈C} A_m t_im^−β)`, β = 2,
  A = attractiveness (clinicians at the location; 1 if unknown), C = competitors plus the practice's existing offices.
- **Net-new** for site j: `Δ_j = Σ_i D_i [ (P_ij + Σ_{o∈O} P^after_io) − Σ_{o∈O} P^before_io ]`, O = existing offices.
  Report captured, cannibalized and net-new separately.
- **Site quality** `Q_j` in [0, 1]: mean of parking fit `min(1, (spaces / (sqft/1000)) / min_ratio)`, ground-floor
  fit, size fit and rent fit; unknown parts count 0.5. Sites failing a hard constraint (size out of range, upper
  floor when ground floor is required, rent above max — only when the value is known) are excluded before ranking.
- **Final**: `F_j = α · pct(Δ_j) + (1 − α) · (0.6 · S_zone(j) + 0.4 · Q_j)`, α = 0.7. The zone-score term is what
  makes the weight sliders re-rank sites (net-new does not depend on the weights).
- **Sensitivity**: 200 draws `w' ~ Dirichlet(κ·w)`, κ = 50, recomputing `S` and `F`; `stability_j` = share of draws
  with j in the top 10.
- geo-service computes all of this; `POST /rank` returns net-new, quality, final score and stability per site.

## Worked examples (targets for the factor-analyst; also in `evals/`)
| Practice | Factors it should reason about | Stock covers | Agent must find |
|---|---|---|---|
| Orthopedic group: Medicare + commercial, 3 offices, needs X-ray and 15+ parking spaces | Aging population, arthritis, youth sports injuries, primary care referrals nearby, few orthopedic surgeons, easy parking for patients on crutches | Age, insurance mix, orthopedic surgeon supply, primary care supply; parking from CoStar | Arthritis by tract (CDC PLACES), high schools with athletics |
| Pediatric dentist: Medicaid + CHIP, Spanish-speaking staff | Children per household, Medicaid/CHIP families, Spanish-speaking households, schools, bus access for families without cars, few pediatric dentists | Under-18 population, insurance mix, Spanish / limited-English share | Schools, METRO bus stops (GTFS), pediatric dentists (NPPES taxonomy) |
| Dialysis clinic: mostly Medicare | Kidney disease, diabetes and hypertension; patients come 3× a week so short drives and transit matter; existing dialysis stations | Age, Medicare share | Kidney disease and diabetes by tract (CDC PLACES), dialysis facilities (CMS), METRO bus stops |
| Physical therapy: commercial + workers' comp | Orthopedic surgeons nearby (a **referral source** here: opposite sign from the orthopedic group), active adults, gyms, large employers | Age, orthopedic surgeon supply | Gyms and sports facilities (OSM), large employer locations |

Example factor (pediatric dentist):
```json
{"id": "families_without_car", "factor": "Families without a car",
 "why": "Medicaid families miss appointments when the office isn't near a bus line",
 "proxy": "Walking distance from each cell to the nearest METRO bus stop",
 "direction": -1, "priority": "high", "stock_covered": false, "status": "searching"}
```

## Guardrails
- Scouts never download; geo-service does, through the generic ingest or a named connector.
- Search results and downloads are restricted to an allowlist: `.gov`, `census.gov`, `data.cms.gov`, `cdc.gov`,
  Socrata portals, ArcGIS Hub portals, `ridemetro.org` (METRO), `openstreetmap.org` / Overpass.
- 200 MB cap and 60 s timeout per download. FeatureSpecs never contain code.
- `verify_claims` parses every number in the report and matches it to a stored value or model output, within rounding.

## Two-minute demo
0:00 the problem: generic location tools treat "retail" or "healthcare" as one category, but a laundromat, a tutoring
center and an orthopedic group share no customers or competitors; Dr. Deshmukh's quote · 0:15 live laundromat run:
the profiler picks renter households, a scout pulls competing laundromats from OpenStreetMap, approval · 0:45 switch
to the orthopedic group's golden run (Dr. Deshmukh's practice: a scout finds CDC PLACES arthritis; different factors,
different map) · 1:05 real CoStar top 10: customers won, rent per customer, floor, parking, Matterport link, cited
reasons; click a site: its flyer, catchment ring and sourced claims · 1:35 Dr. Deshmukh's reaction · 1:50 vision:
every US metro.
