"""load_profile: validate the intake, pick the stock pack, geocode offices that have an address but no coordinates."""

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

import httpx

from contracts.models import Office, PracticeProfile
from services.agent.tools.packs import pack_for

log = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[3]
CACHE = ROOT / "data" / "cache" / "geocoder"
GEOCODER = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"


async def geocode(address: str, client: httpx.AsyncClient) -> tuple[float, float] | None:
    """Census single-address geocoder (cached in data/cache/geocoder/). None when there is no match."""
    CACHE.mkdir(parents=True, exist_ok=True)
    cached = CACHE / f"{hashlib.sha1(address.lower().encode()).hexdigest()}.json"
    if cached.exists():
        data: dict[str, Any] = json.loads(cached.read_text())
    else:
        params = {"address": address, "benchmark": "Public_AR_Current", "format": "json"}
        resp = await client.get(GEOCODER, params=params, timeout=60)
        log.info("GET %s status=%s bytes=%s", resp.url, resp.status_code, len(resp.content))
        resp.raise_for_status()
        data = resp.json()
        cached.write_text(json.dumps(data))
    matches = data.get("result", {}).get("addressMatches", [])
    if not matches:
        return None
    coords = matches[0]["coordinates"]
    return float(coords["y"]), float(coords["x"])


async def load_profile(profile: PracticeProfile) -> tuple[PracticeProfile, list[str]]:
    """The profile with stock_pack filled in and office coordinates resolved, plus notes for the trace."""
    notes: list[str] = []
    pack = pack_for(profile)
    offices: list[Office] = []
    async with httpx.AsyncClient() as client:
        for office in profile.existing_offices:
            if office.lat is not None and office.lon is not None:
                offices.append(office)
                continue
            point = await geocode(office.address, client) if office.address else None
            if point is None:
                notes.append(f"Could not place {office.name}; it is left out of the cannibalization estimate.")
                offices.append(office)
            else:
                offices.append(office.model_copy(update={"lat": point[0], "lon": point[1]}))
                notes.append(f"Geocoded {office.name} with the Census geocoder.")
    if profile.stock_pack and profile.stock_pack != pack.id:
        notes.append(f"No pack {profile.stock_pack}; using {pack.id}.")
    return profile.model_copy(update={"stock_pack": pack.id, "existing_offices": offices}), notes
