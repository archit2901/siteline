"""weights_from_priorities: factor priorities -> ScoringSpec (high 3, medium 2, low 1, normalized)."""

from contracts.models import Factor, ScoringSpec

POINTS = {"high": 3, "medium": 2, "low": 1}
SCORABLE = ("covered", "found", "proxy")


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
