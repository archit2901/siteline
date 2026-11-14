"""dataset-scout (scripted): find one public Houston dataset that measures ONE uncovered factor.

Python drives the steps; the model (MODEL_FAST) only writes search queries and chooses among real search results.
Up to 3 tries; then the factor is dropped with a caveat. Scouts never download anything.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from contracts.models import DatasetCandidate, DatasetFormat, EventType, Factor, GeoLevel
from services.agent import llm
from services.agent.tools.catalog import catalog_search
from services.agent.tools.search import SearchHit, web_search

Emit = Callable[[str, EventType, dict[str, Any]], Awaitable[None]]
MAX_TRIES = 3
REGION = "Houston, Texas (Harris, Fort Bend and Montgomery counties)"

QUERY_PROMPT = (
    "You help find public datasets. Write 3 short search queries (3-6 words each, no quotes) for open-data portal "
    "search engines (Socrata, ArcGIS Hub) that would find a dataset measuring the factor below across " + REGION + ". "
    "Start specific, then broaden. Prefer nationwide or Texas datasets with census tract, ZIP, county or point "
    "geography (for example CDC PLACES for health measures, GTFS feeds for transit stops)."
)
CHOICE_PROMPT = (
    "Choose the single dataset that best measures the factor across " + REGION + ", or none. Reject datasets for "
    "other cities or states, aggregate-only statistics without geography, and anything unrelated. Accept nationwide "
    "or Texas datasets that cover Houston. Among equally good results prefer the most recently updated release. "
    "Use only the listed results."
)


class Queries(BaseModel):
    queries: list[str] = Field(min_length=1, max_length=3)


class Choice(BaseModel):
    choice: int | None = Field(default=None, description="1-based number of the chosen result, or null if none fits")
    format: DatasetFormat = "csv"
    geo_level: GeoLevel = "tract"
    why: str = ""


@dataclass
class ScoutResult:
    factor_id: str
    candidate: DatasetCandidate | None
    tries: int
    caveat: str | None = None


def _hit_lines(hits: list[SearchHit]) -> str:
    return "\n".join(f"{i}. {h.title} | {h.url} | {h.publisher} ({h.source}{', updated ' + h.updated if h.updated else ''})"
                     for i, h in enumerate(hits, start=1))


async def run_scout(factor: Factor, meter: llm.UsageMeter, emit: Emit) -> ScoutResult:
    agent = f"dataset-scout:{factor.id}"
    fast = llm.get_chat_model("dataset_scout", meter)
    ask = f"FACTOR: {factor.factor}\nWHY: {factor.why}\nPROXY: {factor.proxy}"

    queries_out = await fast.with_structured_output(Queries).ainvoke([SystemMessage(QUERY_PROMPT), HumanMessage(ask)])
    queries = (queries_out.queries if isinstance(queries_out, Queries) else []) or [factor.proxy]

    for attempt in range(1, MAX_TRIES + 1):
        query = queries[min(attempt - 1, len(queries) - 1)]
        hits: list[SearchHit] = []
        if attempt == 1:
            await emit(agent, "tool_call", {"tool": "catalog_search", "args": {"query": factor.proxy}, "factor_id": factor.id})
            found = catalog_search(f"{factor.factor} {factor.proxy}")
            hits += [SearchHit(e.title, str(e.url), e.publisher, "catalog") for e in found]
            await emit(agent, "tool_result", {"tool": "catalog_search", "factor_id": factor.id, "ok": True,
                                              "summary": f"{len(found)} catalog matches" + (f": {found[0].title}" if found else "")})
        await emit(agent, "tool_call", {"tool": "web_search", "args": {"query": query}, "factor_id": factor.id})
        web = await web_search(query, meter)
        hits += web
        await emit(agent, "tool_result", {"tool": "web_search", "factor_id": factor.id, "ok": bool(web),
                                          "summary": f"{len(web)} open-data results" + (f", e.g. {web[0].title}" if web else "")})
        if not hits:
            continue

        choice = await fast.with_structured_output(Choice).ainvoke(
            [SystemMessage(CHOICE_PROMPT), HumanMessage(f"{ask}\n\nRESULTS:\n{_hit_lines(hits)}")])
        picked = choice if isinstance(choice, Choice) else Choice()
        if picked.choice is not None and 1 <= picked.choice <= len(hits):
            hit = hits[picked.choice - 1]
            candidate = DatasetCandidate(
                id=f"cand_{factor.id}", factor_id=factor.id, title=hit.title or hit.url, url=hit.url,  # type: ignore[arg-type]
                publisher=hit.publisher or hit.source, format=picked.format, geo_level=picked.geo_level,
                why=picked.why or f"Measures {factor.proxy.lower()}",
            )
            await emit(agent, "tool_result", {"tool": "choose_dataset", "factor_id": factor.id, "ok": True,
                                              "summary": f"Proposed {candidate.title} ({candidate.publisher})"})
            return ScoutResult(factor.id, candidate, attempt)
        await emit(agent, "tool_result", {"tool": "choose_dataset", "factor_id": factor.id, "ok": False,
                                          "summary": f"No result fits (try {attempt} of {MAX_TRIES})"})
    return ScoutResult(factor.id, None, MAX_TRIES,
                       caveat=f"No public Houston dataset found for {factor.factor.lower()} after {MAX_TRIES} tries.")
