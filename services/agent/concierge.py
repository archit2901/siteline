"""concierge: answers questions about a finished run, only from the verified report.

The model only classifies the question (which space is best or worst on a field, a comparison, why a space ranks
where it does, or which facts to list). Python then finds the answer in the report and writes every word and number
of the reply, the same rule as the critic: the model decides what to look at, Python computes and writes.
"""

import uuid
from typing import Literal

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from contracts.models import AskFact, AskResponse, Recommendation, Report
from services.agent import llm
from services.agent.critic import singular

MAX_FACTS = 6
# The report arrives from the client (so offline and recorded runs work too); cap what reaches the prompt.
MAX_SITES, MAX_CLAIMS, MAX_CAVEATS, MAX_TEXT, MAX_LISTING = 15, 6, 8, 300, 14_000
OUT_OF_SCOPE = "I can only answer from this run's results: the ranked spaces, their numbers and the data behind them."

PROMPT = """You route a business owner's question about the ranked spaces Siteline found. Do not answer it; classify it.
- kind "superlative": which space is best or worst on one field (most parking, cheapest rent, biggest, most customers).
  Set field and direction ("max" for most/biggest/highest, "min" for least/cheapest/smallest).
- kind "compare": compare specific spaces. Set ranks (e.g. [1, 2]) and field if the question names one.
- kind "explain": why a space ranks where it does. Set ranks to that space (default [1]).
- kind "list": anything else the FACTS can answer. Set fact_ids to the relevant fact ids, at most six.
- kind "none": the FACTS cannot answer it.
Fields: won (customers won), rent_per (rent per customer won), rent (asking rent per sq ft), size, parking, stability,
score (final score), fit (zone fit), quality (space quality)."""


Field_ = Literal["won", "rent_per", "rent", "size", "parking", "stability", "score", "fit", "quality"]


class AnswerDraft(BaseModel):
    kind: Literal["superlative", "compare", "explain", "list", "none"] = "none"
    field: Field_ | None = None
    direction: Literal["max", "min"] | None = None
    ranks: list[int] = Field(default_factory=list)
    fact_ids: list[str] = Field(default_factory=list)


# field -> (fact key in site_facts, plain words, value getter)
FIELDS = {
    "won": ("won", "customers won", lambda r: r.net_new_patients),
    "rent_per": ("rent_per", "rent per customer won", lambda r: r.annual_rent_per_customer),
    "rent": ("rent", "asking rent", lambda r: r.site.rent_psf),
    "size": ("size", "space", lambda r: r.site.sqft),
    "parking": ("parking", "parking", lambda r: r.site.parking_spaces),
    "stability": ("stability", "stability across weightings", lambda r: r.stability),
    "score": ("scores", "final score", lambda r: r.final_score),
    "fit": ("scores", "zone fit", lambda r: r.zone_score),
    "quality": ("scores", "space quality", lambda r: r.site_quality),
}


def _money(v: float) -> str:
    return f"${v:,.2f}"


def site_facts(rec: Recommendation, label: str) -> list[tuple[str, str]]:
    """(key, text) facts for one ranked space, every number rendered from the report."""
    s = rec.site
    n = rec.rank
    where = f"#{n} {s.address}"
    out = []
    if label == "patients":
        out.append(("won", f"{where}: about {rec.net_new_patients:,.0f} net-new patients a year."))
    else:
        out.append(("won", f"{where}: about {rec.net_new_patients:,.0f} {label} in reach would pick it."))
    if rec.annual_rent_per_customer is not None:
        out.append(("rent_per", f"{where}: {_money(rec.annual_rent_per_customer)} of annual rent per {singular(label)} won."))
    if s.rent_psf:
        out.append(("rent", f"{where}: asking rent {_money(s.rent_psf)} per sq ft per year."))
    if s.sqft:
        out.append(("size", f"{where}: {s.sqft:,} sq ft."))
    if s.floor is not None:
        out.append(("floor", f"{where}: on floor {s.floor}."))
    if s.parking_spaces is not None:
        out.append(("parking", f"{where}: {s.parking_spaces:,} parking spaces at the building."))
    avail = "for lease" if s.available is True else "lease pending" if s.available is False else "a candidate building"
    out.append(("availability", f"{where}: {avail}" + (", with a 3D tour." if s.matterport_id else ".")))
    if rec.stability is not None:
        out.append(("stability", f"{where}: stays in the top 10 in {rec.stability * 100:.0f}% of alternative weightings."))
    out.append(("scores", f"{where}: zone fit {rec.zone_score * 100:.0f}/100, space quality {rec.site_quality * 100:.0f}/100, "
                          f"final score {rec.final_score * 100:.0f}/100."))
    for i, claim in enumerate(rec.justification[:MAX_CLAIMS]):
        out.append((f"claim{i}", f"{where}: {claim.text[:MAX_TEXT]}"))
    return out


def fact_options(report: Report) -> dict[str, AskFact]:
    """Every fact the concierge may use, keyed by a stable id like "r2.parking" or "caveat0"."""
    label = report.demand_label or "patients"
    facts: dict[str, AskFact] = {}
    for rec in report.recommendations[:MAX_SITES]:
        refs = {f"claim{i}": c.evidence_refs[:6] for i, c in enumerate(rec.justification[:MAX_CLAIMS])}
        for key, text in site_facts(rec, label):
            facts[f"r{rec.rank}.{key}"] = AskFact(text=text, site_id=rec.site.id, rank=rec.rank, refs=refs.get(key, []))
    for i, c in enumerate((report.caveats or [])[:MAX_CAVEATS]):
        facts[f"caveat{i}"] = AskFact(text=c[:MAX_TEXT])
    return facts


def compose(draft: AnswerDraft, facts: dict[str, AskFact], report: Report) -> AskResponse:
    """Python answers the classified question from the report; unknown ranks, fields and ids are dropped."""
    recs = {r.rank: r for r in report.recommendations[:MAX_SITES]}
    pick = lambda ids: [facts[i] for i in dict.fromkeys(ids) if i in facts][:MAX_FACTS]  # noqa: E731
    if draft.kind == "superlative" and draft.field:
        key, words, get = FIELDS[draft.field]
        have = [r for r in recs.values() if get(r) is not None]
        if have:
            ordered = sorted(have, key=lambda r: get(r), reverse=draft.direction != "min")  # type: ignore[arg-type,return-value]
            best = ordered[0]
            most = "most" if draft.direction != "min" else "least"
            if draft.field in ("rent", "rent_per"):
                most = "highest" if draft.direction != "min" else "lowest"
            lead = f"#{best.rank} {best.site.address} has the {most} {words} of the {len(have)} spaces with that figure."
            return AskResponse(answer=lead, facts=pick([f"r{r.rank}.{key}" for r in ordered[:4]]))
    if draft.kind == "compare":
        ranks = [n for n in dict.fromkeys(draft.ranks) if n in recs][:3] or [n for n in sorted(recs)[:2]]
        keys = [FIELDS[draft.field][0]] if draft.field else ["won", "rent_per", "scores"]
        names = " and ".join(f"#{n}" for n in ranks)
        return AskResponse(answer=f"How {names} compare.", facts=pick([f"r{n}.{k}" for k in keys for n in ranks]))
    if draft.kind == "explain":
        n = next((n for n in draft.ranks if n in recs), min(recs))
        rec = recs[n]
        lead = (f"#{n} {rec.site.address}: Siteline ranks spaces mostly by customers won, then by zone fit and space "
                "quality. Here is what puts it there.")
        won = f"{rec.net_new_patients:,.0f}"
        claims = [f"r{n}.claim{i}" for i, c in enumerate(rec.justification)]
        has_won_claim = any(won in c.text for c in rec.justification)
        ids = ([] if has_won_claim else [f"r{n}.won"]) + [f"r{n}.scores"] + claims[:MAX_CLAIMS]
        if n + 1 in recs:
            ids.append(f"r{n + 1}.won")
        return AskResponse(answer=lead, facts=pick(ids))
    if draft.kind == "list":
        chosen = pick(draft.fact_ids)
        if chosen:
            return AskResponse(answer="Here is what the results show.", facts=chosen)
    return AskResponse(answer=OUT_OF_SCOPE, facts=[])


async def ask(question: str, report: Report, model: BaseChatModel | None = None) -> AskResponse:
    """Answer one question about the report. Never raises on model errors."""
    question = " ".join(question.split())[:500]
    if not question or not report.recommendations:
        return AskResponse(answer=OUT_OF_SCOPE)
    facts = fact_options(report)
    listing = "\n".join(f"- {k}: {f.text}" for k, f in facts.items())[:MAX_LISTING]
    meter = llm.UsageMeter(run_id=f"ask-{uuid.uuid4().hex[:8]}", budget=4)
    try:
        chat_model = model if model is not None else llm.get_chat_model("critic", meter)
        out = await chat_model.with_structured_output(AnswerDraft).ainvoke(
            [SystemMessage(PROMPT), HumanMessage(f"QUESTION: {question}\n\nFACTS:\n{listing}")])
        draft = out if isinstance(out, AnswerDraft) else AnswerDraft()
    except Exception:  # an unanswerable turn is better than a broken chat
        draft = AnswerDraft()
    return compose(draft, facts, report)
