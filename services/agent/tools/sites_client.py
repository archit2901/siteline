"""SitesClient: real spaces in the best zones. Contract shapes in and out.

MockSites  -> pseudo-sites at zone centroids (source="fixture", address "Zone <h3>"); no data needed
LocalSites -> CoStar lease spaces joined to properties from data/raw/ (never committed)
HttpSites  -> sites-service at SITES_URL
"""

import math
import os
import re
from functools import cached_property
from pathlib import Path
from typing import Any, Protocol

import h3
import httpx
from pydantic import TypeAdapter

from contracts.models import Constraints, SiteCandidate, SiteCheckResult, SiteSearchRequest

ROOT = Path(__file__).resolve().parents[3]
RAW = ROOT / "data" / "raw"
H3_RES = 8
SEARCH_RADIUS_KM = 2.0  # a zone is searched only if a clinic-fit listing is within 2 km of its centroid
CLINIC_SPACE_USES = {"Office", "OfficeMedical", "Medical", "Retail"}
CLINIC_SQFT = (2000, 10000)
COWORKING = "PropertyCoworking"

UNITS = {"First": 1, "Second": 2, "Third": 3, "Fourth": 4, "Fifth": 5, "Sixth": 6, "Seventh": 7, "Eighth": 8,
         "Ninth": 9, "Tenth": 10, "Eleventh": 11, "Twelfth": 12, "Thirteenth": 13, "Fourteenth": 14,
         "Fifteenth": 15, "Sixteenth": 16, "Seventeenth": 17, "Eighteenth": 18, "Nineteenth": 19}
TENS = {"Twent": 20, "Thirt": 30, "Fort": 40, "Fift": 50, "Sixt": 60, "Sevent": 70, "Eight": 80, "Ninet": 90}
SPECIAL_FLOORS: dict[str, int | None] = {"Basement": 0, "LowerLevel": 0, "Ground": 1, "Lobby": 1, "Penthouse": None}


def parse_floor(word: str | None) -> int | None:
    """CoStar floor words -> int: "FirstFloor" 1, "FiftyEighthFloor" 58, "TwentiethFloor" 20, "Ground" 1."""
    if not word:
        return None
    if word in SPECIAL_FLOORS:
        return SPECIAL_FLOORS[word]
    parts = re.findall(r"[A-Z][a-z]*", word.removesuffix("Floor"))
    if len(parts) == 1 and parts[0] in UNITS:
        return UNITS[parts[0]]
    if len(parts) == 1 and parts[0].endswith("ieth"):  # Twentieth, Thirtieth, ...
        return TENS.get(parts[0].removesuffix("ieth"))
    if len(parts) == 2 and parts[1] in UNITS and parts[0].endswith("y"):  # Fifty + Eighth
        tens = TENS.get(parts[0].removesuffix("y"))
        return tens + UNITS[parts[1]] if tens else None
    return None


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(a))


def fits(site: SiteCandidate, sqft_range: tuple[int, int], cons: Constraints) -> bool:
    """Space size range overlaps the practice's range; ground floor and max rent only judged when known."""
    low, high = sqft_range
    if high < cons.min_sqft or low > cons.max_sqft:
        return False
    if cons.ground_floor and site.floor is not None and site.floor != 1:
        return False
    return not (cons.max_rent_psf is not None and site.rent_psf is not None and site.rent_psf > cons.max_rent_psf)


class SitesClient(Protocol):
    async def search(self, request: SiteSearchRequest) -> list[SiteCandidate]: ...
    async def checks(self, site_id: str) -> SiteCheckResult: ...


class MockSites:
    """Pseudo-sites at the centroids of the requested zones (P1 stand-in until real spaces are wired)."""

    async def search(self, request: SiteSearchRequest) -> list[SiteCandidate]:
        size = max(request.constraints.min_sqft, min(request.constraints.max_sqft, 3000))
        sites = []
        for zone in request.zones[: request.limit]:
            lat, lon = h3.cell_to_latlng(zone)
            sites.append(SiteCandidate(id=f"zone_{zone}", address=f"Zone {zone}", lat=lat, lon=lon, h3=zone, sqft=size,
                                       floor=1, source="fixture"))
        return sites

    async def checks(self, site_id: str) -> SiteCheckResult:
        return SiteCheckResult(site_id=site_id)


class LocalSites:
    """CoStar lease spaces (one row per active space) joined to their properties."""

    def __init__(self, raw_dir: Path = RAW) -> None:
        self.raw_dir = raw_dir
        for name in ("houston_costar_lease_spaces.parquet", "houston_costar_properties.parquet"):
            if not (raw_dir / name).exists():
                raise FileNotFoundError(f"{raw_dir / name} missing: AGENT_BACKEND=local needs the CoStar files")

    def _read(self, name: str) -> list[dict[str, Any]]:
        import pyarrow.parquet as pq

        return pq.read_table(self.raw_dir / name).to_pylist()

    @cached_property
    def properties(self) -> dict[str, dict[str, Any]]:
        return {str(p["property_id"]): p for p in self._read("houston_costar_properties.parquet")}

    @cached_property
    def spaces(self) -> list[dict[str, Any]]:
        """Active, non-coworking spaces, de-duplicated on (property_id, suite, floor) and (address, suite, floor)."""
        rows = sorted(self._read("houston_costar_lease_spaces.parquet"), key=lambda r: r["space_id"])
        seen: set[tuple[Any, ...]] = set()
        out = []
        for r in rows:
            if r["listing_type"] == COWORKING:
                continue
            prop = self.properties[str(r["property_id"])]
            keys = [(r["property_id"], r["suite"], r["floor"]),
                    ((prop["address"] or "").strip().lower(), r["suite"], r["floor"])]
            if any(k in seen for k in keys):
                continue
            seen.update(keys)
            out.append(r)
        return out

    @cached_property
    def clinic_fit(self) -> list[tuple[SiteCandidate, tuple[int, int]]]:
        """Clinic-fit spaces (Office / OfficeMedical / Medical / Retail, size range overlapping 2,000-10,000 sq ft)."""
        out = []
        for r in self.spaces:
            low, high = int(r["sqft_min"]), int(r["sqft_max"])
            if r["space_use"] in CLINIC_SPACE_USES and low <= CLINIC_SQFT[1] and high >= CLINIC_SQFT[0]:
                out.append((self._candidate(r), (low, high)))
        return out

    def _candidate(self, r: dict[str, Any]) -> SiteCandidate:
        prop = self.properties[str(r["property_id"])]
        lat, lon = r["lat"], r["lon"]
        mp = [m for m in (prop["matterport_model_ids"] or "").split(";") if m]
        building_class = prop["building_class"] if prop["building_class"] in ("A", "B", "C") else None
        return SiteCandidate(
            id=str(r["space_id"]), address=r["address"] or prop["address"], lat=lat, lon=lon,
            h3=h3.latlng_to_cell(lat, lon, H3_RES), submarket=f"CoStar submarket {prop['submarket_id']}",
            sqft=int(r["sqft_max"]), rent_psf=r["rent_low_usd_sf_yr"], floor=parse_floor(r["floor"]),
            building_class=building_class, available=not r["is_lease_pending"],
            parking_spaces=None if prop["parking_spaces"] is None else int(prop["parking_spaces"]),
            matterport_id=mp[0] if mp else None, source="costar",
            checks={
                "property_id": str(r["property_id"]), "building_name": r["building_name"] or prop["building_name"],
                "suite": r["suite"], "floor_label": r["floor"], "space_use": r["space_use"],
                "listing_type": r["listing_type"], "sqft_min": int(r["sqft_min"]),
                "rent_high_psf": r["rent_high_usd_sf_yr"], "service_type": r["service_type"],
                "on_market_since": (r["on_market_since"] or "")[:10] or None,
                "parking_ratio_per_1000sf": prop["parking_ratio_per_1000sf"],
                "n_matterport_models": prop["n_matterport_models"],
            },
        )

    def zones_with_listings(self, zones: list[str]) -> dict[str, bool]:
        """Which zones have a clinic-fit listing within 2 km ("strong zones with no listings yet" otherwise)."""
        return {z: bool(self._near(z)) for z in zones}

    def _near(self, zone: str) -> list[tuple[float, SiteCandidate, tuple[int, int]]]:
        lat, lon = h3.cell_to_latlng(zone)
        near = [(haversine_km(lat, lon, s.lat, s.lon), s, rng) for s, rng in self.clinic_fit]
        return sorted((x for x in near if x[0] <= SEARCH_RADIUS_KM), key=lambda x: (x[0], x[1].id))

    async def search(self, request: SiteSearchRequest) -> list[SiteCandidate]:
        """Spaces near the zones (best zone first) that fit the practice's constraints, up to request.limit."""
        cons = request.constraints
        out: list[SiteCandidate] = []
        seen: set[str] = set()
        for zone in request.zones:
            for dist, site, sqft_range in self._near(zone):
                if site.id in seen or not fits(site, sqft_range, cons):
                    continue
                seen.add(site.id)
                sqft = min(site.sqft or cons.max_sqft, cons.max_sqft)  # divisible spaces are offered at the max size
                out.append(site.model_copy(update={
                    "sqft": sqft, "checks": {**site.checks, "zone_h3": zone, "distance_to_zone_km": round(dist, 2)},
                }))
                if len(out) >= request.limit:
                    return out
        return out

    async def checks(self, site_id: str) -> SiteCheckResult:
        for site, _ in self.clinic_fit:
            if site.id == site_id:
                return SiteCheckResult(
                    site_id=site_id, parking_estimate=site.parking_spaces,
                    parking_note="CoStar property parking count" if site.parking_spaces is not None else None,
                    ground_floor=None if site.floor is None else site.floor == 1, matterport_id=site.matterport_id,
                )
        raise KeyError(f"no clinic-fit space {site_id!r}")


class HttpSites:
    def __init__(self, base_url: str | None = None, timeout: float = 60.0) -> None:
        self.client = httpx.AsyncClient(base_url=base_url or os.environ.get("SITES_URL", "http://localhost:8002"),
                                        timeout=timeout)

    async def search(self, request: SiteSearchRequest) -> list[SiteCandidate]:
        resp = await self.client.post("/sites/search", json=request.model_dump(mode="json"))
        resp.raise_for_status()
        return TypeAdapter(list[SiteCandidate]).validate_python(resp.json())

    async def checks(self, site_id: str) -> SiteCheckResult:
        resp = await self.client.post(f"/sites/{site_id}/checks")
        resp.raise_for_status()
        return SiteCheckResult.model_validate(resp.json())
