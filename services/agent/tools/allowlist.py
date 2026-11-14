"""Domains datasets may come from (docs/DESIGN.md "Guardrails"). Used by web_search and, later, ingestion."""

import asyncio
import ipaddress
import socket
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


NAT64 = ipaddress.ip_network("64:ff9b::/96")


def _public_ip(text: str) -> bool:
    """Deny by default: only globally routable addresses, including IPv4 embedded in IPv6 forms."""
    addr = ipaddress.ip_address(text.split("%")[0])
    if isinstance(addr, ipaddress.IPv6Address):
        if addr in NAT64:
            return False
        embedded = addr.ipv4_mapped or addr.sixtofour
        if embedded is not None:
            return _public_ip(str(embedded))
    return addr.is_global and not addr.is_multicast


async def _check_dns(host: str) -> None:
    infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    for info in infos:
        if not _public_ip(info[4][0]):
            raise BlockedURL(f"{host} resolves to a non-public address")


def _check_peer(resp: httpx.Response, url: str) -> None:
    """The address the socket actually connected to (closes the gap between the DNS check and the connect)."""
    stream = resp.extensions.get("network_stream")
    peer = stream.get_extra_info("server_addr") if stream is not None else None
    if peer and not _public_ip(str(peer[0])):
        raise BlockedURL(f"{url} connected to a non-public address")


async def safe_request(client: httpx.AsyncClient, method: str, url: str, *, max_redirects: int = 5,
                       max_bytes: int = 2_000_000, **kwargs: object) -> httpx.Response:
    """An HTTP request for untrusted URLs: allowlist + public-DNS check on every redirect hop, peer-address check
    before any body is read, and at most max_bytes of body. Redirects are followed by hand."""
    for _ in range(max_redirects + 1):
        parsed = urlparse(url)
        if not is_allowed(url) or not parsed.hostname:
            raise BlockedURL(f"{url} is not on the allowlist")
        await _check_dns(parsed.hostname)
        async with client.stream(method, url, follow_redirects=False, **kwargs) as resp:  # type: ignore[arg-type]
            _check_peer(resp, url)
            if resp.status_code in (301, 302, 303, 307, 308) and "location" in resp.headers:
                url = str(resp.url.join(resp.headers["location"]))
                continue
            body = b""
            async for chunk in resp.aiter_bytes():
                body += chunk
                if len(body) > max_bytes:
                    raise BlockedURL(f"{url} is larger than {max_bytes} bytes")
            return httpx.Response(resp.status_code, headers=resp.headers, content=body, request=resp.request)
    raise BlockedURL(f"too many redirects from {url}")
