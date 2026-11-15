"""Expert rules: orthopedic site-selection knowledge from a practitioner, kept apart from what the model knows.

Each rule says, in the expert's terms, what should be looked for or avoided, and does something concrete:
- promotes or dampens matching factors in the weights (TIER),
- sends a scout for a point layer the stock data does not have (free-standing PT clinics, urgent cares, imaging
  centers, primary care groups, other orthopedic offices; see tools/cms_points.py),
- or sets a space rule (visibility, parking, size, hospital proximity).

The owner picks which functions the new office will have and how much each matters (0-10). Functions decide
which rules fire: a PT gym that matters little turns a nearby free-standing PT clinic from a competitor into a
referral partner; urgent care makes visibility essential; a surgery center pulls the site toward a hospital.

provenance() labels every scored factor "expert" (a rule backs it) or "ai" (general model knowledge), written to
expert.json so the UI can show which choices came from which.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from pydantic import BaseModel, Field

from contracts.models import DatasetCandidate, FeatureSpec, Factor, PracticeProfile
from services.agent.scout import ScoutResult
from services.agent.tools import cms_points
from services.agent.tools.packs import covers_teams, is_orthopedic, orthopedic_focus

# Office functions the owner can open, in intake order. Labels are what the form shows and free_text stores.
FUNCTIONS: dict[str, str] = {
    "clinic": "Clinic visits",
    "xray": "X-ray",
    "mri": "MRI / imaging",
    "pt": "Physical therapy",
    "asc": "Surgery center (ASC)",
    "urgent": "Urgent care / walk-in",
}
ORG_TYPES = ["Private practice", "Physician group (multi-site)", "Hospital / health system outpatient",
             "Academic medical center", "Private equity-backed platform"]
# Planning assumptions per function for the space suggestion: (sq ft, parking spaces per 1,000 sq ft).
SPACE: dict[str, tuple[int, float]] = {
    "clinic": (1_600, 5.0),  # per physician: about 3 exam rooms each plus waiting, cast room, staff
    "xray": (300, 5.0), "mri": (1_000, 4.0), "pt": (2_500, 5.0), "asc": (9_000, 5.0), "urgent": (3_000, 6.0),
}
HIGH, LOW = 6, 4  # function weight: >= HIGH is a priority, <= LOW is a nice-to-have


def _answer(profile: PracticeProfile, qid: str) -> str:
    m = re.search(rf"^\[{qid}\][^:]*: (.+)$", profile.free_text or "", re.M)
    return m.group(1) if m else ""


def functions_of(profile: PracticeProfile) -> dict[str, int]:
    """'Clinic visits 10, Physical therapy 3' -> {'clinic': 10, 'pt': 3}. Unweighted names count as 5."""
    said = _answer(profile, "functions")
    out: dict[str, int] = {}
    for part in re.split(r",\s*(?![^()]*\))", said):
        m = re.match(r"\s*(.+?)\s*(?:[:=]\s*)?(\d{1,2})?\s*$", part)
        if not m:
            continue
        name, weight = m.group(1).lower(), int(m.group(2)) if m.group(2) else 5
        key = next((k for k, label in FUNCTIONS.items() if label.lower() == name or name.startswith(k)), None)
        if key is None:
            key = next((k for k, pat in _FUNCTION_WORDS.items() if re.search(pat, name)), None)
        if key is not None and weight > 0:
            out[key] = min(10, weight)
    return out


_FUNCTION_WORDS = {"clinic": r"clinic|office visit", "xray": r"x-?ray", "mri": r"mri|imaging", "pt": r"physical|therap|\bpt\b",
                   "asc": r"surgery|surgical|\basc\b|\bor\b", "urgent": r"urgent|walk"}


def functions_text(weights: dict[str, int]) -> str:
    return ", ".join(f"{FUNCTIONS[k]} {v}" for k, v in weights.items() if k in FUNCTIONS and v > 0)


def suggest_space(weights: dict[str, int], physicians: float) -> tuple[int, int, float]:
    """(min sq ft, max sq ft, parking per 1,000 sq ft) for the chosen functions. Same numbers as the form."""
    if not weights:
        return 0, 0, 0.0
    total = sum(SPACE[k][0] * (max(1.0, physicians) if k == "clinic" else 1) for k in weights)
    parking = max(SPACE[k][1] for k in weights)
    return int(round(total * 0.7, -2)), int(round(total * 1.3, -2)), parking  # phased or shared space to a roomy fit


# ---------------------------------------------------------------------------
# rules
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Rule:
    id: str
    text: str  # the expert's knowledge, in plain words (shown in the UI)
    applies: Callable[[PracticeProfile, dict[str, int]], bool]
    match: str = ""  # regex over a factor's id, name, proxy and feature id
    tier: float = 1.0  # weight multiplier for matching factors (>1 promote, <1 dampen)


def _ortho(p: PracticeProfile, _w: dict[str, int]) -> bool:
    return is_orthopedic(p)


def _w(key: str, lo: int = 1, hi: int = 10) -> Callable[[PracticeProfile, dict[str, int]], bool]:
    return lambda p, w: is_orthopedic(p) and lo <= w.get(key, 0) <= hi


def _sports(p: PracticeProfile, _w: dict[str, int]) -> bool:
    return is_orthopedic(p) and (covers_teams(p) or bool({"sports", "pediatric"} & set(orthopedic_focus(p))))


RULES: list[Rule] = [
    Rule("competition_distance", "Distance from other orthopedic centers decides a new office more than any hazard "
         "or amenity: patients and referrers stay with the group that is closer.", _ortho,
         r"ortho\w*.{0,40}(compet|clinician|office|group|practice|surgeon)|compet\w*.{0,40}ortho|other ortho|"
         r"n_orthopedics|x_ortho_offices", 3.0),
    Rule("referral_primary_care", "Being near primary care groups puts the office inside a referral base.", _ortho,
         r"primary care|referr|pcp|n_primary_care|x_primary_care", 2.2),
    Rule("demographics_income", "Demographics and median income decide payer mix: commercial and Medicare patients "
         "keep an orthopedic office full.", _ortho,
         r"income|median|commercial|medicare|insur|payer|pop_45|pop_65|45\+|65\+|\bage[sd]?\b|demograph", 1.6),
    Rule("currently_serving", "Count where you already serve: a new office should not sit on your own patients.", _ortho,
         r"existing|own office|current office|cannibal|already serve", 1.6),
    Rule("high_schools", "Local high schools feed sports injuries: athletic trainers send injured athletes to the "
         "closest doctor they trust.", _sports, r"high school|school|student|athlet|team|hs_enrollment|u18|youth", 2.0),
    Rule("pt_compete", "If physical therapy matters to this office, stay away from free-standing PT clinics: they "
         "compete for the same post-op and sports patients.", _w("pt", HIGH), r"x_pt_clinics", 1.8),
    Rule("pt_partner", "If physical therapy matters little, a free-standing PT clinic next door is a referral partner; "
         "you may drop some of your own PT and send patients there.", _w("pt", 0, LOW), r"x_pt_clinics", 1.4),
    Rule("urgent_visible", "Walk-in urgent care must be visible from a busy road so people can come; scheduled care "
         "like PT can be lower cost and tucked away.", _w("urgent", 5), r"traffic|visib|frontage|road|aadt|x_urgent", 2.0),
    Rule("urgent_referrer", "Without your own walk-in care, nearby urgent cares are referrers: they splint fractures "
         "and send them to the orthopedist.", _w("urgent", 0, 4), r"x_urgent", 1.4),
    Rule("imaging", "With your own MRI, other imaging centers nearby compete for the same scans; without it, a nearby "
         "imaging center is where your patients go.", lambda p, w: is_orthopedic(p), r"imaging|mri|x_imaging", 1.3),
    Rule("asc_hospital", "A surgery center needs a hospital close by (transfer agreement, surgeons' OR days), and the "
         "larger footprint needs more parking and a freestanding or ground-floor site.", _w("asc", 5),
         r"hospital|surgery cent|asc|operat|x_partner_hospitals|partner_hospitals", 2.0),
    Rule("hazards_secondary", "Hazards, heat, walkability and transit are secondary for a referral-driven clinic "
         "where nearly everyone drives.", _ortho,
         r"flood|hazard|\bheat\b|tree canopy|walkab|transit|\bbus\b|bike|crime|noise|air quality|green space|"
         r"language|spanish|limited english", 0.35),
]


def active_rules(profile: PracticeProfile) -> list[Rule]:
    w = functions_of(profile)
    return [r for r in RULES if r.applies(profile, w)]


def _blob(f: Factor) -> str:
    return f"{f.id} {f.factor} {f.proxy} {f.feature_id or ''}".lower()


def rules_for(profile: PracticeProfile, factor: Factor) -> list[Rule]:
    return [r for r in active_rules(profile) if r.match and re.search(r.match, _blob(factor), re.I)]


def tier(profile: PracticeProfile, factor: Factor) -> float:
    """Weight multiplier from the expert rules that match a factor (promotions multiply; a dampen wins outright)."""
    rules = rules_for(profile, factor)
    damp = [r.tier for r in rules if r.tier < 1]
    if damp and not [r for r in rules if r.tier > 1 and r.id != "demographics_income"]:
        return min(damp)
    out = 1.0
    for r in rules:
        if r.tier > 1:
            out = max(out, r.tier)
    return out


# ---------------------------------------------------------------------------
# function scouts: the agent finds the practices that matter for the chosen functions
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Scout:
    layer: str
    factor_id: str
    factor: str
    why: str
    agg: str
    direction: int
    rule: str


def scouts_for(profile: PracticeProfile) -> list[Scout]:
    if not is_orthopedic(profile):
        return []
    w = functions_of(profile)
    out = [
        Scout("ortho_offices", "x_ortho_offices", "Far from other orthopedic offices",
              "Patients and referrers stay with the closest group they know.", "distance_to_nearest", 1,
              "competition_distance"),
        Scout("primary_care_groups", "x_primary_care", "Primary care groups nearby (referral base)",
              "Primary care physicians refer most new orthopedic patients.", "count_within", 1, "referral_primary_care"),
    ]
    if w.get("pt", 0) >= HIGH:
        out.append(Scout("freestanding_pt", "x_pt_clinics", "Few free-standing PT clinics nearby",
                         "Your PT is a priority, and they compete for the same patients.", "count_within", -1, "pt_compete"))
    else:
        out.append(Scout("freestanding_pt", "x_pt_clinics", "A free-standing PT clinic close by (referral partner)",
                         "Your PT matters little here, so a PT clinic next door is a partner, not a rival.",
                         "distance_to_nearest", -1, "pt_partner"))
    if w.get("urgent", 0) >= 5:
        out.append(Scout("urgent_care", "x_urgent", "Few urgent cares nearby",
                         "Your walk-in care competes with them for the same injuries.", "count_within", -1, "urgent_visible"))
    else:
        out.append(Scout("urgent_care", "x_urgent", "Urgent cares nearby (they refer fractures)",
                         "They splint and refer; without your own walk-in, they send you patients.", "count_within", 1,
                         "urgent_referrer"))
    if w.get("mri", 0) >= 5:
        out.append(Scout("imaging_centers", "x_imaging", "Few outpatient imaging centers nearby",
                         "Your own MRI competes with them for the same scans.", "count_within", -1, "imaging"))
    return out


SAME = {"ortho_offices": r"ortho\w*.{0,40}(compet|clinic|office|group|practice)|compet\w*.{0,40}ortho",
        "primary_care_groups": r"primary care|referral|\bpcp", "freestanding_pt": r"physical therap|\bpt\b|physio",
        "urgent_care": r"urgent|walk-in", "imaging_centers": r"imaging|\bmri\b|radiolog"}


def function_scouts(profile: PracticeProfile, factors: list[Factor], run_tag: str, keep: set[str] | None = None
                    ) -> tuple[list[Factor], list[ScoutResult], list[str]]:
    """(factors with the expert factors added, scout results, notes)."""
    results, notes = [], []
    have = {f.id for f in factors}
    for s in scouts_for(profile):
        if s.factor_id in have:
            continue
        # the analyst's unmeasured factor for the same thing gives way to the expert one (no duplicate scout)
        factors = [f for f in factors if f.feature_id is not None or f.id in (keep or set())
                   or not re.search(SAME[s.layer], _blob(f), re.I)]  # never drop a factor another scout measures
        label = cms_points.LAYERS[s.layer]
        proxy = (f"Distance to the nearest {label.lower()[:-1]} (CMS)" if s.agg == "distance_to_nearest"
                 else f"{label} within about 5 km (CMS)")
        f = Factor(id=s.factor_id, factor=s.factor, why=s.why, proxy=proxy, direction=s.direction,  # type: ignore[arg-type]
                   priority="high")
        factors = [*factors, f]
        url = cms_points.layer_url(s.layer)
        spec = FeatureSpec(id=f"{s.factor_id}_{run_tag}", label=s.factor, source_id="cms.doctors_practices",
                           source_url=url, format="json", geo_level="point", geo_key="_lat,_lon",  # type: ignore[arg-type]
                           agg=s.agg, radius_min=7.0 if s.agg == "count_within" else None,  # type: ignore[arg-type]
                           direction=s.direction, unit="km" if s.agg == "distance_to_nearest" else "count",  # type: ignore[arg-type]
                           layer="agent_found", provenance_url=cms_points.CMS_URL)  # type: ignore[arg-type]
        cand = DatasetCandidate(id=f"cand_{s.factor_id}", factor_id=f.id, url=url, title=f"CMS: {label}",  # type: ignore[arg-type]
                                publisher="CMS Doctors and Clinicians (practices grouped by address)", format="json",
                                geo_level="point", why=s.why, status="valid")
        results.append(ScoutResult(f.id, cand, 1, spec=spec))
        notes.append(f"{label}: found by grouping CMS clinicians by practice address.")
    return factors, results, notes


# ---------------------------------------------------------------------------
# provenance
# ---------------------------------------------------------------------------
class FactorSource(BaseModel):
    factor_id: str
    feature_id: str | None = None
    source: str  # "expert" | "ai"
    rules: list[str] = Field(default_factory=list)  # rule ids
    tier: float = 1.0


class ExpertReport(BaseModel):
    functions: dict[str, int] = Field(default_factory=dict)
    org_type: str = ""
    rules: dict[str, str] = Field(default_factory=dict)  # active rule id -> text
    factors: list[FactorSource] = Field(default_factory=list)
    space: dict[str, float] = Field(default_factory=dict)


# factors measured by an expert scout or a named partner carry that rule, not whatever their words happen to match
PARTNER_RULE = {"partner_schools_": "high_schools", "partner_hospitals_": "asc_hospital"}


def provenance(profile: PracticeProfile, factors: list[Factor]) -> ExpertReport:
    w = functions_of(profile)
    scouts = {s.factor_id: s.rule for s in scouts_for(profile)}
    rows = []
    for f in factors:
        fixed = scouts.get(f.id) or next((rule for prefix, rule in PARTNER_RULE.items()
                                          if (f.feature_id or "").startswith(prefix)), None)
        rules = [fixed] if fixed else [r.id for r in rules_for(profile, f)]
        boost = tier(profile, f)
        promoted = bool(rules) and (bool(fixed) or boost >= 1)  # a factor the rules dampen stays the model's
        rows.append(FactorSource(factor_id=f.id, feature_id=f.feature_id, source="expert" if promoted else "ai",
                                 rules=rules, tier=round(boost, 2)))
    lo, hi, parking = suggest_space(w, _physicians(profile))
    return ExpertReport(functions=w, org_type=_answer(profile, "org_type"),
                        rules={r.id: r.text for r in active_rules(profile)}, factors=rows,
                        space={"min_sqft": lo, "max_sqft": hi, "parking_per_1000sf": parking} if w else {})


def _physicians(profile: PracticeProfile) -> float:
    from services.agent.tools.geomath import site_providers

    return site_providers(profile)


# ---------------------------------------------------------------------------
# program options: smaller offices that leave some functions elsewhere
# ---------------------------------------------------------------------------
# Where a function goes when this office leaves it out (shown with the option).
ELSEWHERE = {
    "asc": "operate at your hospital or a partner ASC (most ASCs are their own building)",
    "pt": "refer to a free-standing PT clinic nearby, or keep PT at an existing office",
    "mri": "send scans to a nearby imaging center",
    "urgent": "keep walk-in hours at an existing office",
    "xray": "send films out (rarely worth it: X-ray is small and drives same-visit decisions)",
}
SHORT = {"clinic": "Clinic", "xray": "X-ray", "mri": "MRI", "pt": "PT", "asc": "ASC", "urgent": "Walk-in"}
BIG_FIRST = ["asc", "pt", "mri", "urgent"]  # dropped in this order, lowest importance first among equals


class ProgramOption(BaseModel):
    id: str
    label: str
    functions: dict[str, int]
    left_out: dict[str, str] = Field(default_factory=dict)  # function label -> where it goes instead
    min_sqft: int
    max_sqft: int
    parking_per_1000sf: float
    sites: list[dict] = Field(default_factory=list)  # filled by the pipeline: best lease spaces for this program


def program_options(profile: PracticeProfile) -> list[ProgramOption]:
    """The full program, then smaller ones: drop the surgery center first (usually its own building), then the
    function the owner weighted lowest among PT, MRI and walk-in care, down to a clinic + X-ray core."""
    w = functions_of(profile)
    if not w:
        return []
    physicians = _physicians(profile)
    out: list[ProgramOption] = []
    seen: set[frozenset[str]] = set()
    current = dict(w)

    def add(label: str) -> None:
        key = frozenset(current)
        if not current or key in seen:
            return
        seen.add(key)
        lo, hi, parking = suggest_space(current, physicians)
        gone = {FUNCTIONS[k]: ELSEWHERE.get(k, "elsewhere") for k in w if k not in current}
        name = " + ".join(SHORT[k] for k in FUNCTIONS if k in current)
        out.append(ProgramOption(id=f"opt{len(out) + 1}", label=name if out else f"{name} (everything)", functions=dict(current), left_out=gone,
                                 min_sqft=lo, max_sqft=hi, parking_per_1000sf=parking))

    add("Everything in one building")
    if 0 < w.get("pt", 0) <= LOW:  # PT matters little: the same office without it, PT sent to a nearby clinic
        current.pop("pt")
        add("")
        current = dict(w)
    if "asc" in current:
        current.pop("asc")
        add("Without the surgery center")
    for k in sorted([k for k in BIG_FIRST[1:] if k in current], key=lambda k: current[k]):
        current.pop(k)
        add("")
    core = {k: v for k, v in w.items() if k in ("clinic", "xray")}
    if core and frozenset(core) not in seen:
        current = core
        add("Clinic + X-ray core")
    return out


def option_sites(ranked: list, sites: dict, top: int = 3) -> list[dict]:
    """Best eligible spaces for a program, with yearly asking rent when the listing states it."""
    out = []
    for r in ranked:
        s = sites.get(r.site_id)
        if r.excluded_reason is not None or s is None:
            continue
        rent = round(s.rent_psf * s.sqft) if s.rent_psf and s.sqft else None
        out.append({"site_id": r.site_id, "address": s.address, "sqft": s.sqft, "rent_psf": s.rent_psf,
                    "annual_rent": rent, "net_new": r.net_new_patients, "final_score": r.final_score})
        if len(out) == top:
            break
    return out


def nearest_pt(lat: float, lon: float) -> tuple[str, float] | None:
    """The closest free-standing PT clinic (CMS practices) and its straight-line distance in km."""
    import numpy as np

    from services.agent.tools.geomath import pairwise_km

    try:
        rows = cms_points.rows("freestanding_pt")
    except ValueError:
        return None
    if not rows:
        return None
    km = pairwise_km(np.array([lat]), np.array([lon]), np.array([r["_lat"] for r in rows]), np.array([r["_lon"] for r in rows]))[0]
    i = int(km.argmin())
    return rows[i]["name"].title(), float(km[i])
