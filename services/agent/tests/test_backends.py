import collections
from pathlib import Path

import pytest
import yaml
from pydantic import TypeAdapter

from contracts.models import (
    EvalCase,
    RankedSite,
    RankRequest,
    RegionFeature,
    ScoreRequest,
    ScoreResponse,
    ScoringSpec,
    SensitivityRequest,
    SiteCandidate,
    SiteSearchRequest,
)
from services.agent.tools import backends
from services.agent.tools.geo_client import LocalGeo, MockGeo
from services.agent.tools.packs import all_pack_ids, load_pack
from services.agent.tools.sites_client import COWORKING, LocalSites, MockSites, parse_floor

ROOT = Path(__file__).resolve().parents[3]
RAW = ROOT / "data" / "raw"
requires_raw = pytest.mark.skipif(not (RAW / "houston_h3_r8.parquet").exists(), reason="real data not in data/raw/")
ORTHO = EvalCase.model_validate(yaml.safe_load((ROOT / "evals" / "orthopedic_group.yaml").read_text())).profile
SPEC = ScoringSpec(weights={"pop_45plus_within_5km": 0.5, "n_orthopedics_within_10km": 0.3, "n_primary_care_within_5km": 0.2})


@pytest.fixture(scope="module")
def local_geo() -> LocalGeo:
    return LocalGeo()


@pytest.fixture(scope="module")
def local_sites() -> LocalSites:
    return LocalSites()


@pytest.mark.parametrize(("word", "floor"), [
    ("FirstFloor", 1), ("EighteenthFloor", 18), ("TwentiethFloor", 20), ("FiftyEighthFloor", 58),
    ("SeventyFirstFloor", 71), ("Ground", 1), ("Lobby", 1), ("Basement", 0), ("LowerLevel", 0), ("Penthouse", None),
])
def test_floors_parse(word: str, floor: int | None) -> None:
    assert parse_floor(word) == floor


def test_every_pack_loads_and_its_features_exist_in_the_grid_fixture() -> None:
    grid = MockGeo().grid
    for pack_id in all_pack_ids():
        for feature in load_pack(pack_id).features:
            grid.column(feature.value or feature.id)  # raises KeyError if a column is missing


def test_backend_choice(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_BACKEND", "mock")
    assert isinstance(backends.get_geo(), MockGeo) and isinstance(backends.get_sites(), MockSites)
    monkeypatch.setenv("AGENT_BACKEND", "nope")
    with pytest.raises(ValueError):
        backends.get_geo()


@pytest.mark.asyncio
async def test_mock_backends_return_contract_shapes() -> None:
    geo, sites = MockGeo(), MockSites()
    zones = await geo.score(ScoreRequest(profile=ORTHO, spec=SPEC))
    ScoreResponse.model_validate(zones.model_dump())
    cands = await sites.search(SiteSearchRequest(zones=zones.top, constraints=ORTHO.constraints))
    assert len(cands) == 15 and all(c.source == "fixture" for c in cands)
    ranked = await geo.rank(RankRequest(profile=ORTHO, spec=SPEC, candidates=cands))
    TypeAdapter(list[RankedSite]).validate_python([r.model_dump() for r in ranked])


# ---------------------------------------------------------------------------
# Real data (skipped when data/raw/ is absent)
# ---------------------------------------------------------------------------
@requires_raw
def test_tract_and_grid_population_totals_match() -> None:
    import pyarrow.parquet as pq

    tracts = sum(pq.read_table(RAW / "houston_tracts.parquet", columns=["pop_total"]).column(0).to_pylist())
    grid = sum(v or 0 for v in pq.read_table(RAW / "houston_h3_r8.parquet", columns=["pop_total"]).column(0).to_pylist())
    assert tracts == 6_416_502
    assert grid == pytest.approx(tracts, abs=1)


@requires_raw
def test_every_lease_space_joins_a_property(local_sites: LocalSites) -> None:
    import pyarrow.parquet as pq

    spaces = pq.read_table(RAW / "houston_costar_lease_spaces.parquet", columns=["property_id"]).column(0).to_pylist()
    assert all(str(pid) in local_sites.properties for pid in spaces)


@requires_raw
def test_coworking_excluded_and_spaces_deduplicated(local_sites: LocalSites) -> None:
    assert not any(s["listing_type"] == COWORKING for s in local_sites.spaces)
    keys = collections.Counter((s["property_id"], s["suite"], s["floor"]) for s in local_sites.spaces)
    assert max(keys.values()) == 1
    assert all(parse_floor(s["floor"]) is not None or s["floor"] == "Penthouse" for s in local_sites.spaces)


@requires_raw
@pytest.mark.asyncio
async def test_local_backends_return_contract_shapes(local_geo: LocalGeo, local_sites: LocalSites) -> None:
    features = await local_geo.list_features("hou")
    TypeAdapter(list[RegionFeature]).validate_python([f.model_dump() for f in features])
    zones = await local_geo.score(ScoreRequest(profile=ORTHO, spec=SPEC))
    assert len(zones.zones) == 11_720 and all(0 <= z.score <= 1 for z in zones.zones)

    request = SiteSearchRequest(zones=[z.h3 for z in zones.zones[:400]], constraints=ORTHO.constraints)
    cands = await local_sites.search(request)
    assert cands, "expected clinic-fit spaces near the top zones"
    cons = ORTHO.constraints
    for c in cands:
        SiteCandidate.model_validate(c.model_dump())
        assert c.source == "costar" and c.floor == 1 and cons.min_sqft <= (c.sqft or 0) <= cons.max_sqft
        assert c.checks["distance_to_zone_km"] <= 2.0

    ranked = await local_geo.rank(RankRequest(profile=ORTHO, spec=SPEC, candidates=cands))
    assert {r.site_id for r in ranked} == {c.id for c in cands}
    stability = await local_geo.sensitivity(SensitivityRequest(profile=ORTHO, spec=SPEC, candidates=cands))
    assert all(0 <= s.stability <= 1 for s in stability)
    value = await local_geo.feature_value("pop_45plus_within_5km", zones.top[0])
    assert value.value is not None and value.value > 0


@requires_raw
@pytest.mark.asyncio
async def test_local_backends_are_deterministic(local_geo: LocalGeo, local_sites: LocalSites) -> None:
    async def run(geo: LocalGeo) -> tuple[ScoreResponse, list[SiteCandidate], list[RankedSite]]:
        zones = await geo.score(ScoreRequest(profile=ORTHO, spec=SPEC))
        cands = await local_sites.search(SiteSearchRequest(zones=[z.h3 for z in zones.zones[:400]],
                                                           constraints=ORTHO.constraints))
        return zones, cands, await geo.rank(RankRequest(profile=ORTHO, spec=SPEC, candidates=cands))

    assert await run(local_geo) == await run(LocalGeo())


@requires_raw
def test_grid_fixture_matches_the_real_grid() -> None:
    import pyarrow.parquet as pq

    mock = MockGeo().grid
    assert len(mock) == pq.read_metadata(RAW / "houston_h3_r8.parquet").num_rows
    assert not any(name.startswith(("n_costar", "n_lease_spaces", "n_medical_lease_spaces")) for name in mock.columns)
