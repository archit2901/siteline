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


def test_weights_reorder_spaces_even_when_their_zones_score_alike() -> None:
    """Candidates all sit in top zones, so the zone part is ranked among them: weights flip close calls."""
    grid, pack = small_grid(), load_pack("healthcare.orthopedics")
    sites = [site(f"s{i}", 29.70 + 0.012 * i, -95.43 + 0.012 * i) for i in range(8)]
    w = {"pop_45plus_within_5km": 1.0}
    order = lambda d: [r.site_id for r in geomath.rank(grid, pack, PROFILE, ScoringSpec(weights=w, directions=d, alpha=0.0),
                                                       sites, None, with_stability=False)]
    up, down = order({}), order({"pop_45plus_within_5km": -1})
    assert up[0] == down[-1] and up[-1] == down[0]  # flipping the one weighted factor reverses the order
    ranked = geomath.rank(grid, pack, PROFILE, ScoringSpec(weights={"pop_45plus_within_5km": 1}, alpha=0.0), sites,
                          None, with_stability=False)
    zone_part = sorted((r.final_score - 0.4 * r.site_quality) / 0.6 for r in ranked)
    assert zone_part == pytest.approx([(k + 1) / 8 for k in range(8)], abs=1e-3)  # a rank among the candidates, not a raw score


def test_same_input_same_output() -> None:
    grid, pack = small_grid(), load_pack("healthcare.orthopedics")
    sites = [site(f"s{i}", 29.70 + 0.01 * i, -95.45) for i in range(12)]
    a = geomath.rank(grid, pack, PROFILE, SPEC, sites, None, with_stability=True)
    b = geomath.rank(small_grid(), pack, PROFILE, SPEC, sites, None, with_stability=True)
    assert a == b


def test_packs_without_competitor_data_are_flagged() -> None:
    assert geomath.competitor_column(load_pack("healthcare.orthopedics"), None) == "_supply_healthcare.orthopedics"
    assert geomath.competitor_column(load_pack("healthcare.pediatric_dentistry"), None) is None
    assert geomath.competitor_column(load_pack("healthcare.pediatric_dentistry"), "peds_dentists") == "peds_dentists"


def test_reach_counts_core_segment_and_decays_with_distance() -> None:
    """General businesses: reach is in the core segment's units, and far cells mostly choose other options."""
    from contracts.models import DemandModel, DemandSegment

    grid = small_grid()
    pack = load_pack("general.base")
    profile = PracticeProfile(name="Tutor", business_type="tutoring center")
    kids = DemandModel(segments=[DemandSegment(feature_id="pop_u18", label="children under 18", weight=1.0)],
                       demand_label="children under 18", space_uses=["Retail"], trip="local")
    mixed = kids.model_copy(update={"segments": [*kids.segments, DemandSegment(feature_id="pop_18_44", label="adults", weight=0.7)]})
    sites = [site("a", *CENTER)]
    only_kids = geomath.huff(grid, pack, profile, SPEC, sites, None, kids)[0].net_new_patients
    with_adults = geomath.huff(grid, pack, profile, SPEC, sites, None, mixed)[0].net_new_patients
    assert only_kids == with_adults  # secondary segments never inflate the headline count

    in_reach = geomath.travel_minutes(grid.lat, grid.lon, *CENTER) <= geomath.TRIP_MINUTES["local"]
    everyone = float(grid.columns["pop_u18"][in_reach].sum())
    assert 0 < only_kids < 0.6 * everyone  # no mapped competitors, yet far fewer than everyone in reach


def test_healthcare_huff_has_no_outside_option() -> None:
    grid, pack = small_grid(), load_pack("healthcare.orthopedics")
    sites = [site("a", *CENTER)]
    net = geomath.huff(grid, pack, PROFILE, SPEC, sites, None)[0].net_new_patients
    in_reach = geomath.travel_minutes(grid.lat, grid.lon, *CENTER) <= geomath.MAX_MINUTES
    expected = float(geomath.demand(grid, pack, PROFILE)[in_reach].sum())
    assert net == round(expected)  # no competitors and no outside option: every patient in reach (unchanged model)


def test_singular_handles_qualified_labels() -> None:
    from services.agent.critic import singular

    assert singular("children under 18") == "child under 18"
    assert singular("adults 18-44") == "adult 18-44"
    assert singular("renter households") == "renter household"
    assert singular("patients") == "patient"


def test_new_site_is_sized_in_physicians_like_the_competitor_count() -> None:
    """CMS counts PAs/NPs under their own specialty, so '2 surgeons and 2 PAs' is 2 in the orthopedic supply unit."""
    from contracts.models import Office

    p = PracticeProfile(name="O", specialty="orthopedics", existing_offices=[Office(name="A", lat=29.7, lon=-95.4, clinicians=5)],
                        free_text="[capacity] How many surgeons: 2 surgeons and 2 PAs, 12 exam rooms")
    assert geomath.site_providers(p) == 2.0
    assert geomath.site_providers(p.model_copy(update={"free_text": ""})) == 5.0


def test_orthopedic_supply_counts_physiatrists_and_podiatrists() -> None:
    grid = small_grid()
    pack = load_pack("healthcare.orthopedics.foot_ankle")
    for k in ("n_orthopedics", "n_podiatry"):
        grid.columns.setdefault(k, np.ones(len(grid)))
    geomath.ensure_supply(grid, pack)
    col = grid.columns["_supply_healthcare.orthopedics.foot_ankle"]
    assert np.allclose(col, np.nan_to_num(grid.columns["n_orthopedics"]) + np.nan_to_num(grid.columns["n_podiatry"]))


def test_relaxed_sites_say_what_they_miss_and_rank_after_strict_fits() -> None:
    from contracts.models import Constraints, RankedSite, SiteCandidate
    from services.agent.tools import geomath as gm

    cons = Constraints(min_sqft=11_200, max_sqft=20_800, ground_floor=True)
    steps = gm.relaxations(cons)
    assert not steps[0].ground_floor and steps[-1].min_sqft == 6_700
    small = SiteCandidate(id="b", address="2 B St", lat=29.7, lon=-95.4, sqft=8_000, floor=3)
    assert gm.misses(small, cons) == ["8,000 sq ft (min 11,200)", "Floor 3"]
    sites = {"a": SiteCandidate(id="a", address="1 A St", lat=29.7, lon=-95.4, sqft=12_000, floor=1),
             "b": small.model_copy(update={"checks": {"misses": gm.misses(small, cons)}})}
    r = lambda sid, score, ex=None: RankedSite(site_id=sid, net_new_patients=1, captured_patients=1, cannibalized_patients=0,
                                                zone_score=0, site_quality=0, final_score=score, excluded_reason=ex)
    sites["c"] = small.model_copy(update={"id": "c", "floor": 1, "checks": {"misses": ["8,000 sq ft (min 11,200)"]}})
    order = gm.strict_first([r("b", 0.9), r("x", 0, "outside"), r("c", 0.2), r("a", 0.5)], sites)
    assert [x.site_id for x in order] == ["a", "c", "b", "x"]
