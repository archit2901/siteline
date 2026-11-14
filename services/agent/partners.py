"""Partner scout: the schools a practice covers and the hospitals/ASCs where its surgeons operate, from the intake.

No model call. The intake names them in plain words ("Klein Cain and Klein Oak football", "Texas Orthopedic
Hospital"); we match those words against official point layers (Texas Education Agency high schools and City of
Houston hospitals, both served by City of Houston GIS) and hand the geo service a distance_to_nearest FeatureSpec
filtered to the exact matched names, the same way the competitor scout hands it OpenStreetMap competitors.
A partner is something the practice is near ON PURPOSE (closer is better), never a competitor.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

import httpx

from contracts.models import DatasetCandidate, FeatureSpec, Factor, PracticeProfile
from services.agent.scout import ScoutResult
from services.agent.tools.packs import covers_teams, is_orthopedic

log = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "data" / "cache"
SCHOOLS = "https://services.arcgis.com/NummVBqZSIJKUeVR/arcgis/rest/services/COH_Schools_view/FeatureServer/0"
HOSPITALS = "https://services.arcgis.com/NummVBqZSIJKUeVR/arcgis/rest/services/COH_Hospitals_view/FeatureServer/10"
# Words that name a kind of place, not which one: dropped before matching so "Klein Cain High School" = "KLEIN CAIN H S".
SCHOOL_WORDS = r"\b(H S|HS|HIGH SCHOOL|HIGH|SENIOR|SCHOOL|SR|ACADEMY)\b"
HOSPITAL_WORDS = r"\b(HOSPITAL|HOSP|MEDICAL CENTER|MED CTR|CENTER|THE|AND|OF)\b"


def _answer(profile: PracticeProfile, qid: str) -> str:
    m = re.search(rf"^\[{qid}\][^:]*: (.+)$", profile.free_text or "", re.M)
    return m.group(1) if m else ""


def _norm(text: str, drop: str) -> str:
    text = re.sub(r"[^A-Z0-9 ]", " ", text.upper().replace("'", "").replace(".", ""))
    return re.sub(r"\s+", " ", re.sub(drop, " ", text)).strip()


def match_names(said: str, names: list[str], drop: str) -> list[str]:
    """The official names the owner's words refer to. Longest names match first and their words are used up, so
    'Klein Cain and Klein Oak' finds KLEIN CAIN H S and KLEIN OAK H S but not KLEIN H S."""
    text = f" {_norm(said, drop)} "
    cores = sorted({(_norm(n, drop), n) for n in names if _norm(n, drop)}, key=lambda c: -len(c[0]))
    found: list[str] = []
    for core, name in cores:
        if len(core) < 4:  # too short to tell apart ("ST", "AIM")
            continue
        hit = f" {core} "
        if hit in text:
            found.append(name)
            text = text.replace(hit, " | ")
    return found


def _layer_names(url: str, cache_name: str, field: str, where: str = "1=1") -> list[str]:
    """Names in an ArcGIS point layer (cached in data/cache/; the high school cache is shared with build_sector_stock)."""
    path = CACHE / cache_name
    if not path.is_file():
        try:
            resp = httpx.get(f"{url}/query", timeout=60, headers={"User-Agent": "Siteline/0.1"}, params={
                "where": where, "outFields": field, "returnGeometry": "true", "outSR": "4326", "f": "json",
                "resultRecordCount": "4000"})
            resp.raise_for_status()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(resp.text)
        except httpx.HTTPError as e:
            log.warning("partner layer %s unavailable: %s", url, e)
            return []
    return [f["attributes"][field] for f in json.loads(path.read_text()).get("features", [])
            if f.get("attributes", {}).get(field)]


def high_school_names() -> list[str]:
    return _layer_names(SCHOOLS, "coh_high_schools.json", "School_Name", "Grade_Range LIKE '%12'")


def hospital_names() -> list[str]:
    return _layer_names(HOSPITALS, "coh_hospitals.json", "NAME")


def _target(factors: list[Factor], pattern: str) -> Factor | None:
    return next((f for f in factors if f.feature_id is None and re.search(pattern, f"{f.id} {f.factor} {f.proxy}", re.I)), None)


def _spec(fid: str, label: str, url: str, column: str, names: list[str], source: str) -> FeatureSpec:
    return FeatureSpec(id=fid, label=label, source_id=source, source_url=url, format="arcgis",  # type: ignore[arg-type]
                       geo_level="point", geo_key="_lat,_lon", filter_column=column, filter_values=names,
                       agg="distance_to_nearest", direction=-1, unit="km", layer="agent_found",
                       provenance_url=url)  # type: ignore[arg-type]


def partner_scouts(profile: PracticeProfile, factors: list[Factor], run_tag: str,
                   schools: list[str] | None = None, hospitals: list[str] | None = None,
                   ) -> tuple[list[Factor], list[ScoutResult], list[str]]:
    """(factors with partner factors attached or added, scout results, notes). Orthopedic practices only."""
    if not is_orthopedic(profile):
        return factors, [], []
    results, notes = [], []
    teams = _answer(profile, "team_coverage")
    if covers_teams(profile):
        matched = match_names(teams, schools if schools is not None else high_school_names(), SCHOOL_WORDS)
        if matched:
            target = _target(factors, r"(cover|your|partner|sideline).*(school|team)|(school|team).*(cover|your|partner)")
            if target is None:
                target = Factor(id="covered_schools_drive", factor="Short drive to the schools you cover",
                                why="Your sideline physicians and athletic trainers send those schools' injured athletes "
                                    "to your Saturday and Monday injury clinics; a long drive loses them to a closer group.",
                                proxy="Distance to the nearest school you cover (Texas Education Agency campus locations)",
                                direction=-1, priority="high")
                factors = [*factors, target]
            spec = _spec(f"partner_schools_{run_tag}", "Distance to the nearest school you cover", SCHOOLS, "School_Name",
                         matched, "tea.coh_schools")
            cand = DatasetCandidate(id="cand_partner_schools", factor_id=target.id, url=SCHOOLS,  # type: ignore[arg-type]
                                    title=f"Schools you cover: {', '.join(m.title() for m in matched)}",
                                    publisher="Texas Education Agency (City of Houston GIS)", format="arcgis",
                                    geo_level="point", why="Campus locations of the schools named in your intake.",
                                    status="valid")
            results.append(ScoutResult(target.id, cand, 1, spec=spec))
            notes.append(f"Matched the schools you cover to {', '.join(m.title() for m in matched)}.")
        else:
            notes.append(f"Could not match the schools you cover ({teams[:80]}) to a Houston-area high school; "
                         "add their names as they appear on the district site.")
    said = _answer(profile, "surgery_site") or _answer(profile, "hospital")
    if said:
        matched = match_names(said, hospitals if hospitals is not None else hospital_names(), HOSPITAL_WORDS)
        if matched:
            target = _target(factors, r"hospital|asc|surgery cent|operat")
            if target is None:
                target = Factor(id="surgery_site_drive", factor="Short drive to where your surgeons operate",
                                why="Surgeons split days between clinic and OR; post-op patients return to the clinic.",
                                proxy="Distance to the nearest named hospital or surgery center", direction=-1,
                                priority="high")
                factors = [*factors, target]
            spec = _spec(f"partner_hospitals_{run_tag}", "Distance to the nearest hospital or ASC where you operate",
                         HOSPITALS, "NAME", matched, "coh.hospitals")
            cand = DatasetCandidate(id="cand_partner_hospitals", factor_id=target.id, url=HOSPITALS,  # type: ignore[arg-type]
                                    title=f"Where you operate: {', '.join(m.title() for m in matched)}",
                                    publisher="City of Houston GIS (hospital layer; names may be dated)", format="arcgis",
                                    geo_level="point", why="Locations of the hospitals named in your intake.",
                                    status="valid")
            results.append(ScoutResult(target.id, cand, 1, spec=spec))
            notes.append(f"Matched where you operate to {', '.join(m.title() for m in matched)}.")
        else:
            notes.append("Could not match where your surgeons operate to a Houston hospital in the City layer; "
                         "the hospital factor uses a dataset scout instead.")
    return factors, results, notes
