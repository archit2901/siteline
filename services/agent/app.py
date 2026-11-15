"""agent-api: runs (live pipeline or replay), live event stream (SSE), approvals, rescore and run artifacts."""

import asyncio
import os
import time
from collections import deque
from collections.abc import AsyncIterator

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from contracts.models import (ApproveRequest, PracticeProfile, AskRequest, AskResponse, Health, IntakeRequest, IntakeResponse,
                             RescoreRequest, RescoreResponse, RunCreate, RunCreated)
from services.agent import concierge, intake, questions, snapshot, website
from services.agent.critic import build_report
from services.agent.pipeline import normalize, run_pipeline, score_and_rank
from services.agent.runs import BadRequest, Run, RunNotFound, RunStore, TooBusy, list_golden
from services.agent.tools.backends import get_geo

FIXTURE_MODE = os.environ.get("FIXTURE_MODE", "1") == "1"
CORS_ORIGINS = os.environ.get("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",")

app = FastAPI(title="Siteline agent-api")
app.add_middleware(CORSMiddleware, allow_origins=CORS_ORIGINS, allow_methods=["*"], allow_headers=["*"])
store = RunStore()

MAX_BODY_BYTES = int(os.environ.get("MAX_BODY_BYTES", str(512 * 1024)))  # a full report is ~50 KB


class BodyLimit:
    """Pure ASGI middleware: reads the request body itself, counting the bytes actually received (chunked uploads
    included), and answers 413 once they pass MAX_BODY_BYTES, before the app parses anything. Bodies under the cap
    are replayed to the app unchanged. A declared Content-Length over the cap is refused without reading."""

    def __init__(self, app, max_bytes: int) -> None:  # type: ignore[no-untyped-def]
        self.app, self.max_bytes = app, max_bytes

    async def __call__(self, scope, receive, send):  # type: ignore[no-untyped-def]
        if scope["type"] != "http" or scope["method"] in ("GET", "HEAD", "OPTIONS"):
            return await self.app(scope, receive, send)
        too_large = JSONResponse({"detail": "request body too large"}, status_code=413)
        declared = dict(scope.get("headers") or []).get(b"content-length")
        if declared is not None and (not declared.isdigit() or int(declared) > self.max_bytes):
            return await too_large(scope, receive, send)
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body += message.get("body", b"")
            if len(body) > self.max_bytes:
                return await too_large(scope, receive, send)
            if not message.get("more_body", False):
                break
        replayed = False

        async def replay():  # type: ignore[no-untyped-def]
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, replay, send)


app.add_middleware(BodyLimit, max_bytes=MAX_BODY_BYTES)


class ChatLimiter:
    """Shared cap on the chat endpoints (/intake, /ask), which call the model outside any run's budget:
    at most CHAT_CONCURRENCY in flight and CHAT_PER_MINUTE started per rolling minute, process-wide."""

    def __init__(self) -> None:
        self.slots = asyncio.Semaphore(int(os.environ.get("CHAT_CONCURRENCY", "4")))
        self.per_minute = int(os.environ.get("CHAT_PER_MINUTE", "30"))
        self.started: deque[float] = deque()

    def admit(self) -> None:
        now = time.monotonic()
        while self.started and now - self.started[0] > 60:
            self.started.popleft()
        if len(self.started) >= self.per_minute or self.slots.locked():
            raise HTTPException(429, "The assistant is busy; try again in a moment.")
        self.started.append(now)


chat_limiter = ChatLimiter()


def get_run(run_id: str) -> Run:
    try:
        return store.get(run_id)
    except RunNotFound as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get("/health")
async def health() -> Health:
    return Health(service="agent", fixture_mode=FIXTURE_MODE)


@app.post("/intake")
async def intake_chat(request: IntakeRequest) -> IntakeResponse:
    """One chat turn: the owner's words so far -> the profile understood, what is missing, and the next reply."""
    chat_limiter.admit()
    async with chat_limiter.slots:
        return await intake.chat(request.messages[-40:], request.draft)


@app.get("/intake/questions")
async def intake_questions(business: str = "", specialty: str = "") -> questions.QuestionSet:
    """The base questions plus the sector set for a business type (no model call; the web form pops it up)."""
    return questions.questions_for(business[:200], specialty[:60])


class WebsiteRequest(BaseModel):
    url: str
    draft: PracticeProfile | None = None


@app.post("/intake/website")
async def intake_website(request: WebsiteRequest) -> IntakeResponse:
    """Intake step one: read the owner's website and pre-fill what it states (public hosts only, body-capped)."""
    chat_limiter.admit()
    async with chat_limiter.slots:
        return await website.prefill(request.url[:300], request.draft)


class MissingRequest(BaseModel):
    profile: PracticeProfile


@app.post("/intake/missing")
async def intake_missing(request: MissingRequest) -> dict[str, list[str]]:
    """What a run still needs for this profile (the same rule the chat uses)."""
    return {"missing": intake.missing_for(request.profile)}


@app.post("/ask")
async def ask(request: AskRequest) -> AskResponse:
    """A question about a finished run, answered only from facts rendered from its verified report."""
    chat_limiter.admit()
    async with chat_limiter.slots:
        return await concierge.ask(request.question, request.report)


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
    if "sites" not in run.state and not run.live:  # a recorded run: load what its live run saved
        snapshot.hydrate(run.artifacts_dir, run.state, get_geo())
    if "profile" not in run.state or "sites" not in run.state:
        raise HTTPException(409, "this run has no scored sites yet")
    spec = normalize(request.spec)
    zones, ranked = await score_and_rank(run, spec)
    report, _ = await build_report(run.run_id, run.state["profile"], run.state.get("factors", []), run.state["sites"],
                                   ranked, get_geo(), None, [], select=False, demand=run.state.get("demand"),
                                   inspections=run.state.get("inspections"), community=run.state.get("community"))
    return RescoreResponse(zones=zones, recommendations=report.recommendations)


@app.get("/runs/{run_id}/artifacts/{name}")
async def run_artifact(run_id: str, name: str) -> FileResponse:
    try:
        path = store.artifact_path(run_id, name)
    except RunNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    return FileResponse(path, media_type="application/json")
