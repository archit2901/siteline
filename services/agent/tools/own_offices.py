"""The business's own offices as a point layer: how far each neighborhood is from the nearest one (straight line).

A new office close to an existing one mostly moves the same patients between buildings. The Huff model already
subtracts those patients from net-new; this layer lets the zone score count distance from your own offices too.
The points come from the intake (existing offices with coordinates), so nothing is fetched. A FeatureSpec asks for the
layer with an openstreetmap.org link that pins the first office and lists all of them in `offices=lat,lon;lat,lon`,
so the provenance link opens a map of where you already are.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

from contracts.models import Office

OSM = "https://www.openstreetmap.org/"
CAP_KM = 15.0  # past this, a new office no longer shares patients with an existing one


def located(offices: list[Office]) -> list[Office]:
    return [o for o in offices if o.lat is not None and o.lon is not None]


def offices_url(offices: list[Office]) -> str:
    pts = located(offices)
    first = pts[0]
    query = {"mlat": f"{first.lat:.5f}", "mlon": f"{first.lon:.5f}",
             "offices": ";".join(f"{o.lat:.5f},{o.lon:.5f}" for o in pts)}
    return f"{OSM}?{urlencode(query)}"


def points_of(url: str) -> list[tuple[float, float]] | None:
    """The office points a URL lists, only for an openstreetmap.org link with an offices= parameter."""
    u = urlparse(url)
    if f"{u.scheme}://{u.netloc}{u.path}" != OSM:
        return None
    raw = parse_qs(u.query).get("offices", [""])[0]
    out = []
    for part in raw.split(";"):
        try:
            lat, lon = (float(x) for x in part.split(","))
        except ValueError:
            continue
        if -90 <= lat <= 90 and -180 <= lon <= 180:
            out.append((lat, lon))
    return out or None


def rows(points: list[tuple[float, float]]) -> list[dict[str, Any]]:
    return [{"_lat": lat, "_lon": lon, "name": f"your office {i + 1}"} for i, (lat, lon) in enumerate(points)]
