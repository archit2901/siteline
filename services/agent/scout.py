"""dataset-scout (scripted): find one public Houston dataset that measures ONE uncovered factor.

Python drives the steps; the model (MODEL_FAST) only writes search queries and chooses among real search results.
Up to 3 tries; then the factor is dropped with a caveat. Scouts never download anything.
"""

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from contracts.models import Agg, DatasetCandidate, DatasetFormat, DatasetPreview, EventType, Factor, FeatureSpec, GeoLevel, PreviewRequest
from services.agent import llm
from services.agent.tools.backends import get_geo
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
    "or Texas datasets that cover Houston. Results from (catalog) are curated, verified Houston datasets that we know "
    "we can map: when one measures the factor, choose it over open-search results. Among equally good results prefer "
    "the most recently updated release. Use only the listed results."
)


class Queries(BaseModel):
    queries: list[str] = Field(min_length=1, max_length=3)


class SpecDraft(BaseModel):
    """The model's reading of a previewed dataset; Python validates every field against the real columns."""

    value_column: str | None = Field(default=None, description="numeric column measuring the factor (tables only)")
    agg: Agg = Field(default="mean", description="mean/sum for tables; count_within/distance_to_nearest/density for points")
    filter_column: str | None = None
    filter_values: list[str] = Field(default_factory=list)
    label: str = ""
    unit: str = ""


SPEC_PROMPT = (
    "You map a public dataset to ONE measurable factor. Given the factor, the dataset's columns and sample rows, "
    "choose: for tract/county tables the numeric value_column that measures the factor (agg mean for rates or "
    "percentages, sum for counts); for point datasets value_column null and agg count_within (how many nearby) or "
    "distance_to_nearest (how far to the closest). Optionally a filter_column/filter_values to keep only relevant "
    "rows (exact values seen in the sample), but only on a category column shared by many rows (type, category, "
    "grade range, status). Never filter on a name, id, address or phone column, and when unsure use no filter. "
    "label: short plain-language name; unit: %, km, count, share, USD..."
)


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
    spec: FeatureSpec | None = None


IDENTIFIER = re.compile(r"(name|^id$|_id$|objectid|^fid$|address|street|phone|fax|email|web|url|number|^npi$)", re.I)


def _identifier_filter(column: str, values: list[str], sample: list[dict[str, Any]]) -> bool:
    """True when a model-chosen filter would keep only a few named rows (e.g. one school by its name) instead of a
    category: the column looks like an identifier, or every sampled value is distinct and the filter picks from them."""
    if IDENTIFIER.search(column):
        return True
    seen = [str(r.get(column)) for r in sample if r.get(column) not in (None, "")]
    return len(seen) >= 3 and len(set(seen)) == len(seen) and len(values) <= 2


def build_spec(factor: Factor, candidate: DatasetCandidate, preview: DatasetPreview, draft: SpecDraft,
               run_tag: str = "") -> FeatureSpec | None:
    """A FeatureSpec only if every field the model chose exists in the previewed data."""
    columns = set(preview.columns)
    level, key = preview.geo_level_guess, preview.geo_key_guess
    if level is None or key is None:
        return None
    if level == "point":
        agg = draft.agg if draft.agg in ("count_within", "distance_to_nearest", "density") else "count_within"
        value = None
    else:
        value = draft.value_column if draft.value_column in columns else None
        if value is None:
            return None
        agg = draft.agg if draft.agg in ("mean", "sum") else "mean"
    keep_filter = draft.filter_column in columns and bool(draft.filter_values) and \
        not _identifier_filter(draft.filter_column or "", draft.filter_values, preview.sample_rows)
    # The run tag keeps concurrent runs from overwriting each other's ingested columns in the shared grid.
    fid = "found_" + re.sub(r"[^a-z0-9_]+", "_", factor.id.lower())[:40] + (f"_{run_tag}" if run_tag else "")
    unit = draft.unit or ("km" if agg == "distance_to_nearest" else "count" if level == "point" else "")
    return FeatureSpec(
        id=fid, label=(draft.label or factor.factor)[:80], source_id=candidate.id, source_url=candidate.url,
        format=preview.format, geo_level=level, geo_key=key, value_column=value,
        filter_column=draft.filter_column if keep_filter else None,
        filter_values=[str(v) for v in draft.filter_values][:20] if keep_filter else [],
        agg=agg, radius_min=7.0 if agg == "count_within" else None,  # ≈5 km straight line
        direction=distance_direction(factor) if agg == "distance_to_nearest" else factor.direction, unit=unit, layer="agent_found", provenance_url=candidate.url,
    )


NEAR_IS_BAD = re.compile(r"\bfew\b|compet|rival|saturat|flood|hazard|crime|landfill|pollut|far from|away from", re.I)


def distance_direction(factor: Factor) -> int:
    """Direction for a distance_to_nearest measure (bigger = farther away). The factor's own direction is often
    written about the thing ("proximity to hospitals is good" = +1), which on a distance would reward being far.
    Near the thing helps unless it competes or harms."""
    return 1 if NEAR_IS_BAD.search(f"{factor.factor} {factor.proxy}") else -1


def _hit_lines(hits: list[SearchHit]) -> str:
    return "\n".join(f"{i}. {h.title} | {h.url} | {h.publisher} ({h.source}{', updated ' + h.updated if h.updated else ''})"
                     for i, h in enumerate(hits, start=1))


async def run_scout(factor: Factor, meter: llm.UsageMeter, emit: Emit, run_tag: str = "") -> ScoutResult:
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
                                              "summary": f"Chose {candidate.title} ({candidate.publisher})"})
            await emit(agent, "tool_call", {"tool": "preview_dataset", "args": {"url": hit.url}, "factor_id": factor.id})
            preview = await get_geo().preview(PreviewRequest(url=hit.url))  # type: ignore[arg-type]
            spec = None
            if preview.ingestible:
                draft_out = await fast.with_structured_output(SpecDraft).ainvoke([SystemMessage(SPEC_PROMPT), HumanMessage(
                    f"{ask}\n\nDATASET: {candidate.title}\nGEOGRAPHY: {preview.geo_level_guess} via {preview.geo_key_guess}\n"
                    f"COLUMNS: {', '.join(preview.columns[:120])}\nSAMPLE: {preview.sample_rows[:2]}")])
                draft = draft_out if isinstance(draft_out, SpecDraft) else SpecDraft()
                spec = build_spec(factor, candidate, preview, draft, run_tag)
            if spec is not None:
                candidate = candidate.model_copy(update={"status": "valid", "format": preview.format,
                                                         "geo_level": spec.geo_level})
                await emit(agent, "tool_result", {"tool": "preview_dataset", "factor_id": factor.id, "ok": True,
                                                  "summary": f"Ingestible: {spec.geo_level} data; will measure "
                                                             f"{spec.value_column or spec.agg.replace('_', ' ')}."})
                return ScoutResult(factor.id, candidate, attempt, spec=spec)
            reason = preview.reason or "no column measures this factor"
            await emit(agent, "tool_result", {"tool": "preview_dataset", "factor_id": factor.id, "ok": False,
                                              "summary": f"Not usable: {reason}. Trying again (try {attempt} of {MAX_TRIES})."})
            continue
        await emit(agent, "tool_result", {"tool": "choose_dataset", "factor_id": factor.id, "ok": False,
                                          "summary": f"No result fits (try {attempt} of {MAX_TRIES})"})
    return ScoutResult(factor.id, None, MAX_TRIES,
                       caveat=f"No public Houston dataset found for {factor.factor.lower()} after {MAX_TRIES} tries.")
