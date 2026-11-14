"""Intake step one: read the owner's website and pre-fill as much of the intake as it states.

Python fetches the home page and up to five same-site pages whose links look useful (locations, about, services,
providers, insurance, contact), with the same SSRF checks as dataset ingestion (public addresses only, body cap)
minus the domain allowlist. The text goes to the intake model with the question list; the reply is merged like a
chat turn, so the owner reviews every pre-filled answer in the form before a run. Nothing is invented: fields the
site doesn't state stay empty, and the answers are tagged as coming from the website.
"""

import re
import uuid
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import httpx
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from contracts.models import IntakeResponse, PracticeProfile
from services.agent import llm
from services.agent.intake import IntakeDraft, LocationDraft, address_key, merge
from services.agent.questions import SECTORS, base_for, sector_of
from services.agent.tools.allowlist import BlockedURL, safe_request

USEFUL = re.compile(r"locat|office|clinic|about|service|specialt|provider|physician|doctor|insurance|accepted|"
                    r"contact|program|who-we|our-|find|visit|hours|menu|team", re.I)
# Pages worth reading, best first: where the locations are, then contact/hours, who pays, who they are, what they
# do, and individual clinic or provider pages. At most PER_TIER pages from each tier, so services don't crowd out
# the locations page.
TIERS = [re.compile(p, re.I) for p in (
    r"locations?\b|find-(a-|an-|our-)?(location|clinic|office|center)|our-(locations|clinics|offices|centers)|where-we|visit-us",
    r"contact|hours",
    r"insurance|accepted|payment|fees|sliding|eligib|new-patient|patients",
    r"about|who-we|mission",
    r"service|program|specialt",
    r"clinic|office|center|provider|physician|doctor|team",
)]
PER_TIER = 2
MAX_PAGES = 7
MAX_CHARS = 6000  # per page
MAX_TOTAL = 24000

PROMPT = """You pre-fill a site-selection intake from a business's own website. The text of several pages follows.
Fill only what the website states; leave everything else empty. Never guess numbers, sizes or rents.

- business_type / healthcare_specialty: what the business is (a medical practice goes in healthcare_specialty, e.g.
  "orthopedics", "sports medicine", "pediatric dentistry"; a nonprofit, FQHC, free or charity clinic is
  "community health center").
- name: the business's name.
- customers: who it serves, as the site describes them (patients, clients, members), in a sentence.
- services: the service lines it lists (up to 8 short items).
- payers: insurance plans or programs it says it accepts (Medicare, Medicaid, CHIP, commercial, workers' comp).
- languages: languages it says staff speak.
- locations: EVERY location with a street address on these pages (name + full address). Skip PO boxes.
- answers: for each QUESTION below that the website answers, its question_id and a one- or two-sentence answer in
  plain words. Prefix each answer with "Website: ".
- notes: anything else from the site that matters for choosing a new location (hospital affiliations, hours,
  walk-in vs. appointment, equipment such as MRI, surgery center, physical therapy).
- reply: one sentence telling the owner what you found and what they still need to add."""


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.links: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style", "noscript", "svg"):
            self.skip += 1
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.append(href)
        if tag in ("p", "li", "br", "h1", "h2", "h3", "h4", "div", "tr", "address"):
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "noscript", "svg") and self.skip:
            self.skip -= 1

    def handle_data(self, data: str) -> None:
        if not self.skip and data.strip():
            self.parts.append(data.strip() + " ")


def page_text(html: str) -> tuple[str, list[str]]:
    parser = _Text()
    parser.feed(html)
    text = re.sub(r"[ \t]+", " ", "".join(parser.parts))
    text = re.sub(r"\n\s*\n+", "\n", text).strip()
    return text, parser.links


def _tier(path: str) -> int:
    return next((i for i, t in enumerate(TIERS) if t.search(path)), len(TIERS))


def same_site_links(base: str, links: list[str]) -> list[str]:
    """Useful same-site links, best first (locations before services), at most PER_TIER from each tier."""
    host = urlparse(base).hostname or ""
    root = host.removeprefix("www.")
    found: list[str] = []
    for href in links:
        url = urljoin(base, href.split("#")[0].split("?")[0])
        h = (urlparse(url).hostname or "").removeprefix("www.")
        if url.startswith("http") and (h == root or h.endswith(f".{root}")) and USEFUL.search(urlparse(url).path):
            if url not in found and url.rstrip("/") != base.rstrip("/"):
                found.append(url)
    ranked = sorted(found, key=lambda u: _tier(urlparse(u).path))  # stable: page order within a tier
    out: list[str] = []
    per: dict[int, int] = {}
    for url in ranked:
        t = _tier(urlparse(url).path)
        if per.get(t, 0) < PER_TIER:
            per[t] = per.get(t, 0) + 1
            out.append(url)
    return out


def strip_boilerplate(text: str, seen: set[str]) -> str:
    """Drop lines repeated within the page or already seen on the home page (menus, footers)."""
    out: list[str] = []
    mine: set[str] = set()
    for line in text.splitlines():
        key = line.strip().lower()
        if key and key not in mine and key not in seen:
            mine.add(key)
            out.append(line.strip())
    return "\n".join(out)


STREET = re.compile(r"^\d{1,6}\s+[A-Za-z0-9.' -]+")
CITY_ZIP = re.compile(r"\b[A-Z][A-Za-z .'-]+,?\s+(?:[A-Z]{2}|Texas)\.?,?\s+\d{5}(?:-\d{4})?\b")


def find_addresses(text: str) -> list[tuple[str, str]]:
    """[(name, address)] for street addresses on a page: a line starting with a street number that ends in
    "City, ST 12345" (or whose next line does), named by the short line above it. Skips PO boxes."""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    out: list[tuple[str, str]] = []
    for i, line in enumerate(lines):
        if not STREET.match(line) or re.search(r"p\.?\s*o\.?\s*box", line, re.I):
            continue
        address, nxt = line, lines[i + 1] if i + 1 < len(lines) else ""
        if not CITY_ZIP.search(address) and CITY_ZIP.fullmatch(nxt.strip(" ,")):
            address = f"{address.rstrip(' ,')}, {nxt}"
        if not CITY_ZIP.search(address) or len(address) > 140:
            continue
        prev = lines[i - 1] if i else ""
        name = prev if prev and len(prev) <= 60 and not re.search(r"\d{3}", prev) else ""
        out.append((name, " ".join(address.split())))
    return out


async def read_site(url: str) -> list[tuple[str, str]]:
    """[(url, text)] for the home page and up to MAX_PAGES-1 useful same-site pages, menus stripped, not truncated."""
    if not urlparse(url).scheme:
        url = f"https://{url}"
    pages: list[tuple[str, str]] = []
    headers = {"User-Agent": "Mozilla/5.0 (compatible; Siteline intake reader)"}
    async with httpx.AsyncClient(timeout=20, headers=headers) as client:
        resp = await safe_request(client, "GET", url, max_bytes=3_000_000, any_public_host=True)
        resp.raise_for_status()
        text, links = page_text(resp.text)
        home = strip_boilerplate(text, set())
        seen = {l.lower() for l in home.splitlines()}
        pages.append((str(resp.request.url), home))
        for link in same_site_links(str(resp.request.url), links)[: MAX_PAGES - 1]:
            try:
                sub = await safe_request(client, "GET", link, max_bytes=3_000_000, any_public_host=True)
                if sub.status_code == 200 and "html" in sub.headers.get("content-type", "html"):
                    pages.append((link, strip_boilerplate(page_text(sub.text)[0], seen)))
            except (BlockedURL, httpx.HTTPError):
                continue
    return pages


async def prefill(url: str, previous: PracticeProfile | None, model: BaseChatModel | None = None) -> IntakeResponse:
    """Website -> pre-filled profile (merged over what the owner already typed), what is still missing, and a reply."""
    try:
        pages = await read_site(url.strip())
    except (BlockedURL, httpx.HTTPError, ValueError) as exc:
        profile = previous or PracticeProfile(name="My business")
        return IntakeResponse(reply=f"I couldn't read that website ({type(exc).__name__}). You can fill in the questions instead.",
                              profile=profile, ready=False, missing=[])
    # Pages that list addresses go first so they survive the length cap.
    ordered = sorted(pages, key=lambda p: -len(find_addresses(p[1])))
    corpus = "\n\n".join(f"=== PAGE {u}\n{t[:MAX_CHARS]}" for u, t in ordered)[:MAX_TOTAL]
    hint = previous.business_type or previous.specialty if previous else ""
    sector = sector_of(f"{hint} {corpus[:3000]}")
    _, qs = SECTORS[sector]
    questions = "\n".join(f"- {q.id}: {q.text}" for q in (*base_for(sector), *qs))
    meter = llm.UsageMeter(run_id=f"site-{uuid.uuid4().hex[:8]}", budget=4)
    try:
        chat = model if model is not None else llm.get_chat_model("intake", meter)
        out = await chat.with_structured_output(IntakeDraft).ainvoke(
            [SystemMessage(PROMPT), HumanMessage(f"QUESTIONS:\n{questions}\n\nWEBSITE:\n{corpus}")])
        draft = out if isinstance(out, IntakeDraft) else IntakeDraft()
    except Exception as exc:  # the owner can still fill the form
        profile = previous or PracticeProfile(name="My business")
        return IntakeResponse(reply=f"I read {len(pages)} pages but couldn't extract them ({type(exc).__name__}).",
                              profile=profile, ready=False, missing=[])
    # What the owner already typed wins over the website; a multi-specialty system names no single specialty.
    if previous and (previous.business_type or previous.specialty):
        draft = draft.model_copy(update={"business_type": "", "healthcare_specialty": ""})
    elif sector == "healthcare.community_health":  # the site says it is a nonprofit / community health center
        draft = draft.model_copy(update={"business_type": "", "healthcare_specialty": "community health center"})
    elif "," in draft.healthcare_specialty or " and " in draft.healthcare_specialty:
        draft = draft.model_copy(update={"healthcare_specialty": ""})
    # Every street address on the site is a location, whether or not the model listed it; one per place.
    locs = list(draft.locations)
    keys = {address_key(l.address) for l in locs}
    for _, text in pages:
        for name, address in find_addresses(text):
            if address_key(address) not in keys:
                keys.add(address_key(address))
                locs.append(LocationDraft(name=name or f"Location {len(locs) + 1}", address=address))
    unique: dict[str, LocationDraft] = {}
    for loc in locs:
        unique.setdefault(address_key(loc.address), loc)
    draft = draft.model_copy(update={"locations": list(unique.values())})
    profile, missing = merge(draft, previous)
    found = []
    if profile.existing_offices:
        found.append(f"{len(profile.existing_offices)} locations")
    if profile.services:
        found.append("services")
    if profile.payers_accepted:
        found.append("insurance")
    reply = (f"Read {len(pages)} pages of your website and filled in {', '.join(found) or 'what it says'}. "
             "Check each answer; anything the site doesn't say is still yours to add.")
    return IntakeResponse(reply=reply, profile=profile, ready=not missing, missing=missing)
