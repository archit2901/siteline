"""Nonprofit clinics (FQHCs, free and charity clinics) get healthcare AND nonprofit: questions, pack, playbooks."""

import numpy as np
import pytest

from contracts.models import Office, PracticeProfile, ScoringSpec
from services.agent.factor_analyst import playbooks_for
from services.agent.questions import questions_for, sector_of
from services.agent.tools import geomath
from services.agent.tools.geo_client import MockGeo
from services.agent.tools.packs import load_pack, pack_for


@pytest.mark.parametrize("business,specialty", [
    ("nonprofit clinic", ""), ("community health center", ""), ("free clinic", ""), ("charity dental clinic", ""),
    ("health clinic for the uninsured", ""), ("", "community_health"), ("", "nonprofit_clinic"), ("", "fqhc"),
])
def test_nonprofit_clinics_get_both_halves(business: str, specialty: str) -> None:
    p = PracticeProfile(name="X", business_type=business, specialty=specialty)
    assert sector_of(business, specialty) == "healthcare.community_health"
    assert pack_for(p).id == "healthcare.community_health"
    assert {"healthcare", "nonprofit", "community_health"} <= set(playbooks_for(p))
    ids = [q.id for q in questions_for(business, specialty).sector_questions]
    assert {"payers", "who_served", "service_area", "partners"} <= set(ids)


@pytest.mark.parametrize("business,specialty,sector", [
    ("food pantry", "", "nonprofit.food_bank"), ("nonprofit youth center", "", "nonprofit.base"),
    ("", "orthopedics", "healthcare"), ("laundromat", "", "retail.services"),
])
def test_other_businesses_route_as_before(business: str, specialty: str, sector: str) -> None:
    assert sector_of(business, specialty) == sector


def test_sliding_fee_demand_ignores_payers_and_own_sites_are_not_competitors() -> None:
    grid = MockGeo().grid
    pack = load_pack("healthcare.community_health")
    from services.agent.profiler import preset
    hope = PracticeProfile(name="HOPE", specialty="community_health", payers_accepted=["medicaid"])
    model, _ = preset(hope)  # type: ignore[misc]
    assert [s.feature_id for s in model.segments] == ["below_200fpl_pop", "uninsured"]
    no_payers = hope.model_copy(update={"payers_accepted": []})
    assert np.allclose(geomath.demand(grid, pack, hope, model), geomath.demand(grid, pack, no_payers, model))

    counts = np.nan_to_num(grid.columns["n_health_centers"], nan=0.0)
    cell = int(np.flatnonzero(counts > 0)[0])
    office = Office(name="Own", lat=float(grid.lat[cell]), lon=float(grid.lon[cell]))
    spec = ScoringSpec(weights={"below_200fpl_within_5km": 1.0})
    raw = geomath.competitor_attraction(grid, "n_health_centers", spec.beta)
    less = geomath.without_own_sites(grid, raw, "n_health_centers", [office], pack, spec.beta, geomath.MAX_MINUTES)
    assert (less <= raw + 1e-12).all() and less[cell] < raw[cell]


def test_health_center_reach_counts_likely_patients_not_everyone_eligible() -> None:
    from services.agent.profiler import preset
    hope = PracticeProfile(name="HOPE", specialty="community_health")
    model, _ = preset(hope)  # type: ignore[misc]
    assert model.demand_label == "likely health center patients"
    pack = load_pack("healthcare.community_health")
    assert geomath.uses_rates(pack) and not geomath.uses_rates(load_pack("healthcare.base"))
    grid = MockGeo().grid
    eligible = np.nansum(grid.columns["below_200fpl_pop"])
    assert np.nansum(geomath.demand(grid, pack, hope, model)) < 0.5 * eligible
