"""site-inspector (CoStar + Vexcel + Matterport) and the community lens, with the partner APIs mocked."""

import asyncio
import re

import pytest

from contracts.models import PracticeProfile, RankedSite, SiteCandidate
from services.agent import inspector
from services.agent.critic import community_option, evidence_values, inspection_options, resolve
from services.agent.tools import geomath
from services.agent.tests.test_geomath import CENTER, small_grid

PROFILE = PracticeProfile(name="Wash", business_type="laundromat", customers="renters")
SITES = {
    "a": SiteCandidate(id="a", address="1 Main St", lat=29.76, lon=-95.37, sqft=3600, floor=1, parking_spaces=30,
                       matterport_id="abc123"),
    "b": SiteCandidate(id="b", address="2 Main St", lat=29.77, lon=-95.38, sqft=2000, floor=1, parking_spaces=None),
}
RANKED = [RankedSite(site_id=s, net_new_patients=1000, captured_patients=1000, cannibalized_patients=0, zone_score=0.8,
                     site_quality=0.9, final_score=0.9) for s in SITES]


@pytest.fixture
def apis(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VEXCEL_API_KEY", "test")
    monkeypatch.setenv("MATTERPORT_API_KEY", "k")
    monkeypatch.setenv("MATTERPORT_API_TOKEN", "t")

    async def cars(client, lat, lon, meters=inspector.DETECT_METERS):  # type: ignore[no-untyped-def]
        return 28

    async def scan(client, model_id):  # type: ignore[no-untyped-def]
        return {"name": "1 Main St", "floors": 2, "rooms": 14, "area_sqft": 3410}

    monkeypatch.setattr(inspector, "count_parked_cars", cars)
    monkeypatch.setattr(inspector, "read_scan", scan)


def test_fallback_inspection_combines_all_three_layers(apis: None) -> None:
    results, notes = asyncio.run(inspector.inspect(PROFILE, RANKED, SITES, use_agent=False))
    a = results["a"]
    assert a.checked == ["listing", "aerial", "3d"]
    assert (a.parked_cars, a.listed_parking, a.occupancy, a.scan_area_sqft) == (28, 30, 0.933, 3410)
    assert any("may be tight at peak" in f for f in a.findings)  # 93% full on the aerial
    assert any("3,410 sq ft indoors for a listing of 3,600 sq ft" in f for f in a.findings)
    b = results["b"]
    assert b.occupancy is None and "3d" not in b.checked  # no listed parking, no scan: nothing invented


def test_missing_keys_mark_checks_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VEXCEL_API_KEY", raising=False)
    monkeypatch.delenv("MATTERPORT_API_KEY", raising=False)
    results, _ = asyncio.run(inspector.inspect(PROFILE, RANKED, SITES, use_agent=False))
    assert results["a"].parked_cars is None and results["a"].findings == []
    assert any("no Vexcel key" in u for u in results["a"].unavailable)


def test_inspection_and_community_claims_verify(apis: None) -> None:
    results, _ = asyncio.run(inspector.inspect(PROFILE, RANKED, SITES, use_agent=False))
    need = geomath.community_need(small_grid_with_households(), *CENTER, 10)
    community = {"a": need}
    extra = evidence_values(results, community)
    options = inspection_options(SITES["a"], results["a"]) + community_option(SITES["a"], need)
    assert {o.key for o in options} >= {"inspect_aerial", "inspect_scan", "community"}
    for o in options:  # every number in every claim matches a value its refs point to
        values = [v for v in [asyncio.run(resolve(r, SITES, {r_.site_id: r_ for r_ in RANKED}, None, extra))  # type: ignore[arg-type]
                              for r in o.refs] if v is not None]
        for number in re.findall(r"\d[\d,]*(?:\.\d+)?%?", o.text):
            x = float(number.replace(",", "").rstrip("%"))
            assert any(abs(x - v) <= max(1, 0.02 * v) or abs(x - v * 100) <= 1 for v in values), (number, o.text)


def small_grid_with_households() -> geomath.Grid:
    grid = small_grid()
    n = len(grid)
    import numpy as np

    grid.columns.update({
        "households": np.full(n, 100.0), "no_vehicle_households": np.full(n, 12.0), "renter_households": np.full(n, 60.0),
        "pop_total": np.full(n, 250.0), "share_any_medicaid": np.full(n, 0.2),
        "median_hh_income": np.where(np.arange(n) % 4 == 0, 30_000.0, 90_000.0),
    })
    return grid


def test_community_need_shares() -> None:
    need = geomath.community_need(small_grid_with_households(), *CENTER, 10)
    assert need.no_car_share == 0.12 and need.renter_share == 0.6 and need.medicaid_share == 0.2
    assert 0.15 < (need.lower_income_share or 0) < 0.35  # about a quarter of cells are under 80% of the median
