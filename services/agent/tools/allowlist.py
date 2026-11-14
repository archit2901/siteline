"""Domains datasets may come from (docs/DESIGN.md "Guardrails"). Used by web_search and, later, ingestion."""

from urllib.parse import urlparse

ALLOWED_SUFFIXES = (
    ".gov",  # census.gov, data.cms.gov, cdc.gov, city and county portals, ...
    "ridemetro.org",  # Houston METRO
    "openstreetmap.org",
    "overpass-api.de",
    "arcgis.com",  # ArcGIS Hub / feature services
    "socrata.com",
)


def is_allowed(url: str) -> bool:
    """True for http(s) URLs on an allowlisted domain."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    host = parsed.hostname.lower()
    for suffix in ALLOWED_SUFFIXES:
        if suffix.startswith("."):
            if host.endswith(suffix):
                return True
        elif host == suffix or host.endswith(f".{suffix}"):
            return True
    return False
