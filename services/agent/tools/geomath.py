"""All scoring math (docs/DESIGN.md "Scoring model" + HANDOFF decision 6). Deterministic; numpy only.

Grid -> percentile-normalized features -> zone score S, demand D -> Huff net-new per candidate -> site quality Q ->
final score F -> Dirichlet sensitivity. geo-service will import this module unchanged when it exists.
"""

import math
from dataclasses import dataclass, field

import h3
import numpy as np

from contracts.models import (
    CommunityNeed,
    Constraints,
    DemandModel,
    DemandSegment,
    HuffResult,
    PracticeProfile,
    RankedSite,
    ScoreResponse,
    ScoringSpec,
    SensitivityResult,
    SiteCandidate,
    StockPack,
    ZoneScore,
)

H3_RES = 8
DETOUR = 1.3  # straight line -> road distance
SPEED_KMH = 56.33  # 35 mph
MIN_MINUTES = 2.0
MAX_MINUTES = 30.0  # Huff catchment: competitors, offices and the new site only reach cells within 30 minutes
TRIP_MINUTES = {"neighborhood": 10.0, "local": 20.0, "destination": 30.0}
# Businesses scored from a DemandModel use OpenStreetMap competitors, which miss many real ones, so their Huff model
# adds an "other options" alternative (stores we can't see, online, not going at all) that is as attractive as a
# store a quarter of the trip time away, so half the people that far out already choose something else. Without it,
# every household in reach with no mapped competitor counts as captured.
OUTSIDE_OPTION_FRACTION = 0.25
TOP_K = 10  # stability = share of draws in which a site stays in the top 10
BAND_COLUMNS = {"0-17": "pop_u18", "18-44": "pop_18_44", "45-64": "pop_45_64", "65+": "pop_65p"}
# Payer -> share of residents with that coverage (ACS). CHIP is counted inside Medicaid; workers' comp has no
# residence-level data, so it does not change π.
# Per-cell count columns a DemandModel may use as customer segments (Huff sums them over cells).
DEMAND_COLUMNS = {
    "pop_total": "people", "pop_u18": "children under 18", "pop_18_44": "adults 18-44", "pop_45_64": "adults 45-64",
    "pop_65p": "adults 65+", "households": "households", "renter_households": "renter households",
    "no_vehicle_households": "households without a car", "jobs": "workers (jobs located here)",
}
PAYER_COLUMNS = {"medicare": "share_any_medicare", "medicaid": "share_any_medicaid", "chip": "share_any_medicaid",
                 "commercial": "share_any_private", "private": "share_any_private"}


@dataclass
class Grid:
    """One region's cells and feature columns (NaN = missing)."""

    h3: list[str]
    lat: np.ndarray
    lon: np.ndarray
    columns: dict[str, np.ndarray]
    tracts: list[str] | None = None  # tract GEOID per cell (for joining tract tables)
    index: dict[str, int] = field(init=False)
    _competitors: dict[tuple[str, float], np.ndarray] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        self.index = {cell: i for i, cell in enumerate(self.h3)}

    def __len__(self) -> int:
        return len(self.h3)

    def column(self, expr: str) -> np.ndarray:
        """A column, or a sum of columns written "a+b" (the only operator packs may use)."""
        parts = [p.strip() for p in expr.split("+")]
        missing = [p for p in parts if p not in self.columns]
        if missing:
            raise KeyError(f"unknown grid column(s) {missing}")
        values = np.vstack([self.columns[p] for p in parts])
        out = np.nansum(values, axis=0)
        out[np.isnan(values).all(axis=0)] = np.nan
        return out

    def cell_of(self, lat: float, lon: float) -> int | None:
        return self.index.get(h3.latlng_to_cell(lat, lon, H3_RES))


def travel_minutes(lat1: np.ndarray | float, lon1: np.ndarray | float, lat2: float, lon2: float) -> np.ndarray:
    """Straight-line km × 1.3 at 35 mph, floored at 2 minutes."""
    p1, p2 = np.radians(lat1), math.radians(lat2)
    a = np.sin((p2 - p1) / 2) ** 2 + np.cos(p1) * math.cos(p2) * np.sin(np.radians(np.asarray(lon2) - lon1) / 2) ** 2
    km = 2 * 6371.0 * np.arcsin(np.sqrt(a))
    return np.maximum(MIN_MINUTES, km * DETOUR / SPEED_KMH * 60)


def percentile(values: np.ndarray) -> np.ndarray:
    """rank / N over non-missing values (ties share the average rank); missing stays NaN."""
    out = np.full(values.shape, np.nan)
    ok = ~np.isnan(values)
    n = int(ok.sum())
    if n == 0:
        return out
    _, inverse, counts = np.unique(values[ok], return_inverse=True, return_counts=True)
    avg_rank = np.cumsum(counts) - (counts - 1) / 2
    out[ok] = avg_rank[inverse] / n
    return out


# ---------------------------------------------------------------------------
# Zone score and demand
# ---------------------------------------------------------------------------
def feature_expr(pack: StockPack, feature_id: str) -> str:
    for f in pack.features:
        if f.id == feature_id:
            return f.value or f.id
    return feature_id  # agent-found features and plain grid columns


def contribution_matrix(grid: Grid, pack: StockPack, spec: ScoringSpec) -> tuple[list[str], np.ndarray, np.ndarray]:
    """Feature ids, normalized weights, and c_ik (cells × features): x̃ for direction +1, 1 − x̃ for −1, 0 if missing."""
    default_direction = {f.id: f.direction for f in pack.features}
    ids = [k for k, w in spec.weights.items() if w > 0]
    if not ids:
        raise ValueError("spec has no positive weights")
    weights = np.array([spec.weights[k] for k in ids], dtype=float)
    weights = weights / weights.sum()
    c = np.zeros((len(grid), len(ids)))
    for j, fid in enumerate(ids):
        x = percentile(grid.column(feature_expr(pack, fid)))
        direction = spec.directions.get(fid, default_direction.get(fid, 1))
        c[:, j] = np.nan_to_num(x if direction == 1 else 1 - x, nan=0.0)
    return ids, weights, c


def payer_share(grid: Grid, profile: PracticeProfile) -> np.ndarray:
    """π_i: share of residents whose coverage the practice accepts (capped at 1; overlapping coverage double-counts)."""
    columns = sorted({PAYER_COLUMNS[p.lower()] for p in profile.payers_accepted if p.lower() in PAYER_COLUMNS})
    if not columns:
        return np.ones(len(grid))
    share = sum(np.nan_to_num(grid.columns[c], nan=0.0) for c in columns)
    return np.minimum(1.0, share)


def primary_segment(model: DemandModel) -> DemandSegment | None:
    """The core customer group (highest weight, first on ties): reach is counted in its units, never in a mix."""
    usable = [s for s in model.segments if s.feature_id in DEMAND_COLUMNS]
    return max(usable, key=lambda s: s.weight) if usable else None


def demand(grid: Grid, pack: StockPack, profile: PracticeProfile, model: DemandModel | None = None) -> np.ndarray:
    """D_i = π_i · Σ_a P_ia · r_a over the pack's demand bands (r_a = 1.0 if missing), or, with a DemandModel,
    Σ_s w_s · count_is over its customer segments (π only when the practice lists accepted payers)."""
    if model is not None and model.segments:
        people = np.zeros(len(grid))
        for seg in model.segments:
            if seg.feature_id in DEMAND_COLUMNS and seg.feature_id in grid.columns:
                people += max(0.0, seg.weight) * np.nan_to_num(grid.columns[seg.feature_id], nan=0.0)
        return payer_share(grid, profile) * people if profile.payers_accepted else people
    bands = pack.demand_age_bands or list(BAND_COLUMNS)
    people = sum(np.nan_to_num(grid.columns[BAND_COLUMNS[b]], nan=0.0) * pack.visit_rates.get(b, 1.0) for b in bands)
    return payer_share(grid, profile) * people


def score(grid: Grid, pack: StockPack, profile: PracticeProfile, spec: ScoringSpec, region_id: str, top_n: int,
          model: DemandModel | None = None) -> ScoreResponse:
    ids, weights, c = contribution_matrix(grid, pack, spec)
    contrib = c * weights
    s = contrib.sum(axis=1)
    d = demand(grid, pack, profile, model)
    order = np.argsort(-s, kind="stable")
    zones = [
        ZoneScore(
            h3=grid.h3[i], score=round(float(s[i]), 4), demand=round(float(d[i]), 1),
            contributions={fid: round(float(contrib[i, j]), 4) for j, fid in enumerate(ids)},
        )
        for i in order
    ]
    return ScoreResponse(region_id=region_id, zones=zones, top=[z.h3 for z in zones[:top_n]])


# ---------------------------------------------------------------------------
# Huff net-new
# ---------------------------------------------------------------------------
NO_COMPETITOR_CAVEAT = ("No competitor data for this specialty yet, so net-new counts every resident in reach that "
                        "the practice's existing offices don't already serve; treat it as an upper bound.")


def competitor_column(pack: StockPack, competitor_feature: str | None) -> str | None:
    """The per-cell competitor count column: the request's, else the pack's site_rules.competitor_feature.

    None means net-new has no competition term; callers must show NO_COMPETITOR_CAVEAT (never invent a cap).
    """
    return competitor_feature or pack.site_rules.get("competitor_feature")


def competitor_attraction(grid: Grid, column: str | None, beta: float, max_minutes: float = MAX_MINUTES) -> np.ndarray:
    """Σ_m A_m t_im^−β over competitor cells m within the catchment (A_m = competitors counted in cell m)."""
    if column is None:
        return np.zeros(len(grid))
    key = (column, beta, max_minutes)
    if key not in grid._competitors:
        counts = np.nan_to_num(grid.columns[column], nan=0.0)
        m = np.flatnonzero(counts > 0)
        total = np.zeros(len(grid))
        for start in range(0, len(grid), 2000):  # chunked cells × competitors
            rows = slice(start, start + 2000)
            t = _pairwise_minutes(grid.lat[rows], grid.lon[rows], grid.lat[m], grid.lon[m])
            total[rows] = np.where(t <= max_minutes, counts[m] * t ** -beta, 0.0).sum(axis=1)
        grid._competitors[key] = total
    return grid._competitors[key]


def pairwise_km(lat1: np.ndarray, lon1: np.ndarray, lat2: np.ndarray, lon2: np.ndarray) -> np.ndarray:
    """Straight-line km between every (lat1, lon1) and every (lat2, lon2)."""
    p1, p2 = np.radians(lat1)[:, None], np.radians(lat2)[None, :]
    dl = np.radians(lon2)[None, :] - np.radians(lon1)[:, None]
    a = np.sin((p2 - p1) / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return 2 * 6371.0 * np.arcsin(np.sqrt(a))


def _pairwise_minutes(lat1: np.ndarray, lon1: np.ndarray, lat2: np.ndarray, lon2: np.ndarray) -> np.ndarray:
    return np.maximum(MIN_MINUTES, pairwise_km(lat1, lon1, lat2, lon2) * DETOUR / SPEED_KMH * 60)


def _reach(grid: Grid, lat: float, lon: float, attractiveness: float, beta: float,
           max_minutes: float = MAX_MINUTES) -> np.ndarray:
    t = travel_minutes(grid.lat, grid.lon, lat, lon)
    return np.where(t <= max_minutes, attractiveness * t ** -beta, 0.0)


def huff(grid: Grid, pack: StockPack, profile: PracticeProfile, spec: ScoringSpec, candidates: list[SiteCandidate],
         competitor_feature: str | None, model: DemandModel | None = None) -> list[HuffResult]:
    """Captured, cannibalized and net-new customers per candidate (docs/DESIGN.md Huff + net-new)."""
    core = primary_segment(model) if model is not None else None
    # Reach counts one kind of person (the core segment) so the number has a single unit and label.
    d = demand(grid, pack, profile, model.model_copy(update={"segments": [core.model_copy(update={"weight": 1.0})]})
               if model is not None and core is not None else model)
    column = competitor_feature or (model.competitor_feature if model else None)
    column = column if column is None or column in grid.columns else None
    reach = TRIP_MINUTES[model.trip] if model is not None else MAX_MINUTES
    competitors = competitor_attraction(grid, competitor_column(pack, column), spec.beta, reach)
    if model is not None:  # see OUTSIDE_OPTION_FRACTION; healthcare packs keep the CMS-competitor model unchanged
        competitors = competitors + (reach * OUTSIDE_OPTION_FRACTION) ** -spec.beta
    offices = [o for o in profile.existing_offices if o.lat is not None and o.lon is not None]
    office_attr = sum((_reach(grid, o.lat, o.lon, max(1, o.clinicians), spec.beta, reach) for o in offices),  # type: ignore[arg-type]
                      np.zeros(len(grid)))
    site_size = max(1.0, float(np.mean([o.clinicians for o in profile.existing_offices]))) if profile.existing_offices else 1.0
    before_denom = competitors + office_attr
    before_share = np.divide(office_attr, before_denom, out=np.zeros(len(grid)), where=before_denom > 0)

    results = []
    for site in candidates:
        site_attr = _reach(grid, site.lat, site.lon, site_size, spec.beta, reach)
        after_denom = before_denom + site_attr
        p_site = np.divide(site_attr, after_denom, out=np.zeros(len(grid)), where=after_denom > 0)
        after_share = np.divide(office_attr, after_denom, out=np.zeros(len(grid)), where=after_denom > 0)
        # Whole patients, and net-new = captured - cannibalized exactly, so every display shows the same integer.
        captured = round(float((d * p_site).sum()))
        cannibalized = round(float((d * (before_share - after_share)).sum()))
        results.append(HuffResult(site_id=site.id, captured_patients=captured, cannibalized_patients=cannibalized,
                                  net_new_patients=captured - cannibalized))
    return results


# ---------------------------------------------------------------------------
# Site quality, hard constraints, final score, sensitivity
# ---------------------------------------------------------------------------
def site_quality(site: SiteCandidate, cons: Constraints) -> tuple[float, str | None]:
    """Q in [0, 1] (mean of parking, ground-floor, size and rent fit; unknown parts 0.5) and the hard-constraint
    failure, if any (only judged on known values)."""
    if site.sqft is not None and not cons.min_sqft <= site.sqft <= cons.max_sqft:
        return 0.0, f"size {site.sqft} sq ft outside {cons.min_sqft}-{cons.max_sqft}"
    if cons.ground_floor and site.floor is not None and site.floor > 1:
        return 0.0, f"floor {site.floor}; ground floor required"
    if cons.max_rent_psf is not None and site.rent_psf is not None and site.rent_psf > cons.max_rent_psf:
        return 0.0, f"rent ${site.rent_psf:.2f}/sq ft above max ${cons.max_rent_psf:.2f}"

    ratio = site.checks.get("parking_ratio_per_1000sf")
    if ratio is None and site.parking_spaces is not None and site.sqft:
        ratio = site.parking_spaces / (site.sqft / 1000)
    parking = 0.5 if ratio is None else min(1.0, float(ratio) / max(cons.min_parking_per_1000sf, 1e-9))
    ground = 0.5 if site.floor is None else (1.0 if site.floor == 1 or not cons.ground_floor else 0.0)
    size = 0.5 if site.sqft is None else 1.0
    rent = 0.5 if site.rent_psf is None else 1.0
    return (parking + ground + size + rent) / 4, None


def rank(grid: Grid, pack: StockPack, profile: PracticeProfile, spec: ScoringSpec, candidates: list[SiteCandidate],
         competitor_feature: str | None, with_stability: bool, draws: int = 200, kappa: float = 50.0,
         model: DemandModel | None = None) -> list[RankedSite]:
    _, weights, c = contribution_matrix(grid, pack, spec)
    zone = c @ weights
    huffs = {h.site_id: h for h in huff(grid, pack, profile, spec, candidates, competitor_feature, model)}

    rows = []
    for site in candidates:
        cell = grid.index.get(site.h3) if site.h3 else grid.cell_of(site.lat, site.lon)
        q, excluded = site_quality(site, profile.constraints)
        if cell is None:
            excluded = excluded or "outside the region grid"
        rows.append((site, cell, q, excluded))

    eligible = [r for r in rows if r[3] is None]
    net_pct = percentile(np.array([huffs[r[0].id].net_new_patients for r in eligible], dtype=float))
    zone_s = np.array([zone[r[1]] for r in eligible])
    quality = np.array([r[2] for r in eligible])
    final = spec.alpha * net_pct + (1 - spec.alpha) * (0.6 * zone_s + 0.4 * quality)
    stability = (_stability(c[[r[1] for r in eligible]], weights, spec.alpha, net_pct, quality, draws, kappa)
                 if with_stability and eligible else None)

    ranked = []
    for k, (site, cell, q, _) in enumerate(eligible):
        h = huffs[site.id]
        ranked.append(RankedSite(
            site_id=site.id, net_new_patients=h.net_new_patients, captured_patients=h.captured_patients,
            cannibalized_patients=h.cannibalized_patients, zone_score=round(float(zone_s[k]), 4),
            site_quality=round(float(q), 4), final_score=round(float(final[k]), 4),
            stability=None if stability is None else round(float(stability[k]), 3),
        ))
    ranked.sort(key=lambda r: (-r.final_score, r.site_id))
    for site, cell, q, excluded in rows:
        if excluded is not None:
            h = huffs[site.id]
            ranked.append(RankedSite(
                site_id=site.id, net_new_patients=h.net_new_patients, captured_patients=h.captured_patients,
                cannibalized_patients=h.cannibalized_patients,
                zone_score=round(float(zone[cell]), 4) if cell is not None else 0.0,
                site_quality=0.0, final_score=0.0, excluded_reason=excluded,
            ))
    return ranked


def _stability(c_sites: np.ndarray, weights: np.ndarray, alpha: float, net_pct: np.ndarray, quality: np.ndarray,
               draws: int, kappa: float) -> np.ndarray:
    """Share of Dirichlet(κ·w) weight draws in which each site stays in the top 10 (seed 42)."""
    rng = np.random.default_rng(42)
    w = rng.dirichlet(kappa * weights, size=draws)  # draws × features
    zone = c_sites @ w.T  # sites × draws
    final = alpha * net_pct[:, None] + (1 - alpha) * (0.6 * zone + 0.4 * quality[:, None])
    k = min(TOP_K, len(net_pct))
    top = np.argsort(-final, axis=0, kind="stable")[:k]  # k × draws
    hits = np.zeros(len(net_pct))
    np.add.at(hits, top.ravel(), 1)
    return hits / draws


def sensitivity(grid: Grid, pack: StockPack, profile: PracticeProfile, spec: ScoringSpec,
                candidates: list[SiteCandidate], draws: int, kappa: float,
                model: DemandModel | None = None) -> list[SensitivityResult]:
    ranked = rank(grid, pack, profile, spec, candidates, None, with_stability=True, draws=draws, kappa=kappa, model=model)
    return [SensitivityResult(site_id=r.site_id, stability=r.stability or 0.0) for r in ranked]


# ---------------------------------------------------------------------------
# Community need: who lives in a site's catchment
# ---------------------------------------------------------------------------
LOWER_INCOME = 0.8  # HUD's low-and-moderate income line: 80% of the area median


def community_need(grid: Grid, lat: float, lon: float, minutes: float) -> CommunityNeed:
    """Household- and population-weighted shares over the cells within `minutes` of the site (Census columns)."""
    t = travel_minutes(grid.lat, grid.lon, lat, lon)
    inside = t <= minutes
    col = lambda name: np.nan_to_num(grid.columns[name], nan=0.0) if name in grid.columns else None  # noqa: E731
    hh, pop = col("households"), col("pop_total")
    out = CommunityNeed(minutes=minutes)
    if hh is None or not inside.any():
        return out
    total_hh = float(hh[inside].sum())
    out.households = round(total_hh)
    if total_hh <= 0:
        return out
    share = lambda num: round(float(num[inside].sum()) / total_hh, 4)  # noqa: E731
    if (no_car := col("no_vehicle_households")) is not None:
        out.no_car_share = share(no_car)
    if (renters := col("renter_households")) is not None:
        out.renter_share = share(renters)
    if "median_hh_income" in grid.columns and pop is not None:
        income = grid.columns["median_hh_income"]
        known = ~np.isnan(income) & (pop > 0)
        # Region median of tract incomes, weighted by population (same for every site in the run).
        order = np.argsort(income[known])
        cum = np.cumsum(pop[known][order])
        region_median = float(income[known][order][np.searchsorted(cum, cum[-1] / 2)])
        low = inside & known & (income < LOWER_INCOME * region_median)
        out.lower_income_share = round(float(hh[low].sum()) / total_hh, 4)
    if "share_any_medicaid" in grid.columns and pop is not None and float(pop[inside].sum()) > 0:
        medicaid = np.nan_to_num(grid.columns["share_any_medicaid"], nan=0.0)
        out.medicaid_share = round(float((medicaid * pop)[inside].sum() / pop[inside].sum()), 4)
    return out
