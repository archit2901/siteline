"""market-modeler weights: validation of model advice, fallbacks, and no model-written numbers."""

import asyncio

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel

from contracts.models import Factor, PracticeProfile
from services.agent.tools.weights import FactorWeight, WeightAdvice, advise_weights, spec_from_advice


def factor(fid: str, feature: str | None, priority: str = "medium", status: str = "covered", direction: int = 1) -> Factor:
    return Factor(id=fid, factor=fid.replace("_", " "), why=f"why {fid}", proxy="p", direction=direction,  # type: ignore[arg-type]
                  priority=priority, stock_covered=status == "covered", feature_id=feature, status=status)  # type: ignore[arg-type]


FACTORS = [factor("kids", "pop_u18_within_5km", "high"), factor("transit", "found_transit", "high", "found", -1),
           factor("growth", "pop_growth_pct", "low"), factor("unmeasured", None, "high", "dropped")]


def test_advice_becomes_normalized_weights_with_reasons() -> None:
    advice = WeightAdvice(weights=[FactorWeight(factor_id="kids", importance=10, why="Your patients are all children."),
                                   FactorWeight(factor_id="transit", importance=5, why="Many of your families ride the bus."),
                                   FactorWeight(factor_id="growth", importance=5, why="New suburbs bring young families.")])
    spec, notes = spec_from_advice(FACTORS, advice)
    assert notes == []
    assert spec.weights == {"pop_u18_within_5km": 0.7915, "found_transit": 0.1042, "pop_growth_pct": 0.1042}  # convex
    assert abs(sum(spec.weights.values()) - 1) < 1e-3
    assert spec.rationale["found_transit"] == "Many of your families ride the bus."
    assert spec.directions["found_transit"] == -1
    assert "unmeasured" not in str(spec.weights)  # dropped factors are never scored


def test_bad_items_fall_back() -> None:
    advice = WeightAdvice(weights=[FactorWeight(factor_id="kids", importance=99, why="About 40,000 kids live nearby."),
                                   FactorWeight(factor_id="ghost", importance=7, why="Not a real factor.")])
    spec, notes = spec_from_advice(FACTORS, advice)
    assert spec.rationale["pop_u18_within_5km"] == "why kids"  # digits in a model sentence are never shown
    assert any("transit" in n for n in notes)  # missing items use the analyst's priority, and say so
    assert spec.weights["pop_u18_within_5km"] > spec.weights["pop_growth_pct"]  # clamped 10 vs low priority 3


def test_model_failure_uses_priorities() -> None:
    broken = GenericFakeChatModel(messages=iter([]))
    spec, notes = asyncio.run(advise_weights(PracticeProfile(name="x", specialty="pediatric_dentistry"), FACTORS, model=broken))
    assert notes and "priorities" in notes[0]
    assert spec.weights["pop_u18_within_5km"] == spec.weights["found_transit"] > spec.weights["pop_growth_pct"]


def test_expert_rules_put_orthopedic_distance_far_above_flood() -> None:
    """Same importance from the model; the expert rules still make competition distance dominate flood risk."""
    from contracts.models import PracticeProfile

    p = PracticeProfile(name="O", specialty="orthopedics", free_text="[functions] Office functions: Clinic visits 10")
    fs = [factor("comp", "n_orthopedics_within_10km", "high"), factor("flood", "flood_share", "medium")]
    fs[0] = fs[0].model_copy(update={"factor": "Few orthopedic competitors nearby"})
    fs[1] = fs[1].model_copy(update={"factor": "Low flood hazard"})
    advice = WeightAdvice(weights=[FactorWeight(factor_id="comp", importance=8, why="Your referrals stay local."),
                                   FactorWeight(factor_id="flood", importance=6, why="Your patients drive in storms.")])
    spec, _ = spec_from_advice(fs, advice, p)
    assert spec.weights["n_orthopedics_within_10km"] > 10 * spec.weights["flood_share"]
