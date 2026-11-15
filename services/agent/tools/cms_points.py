"""Point layers the agent derives from CMS Doctors and Clinicians: where each kind of practice actually is.

The national file (data.cms.gov dataset mj5m-pzi6) lists every Medicare-enrolled clinician with their practice
address and group. data/raw/houston_clinicians.parquet is our geocoded Houston copy. Grouping clinicians by address
turns it into practices: a free-standing PT clinic is an address with physical therapists and no orthopedist or
physiatrist; a primary care group is an address with three or more primary care clinicians; and so on.

A FeatureSpec asks for one of these layers with source_url = CMS_URL + "?layer=<name>", so the layer goes through
the normal ingest, aggregation and validation like any other found dataset, and its provenance is the CMS page.
"""

from __future__ import annotations

import re
from collections import defaultdict
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[3]
CLINICIANS = ROOT / "data" / "raw" / "houston_clinicians.parquet"
CMS_URL = "https://data.cms.gov/provider-data/dataset/mj5m-pzi6"
URGENT = re.compile(r"URGENT CARE|CARENOW|NEXTCARE|MINUTECLINIC|FAST ?MED|PATIENT FIRST|URGENT MEDICAL")
NOT_URGENT = re.compile(r"HOSPITAL MEDICINE|INPATIENT|HOSPITALIST|PATHOLOGY|ANESTHES")
IMAGING = re.compile(r"IMAGING|RADIOLOGY|\bMRI\b|DIAGNOSTIC")
ORTHO_NAME = re.compile(r"ORTHO|SPINE|SPORTS MED|JOINT|HOSPITAL")

LAYERS: dict[str, str] = {
    "freestanding_pt": "Free-standing physical therapy clinics",
    "urgent_care": "Urgent care clinics",
    "imaging_centers": "Outpatient imaging centers",
    "primary_care_groups": "Primary care groups (3+ clinicians at one address)",
    "ortho_offices": "Orthopedic offices",
}


def layer_url(layer: str) -> str:
    return f"{CMS_URL}?layer={layer}"


def layer_of(url: str) -> str | None:
    """The layer a URL names, only for the canonical CMS dataset URL."""
    u = urlparse(url)
    if f"{u.scheme}://{u.netloc}{u.path}" != CMS_URL:
        return None
    layer = parse_qs(u.query).get("layer", [""])[0]
    return layer if layer in LAYERS else None


@lru_cache(maxsize=1)
def _addresses() -> dict[str, list[dict[str, Any]]]:
    import pyarrow.parquet as pq

    cols = ["primary_specialty", "group_practice", "address_id", "specialty_group", "lat", "lon"]
    by: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in pq.read_table(CLINICIANS, columns=cols).to_pylist():
        if r["lat"] is not None and r["lon"] is not None and r["address_id"]:
            by[r["address_id"]].append(r)
    return by


def _groups(rs: list[dict[str, Any]]) -> str:
    return " ".join(sorted({(r["group_practice"] or "").upper() for r in rs}))


def _count(rs: list[dict[str, Any]], group: str) -> int:
    return sum(r["specialty_group"] == group for r in rs)


def _keep(layer: str, rs: list[dict[str, Any]]) -> bool:
    names = _groups(rs)
    if layer == "freestanding_pt":
        return _count(rs, "physical_therapy") > 0 and not (_count(rs, "orthopedics") or _count(rs, "pm_and_r")) \
            and not ORTHO_NAME.search(names)
    if layer == "urgent_care":
        return bool(URGENT.search(names)) and not NOT_URGENT.search(names)
    if layer == "imaging_centers":
        return any("RADIOLOGY" in (r["primary_specialty"] or "") for r in rs) and bool(IMAGING.search(names)) \
            and "HOSPITAL" not in names
    if layer == "primary_care_groups":
        return _count(rs, "primary_care") >= 3
    if layer == "ortho_offices":
        return _count(rs, "orthopedics") > 0
    return False


def rows(layer: str) -> list[dict[str, Any]]:
    """One row per practice address in the layer: _lat, _lon, name, clinicians."""
    if not CLINICIANS.is_file():
        raise ValueError("CMS clinician file missing (data/raw/houston_clinicians.parquet)")
    out = []
    for rs in _addresses().values():
        if _keep(layer, rs):
            out.append({"_lat": rs[0]["lat"], "_lon": rs[0]["lon"], "name": _groups(rs)[:120], "clinicians": len(rs)})
    return out
