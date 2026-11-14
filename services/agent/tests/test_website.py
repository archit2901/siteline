"""Website pre-fill: HTML -> text and same-site useful links; blocked hosts fail softly."""

import asyncio

from services.agent import website
from services.agent.tools.allowlist import BlockedURL, safe_request


def test_page_text_and_links() -> None:
    html = """<html><head><script>var x=1</script><style>p{}</style></head><body>
      <h1>Bayou Ortho</h1><p>Sports medicine, MRI and physical therapy.</p>
      <a href="/locations/">Locations</a><a href="https://other.com/about">x</a><a href="/blog/post-1">Blog</a>
      <a href="https://www.bayou.example/insurance">Insurance</a></body></html>"""
    text, links = website.page_text(html)
    assert "Sports medicine" in text and "var x" not in text
    useful = website.same_site_links("https://bayou.example/", links)
    assert useful == ["https://bayou.example/locations/", "https://www.bayou.example/insurance"]


def test_private_hosts_are_blocked() -> None:
    import httpx

    async def go() -> str:
        async with httpx.AsyncClient() as c:
            try:
                await safe_request(c, "GET", "http://127.0.0.1:8010/health", any_public_host=True)
            except BlockedURL as exc:
                return str(exc)
        return "not blocked"

    assert "non-public" in asyncio.run(go())
    out = asyncio.run(website.prefill("http://localhost:8010", None))
    assert "couldn't read" in out.reply and not out.ready
