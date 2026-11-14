"""web_search for dataset-scouts: official open-data search APIs first, Nova web grounding last.

- Socrata Discovery API (data.cdc.gov and city/county portals) and ArcGIS Hub search return real dataset pages.
- Nova grounding (bind_tools(["nova_grounding"])) hides its sources and composes URLs from memory, which were 404s
  in testing, so its URLs are kept only when a HEAD request succeeds.
Every hit must be on the allowlist. Titles and URLs only; nothing is downloaded here.
"""

import logging
import re
from dataclasses import dataclass
from itertools import zip_longest

import httpx

from services.agent import llm
from services.agent.tools.allowlist import BlockedURL, is_allowed, safe_request

log = logging.getLogger(__name__)
SOCRATA = "https://api.us.socrata.com/api/catalog/v1"
ARCGIS_HUB = "https://hub.arcgis.com/api/v3/datasets"
HEADERS = {"User-Agent": "Siteline/0.1 (HackPrinceton; public data research)"}
URL = re.compile(r"https?://[^\s)\]>\"'*]+")
GROUNDING_INSTRUCTION = (
    "Search the web for official public datasets that answer the request. Reply with one line per dataset: "
    "'<title> | <URL of the dataset page>'. Only list URLs that appeared in your search results."
)


@dataclass
class SearchHit:
    title: str
    url: str
    publisher: str = ""
    source: str = ""  # "socrata" | "arcgis" | "web"
    updated: str = ""


async def _get_json(client: httpx.AsyncClient, url: str, params: dict[str, str | int]) -> dict:  # type: ignore[type-arg]
    resp = await client.get(url, params=params, timeout=20)
    log.info("GET %s status=%s bytes=%s", resp.url, resp.status_code, len(resp.content))
    resp.raise_for_status()
    return resp.json()


async def socrata_search(query: str, client: httpx.AsyncClient, limit: int = 8) -> list[SearchHit]:
    data = await _get_json(client, SOCRATA, {"q": query, "only": "datasets", "limit": limit})
    hits = []
    for r in data.get("results", []):
        res, url = r.get("resource", {}), r.get("permalink") or r.get("link") or ""
        hits.append(SearchHit(title=res.get("name", "")[:160], url=url, source="socrata",
                              publisher=r.get("metadata", {}).get("domain", ""), updated=res.get("updatedAt", "")[:10]))
    return hits


async def arcgis_search(query: str, client: httpx.AsyncClient, limit: int = 8) -> list[SearchHit]:
    data = await _get_json(client, ARCGIS_HUB, {"q": query, "page[size]": limit})
    hits = []
    for r in data.get("data", []):
        a = r.get("attributes", {})
        if a.get("url"):
            hits.append(SearchHit(title=a.get("name", "")[:160], url=a["url"], source="arcgis",
                                  publisher=a.get("source") or a.get("owner") or ""))
    return hits


def parse_grounded_text(text: str) -> list[SearchHit]:
    hits, seen = [], set()
    for line in text.splitlines():
        for url in URL.findall(line):
            url = url.rstrip(".,;:")
            if url not in seen:
                seen.add(url)
                title = re.sub(r"[*_`#-]+", " ", line.split(url)[0]).strip(" |:\t") or url
                hits.append(SearchHit(title=title[:160], url=url, source="web"))
    return hits


async def reachable(url: str, client: httpx.AsyncClient) -> bool:
    """HEAD (or a 1-byte ranged GET when HEAD is refused). Downloads nothing. Every redirect hop must stay on the
    allowlist and resolve to a public address (safe_request), so a listed site can't bounce us to an internal host."""
    try:
        resp = await safe_request(client, "HEAD", url, timeout=10)
        if resp.status_code in (403, 405):
            resp = await safe_request(client, "GET", url, timeout=10, headers={"Range": "bytes=0-0"})
        log.info("HEAD %s status=%s", url, resp.status_code)
        return resp.status_code < 400
    except (httpx.HTTPError, BlockedURL, OSError) as exc:
        log.info("HEAD %s failed: %s", url, exc)
        return False


async def grounded_search(query: str, client: httpx.AsyncClient, meter: llm.UsageMeter | None) -> list[SearchHit]:
    model = llm.get_chat_model("dataset_scout", meter).bind_tools(["nova_grounding"])
    reply = await model.ainvoke(f"{GROUNDING_INSTRUCTION}\n\nRequest: {query}")
    content = reply.content
    text = content if isinstance(content, str) else "".join(
        b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return [h for h in parse_grounded_text(text) if is_allowed(h.url) and await reachable(h.url, client)]


async def web_search(query: str, meter: llm.UsageMeter | None = None, limit: int = 6,
                     use_grounding: bool = True) -> list[SearchHit]:
    """Allowlisted dataset pages for a plain-language query, best sources first, de-duplicated by URL."""
    per_source: list[list[SearchHit]] = []
    async with httpx.AsyncClient(headers=HEADERS) as client:
        for search in (socrata_search, arcgis_search):
            try:
                per_source.append([h for h in await search(query, client) if is_allowed(h.url)])
            except httpx.HTTPError as exc:
                log.warning("%s failed: %s", search.__name__, exc)
        # Interleave so one portal's long tail doesn't crowd out the other.
        hits = [h for group in zip_longest(*per_source) for h in group if h is not None]
        if use_grounding and len(hits) < 2:
            try:
                hits += await grounded_search(query, client, meter)
            except Exception as exc:  # grounding is a bonus source; never fail the search because of it
                log.warning("nova grounding failed: %s", exc)
    seen: set[str] = set()
    unique = []
    for h in hits:
        if h.url not in seen:
            seen.add(h.url)
            unique.append(h)
    return unique[:limit]
