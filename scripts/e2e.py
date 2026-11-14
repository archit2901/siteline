"""End to end against a running agent-api: one replay run and one live orthopedic-group run.

  PYTHONPATH=. python scripts/e2e.py [--live-only | --replay-only] [--case <eval id>] [--golden <name>]
                                     [--agent-url http://localhost:8000]

Approvals are answered by this script (approve); replays pause at approvals like live runs. Checks the event order todo -> factor -> approval -> file
zones.json -> done, that every artifact validates against the contracts, and that the report is verified.
"""

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

import httpx
import yaml
from pydantic import TypeAdapter

from contracts.models import EvalCase, Factor, RankedSite, Report, RunCreate, RunEvent, ScoreResponse, SiteCandidate

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = {"factors.json": TypeAdapter(list[Factor]), "zones.json": TypeAdapter(ScoreResponse),
             "sites.json": TypeAdapter(list[SiteCandidate]), "ranked.json": TypeAdapter(list[RankedSite]),
             "report.json": TypeAdapter(Report)}
ORDER = ["todo", "factor", "approval", "file:zones.json", "done"]


def first_index(events: list[RunEvent], marker: str) -> int:
    for i, e in enumerate(events):
        if marker.startswith("file:") and e.type == "file" and e.payload.get("name") == marker[5:]:
            return i
        if e.type == marker:
            return i
    return -1


async def run_once(client: httpx.AsyncClient, body: RunCreate) -> tuple[str, list[RunEvent], float]:
    started = time.monotonic()
    run_id = (await client.post("/runs", json=body.model_dump(mode="json"))).raise_for_status().json()["run_id"]
    events: list[RunEvent] = []
    async with client.stream("GET", f"/runs/{run_id}/events", timeout=None) as resp:
        async for line in resp.aiter_lines():
            if not line.startswith("data:"):
                continue
            event = RunEvent.model_validate_json(line[5:].strip())
            events.append(event)
            p = event.payload
            label = p.get("tool") or p.get("name") or p.get("factor") or p.get("kind") or ""
            summary = p.get("summary") or ""
            print(f"  {event.seq:>3} {event.agent:38s} {event.type:11s} {label} {summary}"[:180])
            if event.type == "approval":
                await client.post(f"/runs/{run_id}/approve", json={"approval_id": p["approval_id"], "decision": "approve"})
    return run_id, events, time.monotonic() - started


async def check(client: httpx.AsyncClient, name: str, body: RunCreate) -> bool:
    print(f"\n=== {name}")
    run_id, events, secs = await run_once(client, body)
    problems = []
    positions = [first_index(events, m) for m in ORDER]
    if -1 in positions or positions != sorted(positions):
        problems.append(f"event order wrong: {dict(zip(ORDER, positions))}")
    if events and events[-1].type != "done":
        problems.append(f"last event is {events[-1].type}: {events[-1].payload}")
    names = {e.payload.get("name") for e in events if e.type == "file"}
    for artifact in sorted(names & ARTIFACTS.keys()):
        resp = await client.get(f"/runs/{run_id}/artifacts/{artifact}")
        try:
            data = ARTIFACTS[artifact].validate_json(resp.content)
        except Exception as exc:
            problems.append(f"{artifact} invalid: {str(exc)[:200]}")
            continue
        if artifact == "report.json":
            report: Report = data
            if not report.verified:
                problems.append("report.verified is false")
            print(f"\n  top sites ({len(report.recommendations)}), verified={report.verified}:")
            for rec in report.recommendations[:5]:
                rpc = f"${rec.annual_rent_per_customer:,.2f}/{report.demand_label[:-1]}" if rec.annual_rent_per_customer else "-"
                print(f"   {rec.rank:>2}. {rec.site.address[:40]:40s} net-new {rec.net_new_patients:>8,.0f} {report.demand_label}  "
                      f"rent/cust {rpc}  final {rec.final_score:.3f}")
                for claim in rec.justification[:2]:
                    print(f"       - {claim.text}")
    usage = next((e.payload.get("usage") for e in reversed(events) if e.payload.get("usage")), None)
    scouts = sorted({e.agent for e in events if e.agent.startswith("dataset-scout")})
    print(f"\n  {len(events)} events in {secs:.0f}s; scouts: {len(scouts)}; usage: {json.dumps(usage)}")
    for p in problems:
        print(f"  FAIL {p}")
    print(f"  {'PASS' if not problems else 'FAIL'} {name}")
    return not problems


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent-url", default=os.environ.get("AGENT_URL", "http://localhost:8000"))
    parser.add_argument("--live-only", action="store_true")
    parser.add_argument("--replay-only", action="store_true")
    parser.add_argument("--case", default="orthopedic_group", help="eval case id for the live run (evals/<id>.yaml)")
    parser.add_argument("--golden", default=None, help="replay this golden run (data/golden/<name>.jsonl)")
    args = parser.parse_args()
    case = EvalCase.model_validate(yaml.safe_load((ROOT / "evals" / f"{args.case}.yaml").read_text()))
    ok = True
    async with httpx.AsyncClient(base_url=args.agent_url, timeout=60) as client:
        (await client.get("/health")).raise_for_status()
        if not args.live_only:
            source = f"data/golden/{args.golden}.jsonl" if args.golden else "fixtures/events.jsonl"
            ok &= await check(client, f"replay ({source})", RunCreate(profile=case.profile, mode="replay", golden=args.golden))
        if not args.replay_only:
            ok &= await check(client, f"live {case.id}", RunCreate(profile=case.profile, mode="live"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
