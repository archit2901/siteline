"""Domains datasets may come from (docs/DESIGN.md "Guardrails"). Used by web_search and, later, ingestion."""

from urllib.parse import urlparse

import httpx

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


class BlockedURL(Exception):
    pass


async def _public_host(host: str) -> None:
    """Refuse hosts that resolve to private, loopback, link-local, reserved or metadata addresses."""
    import asyncio
    import ipaddress
    import socket

    infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    for info in infos:
        addr = ipaddress.ip_address(info[4][0])
        if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved or addr.is_multicast \
                or addr.is_unspecified:
            raise BlockedURL(f"{host} resolves to a non-public address")


async def safe_request(client: httpx.AsyncClient, method: str, url: str, *, max_redirects: int = 5,
                       **kwargs: object) -> httpx.Response:
    """An HTTP request that follows redirects by hand, checking the allowlist and public DNS on every hop."""
    for _ in range(max_redirects + 1):
        parsed = urlparse(url)
        if not is_allowed(url) or not parsed.hostname:
            raise BlockedURL(f"{url} is not on the allowlist")
        await _public_host(parsed.hostname)
        resp = await client.request(method, url, follow_redirects=False, **kwargs)  # type: ignore[arg-type]
        if resp.status_code not in (301, 302, 303, 307, 308) or "location" not in resp.headers:
            return resp
        url = str(resp.url.join(resp.headers["location"]))
    raise BlockedURL(f"too many redirects from {url}")
