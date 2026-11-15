"""Decision review: hard requirements and the six perspectives that challenge the leading sites (review.json).

Python only, no model call: every finding is rendered from a stored number (ranked.json, sites.json, grid feature
values at the site's cell, the partner layers) so the critic's rule holds here too. A perspective that does not
apply says why. Requirements are binding: a confirmed failure makes a site ineligible, an unknown keeps it
provisional with the exact check that would settle it, so no site is presented as confirmed on an assumption.
"""

from __future__ import annotations

from typing import Literal

import h3
import numpy as np
from pydantic import BaseModel, Field

from contracts.models import Factor, PracticeProfile, RankedSite, SiteCandidate, StockPack
from services.agent.tools import geomath
from services.agent.tools.geo_client import GeoClient
from services.agent.tools.packs import is_orthopedic, orthopedic_focus

Status = Literal["supports", "risk", "open", "not_applicable"]
TRANSFER_RISK = 0.30  # more than 30% of what a site captures coming from your own offices is mostly a move, not growth
PARTNER_MINUTES = 25  # beyond this a covered school or the OR is a real drive for staff and patients
STABLE = 0.6  # in the top 10 in at least 60% of the 200 alternative weightings


class Requirement(BaseModel):
    name: str
    status: Literal["met", "failed", "unknown"]
    detail: str
    verify: str = ""  # what settles it before a lease (unknown or failed)


class Note(BaseModel):
    perspective: str
    question: str
    finding: str
    status: Status
    evidence: list[str] = Field(default_factory=list)  # "feature:<id>@<h3>", "model:net_new@<site>", "site:<field>@<site>"


class SiteReview(BaseModel):
    site_id: str
    address: str
    rank: int
    status: Literal["confirmed", "provisional", "ineligible"]
    requirements: list[Requirement]
    notes: list[Note]


class Review(BaseModel):
    objective: str
    baseline: str
    decision: str
    why: str
    alternative: str
    what_could_change: list[str]
    sites: list[SiteReview]


def _fmt(x: float) -> str:
    return f"{x:,.0f}"


def requirements(site: SiteCandidate, profile: PracticeProfile, pack: StockPack) -> list[Requirement]:
    c, rules = profile.constraints, pack.site_rules
    out: list[Requirement] = []
    if site.sqft is None:
        out.append(Requirement(name="Size", status="unknown", detail="The listing does not give the suite size.",
                               verify="Get the rentable square feet from the landlord."))
    else:
        ok = c.min_sqft <= site.sqft <= c.max_sqft
        out.append(Requirement(name="Size", status="met" if ok else "failed",
                               detail=f"{_fmt(site.sqft)} sq ft against your {_fmt(c.min_sqft)}-{_fmt(c.max_sqft)} sq ft."))
    if c.max_rent_psf:
        if site.rent_psf is None:
            out.append(Requirement(name="Rent", status="unknown", detail="Asking rent is not listed.",
                                   verify="Ask for base rent and operating expenses per sq ft."))
        else:
            ok = site.rent_psf <= c.max_rent_psf
            out.append(Requirement(name="Rent", status="met" if ok else "failed",
                                   detail=f"${site.rent_psf:.2f}/sq ft/yr asking against your ${c.max_rent_psf:.0f} ceiling "
                                          "(base rent; operating expenses and buildout are extra)."))
    if site.available is False:
        out.append(Requirement(name="Available", status="failed", detail="The listing says the space is not available."))
    elif site.available is None:
        out.append(Requirement(name="Available", status="unknown", detail="The listing does not say it is available now.",
                               verify="Confirm availability and the earliest possession date."))
    if c.ground_floor or rules.get("ground_floor"):
        if site.floor is None:
            out.append(Requirement(name="Ground floor", status="unknown", detail="The listing does not give the floor.",
                                   verify="Confirm a ground-floor suite" + (": X-ray equipment weight, patients on crutches and walkers."
                                                                            if profile.specialty == "orthopedics" else ".")))
        else:
            ok = site.floor <= 1
            out.append(Requirement(name="Ground floor", status="met" if ok else "failed", detail=f"Suite on floor {site.floor}."))
    need = max(c.min_parking_per_1000sf or 0, float(rules.get("min_parking_per_1000sf") or 0))
    if need:
        if site.parking_spaces is None or not site.sqft:
            out.append(Requirement(name="Parking", status="unknown", detail=f"Need about {need:g} spaces per 1,000 sq ft; not listed.",
                                   verify="Count spaces you can use at the 9-11 am peak, including shared lots."))
        else:
            ratio = site.parking_spaces / (site.sqft / 1000)
            if site.parking_estimated:
                out.append(Requirement(name="Parking", status="unknown",
                                       detail=f"About {ratio:.1f} per 1,000 sq ft, estimated from the aerial image (need {need:g}).",
                                       verify="Confirm how many spaces the lease grants and how full the lot is at the 9-11 am peak."))
            else:
                out.append(Requirement(name="Parking", status="met" if ratio >= need else "failed",
                                       detail=f"{ratio:.1f} spaces per 1,000 sq ft (need {need:g})."))
    rooms = {r.lower() for r in [*c.special_rooms, *rules.get("special_rooms", [])]}
    if "x-ray" in rooms:
        out.append(Requirement(name="X-ray room", status="unknown", detail="Listings never state whether a room can be shielded.",
                               verify="Physicist shielding plan for one room, landlord consent, Texas DSHS X-ray registration."))
    if orthopedic_focus(profile) or profile.specialty == "orthopedics":
        out.append(Requirement(name="Step-free arrival", status="unknown",
                               detail="Drop-off to check-in path is not in any listing.",
                               verify="Walk it: covered drop-off at the door, step-free, short corridor, automatic doors, "
                                      "no long elevator wait for patients on crutches and walkers."))
    return out


def status_of(reqs: list[Requirement]) -> Literal["confirmed", "provisional", "ineligible"]:
    if any(r.status == "failed" for r in reqs):
        return "ineligible"
    return "provisional" if any(r.status == "unknown" for r in reqs) else "confirmed"


async def _value(geo: GeoClient, feature: str, cell: str) -> float | None:
    try:
        return (await geo.feature_value(feature, cell)).value
    except Exception:  # noqa: BLE001 - a feature this run did not register is simply missing
        return None


def _minutes(km: float) -> float:
    return km * geomath.DETOUR / geomath.SPEED_KMH * 60


async def notes_for(site: SiteCandidate, r: RankedSite, profile: PracticeProfile, pack: StockPack, factors: list[Factor],
                    geo: GeoClient, label: str, reqs: list[Requirement]) -> list[Note]:
    cell = site.h3 or h3.latlng_to_cell(site.lat, site.lon, 8)
    variant = pack.site_rules.get("variant")
    notes: list[Note] = []

    # Practice owner: growth, not a move
    transfer = r.cannibalized_patients / r.captured_patients if r.captured_patients else 0.0
    owner = (f"About {_fmt(r.net_new_patients)} net-new {label} a year; {transfer:.0%} of what it captures would move "
             "from your own offices.")
    per, market = _per_physician(r, profile, pack, geo)
    if per is not None:
        owner += f" That is about {_fmt(per)} a year per surgeon"
        owner += f" (Houston average about {_fmt(market)} per orthopedic-equivalent clinician)." if market else "."
    if r.stability is not None:
        owner += f" Stays in the top 10 in {r.stability:.0%} of 200 alternative weightings."
    notes.append(Note(perspective="Practice owner", question="Does it add patients across the network, or mostly move them?",
                      finding=owner, status="risk" if transfer > TRANSFER_RISK or (r.stability or 1) < STABLE else "supports",
                      evidence=[f"model:net_new@{r.site_id}", f"model:cannibalized@{r.site_id}"]))

    # Patient and caregiver: who comes, and how they arrive
    if variant in ("sports", "pediatric"):
        hs = await _value(geo, "hs_enrollment_within_10km", cell)
        big = await _value(geo, "n_large_high_schools_within_10km", cell)
        finding = (f"{_fmt(hs)} high school students within 10 km in {big:.0f} large (UIL 5A/6A-size) schools; "
                   "teens are driven by parents after school and on Saturday mornings." if hs is not None and big is not None
                   else "High school enrollment near this site is not available.")
        refs = [f"feature:hs_enrollment_within_10km@{cell}", f"feature:n_large_high_schools_within_10km@{cell}"]
    else:
        older = await _value(geo, "pop_65p_within_10km", cell)
        finding = (f"{_fmt(older)} adults 65+ within 10 km; post-op patients return 3-4 times in the first year, on walkers."
                   if older is not None else "Older-adult population near this site is not available.")
        refs = [f"feature:pop_65p_within_10km@{cell}"]
    route = next((q for q in reqs if q.name == "Step-free arrival"), None)
    notes.append(Note(perspective="Patient and caregiver",
                      question="Can patients make repeat trips and get from the car to check-in on crutches?",
                      finding=finding + (" The drop-off-to-check-in path is unverified." if route else ""),
                      status="open" if route else "supports", evidence=refs))

    # Clinicians and operations: space and the binding bottleneck
    rooms = [q for q in reqs if q.name in ("Size", "X-ray room")]
    size = next((q for q in rooms if q.name == "Size"), None)
    ops = (size.detail if size else "Suite size unknown.") + " About 3 exam rooms per provider and one X-ray room per 2-3 providers."
    notes.append(Note(perspective="Clinicians and operations",
                      question="Can the space and staffing absorb this many new patients?",
                      finding=ops + " The X-ray room needs a shielding plan before signing.",
                      status="risk" if size and size.status == "failed" else "open",
                      evidence=[f"site:sqft@{r.site_id}"]))

    # Payers, referrers and partners
    pcp = await _value(geo, "n_primary_care_within_5km", cell)
    parts, refs = [], [f"feature:n_primary_care_within_5km@{cell}"]
    if pcp is not None:
        parts.append(f"{pcp:.0f} primary care clinicians within 5 km (referral base)")
    far, served = False, ""
    for f in factors:
        if f.feature_id and f.feature_id.startswith(("partner_schools_", "partner_hospitals_")):
            km = await _value(geo, f.feature_id, cell)
            if km is None:
                continue
            schools = "schools" in f.feature_id
            what = "the nearest school you cover" if schools else "where your surgeons operate"
            parts.append(f"about {_minutes(km):.0f} min by car to {what} ({km:.1f} km straight line)")
            refs.append(f"feature:{f.feature_id}@{cell}")
            far = far or _minutes(km) > PARTNER_MINUTES
            if schools:  # an office already beside the schools serves them; a new site there mostly moves patients
                for o in profile.existing_offices:
                    if o.lat is None or o.lon is None:
                        continue
                    ocell = h3.latlng_to_cell(o.lat, o.lon, 8)
                    okm = await _value(geo, f.feature_id, ocell)
                    if okm is not None and okm < km:
                        served = (f" Your {o.name} office is already about {_minutes(okm):.0f} min from those schools, so "
                                  "their athletes are served; this site adds patients elsewhere instead of moving them.")
                        refs.append(f"feature:{f.feature_id}@{ocell}")
                        break
    ortho = await _value(geo, "n_orthopedics_within_10km", cell)
    if ortho is not None:
        parts.append(f"{ortho:.0f} orthopedic clinicians within 10 km (competitors unless a referral relationship is documented)")
        refs.append(f"feature:n_orthopedics_within_10km@{cell}")
    notes.append(Note(perspective="Payers, referrers and partners",
                      question="Who sends patients here, and is the nearby provider a competitor or a partner?",
                      finding=(("; ".join(parts) + ".") if parts else "No referral or partner data at this site.") + served,
                      status="risk" if far and not served else "supports", evidence=refs))

    # Property and implementation
    unknown = [q.name.lower() for q in reqs if q.status == "unknown"]
    failed = [q.name.lower() for q in reqs if q.status == "failed"]
    notes.append(Note(perspective="Property and implementation", question="Can this exact suite and lease support the plan?",
                      finding=(f"Fails: {', '.join(failed)}. " if failed else "")
                      + (f"To verify before a lease: {', '.join(unknown)}." if unknown else "Every listed requirement is met."),
                      status="risk" if failed else "open" if unknown else "supports",
                      evidence=[f"site:sqft@{r.site_id}", f"site:rent_psf@{r.site_id}"]))

    # Public access and planning
    if {"medicaid", "chip"} & set(profile.payers_accepted):
        share = await _value(geo, "share_any_medicaid", cell)
        notes.append(Note(perspective="Public access", question="Does it bring care closer to people with fewer options?",
                          finding=(f"{share:.0%} of residents here have Medicaid, which you accept." if share is not None
                                   else "Medicaid share here is not available."),
                          status="supports", evidence=[f"feature:share_any_medicaid@{cell}"]))
    else:
        notes.append(Note(perspective="Public access", question="Does it bring care closer to people with fewer options?",
                          finding="Not material to this decision: the practice does not take Medicaid or CHIP, so the "
                                  "site choice does not change public-program access.", status="not_applicable"))
    return notes


def _per_physician(r: RankedSite, profile: PracticeProfile, pack: StockPack, geo: GeoClient) -> tuple[float | None, float | None]:
    """Net-new per planned surgeon, and the market's patients per unit of competitor supply (same units), so the
    headline number can be checked against Houston as a whole. Healthcare packs with a clinician supply only."""
    if not is_orthopedic(profile):
        return None, None
    per = r.net_new_patients / geomath.site_providers(profile)
    grid = getattr(geo, "grid", None)
    column = geomath.competitor_column(pack, None)
    if grid is None or column is None:
        return per, None
    geomath.ensure_supply(grid, pack)
    supply = float(np.nansum(grid.columns.get(column, np.zeros(1))))
    return per, (float(geomath.demand(grid, pack, profile).sum()) / supply if supply else None)


async def review(profile: PracticeProfile, pack: StockPack, ranked: list[RankedSite], sites: dict[str, SiteCandidate],
                 factors: list[Factor], geo: GeoClient, label: str, top: int = 5) -> Review:
    eligible = [r for r in ranked if r.excluded_reason is None and r.site_id in sites][:top]
    reviews = []
    for i, r in enumerate(eligible, 1):
        site = sites[r.site_id]
        reqs = requirements(site, profile, pack)
        reviews.append(SiteReview(site_id=r.site_id, address=site.address, rank=i, status=status_of(reqs), requirements=reqs,
                                  notes=await notes_for(site, r, profile, pack, factors, geo, label, reqs)))
    objective = f"Most net-new {label} a year across your offices (new site's gain minus what your offices lose)."
    n = len(profile.existing_offices)
    baseline = (f"Your {'current office' if n == 1 else f'{n} current offices'} with no new site."
                if profile.existing_offices else "No current office: every patient a site captures is new.")
    if not reviews:
        return Review(objective=objective, baseline=baseline, decision="No eligible listed space; use the regional shortlist.",
                      why="", alternative="", what_could_change=[], sites=[])
    by_id = {r.site_id: r for r in eligible}
    viable = [v for v in reviews if v.status != "ineligible"]  # a confirmed failure cannot lead or be the fallback
    if not viable:
        return Review(objective=objective, baseline=baseline, sites=reviews, why="", alternative="", what_could_change=[],
                      decision="No reviewed space meets your requirements; use the regional shortlist and the checks below.")
    lead, runner = viable[0], (viable[1] if len(viable) > 1 else None)
    rl = by_id[lead.site_id]
    status_word = {"confirmed": "Recommended", "provisional": "Provisional pick", "ineligible": "Not eligible"}[lead.status]
    open_items = [q.name.lower() for q in lead.requirements if q.status == "unknown"]
    decision = f"{status_word}: {lead.address}" + (f", pending {', '.join(open_items)}." if open_items else ".")
    why = f"It adds the most net-new {label} ({_fmt(rl.net_new_patients)} a year) after subtracting what your offices would lose."
    alternative, changes = "", []
    if runner:
        rr = by_id[runner.site_id]
        gap = rl.net_new_patients - rr.net_new_patients
        alternative = (f"Strongest alternative: {runner.address}, {_fmt(rr.net_new_patients)} net-new a year "
                       f"({_fmt(abs(gap))} {'fewer' if gap >= 0 else 'more'})")
        risks = [n.perspective.lower() for n in runner.notes if n.status == "risk"]
        lead_risks = [n.perspective.lower() for n in lead.notes if n.status == "risk"]
        if lead_risks and not risks:
            alternative += f"; it has no flagged risk where the leader does ({', '.join(lead_risks)})."
        else:
            alternative += "."
        changes.append(f"If {lead.address} fails any of: {', '.join(open_items) or 'its checks'}, {runner.address} leads.")
        if rl.net_new_patients and gap / rl.net_new_patients < 0.1:
            changes.append("The top two are within 10% on net-new patients: a modest change in demand or a competitor "
                           "opening could swap them.")
    if rl.stability is not None and rl.stability < STABLE:
        changes.append(f"The leader stays in the top 10 in only {rl.stability:.0%} of alternative weightings: the pick depends "
                       "on the approved weights.")
    return Review(objective=objective, baseline=baseline, decision=decision, why=why, alternative=alternative,
                  what_could_change=changes, sites=reviews)
