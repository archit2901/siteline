"""agent-api: runs, live event stream (SSE) and run artifacts.

Setup milestone: replay runs only; live runs return 501 until the deepagents graph lands (P0/P1).
"""

import os
from collections.abc import AsyncIterator

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from sse_starlette.sse import EventSourceResponse

from contracts.models import Health, RunCreate, RunCreated
from services.agent.runs import BadRequest, RunNotFound, RunStore

FIXTURE_MODE = os.environ.get("FIXTURE_MODE", "1") == "1"
CORS_ORIGINS = os.environ.get("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",")

app = FastAPI(title="Siteline agent-api")
app.add_middleware(CORSMiddleware, allow_origins=CORS_ORIGINS, allow_methods=["*"], allow_headers=["*"])
store = RunStore()


@app.get("/health")
async def health() -> Health:
    return Health(service="agent", fixture_mode=FIXTURE_MODE)


@app.post("/runs")
async def create_run(request: RunCreate) -> RunCreated:
    if request.mode == "live":
        raise HTTPException(501, "live runs are not implemented yet; use mode=replay")
    try:
        run = store.start_replay(request)
    except BadRequest as exc:
        raise HTTPException(400, str(exc)) from exc
    except RunNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    return RunCreated(run_id=run.run_id)


@app.get("/runs/{run_id}/events")
async def run_events(run_id: str) -> EventSourceResponse:
    try:
        run = store.get(run_id)
    except RunNotFound as exc:
        raise HTTPException(404, str(exc)) from exc

    async def messages() -> AsyncIterator[dict[str, str]]:
        async for event in run.subscribe():
            yield {"id": str(event.seq), "data": event.model_dump_json()}

    return EventSourceResponse(messages())


@app.get("/runs/{run_id}/artifacts/{name}")
async def run_artifact(run_id: str, name: str) -> FileResponse:
    try:
        path = store.artifact_path(run_id, name)
    except RunNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    return FileResponse(path, media_type="application/json")
