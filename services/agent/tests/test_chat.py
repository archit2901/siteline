"""Chat intake (conversation -> profile) and the results concierge (answers only from report facts)."""

import asyncio
import json
import re

import httpx
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel

from contracts.models import ChatMessage, PracticeProfile, Report
from services.agent import concierge, intake
from services.agent.concierge import AnswerDraft, compose, fact_options
from services.agent.intake import IntakeDraft, guard_reply, is_ready, merge
from services.agent.runs import FIXTURES

REPORT = Report.model_validate(json.loads((FIXTURES / "report.json").read_text()))
USER = [ChatMessage(role="user", content="A bakery for young families, about 1,500 sq ft")]


def test_merge_fills_profile_and_clamps() -> None:
    draft = IntakeDraft(business_type="bakery", customers="young families", min_sqft=1800, max_sqft=1200, max_rent_psf=9999)
    profile, missing = merge(draft, None)
    assert profile.business_type == "bakery" and profile.name == "My bakery" and missing == []
    assert (profile.constraints.min_sqft, profile.constraints.max_sqft) == (1200, 1800)  # swapped into order
    assert profile.constraints.max_rent_psf == 500  # clamped


def test_merge_maps_specialties_and_keeps_previous() -> None:
    prev = PracticeProfile(name="Bayou Bones", business_type="clinic", customers="older adults")
    profile, missing = merge(IntakeDraft(healthcare_specialty="Orthopedic"), prev)
    assert profile.specialty == "orthopedics" and profile.business_type == "" and profile.name == "Bayou Bones"
    assert profile.customers == "older adults" and missing == []


def test_missing_and_ready() -> None:
    profile, missing = merge(IntakeDraft(business_type="gym"), None)
    assert missing == ["who the customers are"]
    assert not is_ready(profile, missing, USER)
    assert is_ready(profile, missing, [*USER, ChatMessage(role="user", content="just run it")])


def test_reply_guard_blocks_invented_numbers() -> None:
    assert guard_reply("Great, a 1,500 sq ft bakery.", USER, [], False) == "Great, a 1,500 sq ft bakery."
    swapped = guard_reply("About 40,000 families live near Katy.", USER, [], False)
    assert not re.search(r"\d", swapped)
    assert guard_reply("Got it.", USER, [], True).startswith("Siteline covers Houston only")


def test_chat_survives_model_failure() -> None:
    broken = GenericFakeChatModel(messages=iter([]))
    out = asyncio.run(intake.chat(USER, None, model=broken))
    assert out.reply and not out.ready and "what the business is" in out.missing


def test_fact_options_render_report_numbers() -> None:
    facts = fact_options(REPORT)
    top = REPORT.recommendations[0]
    assert f"r{top.rank}.won" in facts and facts[f"r{top.rank}.won"].site_id == top.site.id
    assert f"{top.net_new_patients:,.0f}" in facts[f"r{top.rank}.won"].text  # rendered from the stored value
    for rec in REPORT.recommendations:
        if rec.annual_rent_per_customer is not None:
            assert f"${rec.annual_rent_per_customer:,.2f}" in facts[f"r{rec.rank}.rent_per"].text
        for i, claim in enumerate(rec.justification):  # verified claims pass through word for word
            assert facts[f"r{rec.rank}.claim{i}"].text.endswith(claim.text)


def test_superlative_is_computed_not_written_by_the_model() -> None:
    facts = fact_options(REPORT)
    out = compose(AnswerDraft(kind="superlative", field="parking", direction="max"), facts, REPORT)
    best = max((r for r in REPORT.recommendations if r.site.parking_spaces is not None), key=lambda r: r.site.parking_spaces or 0)
    assert out.answer.startswith(f"#{best.rank} ") and out.facts[0].rank == best.rank
    cheap = compose(AnswerDraft(kind="superlative", field="rent", direction="min"), facts, REPORT)
    low = min((r for r in REPORT.recommendations if r.site.rent_psf is not None), key=lambda r: r.site.rent_psf or 0)
    assert cheap.facts[0].rank == low.rank and "lowest" in cheap.answer


def test_compare_explain_list_and_unknowns() -> None:
    facts = fact_options(REPORT)
    cmp_ = compose(AnswerDraft(kind="compare", ranks=[1, 2, 99], field="rent"), facts, REPORT)
    assert {f.rank for f in cmp_.facts} <= {1, 2} and "#1 and #2" in cmp_.answer
    why = compose(AnswerDraft(kind="explain", ranks=[1]), facts, REPORT)
    assert why.facts[0].rank == 1 and why.answer.startswith("#1 ")
    first = next(iter(facts))
    listed = compose(AnswerDraft(kind="list", fact_ids=["nope", first, first]), facts, REPORT)
    assert [f.text for f in listed.facts] == [facts[first].text]
    assert compose(AnswerDraft(kind="list", fact_ids=["nope"]), facts, REPORT).answer == concierge.OUT_OF_SCOPE
    assert compose(AnswerDraft(kind="none"), facts, REPORT).answer == concierge.OUT_OF_SCOPE


def test_endpoints(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from services.agent.app import app

    async def fake_chat(messages, draft):  # type: ignore[no-untyped-def]
        return intake.IntakeResponse(reply="Who are your customers?", profile=PracticeProfile(name="My bakery", business_type="bakery"),
                                     ready=False, missing=["who the customers are"])

    async def fake_ask(question, report):  # type: ignore[no-untyped-def]
        return compose(AnswerDraft(kind="explain", ranks=[report.recommendations[0].rank]), fact_options(report), report)

    monkeypatch.setattr(intake, "chat", fake_chat)
    monkeypatch.setattr(concierge, "ask", fake_ask)

    async def go() -> tuple[httpx.Response, httpx.Response]:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            a = await c.post("/intake", json={"messages": [m.model_dump() for m in USER]})
            b = await c.post("/ask", json={"question": "Why is #1 first?", "report": REPORT.model_dump(mode="json")})
            return a, b

    a, b = asyncio.run(go())
    assert a.status_code == 200 and a.json()["profile"]["business_type"] == "bakery"
    assert b.status_code == 200 and b.json()["facts"][0]["rank"] == REPORT.recommendations[0].rank
