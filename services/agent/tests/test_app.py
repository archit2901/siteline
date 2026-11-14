import asyncio
import json
from pathlib import Path

import httpx
import pytest

from contracts.models import Health, Report, RunCreate, RunEvent, ScoreResponse
from services.agent.app import app, store
from services.agent.runs import FIXTURES, load_events

PROFILE = json.loads((FIXTURES / "report.json").read_text())["profile"]


@pytest.fixture(autouse=True)
def no_replay_gaps(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REPLAY_MAX_GAP_SECONDS", "0")
    monkeypatch.setenv("DEMO_AUTO_APPROVE_SECONDS", "0.01")  # replays pause at approvals like live runs


def client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def replay_body(**kwargs: object) -> dict[str, object]:
    return RunCreate.model_validate({"profile": PROFILE, "mode": "replay", **kwargs}).model_dump(mode="json")


async def read_sse(c: httpx.AsyncClient, run_id: str) -> list[RunEvent]:
    events = []
    async with c.stream("GET", f"/runs/{run_id}/events") as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        async for line in resp.aiter_lines():
            if line.startswith("data:"):
                events.append(RunEvent.model_validate_json(line.removeprefix("data:").strip()))
    return events


@pytest.mark.asyncio
async def test_health() -> None:
    async with client() as c:
        resp = await c.get("/health")
    health = Health.model_validate(resp.json())
    assert health.service == "agent" and health.ok


@pytest.mark.asyncio
async def test_replay_streams_every_event_in_order() -> None:
    expected = load_events(FIXTURES / "events.jsonl")
    async with client() as c:
        resp = await c.post("/runs", json=replay_body())
        assert resp.status_code == 200
        run_id = resp.json()["run_id"]
        events = await read_sse(c, run_id)
    assert [e.seq for e in events] == [e.seq for e in expected]
    assert [(e.agent, e.type, e.payload) for e in events] == [(e.agent, e.type, e.payload) for e in expected]
    assert all(e.run_id == run_id for e in events)
    assert events[-1].type == "done"


@pytest.mark.asyncio
async def test_late_subscriber_gets_full_history() -> None:
    async with client() as c:
        run_id = (await c.post("/runs", json=replay_body())).json()["run_id"]
        task = store.get(run_id).task
        assert task is not None
        await task
        events = await read_sse(c, run_id)
    assert len(events) == len(load_events(FIXTURES / "events.jsonl"))


@pytest.mark.asyncio
async def test_live_run_streams_and_waits_for_approval(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from contracts.models import ApprovalRequest
    from services.agent import app as app_module

    monkeypatch.setenv("RUNS_DIR", str(tmp_path))
    monkeypatch.setenv("DEMO_AUTO_APPROVE_SECONDS", "0")
    monkeypatch.setattr(store, "_db", None)

    async def fake_pipeline(run) -> None:  # type: ignore[no-untyped-def]
        await run.emit("site-strategist", "todo", {"todos": []})
        answer = await run.request_approval(ApprovalRequest(approval_id="a1", kind="weights"))
        await run.emit("site-strategist", "done", {"report": "report.json", "decision": answer.decision})

    monkeypatch.setattr(app_module, "run_pipeline", fake_pipeline)
    async with client() as c:
        run_id = (await c.post("/runs", json=replay_body(mode="live"))).json()["run_id"]
        run = store.get(run_id)
        while run.pending_approval is None:
            await asyncio.sleep(0.01)
        wrong = await c.post(f"/runs/{run_id}/approve", json={"approval_id": "nope", "decision": "approve"})
        ok = await c.post(f"/runs/{run_id}/approve", json={"approval_id": "a1", "decision": "approve"})
        events = await read_sse(c, run_id)
        early = await c.post(f"/runs/{run_id}/rescore", json={"spec": {"weights": {"x": 1}}})
    assert wrong.status_code == 409 and ok.status_code == 200
    assert [e.type for e in events] == ["todo", "approval", "done"]
    assert events[-1].payload["decision"] == "approve"
    assert (tmp_path / run_id / "events.jsonl").read_text().count("\n") == 3
    assert early.status_code == 409  # nothing scored yet


@pytest.mark.asyncio
async def test_replay_waits_for_approval(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEMO_AUTO_APPROVE_SECONDS", "0")
    first = next(e for e in load_events(FIXTURES / "events.jsonl") if e.type == "approval")
    async with client() as c:
        run_id = (await c.post("/runs", json=replay_body())).json()["run_id"]
        run = store.get(run_id)
        while run.pending_approval is None:
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.05)
        assert run.events[-1].seq == first.seq and not run.finished  # paused at the recorded approval
        while not run.finished:
            if run.pending_approval:
                approval_id = run.pending_approval.approval_id
                assert (await c.post(f"/runs/{run_id}/approve", json={"approval_id": approval_id, "decision": "approve"})).status_code == 200
            await asyncio.sleep(0.01)
    assert run.events[-1].type == "done"


@pytest.mark.asyncio
async def test_golden_runs_are_listed_and_replayed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from services.agent import runs

    (tmp_path / "demo").mkdir()
    (tmp_path / "demo.jsonl").write_text((FIXTURES / "events.jsonl").read_text())
    (tmp_path / "demo" / "report.json").write_text((FIXTURES / "report.json").read_text())
    (tmp_path / "bad name.jsonl").write_text("")
    monkeypatch.setattr(runs, "GOLDEN", tmp_path)
    async with client() as c:
        listed = (await c.get("/golden")).json()
        run_id = (await c.post("/runs", json=replay_body(golden="demo"))).json()["run_id"]
        events = await read_sse(c, run_id)
        report = await c.get(f"/runs/{run_id}/artifacts/report.json")
    assert listed == {"golden": ["demo"]}
    assert events[-1].type == "done"
    assert Report.model_validate(report.json()).verified


@pytest.mark.asyncio
@pytest.mark.parametrize(("golden", "status"), [("../events", 400), ("a/b", 400), ("no_such_run", 404)])
async def test_golden_name_is_checked(golden: str, status: int) -> None:
    async with client() as c:
        resp = await c.post("/runs", json=replay_body(golden=golden))
    assert resp.status_code == status


@pytest.mark.asyncio
async def test_artifacts_come_from_fixtures() -> None:
    async with client() as c:
        run_id = (await c.post("/runs", json=replay_body())).json()["run_id"]
        zones = await c.get(f"/runs/{run_id}/artifacts/zones.json")
        report = await c.get(f"/runs/{run_id}/artifacts/report.json")
        unknown = await c.get(f"/runs/{run_id}/artifacts/grid.json")
        missing_run = await c.get("/runs/nope/artifacts/zones.json")
    ScoreResponse.model_validate(zones.json())
    assert len(Report.model_validate(report.json()).recommendations) == 10
    assert unknown.status_code == 404
    assert missing_run.status_code == 404


@pytest.mark.asyncio
async def test_cors_allows_the_web_app() -> None:
    async with client() as c:
        resp = await c.options(
            "/runs", headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST"}
        )
    assert resp.headers["access-control-allow-origin"] == "http://localhost:5173"
