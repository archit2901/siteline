"""intake: a chat in the owner's words -> PracticeProfile, asking good questions until there is enough to run.

Nova Pro reads the conversation and fills an IntakeDraft. Python merges it into the previous draft, clamps the
numbers, maps healthcare specialties onto their packs, decides what is still missing and whether the agent can run,
and guards the reply: a reply that states numbers the owner never wrote is replaced by a template question.

The questions come from services/agent/questions.py: a base set for every business (current locations, customers,
what makes you different, what pulls people to a neighborhood...) and a sector set chosen from the business type.
A run needs the business, the customers, the current locations (or "this is our first") and the sector's required
answers. There is no turn-count escape: a ranking from too little data is worse than one more question.
"""

import re
import uuid

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from contracts.models import ChatMessage, Constraints, IntakeResponse, Office, PracticeProfile
from services.agent import llm
from services.agent.questions import BASE, SECTORS, Question, base_for, sector_of

SPECIALTIES = {
    "orthopedics": "orthopedics", "orthopedic": "orthopedics", "orthopaedics": "orthopedics",
    "orthopedic surgery": "orthopedics", "sports medicine": "orthopedics",
    "pediatric_dentistry": "pediatric_dentistry", "pediatric dentistry": "pediatric_dentistry",
    "pediatric dentist": "pediatric_dentistry", "kids dentist": "pediatric_dentistry",
    "dialysis": "dialysis", "nephrology": "dialysis", "kidney care": "dialysis",
    "physical_therapy": "physical_therapy", "physical therapy": "physical_therapy", "physiotherapy": "physical_therapy",
    "pt": "physical_therapy", "rehab": "physical_therapy", "rehabilitation": "physical_therapy",
}
FIRST_LOCATION = "Current locations: none yet (this will be the first)"
SQFT_RANGE = (200, 100_000)
GREETING = ("Tell me what you want to open in Houston, who your customers are, and where your current locations "
            "are (or that this is your first).")
HOUSTON_ONLY = ("Siteline covers Houston only for now (Harris, Fort Bend and Montgomery counties), so I'll search "
                "there.")

PROMPT = """You help a business owner describe the new location they want to open in Houston, Texas, so a site
selection agent can find where they will succeed. You are a sharp location strategist: ask the questions that reveal
which neighborhood traits actually predict success for THIS business, not generic ones. Read the whole conversation
and fill every field you can from what the owner said. Leave a field empty or null when the owner has not said it;
never guess sizes, rents, customers or addresses.

- business_type: what the business is, in a few plain words ("laundromat", "food pantry", "pediatric dentist").
- healthcare_specialty: only for a medical practice: its specialty in plain words ("orthopedics", "pediatric
  dentistry", "dialysis", "physical therapy", "cardiology", "urgent care"...). Otherwise empty.
- customers: who the customers (patients, clients, members) are, in the owner's words.
- services, languages, payers (insurance accepted): lists, only if mentioned.
- locations: every current location the owner gave, as name + street address (or cross streets / neighborhood).
- first_location: true only if the owner said they have no current location.
- min_sqft, max_sqft, max_rent_psf (dollars per sq ft per year), min_parking_per_1000sf, ground_floor: only the
  numbers the owner gave. "about 1,500 sq ft" means min 1200 and max 1800.
- name: the business's name if given.
- answers: for each QUESTION below that the owner has answered (even partly), its question_id and the answer in the
  owner's words, condensed to one or two sentences.
- notes: anything else the owner said that matters for choosing a location.
- mentions_other_city: true if the owner wants a city other than Houston.
- reply: your next chat message. Briefly acknowledge what they said in a few words, then ask the ONE or TWO most
  useful unanswered questions, starting with any STILL NEEDED item. Make questions concrete for this business (a
  food pantry: who it serves and how clients get there; a laundromat: apartments nearby and the anchor tenants).
  When nothing is still needed, summarize in one sentence and say they can press Find sites, or keep adding detail
  (their best and worst current location and why is the most useful). Never state facts or numbers about the
  market."""


def address_key(address: str) -> str:
    """Street number + ZIP: the same place written two ways ("Blvd." vs "Blvd", suite or not) matches."""
    num = re.match(r"\s*(\d+)", address)
    zips = re.findall(r"\b(\d{5})(?:-\d{4})?\b", address)
    return f"{num.group(1)}-{zips[-1]}" if num and zips else " ".join(address.lower().split())


class LocationDraft(BaseModel):
    name: str = ""
    address: str = ""


class AnswerDraft(BaseModel):
    question_id: str
    answer: str


class IntakeDraft(BaseModel):
    business_type: str = ""
    healthcare_specialty: str = ""
    customers: str = ""
    services: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    payers: list[str] = Field(default_factory=list)
    locations: list[LocationDraft] = Field(default_factory=list)
    first_location: bool = False
    answers: list[AnswerDraft] = Field(default_factory=list)
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


PAYERS = {"medicare": "medicare", "medicaid": "medicaid", "chip": "chip", "commercial": "commercial",
          "private": "commercial", "workers' comp": "workers_comp", "workers comp": "workers_comp",
          "workers_comp": "workers_comp"}
ANSWER_LINE = re.compile(r"^\[(?P<id>[a-z_]+)\] (?P<label>[^:]{1,80}): (?P<answer>.+)$")


def _specialty(text: str) -> str:
    """Known specialties map to their pack ids; any other medical specialty is kept as plain words."""
    low = " ".join(text.lower().replace("_", " ").split())
    return SPECIALTIES.get(low) or SPECIALTIES.get(low.replace(" ", "_")) or low.replace(" ", "_")[:40]


def _questions(sector: str) -> dict[str, Question]:
    return {q.id: q for q in (*BASE, *SECTORS[sector][1])}


def parse_answers(free_text: str) -> tuple[dict[str, str], list[str]]:
    """free_text -> ({question_id: answer}, other lines). Answers are stored as '[id] Short label: answer'."""
    answers: dict[str, str] = {}
    other: list[str] = []
    for line in free_text.splitlines():
        m = ANSWER_LINE.match(line.strip())
        if m:
            answers[m["id"]] = m["answer"]
        elif line.strip():
            other.append(line.strip())
    return answers, other


def _label(q: Question) -> str:
    return q.text.split("?")[0].replace(":", "")[:80]


def compose_free_text(answers: dict[str, str], notes: str, sector: str) -> str:
    qs = _questions(sector)
    lines = [f"[{k}] {_label(qs[k]) if k in qs else k}: {v}" for k, v in answers.items()]
    return "\n".join([*lines, *([notes] if notes else [])])[:3000]


def merge(draft: IntakeDraft, previous: PracticeProfile | None) -> tuple[PracticeProfile, list[str]]:
    """Validated draft merged over the previous profile -> (profile, missing). Nothing the owner said is dropped."""
    base = previous or PracticeProfile(name="", constraints=Constraints())
    cons = base.constraints.model_copy()
    specialty = _specialty(draft.healthcare_specialty) if draft.healthcare_specialty.strip() else ""
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

    # current locations: keep every one the owner gave (by address), in order
    offices = list(base.existing_offices)
    known = {address_key(o.address or o.name) for o in offices}
    for loc in draft.locations[:12]:
        address = " ".join(loc.address.split())[:160]
        if address and address_key(address) not in known:
            known.add(address_key(address))
            offices.append(Office(name=" ".join(loc.name.split())[:80] or f"Location {len(offices) + 1}", address=address))

    payers = sorted({PAYERS[p.strip().lower()] for p in draft.payers if p.strip().lower() in PAYERS})
    profile = base.model_copy(update={
        **kind,
        "customers": " ".join(draft.customers.split())[:300] or base.customers,
        "services": [s.strip()[:60] for s in draft.services if s.strip()][:8] or base.services,
        "languages": [s.strip()[:30] for s in draft.languages if s.strip()][:6] or base.languages,
        "existing_offices": offices,
        "constraints": cons,
        "stock_pack": None,
    })
    if payers and profile.specialty:
        profile = profile.model_copy(update={"payers_accepted": payers})
    sector = sector_of(profile.business_type or profile.name, profile.specialty)

    # answers: earlier ones stay unless the owner changed them; the model re-reads the conversation each turn
    answers, other = parse_answers(base.free_text)
    valid = _questions(sector)
    for item in draft.answers:
        text = " ".join(item.answer.split())[:300]
        if item.question_id in valid and text:
            answers[item.question_id] = text
    if draft.first_location and not offices:
        answers["locations"] = FIRST_LOCATION.split(": ", 1)[1]
    notes = " ".join(draft.notes.split())[:600] or " ".join(other)[:600]
    profile = profile.model_copy(update={"free_text": compose_free_text(answers, notes, sector)})

    what = profile.business_type or profile.specialty.replace("_", " ")
    name = " ".join(draft.name.split())[:80] or base.name or (f"My {what}" if what else "")
    profile = profile.model_copy(update={"name": name or "My business"})
    return profile, missing_for(profile)


def missing_for(profile: PracticeProfile) -> list[str]:
    """What a run still needs, in plain words: the business, its customers, current locations (or 'first'), and
    the sector's required answers."""
    missing = []
    if not (profile.business_type or profile.specialty):
        return ["what the business is", "who the customers are", "your current locations"]
    if not profile.customers:
        missing.append("who the customers are")
    answers, _ = parse_answers(profile.free_text)
    if not profile.existing_offices and "locations" not in answers:
        missing.append("your current locations")
    sector = sector_of(profile.business_type or profile.name, profile.specialty)
    for q in SECTORS[sector][1]:
        if not q.required:
            continue
        filled = {"services": bool(profile.services), "payers": bool(profile.payers_accepted),
                  "customers": bool(profile.customers)}.get(q.field, False)
        if not filled and q.id not in answers:
            missing.append(_label(q).lower())
    return missing


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
        return GREETING
    if "who the customers are" in missing:
        return "Who are your customers? Ages, household type, income, how they pay, languages."
    if "your current locations" in missing:
        return ("Where are your current locations (name and street address)? If this is your first, just say so. "
                "Your own sites are the best guide to where a new one will work.")
    if missing:
        return f"One more thing before I search: {missing[0]}?"
    if profile is None:
        return "Got it. Press Find sites to start, or tell me which current location does best and why."
    what = profile.business_type or profile.specialty.replace("_", " ")
    c = profile.constraints
    return (f"Got it: a {what} for {profile.customers}, in {c.min_sqft:,} to {c.max_sqft:,} sq ft. Press Find sites, "
            "or tell me which current location does best and why, and what pulls people to a neighborhood.")


def is_ready(profile: PracticeProfile, missing: list[str], messages: list[ChatMessage]) -> bool:
    """Ready only when nothing required is missing. No turn-count or 'just run it' escape."""
    return not missing


def _question_list(messages: list[ChatMessage], previous: PracticeProfile | None) -> str:
    owner = " ".join(m.content for m in messages if m.role == "user")
    hint = previous.business_type or previous.specialty if previous else ""
    sector = sector_of(f"{hint} {owner}"[:4000], previous.specialty if previous else "")
    label, qs = SECTORS[sector]
    lines = [f"- {q.id}{' (REQUIRED)' if q.required else ''}: {q.text}" for q in (*base_for(sector), *qs)]
    return f"QUESTIONS (base + {label}):\n" + "\n".join(lines)


async def chat(messages: list[ChatMessage], previous: PracticeProfile | None,
               model: BaseChatModel | None = None) -> IntakeResponse:
    """One intake turn. Never raises on model errors: the draft stays and a template question keeps the chat going."""
    if not any(m.role == "user" and m.content.strip() for m in messages):
        profile = previous or PracticeProfile(name="My business")
        return IntakeResponse(reply=GREETING, profile=profile, ready=False, missing=missing_for(profile))
    meter = llm.UsageMeter(run_id=f"intake-{uuid.uuid4().hex[:8]}", budget=6)
    convo = "\n".join(f"{'OWNER' if m.role == 'user' else 'YOU'}: {m.content[:1500]}" for m in messages[-16:])
    still = missing_for(previous) if previous else ["what the business is", "who the customers are", "your current locations"]
    request = f"{_question_list(messages, previous)}\n\nSTILL NEEDED: {', '.join(still) or 'nothing'}\n\nCONVERSATION:\n{convo}"
    try:
        chat_model = model if model is not None else llm.get_chat_model("intake", meter)
        out = await chat_model.with_structured_output(IntakeDraft).ainvoke([SystemMessage(PROMPT), HumanMessage(request)])
        draft = out if isinstance(out, IntakeDraft) else IntakeDraft()
    except Exception:  # keep the conversation alive; the owner can also open Edit details
        draft = IntakeDraft()
    profile, missing = merge(draft, previous)
    reply = guard_reply(draft.reply, messages, missing, draft.mentions_other_city, profile)
    return IntakeResponse(reply=reply, profile=profile, ready=is_ready(profile, missing, messages), missing=missing)
