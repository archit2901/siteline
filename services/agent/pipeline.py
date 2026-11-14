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
    Factor,
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
from services.agent import llm
from services.agent.critic import build_report
from services.agent.factor_analyst import analyze
from services.agent.runs import Run
from services.agent.scout import run_scout
from services.agent.tools import geomath
from services.agent.tools.backends import get_geo, get_sites
from services.agent.tools.packs import pack_for
from services.agent.tools.profile import load_profile
from services.agent.tools.sites_client import MockSites
from services.agent.tools.weights import weights_from_priorities

S = "site-strategist"
STEPS = [
    "Load the practice profile",
    "Reason about what makes an office succeed",
    "Find data for uncovered factors",
    "Approve the data plan",
    "Approve weights and score Houston",
    "Find real spaces in the top zones",
    "Rank sites by net-new patients",
    "Write and verify the report",
]
SEARCH_ZONES = 400  # zones handed to the site search, best first (CoStar covers inner Harris only)
SITE_LIMIT = 40


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
    zones = await geo.score(ScoreRequest(profile=profile, spec=spec, top_n=15))
    sites: list[SiteCandidate] = run.state.get("sites", [])
    ranked = await geo.rank(RankRequest(profile=profile, spec=spec, candidates=sites)) if sites else []
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

    # 3. scouts, one per factor without data, in parallel
    await run.emit(S, "todo", todos(2))
    uncovered = [f for f in factors if f.feature_id is None]
    results = await asyncio.gather(*(run_scout(f, meter, run.emit) for f in uncovered))
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
            by_id[c.factor_id] = f.model_copy(update={"status": "found", "feature_id": spec.id, "caveat": None})
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
    spec = weights_from_priorities(factors)
    answer = await run.request_approval(ApprovalRequest(approval_id=f"{run.run_id}-weights", kind="weights",
                                                        factors=factors, spec=spec))
    if answer.decision == "edit" and answer.edited_spec:
        spec = normalize(answer.edited_spec)
    run.state["spec"] = spec
    await run.emit(S, "tool_call", {"tool": "score_grid", "args": {"features": len(spec.weights), "top_n": 15}})
    zones = await geo.score(ScoreRequest(profile=profile, spec=spec, top_n=15))
    run.write_artifact("zones.json", zones.model_dump_json())
    await run.emit(S, "tool_result", {"tool": "score_grid", "ok": True,
                                      "summary": f"Scored {len(zones.zones):,} cells; top {len(zones.top)} zones written."})
    await run.emit(S, "file", {"name": "zones.json", "path": "/zones.json"})

    # 6. real spaces near the best zones
    await run.emit(S, "todo", todos(5))
    request = SiteSearchRequest(zones=[z.h3 for z in zones.zones[:SEARCH_ZONES]], constraints=profile.constraints,
                                limit=SITE_LIMIT)
    await run.emit("site-scout", "tool_call", {"tool": "search_sites", "args": {"zones": len(request.zones), "limit": SITE_LIMIT}})
    sites = await sites_client.search(request)
    if not sites:
        caveats.append("No listed spaces fit near the top zones; showing zone centroids instead of buildings.")
        sites = await MockSites().search(request.model_copy(update={"zones": zones.top}))
    run.state["sites"] = sites
    run.write_artifact("sites.json", TypeAdapter(list[SiteCandidate]).dump_json(sites, indent=2).decode())
    await run.emit("site-scout", "tool_result", {"tool": "search_sites", "ok": True,
                                                 "summary": f"{len(sites)} spaces that fit the practice near the top zones."})
    await run.emit("site-scout", "file", {"name": "sites.json", "path": "/sites.json"})

    # 7. rank
    await run.emit(S, "todo", todos(6))
    await run.emit(S, "tool_call", {"tool": "rank_sites", "args": {"candidates": len(sites)}})
    ranked = await geo.rank(RankRequest(profile=profile, spec=spec, candidates=sites, with_stability=True))
    run.write_artifact("ranked.json", TypeAdapter(list[RankedSite]).dump_json(ranked, indent=2).decode())
    excluded = sum(r.excluded_reason is not None for r in ranked)
    await run.emit(S, "tool_result", {"tool": "rank_sites", "ok": True,
                                      "summary": f"Ranked {len(ranked) - excluded} sites; {excluded} failed a hard constraint."})

    # 8. report
    await run.emit(S, "todo", todos(7))
    if geomath.competitor_column(pack_for(profile), None) is None:
        caveats.append(geomath.NO_COMPETITOR_CAVEAT)
    if not pack_for(profile).visit_rates:
        caveats.append("Visit rates by age are not sourced yet, so net-new counts residents in reach, not visits.")
    await run.emit("analyst-critic", "tool_call", {"tool": "verify_claims", "args": {"sites": min(10, len(ranked))}})
    report, problems = await build_report(run.run_id, profile, factors, sites, ranked, geo, meter, caveats)
    run.write_artifact("report.json", report.model_dump_json(indent=2))
    await run.emit("analyst-critic", "tool_result", {
        "tool": "verify_claims", "ok": report.verified, "usage": meter.snapshot(),
        "summary": "Every number in every claim matches a stored value." if report.verified
        else f"{len(problems)} numbers did not match; those claims were removed."})
    await run.emit("analyst-critic", "file", {"name": "report.json", "path": "/report.json"})
    await run.emit(S, "todo", todos(len(STEPS)))
    await run.emit(S, "done", {"report": "report.json", "usage": meter.snapshot()})
    (run.artifacts_dir / "usage.json").write_text(json.dumps(meter.snapshot()))
