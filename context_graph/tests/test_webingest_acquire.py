"""Tests for the web-ingest acquisition primitives (urls, clean, fetch). Offline.

The fetcher is exercised against an in-memory site via ``httpx.MockTransport``
(no network), with an injected clock/sleep so rate limiting is deterministic.
"""

from __future__ import annotations

import httpx
import pytest

from context_graph.webingest import (
    StaticFetcher,
    extract_main_text,
    host_of,
    is_http,
    normalize_url,
    resolve,
    same_site,
)


# ── urls ─────────────────────────────────────────────────────────────────────


@pytest.mark.offline
def test_is_http():
    assert is_http("https://x.org/a")
    assert not is_http("mailto:a@b.com")
    assert not is_http("javascript:void(0)")


@pytest.mark.offline
def test_host_strips_www_and_port():
    assert host_of("https://www.EastHartford.org:443/x") == "easthartford.org"
    assert host_of("http://sub.easthartford.org/x") == "sub.easthartford.org"


@pytest.mark.offline
@pytest.mark.parametrize("url,expected", [
    ("https://x.org/a/#frag", "https://x.org/a"),
    ("https://X.org/a/", "https://x.org/a"),
    ("https://x.org", "https://x.org/"),
    ("https://x.org/a?q=1", "https://x.org/a?q=1"),
    ("https://x.org:443/a", "https://x.org/a"),
])
def test_normalize_url(url, expected):
    assert normalize_url(url) == expected


@pytest.mark.offline
def test_resolve_and_same_site():
    assert resolve("https://x.org/a/b", "../c") == "https://x.org/c"
    assert same_site("https://www.x.org/p", "https://x.org/")
    assert not same_site("https://other.org/p", "https://x.org/")


# ── clean ────────────────────────────────────────────────────────────────────

HTML = """
<html><head><title>Policy 4148</title></head>
<body>
  <nav><a href="/home">Home</a></nav>
  <header>banner</header>
  <main>
    <h1>Mandated Reporter Training</h1>
    <p>All staff must complete this annually.</p>
    <script>var x = 1;</script>
    <p>Adopted by the Board in 2024. See <a href="policy/4149">4149</a>.</p>
  </main>
  <footer>© East Hartford</footer>
</body></html>
"""


@pytest.mark.offline
def test_clean_extracts_main_text_and_drops_boilerplate():
    page = extract_main_text(HTML, "https://x.org/policies/4148")
    assert page.title == "Policy 4148"
    assert "Mandated Reporter Training" in page.text
    assert "must complete this annually" in page.text
    # boilerplate + scripts gone
    assert "banner" not in page.text
    assert "© East Hartford" not in page.text
    assert "var x" not in page.text


@pytest.mark.offline
def test_clean_resolves_links():
    page = extract_main_text(HTML, "https://x.org/policies/4148")
    assert "https://x.org/home" in page.links
    assert "https://x.org/policies/policy/4149" in page.links


@pytest.mark.offline
def test_clean_empty_html():
    assert extract_main_text("", "https://x.org/").is_empty
    assert extract_main_text("   ", "https://x.org/").is_empty


# ── fetch (httpx.MockTransport) ──────────────────────────────────────────────


def _site(handler):
    """Build a StaticFetcher over a mock transport, with deterministic timing."""
    ticks = {"t": 0.0}
    slept = []

    async def sleep(s):
        slept.append(s)
        ticks["t"] += s

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    f = StaticFetcher(client, min_interval=2.0, sleep=sleep, clock=lambda: ticks["t"])
    return f, slept


ROBOTS_ALLOW = "User-agent: *\nAllow: /\n"
ROBOTS_BLOCK = "User-agent: *\nDisallow: /private\n"


@pytest.mark.offline
@pytest.mark.asyncio
async def test_fetch_ok_html():
    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text=ROBOTS_ALLOW)
        return httpx.Response(200, text="<html><body><p>hi</p></body></html>",
                              headers={"content-type": "text/html"})
    f, _ = _site(handler)
    r = await f.fetch("https://x.org/page")
    assert r.ok and r.status == 200 and "hi" in r.html


@pytest.mark.offline
@pytest.mark.asyncio
async def test_fetch_respects_robots():
    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text=ROBOTS_BLOCK)
        return httpx.Response(200, text="<html/>", headers={"content-type": "text/html"})
    f, _ = _site(handler)
    assert (await f.fetch("https://x.org/private/x")).reason == "blocked by robots.txt"
    assert (await f.fetch("https://x.org/public")).ok


@pytest.mark.offline
def test_classify_content_type():
    from context_graph.webingest.fetch import classify_content_type
    assert classify_content_type("text/html; charset=utf-8") == "html"
    assert classify_content_type("application/xml") == "html"        # sitemaps
    assert classify_content_type("application/pdf") == "data"
    assert classify_content_type("application/json") == "data"
    assert classify_content_type("image/png") == "other"


@pytest.mark.offline
@pytest.mark.asyncio
async def test_fetch_classifies_data_and_other():
    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text=ROBOTS_ALLOW)
        if req.url.path.endswith(".pdf"):
            return httpx.Response(200, content=b"%PDF-1.7 ...",
                                  headers={"content-type": "application/pdf"})
        return httpx.Response(200, content=b"\x89PNG", headers={"content-type": "image/png"})
    f, _ = _site(handler)
    pdf = await f.fetch("https://x.org/policy.pdf")
    assert pdf.ok and pdf.kind == "data" and pdf.content.startswith(b"%PDF")
    img = await f.fetch("https://x.org/logo.png")
    assert not img.ok and img.kind == "other"


@pytest.mark.offline
@pytest.mark.asyncio
async def test_fetch_rate_limits_same_host():
    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text=ROBOTS_ALLOW)
        return httpx.Response(200, text="<html><body>x</body></html>",
                              headers={"content-type": "text/html"})
    f, slept = _site(handler)
    await f.fetch("https://x.org/a")
    await f.fetch("https://x.org/b")     # second same-host fetch must wait
    assert any(s > 0 for s in slept)


@pytest.mark.offline
@pytest.mark.asyncio
async def test_fetch_http_error():
    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text=ROBOTS_ALLOW)
        return httpx.Response(404, text="nope")
    f, _ = _site(handler)
    r = await f.fetch("https://x.org/missing")
    assert not r.ok and r.status == 404
