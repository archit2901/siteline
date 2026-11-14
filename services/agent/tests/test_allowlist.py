import httpx
import pytest

from services.agent.tools.allowlist import BlockedURL, is_allowed, safe_request


@pytest.mark.parametrize(("url", "ok"), [
    ("https://data.cdc.gov/d/yjkw-uj5s", True), ("https://www.ridemetro.org/x", True),
    ("https://services.arcgis.com/a/FeatureServer/0", True), ("https://evilgov.com/x", False),
    ("javascript:alert(1)", False), ("http://169.254.169.254/latest/meta-data", False),
])
def test_allowlist(url: str, ok: bool) -> None:
    assert is_allowed(url) is ok


@pytest.mark.asyncio
async def test_redirect_off_the_allowlist_is_blocked() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(BlockedURL):
            await safe_request(client, "HEAD", "https://data.cdc.gov/d/x")


@pytest.mark.asyncio
async def test_allowed_host_resolving_to_loopback_is_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    async def fake_getaddrinfo(*args: object, **kwargs: object) -> list[tuple]:  # type: ignore[type-arg]
        return [(2, 1, 6, "", ("127.0.0.1", 0))]

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", fake_getaddrinfo)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200))) as client:
        with pytest.raises(BlockedURL, match="non-public"):
            await safe_request(client, "HEAD", "https://data.cdc.gov/d/x")
