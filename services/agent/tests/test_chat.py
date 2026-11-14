"""Chat intake (conversation -> profile) and the results concierge (answers only from report facts)."""

import asyncio
import json
import re

import httpx
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel

from contracts.models import ChatMessage, PracticeProfile, Report
from services.agent import concierge, intake
from services.agent.concierge import AnswerDraft, compose, fact_options
from services.agent.intake import AnswerDraft as IntakeAnswer
from services.agent.intake import IntakeDraft, LocationDraft, guard_reply, is_ready, merge, missing_for
from services.agent.questions import questions_for, sector_of
from services.agent.runs import FIXTURES

REPORT = Report.model_validate(json.loads((FIXTURES / "report.json").read_text()))
USER = [ChatMessage(role="user", content="A bakery for young families, about 1,500 sq ft")]


def test_merge_fills_profile_and_clamps() -> None:
    draft = IntakeDraft(business_type="bakery", customers="young families", min_sqft=1800, max_sqft=1200, max_rent_psf=9999,
                        first_location=True)
    profile, missing = merge(draft, None)
    assert profile.business_type == "bakery" and profile.name == "My bakery" and missing == []
    assert (profile.constraints.min_sqft, profile.constraints.max_sqft) == (1200, 1800)  # swapped into order
    assert profile.constraints.max_rent_psf == 500  # clamped


def test_merge_maps_specialties_and_keeps_previous() -> None:
    prev = PracticeProfile(name="Bayou Bones", business_type="clinic", customers="older adults",
                           free_text="[ortho_focus] Focus: Joint replacement (hip, knee, shoulder)")
    draft = IntakeDraft(healthcare_specialty="Orthopedic", payers=["Medicare", "Commercial", "bitcoin"],
                        locations=[LocationDraft(name="Main", address="6400 Fannin St, Houston, TX")])
    profile, missing = merge(draft, prev)
    assert profile.specialty == "orthopedics" and profile.business_type == "" and profile.name == "Bayou Bones"
    assert profile.customers == "older adults" and missing == []
    assert profile.payers_accepted == ["commercial", "medicare"] and profile.existing_offices[0].name == "Main"
    other, _ = merge(IntakeDraft(healthcare_specialty="Cardiology"), prev)
    assert other.specialty == "cardiology"  # any specialty is kept; packs fall back to healthcare.base


def test_missing_and_ready_need_locations_and_sector_answers() -> None:
    profile, missing = merge(IntakeDraft(business_type="gym"), None)
    assert missing == ["who the customers are", "your current locations"]
    assert not is_ready(profile, missing, [*USER, ChatMessage(role="user", content="just run it")])  # no escape
    pantry, missing = merge(IntakeDraft(business_type="food pantry", customers="families", first_location=True), None)
    assert missing == ["what does this site do"]  # who_served is answered by the customers field it replaces
    pantry, missing = merge(IntakeDraft(services=["Choice pantry"], answers=[
        IntakeAnswer(question_id="who_served", answer="Seniors and families with kids"),
        IntakeAnswer(question_id="nope", answer="ignored")]), pantry)
    assert missing == [] and is_ready(pantry, missing, USER)
    assert "[locations]" in pantry.free_text and "nope" not in pantry.free_text
    again, _ = merge(IntakeDraft(), pantry)  # earlier answers survive a turn where the model returns none
    assert missing_for(again) == [] and again.free_text == pantry.free_text


def test_sector_question_sets() -> None:
    assert sector_of("community food pantry") == "nonprofit.food_bank"
    assert sector_of("Laundromat") == "retail.services"
    assert sector_of("homeless shelter health clinic") == "nonprofit.base"
    assert sector_of("urgent care clinic") == "healthcare" and sector_of("", "dialysis") == "healthcare"
    assert sector_of("daycare") == "education.childcare" and sector_of("bookkeeping") == "general.base"
    qs = questions_for("food bank")
    assert qs.base[0].id == "locations" and qs.base[0].required
    assert {q.id for q in qs.sector_questions} >= {"program_type", "client_travel", "logistics"}
    asked = [q.id for q in (*qs.base, *qs.sector_questions)]
    assert "customers" not in asked and "access" not in asked and len(asked) == len(set(asked))  # nothing twice
    for text in ("food pantry", "laundromat", "daycare", "gym", "cafe", "nonprofit", "orthopedic clinic", "bookkeeping"):
        q = questions_for(text)
        words = [x.text.split("?")[0].lower() for x in (*q.base, *q.sector_questions)]
        assert len(words) == len(set(words))


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


def test_ask_caps_client_sent_report() -> None:
    big = REPORT.model_copy(deep=True)
    rec = big.recommendations[0]
    rec.justification = [rec.justification[0].model_copy(update={"text": "x" * 5000})] * 50
    big.recommendations = [rec.model_copy(update={"rank": n}) for n in range(1, 200)]
    big.caveats = ["c" * 5000] * 100
    facts = fact_options(big)
    assert max(len(f.text) for f in facts.values()) < 400
    assert len({f.rank for f in facts.values() if f.rank}) == concierge.MAX_SITES
    assert sum(k.startswith("caveat") for k in facts) == concierge.MAX_CAVEATS


def test_chat_endpoints_are_rate_limited_and_body_capped(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from services.agent import app as app_module

    async def fake_chat(messages, draft):  # type: ignore[no-untyped-def]
        return intake.IntakeResponse(reply="ok", profile=PracticeProfile(name="x"))

    monkeypatch.setattr(intake, "chat", fake_chat)
    monkeypatch.setattr(app_module.chat_limiter, "per_minute", 2)
    monkeypatch.setattr(app_module.chat_limiter, "started", __import__("collections").deque())

    async def go() -> list[int]:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_module.app), base_url="http://test") as c:
            body = {"messages": [m.model_dump() for m in USER]}
            codes = [(await c.post("/intake", json=body)).status_code for _ in range(3)]
            huge = await c.post("/ask", content=b"{" + b" " * (app_module.MAX_BODY_BYTES + 10) + b"}",
                                headers={"content-type": "application/json"})
            return [*codes, huge.status_code]

    assert asyncio.run(go()) == [200, 200, 429, 413]


def test_body_cap_counts_streamed_bytes(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """A chunked upload with no Content-Length is still cut off at the cap."""
    from services.agent import app as app_module

    async def chunks():  # type: ignore[no-untyped-def]
        for _ in range(app_module.MAX_BODY_BYTES // 65536 + 2):
            yield b" " * 65536

    async def go() -> int:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_module.app), base_url="http://test") as c:
            r = await c.post("/ask", content=chunks(), headers={"content-type": "application/json"})
            return r.status_code

    assert asyncio.run(go()) == 413
