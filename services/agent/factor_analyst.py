"""factor-analyst: PracticeProfile -> list[Factor] (Nova through llm.py, structured output, Python repair pass).

Used directly in AGENT_MODE=scripted and by the evals; graph.py wraps the same prompt as a deepagents subagent.
"""

import json
import re
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
MAX_FACTORS = 24  # the cut only drops what cannot affect success; scouts are capped separately
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


PLAYBOOKS = Path(__file__).parent / "playbooks"
SPECIFIC = [  # (pattern over specialty, business type, services and notes) -> playbook
    (r"ortho|sports med|joint|spine|fracture|acl", "orthopedics"),
    (r"urgent care|walk-in|walk in|retail clinic", "urgent_care"),
    (r"physical therap|physiotherap|\brehab", "physical_therapy"),
]
SECTOR_PLAYBOOK = {"healthcare": "healthcare", "healthcare.community_health": "community_health", "nonprofit.food_bank": "food_bank", "nonprofit.base": "nonprofit",
                   "retail.services": "retail", "food.restaurant": "restaurant", "education.childcare": "childcare",
                   "fitness.base": "fitness"}
MAX_CANDIDATES = 40


def playbooks_for(profile: PracticeProfile) -> list[str]:
    """Domain playbook names for this business: first principles, its sector, and any service lines it names."""
    from services.agent.questions import profile_sector

    names = ["general"]
    sector = SECTOR_PLAYBOOK.get(profile_sector(profile))
    if sector == "community_health":  # both halves of a nonprofit clinic, then what is specific to it
        names += ["healthcare", "nonprofit"]
    if sector:
        names.append(sector)
    text = " ".join([profile.specialty, profile.business_type, profile.name, *profile.services, profile.free_text]).lower().replace("_", " ")
    for pattern, name in SPECIFIC:
        if re.search(pattern, text) and name not in names:
            if name != "orthopedics" and "healthcare" not in names:
                names.append("healthcare")
            names.append(name)
    return names


def playbook_text(profile: PracticeProfile) -> str:
    return "\n\n".join((PLAYBOOKS / f"{n}.md").read_text() for n in playbooks_for(profile) if (PLAYBOOKS / f"{n}.md").exists())


def must_keep(profile: PracticeProfile) -> list[tuple[str, str]]:
    """[(pattern, factor)] from the playbooks' "Must keep:" lists: factors the cut may not drop for this business."""
    out: list[tuple[str, str]] = []
    for block in playbook_text(profile).split("Must keep:")[1:]:
        for line in block.splitlines()[1:]:
            m = re.match(r"^- `([^`]+)` (.+)$", line.strip())
            if not m:
                break
            out.append((m.group(1), m.group(2)))
    return out


def dropped(factors: list[Factor], keep: list[tuple[str, str]]) -> list[str]:
    text = " ".join(f"{f.id} {f.factor} {f.why} {f.proxy}" for f in factors).lower()
    return [desc for pattern, desc in keep if not re.search(pattern, text)]


class Candidate(BaseModel):
    factor: str
    mechanism: str  # how it changes revenue, cost or risk for THIS business
    kind: str  # demand | payer | access | visibility | competition | referral | complement | cost | tenure | risk | evidence
    derived_from: str = ""  # the broader factor this one refines, if any
    measure: str  # the dataset or site attribute that would measure it


class Brainstorm(BaseModel):
    candidates: list[Candidate]


BRAINSTORM = """You are a site-selection strategist with deep domain knowledge. Before anyone picks factors, brainstorm.
List 25 to 40 DISTINCT candidate factors that could change whether a new location of THIS business succeeds, using
the domain playbook, the owner's answers (their best and worst sites are evidence), their service lines and their
space needs. Then add derivatives: for every broad factor, the specific, measurable versions of it (for example
"young athletes" -> "high schools with athletic programs", "youth sports fields", "club sports complexes").
Cover every kind that applies: demand, payer, access, visibility, competition (direct AND indirect, including
hospital systems or chains that capture the same customers), referral sources, complements/anchors, cost (rent,
buildout, labor), tenure (lease vs. buy vs. build for heavy buildouts), risk (flooding, safety, decline), and the
owner's own evidence. For each: the mechanism (how it moves revenue, cost or risk for THIS business, in one
sentence) and what would measure it in every Houston neighborhood or at each building. No numbers or statistics."""


def request_text(profile: PracticeProfile, candidates: list[Candidate] | None = None) -> str:
    pack = pack_for(profile)
    intake = profile.model_dump(mode="json", exclude={"stock_pack", "region_id"})
    hints = "\n".join(f"- {h}" for h in pack.factor_hints) or "- (none)"
    return (
        f"PRACTICE INTAKE:\n{json.dumps(intake, indent=2)}\n\n"
        f"AVAILABLE STOCK FEATURES (pack {pack.id}):\n{stock_feature_summary(profile)}\n\n"
        f"HINTS FROM THE SPECIALTY PACK (seeds, not limits):\n{hints}\n\n"
        f"DATASETS THE SCOUTS CAN FETCH (curated Houston catalog; the scouts can also search for others):\n"
        f"{catalog_summary(profile.region_id)}\n\n"
        f"DOMAIN PLAYBOOK:\n{playbook_text(profile)}\n\n"
        + (("BRAINSTORMED CANDIDATES (cut these down ONLY by removing a candidate when there is no plausible way it "
            "correlates with this business's success, and by merging true duplicates. When unsure, keep it. Keep "
            "specific derivatives as their own factors. Rank what remains with priority: high for the largest "
            "effects, low for plausible but small ones. At most 24):\n"
            + "\n".join(f"- [{c.kind}] {c.factor}: {c.mechanism} (measure: {c.measure})" for c in candidates) + "\n\n")
           if candidates else "")
        + "Write this practice's factor list."
    )


async def brainstorm(profile: PracticeProfile, chat: BaseChatModel) -> list[Candidate]:
    """Stage one: a wide list of candidate factors and derivatives (empty if the model fails; stage two still runs)."""
    intake = profile.model_dump(mode="json", exclude={"stock_pack", "region_id"})
    try:
        out = await chat.with_structured_output(Brainstorm).ainvoke([
            SystemMessage(BRAINSTORM),
            HumanMessage(f"BUSINESS:\n{json.dumps(intake, indent=2)}\n\nDOMAIN PLAYBOOK:\n{playbook_text(profile)}"),
        ])
    except Exception:
        return []
    items = out.candidates if isinstance(out, Brainstorm) else []
    seen: set[str] = set()
    unique = []
    for c in items:
        key = " ".join(c.factor.lower().split())
        if key and key not in seen:
            seen.add(key)
            unique.append(c)
    return unique[:MAX_CANDIDATES]


def repair(factors: list[Factor], profile: PracticeProfile) -> tuple[list[Factor], list[str]]:
    """Enforce what the model may not decide: real feature ids, coverage, status, unique ids, at most MAX_FACTORS factors."""
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
                  model: BaseChatModel | None = None, think: bool = True) -> tuple[list[Factor], list[str]]:
    """The factor list for one practice, plus notes about repairs. Retries once on a validation error."""
    chat = model or llm.get_chat_model("factor_analyst", meter)
    candidates = await brainstorm(profile, chat) if think else []
    structured = chat.with_structured_output(FactorList, include_raw=True)
    messages: list[BaseMessage] = [SystemMessage(PROMPT), HumanMessage(request_text(profile, candidates))]
    error: str | None = None
    for _ in range(2):
        result: dict[str, Any] = await structured.ainvoke(messages)  # type: ignore[assignment]
        parsed = result.get("parsed")
        if isinstance(parsed, FactorList) and parsed.factors:
            factors, notes = repair(parsed.factors, profile)
            missing = dropped(factors, must_keep(profile)) if think else []
            if missing:  # one more pass: the playbook's must-keep factors replace the weakest ones
                again: dict[str, Any] = await structured.ainvoke([*messages, HumanMessage(
                    "Your list drops factors this kind of business cannot be judged without. Add each of these, "
                    "replacing the weakest factors so the list stays at 24 or fewer:\n" + "\n".join(f"- {m}" for m in missing))])  # type: ignore[assignment]
                if isinstance(again.get("parsed"), FactorList) and again["parsed"].factors:
                    factors, notes = repair(again["parsed"].factors, profile)
                still = dropped(factors, must_keep(profile))
                notes = [f"Added back must-keep factors: {', '.join(m.split(':')[0] for m in missing if m not in still)}."] + notes
                if still:
                    notes.append(f"Still missing: {'; '.join(still)}.")
            return factors, ([f"Brainstormed {len(candidates)} candidate factors, kept {len(factors)}."] if candidates else []) + notes
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
