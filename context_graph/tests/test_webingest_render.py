"""Tests for the Playwright JS-rendering fetcher (P-web, phase 1b).

Routing logic is tested offline with an injected ``navigate`` (no browser). One
integration test launches real Chromium to confirm JS actually renders.
"""

from __future__ import annotations

import httpx
import pytest

from context_graph.webingest.fetch import StaticFetcher
from context_graph.webingest.render import NavResult, PlaywrightFetcher

ROBOTS_ALLOW = "User-agent: *\nAllow: /\n"


def _static(handler):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return StaticFetcher(client, min_interval=0.0)


# ── routing logic (offline, injected navigate) ───────────────────────────────


@pytest.mark.offline
@pytest.mark.asyncio
async def test_render_returns_rendered_html():
    def handler(req):
        return httpx.Response(200, text=ROBOTS_ALLOW)
    async def nav(url):
        return NavResult(200, "text/html", url, "<html><body>JS_RENDERED</body></html>")
    pf = PlaywrightFetcher(_static(handler), navigate=nav)
    r = await pf.fetch("https://x.org/page")
    assert r.ok and r.kind == "html" and "JS_RENDERED" in r.html


@pytest.mark.offline
@pytest.mark.asyncio
async def test_render_delegates_data_to_static():
    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text=ROBOTS_ALLOW)
        return httpx.Response(200, content=b"%PDF bytes",
                              headers={"content-type": "application/pdf"})
    async def nav(url):
        return NavResult(200, "application/pdf", url, "")   # browser hit a PDF
    pf = PlaywrightFetcher(_static(handler), navigate=nav)
    r = await pf.fetch("https://x.org/policy.pdf")
    assert r.ok and r.kind == "data" and r.content == b"%PDF bytes"


@pytest.mark.offline
@pytest.mark.asyncio
async def test_render_respects_robots_without_navigating():
    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /priv\n")
        return httpx.Response(200, text="x", headers={"content-type": "text/html"})
    calls = {"n": 0}
    async def nav(url):
        calls["n"] += 1
        return NavResult(200, "text/html", url, "<html/>")
    pf = PlaywrightFetcher(_static(handler), navigate=nav)
    r = await pf.fetch("https://x.org/priv/secret")
    assert r.reason == "blocked by robots.txt" and calls["n"] == 0


@pytest.mark.offline
@pytest.mark.asyncio
async def test_render_attaches_captured_network_resources():
    from context_graph.webingest.fetch import FetchResult
    def handler(req):
        return httpx.Response(200, text=ROBOTS_ALLOW)
    cap = FetchResult("https://x.org/svc/GetItemList", 200, kind="data",
                      content_type="application/json", content=b'{"items":[1]}', ok=True)
    async def nav(url):
        return NavResult(200, "text/html", url, "<html/>", [cap])
    pf = PlaywrightFetcher(_static(handler), navigate=nav)
    r = await pf.fetch("https://x.org/page")
    assert r.ok and len(r.captured) == 1
    assert r.captured[0].content_type == "application/json"


@pytest.mark.offline
@pytest.mark.asyncio
async def test_render_navigation_error_is_captured():
    def handler(req):
        return httpx.Response(200, text=ROBOTS_ALLOW)
    async def nav(url):
        raise RuntimeError("timeout")
    pf = PlaywrightFetcher(_static(handler), navigate=nav)
    r = await pf.fetch("https://x.org/page")
    assert not r.ok and "render error" in r.reason


# ── real browser (integration) ───────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.asyncio
async def test_real_browser_executes_javascript():
    static = StaticFetcher(httpx.AsyncClient(), respect_robots=False)
    pf = PlaywrightFetcher(static, wait_until="load")
    try:
        url = ("data:text/html,<body><div id='x'></div>"
               "<script>document.getElementById('x').innerText='HELLO_JS'</script></body>")
        nav = await pf._default_navigate(url)
        assert nav is not None and "HELLO_JS" in nav.html   # JS ran, DOM updated
    finally:
        await pf.aclose()
        await static._client.aclose()
