# services/sites — sites-service (owner: Jayan)

FastAPI service on :8002. Wraps the hackathon's CoStar MCP servers (CoStar buildings and space, Vexcel aerial
imagery, Matterport) and returns real office spaces as `contracts.models.SiteCandidate`.

## Layout
```
services/sites/
  app.py        /health, POST /sites/search, POST /sites/{id}/checks
  costar.py     MCP client; discovers tool names at startup and logs them; one function per tool we use
  mapping.py    CoStar / Vexcel / Matterport responses -> SiteCandidate / SiteCheckResult
  checks.py     Vexcel image fetch + Gemini vision parking estimate; Matterport lookup if scans exist
  cache.py      disk cache in data/cache/costar/ keyed by a hash of (tool, args)
  smoke.py      Setup smoke test: list tools, fetch one Houston building, save raw JSON to data/cache/costar/
  fixture.py    FIXTURE_MODE responses from fixtures/sites.json
  tests/
  requirements.txt, Dockerfile
```
Run: `make run-sites`. Tests: `PYTHONPATH=. pytest -q services/sites`.
Dependencies: fastapi, uvicorn[standard], httpx, pydantic>=2, h3, langchain-mcp-adapters (or the official `mcp`
Python client), langchain-google-genai (vision), pyyaml.

## Connecting to CoStar
Use the URL, transport and auth exactly as the CoStar track instructions say (`COSTAR_MCP_URL`, `COSTAR_API_KEY`
in `.env`). Discover tools at runtime and print their names and input schemas in smoke.py; don't guess tool names.
With langchain-mcp-adapters this looks like `MultiServerMCPClient({"costar": {"url": ..., "transport": ...}})`
then `await client.get_tools()` — check the installed version's README for the exact transport string.

## POST /sites/search (SiteSearchRequest → list[SiteCandidate])
1. For each zone (best first), take the H3 cell centroid (`h3.cell_to_latlng`).
2. Query CoStar for office / medical-office space near that point (about 1.5 km) or by submarket — whatever the
   tools support. Prefer spaces for lease; fall back to buildings.
3. Map to SiteCandidate; set `h3 = h3.latlng_to_cell(lat, lon, 8)`; `source="costar"`.
4. Filter by Constraints when the value is known: sqft in range, rent ≤ max_rent_psf, floor ≤ 1 if ground_floor.
5. De-duplicate by address; stop at `limit`.
If CoStar has building records but no availability, return buildings with `available=None` and
`checks={"availability": "unknown"}` so the UI can say "candidate buildings".

## POST /sites/{id}/checks (→ SiteCheckResult)
- Vexcel: fetch the aerial image for the site's location through the Vexcel tools; save to
  `data/cache/vexcel/<site_id>.jpg`; `vexcel_image_ref` = a path the agent/web can request
  (add `GET /sites/{id}/image` if needed).
- Parking estimate: send the image to Gemini (`GEMINI_FLASH_MODEL`) asking for the number of marked parking stalls
  on the site's lot; return an integer and `parking_note="estimate from aerial image"`. Never present it as exact.
- Matterport: only if CoStar confirms scans exist for the building; fill `ground_floor`, `step_free_entry`,
  `matterport_id`.

## Data rules
- Raw CoStar, Vexcel and Matterport responses stay under `data/` (gitignored). Never commit them.
- `fixtures/sites.json` is a list of SiteCandidate with **fake values** but the real field structure, written by
  hand from what the smoke test shows. Ask CoStar what may appear in screenshots and the video.
- Cache every call; back off on rate limits; log tool name, latency and result count.

## Milestones
- **Setup:** smoke.py runs; fixtures/sites.json (12 fake Houston sites with valid lat/lon and h3) validates;
  app.py serves both endpoints in fixture mode; Dockerfile (python:3.11-slim, repo-root context,
  `uvicorn services.sites.app:app --host 0.0.0.0 --port 8002`).
- **P1:** live /sites/search returns real Houston spaces for the top zones.
- **P3:** /checks with Vexcel image + parking estimate; Matterport only if scans exist.
