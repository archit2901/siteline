"""Weights for the zone score.

weights_from_priorities: factor priorities -> ScoringSpec (high 3, medium 2, low 1, normalized). Deterministic
fallback, also used when the model is unavailable.

advise_weights: the market-modeler step. The model reads the practice and its measured factors and says how
important each one is (an integer 1-10) and why, in one sentence about this practice. Python validates every item,
turns importance into weights that sum to 1, and keeps the justifications as ScoringSpec.rationale. The model never
writes a weight; justifications with digits are replaced, so no model-written number reaches the UI.
"""

import re
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from contracts.models import Factor, PracticeProfile, ScoringSpec

POINTS = {"high": 3, "medium": 2, "low": 1}
SCORABLE = ("covered", "found", "proxy")
MAX_WHY = 220


def scorable(factors: list[Factor]) -> list[Factor]:
    """Factors with a zone feature: site-level factors (site:*) feed site quality instead, unmeasured ones are left out."""
    return [f for f in factors if f.feature_id and not f.feature_id.startswith("site:") and f.status in SCORABLE]


def weights_from_priorities(factors: list[Factor]) -> ScoringSpec:
    usable = scorable(factors)
    if not usable:
        raise ValueError("no factor has a zone feature to score")
    total = sum(POINTS[f.priority] for f in usable)
    weights: dict[str, float] = {}
    for f in usable:  # two factors on the same feature share it
        weights[f.feature_id] = weights.get(f.feature_id, 0.0) + POINTS[f.priority] / total  # type: ignore[index]
    return ScoringSpec(
        weights={k: round(v, 4) for k, v in weights.items()},
        directions={f.feature_id: f.direction for f in usable},  # type: ignore[misc]
        rationale={f.feature_id: f"{f.priority} priority: {f.factor}" for f in usable},  # type: ignore[misc]
    )


# ---------------------------------------------------------------------------
# market-modeler: importance + justification per factor
# ---------------------------------------------------------------------------
class FactorWeight(BaseModel):
    factor_id: str
    importance: int = Field(description="1 (barely matters) to 10 (decides success) for THIS practice")
    why: str = Field(description="one sentence, specific to this practice's patients, payers, services or notes; no numbers")


class WeightAdvice(BaseModel):
    weights: list[FactorWeight]


PROMPT = """You are the market-modeler for Siteline, which helps a business choose where to open its next location.
You get the business and the factors we can measure in every neighborhood. Say how much each factor should count
toward picking a neighborhood FOR THIS BUSINESS: an integer importance from 1 (barely matters) to 10 (decides success).

Rules:
- Rate every factor listed, once, by its factor_id.
- Base importance on this business's own details: who its customers are, which insurance it takes, its services,
  how people get there, and anything in its notes. Two businesses of the same type can need different weights.
- Use the whole scale. Give the one or two factors that decide success 9 or 10 and true nice-to-haves 2 or 3;
  do not rate everything 5 to 7. Demand, payer fit and competition usually outweigh hazards and amenities.
- why: one plain sentence, written to the owner ("Your ..."), that names the specific detail of this business that
  makes the factor matter this much. Do not repeat the factor's name. Do not use any digits or numbers."""


def _profile_text(profile: PracticeProfile) -> str:
    parts = [f"Business: {profile.name}"]
    if profile.business_type:
        parts.append(f"Type: {profile.business_type}")
    if profile.specialty:
        parts.append(f"Specialty: {profile.specialty}")
    if profile.customers:
        parts.append(f"Customers: {profile.customers}")
    if profile.services:
        parts.append(f"Services: {', '.join(profile.services)}")
    if profile.payers_accepted:
        parts.append(f"Insurance accepted: {', '.join(profile.payers_accepted)}")
    if profile.languages:
        parts.append(f"Languages: {', '.join(profile.languages)}")
    if profile.existing_offices:
        parts.append(f"Existing locations: {len(profile.existing_offices)}")
    if profile.free_text:
        parts.append(f"Owner's notes: {profile.free_text}")
    return "\n".join(parts)


def _factor_lines(factors: list[Factor]) -> str:
    return "\n".join(f"- factor_id={f.id} | {f.factor} | {'more is better' if f.direction == 1 else 'less is better'} | "
                     f"analyst priority: {f.priority} | why it matters: {f.why}"
                     + (" | measured by a proxy" if f.status == "proxy" else "") for f in factors)


GROWTH = 1.5  # importance is convex: each step up is worth half again, so a 9 counts ~11x a 3, not 3x


def points_for(importance: float) -> float:
    return GROWTH ** (importance - 1)


def spec_from_advice(factors: list[Factor], advice: WeightAdvice | None,
                     profile: PracticeProfile | None = None) -> tuple[ScoringSpec, list[str]]:
    """Validated advice -> ScoringSpec. Missing or invalid items fall back to the analyst's priority, and a
    justification with digits (or empty) falls back to the factor's own why. Returns the spec and notes.

    Importance maps to points convexly (points_for), then expert rules (services/agent/expert.py) multiply: an
    orthopedic office's distance from other orthopedic offices is promoted, flood and transit are dampened."""
    from services.agent import expert
    usable = scorable(factors)
    if not usable:
        raise ValueError("no factor has a zone feature to score")
    by_id = {a.factor_id: a for a in (advice.weights if advice else [])}
    notes: list[str] = []
    points: dict[str, float] = {}
    why: dict[str, str] = {}
    for f in usable:
        item = by_id.get(f.id)
        if item is None:
            importance, reason = POINTS[f.priority] * 3, f.why  # same scale as 1-10: high 9, medium 6, low 3
            if advice is not None:
                notes.append(f"No weight advice for {f.factor}; used its {f.priority} priority.")
        else:
            importance = float(min(10, max(1, item.importance)))
            reason = " ".join(item.why.split())[:MAX_WHY]
            if not reason or re.search(r"\d", reason):
                reason = f.why
        if profile is not None:  # expert floors and caps: ortho competition always decides, hazards never do
            lo, hi, rule = expert.bounds(profile, f)
            if not lo <= importance <= hi and rule is not None:
                importance = min(hi, max(lo, importance))
                reason = rule.text
        fid = f.feature_id
        boost = expert.tier(profile, f) if profile is not None else 1.0
        points[fid] = points.get(fid, 0.0) + points_for(importance) * boost  # type: ignore[index]
        why[fid] = reason if fid not in why else f"{why[fid]} {reason}"  # type: ignore[index]
    total = sum(points.values())
    return ScoringSpec(
        weights={k: round(v / total, 4) for k, v in points.items()},
        directions={f.feature_id: f.direction for f in usable},  # type: ignore[misc]
        rationale=why,
    ), notes


async def advise_weights(profile: PracticeProfile, factors: list[Factor], meter: Any = None,
                         model: BaseChatModel | None = None) -> tuple[ScoringSpec, list[str]]:
    """Personalized weights with a justification per factor; falls back to priorities if the model fails."""
    usable = scorable(factors)
    if not usable:
        raise ValueError("no factor has a zone feature to score")
    try:
        if model is None:
            from services.agent import llm
            model = llm.get_chat_model("factor_analyst", meter)
        result = await model.with_structured_output(WeightAdvice).ainvoke([
            SystemMessage(PROMPT),
            HumanMessage(f"{_profile_text(profile)}\n\nFACTORS:\n{_factor_lines(usable)}"),
        ])
        advice = result if isinstance(result, WeightAdvice) else None
        if advice is None or not advice.weights:
            raise ValueError("no weight advice returned")
        return spec_from_advice(factors, advice, profile)
    except Exception as exc:  # the run must go on: priorities are a sound default
        spec, _ = spec_from_advice(factors, None, profile)
        return spec, [f"Weight advice unavailable ({type(exc).__name__}); weights follow the analyst's priorities."]
