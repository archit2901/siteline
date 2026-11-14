"""Geo tools as the agents see them: short text summaries, full results stay in Python."""

from contracts.models import PracticeProfile
from services.agent.tools.packs import pack_for

# Building-level facts come from the site data (CoStar) and feed site quality, not zone weights.
SITE_FEATURES = {
    "site:parking": "parking spaces per 1,000 sq ft at the building (CoStar)",
    "site:ground_floor": "ground-floor, step-free space (CoStar floor)",
    "site:rent": "asking rent per sq ft (CoStar)",
    "site:size": "space size fits the business's sq ft range (CoStar)",
}


def stock_feature_ids(profile: PracticeProfile) -> set[str]:
    return {f.id for f in pack_for(profile).features}


def stock_feature_summary(profile: PracticeProfile) -> str:
    """One line per stock feature available for this practice's pack, plus the site-level features."""
    lines = []
    for f in pack_for(profile).features:
        sign = "more is better" if f.direction == 1 else "less is better"
        lines.append(f"- {f.id}: {f.label or f.id} ({f.unit or 'value'}; pack default: {sign})")
    lines += [f"- {fid}: {label}" for fid, label in SITE_FEATURES.items()]
    return "\n".join(lines)
