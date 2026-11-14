"""factor-analyst: PracticeProfile -> list[Factor] (Nova through llm.py, structured output, Python repair pass).

Used directly in AGENT_MODE=scripted and by the evals; graph.py wraps the same prompt as a deepagents subagent.
"""

import json
from pathlib import Path
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, ValidationError

from contracts.models import Factor, PracticeProfile
from services.agent import llm
from services.agent.tools.geo import SITE_FEATURES, stock_feature_ids, stock_feature_summary
from services.agent.tools.catalog import load_catalog
from services.agent.tools.packs import pack_for

PROMPT = (Path(__file__).parent / "prompts" / "factor_analyst.md").read_text()
MAX_FACTORS = 12
PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}


class FactorList(BaseModel):
    """Structured-output wrapper for the model; each item is the contract Factor."""

    factors: list[Factor]


def catalog_summary(region_id: str = "hou") -> str:
    """One line per curated dataset: what the scouts can fetch without searching the open web."""
    try:
        entries = load_catalog(region_id).entries
    except Exception:  # a broken catalog must not block the factor list
        return "- (catalog unavailable)"
    return "\n".join(f"- {e.title}: {', '.join(e.measures[:6])}" for e in entries)


def request_text(profile: PracticeProfile) -> str:
    pack = pack_for(profile)
    intake = profile.model_dump(mode="json", exclude={"stock_pack", "region_id"})
    hints = "\n".join(f"- {h}" for h in pack.factor_hints) or "- (none)"
    return (
        f"PRACTICE INTAKE:\n{json.dumps(intake, indent=2)}\n\n"
        f"AVAILABLE STOCK FEATURES (pack {pack.id}):\n{stock_feature_summary(profile)}\n\n"
        f"HINTS FROM THE SPECIALTY PACK (seeds, not limits):\n{hints}\n\n"
        f"DATASETS THE SCOUTS CAN FETCH (curated Houston catalog; the scouts can also search for others):\n"
        f"{catalog_summary(profile.region_id)}\n\n"
        "Write this practice's factor list."
    )


def repair(factors: list[Factor], profile: PracticeProfile) -> tuple[list[Factor], list[str]]:
    """Enforce what the model may not decide: real feature ids, coverage, status, unique ids, at most 12 factors."""
    stock = stock_feature_ids(profile)
    notes: list[str] = []
    out: list[Factor] = []
    seen: set[str] = set()
    for f in sorted(factors, key=lambda f: PRIORITY_ORDER[f.priority])[:MAX_FACTORS]:
        fid = f.id
        n = 2
        while fid in seen:
            fid, n = f"{f.id}_{n}", n + 1
        seen.add(fid)
        feature = f.feature_id
        if feature is not None and feature not in stock and feature not in SITE_FEATURES:
            notes.append(f"{f.id}: '{feature}' is not a stock feature; marked for a data scout.")
            feature = None
        covered = feature is not None
        out.append(f.model_copy(update={
            "id": fid, "feature_id": feature, "stock_covered": covered,
            "status": "covered" if covered else "searching", "caveat": None,
        }))
    if len(factors) > MAX_FACTORS:
        notes.append(f"Kept the {MAX_FACTORS} highest-priority of {len(factors)} factors.")
    return out, notes


async def analyze(profile: PracticeProfile, meter: llm.UsageMeter | None = None,
                  model: BaseChatModel | None = None) -> tuple[list[Factor], list[str]]:
    """The factor list for one practice, plus notes about repairs. Retries once on a validation error."""
    chat = model or llm.get_chat_model("factor_analyst", meter)
    structured = chat.with_structured_output(FactorList, include_raw=True)
    messages: list[BaseMessage] = [SystemMessage(PROMPT), HumanMessage(request_text(profile))]
    error: str | None = None
    for _ in range(2):
        result: dict[str, Any] = await structured.ainvoke(messages)  # type: ignore[assignment]
        parsed = result.get("parsed")
        if isinstance(parsed, FactorList) and parsed.factors:
            return repair(parsed.factors, profile)
        error = str(result.get("parsing_error") or "no factors returned")
        try:  # some models answer with plain JSON text instead of the tool call
            raw = result.get("raw")
            text = raw.content if raw is not None and isinstance(raw.content, str) else ""
            if text.strip():
                return repair(FactorList.model_validate_json(_json_block(text)).factors, profile)
        except (ValidationError, ValueError) as exc:
            error = str(exc)
        messages = [*messages, HumanMessage(f"That output was invalid: {error[:800]}\nReturn the factor list again, valid this time.")]
    raise ValueError(f"factor-analyst returned no valid factors: {error}")


def _json_block(text: str) -> str:
    """The first {...} object in a text answer (models sometimes wrap JSON in prose or code fences)."""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in the answer")
    return text[start : end + 1]
