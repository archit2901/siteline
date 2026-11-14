"""Real-estate strategy: service lines -> space program, visibility need, and lease vs. buy vs. build.

Python only (no model): every number here is either the owner's, CoStar's, or a PLANNING ASSUMPTION listed in
ASSUMPTIONS and shown with the result. The point is the comparison and why it flips (heavy, specialized buildouts
such as MRI suites or surgery centers stay with the building, so owning or building to suit can beat leasing), not
a quote. Written to the run as strategy.json and summarized in the report caveats.
"""

import re
from typing import Literal

from pydantic import BaseModel, Field

from contracts.models import PracticeProfile, RankedSite, SiteCandidate

Need = Literal["high", "medium", "low"]

# service line -> (pattern over services, specialty, business type and notes; extra sq ft; buildout $ beyond a
# standard fit-out; requirements). Planning assumptions for Houston, 2026.
LINES: dict[str, tuple[str, int, int, list[str]]] = {
    "xray": (r"x-?ray|radiograph", 250, 60_000, ["lead-shielded room"]),
    "mri": (r"\bmri\b|magnetic resonance", 900, 450_000,
            ["ground floor or slab", "heavy floor load", "RF shielding", "magnet delivery access", "3-phase power"]),
    "physical_therapy": (r"physical therap|physiotherap|\bpt\b|rehab", 2_500, 100_000, ["open gym space", "elevator if upstairs"]),
    "asc": (r"surgery center|surgical center|\basc\b|ambulatory surg|operating room", 10_000, 2_500_000,
            ["Medicare-certified ASC buildout", "separate entrance", "generator", "medical gas", "ground floor"]),
    "urgent_care": (r"urgent care|walk-?in", 0, 50_000, ["street visibility and signage", "evening and weekend access"]),
    "cold_storage": (r"cold storage|walk-in cooler|freezer|refrigerat", 400, 80_000, ["walk-in cooler or freezer"]),
    "loading": (r"loading dock|box truck|\bdock\b|truck", 0, 40_000, ["loading dock or truck access"]),
    "kitchen": (r"commercial kitchen|\bhoods?\b|hot meals|restaurant|\bcafe\b|bakery", 0, 150_000, ["grease trap and hood"]),
}
STANDARD_TI_PSF = 90.0  # standard medical/retail fit-out, $ per sq ft
TI_ALLOWANCE_PSF = 50.0  # what a landlord typically funds on a new lease
ASSUMPTIONS = {
    "lease_term_years": 10,
    "cap_rate": 0.075,  # building value ≈ annual rent ÷ cap rate
    "mortgage_rate": 0.07, "loan_to_value": 0.75, "equity_return": 0.08,
    "property_tax_rate": 0.023,  # Harris County, all taxing units, approx.
    "insurance_maintenance_rate": 0.015, "appreciation_rate": 0.02,
    "improvement_life_years": 20,
    "build_cost_psf": 400.0,  # land + shell + standard fit-out, suburban Houston medical/retail
}


class SpaceProgram(BaseModel):
    service_lines: list[str]
    base_sqft: tuple[int, int]
    recommended_sqft: tuple[int, int]
    requirements: list[str]
    special_buildout_usd: int  # beyond a standard fit-out


class TenureOption(BaseModel):
    option: Literal["lease", "buy", "build"]
    annual_cost_usd: int
    upfront_usd: int
    notes: str


class SiteTenure(BaseModel):
    site_id: str
    address: str
    sqft: int | None = None
    options: list[TenureOption]
    cheapest: str


class Strategy(BaseModel):
    program: SpaceProgram
    visibility: Need
    visibility_reason: str
    preferred_space_uses: list[str]
    tenure_preference: str
    recommendation: str
    sites: list[SiteTenure] = Field(default_factory=list)
    assumptions: dict[str, float] = Field(default_factory=lambda: dict(ASSUMPTIONS))


def _text(profile: PracticeProfile) -> str:
    return " ".join([profile.specialty, profile.business_type, *profile.services, profile.free_text]).lower().replace("_", " ")


def _answer(profile: PracticeProfile, qid: str) -> str:
    m = re.search(rf"^\[{qid}\][^:]*: (.+)$", profile.free_text, re.M)
    return m.group(1).lower() if m else ""


def service_lines(profile: PracticeProfile) -> list[str]:
    text = _text(profile)
    return [k for k, (pattern, *_rest) in LINES.items() if re.search(pattern, text)]


def space_program(profile: PracticeProfile) -> SpaceProgram:
    lines = service_lines(profile)
    c = profile.constraints
    extra = sum(LINES[k][1] for k in lines)
    reqs = [r for k in lines for r in LINES[k][3]]
    return SpaceProgram(service_lines=lines, base_sqft=(c.min_sqft, c.max_sqft),
                        recommended_sqft=(c.min_sqft + extra, c.max_sqft + extra),
                        requirements=list(dict.fromkeys(reqs)), special_buildout_usd=sum(LINES[k][2] for k in lines))


def visibility_need(profile: PracticeProfile) -> tuple[Need, str]:
    said = _answer(profile, "visibility")
    if "must be seen" in said or "busy road" in said:
        return "high", "you said walk-in customers need to see you from the road"
    if "doesn't matter" in said or "referral or appointment" in said:
        return "low", "you said customers come by referral or appointment"
    lines = set(service_lines(profile))
    if "urgent_care" in lines:
        return "high", "walk-in care is decided in the moment, from the road"
    if lines & {"asc", "physical_therapy", "mri"} and not lines - {"asc", "physical_therapy", "mri", "xray"}:
        return "low", "surgery, therapy and imaging patients arrive by referral and appointment"
    if "helps" in said:
        return "medium", "you said visibility helps but is not essential"
    sector_text = _text(profile)
    if re.search(r"restaurant|cafe|coffee|retail|store|shop|laundromat|salon|barber|gym", sector_text):
        return "high", "this kind of business wins impulse and drive-by customers"
    if profile.specialty:
        return "medium", "most patients book ahead, but a visible sign builds awareness"
    return "medium", "no strong reason either way"


SPACE_FIT: dict[Need, dict[str, float]] = {
    "high": {"Retail": 1.0, "OfficeRetail": 0.9, "Medical": 0.6, "OfficeMedical": 0.5, "Office": 0.4, "Flex": 0.3, "Industrial": 0.2},
    "medium": {"Retail": 1.0, "OfficeRetail": 1.0, "Medical": 0.9, "OfficeMedical": 0.9, "Office": 0.8, "Flex": 0.7, "Industrial": 0.6},
    "low": {},  # visibility earns nothing: every space use fits equally
}


def visibility_fit(space_use: str | None, need: Need) -> float:
    """How well a listing's space use (CoStar) serves the business's visibility need, 0-1 (unknown 0.5 or 1.0)."""
    if need == "low":
        return 1.0
    return SPACE_FIT[need].get(space_use or "", 0.5)


def preferred_space_uses(need: Need) -> list[str]:
    return [u for u, v in SPACE_FIT[need].items() if v >= 0.8] if need != "low" else ["Office", "OfficeMedical", "Medical", "Flex", "Industrial", "Retail"]


def tenure_options(site: SiteCandidate, program: SpaceProgram) -> list[TenureOption]:
    a = ASSUMPTIONS
    sqft = site.sqft or program.recommended_sqft[0]
    rent = site.rent_psf
    special = program.special_buildout_usd
    tenant_ti = max(0.0, STANDARD_TI_PSF - TI_ALLOWANCE_PSF) * sqft
    out: list[TenureOption] = []
    carry = a["loan_to_value"] * a["mortgage_rate"] + (1 - a["loan_to_value"]) * a["equity_return"]
    hold = carry + a["property_tax_rate"] + a["insurance_maintenance_rate"] - a["appreciation_rate"]
    if rent:
        lease = rent * sqft + (tenant_ti + special) / a["lease_term_years"]
        out.append(TenureOption(option="lease", annual_cost_usd=round(lease), upfront_usd=round(tenant_ti + special),
                                notes="Asking rent plus your fit-out and special rooms, written off over the lease; "
                                      "the improvements stay with the landlord at the end."))
        value = rent * sqft / a["cap_rate"]
        own = value * hold + (STANDARD_TI_PSF * sqft + special) / a["improvement_life_years"]
        out.append(TenureOption(option="buy", annual_cost_usd=round(own),
                                upfront_usd=round(value * (1 - a["loan_to_value"]) + STANDARD_TI_PSF * sqft + special),
                                notes="Building value from rent at the cap rate; financing, tax, upkeep, less appreciation; "
                                      "improvements kept for their full life."))
    build_cost = a["build_cost_psf"] * sqft + special
    out.append(TenureOption(option="build", annual_cost_usd=round(build_cost * hold),
                            upfront_usd=round(build_cost * (1 - a["loan_to_value"])),
                            notes="Build to suit on land nearby: designed for the program (dock, shielding, ASC) from "
                                  "the start; 12-24 months before opening."))
    return out


def plan(profile: PracticeProfile, ranked: list[RankedSite], sites: dict[str, SiteCandidate], top: int = 10) -> Strategy:
    program = space_program(profile)
    need, why = visibility_need(profile)
    pref = _answer(profile, "tenure") or "not stated"
    rows = []
    for r in [r for r in ranked if r.excluded_reason is None][:top]:
        site = sites.get(r.site_id)
        if site is None:
            continue
        opts = tenure_options(site, program)
        rows.append(SiteTenure(site_id=site.id, address=site.address, sqft=site.sqft, options=opts,
                               cheapest=min(opts, key=lambda o: o.annual_cost_usd).option))
    wins = {o: sum(s.cheapest == o for s in rows) for o in ("lease", "buy", "build")}
    heavy = program.special_buildout_usd >= 400_000
    if heavy:
        rec = ("Your service lines need a heavy, specialized buildout, which stays with the building. Owning or "
               "building to suit usually costs less per year than leasing once that buildout is counted; compare "
               "each listing's three options before signing a lease.")
    elif rows and wins["lease"] >= max(wins["buy"], wins["build"]):
        rec = "Leasing is the cheapest option at most of the top spaces; your buildout is standard enough to leave behind."
    else:
        rec = "Buying is competitive at several top spaces; worth pricing the buildings, not just the suites."
    if "lease only" in pref:
        rec += " You said lease only, so the ranking is unchanged; the comparison is for reference."
    return Strategy(program=program, visibility=need, visibility_reason=why, preferred_space_uses=preferred_space_uses(need),
                    tenure_preference=pref, recommendation=rec, sites=rows)


def caveat(strategy: Strategy) -> str:
    p = strategy.program
    lines = ", ".join(x.replace("_", " ") for x in p.service_lines) or "standard rooms only"
    return (f"Space program ({lines}): {p.recommended_sqft[0]:,}-{p.recommended_sqft[1]:,} sq ft"
            + (f", needs {', '.join(p.requirements[:4])}" if p.requirements else "")
            + f". Visibility need: {strategy.visibility} ({strategy.visibility_reason}). {strategy.recommendation} "
            "Lease/buy/build costs use planning assumptions (cap rate, financing, tax, buildout), not quotes.")
