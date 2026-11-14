"""intake: a chat in the owner's words -> PracticeProfile, asking only for what is missing.

Nova Pro reads the conversation and fills an IntakeDraft. Python merges it into the previous draft, clamps the
numbers, maps healthcare specialties onto their packs, decides what is still missing and whether the agent can run,
and guards the reply: a reply that states numbers the owner never wrote is replaced by a template question.
"""

import re
import uuid

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from contracts.models import ChatMessage, Constraints, IntakeResponse, PracticeProfile
from services.agent import llm

SPECIALTIES = {
    "orthopedics": "orthopedics", "orthopedic": "orthopedics", "orthopaedics": "orthopedics",
    "pediatric_dentistry": "pediatric_dentistry", "pediatric dentistry": "pediatric_dentistry",
    "pediatric dentist": "pediatric_dentistry", "dialysis": "dialysis",
    "physical_therapy": "physical_therapy", "physical therapy": "physical_therapy",
}
SQFT_RANGE = (200, 100_000)
GO_WORDS = re.compile(r"\b(go|run it|start|find (sites|spaces)|search now|that'?s all|just run)\b", re.I)
GREETING = "Tell me what you want to open in Houston and who your customers are."
HOUSTON_ONLY = ("Siteline covers Houston only for now (Harris, Fort Bend and Montgomery counties), so I'll search "
                "there.")

PROMPT = """You help a business owner describe the new location they want to open in Houston, Texas, so a site
selection agent can search for them. Read the whole conversation and fill every field you can from what the owner
said. Leave a field empty or null when the owner has not said it; never guess sizes, rents or customers.

- business_type: what the business is, in a few plain words ("laundromat", "bakery", "pediatric dentist").
- healthcare_specialty: only if it is a medical practice of one of these kinds: orthopedics, pediatric_dentistry,
  dialysis, physical_therapy. Otherwise empty.
- customers: who the customers are, in the owner's words.
- services, languages: lists, only if mentioned.
- min_sqft, max_sqft, max_rent_psf (dollars per sq ft per year), min_parking_per_1000sf, ground_floor: only the
  numbers the owner gave. "about 1,500 sq ft" means min 1200 and max 1800.
- name: the business's name if given.
- notes: anything else the owner said that matters for choosing a location.
- mentions_other_city: true if the owner wants a city other than Houston.
- reply: your next chat message, one or two short sentences. If the business type or the customers are unknown, ask
  for the missing one. If both are known, briefly restate what you understood and say they can press Find sites or
  add details such as space size or budget. Never state facts or numbers about the market."""


class IntakeDraft(BaseModel):
    business_type: str = ""
    healthcare_specialty: str = ""
    customers: str = ""
    services: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    min_sqft: int | None = None
    max_sqft: int | None = None
    max_rent_psf: float | None = None
    min_parking_per_1000sf: float | None = None
    ground_floor: bool | None = None
    name: str = ""
    notes: str = ""
    mentions_other_city: bool = False
    reply: str = ""


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def merge(draft: IntakeDraft, previous: PracticeProfile | None) -> tuple[PracticeProfile, list[str]]:
    """Validated draft merged over the previous profile -> (profile, missing). Nothing the owner said is dropped."""
    base = previous or PracticeProfile(name="", constraints=Constraints())
    cons = base.constraints.model_copy()
    specialty = SPECIALTIES.get(draft.healthcare_specialty.strip().lower(), "")
    business = " ".join(draft.business_type.split())[:60]
    if specialty:
        kind = {"specialty": specialty, "business_type": ""}
    elif business:
        kind = {"specialty": "", "business_type": business, "payers_accepted": []}
    else:
        kind = {}

    if draft.min_sqft:
        cons.min_sqft = int(_clamp(draft.min_sqft, *SQFT_RANGE))
    if draft.max_sqft:
        cons.max_sqft = int(_clamp(draft.max_sqft, *SQFT_RANGE))
    if cons.min_sqft > cons.max_sqft:
        cons.min_sqft, cons.max_sqft = cons.max_sqft, cons.min_sqft
    if draft.max_rent_psf:
        cons.max_rent_psf = round(_clamp(draft.max_rent_psf, 1, 500), 2)
    if draft.min_parking_per_1000sf is not None:
        cons.min_parking_per_1000sf = round(_clamp(draft.min_parking_per_1000sf, 0, 20), 1)
    if draft.ground_floor is not None:
        cons.ground_floor = draft.ground_floor

    profile = base.model_copy(update={
        **kind,
        "customers": " ".join(draft.customers.split())[:300] or base.customers,
        "services": [s.strip()[:60] for s in draft.services if s.strip()][:8] or base.services,
        "languages": [s.strip()[:30] for s in draft.languages if s.strip()][:6] or base.languages,
        # the model re-reads the whole conversation each turn, so its notes replace (never append to) the last ones
        "free_text": " ".join(draft.notes.split())[:600] or base.free_text,
        "constraints": cons,
        "stock_pack": None,
    })
    what = profile.business_type or profile.specialty.replace("_", " ")
    name = " ".join(draft.name.split())[:80] or base.name or (f"My {what}" if what else "")
    profile = profile.model_copy(update={"name": name or "My business"})
    missing = []
    if not (profile.business_type or profile.specialty):
        missing.append("what the business is")
    if not profile.customers:
        missing.append("who the customers are")
    return profile, missing


def guard_reply(reply: str, messages: list[ChatMessage], missing: list[str], other_city: bool,
                profile: PracticeProfile | None = None) -> str:
    """The model's reply, unless it carries numbers the owner never wrote; then a template reply."""
    owner = " ".join(m.content for m in messages if m.role == "user")
    said = set(re.findall(r"\d+", owner.replace(",", "")))
    used = set(re.findall(r"\d+", reply.replace(",", "")))
    reply = " ".join(reply.split())[:400]
    if not reply or not used <= said or other_city:  # another city: the model tends to repeat it back
        reply = template_reply(missing, profile)
    return f"{HOUSTON_ONLY} {reply}" if other_city else reply


def template_reply(missing: list[str], profile: PracticeProfile | None = None) -> str:
    if "what the business is" in missing:
        return "What kind of business is it, and who are your customers?"
    if missing:
        return "Who are your customers? For example, families with young children, office workers or renters."
    if profile is None:
        return "Got it. Press Find sites to start, or add your space size or budget first."
    what = profile.business_type or profile.specialty.replace("_", " ")
    c = profile.constraints
    return (f"Got it: a {what} for {profile.customers}, in {c.min_sqft:,} to {c.max_sqft:,} sq ft. "
            "Press Find sites to start, or tell me your budget, parking or anything else first.")


def is_ready(profile: PracticeProfile, missing: list[str], messages: list[ChatMessage]) -> bool:
    """Ready when the business and customers are known, or the owner says go (or three turns in) with a business."""
    turns = sum(m.role == "user" for m in messages)
    last = next((m.content for m in reversed(messages) if m.role == "user"), "")
    has_business = bool(profile.business_type or profile.specialty)
    return not missing or (has_business and (turns >= 3 or bool(GO_WORDS.search(last))))


async def chat(messages: list[ChatMessage], previous: PracticeProfile | None,
               model: BaseChatModel | None = None) -> IntakeResponse:
    """One intake turn. Never raises on model errors: the draft stays and a template question keeps the chat going."""
    if not any(m.role == "user" and m.content.strip() for m in messages):
        profile = previous or PracticeProfile(name="My business")
        return IntakeResponse(reply=GREETING, profile=profile, ready=False, missing=["what the business is"])
    meter = llm.UsageMeter(run_id=f"intake-{uuid.uuid4().hex[:8]}", budget=6)
    convo = "\n".join(f"{'OWNER' if m.role == 'user' else 'YOU'}: {m.content[:1500]}" for m in messages[-12:])
    try:
        chat_model = model if model is not None else llm.get_chat_model("intake", meter)
        out = await chat_model.with_structured_output(IntakeDraft).ainvoke([SystemMessage(PROMPT), HumanMessage(convo)])
        draft = out if isinstance(out, IntakeDraft) else IntakeDraft()
    except Exception:  # keep the conversation alive; the owner can also open Edit details
        draft = IntakeDraft()
    profile, missing = merge(draft, previous)
    reply = guard_reply(draft.reply, messages, missing, draft.mentions_other_city, profile)
    return IntakeResponse(reply=reply, profile=profile, ready=is_ready(profile, missing, messages), missing=missing)
