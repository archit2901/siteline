"""agent-api: runs (live pipeline or replay), live event stream (SSE), approvals, rescore and run artifacts."""

import os
from collections.abc import AsyncIterator

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from sse_starlette.sse import EventSourceResponse

from contracts.models import ApproveRequest, Health, RescoreRequest, RescoreResponse, RunCreate, RunCreated
from services.agent.critic import build_report
from services.agent.pipeline import normalize, run_pipeline, score_and_rank
from services.agent.runs import BadRequest, Run, RunNotFound, RunStore, TooBusy, list_golden
from services.agent.tools.backends import get_geo

FIXTURE_MODE = os.environ.get("FIXTURE_MODE", "1") == "1"
CORS_ORIGINS = os.environ.get("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",")

app = FastAPI(title="Siteline agent-api")
app.add_middleware(CORSMiddleware, allow_origins=CORS_ORIGINS, allow_methods=["*"], allow_headers=["*"])
store = RunStore()


def get_run(run_id: str) -> Run:
    try:
        return store.get(run_id)
    except RunNotFound as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get("/health")
async def health() -> Health:
    return Health(service="agent", fixture_mode=FIXTURE_MODE)


@app.get("/golden")
async def golden() -> dict[str, list[str]]:
    """Recorded runs available for replay (POST /runs with mode=replay, golden=<name>)."""
    return {"golden": list_golden()}


@app.post("/runs")
async def create_run(request: RunCreate) -> RunCreated:
    try:
        run = store.start_live(request, run_pipeline) if request.mode == "live" else store.start_replay(request)
    except BadRequest as exc:
        raise HTTPException(400, str(exc)) from exc
    except RunNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except TooBusy as exc:
        raise HTTPException(429, str(exc)) from exc
    return RunCreated(run_id=run.run_id)


@app.get("/runs/{run_id}/events")
async def run_events(run_id: str) -> EventSourceResponse:
    run = get_run(run_id)

    async def messages() -> AsyncIterator[dict[str, str]]:
        async for event in run.subscribe():
            yield {"id": str(event.seq), "data": event.model_dump_json()}

    return EventSourceResponse(messages())


@app.post("/runs/{run_id}/approve")
async def approve(run_id: str, response: ApproveRequest) -> dict[str, str]:
    run = get_run(run_id)
    try:
        run.answer(response)
    except BadRequest as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"status": "accepted"}


@app.post("/runs/{run_id}/rescore")
async def rescore(run_id: str, request: RescoreRequest) -> RescoreResponse:
    """New weights -> geo score + rank -> recommendations. No LLM call."""
    run = get_run(run_id)
    if "profile" not in run.state or "sites" not in run.state:
        raise HTTPException(409, "this run has no scored sites yet")
    spec = normalize(request.spec)
    zones, ranked = await score_and_rank(run, spec)
    report, _ = await build_report(run.run_id, run.state["profile"], run.state.get("factors", []), run.state["sites"],
                                   ranked, get_geo(), None, [], select=False)
    return RescoreResponse(zones=zones, recommendations=report.recommendations)


@app.get("/runs/{run_id}/artifacts/{name}")
async def run_artifact(run_id: str, name: str) -> FileResponse:
    try:
        path = store.artifact_path(run_id, name)
    except RunNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    return FileResponse(path, media_type="application/json")
