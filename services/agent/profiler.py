"""business-profiler: any intake -> DemandModel (customers, competitors, space) + OpenStreetMap competitor tags.

Nova Pro proposes; Python keeps only valid choices (known demand columns, well-formed OSM tags, CoStar space uses)
and falls back to safe defaults. Healthcare practices with a specialty pack keep pack-defined demand (returns None).
"""

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from contracts.models import DemandModel, DemandSegment, PracticeProfile
from services.agent import llm
from services.agent.tools.geomath import DEMAND_COLUMNS
from services.agent.tools.ingest import OSM_TAG
from services.agent.tools.packs import pack_for

SPACE_USES = ("Office", "OfficeMedical", "Medical", "Retail", "OfficeRetail", "Industrial", "Flex")
DEFAULT = DemandModel(segments=[DemandSegment(feature_id="pop_total", label="people", weight=1.0)],
                      demand_label="potential customers", space_uses=["Retail", "Office", "OfficeRetail", "Flex"])

PROMPT = (
    "You profile a business that wants to open a new location in Houston, Texas. Decide:\n"
    "1. segments: which groups of people are its customers, chosen ONLY from the DEMAND COLUMNS below, with a "
    "relative weight (1.0 = core customers, 0.3-0.7 = secondary). 1 to 3 segments.\n"
    "2. demand_label: plural noun for what the customer count measures, e.g. 'renter households', 'children', "
    "'workers', 'households'.\n"
    "3. competitor_osm_tags: OpenStreetMap key=value tags for places that compete with it, e.g. shop=laundry, "
    "amenity=cafe, amenity=school, amenity=kindergarten, amenity=childcare, leisure=fitness_centre, "
    "amenity=restaurant, amenity=fast_food, shop=hairdresser, shop=beauty, amenity=pharmacy, amenity=dentist, "
    "amenity=clinic, amenity=veterinary, shop=supermarket, shop=convenience. Up to 4 tags.\n"
    "4. competitor_label: plural plain-language name of those competitors.\n"
    "5. space_uses: which commercial space types fit, from: " + ", ".join(SPACE_USES) + ".\n"
    "6. trip: how far customers usually travel: neighborhood (walk or a few minutes: laundromat, coffee shop, "
    "convenience store), local (a short drive: tutoring, gym, salon, childcare), destination (worth a longer drive: "
    "specialist clinic, furniture store).\n"
    "7. rationale: one sentence. No numbers about the market."
)


class SegmentDraft(BaseModel):
    feature_id: str
    label: str = ""
    weight: float = 1.0


class ProfileDraft(BaseModel):
    segments: list[SegmentDraft] = Field(default_factory=list)
    demand_label: str = "potential customers"
    competitor_osm_tags: list[str] = Field(default_factory=list)
    competitor_label: str = "competitors"
    space_uses: list[str] = Field(default_factory=list)
    trip: str = "local"
    rationale: str = ""


def is_healthcare(profile: PracticeProfile) -> bool:
    """Healthcare practices (a specialty and no other business type) use their curated pack."""
    return bool(profile.specialty) and not profile.business_type and pack_for(profile).id.startswith("healthcare.")


def validate(draft: ProfileDraft) -> tuple[DemandModel, list[str], list[str]]:
    """(DemandModel, valid OSM tags, notes about what was dropped)."""
    notes = []
    segments = []
    for s in draft.segments[:3]:
        if s.feature_id in DEMAND_COLUMNS:
            segments.append(DemandSegment(feature_id=s.feature_id, label=s.label or DEMAND_COLUMNS[s.feature_id],
                                          weight=min(1.0, max(0.1, s.weight))))
        else:
            notes.append(f"ignored unknown demand column {s.feature_id!r}")
    tags = [t.strip() for t in draft.competitor_osm_tags if OSM_TAG.match(t.strip())][:4]
    if len(tags) < len(draft.competitor_osm_tags):
        notes.append("ignored malformed OpenStreetMap tags")
    uses = [u for u in draft.space_uses if u in SPACE_USES] or list(DEFAULT.space_uses)
    model = DemandModel(
        segments=segments or list(DEFAULT.segments),
        demand_label=(draft.demand_label or DEFAULT.demand_label).strip()[:40],
        competitor_label=(draft.competitor_label or "competitors").strip()[:60],
        space_uses=uses, rationale=draft.rationale[:300],
        trip=draft.trip if draft.trip in ("neighborhood", "local", "destination") else "local",  # type: ignore[arg-type]
    )
    return model, tags, notes


async def profile_business(profile: PracticeProfile, meter: llm.UsageMeter | None = None) -> tuple[DemandModel | None, list[str], list[str]]:
    if is_healthcare(profile):
        return None, [], [f"Healthcare practice: demand and competitors from pack {pack_for(profile).id}."]
    columns = "\n".join(f"- {k}: {v}" for k, v in DEMAND_COLUMNS.items())
    intake = (f"BUSINESS: {profile.business_type or profile.specialty or profile.name}\nNAME: {profile.name}\n"
              f"CUSTOMERS (owner's words): {profile.customers or '-'}\nSERVICES: {', '.join(profile.services) or '-'}\n"
              f"NOTES: {profile.free_text or '-'}\n\nDEMAND COLUMNS:\n{columns}")
    try:
        out = await llm.get_chat_model("factor_analyst", meter).with_structured_output(ProfileDraft).ainvoke(
            [SystemMessage(PROMPT), HumanMessage(intake)])
        draft = out if isinstance(out, ProfileDraft) else ProfileDraft()
    except Exception as exc:  # never block a run on the profiler; defaults still produce a ranking
        return DEFAULT, [], [f"profiler failed ({type(exc).__name__}); using defaults"]
    model, tags, notes = validate(draft)
    return model, tags, notes
