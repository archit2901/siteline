"""One live run (AGENT_MODE=scripted): fixed step order in Python, models only for factors, datasets and claims.

Steps: profile -> factor-analyst -> parallel dataset-scouts -> data-plan approval -> register features -> weights
approval -> score the grid -> real spaces -> rank (net-new, quality, stability) -> critic report with verified claims.
Every step streams RunEvents (payload table in services/agent/CLAUDE.md) and writes run artifacts.
"""

import asyncio
import json
import os
from typing import Any

from langsmith import traceable

from pydantic import TypeAdapter

from contracts.models import (
    ApprovalRequest,
    DatasetCandidate,
    DemandModel,
    Factor,
    FeatureSpec,
    IngestRequest,
    PracticeProfile,
    RankedSite,
    RankRequest,
    ScoreRequest,
    ScoreResponse,
    ScoringSpec,
    SiteCandidate,
    SiteSearchRequest,
)
from services.agent import inspector, llm
from services.agent.critic import build_report
from services.agent.factor_analyst import analyze
from services.agent.runs import Run
from services.agent.profiler import profile_business
from services.agent.partners import partner_scouts
from services.agent.review import review
from services.agent.scout import ScoutResult, run_scout
from services.agent.tools import geomath
from services.agent.tools.backends import get_geo, get_sites
from services.agent.tools.ingest import overpass_url
from services.agent import strategy
from services.agent.tools.packs import pack_for
from services.agent.tools.profile import load_profile
from services.agent.tools.sites_client import MockSites
from services.agent.tools.weights import advise_weights, scorable

MAX_SCOUTS = int(os.environ.get("MAX_SCOUTS_PER_RUN", "10"))  # each scout costs ~2-3 model calls

S = "site-strategist"
I = "site-inspector"
P = "business-profiler"
STEPS = [
    "Understand the business and its customers",
    "Reason about what makes a location succeed",
    "Find data for uncovered factors",
    "Approve the data plan",
    "Weigh what matters most for this business",
    "Find real spaces in the top zones",
    "Rank sites by new customers won",
    "Inspect the top spaces from above and inside",
    "Write and verify the report",
]
SEARCH_ZONES = 400  # zones handed to the site search, best first (CoStar covers inner Harris only)
SITE_LIMIT = 40


def competitor_scout(factors: list[Factor], demand: DemandModel, tags: list[str],
                     run_tag: str) -> tuple[list[Factor], ScoutResult]:
    """Attach OpenStreetMap competitor locations to the analyst's competition factor (or add one)."""
    target = next((f for f in factors if f.feature_id is None and f.direction == -1
                   and "compet" in f"{f.id} {f.factor}".lower()), None)
    if target is None:
        target = Factor(id="competitors_nearby", factor=f"Few {demand.competitor_label} nearby",
                        why=f"Every nearby {demand.competitor_label.rstrip('s')} competes for the same customers.",
                        proxy=f"{demand.competitor_label.capitalize()} within 5 km (OpenStreetMap)", direction=-1,
                        priority="high")
        factors = [*factors, target]
    url = overpass_url(tags)
    spec = FeatureSpec(
        id=f"found_competitors_{run_tag}", label=f"{demand.competitor_label.capitalize()} within 5 km",
        source_id="osm.overpass", source_url=url, format="json", geo_level="point", geo_key="_lat,_lon",  # type: ignore[arg-type]
        agg="count_within", radius_min=7.0, direction=-1, unit="count", layer="agent_found",
        provenance_url="https://www.openstreetmap.org/",  # type: ignore[arg-type]
    )
    candidate = DatasetCandidate(
        id="cand_competitors", factor_id=target.id, title=f"OpenStreetMap: {demand.competitor_label} ({', '.join(tags)})",
        url="https://www.openstreetmap.org/", publisher="OpenStreetMap contributors", format="json",  # type: ignore[arg-type]
        geo_level="point", why="Locations of competing businesses across Houston.", status="valid",
    )
    return factors, ScoutResult(target.id, candidate, 1, spec=spec)


def todos(done: int) -> dict[str, Any]:
    return {"todos": [{"content": c, "status": "completed" if i < done else "in_progress" if i == done else "pending"}
                      for i, c in enumerate(STEPS)]}


def normalize(spec: ScoringSpec) -> ScoringSpec:
    weights = {k: v for k, v in spec.weights.items() if v > 0}
    total = sum(weights.values()) or 1.0
    return spec.model_copy(update={"weights": {k: round(v / total, 4) for k, v in weights.items()}})


async def score_and_rank(run: Run, spec: ScoringSpec) -> tuple[ScoreResponse, list[RankedSite]]:
    """geo /score then /rank on the run's sites (also used by /rescore; no LLM)."""
    profile: PracticeProfile = run.state["profile"]
    geo = get_geo()
    demand = run.state.get("demand")
    zones = await geo.score(ScoreRequest(profile=profile, spec=spec, top_n=15, demand=demand))
    sites: list[SiteCandidate] = run.state.get("sites", [])
    ranked = await geo.rank(RankRequest(profile=profile, spec=spec, candidates=sites, demand=demand)) if sites else []
    return zones, ranked


def agent_mode() -> str:
    return os.environ.get("AGENT_MODE", "").strip() or "scripted"


@traceable(name="siteline-run", run_type="chain")
async def run_pipeline(run: Run) -> None:
    meter = llm.UsageMeter(run_id=run.run_id)
    geo, sites_client = get_geo(), get_sites()
    caveats: list[str] = []

    # 1. profile
    await run.emit(S, "todo", todos(0))
    await run.emit(S, "tool_call", {"tool": "load_profile", "args": {"name": run.request.profile.name}})
    profile, notes = await load_profile(run.request.profile)
    run.state["profile"] = profile
    await run.emit(S, "tool_result", {"tool": "load_profile", "ok": True,
                                      "summary": " ".join([f"Stock pack {profile.stock_pack}.", *notes])})
    await run.emit(P, "tool_call", {"tool": "profile_business", "args": {"business": profile.business_type or profile.specialty}})
    demand, competitor_tags, notes = await profile_business(profile, meter)
    run.state["demand"] = demand
    if demand is not None:
        segments = ", ".join(f"{s.label} ({s.weight:g})" for s in demand.segments)
        summary = (f"Customers: {segments}. Competitors: {demand.competitor_label} "
                   f"({', '.join(competitor_tags) or 'none found'}). Trip: {demand.trip}. "
                   f"Space: {', '.join(demand.space_uses)}. {demand.rationale}")
    else:
        summary = " ".join(notes)
    await run.emit(P, "tool_result", {"tool": "profile_business", "ok": True, "summary": summary.strip()})

    # 2. factors
    await run.emit(S, "todo", todos(1))
    await run.emit("factor-analyst", "tool_call", {"tool": "list_stock_features", "args": {"region_id": run.request.region_id}})
    if agent_mode() == "planned":  # deepagents orchestrator delegates to the factor-analyst subagent
        from services.agent.graph import planned_factors

        planned = await planned_factors(profile, run.run_id, run.artifacts_dir / "agent", meter)
        factors, notes = (planned.factors, planned.notes) if planned.ok else await analyze(profile, meter)
    else:
        factors, notes = await analyze(profile, meter)
    await run.emit("factor-analyst", "tool_result", {"tool": "analyze_factors", "ok": True, "usage": meter.snapshot(),
                                                     "summary": f"{len(factors)} factors. " + " ".join(notes)})
    for f in factors:
        await run.emit("factor-analyst", "factor", f.model_dump(mode="json"))

    # 3. scouts, one per factor without data, in parallel; competitors come from OpenStreetMap
    await run.emit(S, "todo", todos(2))
    competitor: ScoutResult | None = None
    if demand is not None and competitor_tags:
        factors, competitor = competitor_scout(factors, demand, competitor_tags, run.run_id[:6])
        await run.emit("factor-analyst", "factor", next(f for f in factors if f.id == competitor.factor_id).model_dump(mode="json"))
        await run.emit(f"dataset-scout:{competitor.factor_id}", "tool_result", {
            "tool": "choose_dataset", "factor_id": competitor.factor_id, "ok": True,
            "summary": f"OpenStreetMap places tagged {', '.join(competitor_tags)} across Houston."})
    # partners named in the intake (schools the practice covers, hospitals where it operates): no model call
    factors, partners, partner_notes = partner_scouts(profile, factors, run.run_id[:6])
    for r in partners:
        await run.emit("factor-analyst", "factor", next(f for f in factors if f.id == r.factor_id).model_dump(mode="json"))
        await run.emit(f"dataset-scout:{r.factor_id}", "tool_result", {
            "tool": "choose_dataset", "factor_id": r.factor_id, "ok": True, "summary": r.candidate.title if r.candidate else ""})
    caveats += [n for n in partner_notes if n.startswith("Could not")]
    taken = {r.factor_id for r in partners} | ({competitor.factor_id} if competitor else set())
    uncovered = [f for f in factors if f.feature_id is None and f.id not in taken]
    uncovered.sort(key=lambda f: {"high": 0, "medium": 1, "low": 2}[f.priority])
    results = list(await asyncio.gather(*(run_scout(f, meter, run.emit, run.run_id[:6]) for f in uncovered[:MAX_SCOUTS])))
    results += [ScoutResult(f.id, None, 0, caveat=f"Not searched this run: only the {MAX_SCOUTS} highest-priority "
                            "factors without data get a dataset scout.") for f in uncovered[MAX_SCOUTS:]]
    if competitor is not None:
        results.append(competitor)
    results += partners
    candidates: list[DatasetCandidate] = [r.candidate for r in results if r.candidate]
    by_id = {f.id: f for f in factors}
    for r in results:
        if r.candidate is None:
            by_id[r.factor_id] = by_id[r.factor_id].model_copy(update={"status": "dropped", "caveat": r.caveat})
            await run.emit(S, "factor", by_id[r.factor_id].model_dump(mode="json"))

    # 4. data plan approval
    await run.emit(S, "todo", todos(3))
    answer = await run.request_approval(ApprovalRequest(approval_id=f"{run.run_id}-data", kind="data_plan",
                                                        factors=list(by_id.values()), candidates=candidates))
    keep = set(answer.kept_factor_ids or by_id) if answer.decision == "edit" else set(by_id)
    if answer.decision == "reject":
        candidates = []
    for fid in list(by_id):
        if fid not in keep:
            by_id[fid] = by_id[fid].model_copy(update={"status": "dropped", "caveat": "Removed in the data plan."})

    # register features: geo ingests each approved FeatureSpec; the ValidationReport decides the factor's status
    specs = {r.factor_id: r.spec for r in results if r.spec is not None}
    approved = [c for c in candidates if by_id[c.factor_id].status != "dropped" and c.factor_id in specs]
    await run.emit(S, "tool_call", {"tool": "register_features", "args": {"datasets": len(approved)}})
    ingested = 0
    for c in approved:
        spec = specs[c.factor_id]
        report = await geo.ingest(IngestRequest(spec=spec))
        f = by_id[c.factor_id]
        if report.ok:
            ingested += 1
            by_id[c.factor_id] = f.model_copy(update={"status": "found", "feature_id": spec.id, "caveat": None,
                                                       "direction": spec.direction})
            if competitor is not None and c.factor_id == competitor.factor_id and demand is not None:
                demand = demand.model_copy(update={"competitor_feature": f"{spec.id}_count"})
                run.state["demand"] = demand
        else:
            by_id[c.factor_id] = f.model_copy(update={
                "status": "dropped", "caveat": f"Found {c.title}, but it did not pass validation: {'; '.join(report.notes)[:160]}"})
        await run.emit(S, "factor", by_id[c.factor_id].model_dump(mode="json"))
    for c in candidates:  # chosen but not readable as map data
        if c.factor_id not in specs and by_id[c.factor_id].status != "dropped":
            by_id[c.factor_id] = by_id[c.factor_id].model_copy(update={
                "status": "dropped", "caveat": f"Found {c.title}, but it is not in a format we can map automatically."})
            await run.emit(S, "factor", by_id[c.factor_id].model_dump(mode="json"))
    await run.emit(S, "tool_result", {"tool": "register_features", "ok": True,
                                      "summary": f"{ingested} of {len(approved)} datasets ingested and validated."})
    factors = list(by_id.values())
    run.state["factors"] = factors
    run.write_artifact("factors.json", TypeAdapter(list[Factor]).dump_json(factors, indent=2).decode())
    await run.emit(S, "file", {"name": "factors.json", "path": "/factors.json"})

    # 5. weights approval, then score
    await run.emit(S, "todo", todos(4))
    await run.emit("market-modeler", "tool_call", {"tool": "advise_weights", "args": {"factors": len(scorable(factors))}})
    spec, weight_notes = await advise_weights(profile, factors, meter)
    labels = {f.feature_id: f.factor for f in factors if f.feature_id}
    top = sorted(spec.weights.items(), key=lambda kv: -kv[1])[:3]
    await run.emit("market-modeler", "tool_result", {
        "tool": "advise_weights", "ok": not weight_notes, "usage": meter.snapshot(),
        "summary": "Weighted for this business, most important first: " + ", ".join(labels.get(k, k) for k, _ in top)
                   + ". " + " ".join(weight_notes)})
    answer = await run.request_approval(ApprovalRequest(approval_id=f"{run.run_id}-weights", kind="weights",
                                                        factors=factors, spec=spec))
    if answer.decision == "edit" and answer.edited_spec:
        spec = normalize(answer.edited_spec)
    run.state["spec"] = spec
    await run.emit(S, "tool_call", {"tool": "score_grid", "args": {"features": len(spec.weights), "top_n": 15}})
    zones = await geo.score(ScoreRequest(profile=profile, spec=spec, top_n=15, demand=demand))
    run.write_artifact("zones.json", zones.model_dump_json())
    await run.emit(S, "tool_result", {"tool": "score_grid", "ok": True,
                                      "summary": f"Scored {len(zones.zones):,} cells; top {len(zones.top)} zones written."})
    await run.emit(S, "file", {"name": "zones.json", "path": "/zones.json"})

    # 6. real spaces near the best zones, sized for the service lines (MRI, PT, ASC, dock ... add space and rules)
    await run.emit(S, "todo", todos(5))
    program = strategy.space_program(profile)
    if program.service_lines:
        c = profile.constraints
        widened = c.model_copy(update={
            "max_sqft": max(c.max_sqft, program.recommended_sqft[1]),
            "ground_floor": c.ground_floor or any("ground floor" in r for r in program.requirements)})
        profile = profile.model_copy(update={"constraints": widened})
        run.state["profile"] = profile
        await run.emit(S, "tool_result", {"tool": "space_program", "ok": True, "summary": (
            f"Service lines {', '.join(x.replace('_', ' ') for x in program.service_lines)} add space and rules: "
            f"searching up to {widened.max_sqft:,} sq ft" + (", ground floor only" if widened.ground_floor else "")
            + (f"; needs {', '.join(program.requirements[:4])}" if program.requirements else "") + ".")})
    constraints = profile.constraints
    if not constraints.space_uses and demand is not None:
        constraints = constraints.model_copy(update={"space_uses": demand.space_uses})
    request = SiteSearchRequest(zones=[z.h3 for z in zones.zones[:SEARCH_ZONES]], constraints=constraints,
                                limit=SITE_LIMIT)
    await run.emit("site-scout", "tool_call", {"tool": "search_sites", "args": {"zones": len(request.zones), "limit": SITE_LIMIT}})
    sites = await sites_client.search(request)
    if not sites:
        caveats.append("No listed spaces fit near the top zones; showing zone centroids instead of buildings.")
        sites = await MockSites().search(request.model_copy(update={"zones": zones.top}))
    run.state["sites"] = sites
    run.write_artifact("sites.json", TypeAdapter(list[SiteCandidate]).dump_json(sites, indent=2).decode())
    await run.emit("site-scout", "tool_result", {"tool": "search_sites", "ok": True,
                                                 "summary": f"{len(sites)} spaces that fit the business near the top zones."})
    await run.emit("site-scout", "file", {"name": "sites.json", "path": "/sites.json"})

    # 7. rank
    await run.emit(S, "todo", todos(6))
    await run.emit(S, "tool_call", {"tool": "rank_sites", "args": {"candidates": len(sites)}})
    ranked = await geo.rank(RankRequest(profile=profile, spec=spec, candidates=sites, with_stability=True, demand=demand))
    run.write_artifact("ranked.json", TypeAdapter(list[RankedSite]).dump_json(ranked, indent=2).decode())
    plan = strategy.plan(profile, ranked, {s.id: s for s in sites})
    run.write_artifact("strategy.json", plan.model_dump_json(indent=2))
    caveats.append(strategy.caveat(plan))
    await run.emit(S, "file", {"name": "strategy.json", "path": "/strategy.json"})
    label = demand.demand_label if demand is not None else "patients"
    decision = await review(profile, pack_for(profile), ranked, {s.id: s for s in sites}, factors, geo, label)
    run.write_artifact("review.json", decision.model_dump_json(indent=2))
    await run.emit(S, "file", {"name": "review.json", "path": "/review.json"})
    excluded = sum(r.excluded_reason is not None for r in ranked)
    await run.emit(S, "tool_result", {"tool": "rank_sites", "ok": True,
                                      "summary": f"Ranked {len(ranked) - excluded} sites; {excluded} failed a hard constraint."})

    # 8. inspect the top spaces (CoStar listing + Vexcel aerial + Matterport scan) and who lives around them
    await run.emit(S, "todo", todos(7))
    by_site = {s.id: s for s in sites}
    usable = [r for r in ranked if r.excluded_reason is None]
    minutes = geomath.TRIP_MINUTES[demand.trip] if demand is not None else 15.0
    community = {}
    for r in usable[:15]:
        need = await geo.community(by_site[r.site_id].lat, by_site[r.site_id].lon, minutes)
        if need is not None:
            community[r.site_id] = need
    await run.emit(I, "tool_call", {"tool": "plan_inspection", "args": {"spaces": min(len(usable), inspector.INSPECT_TOP)}})

    async def inspector_event(type_: str, payload: dict[str, Any]) -> None:
        await run.emit(I, type_, payload)  # type: ignore[arg-type]

    inspections, notes = await inspector.inspect(profile, ranked, by_site, meter, inspector_event)
    checked = sum(len(i.checked) for i in inspections.values())
    await run.emit(I, "tool_result", {"tool": "plan_inspection", "ok": True, "usage": meter.snapshot(),
                                      "summary": f"{checked} checks on {len(inspections)} spaces; " + "; ".join(notes)})
    run.state["inspections"], run.state["community"] = inspections, community

    # 9. report
    await run.emit(S, "todo", todos(8))
    if geomath.competitor_column(pack_for(profile), demand.competitor_feature if demand else None) is None:
        caveats.append(geomath.NO_COMPETITOR_CAVEAT)
    if isinstance(pack_for(profile).site_rules.get("caveat"), str):
        caveats.append(pack_for(profile).site_rules["caveat"])
    if not profile.existing_offices:
        caveats.append("No current locations were given, so nothing is subtracted for cannibalization and the agent "
                       "could not learn from where you already succeed.")
    if demand is None and not pack_for(profile).visit_rates:
        caveats.append("Visit rates by age are not sourced yet, so net-new counts residents in reach, not visits.")
    elif demand is None:
        caveats.append("Net-new patients use planning-assumption rates of how many residents of each age see this "
                       "specialty in a year (informed by national NCHS and USRDS figures), not Houston-sourced rates.")
    await run.emit("analyst-critic", "tool_call", {"tool": "verify_claims", "args": {"sites": min(10, len(ranked))}})
    if demand is not None:
        caveats.append(f"Net-new counts {demand.demand_label} each site would win after competitors and your other "
                       "locations take their share; it is not a sales forecast.")
    report, problems = await build_report(run.run_id, profile, factors, sites, ranked, geo, meter, caveats, demand=demand,
                                          inspections=inspections, community=community)
    run.write_artifact("report.json", report.model_dump_json(indent=2))
    await run.emit("analyst-critic", "tool_result", {
        "tool": "verify_claims", "ok": report.verified, "usage": meter.snapshot(),
        "summary": "Every number in every claim matches a stored value." if report.verified
        else f"{len(problems)} numbers did not match; those claims were removed."})
    await run.emit("analyst-critic", "file", {"name": "report.json", "path": "/report.json"})
    await run.emit(S, "todo", todos(len(STEPS)))
    await run.emit(S, "done", {"report": "report.json", "usage": meter.snapshot()})
    (run.artifacts_dir / "usage.json").write_text(json.dumps(meter.snapshot()))
