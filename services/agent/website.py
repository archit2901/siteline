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
from services.agent.intake import IntakeDraft, merge
from services.agent.questions import SECTORS, base_for, sector_of
from services.agent.tools.allowlist import BlockedURL, safe_request

USEFUL = re.compile(r"locat|office|clinic|about|service|specialt|provider|physician|doctor|insurance|accepted|"
                    r"contact|program|who-we|our-|find|visit|hours|menu|team", re.I)
MAX_PAGES = 6
MAX_CHARS = 6000  # per page
MAX_TOTAL = 24000

PROMPT = """You pre-fill a site-selection intake from a business's own website. The text of several pages follows.
Fill only what the website states; leave everything else empty. Never guess numbers, sizes or rents.

- business_type / healthcare_specialty: what the business is (a medical practice goes in healthcare_specialty, e.g.
  "orthopedics", "sports medicine", "pediatric dentistry").
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


def same_site_links(base: str, links: list[str]) -> list[str]:
    host = urlparse(base).hostname or ""
    root = host.removeprefix("www.")
    out: list[str] = []
    for href in links:
        url = urljoin(base, href.split("#")[0])
        h = (urlparse(url).hostname or "").removeprefix("www.")
        if url.startswith("http") and (h == root or h.endswith(f".{root}")) and USEFUL.search(urlparse(url).path):
            if url not in out and url.rstrip("/") != base.rstrip("/"):
                out.append(url)
    return out


async def read_site(url: str) -> list[tuple[str, str]]:
    """[(url, text)] for the home page and up to MAX_PAGES-1 useful same-site pages."""
    if not urlparse(url).scheme:
        url = f"https://{url}"
    pages: list[tuple[str, str]] = []
    headers = {"User-Agent": "Mozilla/5.0 (compatible; Siteline intake reader)"}
    async with httpx.AsyncClient(timeout=20, headers=headers) as client:
        resp = await safe_request(client, "GET", url, max_bytes=3_000_000, any_public_host=True)
        resp.raise_for_status()
        text, links = page_text(resp.text)
        pages.append((str(resp.request.url), text[:MAX_CHARS]))
        for link in same_site_links(str(resp.request.url), links)[: MAX_PAGES - 1]:
            try:
                sub = await safe_request(client, "GET", link, max_bytes=3_000_000, any_public_host=True)
                if sub.status_code == 200 and "html" in sub.headers.get("content-type", "html"):
                    pages.append((link, page_text(sub.text)[0][:MAX_CHARS]))
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
    corpus = "\n\n".join(f"=== PAGE {u}\n{t}" for u, t in pages)[:MAX_TOTAL]
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
    elif "," in draft.healthcare_specialty or " and " in draft.healthcare_specialty:
        draft = draft.model_copy(update={"healthcare_specialty": ""})
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
