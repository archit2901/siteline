"""site-inspector: checks the top spaces with all three CoStar-kit layers before the report is written.

  CoStar listing  -> listed parking spaces and size
  Vexcel aerial   -> parked cars detected around the building (VEXCEL_API_KEY)
  Matterport 3D   -> scanned floor area, floors and rooms (MATTERPORT_API_KEY + MATTERPORT_API_TOKEN)

A deepagents agent (LangGraph) plans the inspection: it decides which spaces and checks matter for this business and
calls the tools. Python performs every API call, stores every number in a SiteInspection, and renders the findings;
the agent never writes a number. If the agent fails, every top space gets every check (deterministic fallback).
Missing keys or scans make a check "unavailable", never a guess.
"""

import base64
import logging
import os
import re
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from langchain_core.tools import tool

from contracts.models import PracticeProfile, RankedSite, SiteCandidate, SiteInspection
from services.agent import llm

log = logging.getLogger(__name__)
VEXCEL_DETECT = "https://api.vexcelgroup.com/v2/elements/detect"
MATTERPORT_GRAPH = "https://api.matterport.com/api/models/graph"
DETECT_METERS = 120  # square around the building searched for parked cars
INSPECT_TOP = 5
TIGHT = 0.85  # parked cars / listed spaces at capture time above this: parking may be tight at peak
# A 120 m square holds roughly 300 surface spaces (about 30 m² each with aisles, minus the building). Larger listed
# counts are campus lots or garages the square cannot see, so no occupancy is computed for them.
MAX_COMPARABLE_SPACES = 150
TIMEOUT = httpx.Timeout(25.0, connect=10.0)

PROMPT = """You are the site-inspector for Siteline. A business owner is choosing between real commercial spaces in
Houston. Before the report is written, inspect the top-ranked spaces with the tools:
- read_listing(site_id): what the CoStar listing says (size, floor, parking, 3D scan available).
- count_parked_cars(site_id): parked cars detected on the latest Vexcel aerial around the building.
- read_3d_scan(site_id): floor area, floors and rooms from the building's Matterport scan.

Plan first: decide which spaces and checks matter for THIS business (customers who drive need parking; a walk-in
business may care less; a space with a scan should be compared with its listing). Inspect at least the top three
spaces, and every space whose listing has a 3D scan. Call each tool at most once per space. When you are done,
reply with one sentence saying what you checked. Never state numbers in your reply; the tools record them."""


def _vexcel_key() -> str | None:
    return os.environ.get("VEXCEL_API_KEY") or None


def _matterport_auth() -> str | None:
    key, token = os.environ.get("MATTERPORT_API_KEY"), os.environ.get("MATTERPORT_API_TOKEN")
    return base64.b64encode(f"{key}:{token}".encode()).decode() if key and token else None


async def count_parked_cars(client: httpx.AsyncClient, lat: float, lon: float, meters: int = DETECT_METERS) -> int:
    """Parked cars Vexcel detects on the latest aerial in a square around the point."""
    resp = await client.post(VEXCEL_DETECT, headers={"X-API-KEY": _vexcel_key() or ""}, json={
        "wkt": f"POINT({lon} {lat})", "bbox-dimensions": [meters, meters],
        "images": [{"endpoint": "ortho/extract", "request": {"layer": "urban"}}],
        "model-input": {"elements": [{"label": "parked car", "detection-threshold": 0.4}]},
    })
    resp.raise_for_status()
    features = resp.json().get("features") or []
    return sum(1 for f in features if (f.get("properties") or {}).get("label") == "parked car")


async def read_scan(client: httpx.AsyncClient, model_id: str) -> dict[str, Any]:
    """Matterport model name, floors, rooms and indoor area in square feet (when the account exposes estimates)."""
    mid = re.sub(r"[^A-Za-z0-9]", "", model_id)
    headers = {"Authorization": f"Basic {_matterport_auth()}"}
    base = await client.post(MATTERPORT_GRAPH, headers=headers, json={
        "query": f'{{ model(id: "{mid}") {{ name floors {{ id }} rooms {{ id }} }} }}'})
    base.raise_for_status()
    model = (base.json().get("data") or {}).get("model") or {}
    out: dict[str, Any] = {"name": model.get("name"), "floors": len(model.get("floors") or []) or None,
                           "rooms": len(model.get("rooms") or []) or None, "area_sqft": None}
    try:  # property-intelligence estimates are enterprise-only; their absence is not an error
        est = await client.post(MATTERPORT_GRAPH, headers=headers, json={
            "query": f'{{ model(id: "{mid}") {{ dimensionEstimates(units: imperial) {{ areaIndoor area units }} }} }}'})
        dims = ((est.json().get("data") or {}).get("model") or {}).get("dimensionEstimates") or {}
        area = dims.get("areaIndoor") or dims.get("area")
        if area and dims.get("units") == "imperial":
            out["area_sqft"] = round(float(area))
    except Exception:
        pass
    return out


def findings(insp: SiteInspection, site: SiteCandidate) -> list[str]:
    """Plain sentences rendered from the inspection's stored values (the critic verifies their numbers)."""
    out = []
    if insp.parked_cars is not None:
        text = f"Aerial imagery shows {insp.parked_cars:,} parked cars within {insp.radius_m} m of the building (Vexcel)"
        if insp.listed_parking and insp.occupancy is not None:
            text += f", against {insp.listed_parking:,} listed spaces: {insp.occupancy * 100:.0f}% full when the image was taken"
            if insp.occupancy >= TIGHT:
                text += ", so parking may be tight at peak"
        elif insp.listed_parking:
            text += f"; the property lists {insp.listed_parking:,} spaces across a larger lot or garage"
        out.append(text + ".")
    # A building's scan usually covers one part of it (a lobby, a suite, a shell floor), not the listed space, so the
    # findings describe the scan and never claim it measures the listing. Scan names stay out of claim text (they
    # often contain suite numbers the verifier would read as unsupported numbers); the flyer shows the name.
    plural = lambda n, word: f"{n} {word}{'' if n == 1 else 's'}"  # noqa: E731
    if insp.scan_area_sqft is not None:
        text = f"A Matterport scan of part of the building measures {insp.scan_area_sqft:,} sq ft indoors"
        if site.sqft:
            text += f"; the listed space is {site.sqft:,} sq ft"
        out.append(text + ".")
    elif insp.scan_rooms or insp.scan_floors:
        parts = [plural(insp.scan_floors, "floor") if insp.scan_floors else "", plural(insp.scan_rooms, "room") if insp.scan_rooms else ""]
        out.append(f"The building has a Matterport 3D scan covering {' and '.join(p for p in parts if p)}.")
    return out


class Inspector:
    """Runs and records the checks for the top spaces; the agent (or the fallback) only decides what to call."""

    def __init__(self, profile: PracticeProfile, top: list[RankedSite], sites: dict[str, SiteCandidate],
                 emit: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None) -> None:
        self.profile, self.sites, self.emit = profile, sites, emit
        self.order = [r.site_id for r in top]
        self.results: dict[str, SiteInspection] = {sid: SiteInspection() for sid in self.order}
        self.attempted: set[tuple[str, str]] = set()  # (check, site_id): each check runs once per space
        self.client = httpx.AsyncClient(timeout=TIMEOUT)

    def _resolve(self, site_id: str) -> str | None:
        """The model sometimes passes a rank ("1", "#2") or an address instead of the id; map those back."""
        site_id = str(site_id).strip()
        if site_id in self.results:
            return site_id
        rank = site_id.lstrip("#").removeprefix("rank").strip()
        if rank.isdigit() and 1 <= int(rank) <= len(self.order):
            return self.order[int(rank) - 1]
        return next((sid for sid in self.order if self.sites[sid].address.lower() == site_id.lower()), None)

    def _site(self, site_id: str) -> SiteCandidate:
        return self.sites[site_id]

    def _once(self, check: str, site_id: str) -> bool:
        """True the first time a check is asked for this space."""
        if (check, site_id) in self.attempted:
            return False
        self.attempted.add((check, site_id))
        return True

    async def _log(self, tool_name: str, site_id: str, summary: str, ok: bool = True) -> None:
        if self.emit:
            await self.emit("tool_result", {"tool": tool_name, "ok": ok, "summary": f"#{self.order.index(site_id) + 1}: {summary}"})

    async def listing(self, site_id: str) -> str:
        if not self._once("listing", site_id):
            return "Already read."
        site, insp = self._site(site_id), self.results[site_id]
        insp.listed_parking = site.parking_spaces
        if "listing" not in insp.checked:
            insp.checked.append("listing")
        await self._log("read_listing", site_id, site.address)
        return (f"{site.address}: {site.sqft or 'unknown'} sq ft, floor {site.floor if site.floor is not None else 'unknown'}, "
                f"{site.parking_spaces if site.parking_spaces is not None else 'unknown'} parking spaces, "
                f"3D scan: {'yes' if site.matterport_id else 'no'}.")

    async def aerial(self, site_id: str) -> str:
        if not self._once("aerial", site_id):
            return "Already checked."
        site, insp = self._site(site_id), self.results[site_id]
        if not _vexcel_key():
            insp.unavailable.append("aerial: no Vexcel key on the server")
            await self._log("count_parked_cars", site_id, "no Vexcel key", ok=False)
            return "Aerial check unavailable (no Vexcel key)."
        try:
            cars = await count_parked_cars(self.client, site.lat, site.lon)
        except Exception as exc:
            insp.unavailable.append(f"aerial: Vexcel error ({type(exc).__name__})")
            await self._log("count_parked_cars", site_id, "Vexcel request failed", ok=False)
            return "Aerial check failed."
        insp.parked_cars, insp.radius_m, insp.listed_parking = cars, DETECT_METERS // 2, site.parking_spaces
        comparable = site.parking_spaces is not None and 0 < site.parking_spaces <= MAX_COMPARABLE_SPACES
        insp.occupancy = round(cars / site.parking_spaces, 3) if comparable and site.parking_spaces else None
        if "aerial" not in insp.checked:
            insp.checked.append("aerial")
        await self._log("count_parked_cars", site_id, f"{cars} parked cars detected")
        return f"Counted parked cars on the aerial for {site.address}; recorded."

    async def scan(self, site_id: str) -> str:
        if not self._once("3d", site_id):
            return "Already checked."
        site, insp = self._site(site_id), self.results[site_id]
        if not site.matterport_id:
            insp.unavailable.append("3d: no Matterport scan for this building")
            return "No 3D scan for this building."
        if not _matterport_auth():
            insp.unavailable.append("3d: no Matterport key on the server")
            await self._log("read_3d_scan", site_id, "no Matterport key", ok=False)
            return "3D check unavailable (no Matterport key)."
        try:
            scan = await read_scan(self.client, site.matterport_id)
        except Exception as exc:
            insp.unavailable.append(f"3d: Matterport error ({type(exc).__name__})")
            await self._log("read_3d_scan", site_id, "Matterport request failed", ok=False)
            return "3D check failed."
        insp.scan_name, insp.scan_floors, insp.scan_rooms, insp.scan_area_sqft = (
            scan["name"], scan["floors"], scan["rooms"], scan["area_sqft"])
        if "3d" not in insp.checked:
            insp.checked.append("3d")
        await self._log("read_3d_scan", site_id, f"scan read{' (' + scan['name'] + ')' if scan['name'] else ''}")
        return f"Read the 3D scan for {site.address}; recorded."

    def tools(self) -> list[Any]:
        inspector = self
        unknown = "Unknown site_id. Use one of: " + ", ".join(self.order)

        @tool
        async def read_listing(site_id: str) -> str:
            """What the CoStar listing says about a space: size, floor, parking spaces, whether a 3D scan exists."""
            sid = inspector._resolve(site_id)
            return await inspector.listing(sid) if sid else unknown

        @tool
        async def count_parked_cars(site_id: str) -> str:
            """Count parked cars around the building on the latest Vexcel aerial and compare with listed parking."""
            sid = inspector._resolve(site_id)
            return await inspector.aerial(sid) if sid else unknown

        @tool
        async def read_3d_scan(site_id: str) -> str:
            """Read the building's Matterport 3D scan: floor area, floors and rooms."""
            sid = inspector._resolve(site_id)
            return await inspector.scan(sid) if sid else unknown

        return [read_listing, count_parked_cars, read_3d_scan]

    async def fallback(self) -> None:
        """Every check not yet made, for every top space in rank order (used when the planning agent is unavailable
        or stops early). Checks the agent already made are skipped."""
        for sid in self.order:
            await self.listing(sid)
            await self.aerial(sid)
            if self.sites[sid].matterport_id:
                await self.scan(sid)

    def finish(self) -> dict[str, SiteInspection]:
        for sid, insp in self.results.items():
            insp.findings = findings(insp, self.sites[sid])
            insp.unavailable = list(dict.fromkeys(insp.unavailable))
        return self.results


async def inspect(profile: PracticeProfile, ranked: list[RankedSite], sites: dict[str, SiteCandidate],
                  meter: llm.UsageMeter | None = None,
                  emit: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None,
                  use_agent: bool = True) -> tuple[dict[str, SiteInspection], list[str]]:
    """Inspections for the top spaces (best first) and notes about how they were planned."""
    from services.agent.critic import best_per_building  # one space per building, like the report

    top = best_per_building([r for r in ranked if r.excluded_reason is None], sites)[:INSPECT_TOP]
    insp = Inspector(profile, top, sites, emit)
    notes: list[str] = []
    try:
        if not use_agent:
            raise LookupError("agent disabled")
        from deepagents import create_deep_agent
        from langchain_core.messages import HumanMessage

        agent = create_deep_agent(model=llm.get_chat_model("orchestrator", meter), tools=insp.tools(),
                                  system_prompt=PROMPT, name="site-inspector")
        listing = "\n".join(f"- site_id={r.site_id} (rank {i})" for i, r in enumerate(top, start=1))
        business = profile.business_type or profile.specialty.replace("_", " ")
        await agent.ainvoke({"messages": [HumanMessage(
            f"Business: {business}. Customers: {profile.customers or 'not stated'}. Needs "
            f"{profile.constraints.min_parking_per_1000sf} parking spaces per 1,000 sq ft.\n\nTop spaces:\n{listing}")]},
            {"recursion_limit": 40})
        if not any(i.checked for i in insp.results.values()):
            raise RuntimeError("the agent made no checks")
        notes.append("site-inspector planned the checks")
    except Exception as exc:  # the report must not depend on the planner
        log.info("site-inspector fallback: %s", exc)
        notes.append(f"site-inspector ran every check ({type(exc).__name__})" if use_agent else "site-inspector ran every check")
        await insp.fallback()
    finally:
        await insp.client.aclose()
    return insp.finish(), notes
