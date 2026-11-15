"""Run store: live runs (pipeline) and replays, SSE fan-out, approvals, artifacts, runs/runs.db.

Replay streams a recorded JSONL file (fixtures/events.jsonl or a golden run, data/golden/<name>.jsonl) with gaps capped
at REPLAY_MAX_GAP_SECONDS and pauses at approvals like a live run. Golden runs hold real CoStar spaces, so they live
under data/ (never committed; copied to the server with the CoStar files). A live run appends every event to runs/<run_id>/events.jsonl and pauses at approvals until
POST /runs/{id}/approve (or DEMO_AUTO_APPROVE_SECONDS) answers them.
"""

import asyncio
import logging
import os
import re
import sqlite3
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from contracts.models import ApprovalRequest, ApproveRequest, EventType, RunCreate, RunEvent
from services.agent.llm import runs_dir

log = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "fixtures"
GOLDEN = Path(os.environ.get("GOLDEN_DIR", ROOT / "data" / "golden"))
GOLDEN_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
ARTIFACTS = {"factors.json", "zones.json", "sites.json", "ranked.json", "report.json", "strategy.json", "review.json", "expert.json", "options.json"}


def max_gap_seconds() -> float:
    return float(os.environ.get("REPLAY_MAX_GAP_SECONDS", "1.5"))


def max_live_runs() -> int:
    """Live runs allowed at once (each spends LLM calls); 0 = no limit."""
    return int(os.environ.get("MAX_LIVE_RUNS", "0") or 0)


def auto_approve_seconds() -> float:
    return float(os.environ.get("DEMO_AUTO_APPROVE_SECONDS", "0") or 0)


class RunNotFound(Exception):
    pass


class BadRequest(Exception):
    pass


class TooBusy(Exception):
    pass


@dataclass
class Run:
    run_id: str
    request: RunCreate
    artifacts_dir: Path
    source: Path | None = None  # replay: events JSONL being replayed
    live: bool = False
    events: list[RunEvent] = field(default_factory=list)
    finished: bool = False
    status: str = "running"
    changed: asyncio.Condition = field(default_factory=asyncio.Condition)
    task: asyncio.Task[None] | None = None
    pending_approval: ApprovalRequest | None = None
    _answer: asyncio.Future[ApproveRequest] | None = None
    state: dict[str, Any] = field(default_factory=dict)  # pipeline results kept for /rescore

    async def publish(self, event: RunEvent) -> None:
        async with self.changed:
            self.events.append(event)
            self.changed.notify_all()
        if self.live:
            with (self.artifacts_dir / "events.jsonl").open("a") as f:
                f.write(event.model_dump_json() + "\n")

    async def emit(self, agent: str, type_: EventType, payload: dict[str, Any]) -> None:
        seq = self.events[-1].seq + 1 if self.events else 0
        await self.publish(RunEvent(run_id=self.run_id, seq=seq, ts=datetime.now(timezone.utc), agent=agent,
                                    type=type_, payload=payload))

    async def finish(self, status: str = "done") -> None:
        async with self.changed:
            self.finished = True
            self.status = status
            self.changed.notify_all()

    async def subscribe(self) -> AsyncIterator[RunEvent]:
        """Every event so far, then new ones as they arrive, until the run finishes."""
        sent = 0
        while True:
            async with self.changed:
                await self.changed.wait_for(lambda: len(self.events) > sent or self.finished)
                pending = self.events[sent:]
                done = self.finished
            for event in pending:
                yield event
            sent += len(pending)
            if done and sent == len(self.events):
                return

    def write_artifact(self, name: str, text: str) -> str:
        if name not in ARTIFACTS:
            raise ValueError(f"unknown artifact {name!r}")
        (self.artifacts_dir / name).write_text(text)
        return f"/{name}"

    async def request_approval(self, request: ApprovalRequest, event: RunEvent | None = None) -> ApproveRequest:
        """Emit an approval event (or a recorded one) and wait for the answer (auto-approve after
        DEMO_AUTO_APPROVE_SECONDS if set)."""
        self.pending_approval = request
        self._answer = asyncio.get_running_loop().create_future()
        if event is None:
            await self.emit("site-strategist", "approval", request.model_dump(mode="json"))
        else:
            await self.publish(event)
        delay = auto_approve_seconds()
        if delay > 0:
            asyncio.get_running_loop().call_later(delay, self._auto_approve, request.approval_id)
        try:
            return await self._answer
        finally:
            self.pending_approval = None
            self._answer = None

    def _auto_approve(self, approval_id: str) -> None:
        if self.pending_approval and self.pending_approval.approval_id == approval_id and self._answer and not self._answer.done():
            self._answer.set_result(ApproveRequest(approval_id=approval_id, decision="approve", note="auto-approved"))

    def answer(self, response: ApproveRequest) -> None:
        if self.pending_approval is None or self._answer is None or self._answer.done():
            raise BadRequest("this run is not waiting for an approval")
        if response.approval_id != self.pending_approval.approval_id:
            raise BadRequest(f"waiting for approval {self.pending_approval.approval_id!r}, got {response.approval_id!r}")
        self._answer.set_result(response)


def replay_source(golden: str | None) -> tuple[Path, Path]:
    """Events file and artifacts directory for a replay; rejects golden names that could escape fixtures/golden."""
    if golden is None:
        return FIXTURES / "events.jsonl", FIXTURES
    if not GOLDEN_NAME.match(golden):
        raise BadRequest("golden must match [A-Za-z0-9_-]{1,64}")
    source = (GOLDEN / f"{golden}.jsonl").resolve()
    if source.parent != GOLDEN.resolve() or not source.is_file():
        raise RunNotFound(f"no golden run named {golden!r}")
    return source, GOLDEN / golden


def list_golden() -> list[str]:
    """Names of the recorded runs that can be replayed (data/golden/<name>.jsonl)."""
    return sorted(p.stem for p in GOLDEN.glob("*.jsonl") if GOLDEN_NAME.match(p.stem)) if GOLDEN.is_dir() else []


def load_events(path: Path) -> list[RunEvent]:
    return [RunEvent.model_validate_json(line) for line in path.read_text().splitlines() if line.strip()]


class RunDB:
    """runs/runs.db: one row per run (the same file holds llm_calls from llm.UsageMeter)."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or runs_dir() / "runs.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, created_at TEXT, mode TEXT, "
                       "status TEXT, profile_json TEXT)")

    def insert(self, run: Run) -> None:
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO runs VALUES (?, ?, ?, ?, ?)", (
                run.run_id, datetime.now(timezone.utc).isoformat(), run.request.mode, run.status,
                run.request.profile.model_dump_json()))

    def set_status(self, run_id: str, status: str) -> None:
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE runs SET status = ? WHERE id = ?", (status, run_id))


Pipeline = Callable[[Run], Awaitable[None]]


class RunStore:
    def __init__(self) -> None:
        self._runs: dict[str, Run] = {}
        self._db: RunDB | None = None

    @property
    def db(self) -> RunDB:
        if self._db is None:
            self._db = RunDB()
        return self._db

    def get(self, run_id: str) -> Run:
        try:
            return self._runs[run_id]
        except KeyError:
            raise RunNotFound(f"no run {run_id!r}") from None

    def start_replay(self, request: RunCreate) -> Run:
        source, artifacts_dir = replay_source(request.golden)
        run = Run(run_id=uuid.uuid4().hex[:12], request=request, source=source, artifacts_dir=artifacts_dir)
        self._runs[run.run_id] = run
        run.task = asyncio.create_task(self._replay(run))
        return run

    def start_live(self, request: RunCreate, pipeline: Pipeline) -> Run:
        limit = max_live_runs()
        if limit and sum(r.live and not r.finished for r in self._runs.values()) >= limit:
            raise TooBusy(f"{limit} live runs are already in progress; try again when one finishes")
        run_id = uuid.uuid4().hex[:12]
        artifacts_dir = runs_dir() / run_id
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        run = Run(run_id=run_id, request=request, artifacts_dir=artifacts_dir, live=True)
        self._runs[run_id] = run
        self.db.insert(run)
        run.task = asyncio.create_task(self._live(run, pipeline))
        return run

    async def _live(self, run: Run, pipeline: Pipeline) -> None:
        status = "done"
        try:
            await pipeline(run)
        except Exception:  # the stream must end with an error event, never hang
            status = "error"
            error_id = uuid.uuid4().hex[:8]
            log.exception("run %s failed (error %s)", run.run_id, error_id)  # full detail stays in the server log
            await run.emit("site-strategist", "error", {
                "message": f"The run stopped because of an internal error (error id {error_id}).", "recoverable": False})
        finally:
            await run.finish(status)
            self.db.set_status(run.run_id, status)

    async def _replay(self, run: Run) -> None:
        try:
            recorded = load_events(run.source)  # type: ignore[arg-type]
            cap = max_gap_seconds()
            for prev, event in zip([None, *recorded], recorded):
                if prev is not None:
                    gap = (event.ts - prev.ts).total_seconds()
                    await asyncio.sleep(max(0.0, min(gap, cap)))
                event = event.model_copy(update={"run_id": run.run_id})
                if event.type == "approval":  # wait for the presenter; the recording continues as approved
                    await run.request_approval(ApprovalRequest.model_validate(event.payload), event)
                else:
                    await run.publish(event)
        except Exception:  # surface replay failures to subscribers instead of hanging them
            log.exception("replay %s failed", run.run_id)
            await run.emit("site-strategist", "error", {"message": "The replay could not be played.", "recoverable": False})
        finally:
            await run.finish()

    def artifact_path(self, run_id: str, name: str) -> Path:
        run = self.get(run_id)
        if name not in ARTIFACTS:
            raise RunNotFound(f"unknown artifact {name!r}")
        path = run.artifacts_dir / name
        if not path.is_file():
            raise RunNotFound(f"artifact {name!r} not available for run {run_id!r}")
        return path
