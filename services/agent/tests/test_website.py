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


def test_locations_page_is_read_before_service_pages():
    from services.agent.website import same_site_links
    links = ["/about", "/our-team", "/service-overview", "/adult-medicine-service", "/pediatric-care-service",
             "/women-health-service", "/pharmacy-service", "/locations", "/hope-beltway-clinic", "/contact-us"]
    picked = same_site_links("https://www.example.org/", links)
    assert picked[0] == "https://www.example.org/locations"
    assert picked.index("https://www.example.org/contact-us") < picked.index("https://www.example.org/about")
    assert sum("service" in u for u in picked) <= 2


def test_addresses_are_found_and_deduped_by_street_number_and_zip():
    from services.agent.intake import address_key
    from services.agent.website import find_addresses, strip_boilerplate
    page = ("Menu\nHome\nClinic Beltway\n7001 Corporate Dr. #120, Houston, TX 77036\nClinic Aldine\n"
            "3000 Aldine Mail Rd. Building C, #200\nHouston, TX 77039\nMailing\nP.O. Box 5, Houston, TX 77001\nHome")
    text = strip_boilerplate(page, {"menu"})
    assert text.count("Home") == 1 and "Menu" not in text
    found = find_addresses(text)
    assert found[0] == ("Clinic Beltway", "7001 Corporate Dr. #120, Houston, TX 77036")
    assert found[1][1] == "3000 Aldine Mail Rd. Building C, #200, Houston, TX 77039"
    assert len(found) == 2
    assert address_key("13930 Bellaire Blvd., Houston, TX 77083") == address_key("13930 Bellaire Blvd Houston, TX 77083")
