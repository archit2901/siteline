"""Orthopedic track: subspecialty routing, team coverage, partner scout, and the decision review (no network, no model)."""

import asyncio

import pytest

from contracts.models import Constraints, Factor, FeatureSpec, PracticeProfile, RankedSite, SiteCandidate
from services.agent.factor_analyst import must_keep, playbooks_for
from services.agent.partners import HOSPITAL_WORDS, SCHOOL_WORDS, match_names, partner_scouts
from services.agent.questions import questions_for, sector_of
from services.agent.review import requirements, review, status_of
from services.agent.tools import ingest
from services.agent.tools.geo_client import MockGeo
from services.agent.tools.packs import covers_teams, orthopedic_focus, pack_for

SCHOOLS = ["KLEIN H S", "KLEIN CAIN H S", "KLEIN OAK H S", "KLEIN COLLINS H S", "KATY H S", "TOMPKINS H S", "CY-FAIR H S"]
HOSPITALS = ["TEXAS ORTHOPEDIC HOSPITAL", "METHODIST HOSPITAL", "METHODIST SUGAR LAND HOSPITAL",
             "MEMORIAL HERMANN - KATY HOSPITAL", "MEMORIAL HERMANN HOSPITAL"]


def ortho(focus: str = "", teams: str = "", surgery: str = "", **kw) -> PracticeProfile:
    lines = [f"[ortho_focus] Focus: {focus}" if focus else "", f"[team_coverage] Teams: {teams}" if teams else "",
             f"[surgery_site] Where: {surgery}" if surgery else ""]
    return PracticeProfile(name="Synthetic Ortho", specialty="orthopedics", free_text="\n".join(l for l in lines if l), **kw)


def keep_text(p: PracticeProfile) -> str:
    return " ".join(f for _, f in must_keep(p)).lower()


def test_joint_care_and_sports_medicine_pick_different_factors() -> None:
    joint, sports = ortho("Joint replacement (hip, knee, shoulder)"), ortho("Sports medicine")
    assert pack_for(joint).id == "healthcare.orthopedics.joint" and pack_for(sports).id == "healthcare.orthopedics.sports"
    assert pack_for(joint).demand_age_bands == ["45-64", "65+"] and "0-17" in pack_for(sports).demand_age_bands
    assert "hs_enrollment_within_10km" in {f.id for f in pack_for(sports).features}
    assert "hs_enrollment_within_10km" not in {f.id for f in pack_for(joint).features}
    assert "high school" in keep_text(sports) and "high school" not in keep_text(joint)
    assert "arthritis" in keep_text(joint) and "medicare" in keep_text(joint)
    for p in (joint, sports):  # the core orthopedic gates hold for every variant
        assert "where the surgeons operate" in keep_text(p) and "parking" in keep_text(p)


@pytest.mark.parametrize("focus,variant", [
    ("Spine", "spine"), ("Hand and upper extremity", "hand"), ("Foot and ankle", "foot_ankle"),
    ("Pediatric orthopedics", "pediatric"), ("Workers' comp / occupational injuries", "occupational"),
    ("Joint replacement (hip, knee, shoulder), Sports medicine", "sports"),  # youth-facing pack covers a mixed practice
])
def test_every_subspecialty_has_a_pack_and_playbook(focus: str, variant: str) -> None:
    p = ortho(focus)
    assert pack_for(p).id == f"healthcare.orthopedics.{variant}"
    assert f"orthopedics_{variant}" in playbooks_for(p) and must_keep(p)


def test_team_coverage_adds_the_teams_playbook_and_sports_features() -> None:
    p = ortho(teams="Klein Cain and Klein Oak football, two athletic trainers")
    assert covers_teams(p) and "orthopedics_teams" in playbooks_for(p)
    assert pack_for(p).id == "healthcare.orthopedics.sports"  # no focus given: the sports features score the teams
    assert "schools the practice covers" in keep_text(p)
    for no in ("No", "none", "N/A", "Not yet"):
        assert not covers_teams(ortho(teams=no))
    joint_with_team = ortho("Joint replacement (hip, knee, shoulder)", teams="Katy HS football")
    assert pack_for(joint_with_team).id == "healthcare.orthopedics.joint" and "orthopedics_teams" in playbooks_for(joint_with_team)


def test_intake_routes_orthopedics_to_its_own_questions() -> None:
    assert sector_of("", "orthopedics") == "healthcare.orthopedics" == sector_of("Bayou Sports Medicine")
    ids = [q.id for q in questions_for("", "orthopedics").sector_questions]
    assert {"ortho_focus", "surgery_site", "team_coverage", "capacity", "hours"} <= set(ids)
    assert sector_of("", "cardiology") == "healthcare"
    assert orthopedic_focus(PracticeProfile(name="x", specialty="dialysis", free_text="[ortho_focus] F: Sports medicine")) == []


def test_names_match_longest_first_without_false_hits() -> None:
    assert match_names("Klein Cain and Klein Oak football", SCHOOLS, SCHOOL_WORDS) == ["KLEIN CAIN H S", "KLEIN OAK H S"]
    assert match_names("Katy High School, Tompkins", SCHOOLS, SCHOOL_WORDS) == ["TOMPKINS H S", "KATY H S"]
    assert match_names("we cover Klein HS", SCHOOLS, SCHOOL_WORDS) == ["KLEIN H S"]
    assert match_names("Texas Orthopedic Hospital and Methodist", HOSPITALS, HOSPITAL_WORDS) == [
        "TEXAS ORTHOPEDIC HOSPITAL", "METHODIST HOSPITAL"]
    assert match_names("a hospital", HOSPITALS, HOSPITAL_WORDS) == []


def test_partner_scout_turns_named_partners_into_near_is_better_distances() -> None:
    p = ortho("Sports medicine", teams="Klein Cain and Klein Oak", surgery="Texas Orthopedic Hospital")
    analyst = [Factor(id="hospital_access", factor="Hospital where surgeons operate", why="OR days", proxy="drive time",
                      direction=1, priority="high")]
    factors, results, notes = partner_scouts(p, analyst, "t1", schools=SCHOOLS, hospitals=HOSPITALS)
    specs = {r.factor_id: r.spec for r in results}
    assert set(specs) == {"covered_schools_drive", "hospital_access"}  # the analyst's hospital factor gets the data
    for spec in specs.values():
        assert spec.agg == "distance_to_nearest" and spec.direction == -1 and spec.id.startswith("partner_")
    assert specs["covered_schools_drive"].filter_values == ["KLEIN CAIN H S", "KLEIN OAK H S"]
    assert any(f.id == "covered_schools_drive" and f.direction == -1 for f in factors)
    assert len(notes) == 2 and not any(n.startswith("Could not") for n in notes)
    _, none, missed = partner_scouts(ortho(teams="Springfield Prep"), [], "t2", schools=SCHOOLS, hospitals=HOSPITALS)
    assert none == [] and missed[0].startswith("Could not match")
    hope = PracticeProfile(name="HOPE", specialty="community_health", free_text="[team_coverage] T: Klein Cain")
    assert partner_scouts(hope, [], "t3", schools=SCHOOLS, hospitals=HOSPITALS)[1] == []


def test_named_partner_layers_validate_on_every_named_place_not_five_points() -> None:
    import numpy as np
    spec = FeatureSpec(id="partner_schools_x", label="d", source_id="tea", geo_level="point", geo_key="_lat,_lon",
                       filter_column="School_Name", filter_values=["A", "B"], agg="distance_to_nearest", direction=-1,
                       provenance_url="https://example.org")  # type: ignore[arg-type]
    values = np.ones(10)
    assert ingest.validate(values, spec, 2, [], 2).ok
    assert not ingest.validate(values, spec, 1, [], 1).ok  # one named school missing
    scout = spec.model_copy(update={"id": "found_x"})
    assert not ingest.validate(values, scout, 2, [], 2).ok  # discovered layers still need 5 points


C = Constraints(min_sqft=4000, max_sqft=9000, max_rent_psf=35, ground_floor=True, min_parking_per_1000sf=5)


def site(i: str, **kw) -> SiteCandidate:
    return SiteCandidate(id=i, address=f"{i} Main St", lat=29.98, lon=-95.52, **kw)


def ranked(i: str, net: float, captured: float | None = None, cannibal: float = 0.0, stability: float = 0.9) -> RankedSite:
    return RankedSite(site_id=i, net_new_patients=net, captured_patients=captured or net + cannibal,
                      cannibalized_patients=cannibal, zone_score=0.8, site_quality=0.7, final_score=0.8, stability=stability)


def test_requirements_decide_confirmed_provisional_and_ineligible() -> None:
    p = ortho("Sports medicine", constraints=C)
    full = site("a", sqft=6000, rent_psf=30, floor=1, parking_spaces=40, available=True)
    reqs = {q.name: q.status for q in requirements(full, p, pack_for(p))}
    assert reqs["Size"] == reqs["Rent"] == reqs["Ground floor"] == reqs["Parking"] == "met"
    assert reqs["X-ray room"] == "unknown" and status_of(requirements(full, p, pack_for(p))) == "provisional"
    upstairs = site("b", sqft=6000, rent_psf=30, floor=3, parking_spaces=40, available=True)
    assert status_of(requirements(upstairs, p, pack_for(p))) == "ineligible"
    guessed = site("c", sqft=6000, parking_spaces=40, parking_estimated=True)
    assert {q.name: q.status for q in requirements(guessed, p, pack_for(p))}["Parking"] == "unknown"
    plain = PracticeProfile(name="laundry", business_type="laundromat", constraints=Constraints(min_sqft=1000, max_sqft=3000))
    shop = site("d", sqft=2000, available=True, floor=1, parking_spaces=10)
    assert status_of(requirements(shop, plain, pack_for(plain))) == "confirmed"  # no X-ray or arrival-path gate


def test_a_failed_gate_cannot_lead_and_the_review_says_what_flips_it() -> None:
    p = ortho("Joint replacement (hip, knee, shoulder)", constraints=C, payers_accepted=["medicare", "commercial"])
    sites = {"a": site("a", sqft=6000, rent_psf=30, floor=2, parking_spaces=40, available=True),  # best score, upstairs
             "b": site("b", sqft=6000, rent_psf=30, floor=1, parking_spaces=40, available=True),
             "c": site("c", sqft=5000, rent_psf=25, floor=1, parking_spaces=30, available=True)}
    out = asyncio.run(review(p, pack_for(p), [ranked("a", 1000), ranked("b", 900), ranked("c", 880)], sites, [], MockGeo(), "patients"))
    assert [s.status for s in out.sites] == ["ineligible", "provisional", "provisional"]
    assert out.decision.startswith("Provisional pick: b Main St") and "x-ray room" in out.decision
    assert out.alternative.startswith("Strongest alternative: c Main St")
    assert any("c Main St leads" in c for c in out.what_could_change)
    assert any("within 10%" in c for c in out.what_could_change)  # 900 vs 880
    lead = out.sites[1]
    assert {n.perspective for n in lead.notes} == {"Practice owner", "Patient and caregiver", "Clinicians and operations",
                                                     "Payers, referrers and partners", "Property and implementation",
                                                     "Public access"}
    public = next(n for n in lead.notes if n.perspective == "Public access")
    assert public.status == "not_applicable" and "Medicaid" in public.finding
    patient = next(n for n in lead.notes if n.perspective == "Patient and caregiver")
    assert "65+" in patient.finding  # joint care looks at older adults, not high schools


def test_moving_patients_from_your_own_offices_is_flagged_not_counted_as_growth() -> None:
    p = ortho("Sports medicine", constraints=C)
    sites = {"a": site("a", sqft=6000, rent_psf=30, floor=1, parking_spaces=40, available=True)}
    out = asyncio.run(review(p, pack_for(p), [ranked("a", 600, cannibal=500)], sites, [], MockGeo(), "patients"))
    owner = next(n for n in out.sites[0].notes if n.perspective == "Practice owner")
    assert owner.status == "risk" and "45%" in owner.finding
    patient = next(n for n in out.sites[0].notes if n.perspective == "Patient and caregiver")
    assert "high school students" in patient.finding


def test_changing_the_intake_reroutes_factors() -> None:
    before, after = ortho("Joint replacement (hip, knee, shoulder)"), ortho("Sports medicine")
    assert pack_for(before).id != pack_for(after).id
    assert set(playbooks_for(before)) ^ set(playbooks_for(after)) == {"orthopedics_joint", "orthopedics_sports"}
