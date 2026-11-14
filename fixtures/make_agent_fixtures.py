"""Generate the agent fixtures from fixtures/grid.json and evals/pediatric_dentist.yaml.

Writes fixtures/zones.json (ScoreResponse), fixtures/report.json (Report) and fixtures/events.jsonl (one RunEvent per
line): a fake pediatric-dentist run. Stock feature values are the real Census / CMS grid values; zone scores, net-new,
site quality, final score and stability come from the real math (services/agent/tools/geomath.py). Invented: the
three "agent-found" feature columns, the sites (no CoStar data may be committed) and the event timeline.
Every number in a report claim is copied from a stored value or model output, so verify_claims can be exercised.
Run: PYTHONPATH=. python fixtures/make_agent_fixtures.py   (after fixtures/make_grid_fixture.py)
"""

import math
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import h3
import numpy as np
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
    ScoringSpec,
    SiteCandidate,
)
from services.agent.tools import geomath
from services.agent.tools.geo_client import MockGeo
from services.agent.tools.packs import PROVENANCE, pack_for

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "fixtures"
RUN_ID = "fixture-peds-001"
START = datetime(2026, 11, 14, 5, 0, 0, tzinfo=timezone.utc)
COMPETITOR = "peds_dentists"  # fake per-cell count, stands in for NPPES pediatric dentists

SOURCES = {
    "pop_u18_within_5km": PROVENANCE["acs"],
    "medicaid_u19_within_5km": PROVENANCE["acs"],
    "share_spanish": PROVENANCE["acs"],
    "pop_growth_pct": PROVENANCE["acs"],
    "n_pediatrics_within_5km": PROVENANCE["cms.doctors"],
    "pop_density_km2": PROVENANCE["acs"],
    "transit_stop_km": "https://www.ridemetro.org/",
    "schools_within_5km": "https://wiki.openstreetmap.org/wiki/Overpass_API",
    "peds_dentists_within_5km": "https://npiregistry.cms.hhs.gov/api-page",
}

FACTORS = [
    Factor(id="children_nearby", factor="Children living nearby", why="Every patient is a child; more children within a short drive means more first visits and recalls.", proxy="Children under 18 within 5 km of each cell", direction=1, priority="high", stock_covered=True, feature_id="pop_u18_within_5km", status="covered"),
    Factor(id="medicaid_chip_families", factor="Medicaid and CHIP families", why="The practice is built around Medicaid and CHIP; most commercial-only offices turn these families away.", proxy="Children on Medicaid or CHIP within 5 km", direction=1, priority="high", stock_covered=True, feature_id="medicaid_u19_within_5km", status="covered"),
    Factor(id="spanish_speaking_households", factor="Spanish-speaking households", why="Spanish-speaking staff are the practice's edge; parents choose offices where they can talk to the front desk.", proxy="Share of residents who speak Spanish at home", direction=1, priority="medium", stock_covered=True, feature_id="share_spanish", status="covered"),
    Factor(id="families_without_car", factor="Families without a car", why="Medicaid families miss appointments when the office isn't near a bus line.", proxy="Distance from each cell to the nearest METRO bus stop", direction=-1, priority="high", status="searching"),
    Factor(id="pediatric_dentist_competition", factor="Few pediatric dentists nearby", why="Fewer pediatric dentists within a short trip means less competition for each child.", proxy="Pediatric dentists (NPPES taxonomy 1223P0221X) within 5 km", direction=-1, priority="high", status="searching"),
    Factor(id="schools_nearby", factor="Schools nearby", why="School screenings and after-school slots bring families in; offices near schools are on the daily route.", proxy="K-12 schools within 5 km", direction=1, priority="medium", status="searching"),
    Factor(id="young_family_growth", factor="Growing neighborhoods", why="Neighborhoods that are adding residents keep adding young families and new patients.", proxy="Percent population change 2020-24", direction=1, priority="low", stock_covered=True, feature_id="pop_growth_pct", status="covered"),
    Factor(id="pediatrician_referrals", factor="Pediatricians nearby", why="Pediatricians refer for the first dental visit at age one.", proxy="Pediatricians within 5 km", direction=1, priority="low", stock_covered=True, feature_id="n_pediatrics_within_5km", status="covered"),
    Factor(id="population_density", factor="Dense neighborhoods", why="Dense areas give more patients within a short trip, which matters for families without cars.", proxy="People per km² in each cell", direction=1, priority="low", stock_covered=True, feature_id="pop_density_km2", status="covered"),
]
FOUND = {
    "families_without_car": ("transit_stop_km", "found", None),
    "pediatric_dentist_competition": ("peds_dentists_within_5km", "proxy", "NPPES lists registered practice addresses, which can include inactive locations; counts are an upper bound."),
    "schools_nearby": ("schools_within_5km", "found", None),
}
CANDIDATES = [
    DatasetCandidate(id="cand_metro_gtfs", factor_id="families_without_car", title="Houston METRO GTFS feed (stops.txt)", url="https://www.ridemetro.org/", publisher="Houston METRO", format="gtfs", geo_level="point", why="Every METRO bus stop with coordinates; distance to the nearest stop measures transit access.", status="valid"),
    DatasetCandidate(id="cand_nppes_peds", factor_id="pediatric_dentist_competition", title="NPPES NPI Registry: pediatric dentistry (1223P0221X)", url=SOURCES["peds_dentists_within_5km"], publisher="CMS", format="json", geo_level="point", why="Registered pediatric dentists with practice addresses; the CMS clinician file has almost no dentists.", status="valid"),
    DatasetCandidate(id="cand_tea_directory", factor_id="schools_nearby", title="TEA school directory", url="https://tea.texas.gov/", publisher="Texas Education Agency", format="csv", geo_level="point", why="Official list of Texas public schools.", status="invalid"),
    DatasetCandidate(id="cand_osm_schools", factor_id="schools_nearby", title="OpenStreetMap schools (amenity=school) via Overpass", url=SOURCES["schools_within_5km"], publisher="OpenStreetMap contributors", format="geojson", geo_level="point", why="School points with coordinates for the whole region, public and private.", status="valid"),
]
NAMED_PLACES = [
    (29.721, -95.476, "Gulfton", ["Hillcroft Ave", "Bellaire Blvd", "Gulfton St"]),
    (29.700, -95.530, "Sharpstown", ["Bellaire Blvd", "Fondren Rd", "Bissonnet St"]),
    (29.690, -95.200, "Pasadena", ["Spencer Hwy", "Red Bluff Rd", "Southmore Ave"]),
    (29.850, -95.260, "Northeast Houston", ["Tidwell Rd", "Mesa Dr", "Homestead Rd"]),
    (29.760, -95.370, "Downtown / EaDo", ["Navigation Blvd", "Harrisburg Blvd", "Telephone Rd"]),
    (29.900, -95.400, "Aldine", ["Aldine Mail Route Rd", "Airline Dr", "W Gulf Bank Rd"]),
    (29.640, -95.400, "South Houston", ["W Fuqua St", "Hiram Clarke Rd", "S Post Oak Rd"]),
    (29.780, -95.580, "Spring Branch", ["Long Point Rd", "Gessner Rd", "Hammerly Blvd"]),
    (29.800, -95.180, "Channelview", ["Market St", "Sheldon Rd", "Woodforest Blvd"]),
    (29.940, -95.550, "Northwest Houston", ["W Little York Rd", "Fairbanks N Houston Rd", "Antoine Dr"]),
]


def add_agent_found(grid: geomath.Grid) -> None:
    """Fake values for the three features the scouts 'find'; real ones arrive with ingestion (Task 6)."""
    density = np.nan_to_num(grid.columns["pop_density_km2"], nan=0.0)
    kids = np.nan_to_num(grid.columns["pop_u18_within_5km"], nan=0.0)
    # Fake dental supply: dentists cluster where pediatric and primary care clinicians are.
    peds = np.nan_to_num(grid.columns["n_pediatrics"], nan=0.0) + np.nan_to_num(grid.columns["n_primary_care"], nan=0.0)
    grid.columns["transit_stop_km"] = np.round(0.1 + 6 * np.exp(-density / 600), 2)
    grid.columns["schools_within_5km"] = np.round(kids / 1800)
    grid.columns[COMPETITOR] = np.round(peds / 2)
    grid.columns["peds_dentists_within_5km"] = np.round(np.nan_to_num(grid.columns["n_pediatrics_within_5km"], nan=0.0) / 3)


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


def pick_zones(top: list[str], n: int, min_rings: int = 7) -> list[str]:
    chosen: list[str] = []
    for cell in top:
        if all(h3.grid_distance(cell, c) >= min_rings for c in chosen):
            chosen.append(cell)
        if len(chosen) == n:
            break
    return chosen


def nearest_place(lat: float, lon: float) -> tuple[str, list[str]]:
    best = min(NAMED_PLACES, key=lambda p: math.hypot(lat - p[0], lon - p[1]))
    return best[2], best[3]


def fake_sites(zones: list[str], profile: PracticeProfile) -> list[SiteCandidate]:
    rng = random.Random(7)
    cons = profile.constraints
    sites = []
    for i, zone in enumerate(zones, start=1):
        lat, lon = h3.cell_to_latlng(zone)
        place, streets = nearest_place(lat, lon)
        sqft = rng.randrange(cons.min_sqft, cons.max_sqft + 1, 100)
        sites.append(SiteCandidate(
            id=f"site_{i:02d}", address=f"{rng.randrange(1200, 9800, 2)} {rng.choice(streets)}, Houston, TX",
            lat=round(lat, 6), lon=round(lon, 6), h3=zone, submarket=place, sqft=sqft,
            rent_psf=round(rng.uniform(19, 31), 2), floor=1, building_class=rng.choice(["A", "B", "B", "C"]),
            available=rng.random() > 0.2 or None, parking_spaces=round(sqft / 1000 * rng.uniform(1.8, 5.5)),
            parking_estimated=rng.random() < 0.3, source="fixture",
        ))
    return sites


def build_report(grid: geomath.Grid, profile: PracticeProfile, factors: list[Factor], spec: ScoringSpec, top: list[str]) -> Report:
    pack = pack_for(profile)
    sites = fake_sites(pick_zones(top, 14), profile)
    ranked = geomath.rank(grid, pack, profile, spec, sites, COMPETITOR, with_stability=True)
    by_id = {s.id: s for s in sites}
    recs = []
    for r in [r for r in ranked if r.excluded_reason is None][:10]:
        s = by_id[r.site_id]
        cell = grid.index[s.h3 or ""]
        kids = grid.columns["pop_u18_within_5km"][cell]
        spanish = grid.columns["share_spanish"][cell]
        claims = [
            Claim(text=f"Estimated {r.net_new_patients:,.0f} net-new patients a year"
                       + (f", after {r.cannibalized_patients:,.0f} shift over from Office A." if r.cannibalized_patients >= 1 else "."),
                  evidence_refs=[f"model:net_new@{s.id}"] + ([f"model:cannibalized@{s.id}"] if r.cannibalized_patients >= 1 else [])),
            Claim(text=f"{kids:,.0f} children live within 5 km.", evidence_refs=[f"feature:pop_u18_within_5km@{s.h3}"]),
            Claim(text=f"{spanish * 100:.0f}% of residents speak Spanish at home.", evidence_refs=[f"feature:share_spanish@{s.h3}"]),
            Claim(text=f"{s.sqft:,} sq ft on the ground floor with {s.parking_spaces} parking spaces.", evidence_refs=[f"site:sqft@{s.id}", f"site:parking_spaces@{s.id}"]),
        ]
        caveats = ["Parking count estimated from aerial imagery."] if s.parking_estimated else []
        if s.available is None:
            caveats.append("The source does not say whether space is available; treat as a candidate building.")
        recs.append(Recommendation(rank=len(recs) + 1, site=s, net_new_patients=r.net_new_patients, zone_score=r.zone_score,
                                   site_quality=r.site_quality, final_score=r.final_score, stability=r.stability,
                                   justification=claims, caveats=caveats))
    return Report(
        run_id=RUN_ID, profile=profile, factors=factors, recommendations=recs,
        caveats=[
            "Fixture data: sites, the three agent-found features and the run timeline are invented; stock values are real Census / CMS grid values.",
            "Plan-level insurance coverage is not public at tract level; payer shares come from ACS.",
            "Visit rates per child are not sourced yet, so net-new counts children in reach, not visits.",
            FOUND["pediatric_dentist_competition"][2],
        ],
        sources={fid: url for fid, url in SOURCES.items() if fid in spec.weights},  # type: ignore[misc]
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
    result(FA, "list_stock_features", "Stock features: population and children within 5 km, payer shares, Spanish and limited-English shares, income, growth, clinician supply, hospitals.")
    for f in FACTORS:
        emit(FA, "factor", f.model_dump(mode="json"))
    emit(S, "todo", todos(2))

    tr, dn, sc = "families_without_car", "pediatric_dentist_competition", "schools_nearby"
    cand = {c.id: c for c in CANDIDATES}
    for fid, q in [(tr, "bus stops transit"), (dn, "pediatric dentists"), (sc, "schools")]:
        call(scout(fid), "catalog_search", {"query": q}, fid)
    result(scout(tr), "catalog_search", "1 match: Houston METRO GTFS feed (connector gtfs.metro).", factor_id=tr)
    result(scout(sc), "catalog_search", "1 match: TEA school directory.", factor_id=sc)
    result(scout(dn), "catalog_search", "No catalog entry lists pediatric dentists.", ok=False, factor_id=dn)
    call(scout(dn), "web_search", {"query": "NPPES pediatric dentistry taxonomy 1223P0221X provider addresses"}, dn)
    call(scout(tr), "preview_dataset", {"url": str(cand["cand_metro_gtfs"].url), "format": "gtfs"}, tr)
    call(scout(sc), "preview_dataset", {"url": str(cand["cand_tea_directory"].url)}, sc)
    result(scout(dn), "web_search", "Allowlisted results; best match: NPPES NPI Registry API (npiregistry.cms.hhs.gov).", factor_id=dn)
    result(scout(tr), "preview_dataset", "GTFS stops.txt with stop_lat and stop_lon; points fall inside the region. Ingestible.", factor_id=tr)
    result(scout(sc), "preview_dataset", "Not ingestible: Excel file with merged headers and no coordinates. Retrying (attempt 2 of 3).", ok=False, factor_id=sc)
    call(scout(sc), "web_search", {"query": "Houston school locations points open data"}, sc)
    call(scout(dn), "preview_dataset", {"url": str(cand["cand_nppes_peds"].url), "format": "json"}, dn)
    result(scout(sc), "web_search", "Best match: OpenStreetMap schools (amenity=school) via Overpass.", factor_id=sc)
    result(scout(dn), "preview_dataset", "Provider records with practice addresses; filter by taxonomy, geocode through connector nppes.registry. Ingestible.", factor_id=dn)
    call(scout(sc), "preview_dataset", {"url": str(cand["cand_osm_schools"].url), "format": "geojson"}, sc)
    result(scout(sc), "preview_dataset", "GeoJSON points with names; points fall inside the region. Ingestible.", factor_id=sc)
    emit(S, "todo", todos(3))

    emit(S, "approval", ApprovalRequest(approval_id="appr_data_plan", kind="data_plan", factors=FACTORS, candidates=CANDIDATES).model_dump(mode="json"))
    call(S, "register_features", {"specs": [FOUND[f][0] for f in (tr, dn, sc)]})
    result(S, "register_features", "3 of 3 features ingested; every coverage check passed.")
    by_id = {f.id: f for f in final_factors}
    for fid in (tr, dn, sc):
        emit(S, "factor", by_id[fid].model_dump(mode="json"))
    emit(S, "todo", todos(4))

    emit(S, "approval", ApprovalRequest(approval_id="appr_weights", kind="weights", factors=final_factors, spec=spec).model_dump(mode="json"))
    call(S, "score_grid", {"features": len(spec.weights), "top_n": 15})
    result(S, "score_grid", f"Scored {n_cells:,} cells; top 15 zones written to zones.json.")
    emit(S, "file", {"name": "zones.json", "path": "/zones.json"})
    emit(S, "todo", todos(5))

    call("site-scout", "search_sites", {"zones": 15, "min_sqft": profile.constraints.min_sqft, "max_sqft": profile.constraints.max_sqft})
    result("site-scout", "search_sites", "14 candidate spaces in the top zones.")
    emit(S, "todo", todos(6))
    call(S, "rank_sites", {"candidates": 14})
    result(S, "rank_sites", f"Ranked the candidates; top {len(report.recommendations)} kept.")
    emit(S, "todo", todos(7))

    call("analyst-critic", "verify_claims", {"report": "report.json"})
    result("analyst-critic", "verify_claims", "Every number in every claim matches a stored value.")
    emit("analyst-critic", "file", {"name": "report.json", "path": "/report.json"})
    emit(S, "todo", todos(len(steps)))
    emit(S, "done", {"report": "report.json"})
    return events


def main() -> None:
    grid = MockGeo().grid
    add_agent_found(grid)
    case = yaml.safe_load((ROOT / "evals" / "pediatric_dentist.yaml").read_text())
    profile = PracticeProfile(**case["profile"])

    final_factors = []
    for f in FACTORS:
        if f.id in FOUND:
            fid, status, caveat = FOUND[f.id]
            f = f.model_copy(update={"feature_id": fid, "status": status, "caveat": caveat})
        final_factors.append(f)
    spec = weights(final_factors)

    zones = geomath.score(grid, pack_for(profile), profile, spec, "hou", top_n=15)
    report = build_report(grid, profile, final_factors, spec, [z.h3 for z in zones.zones[:400]])
    events = build_events(profile, final_factors, spec, report, len(grid))

    (FIXTURES / "zones.json").write_text(zones.model_dump_json() + "\n")
    (FIXTURES / "report.json").write_text(report.model_dump_json(indent=2) + "\n")
    (FIXTURES / "events.jsonl").write_text("".join(e.model_dump_json() + "\n" for e in events))
    print(f"wrote zones.json ({len(zones.zones)} zones), report.json ({len(report.recommendations)} recommendations), events.jsonl ({len(events)} events)")


if __name__ == "__main__":
    main()
