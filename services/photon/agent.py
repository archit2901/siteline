"""Siteline on iMessage through Photon 2.0.

  PYTHONPATH=. python -m services.photon.agent

Text the agent's Photon number: "I want to open a laundromat for apartment renters". The same agent-api that drives
the web app runs here: the chat intake asks for anything missing, the live run starts, both approvals (data plan,
weights) are answered by replying YES or NO in Messages, the top spaces come back as a text, and follow-up questions
are answered from the verified report (/ask). Every number in a reply comes from agent-api, never from this file.

Env: PHOTON_API_KEY (project:read + project:write), PHOTON_PROJECT_ID, AGENT_URL (default http://localhost:8000),
PHOTON_ALLOWED_SENDERS (comma-separated phone numbers / iMessage emails allowed to use it; empty = anyone who can
reach the line, which on a shared line is only the sender you registered).

Guards: direct conversations only (no group chats, so nobody approves someone else's run), one conversation per sender,
an optional sender allowlist, and at most MAX_PER_MINUTE messages per sender. Each message's text is capped.
"""

import asyncio
import json
import logging
import os
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

log = logging.getLogger("siteline.photon")
AGENT_URL = os.environ.get("AGENT_URL", "http://localhost:8000").rstrip("/")
POLL_SECONDS = 2.0
MAX_PER_MINUTE = 10
MAX_TEXT = 1000


def allowed_senders() -> set[str]:
    return {h.strip().lower() for h in os.environ.get("PHOTON_ALLOWED_SENDERS", "").split(",") if h.strip()}


class SenderLimiter:
    """At most MAX_PER_MINUTE messages per sender in a rolling minute (each can trigger model calls)."""

    def __init__(self, per_minute: int = MAX_PER_MINUTE) -> None:
        self.per_minute = per_minute
        self.seen: dict[str, list[float]] = {}

    def allow(self, sender: str, now: float) -> bool:
        recent = [t for t in self.seen.get(sender, []) if now - t < 60]
        if len(recent) >= self.per_minute:
            self.seen[sender] = recent
            return False
        self.seen[sender] = [*recent, now]
        return True
YES = re.compile(r"^\s*(y|yes|yeah|yep|ok|okay|approve|sure|go|do it)\b", re.I)
NO = re.compile(r"^\s*(n|no|nope|skip|reject)\b", re.I)
RESTART = re.compile(r"^\s*(new|restart|start over|reset)\s*$", re.I)
WELCOME = ("Hi, I'm Siteline. Tell me what you want to open in Houston and who your customers are, and I'll find "
           "real spaces for lease where it would do best.")

Send = Callable[[str], Awaitable[None]]


class AgentApi:
    """The agent-api endpoints this channel uses (the same ones the web app calls)."""

    def __init__(self, base_url: str = AGENT_URL) -> None:
        self.client = httpx.AsyncClient(base_url=base_url, timeout=httpx.Timeout(120.0, connect=10.0))

    async def intake(self, messages: list[dict[str, str]], draft: dict[str, Any] | None) -> dict[str, Any]:
        return (await self.client.post("/intake", json={"messages": messages, "draft": draft})).raise_for_status().json()

    async def start_run(self, profile: dict[str, Any]) -> str:
        resp = await self.client.post("/runs", json={"profile": profile, "mode": "live"})
        return resp.raise_for_status().json()["run_id"]

    async def events(self, run_id: str):  # type: ignore[no-untyped-def]
        async with self.client.stream("GET", f"/runs/{run_id}/events", timeout=None) as resp:
            async for line in resp.aiter_lines():
                if line.startswith("data:"):
                    yield json.loads(line[5:].strip())

    async def approve(self, run_id: str, approval_id: str, decision: str) -> None:
        (await self.client.post(f"/runs/{run_id}/approve",
                                json={"approval_id": approval_id, "decision": decision})).raise_for_status()

    async def report(self, run_id: str) -> dict[str, Any]:
        return (await self.client.get(f"/runs/{run_id}/artifacts/report.json")).raise_for_status().json()

    async def ask(self, question: str, report: dict[str, Any]) -> dict[str, Any]:
        return (await self.client.post("/ask", json={"question": question, "report": report})).raise_for_status().json()


def approval_text(approval: dict[str, Any], labels: dict[str, str]) -> str:
    """One approval as a short text: what the agent wants to do and how to answer."""
    if approval.get("kind") == "data_plan":
        found = [c.get("title", "a dataset") for c in approval.get("candidates") or []]
        if not found:
            return "The scouts found no extra datasets, so I'll use our stock data. Reply YES to continue."
        return ("To measure what our stock data can't, I'd like to use: " + "; ".join(found[:4]) +
                ". Reply YES to approve or NO to skip them.")
    weights = sorted(((approval.get("spec") or {}).get("weights") or {}).items(), key=lambda kv: -kv[1])
    top = ", ".join(f"{labels.get(k, k.replace('_', ' '))} {round(v * 100)}%" for k, v in weights[:4])
    return f"Here's how I'd weigh Houston for this business: {top}. Reply YES to use these weights."


def results_text(report: dict[str, Any], top: int = 3) -> str:
    """The best spaces from the verified report (numbers exactly as stored there)."""
    label = report.get("demand_label") or "patients"
    lines = []
    for rec in (report.get("recommendations") or [])[:top]:
        s = rec["site"]
        bits = [f"{round(rec['net_new_patients']):,} {label} in reach" if label != "patients"
                else f"{round(rec['net_new_patients']):,} new patients a year"]
        if s.get("sqft"):
            bits.append(f"{s['sqft']:,} sq ft")
        if s.get("rent_psf"):
            bits.append(f"${s['rent_psf']:.2f}/sq ft")
        if s.get("matterport_id"):
            bits.append(f"3D tour: https://my.matterport.com/show/?m={s['matterport_id']}")
        lines.append(f"{rec['rank']}. {s['address']}: " + ", ".join(bits))
    verified = "Every number was checked against its source." if report.get("verified") else ""
    return ("Here are the best spaces I found:\n" + "\n".join(lines) + f"\n{verified}\n"
            "Ask me about them (\"Which has the most parking?\") or reply NEW to start over.").strip()


@dataclass
class Conversation:
    """One person's thread: intake -> run (approvals by reply) -> results -> questions."""

    api: AgentApi
    send: Send
    messages: list[dict[str, str]] = field(default_factory=list)
    draft: dict[str, Any] | None = None
    run_id: str | None = None
    pending: dict[str, Any] | None = None  # approval waiting for YES/NO
    report: dict[str, Any] | None = None
    labels: dict[str, str] = field(default_factory=dict)
    task: asyncio.Task[None] | None = None

    async def handle(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        if RESTART.match(text):
            if self.task:
                self.task.cancel()
            self.messages, self.draft, self.run_id, self.pending, self.report = [], None, None, None, None
            await self.send(WELCOME)
            return
        if self.pending:
            await self._answer(text)
        elif self.report:
            await self._question(text)
        elif self.run_id:
            await self.send("I'm still working on it; I'll text you when I need your approval.")
        else:
            await self._intake(text)

    async def _intake(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})
        res = await self.api.intake(self.messages, self.draft)
        profile: dict[str, Any] = res["profile"]
        self.draft = profile
        self.messages.append({"role": "assistant", "content": res["reply"]})
        if not res.get("ready"):
            await self.send(res["reply"])
            return
        what = profile.get("business_type") or (profile.get("specialty") or "").replace("_", " ")
        await self.send(f"Got it: a {what} for {profile.get('customers') or 'your customers'}. Searching Houston now; "
                        "this takes about a minute.")
        self.run_id = await self.api.start_run(profile)
        self.task = asyncio.create_task(self._follow(self.run_id))

    async def _follow(self, run_id: str) -> None:
        """Stream the run; stop at each approval and text the person, deliver the results at the end."""
        try:
            async for event in self.api.events(run_id):
                payload = event.get("payload") or {}
                if event["type"] == "factor" and payload.get("feature_id"):
                    self.labels[payload["feature_id"]] = payload.get("factor", "")
                elif event["type"] == "approval":
                    self.pending = payload
                    await self.send(approval_text(payload, self.labels))
                elif event["type"] == "error" and not payload.get("recoverable"):
                    await self.send("Sorry, the search stopped. Reply NEW to try again.")
                    self.run_id = None
                    return
                elif event["type"] == "done":
                    self.report = await self.api.report(run_id)
                    await self.send(results_text(self.report))
                    return
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("following run %s failed", run_id)
            await self.send("I lost track of the search. Reply NEW to start over.")

    async def _answer(self, text: str) -> None:
        approval, run_id = self.pending, self.run_id
        if not approval or not run_id:
            return
        if YES.match(text):
            decision = "approve"
        elif NO.match(text) and approval.get("kind") == "data_plan":
            decision = "reject"
        else:
            await self.send("Please reply YES" + (" or NO." if approval.get("kind") == "data_plan" else "."))
            return
        self.pending = None
        await self.api.approve(run_id, approval["approval_id"], decision)
        await self.send("Thanks, continuing." if decision == "approve" else "Skipped those datasets; continuing.")

    async def _question(self, text: str) -> None:
        res = await self.api.ask(text, self.report or {})
        facts = [f["text"] for f in res.get("facts") or []]
        await self.send("\n".join([res["answer"], *(f"- {f}" for f in facts[:5])]))


# ---------------------------------------------------------------------------
# Photon 2.0 transport: poll received messages, reply in the same space
# ---------------------------------------------------------------------------
def _photon():  # type: ignore[no-untyped-def]
    from photon_api import Photon

    return Photon(headers={"Authorization": f"Bearer {os.environ['PHOTON_API_KEY']}"})


async def main() -> None:
    from photon_api.generated.models import SendMessageRequest, SendTextContent, TextContent
    from photon_api.rpc_generated import ListMessagesInput, SendMessageHeader, SendMessageInput, SendMessagePath

    project = os.environ["PHOTON_PROJECT_ID"]
    api = AgentApi()
    photon = _photon()
    threads: dict[str, Conversation] = {}  # keyed by sender id
    seen: set[str] = set()
    allow = allowed_senders()
    limiter = SenderLimiter()
    since = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    log.info("Siteline is listening on Photon project %s", project)

    def sender_for(space_id: str, agent_id: str, parent_id: str) -> Send:
        async def send(text: str) -> None:
            body = SendMessageRequest(spaceId=space_id, senderId=agent_id, parentMessageId=parent_id,
                                      content=SendTextContent(type="text", format="plain", text=text[:1800]))
            key = f"siteline:{parent_id}:{abs(hash(text)) % 10**10}"
            await asyncio.to_thread(photon.projects.send_message, SendMessageInput(
                path=SendMessagePath(id=project), headers=SendMessageHeader.model_validate({"idempotency-key": key}), body=body))
        return send

    while True:
        query: dict[str, Any] = {"createdSince": since, "direction": "received", "order": "asc", "pageSize": 100,
                                 "platform": "imessage"}
        try:
            page = await asyncio.to_thread(photon.projects.list_messages,
                                           ListMessagesInput.model_validate({"path": {"id": project}, "query": query}))
        except Exception:
            log.exception("Photon poll failed")
            await asyncio.sleep(POLL_SECONDS * 3)
            continue
        for item in page.items:
            m = item.root.root
            if m.id in seen or m.status != "received" or not isinstance(m.content, TextContent):
                continue
            seen.add(m.id)
            since = max(since, m.createdAt)  # next poll starts at the newest message seen (ids dedupe the overlap)
            sender, agent, space = m.sentBy.root, m.receivedBy.root, m.space.root
            if sender.kind != "human" or getattr(space, "kind", "direct") != "direct":
                continue  # direct threads only: in a group anyone could answer another person's approval
            handle = str(getattr(sender, "handle", "") or "").lower()
            if allow and handle not in allow:
                log.info("ignored a message from a sender not in PHOTON_ALLOWED_SENDERS")
                continue
            if not limiter.allow(sender.id, asyncio.get_running_loop().time()):
                continue
            convo = threads.get(sender.id)  # one conversation per sender
            if convo is None:
                convo = threads[sender.id] = Conversation(api, sender_for(space.id, agent.id, m.id))
            convo.send = sender_for(space.id, agent.id, m.id)  # reply under the newest message
            asyncio.create_task(_safe(convo.handle(m.content.text[:MAX_TEXT])))
        await asyncio.sleep(POLL_SECONDS)


async def _safe(coro: Awaitable[None]) -> None:
    try:
        await coro
    except Exception:
        log.exception("handling a message failed")


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()  # PHOTON_API_KEY and PHOTON_PROJECT_ID from .env (never printed)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    asyncio.run(main())
