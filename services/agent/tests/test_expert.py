"""Expert rules: office functions decide scouts, visibility, space and weight tiers; provenance labels factors."""

from contracts.models import Factor, PracticeProfile
from services.agent import expert, strategy
from services.agent.tools import cms_points


def ortho(functions: str, extra: str = "") -> PracticeProfile:
    return PracticeProfile(name="O", specialty="orthopedics",
                           free_text=f"[functions] Office functions: {functions}\n{extra}".strip())


def test_functions_parse_labels_and_weights() -> None:
    p = ortho("Clinic visits 10, Surgery center (ASC) 8, MRI / imaging 7, Physical therapy 3")
    assert expert.functions_of(p) == {"clinic": 10, "asc": 8, "mri": 7, "pt": 3}
    assert expert.functions_text({"clinic": 10, "pt": 0}) == "Clinic visits 10"


def test_low_weight_pt_makes_a_pt_clinic_a_partner_high_weight_a_rival() -> None:
    low = {s.factor_id: s for s in expert.scouts_for(ortho("Clinic visits 10, Physical therapy 3"))}
    high = {s.factor_id: s for s in expert.scouts_for(ortho("Clinic visits 10, Physical therapy 8"))}
    assert low["x_pt_clinics"].agg == "distance_to_nearest" and low["x_pt_clinics"].rule == "pt_partner"
    assert high["x_pt_clinics"].agg == "count_within" and high["x_pt_clinics"].direction == -1


def test_urgent_care_needs_visibility_scheduled_care_does_not() -> None:
    assert strategy.visibility_need(ortho("Urgent care / walk-in 8, Clinic visits 6"))[0] == "high"
    assert strategy.visibility_need(ortho("Physical therapy 9, MRI / imaging 5"))[0] == "low"


def test_space_suggestion_scales_clinic_by_physicians() -> None:
    lo, hi, parking = expert.suggest_space({"clinic": 10, "asc": 8, "urgent": 5}, 3)
    assert lo == round((1_600 * 3 + 9_000 + 3_000) * 0.7, -2) and hi == round((1_600 * 3 + 9_000 + 3_000) * 1.3, -2) and parking == 6.0


def test_competition_promoted_hazards_dampened_and_labeled() -> None:
    p = ortho("Clinic visits 10")
    comp = Factor(id="comp", factor="Few orthopedic competitors nearby", why="", proxy="Orthopedic clinicians within 10 km",
                  direction=-1, priority="high", feature_id="n_orthopedics_within_10km", status="covered")
    flood = Factor(id="flood", factor="Low flood risk", why="", proxy="FEMA flood zone share", direction=-1,
                   priority="medium", feature_id="flood", status="covered")
    assert expert.tier(p, comp) == 3.0 and expert.tier(p, flood) < 1
    rows = {r.factor_id: r for r in expert.provenance(p, [comp, flood]).factors}
    assert rows["comp"].source == "expert" and rows["flood"].source == "ai"


def test_function_scouts_replace_the_analysts_unmeasured_duplicate() -> None:
    p = ortho("Clinic visits 10, Physical therapy 3")
    dup = Factor(id="pt_near", factor="Physical therapy clinics nearby", why="", proxy="PT clinics", direction=1, priority="medium")
    factors, results, _ = expert.function_scouts(p, [dup], "abc123")
    assert "pt_near" not in {f.id for f in factors} and "x_pt_clinics" in {f.id for f in factors}
    assert all(cms_points.layer_of(str(r.spec.source_url)) for r in results if r.spec)


def test_cms_layer_urls_are_canonical_only() -> None:
    assert cms_points.layer_of(cms_points.layer_url("freestanding_pt")) == "freestanding_pt"
    assert cms_points.layer_of("https://example.com/provider-data/dataset/mj5m-pzi6?layer=freestanding_pt") is None
    assert cms_points.layer_of(cms_points.layer_url("nope")) is None


def test_space_program_follows_functions_not_referral_words() -> None:
    p = ortho("Clinic visits 10, MRI / imaging 7", "[referrals] Where do new patients come from today: urgent cares, PT")
    assert strategy.service_lines(p) == ["mri"]


def test_function_scouts_keep_a_factor_another_scout_already_measures() -> None:
    p = ortho("Clinic visits 10")
    er = Factor(id="urgent_er", factor="Urgent care and ER proximity", why="", proxy="hospital ERs", direction=-1, priority="high")
    factors, _, _ = expert.function_scouts(p, [er], "abc123", keep={"urgent_er"})
    assert "urgent_er" in {f.id for f in factors}


def test_expert_floors_and_caps_override_the_models_importance() -> None:
    from services.agent.tools.weights import WeightAdvice, spec_from_advice

    p = ortho("Clinic visits 10, Physical therapy 3", "[team_coverage] Teams you cover: Memorial High School")
    p = p.model_copy(update={"specialty": "orthopedics", "free_text": p.free_text + "\n[ortho_focus] Sports medicine"})
    mk = lambda fid, name, feat, d=1: Factor(id=fid, factor=name, why="w", proxy=name, direction=d, priority="medium",
                                             feature_id=feat, status="covered")
    fs = [mk("comp", "Far from other orthopedic offices", "x_ortho_offices"), mk("school", "Covered school teams", "partner_schools"),
          mk("pt", "Physical therapy competition", "n_physical_therapy_within_5km", -1), mk("flood", "Low flood risk", "flood", -1)]
    advice = WeightAdvice.model_validate({"weights": [
        {"factor_id": "comp", "importance": 8, "why": "a"}, {"factor_id": "school", "importance": 10, "why": "b"},
        {"factor_id": "pt", "importance": 2, "why": "c"}, {"factor_id": "flood", "importance": 8, "why": "d"}]})
    w = spec_from_advice(fs, advice, p)[0].weights
    assert w["x_ortho_offices"] > w["partner_schools"] and w["n_physical_therapy_within_5km"] > w["flood"]
