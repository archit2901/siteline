"""analyst-critic: top-10 report with claims built from Python templates; the model only chooses and orders them.

Every number in a claim comes from a stored feature value, a model output or a site field, and verify_claims checks
each one against its evidence_refs (2% relative or ±1 on integers; shares may be written as percents).
"""

import json
import re
from dataclasses import dataclass

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from contracts.models import (
    Claim,
    CommunityNeed,
    DemandModel,
    Factor,
    PracticeProfile,
    RankedSite,
    Recommendation,
    Report,
    SiteCandidate,
    SiteInspection,
)
from services.agent import llm
from services.agent.tools import geomath
from services.agent.tools.geo_client import GeoClient
from services.agent.tools.packs import pack_for

TOP = 10
MAX_CLAIMS = 5
NUMBER = re.compile(r"[-+]?\$?\d[\d,]*(?:\.\d+)?%?")
SELECT_PROMPT = (
    "You are the analyst-critic for a healthcare practice choosing its next office. For each site, choose the 3 to 5 "
    "claims that best explain why it fits THIS practice, most persuasive first. Choose only from the listed claim "
    "keys; never write new text or numbers."
)


@dataclass(frozen=True)
class Problem:
    site_id: str
    number: str
    claim: str

    def __str__(self) -> str:
        return f"{self.site_id}: {self.number!r} in {self.claim!r}"


@dataclass
class ClaimOption:
    key: str
    text: str
    refs: list[str]


class SiteChoice(BaseModel):
    site_id: str
    keys: list[str]


class Selection(BaseModel):
    sites: list[SiteChoice]


def _fmt(value: float, unit: str, feature_id: str = "") -> str:
    if unit == "share":
        return f"{value * 100:.0f}%"
    if unit == "USD":
        return f"${value:,.0f}"
    if unit == "%":
        return f"{value:+.1f}%" if "growth" in feature_id else f"{value:.1f}%"
    if unit == "km":
        return f"{value:.1f} km"
    return f"{value:,.0f}"


IRREGULAR = {"children": "child", "people": "person", "adults": "adult", "patients": "patient"}


def _singular_word(word: str) -> str:
    if word in IRREGULAR:
        return IRREGULAR[word]
    return word[:-1] if word.endswith("s") and not word.endswith("ss") and len(word) > 3 else word


def singular(label: str) -> str:
    """'renter households' -> 'renter household', 'children under 18' -> 'child under 18', 'adults 18-44' ->
    'adult 18-44': the plural noun is the last word before any qualifier (under, over, 18-44, aged ...)."""
    words = label.split()
    for i, w in enumerate(words):
        if i + 1 == len(words) or words[i + 1] in ("under", "over", "aged", "with", "without") or words[i + 1][:1].isdigit():
            words[i] = _singular_word(w)
            break
    return " ".join(words)


def rent_per_customer(site: SiteCandidate, ranked: RankedSite) -> float | None:
    """Annual asking rent ÷ net-new customers (None when rent, size or net-new is missing)."""
    if site.rent_psf and site.sqft and ranked.net_new_patients >= 1:
        return site.rent_psf * site.sqft / ranked.net_new_patients
    return None


async def claim_options(site: SiteCandidate, ranked: RankedSite, factors: list[Factor], geo: GeoClient,
                        label: str = "patients") -> list[ClaimOption]:
    """Every claim the critic may use for one site, with text rendered from stored values."""
    out = []
    refs = [f"model:net_new@{site.id}"]
    if label == "patients":
        text = f"Estimated {ranked.net_new_patients:,.0f} net-new patients a year"
        if ranked.cannibalized_patients >= 1:
            text += f", after {ranked.cannibalized_patients:,.0f} shift over from your existing locations"
            refs.append(f"model:cannibalized@{site.id}")
        text += "."
    else:  # a reach measure, not a sales forecast
        text = f"About {ranked.net_new_patients:,.0f} {label} in reach would pick this site over competitors and your other locations."
    out.append(ClaimOption("net_new", text, refs))

    meta = {m.id: m for m in await geo.list_features("hou")}  # stock + agent-found features
    seen: set[str] = set()
    for f in factors:
        fid = f.feature_id
        if not fid or fid.startswith("site:") or fid in seen or fid not in meta or not site.h3:
            continue
        seen.add(fid)
        value = (await geo.feature_value(fid, site.h3)).value
        if value is None:
            continue
        m = meta[fid]
        out.append(ClaimOption(f"feature:{fid}", f"{m.label or f.factor} here: {_fmt(value, m.unit, fid)}.",
                               [f"feature:{fid}@{site.h3}"]))

    if site.sqft:
        where = "on the ground floor" if site.floor == 1 else (f"on floor {site.floor}" if site.floor else "")
        out.append(ClaimOption("size", f"{site.sqft:,} sq ft {where}".strip() + ".", [f"site:sqft@{site.id}"]))
    if site.rent_psf:
        out.append(ClaimOption("rent", f"Asking rent ${site.rent_psf:.2f} per sq ft per year.", [f"site:rent_psf@{site.id}"]))
    if site.parking_spaces:
        out.append(ClaimOption("parking", f"{site.parking_spaces:,} parking spaces at the building.",
                               [f"site:parking_spaces@{site.id}"]))
    per_customer = rent_per_customer(site, ranked)
    if per_customer is not None:
        out.append(ClaimOption("rent_per_customer", f"Annual asking rent per net-new {singular(label)}: ${per_customer:,.2f}.",
                               [f"model:rent_per_customer@{site.id}"]))
    if ranked.stability is not None:
        out.append(ClaimOption("stability", f"Stays in the top 10 in {ranked.stability * 100:.0f}% of alternative weightings.",
                               [f"model:stability@{site.id}"]))
    return out


def inspection_options(site: SiteCandidate, insp: SiteInspection | None) -> list[ClaimOption]:
    """The site-inspector's findings as claims (texts rendered in inspector.findings from stored values)."""
    if insp is None:
        return []
    refs = {
        "aerial": [f"inspect:{n}@{site.id}" for n in ("parked_cars", "radius_m", "listed_parking", "occupancy")],
        "scan": [f"inspect:{n}@{site.id}" for n in ("scan_area_sqft", "scan_floors", "scan_rooms")] + [f"site:sqft@{site.id}"],
    }
    out = []
    for text in insp.findings:
        key = "inspect_aerial" if text.startswith("Aerial") else "inspect_scan"
        out.append(ClaimOption(key, text, refs["aerial" if key == "inspect_aerial" else "scan"]))
    return out


def community_option(site: SiteCandidate, need: CommunityNeed | None) -> list[ClaimOption]:
    """Who lives in the catchment: households without a car and in lower-income tracts (Census)."""
    if need is None or need.no_car_share is None or need.lower_income_share is None:
        return []
    text = (f"Within its {need.minutes:.0f}-minute catchment, {need.no_car_share * 100:.0f}% of households have no car "
            f"and {need.lower_income_share * 100:.0f}% live in lower-income tracts (Census).")
    return [ClaimOption("community", text, [f"community:{n}@{site.id}" for n in ("minutes", "no_car_share", "lower_income_share")])]


def evidence_values(inspections: dict[str, SiteInspection] | None,
                    community: dict[str, CommunityNeed] | None) -> dict[str, float]:
    """inspect:<field>@<site> and community:<field>@<site> -> stored number (what the site-inspector and the
    community lens measured), so verify_claims checks their claims like every other claim."""
    out: dict[str, float] = {}
    for prefix, items in (("inspect", inspections or {}), ("community", community or {})):
        for sid, obj in items.items():
            for name, value in obj.model_dump().items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    out[f"{prefix}:{name}@{sid}"] = float(value)
    return out


async def resolve(ref: str, sites: dict[str, SiteCandidate], ranked: dict[str, RankedSite], geo: GeoClient,
                  extra: dict[str, float] | None = None) -> float | None:
    """The stored value an evidence_ref points to."""
    kind, _, rest = ref.partition(":")
    name, _, target = rest.partition("@")
    if kind in ("inspect", "community"):
        return (extra or {}).get(ref)
    if kind == "feature":
        return (await geo.feature_value(name, target)).value
    if kind == "model" and target in ranked:
        r = ranked[target]
        if name == "rent_per_customer":
            return rent_per_customer(sites[target], r) if target in sites else None
        return {"net_new": r.net_new_patients, "cannibalized": r.cannibalized_patients, "captured": r.captured_patients,
                "stability": r.stability, "zone_score": r.zone_score, "final_score": r.final_score}.get(name)
    if kind == "site" and target in sites:
        value = getattr(sites[target], name, None)
        return float(value) if isinstance(value, (int, float)) else None
    return None


def _matches(number: str, value: float) -> bool:
    raw = number.replace("$", "").replace(",", "").replace("+", "")
    percent = raw.endswith("%")
    x = float(raw.rstrip("%"))
    targets = [value, value * 100] if percent else [value]
    return any(abs(x - t) <= max(1.0, 0.02 * abs(t)) for t in targets)


async def verify_claims(report: Report, sites: dict[str, SiteCandidate], ranked: dict[str, RankedSite],
                        geo: GeoClient, extra: dict[str, float] | None = None) -> list[Problem]:
    """Every number in every claim that does not match a value referenced by that claim."""
    problems = []
    for rec in report.recommendations:
        for claim in rec.justification:
            values = [v for v in [await resolve(r, sites, ranked, geo, extra) for r in claim.evidence_refs] if v is not None]
            for number in NUMBER.findall(claim.text):
                if not any(_matches(number, v) for v in values):
                    problems.append(Problem(rec.site.id, number, claim.text))
    return problems


def best_per_building(ranked: list[RankedSite], sites: dict[str, SiteCandidate]) -> list[RankedSite]:
    """One card per building: keep each property's best-ranked space (suites in one building share Huff numbers)."""
    seen: set[str] = set()
    out = []
    for r in ranked:
        site = sites[r.site_id]
        building = str(site.checks.get("property_id") or site.address.strip().lower())
        if building not in seen:
            seen.add(building)
            out.append(r)
    return out


async def build_report(run_id: str, profile: PracticeProfile, factors: list[Factor], sites: list[SiteCandidate],
                       ranked: list[RankedSite], geo: GeoClient, meter: llm.UsageMeter | None,
                       caveats: list[str], select: bool = True,
                       demand: DemandModel | None = None,
                       inspections: dict[str, SiteInspection] | None = None,
                       community: dict[str, CommunityNeed] | None = None) -> tuple[Report, list[Problem]]:
    """The verified top-10 report and any verification problems (claims that fail are dropped, never rewritten).

    select=False skips the model and uses the default claim order (for /rescore, which must not call an LLM)."""
    by_site = {s.id: s for s in sites}
    by_rank = {r.site_id: r for r in ranked}
    top = best_per_building([r for r in ranked if r.excluded_reason is None], by_site)[:TOP]
    label = demand.demand_label if demand else "patients"
    options = {r.site_id: await claim_options(by_site[r.site_id], r, factors, geo, label) for r in top}
    extra_options = {r.site_id: inspection_options(by_site[r.site_id], (inspections or {}).get(r.site_id))
                     + community_option(by_site[r.site_id], (community or {}).get(r.site_id)) for r in top}
    extra = evidence_values(inspections, community)

    listing = "\n\n".join(
        f"SITE {sid} ({by_site[sid].address}):\n" + "\n".join(f"- {o.key}: {o.text}" for o in opts)
        for sid, opts in options.items())
    practice = f"{profile.name}, {profile.specialty}; factors: " + "; ".join(f.factor for f in factors)
    chosen: dict[str, list[str]] = {}
    try:
        if not select:
            raise LookupError("selection disabled")
        selection = await llm.get_chat_model("critic", meter).with_structured_output(Selection).ainvoke(
            [SystemMessage(SELECT_PROMPT), HumanMessage(f"PRACTICE: {practice}\n\n{listing}")])
        if isinstance(selection, Selection):
            chosen = {s.site_id: s.keys for s in selection.sites}
    except Exception:  # selection is a nicety; fall back to the default order below
        chosen = {}

    no_competitors = geomath.competitor_column(pack_for(profile), demand.competitor_feature if demand else None) is None
    recs = []
    for rank, r in enumerate(top, start=1):
        opts = {o.key: o for o in options[r.site_id]}
        keys = [k for k in chosen.get(r.site_id, []) if k in opts][:MAX_CLAIMS]
        if "net_new" not in keys:
            keys = ["net_new", *keys][:MAX_CLAIMS]
        for k in opts:  # top up to at least 3 claims in default order
            if len(keys) >= 3:
                break
            if k not in keys:
                keys.append(k)
        for o in extra_options[r.site_id]:  # what the inspector and the community lens found is always shown
            opts[o.key] = o
            keys.append(o.key)
        site = by_site[r.site_id]
        site_caveats = []
        if no_competitors:
            site_caveats.append(geomath.NO_COMPETITOR_CAVEAT)
        if site.available is None:
            site_caveats.append("The listing does not say whether the space is available; treat it as a candidate building.")
        recs.append(Recommendation(
            rank=rank, site=site, net_new_patients=r.net_new_patients, zone_score=r.zone_score,
            site_quality=r.site_quality, final_score=r.final_score, stability=r.stability,
            justification=[Claim(text=opts[k].text, evidence_refs=opts[k].refs) for k in keys], caveats=site_caveats,
            annual_rent_per_customer=None if (rpc := rent_per_customer(site, r)) is None else round(rpc, 2),
            inspection=(inspections or {}).get(r.site_id), community=(community or {}).get(r.site_id),
        ))

    provenance = {f.id: f.provenance_url for f in await geo.list_features("hou")}
    report = Report(
        run_id=run_id, profile=profile, factors=factors, recommendations=recs, caveats=caveats,
        sources={f.feature_id: provenance[f.feature_id] for f in factors if f.feature_id in provenance},
        verified=False, demand_label=label, demand=demand,
    )
    problems = await verify_claims(report, by_site, by_rank, geo, extra)
    if problems:  # drop failing claims instead of shipping an unverified number
        bad = {p.claim for p in problems}
        for rec in report.recommendations:
            rec.justification = [c for c in rec.justification if c.text not in bad]
        report.caveats.append(f"{len(problems)} claims were removed because their numbers did not match the data.")
        problems = await verify_claims(report, by_site, by_rank, geo, extra)
    report.verified = not problems
    return report, problems


def report_json(report: Report) -> str:
    return json.dumps(report.model_dump(mode="json"), indent=2)
