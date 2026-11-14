"""Space program, visibility need and lease/buy/build from service lines (Python only)."""

from contracts.models import Constraints, PracticeProfile, RankedSite, SiteCandidate
from services.agent import strategy

ORTHO = PracticeProfile(name="x", specialty="orthopedics", services=["Clinic visits", "X-ray", "MRI", "Surgery center (ASC)"],
                        constraints=Constraints(min_sqft=4000, max_sqft=8000))


def test_service_lines_shape_the_program() -> None:
    p = strategy.space_program(ORTHO)
    assert p.service_lines == ["xray", "mri", "asc"]
    assert p.recommended_sqft == (4000 + 250 + 900 + 10_000, 8000 + 250 + 900 + 10_000)
    assert "ground floor or slab" in p.requirements and p.special_buildout_usd > 2_000_000


def test_visibility_need() -> None:
    assert strategy.visibility_need(ORTHO)[0] == "low"  # imaging and surgery come by referral
    walkin = PracticeProfile(name="x", specialty="orthopedics", services=["Urgent care / walk-in"])
    assert strategy.visibility_need(walkin)[0] == "high"
    said = PracticeProfile(name="x", business_type="bakery", free_text="[visibility] Q: Doesn't matter (referral or appointment only)")
    assert strategy.visibility_need(said)[0] == "low"
    assert strategy.visibility_fit("Office", "high") < strategy.visibility_fit("Retail", "high")
    assert strategy.visibility_fit("Industrial", "low") == 1.0


def test_heavy_buildout_favors_owning() -> None:
    site = SiteCandidate(id="s", address="1 Main", lat=29.7, lon=-95.4, sqft=15000, rent_psf=30.0)
    ranked = [RankedSite(site_id="s", zone_score=0.5, site_quality=0.5, net_new_patients=100, captured_patients=100,
                         cannibalized_patients=0, final_score=0.5)]
    plan = strategy.plan(ORTHO, ranked, {"s": site})
    opts = {o.option: o for o in plan.sites[0].options}
    assert opts["buy"].annual_cost_usd < opts["lease"].annual_cost_usd  # the ASC buildout stays with the building
    assert "Owning or building" in plan.recommendation
    plain = PracticeProfile(name="x", business_type="laundromat")
    plan2 = strategy.plan(plain, ranked, {"s": site.model_copy(update={"sqft": 3000})})
    assert plan2.program.special_buildout_usd == 0
