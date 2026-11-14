"""Siteline's iMessage conversation (Photon): intake -> run -> approvals by reply -> results -> questions."""

import asyncio
from typing import Any

from services.photon.agent import Conversation, approval_text, results_text

REPORT = {"demand_label": "renter households", "verified": True, "recommendations": [
    {"rank": 1, "net_new_patients": 9608, "site": {"address": "1 Main St", "sqft": 5000, "rent_psf": None, "matterport_id": None}},
    {"rank": 2, "net_new_patients": 8293, "site": {"address": "2 Main St", "sqft": 3600, "rent_psf": 25.0, "matterport_id": "abc"}},
]}


class FakeApi:
    def __init__(self) -> None:
        self.approved: list[tuple[str, str]] = []
        self.turns = 0

    async def intake(self, messages: list[dict[str, str]], draft: Any) -> dict[str, Any]:
        self.turns += 1
        ready = self.turns > 1
        return {"reply": "Who are your customers?" if not ready else "Got it.", "ready": ready,
                "profile": {"business_type": "laundromat", "customers": "renters" if ready else ""}}

    async def start_run(self, profile: dict[str, Any]) -> str:
        return "run1"

    async def events(self, run_id: str):  # type: ignore[no-untyped-def]
        yield {"type": "factor", "payload": {"feature_id": "renter_households_within_5km", "factor": "Renters nearby"}}
        yield {"type": "approval", "payload": {"approval_id": "a1", "kind": "data_plan",
                                               "candidates": [{"title": "OpenStreetMap: laundromats"}]}}
        while self.approved != [("a1", "approve")]:
            await asyncio.sleep(0.01)
        yield {"type": "done", "payload": {}}

    async def approve(self, run_id: str, approval_id: str, decision: str) -> None:
        self.approved.append((approval_id, decision))

    async def report(self, run_id: str) -> dict[str, Any]:
        return REPORT

    async def ask(self, question: str, report: dict[str, Any]) -> dict[str, Any]:
        return {"answer": "#2 2 Main St has the most parking.", "facts": [{"text": "#2 2 Main St: 30 parking spaces."}]}


def test_full_conversation() -> None:
    sent: list[str] = []

    async def send(text: str) -> None:
        sent.append(text)

    async def go() -> None:
        api = FakeApi()
        c = Conversation(api, send)  # type: ignore[arg-type]
        await c.handle("I want to open a laundromat")
        await c.handle("Apartment renters")
        while not c.pending:
            await asyncio.sleep(0.01)
        await c.handle("maybe")  # not a yes/no: asks again
        await c.handle("yes")
        assert c.task is not None
        await c.task
        await c.handle("Which has the most parking?")
        assert api.approved == [("a1", "approve")]

    asyncio.run(go())
    assert sent[0] == "Who are your customers?"
    assert sent[1].startswith("Got it: a laundromat for renters")
    assert "OpenStreetMap: laundromats" in sent[2] and "Reply YES" in sent[2]
    assert sent[3] == "Please reply YES or NO."
    assert "1. 1 Main St: 9,608 renter households in reach" in sent[5]
    assert "most parking" in sent[-1]


def test_texts_use_report_numbers() -> None:
    text = results_text(REPORT)
    assert "8,293 renter households in reach, 3,600 sq ft, $25.00/sq ft, 3D tour: https://my.matterport.com/show/?m=abc" in text
    weights = approval_text({"kind": "weights", "spec": {"weights": {"a": 0.6, "b": 0.4}}}, {"a": "Renters nearby"})
    assert "Renters nearby 60%" in weights and "b 40%" in weights
