"""Stock packs (packs/*.yaml): loading with `extends`, and the feature catalog they define."""

import re
from functools import lru_cache
from pathlib import Path

import yaml

from contracts.models import PackFeature, PracticeProfile, RegionFeature, StockPack

ROOT = Path(__file__).resolve().parents[3]
PACKS = ROOT / "packs"
BASE = "general.base"
PROVENANCE = {
    "acs": "https://www.census.gov/data/developers/data-sets/acs-5year.html",
    "acs.sf": "https://www.census.gov/programs-surveys/acs/data/summary-file.html",
    "lodes": "https://lehd.ces.census.gov/data/",
    "cms.doctors": "https://data.cms.gov/provider-data/dataset/mj5m-pzi6",
    "cms.hospitals": "https://data.cms.gov/provider-data/dataset/xubh-q36u",
    "usda.fara": "https://www.ers.usda.gov/data-products/food-access-research-atlas/",
    "hrsa": "https://data.hrsa.gov/data/download?data=HCC",
    "tea": "https://services.arcgis.com/NummVBqZSIJKUeVR/arcgis/rest/services/COH_Schools_view/FeatureServer/0",
}
# Orthopedic subspecialties: (pattern over the focus answer, services and name) -> pack healthcare.orthopedics.<variant>
# and playbook orthopedics_<variant>. Order is the order a mixed practice is described in.
ORTHO_VARIANTS = [
    (r"sports?[- ]med|athlet|\bacl\b|concussion|sideline|sports injur", "sports"),
    (r"joint|arthroplast|\bhips?\b|\bknees?\b|replacement|arthritis|shoulder", "joint"),
    (r"spine|spinal|back pain|\bneck\b|scoliosis surgery", "spine"),
    (r"\bhands?\b|wrist|upper extremity|elbow|carpal", "hand"),
    (r"\bfoot\b|\bfeet\b|ankle", "foot_ankle"),
    (r"pediatric ortho|children|\bkids?\b|scoliosis|clubfoot", "pediatric"),
    (r"workers'? comp|occupational|work injur|on-the-job", "occupational"),
]
_ORTHO = r"ortho|sports med|joint replacement|spine|fracture"
COMMUNITY_HEALTH = "healthcare.community_health"
_CHC = r"community health|health center|fqhc|federally qualified|look-?alike|free clinic|charity clinic|safety[- ]net|sliding[- ]fee"
_NONPROFIT = r"non-?profit|not-for-profit|charit|community|ministry|safety[- ]net|underserved|uninsured|low-income|free\b"
_CLINIC = r"clinic|medical|health|doctor|physician|primary care|dental|patients"

# Business type words -> sector pack (first match wins; checked on business_type, then the name and services).
SECTORS: list[tuple[str, tuple[str, ...]]] = [
    ("nonprofit.food_bank", ("food bank", "food pantry", "pantry", "food distribution", "soup kitchen",
                             "meal program", "meals on wheels", "mobile pantry", "community fridge")),
    ("nonprofit.base", ("nonprofit", "non-profit", "not-for-profit", "charity", "ministry", "shelter", "outreach",
                        "social service", "community center", "case management", "homeless", "refugee", "food insecurity")),
    ("education.childcare", ("child care", "childcare", "daycare", "day care", "preschool", "pre-k", "montessori",
                             "early learning", "after-school", "after school", "tutoring", "learning center")),
    ("fitness.base", ("gym", "fitness", "yoga", "pilates", "crossfit", "martial arts", "boxing", "climbing",
                      "dance studio", "cycling studio", "personal training")),
    ("food.restaurant", ("restaurant", "cafe", "café", "coffee", "bakery", "pizza", "taqueria", "food truck",
                         "bar", "brewery", "diner", "juice", "boba", "tea shop")),
    ("retail.services", ("laundromat", "laundry", "dry clean", "salon", "barber", "nail", "spa", "convenience",
                         "phone repair", "pawn", "tailor", "car wash", "pet grooming", "retail", "store", "shop")),
]


@lru_cache
def _raw(pack_id: str) -> StockPack:
    path = PACKS / f"{pack_id}.yaml"
    if not path.is_file():
        raise KeyError(f"no pack {pack_id!r}")
    return StockPack.model_validate(yaml.safe_load(path.read_text()))


@lru_cache
def load_pack(pack_id: str) -> StockPack:
    """The pack merged with its `extends` chain; features with the same id override the parent's."""
    pack = _raw(pack_id)
    if not pack.extends:
        return pack
    parent = load_pack(pack.extends)
    features = {f.id: f for f in parent.features} | {f.id: f for f in pack.features}
    return pack.model_copy(update={
        "features": list(features.values()),
        "demand_age_bands": pack.demand_age_bands or parent.demand_age_bands,
        "visit_rates": parent.visit_rates | pack.visit_rates,
        "site_rules": parent.site_rules | pack.site_rules,
        "factor_hints": parent.factor_hints + pack.factor_hints,
    })


def is_community_health(text: str) -> bool:
    """A nonprofit / safety-net clinic: healthcare AND nonprofit at once (FQHC, Look-Alike, free or charity clinic,
    "nonprofit clinic"). Checked before either sector alone, which would each miss half of what matters."""
    import re

    low = text.lower().replace("_", " ")
    return bool(re.search(_CHC, low) or (re.search(_NONPROFIT, low) and re.search(_CLINIC, low)
                                          and re.search(r"non-?profit|not-for-profit|charit|free clinic|safety|uninsured|underserved", low)))


def sector_for(text: str) -> str | None:
    """The sector pack a business description names, or None (whole-word match, case-insensitive)."""
    import re

    if is_community_health(text):
        return COMMUNITY_HEALTH

    low = f" {text.lower()} "
    for pack_id, words in SECTORS:
        if any(re.search(rf"(?<![a-z]){re.escape(w)}(?![a-z])", low) for w in words):
            return pack_id
    return None


def _answer(profile: PracticeProfile, qid: str) -> str:
    m = re.search(rf"^\[{qid}\][^:]*: (.+)$", profile.free_text or "", re.M)
    return m.group(1) if m else ""


def is_orthopedic(profile: PracticeProfile) -> bool:
    return profile.specialty == "orthopedics" or (not profile.specialty and bool(
        re.search(_ORTHO, " ".join([profile.business_type, profile.name]), re.I)))


def orthopedic_focus(profile: PracticeProfile) -> list[str]:
    """Subspecialties an orthopedic practice says it does, in the order it lists them: the intake's focus answer if
    given (the owner's words win), else its services and name. Empty for general orthopedics and fracture care."""
    if not is_orthopedic(profile):
        return []
    said = _answer(profile, "ortho_focus")
    text = said or " ".join([profile.name, profile.business_type, *profile.services])
    found = sorted(((m.start(), v) for pattern, v in ORTHO_VARIANTS if (m := re.search(pattern, text, re.I))))
    return list(dict.fromkeys(v for _, v in found))


def covers_teams(profile: PracticeProfile) -> bool:
    """The practice covers a school, college or club team (sideline physicians, athletic trainers, injury clinics)."""
    said = _answer(profile, "team_coverage").strip().lower()
    return bool(said) and not re.match(r"^(no|none|n/?a|not yet|we don'?t)\b", said)


def orthopedic_pack(profile: PracticeProfile) -> str | None:
    """The subspecialty pack: youth-facing focus first (sports, then pediatric) because their age bands and high
    school features cover a mixed practice's adults too; otherwise the first focus listed."""
    focus = orthopedic_focus(profile)
    if not focus:  # a general practice that covers school teams is scored with the sports features
        return "healthcare.orthopedics.sports" if is_orthopedic(profile) and covers_teams(profile) else None
    pick = next((v for v in ("sports", "pediatric") if v in focus), focus[0])
    return f"healthcare.orthopedics.{pick}"


def pack_for(profile: PracticeProfile) -> StockPack:
    """profile.stock_pack, else healthcare.<specialty>, else the sector the business type names, else general.base."""
    if is_community_health(" ".join([profile.business_type, profile.specialty, profile.name, *profile.services])):
        return load_pack(COMMUNITY_HEALTH)
    sector = sector_for(profile.business_type or "") or (
        None if profile.specialty else sector_for(" ".join([profile.name, *profile.services])))
    specialty = (f"healthcare.{profile.specialty}", "healthcare.base") if profile.specialty else (None, None)
    for pack_id in (profile.stock_pack, orthopedic_pack(profile), *specialty, sector, BASE):
        if pack_id:
            try:
                return load_pack(pack_id)
            except KeyError:
                continue
    return load_pack(BASE)


def all_pack_ids() -> list[str]:
    return sorted(p.stem for p in PACKS.glob("*.yaml"))


@lru_cache
def feature_catalog() -> dict[str, PackFeature]:
    """Every stock feature defined by any pack (id -> definition; the first pack wins for label and direction)."""
    catalog: dict[str, PackFeature] = {}
    for pack_id in [BASE, *[p for p in all_pack_ids() if p != BASE]]:
        for feature in _raw(pack_id).features:
            catalog.setdefault(feature.id, feature)
    return catalog


def region_features() -> list[RegionFeature]:
    base_ids = {f.id for f in _raw(BASE).features}
    return [
        RegionFeature(
            id=f.id, label=f.label or f.id, source=f.source, unit=f.unit,
            layer="stock_base" if f.id in base_ids else "stock_pack",
            provenance_url=PROVENANCE.get(f.source, PROVENANCE["acs"]),  # type: ignore[arg-type]
        )
        for f in feature_catalog().values()
    ]
