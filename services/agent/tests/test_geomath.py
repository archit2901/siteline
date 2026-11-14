import h3
import numpy as np
import pytest

from contracts.models import Office, PracticeProfile, ScoringSpec, SiteCandidate
from services.agent.tools import geomath
from services.agent.tools.packs import load_pack

CENTER = (29.76, -95.37)


def small_grid() -> geomath.Grid:
    """~1,900 cells around downtown Houston with smooth synthetic columns."""
    cells = sorted(h3.grid_disk(h3.latlng_to_cell(*CENTER, 8), 25))
    lat = np.array([h3.cell_to_latlng(c)[0] for c in cells])
    lon = np.array([h3.cell_to_latlng(c)[1] for c in cells])
    dist = np.hypot(lat - CENTER[0], lon - CENTER[1])
    cols = {
        "pop_u18": 200 - 300 * dist, "pop_18_44": 300 - 400 * dist, "pop_45_64": 150 + 200 * dist,
        "pop_65p": 80 + 150 * dist, "share_any_medicare": 0.15 + dist, "share_any_medicaid": 0.2 - 0.5 * dist,
        "share_any_private": np.full(len(cells), 0.5), "n_orthopedics": np.zeros(len(cells)),
        "pop_45_64_within_5km": 5000 + 9000 * dist, "pop_65p_within_5km": 3000 + 5000 * dist,
        "n_orthopedics_within_10km": 400 - 2000 * dist, "n_primary_care_within_5km": 300 - 1000 * dist,
    }
    cols = {k: np.maximum(v, 0).astype(float) for k, v in cols.items()}
    return geomath.Grid(h3=cells, lat=lat, lon=lon, columns=cols)


PROFILE = PracticeProfile(name="Ortho", specialty="orthopedics", payers_accepted=["medicare", "commercial"],
                          stock_pack="healthcare.orthopedics")
SPEC = ScoringSpec(weights={"pop_45plus_within_5km": 0.5, "n_orthopedics_within_10km": 0.3, "n_primary_care_within_5km": 0.2})


def site(sid: str, lat: float, lon: float, **kw: object) -> SiteCandidate:
    return SiteCandidate(id=sid, address=sid, lat=lat, lon=lon, sqft=5000, floor=1, parking_spaces=30, **kw)  # type: ignore[arg-type]


def test_percentile_is_in_unit_interval_and_keeps_missing() -> None:
    x = np.array([3.0, 1.0, np.nan, 2.0, 2.0])
    p = geomath.percentile(x)
    assert np.isnan(p[2])
    assert np.nanmin(p) > 0 and np.nanmax(p) == 1.0
    assert p[3] == p[4]  # ties share a rank


def test_flipping_direction_flips_the_ranking() -> None:
    grid, pack = small_grid(), load_pack("healthcare.orthopedics")
    up = geomath.score(grid, pack, PROFILE, ScoringSpec(weights={"pop_45plus_within_5km": 1}), "hou", 5)
    down = geomath.score(grid, pack, PROFILE, ScoringSpec(weights={"pop_45plus_within_5km": 1},
                                                         directions={"pop_45plus_within_5km": -1}), "hou", 5)
    assert up.zones[0].h3 == down.zones[-1].h3


def test_more_demand_band_population_means_more_demand() -> None:
    grid, pack = small_grid(), load_pack("healthcare.orthopedics")
    d = geomath.demand(grid, pack, PROFILE)
    older = grid.columns["pop_45_64"] + grid.columns["pop_65p"]
    assert d[np.argmax(older)] > d[np.argmin(older)]


def test_a_competitor_next_to_the_site_lowers_net_new() -> None:
    grid, pack = small_grid(), load_pack("healthcare.orthopedics")
    s = site("s", 29.80, -95.40)
    alone = geomath.huff(grid, pack, PROFILE, SPEC, [s], "n_orthopedics")[0]
    grid.columns["n_orthopedics"] = grid.columns["n_orthopedics"].copy()
    grid.columns["n_orthopedics"][grid.cell_of(29.80, -95.40)] = 10
    grid._competitors.clear()
    crowded = geomath.huff(grid, pack, PROFILE, SPEC, [s], "n_orthopedics")[0]
    assert crowded.net_new_patients < alone.net_new_patients


def test_a_site_on_an_existing_office_adds_almost_nothing() -> None:
    grid, pack = small_grid(), load_pack("healthcare.orthopedics")
    profile = PROFILE.model_copy(update={"existing_offices": [Office(name="A", lat=29.80, lon=-95.40, clinicians=1)]})
    on_office = geomath.huff(grid, pack, profile, SPEC, [site("s", 29.80, -95.40)], None)[0]
    assert on_office.captured_patients > 0
    assert abs(on_office.net_new_patients) < 0.01 * on_office.captured_patients
    far = geomath.huff(grid, pack, profile, SPEC, [site("f", 29.70, -95.30)], None)[0]
    assert far.net_new_patients > on_office.net_new_patients


def test_rank_excludes_hard_constraint_failures_and_stability_is_a_share() -> None:
    grid, pack = small_grid(), load_pack("healthcare.orthopedics")
    sites = [site(f"s{i}", 29.70 + 0.01 * i, -95.45 + 0.01 * i) for i in range(12)]
    sites.append(site("upstairs", 29.76, -95.37).model_copy(update={"floor": 3}))
    ranked = geomath.rank(grid, pack, PROFILE, SPEC, sites, None, with_stability=True)
    assert ranked[-1].site_id == "upstairs" and ranked[-1].excluded_reason
    kept = [r for r in ranked if r.excluded_reason is None]
    assert [r.final_score for r in kept] == sorted((r.final_score for r in kept), reverse=True)
    assert all(r.stability is not None and 0 <= r.stability <= 1 for r in kept)
    assert sum(r.stability or 0 for r in kept) == pytest.approx(10)  # exactly 10 sites in the top 10 per draw


def test_same_input_same_output() -> None:
    grid, pack = small_grid(), load_pack("healthcare.orthopedics")
    sites = [site(f"s{i}", 29.70 + 0.01 * i, -95.45) for i in range(12)]
    a = geomath.rank(grid, pack, PROFILE, SPEC, sites, None, with_stability=True)
    b = geomath.rank(small_grid(), pack, PROFILE, SPEC, sites, None, with_stability=True)
    assert a == b


def test_packs_without_competitor_data_are_flagged() -> None:
    assert geomath.competitor_column(load_pack("healthcare.orthopedics"), None) == "n_orthopedics"
    assert geomath.competitor_column(load_pack("healthcare.pediatric_dentistry"), None) is None
    assert geomath.competitor_column(load_pack("healthcare.pediatric_dentistry"), "peds_dentists") == "peds_dentists"
