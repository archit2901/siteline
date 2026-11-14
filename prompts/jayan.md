# Jayan — prompts to paste into Claude Code (or another coding assistant)

Start in the repo root on branch `sites`. Paste one block at a time. After each block:
`/milestone <name>` → commit → `/handoff` → post the note in team chat.
Not using Claude Code? Start the block with "Read CLAUDE.md, services/sites/CLAUDE.md and connectors/CLAUDE.md first."

**Human tasks (not for Claude) — do these first, tonight:**
- Post on the CoStar Discord: How far does the Houston coverage extend? Does it include current availability
  (space for lease, asking rent) or only building records? What do the MCP servers expose, and at what rate limits?
  What may appear in screenshots and the video? How many Houston buildings have Matterport scans?
- Message Dr. Deshmukh: What would you type into the intake form for SOS? Which factors matter for an orthopedic
  office that a generic tool would miss? Can we quote you, and show you results around 8 pm Saturday?
  No patient data, ever.
- Save the Peri evidence (a screenshot of the conversation or a direct quote). If he is a relative, we disclose it
  on the Devpost.

---

## Setup (now → 12:30 am)
```
I'm Jayan. Setup milestone. Read CLAUDE.md, services/sites/CLAUDE.md, connectors/CLAUDE.md and contracts/models.py.

1. services/sites/smoke.py: connect to the CoStar MCP server using COSTAR_MCP_URL / COSTAR_API_KEY and the
   instructions pasted below; list every tool with its input schema; call the most relevant search tool for one
   Houston location; save the raw JSON under data/cache/costar/ (gitignored). Print which fields exist for
   buildings and spaces: availability, asking rent, floor, size, parking, building class, Matterport, Vexcel.
2. fixtures/sites.json: 12 fake Houston sites as SiteCandidate (fake addresses such as "100 Example St",
   plausible lat/lon spread across Houston, h3 at res 8, sqft, rent_psf, floor, parking_spaces, source="fixture"),
   mirroring the real field structure from the smoke test. Validate against contracts.
3. services/sites/app.py: /health, POST /sites/search and POST /sites/{id}/checks in FIXTURE_MODE;
   requirements.txt; Dockerfile (python:3.11-slim, repo-root context, uvicorn services.sites.app:app on 8002).

Done when the smoke test prints real Houston results and the fixture endpoints validate. Commit on branch `sites`.

CoStar track instructions:
<paste them here>
```

## P0 (→ Sat 4 am): catalog and packs
```
I'm Jayan. Milestone P0.

1. Fill catalog/houston.yaml with real, verified entries. Open each official page — never guess a URL. Cover:
   CDC PLACES tract data, Houston METRO GTFS, NPPES NPI Registry API, OpenStreetMap Overpass, CMS dialysis
   facilities, ACS language spoken at home (B16001 / C16001), City of Houston open data datasets that measure
   healthcare-relevant factors (schools, transit, parks, large employers), Harris County portal datasets, and a
   Texas school directory. For each: format, geo level, geo key column (check the actual data), measures, coverage,
   and connector if the generic ingest can't read it. Validate with the command in the file header.
2. Write packs/healthcare.pediatric_dentistry.yaml, healthcare.dialysis.yaml and healthcare.physical_therapy.yaml
   like the orthopedics pack. Pediatric dentists aren't in the CMS Medicare clinician file, so use nppes.registry.
   For physical therapy, nearby orthopedic surgeons are a referral source (direction +1).
3. If NCHS ambulatory care tables give annual visit rates by age band for these specialties, add them to
   visit_rates with a comment naming the table; otherwise leave them empty.

Commit.
```

## P1 (→ Sat 2 pm): real spaces
```
I'm Jayan. Milestone P1. Implement live /sites/search per services/sites/CLAUDE.md using the real CoStar tools
from the smoke test: caching, constraint filters, de-duplication, h3 per site, and the "candidate buildings" rule
if availability isn't in the data. Test it with the top zones from Parth's /score. Then update
evals/orthopedic_group.yaml with Dr. Deshmukh's answers, pasted below.

Dr. Deshmukh's answers:
<paste them here>
```

## P2 (→ Sat 8 pm): connectors and ingest testing
```
I'm Jayan. Milestone P2.

1. Build connectors/gtfs.py, nppes.py, osm.py, cms_dialysis.py and places.py per connectors/CLAUDE.md, each with
   an offline test on a tiny synthetic sample.
2. Using Parth's /features/profile and /features/ingest (he'll share curl commands), test 10–15 real Houston
   datasets from the catalog. Write evals/ingest_test_log.md: dataset, result (ok, coverage, notes) and every
   failure, so Parth can fix them.
3. Mark catalog entries that need a connector.

Commit. (Human task: the 8 pm call with Dr. Deshmukh — record his reaction as a quote.)
```

## P3 (→ Sun 2 am): site checks and Devpost draft
```
I'm Jayan. Milestone P3.

1. POST /sites/{id}/checks per services/sites/CLAUDE.md: Vexcel image through the CoStar tools saved under
   data/cache/vexcel/, served by GET /sites/{id}/image; Gemini vision parking estimate with
   parking_note="estimate from aerial image"; Matterport only if scans exist.
2. Draft docs/devpost.md: inspiration (Dr. Deshmukh's problem), what it does, how we built it, challenges,
   accomplishments, what we learned, what's next; a Peri section (business: Southeast Orthopedic Specialists;
   person: Dr. Rahul Deshmukh, orthopedic surgeon; the evidence; relationship disclosure if he is a relative);
   short sections for the CoStar, LangChain deepagents and Amazon tracks; how AI was used.

Commit.
```

## P4 (→ Sun 6 am)
```
I'm Jayan. P4. Finalize docs/devpost.md with team Discord usernames (pasted below). Then write the 8 questions
Peri and CoStar judges are most likely to ask, each with a crisp 2-sentence answer grounded in what we built.
```
