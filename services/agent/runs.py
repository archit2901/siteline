"""Run store and replay.

Setup milestone: runs live in memory and only replay mode exists. A replay run streams a recorded JSONL file
(fixtures/events.jsonl or fixtures/golden/<name>.jsonl) with the original gaps capped at REPLAY_MAX_GAP_SECONDS.
"""

import asyncio
import os
import re
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from contracts.models import RunCreate, RunEvent

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "fixtures"
GOLDEN = FIXTURES / "golden"
GOLDEN_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
ARTIFACTS = {"factors.json", "zones.json", "sites.json", "ranked.json", "report.json"}


def max_gap_seconds() -> float:
    return float(os.environ.get("REPLAY_MAX_GAP_SECONDS", "1.5"))


class RunNotFound(Exception):
    pass


class BadRequest(Exception):
    pass


@dataclass
class Run:
    run_id: str
    request: RunCreate
    source: Path  # events JSONL being replayed
    artifacts_dir: Path
    events: list[RunEvent] = field(default_factory=list)
    finished: bool = False
    changed: asyncio.Condition = field(default_factory=asyncio.Condition)
    task: asyncio.Task[None] | None = None

    async def publish(self, event: RunEvent) -> None:
        async with self.changed:
            self.events.append(event)
            self.changed.notify_all()

    async def finish(self) -> None:
        async with self.changed:
            self.finished = True
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


def load_events(path: Path) -> list[RunEvent]:
    return [RunEvent.model_validate_json(line) for line in path.read_text().splitlines() if line.strip()]


class RunStore:
    def __init__(self) -> None:
        self._runs: dict[str, Run] = {}

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

    async def _replay(self, run: Run) -> None:
        try:
            recorded = load_events(run.source)
            cap = max_gap_seconds()
            for prev, event in zip([None, *recorded], recorded):
                if prev is not None:
                    gap = (event.ts - prev.ts).total_seconds()
                    await asyncio.sleep(max(0.0, min(gap, cap)))
                await run.publish(event.model_copy(update={"run_id": run.run_id}))
        except Exception as exc:  # surface replay failures to subscribers instead of hanging them
            seq = run.events[-1].seq + 1 if run.events else 0
            error = RunEvent(
                run_id=run.run_id, seq=seq, ts=datetime.now(timezone.utc), agent="site-strategist", type="error",
                payload={"message": f"replay failed: {exc}", "recoverable": False},
            )
            await run.publish(error)
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
