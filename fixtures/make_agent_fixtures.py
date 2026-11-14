"""Generate the agent fixtures from fixtures/fake_grid.json and evals/pediatric_dentist.yaml.

Writes fixtures/zones.json (ScoreResponse), fixtures/report.json (Report) and fixtures/events.jsonl (one RunEvent per
line): a fake pediatric-dentist run. Every value is invented, but every number in a report claim is copied from the
fake grid or from the fake model outputs below, so verify_claims can be exercised against fixtures.
Run: PYTHONPATH=. python fixtures/make_agent_fixtures.py   (after fixtures/make_fake_grid.py)
"""

import json
import math
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import h3
import yaml

from contracts.models import (
    ApprovalRequest,
    Claim,
    DatasetCandidate,
    EventType,
    Factor,
    PracticeProfile,
    Recommendation,
    Report,
    RunEvent,
    ScoreResponse,
    ScoringSpec,
    SiteCandidate,
    ZoneScore,
)

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "fixtures"
RUN_ID = "fixture-peds-001"
START = datetime(2026, 11, 14, 5, 0, 0, tzinfo=timezone.utc)

ACS = "https://api.census.gov/data/2023/acs/acs5"
SOURCES = {
    "pop_0_17_density": f"{ACS}/groups/B01001.html",
    "pop_total_density": f"{ACS}/groups/B01001.html",
    "pop_growth": f"{ACS}/groups/B01001.html",
    "ins_medicaid_share": f"{ACS}/groups/B27010.html",
    "primary_care_15m": "https://data.cms.gov/provider-data/dataset/mj5m-pzi6",
    "spanish_home_share": f"{ACS}/groups/C16001.html",
    "schools_10m": "https://wiki.openstreetmap.org/wiki/Overpass_API",
    "transit_stop_minutes": "https://www.ridemetro.org/",
    "peds_dentists_15m": "https://npiregistry.cms.hhs.gov/api-page",
}

FACTORS = [
    Factor(id="children_nearby", factor="Children living nearby", why="Every patient is a child; more children within a short drive means more first visits and recalls.", proxy="Children under 18 per km² in each cell", direction=1, priority="high", stock_covered=True, feature_id="pop_0_17_density", status="covered"),
    Factor(id="medicaid_chip_families", factor="Medicaid and CHIP families", why="The practice is built around Medicaid and CHIP; most commercial-only offices turn these families away.", proxy="Share of residents covered by Medicaid (CHIP is reported inside the Medicaid count)", direction=1, priority="high", stock_covered=True, feature_id="ins_medicaid_share", status="covered"),
    Factor(id="families_without_car", factor="Families without a car", why="Medicaid families miss appointments when the office isn't near a bus line.", proxy="Drive-time minutes from each cell to the nearest METRO bus stop", direction=-1, priority="high", feature_id=None, status="searching"),
    Factor(id="spanish_speaking_households", factor="Spanish-speaking households", why="Spanish-speaking staff are the practice's edge; parents choose offices where they can talk to the front desk.", proxy="Share of households that speak Spanish at home, by tract", direction=1, priority="medium", status="searching"),
    Factor(id="pediatric_dentist_competition", factor="Few pediatric dentists nearby", why="Fewer pediatric dentists within 15 minutes means less competition for each child.", proxy="Pediatric dentists (NPPES taxonomy 1223P0221X) within 15 drive minutes", direction=-1, priority="high", status="searching"),
    Factor(id="schools_nearby", factor="Schools nearby", why="School screenings and after-school slots bring families in; offices near schools are on the daily route.", proxy="K-12 schools within 10 drive minutes", direction=1, priority="medium", status="searching"),
    Factor(id="young_family_growth", factor="Growing neighborhoods", why="Neighborhoods that are adding residents keep adding young families and new patients.", proxy="Percent population change over five years", direction=1, priority="low", stock_covered=True, feature_id="pop_growth", status="covered"),
    Factor(id="pediatrician_referrals", factor="Pediatric and family doctors nearby", why="Pediatricians refer for the first dental visit at age one.", proxy="Primary care clinicians within 15 drive minutes", direction=1, priority="low", stock_covered=True, feature_id="primary_care_15m", status="covered"),
    Factor(id="population_density", factor="Dense neighborhoods", why="Dense areas give more patients within a short trip, which matters for families without cars.", proxy="People per km² in each cell", direction=1, priority="low", stock_covered=True, feature_id="pop_total_density", status="covered"),
]
FOUND = {
    "families_without_car": ("transit_stop_minutes", "found", None),
    "spanish_speaking_households": ("spanish_home_share", "found", None),
    "pediatric_dentist_competition": ("peds_dentists_15m", "proxy", "NPPES lists registered practice addresses, which can include inactive locations; counts are an upper bound."),
    "schools_nearby": ("schools_10m", "found", None),
}
CANDIDATES = [
    DatasetCandidate(id="cand_metro_gtfs", factor_id="families_without_car", title="Houston METRO GTFS feed (stops.txt)", url="https://www.ridemetro.org/", publisher="Houston METRO", format="gtfs", geo_level="point", why="Every METRO bus stop with coordinates; distance to the nearest stop measures transit access.", status="valid"),
    DatasetCandidate(id="cand_acs_c16001", factor_id="spanish_speaking_households", title="ACS 5-year C16001: Language spoken at home", url=SOURCES["spanish_home_share"], publisher="U.S. Census Bureau", format="census_api", geo_level="tract", why="Tract-level counts of households speaking Spanish at home.", status="valid"),
    DatasetCandidate(id="cand_nppes_peds", factor_id="pediatric_dentist_competition", title="NPPES NPI Registry: pediatric dentistry (1223P0221X)", url=SOURCES["peds_dentists_15m"], publisher="CMS", format="json", geo_level="point", why="Registered pediatric dentists with practice addresses; no catalog dataset lists them.", status="valid"),
    DatasetCandidate(id="cand_tea_directory", factor_id="schools_nearby", title="TEA school directory", url="https://tea.texas.gov/", publisher="Texas Education Agency", format="csv", geo_level="point", why="Official list of Texas public schools.", status="invalid"),
    DatasetCandidate(id="cand_osm_schools", factor_id="schools_nearby", title="OpenStreetMap schools (amenity=school) via Overpass", url=SOURCES["schools_10m"], publisher="OpenStreetMap contributors", format="geojson", geo_level="point", why="School points with coordinates for the whole region, public and private.", status="valid"),
]
EXISTING_OFFICE = (29.721, -95.476)
NAMED_PLACES = [
    (29.721, -95.476, "Gulfton", ["Hillcroft Ave", "Bellaire Blvd", "Gulfton St"]),
    (29.700, -95.530, "Sharpstown", ["Bellaire Blvd", "Fondren Rd", "Bissonnet St"]),
    (29.690, -95.200, "Pasadena", ["Spencer Hwy", "Red Bluff Rd", "Southmore Ave"]),
    (29.850, -95.260, "Northeast Houston", ["Tidwell Rd", "Mesa Dr", "Homestead Rd"]),
    (29.760, -95.370, "Downtown / EaDo", ["Navigation Blvd", "Harrisburg Blvd", "Telephone Rd"]),
    (29.660, -95.110, "Deer Park", ["Center St", "Pasadena Blvd", "East Blvd"]),
    (29.900, -95.400, "Aldine", ["Aldine Mail Route Rd", "Airline Dr", "W Gulf Bank Rd"]),
    (29.640, -95.400, "South Houston", ["W Fuqua St", "Hiram Clarke Rd", "S Post Oak Rd"]),
    (29.780, -95.580, "Spring Branch", ["Long Point Rd", "Gessner Rd", "Hammerly Blvd"]),
    (29.560, -95.290, "Pearland", ["Broadway St", "Dixie Farm Rd", "Main St"]),
    (29.800, -95.180, "Channelview", ["Market St", "Sheldon Rd", "Woodforest Blvd"]),
]


def travel_minutes(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Straight-line km × 1.3 at 35 mph, floored at 2 minutes (docs/DESIGN.md)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    km = 2 * 6371.0 * math.asin(math.sqrt(a))
    return max(2.0, km * 1.3 / 56.33 * 60)


def percentile(values: list[float]) -> list[float]:
    """Percentile rank in (0, 1], ties share their average rank."""
    n = len(values)
    order = sorted(range(n), key=values.__getitem__)
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j + 2) / 2 / n
        i = j + 1
    return ranks


def add_agent_found(rows: list[dict[str, Any]]) -> None:
    """Fake values for the four features the scouts 'find' (not stored in fake_grid.json)."""
    for r in rows:
        r["spanish_home_share"] = round(min(0.85, max(0.03, 2.6 * (r["ins_medicaid_share"] - 0.07))), 4)
        r["transit_stop_minutes"] = round(2 + 25 * math.exp(-r["pop_total_density"] / 900), 1)
        r["schools_10m"] = round(r["pop_0_17_density"] / 45 + r["primary_care_15m"] / 40)
        r["peds_dentists_15m"] = round(r["primary_care_15m"] / 14)


def weights(factors: list[Factor]) -> ScoringSpec:
    points = {"high": 3, "medium": 2, "low": 1}
    total = sum(points[f.priority] for f in factors)
    w = {f.feature_id: round(points[f.priority] / total, 4) for f in factors if f.feature_id}
    last = list(w)[-1]
    w[last] = round(1 - sum(v for k, v in w.items() if k != last), 4)
    return ScoringSpec(
        weights=w,
        directions={f.feature_id: f.direction for f in factors if f.feature_id},
        rationale={f.feature_id: f"{f.priority} priority: {f.factor.lower()}" for f in factors if f.feature_id},
    )


def score(rows: list[dict[str, Any]], spec: ScoringSpec) -> tuple[list[ZoneScore], dict[str, float]]:
    pct = {fid: percentile([r[fid] for r in rows]) for fid in spec.weights}
    zones, demand = [], {}
    for i, r in enumerate(rows):
        contrib = {}
        for fid, w in spec.weights.items():
            c = pct[fid][i] if spec.directions.get(fid, 1) == 1 else 1 - pct[fid][i]
            contrib[fid] = round(w * c, 4)
        # Demand band 0-17, visit rate 1.0; π = residents on Medicaid/CHIP or commercial coverage.
        d = r["pop_0_17"] * (r["ins_medicaid_share"] + r["ins_private_share"])
        demand[r["h3"]] = d
        zones.append(ZoneScore(h3=r["h3"], score=round(sum(contrib.values()), 4), demand=round(d, 1), contributions=contrib))
    zones.sort(key=lambda z: z.score, reverse=True)
    return zones, demand


def huff(site: tuple[float, float], h: str, by_h3: dict[str, dict[str, Any]], demand: dict[str, float]) -> tuple[float, float]:
    """Fake Huff: captured and cannibalized patients from cells within 6 rings of the site."""
    captured = cannibalized = 0.0
    for c in h3.grid_disk(h, 6):
        r = by_h3.get(c)
        if r is None:
            continue
        competition = 0.03 * r["peds_dentists_15m"] + 0.15  # dentists and general practices nearby
        t_site = travel_minutes(r["lat"], r["lon"], *site) ** -2
        t_office = travel_minutes(r["lat"], r["lon"], *EXISTING_OFFICE) ** -2
        p_site = t_site / (t_site + t_office + competition)
        p_before = t_office / (t_office + competition)
        captured += demand[c] * p_site
        cannibalized += demand[c] * p_site * p_before
    return captured, cannibalized


def pick_sites(zones: list[ZoneScore], n: int, min_rings: int = 7) -> list[str]:
    chosen: list[str] = []
    for z in zones:
        if all(h3.grid_distance(z.h3, c) >= min_rings for c in chosen):
            chosen.append(z.h3)
        if len(chosen) == n:
            break
    return chosen


def nearest_place(lat: float, lon: float) -> tuple[str, list[str]]:
    best = min(NAMED_PLACES, key=lambda p: travel_minutes(lat, lon, p[0], p[1]))
    return best[2], best[3]


def build_report(rows: list[dict[str, Any]], profile: PracticeProfile, factors: list[Factor], spec: ScoringSpec, zones: list[ZoneScore], demand: dict[str, float]) -> Report:
    rng = random.Random(7)
    by_h3 = {r["h3"]: r for r in rows}
    zone_score = {z.h3: z.score for z in zones}
    cons = profile.constraints
    sites, results = [], []
    for i, h in enumerate(pick_sites(zones, 10), start=1):
        lat, lon = h3.cell_to_latlng(h)
        place, streets = nearest_place(lat, lon)
        sqft = rng.randrange(cons.min_sqft, cons.max_sqft + 1, 100)
        site = SiteCandidate(
            id=f"site_{i:02d}", address=f"{rng.randrange(1200, 9800, 2)} {rng.choice(streets)}, Houston, TX",
            lat=round(lat, 6), lon=round(lon, 6), h3=h, submarket=place, sqft=sqft,
            rent_psf=round(rng.uniform(19, 31), 2), floor=1, building_class=rng.choice(["A", "B", "B", "C"]),
            available=rng.random() > 0.2 or None, parking_spaces=round(sqft / 1000 * rng.uniform(1.8, 5.5)),
            parking_estimated=rng.random() < 0.3, source="fixture",
        )
        captured, cannibalized = huff((lat, lon), h, by_h3, demand)
        parking_fit = min(1.0, ((site.parking_spaces or 0) / (sqft / 1000)) / cons.min_parking_per_1000sf)
        quality = round((parking_fit + 1.0 + 1.0 + 1.0) / 4, 4)  # parking, ground floor, size, rent (no max rent)
        sites.append(site)
        results.append({"captured": round(captured), "cannibalized": round(cannibalized), "net_new": round(captured - cannibalized), "quality": quality, "zone": zone_score[h]})

    net_pct = percentile([r["net_new"] for r in results])
    for r, p in zip(results, net_pct):
        r["final"] = round(spec.alpha * p + (1 - spec.alpha) * (0.6 * r["zone"] + 0.4 * r["quality"]), 4)
    order = sorted(range(len(sites)), key=lambda k: results[k]["final"], reverse=True)
    stabilities = sorted((round(rng.uniform(0.55, 0.99), 2) for _ in order), reverse=True)

    recs = []
    for rank, (k, stab) in enumerate(zip(order, stabilities), start=1):
        s, r, g = sites[k], results[k], by_h3[sites[k].h3]
        claims = [
            Claim(text=f"Estimated {r['net_new']:,} net-new patients a year after {r['cannibalized']:,} shift over from Office A.", evidence_refs=[f"model:net_new@{s.id}", f"model:cannibalized@{s.id}"]),
            Claim(text=f"{g['pop_0_17_density']:,.0f} children per km² live in this zone.", evidence_refs=[f"feature:pop_0_17_density@{s.h3}"]),
            Claim(text=f"{g['ins_medicaid_share'] * 100:.0f}% of residents are covered by Medicaid or CHIP, which the practice accepts.", evidence_refs=[f"feature:ins_medicaid_share@{s.h3}"]),
            Claim(text=f"{s.sqft:,} sq ft on the ground floor with {s.parking_spaces} parking spaces.", evidence_refs=[f"site:sqft@{s.id}", f"site:parking_spaces@{s.id}"]),
        ]
        caveats = ["Parking count estimated from aerial imagery."] if s.parking_estimated else []
        if s.available is None:
            caveats.append("The source does not say whether space is available; treat as a candidate building.")
        recs.append(Recommendation(rank=rank, site=s, net_new_patients=r["net_new"], zone_score=r["zone"], site_quality=r["quality"], final_score=r["final"], stability=stab, justification=claims, caveats=caveats))

    return Report(
        run_id=RUN_ID, profile=profile, factors=factors, recommendations=recs,
        caveats=[
            "Fixture data: every number in this report is invented for development.",
            "Plan-level insurance coverage is not public at tract level; Medicaid share comes from ACS B27010.",
            FOUND["pediatric_dentist_competition"][2],
        ],
        sources={fid: url for fid, url in SOURCES.items() if fid in spec.weights},
        verified=True,
    )


def build_events(profile: PracticeProfile, final_factors: list[Factor], spec: ScoringSpec, report: Report, n_cells: int) -> list[RunEvent]:
    rng = random.Random(3)
    events: list[RunEvent] = []
    ts = START

    def emit(agent: str, type_: EventType, payload: dict[str, Any]) -> None:
        nonlocal ts
        events.append(RunEvent(run_id=RUN_ID, seq=len(events), ts=ts, agent=agent, type=type_, payload=payload))
        ts += timedelta(seconds=round(rng.uniform(1, 3), 2))

    steps = [
        "Load the practice profile",
        "Reason about what makes an office succeed",
        "Find data for uncovered factors",
        "Approve the data plan",
        "Approve weights and score Houston",
        "Find real spaces in the top zones",
        "Rank sites by net-new patients",
        "Write and verify the report",
    ]

    def todos(done: int) -> dict[str, Any]:
        return {"todos": [{"content": c, "status": "completed" if i < done else "in_progress" if i == done else "pending"} for i, c in enumerate(steps)]}

    def call(agent: str, tool: str, args: dict[str, Any], factor_id: str | None = None) -> None:
        emit(agent, "tool_call", {"tool": tool, "args": args, **({"factor_id": factor_id} if factor_id else {})})

    def result(agent: str, tool: str, summary: str, ok: bool = True, factor_id: str | None = None) -> None:
        emit(agent, "tool_result", {"tool": tool, "summary": summary, "ok": ok, **({"factor_id": factor_id} if factor_id else {})})

    S, FA = "site-strategist", "factor-analyst"

    def scout(fid: str) -> str:
        return f"dataset-scout:{fid}"

    emit(S, "todo", todos(0))
    call(S, "load_profile", {"name": profile.name, "specialty": profile.specialty})
    result(S, "load_profile", "Profile valid; 1 existing office already has coordinates; stock pack healthcare.pediatric_dentistry.")
    emit(S, "todo", todos(1))
    call(FA, "list_stock_features", {"region_id": "hou"})
    result(FA, "list_stock_features", "Stock features: population by age, insurance mix, income, growth, primary care and specialist supply.")
    for f in FACTORS:
        emit(FA, "factor", f.model_dump(mode="json"))
    emit(S, "todo", todos(2))

    tr, sp, dn, sc = "families_without_car", "spanish_speaking_households", "pediatric_dentist_competition", "schools_nearby"
    cand = {c.id: c for c in CANDIDATES}
    for fid, q in [(tr, "bus stops transit"), (sp, "language spoken at home Spanish"), (dn, "pediatric dentists"), (sc, "schools")]:
        call(scout(fid), "catalog_search", {"query": q}, fid)
    result(scout(tr), "catalog_search", "1 match: Houston METRO GTFS feed (connector gtfs.metro).", factor_id=tr)
    result(scout(sp), "catalog_search", "1 match: ACS 5-year C16001 language spoken at home, by tract.", factor_id=sp)
    result(scout(sc), "catalog_search", "1 match: TEA school directory.", factor_id=sc)
    result(scout(dn), "catalog_search", "No catalog entry lists pediatric dentists.", ok=False, factor_id=dn)
    call(scout(dn), "web_search", {"query": "NPPES pediatric dentistry taxonomy 1223P0221X provider addresses"}, dn)
    call(scout(tr), "preview_dataset", {"url": str(cand["cand_metro_gtfs"].url), "format": "gtfs"}, tr)
    call(scout(sc), "preview_dataset", {"url": str(cand["cand_tea_directory"].url)}, sc)
    result(scout(dn), "web_search", "Allowlisted results; best match: NPPES NPI Registry API (npiregistry.cms.hhs.gov).", factor_id=dn)
    result(scout(tr), "preview_dataset", "GTFS stops.txt with stop_lat and stop_lon; points fall inside the region. Ingestible.", factor_id=tr)
    result(scout(sc), "preview_dataset", "Not ingestible: Excel file with merged headers and no coordinates. Retrying (attempt 2 of 3).", ok=False, factor_id=sc)
    call(scout(sp), "preview_dataset", {"url": str(cand["cand_acs_c16001"].url), "format": "census_api"}, sp)
    call(scout(sc), "web_search", {"query": "Houston school locations points open data"}, sc)
    result(scout(sp), "preview_dataset", "Tract table keyed by GEO_ID with Spanish-at-home household counts. Ingestible.", factor_id=sp)
    call(scout(dn), "preview_dataset", {"url": str(cand["cand_nppes_peds"].url), "format": "json"}, dn)
    result(scout(sc), "web_search", "Best match: OpenStreetMap schools (amenity=school) via Overpass.", factor_id=sc)
    result(scout(dn), "preview_dataset", "Provider records with practice addresses; filter by taxonomy, geocode through connector nppes.registry. Ingestible.", factor_id=dn)
    call(scout(sc), "preview_dataset", {"url": str(cand["cand_osm_schools"].url), "format": "geojson"}, sc)
    result(scout(sc), "preview_dataset", "GeoJSON points with names; points fall inside the region. Ingestible.", factor_id=sc)
    emit(S, "todo", todos(3))

    emit(S, "approval", ApprovalRequest(approval_id="appr_data_plan", kind="data_plan", factors=FACTORS, candidates=CANDIDATES).model_dump(mode="json"))
    call(S, "register_features", {"specs": [FOUND[f][0] for f in (tr, sp, dn, sc)]})
    result(S, "register_features", "4 of 4 features ingested; every coverage check passed.")
    by_id = {f.id: f for f in final_factors}
    for fid in (tr, sp, dn, sc):
        emit(S, "factor", by_id[fid].model_dump(mode="json"))
    emit(S, "todo", todos(4))

    emit(S, "approval", ApprovalRequest(approval_id="appr_weights", kind="weights", factors=final_factors, spec=spec).model_dump(mode="json"))
    call(S, "score_grid", {"features": len(spec.weights), "top_n": 15})
    result(S, "score_grid", f"Scored {n_cells:,} cells; top 15 zones written to zones.json.")
    emit(S, "file", {"name": "zones.json", "path": "/zones.json"})
    emit(S, "todo", todos(5))

    call("site-scout", "search_sites", {"zones": 15, "min_sqft": profile.constraints.min_sqft, "max_sqft": profile.constraints.max_sqft})
    result("site-scout", "search_sites", f"{len(report.recommendations)} candidate spaces in the top zones.")
    emit(S, "todo", todos(6))
    call(S, "rank_sites", {"candidates": len(report.recommendations)})
    result(S, "rank_sites", "All candidates ranked; none excluded by hard constraints.")
    emit(S, "todo", todos(7))

    call("analyst-critic", "verify_claims", {"report": "report.json"})
    result("analyst-critic", "verify_claims", "Every number in every claim matches a stored value.")
    emit("analyst-critic", "file", {"name": "report.json", "path": "/report.json"})
    emit(S, "todo", todos(len(steps)))
    emit(S, "done", {"report": "report.json"})
    return events


def main() -> None:
    rows = json.loads((FIXTURES / "fake_grid.json").read_text())
    add_agent_found(rows)
    case = yaml.safe_load((ROOT / "evals" / "pediatric_dentist.yaml").read_text())
    profile = PracticeProfile(**case["profile"])

    final_factors = []
    for f in FACTORS:
        if f.id in FOUND:
            fid, status, caveat = FOUND[f.id]
            f = f.model_copy(update={"feature_id": fid, "status": status, "caveat": caveat})
        final_factors.append(f)
    spec = weights(final_factors)

    zones, demand = score(rows, spec)
    score_response = ScoreResponse(region_id="hou", zones=zones, top=[z.h3 for z in zones[:15]])
    report = build_report(rows, profile, final_factors, spec, zones, demand)
    events = build_events(profile, final_factors, spec, report, len(rows))

    (FIXTURES / "zones.json").write_text(score_response.model_dump_json() + "\n")
    (FIXTURES / "report.json").write_text(report.model_dump_json(indent=2) + "\n")
    (FIXTURES / "events.jsonl").write_text("".join(e.model_dump_json() + "\n" for e in events))
    print(f"wrote zones.json ({len(zones)} zones), report.json ({len(report.recommendations)} recommendations), events.jsonl ({len(events)} events)")


if __name__ == "__main__":
    main()
