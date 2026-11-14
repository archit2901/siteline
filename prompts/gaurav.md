# Gaurav — prompts to paste into Claude Code

Start Claude Code in the repo root on branch `web`. Paste one block at a time. After each block:
`/milestone <name>` → commit → `/handoff` → post the note in team chat.
You never need a backend to make progress: everything runs from `fixtures/` until Archit's live API is up.

**Human tasks (not for Claude):** at P4, record and edit the 2-minute video; capture Devpost screenshots.

---

## Setup (now → 12:30 am)
```
I'm Gaurav. Setup milestone for web/. Read CLAUDE.md, web/CLAUDE.md, fixtures/README.md, and contracts/models.py
for the shapes.

1. Scaffold a Vite react-ts app inside web/ (keep web/CLAUDE.md). TypeScript strict. Install maplibre-gl,
   @deck.gl/core, @deck.gl/layers, @deck.gl/geo-layers, @deck.gl/mapbox, and vitest.
2. web/scripts/copy-fixtures.mjs plus npm scripts ("fixtures", and "predev" that runs it) copying
   ../fixtures/*.json and ../fixtures/*.jsonl into public/fixtures/ (gitignored).
3. From the repo root run `make types` to generate src/types/contracts.ts. If it fails, show me the error and use
   a stopgap generated from contracts/schema/*.json — never hand-write contract types.
4. MapView: MapLibre basemap with a free style that needs no key, centered on Houston (29.76, -95.37, zoom 9),
   deck.gl H3HexagonLayer via MapboxOverlay drawing public/fixtures/grid.json (real Houston cells; keys h3, cell_lat,
   cell_lon, county + grid columns) colored by one numeric field (sequential scale, e.g. pop_density_km2), tooltip
   with the cell's values. If grid.json isn't on main yet, render a few hard-coded
   valid res-8 cells and pull again in 15 minutes.
5. App shell: header, left panel (trace placeholder), center map, right panel (factor list placeholder).

Done when `npm run dev` shows the colored grid and `npm run typecheck` passes. Commit on branch `web`.
```

## P0 (→ Sat 4 am): the whole run view from fixtures
```
I'm Gaurav. Milestone P0. Use the data flow in web/CLAUDE.md and the event payload table in services/agent/CLAUDE.md.

1. src/state/runReducer.ts with vitest tests that fold fixtures/events.jsonl and check the final state
   (todos completed, factors present with statuses, approvals seen, report loaded).
2. src/api/replay.ts: fixture-mode player (~400 ms per event) that pauses at approval events until the modal is
   answered, and loads artifacts from public/fixtures/.
3. IntakeForm with 4 presets copied from the profiles in evals/*.yaml into src/presets/.
4. Trace (todo list), ScoutsPanel (one row per dataset-scout with tries and status), FactorList (status chips:
   covered, searching, found, proxy, dropped) and a SourceLink component.
5. MapView shows zones.json scores when its file event arrives; top zones outlined.

Done when a full run plays from fixtures with no backend. Commit.
```

## P1 (→ Sat 2 pm): live
```
I'm Gaurav. Milestone P1. Archit's agent-api runs at VITE_AGENT_URL.

1. src/api/client.ts + sse.ts: POST /runs (RunCreate), EventSource on /runs/{id}/events with reconnect,
   POST /runs/{id}/approve, POST /runs/{id}/rescore, GET /runs/{id}/artifacts/{name}.
2. Fixture/live switch (VITE_FIXTURE_MODE plus a small dev toggle in the header).
3. ApprovalModal for both kinds: data_plan (factor → dataset title, publisher, coverage %, ok, link) and weights
   (a slider per feature, sum shown, normalized on submit) → POST /approve (approve / edit / reject).
4. WeightSliders on the results view → POST /rescore (debounced 300 ms) → update the map and cards.
5. Error banner for "error" events.

Done when a live stock-data run plays end to end against agent-api. Commit.
```

## P2 (→ Sat 8 pm): results that convince
```
I'm Gaurav. Milestone P2.

1. SiteCards from report.json: rank, address, sq ft, rent, net-new patients (large), each claim with a SourceLink
   (feature evidence_refs → report.sources provenance URLs), Vexcel image when present, caveats, and
   "candidate building" instead of "for lease" when available is null.
2. Site pins on the map; clicking a card flies to the site and highlights its zone.
3. ScoutsPanel shows each scout's tries, chosen dataset and final status.
4. Every number on screen has a source link or a tooltip explaining where it comes from.

Commit.
```

## P3 (→ Sun 2 am): demo-ready
```
I'm Gaurav. Milestone P3.

1. DemoControls: replay a golden run (POST /runs with mode="replay", golden=<name>), auto-approve toggle,
   Houston / Jacksonville switch (region_id "hou" / "jax"; Jacksonville shows zones only, no sites).
2. Stability badge on cards ("top 10 in 92% of weightings").
3. Polish for judges seeing it from two meters away: large type for key numbers, consistent spacing, loading and
   empty states, no layout jumps, readable colors.
4. Production build works behind Caddy with VITE_AGENT_URL=/api.
5. List the 5 best screens to capture for Devpost and capture them.

Commit.
```

## P4 (→ Sun 6 am)
```
I'm Gaurav. P4 — UI fixes only. Write a shot list for the 2-minute video that follows the demo script in
docs/DESIGN.md: for each segment, the exact screen, clicks and what is on screen, with timings.
```
