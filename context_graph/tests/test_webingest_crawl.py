"""Tests for the site crawler and web ingestor (P-web, steps 4-5). Offline.

Uses an in-memory FakeFetcher (a dict of URL → HTML) so crawl scope/queue logic
and the ingest glue are exercised without any network.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from context_graph.webingest import IngestSummary, SiteCrawler, WebIngestor
from context_graph.webingest.fetch import FetchResult
from context_graph.webingest.urls import normalize_url


class FakeFetcher:
    """Duck-typed StaticFetcher: serves HTML pages and data files from dicts."""

    def __init__(self, pages, docs=None):
        self._pages = {normalize_url(u): html for u, html in pages.items()}
        self._docs = {normalize_url(u): b for u, b in (docs or {}).items()}

    async def fetch(self, url):
        n = normalize_url(url)
        if n in self._pages:
            return FetchResult(url, 200, kind="html", html=self._pages[n], ok=True)
        if n in self._docs:
            return FetchResult(url, 200, kind="data", content_type="application/pdf",
                               content=self._docs[n], ok=True)
        return FetchResult(url, 404, reason="HTTP 404")


def _page(*hrefs, body="Some readable policy content here."):
    links = "".join(f'<a href="{h}">l</a>' for h in hrefs)
    return f"<html><body><main><p>{body}</p>{links}</main></body></html>"


SITE = {
    "https://x.org/": _page("/a", "/b", "https://external.org/z", body="Home page content."),
    "https://x.org/a": _page("/c", body="Page A about approvals."),
    "https://x.org/b": _page("/a", body="Page B references A."),   # /a is a dup
    "https://x.org/c": _page(body="Leaf page C content."),
    "https://x.org/empty": "<html><body><nav>menu</nav></body></html>",  # no main text
}


async def _collect(crawler, seed):
    return [p async for p in crawler.crawl(seed)]


# ── crawl scope ──────────────────────────────────────────────────────────────


@pytest.mark.offline
@pytest.mark.asyncio
async def test_crawl_same_domain_bfs():
    c = SiteCrawler(FakeFetcher(SITE), max_depth=2, use_sitemap=False)
    pages = await _collect(c, "https://x.org/")
    urls = {p.url for p in pages}
    assert urls == {"https://x.org/", "https://x.org/a", "https://x.org/b", "https://x.org/c"}
    # external domain never fetched
    assert not any("external.org" in u for u in urls)


@pytest.mark.offline
@pytest.mark.asyncio
async def test_crawl_respects_depth():
    c = SiteCrawler(FakeFetcher(SITE), max_depth=1, use_sitemap=False)
    urls = {p.url for p in await _collect(c, "https://x.org/")}
    assert "https://x.org/c" not in urls          # c is depth 2
    assert "https://x.org/a" in urls


@pytest.mark.offline
@pytest.mark.asyncio
async def test_crawl_max_pages_cap():
    c = SiteCrawler(FakeFetcher(SITE), max_pages=2, max_depth=3, use_sitemap=False)
    pages = await _collect(c, "https://x.org/")
    assert len(pages) == 2


@pytest.mark.offline
@pytest.mark.asyncio
async def test_crawl_records_skips():
    site = {"https://x.org/": _page("/empty", "/missing", body="Home."),
            "https://x.org/empty": SITE["https://x.org/empty"]}
    c = SiteCrawler(FakeFetcher(site), max_depth=1, use_sitemap=False)
    pages = await _collect(c, "https://x.org/")
    assert [p.url for p in pages] == ["https://x.org/"]
    reasons = {u: r for u, r in c.skipped}
    assert "empty content" in reasons["https://x.org/empty"]
    assert "404" in reasons["https://x.org/missing"]


@pytest.mark.offline
@pytest.mark.asyncio
async def test_crawl_dedups():
    # /b links back to /a; /a must be fetched once
    c = SiteCrawler(FakeFetcher(SITE), max_depth=3, use_sitemap=False)
    pages = await _collect(c, "https://x.org/")
    assert len([p for p in pages if p.url == "https://x.org/a"]) == 1


@pytest.mark.offline
@pytest.mark.asyncio
async def test_crawl_sitemap_seeds():
    sitemap = "<urlset><url><loc>https://x.org/c</loc></url></urlset>"
    site = {"https://x.org/": _page(body="Home only, no links."),
            "https://x.org/sitemap.xml": sitemap,
            "https://x.org/c": SITE["https://x.org/c"]}
    c = SiteCrawler(FakeFetcher(site), max_depth=0, use_sitemap=True)
    urls = {p.url for p in await _collect(c, "https://x.org/")}
    assert "https://x.org/c" in urls              # reached only via sitemap


# ── ingestor glue ────────────────────────────────────────────────────────────


@pytest.mark.offline
@pytest.mark.asyncio
async def test_ingestor_calls_ainsert_with_provenance():
    rag = AsyncMock()
    rag.ainsert = AsyncMock(return_value="track-1")
    summary = await WebIngestor(rag).ingest_site(
        "https://x.org/", fetcher=FakeFetcher(SITE), max_depth=2, use_sitemap=False,
    )
    assert isinstance(summary, IngestSummary)
    assert summary.ingested == 4
    assert summary.track_ids == ["track-1"]
    # single batched ainsert; URLs passed as file_paths (→ rc.provenance)
    rag.ainsert.assert_awaited_once()
    kwargs = rag.ainsert.await_args.kwargs
    assert set(kwargs["file_paths"]) == set(summary.urls)
    assert len(kwargs["file_paths"]) == 4


@pytest.mark.offline
@pytest.mark.asyncio
async def test_crawl_collects_data_documents_by_content_type():
    site = {"https://x.org/": _page("/policy.pdf", "/a", body="Home."),
            "https://x.org/a": _page(body="Page A.")}
    docs = {"https://x.org/policy.pdf": b"%PDF-1.7 data"}   # served as application/pdf
    c = SiteCrawler(FakeFetcher(site, docs), max_depth=2, use_sitemap=False)
    pages = await _collect(c, "https://x.org/")
    assert {p.url for p in pages} == {"https://x.org/", "https://x.org/a"}   # PDF not a page
    assert [r.url for r in c.documents] == ["https://x.org/policy.pdf"]
    assert c.documents[0].content == b"%PDF-1.7 data"


@pytest.mark.offline
@pytest.mark.asyncio
async def test_crawl_collects_captured_network_resources():
    # A page whose JS fetched a JSON API — captured during render, not in the DOM.
    class F:
        async def fetch(self, url):
            if normalize_url(url) == "https://x.org/":
                cap = FetchResult("https://x.org/svc/GetItemList", 200, kind="data",
                                  content_type="application/json", content=b'{"a":1}', ok=True)
                return FetchResult(url, 200, kind="html", html=_page(body="Home."),
                                   ok=True, captured=[cap])
            return FetchResult(url, 404, reason="404")
    c = SiteCrawler(F(), max_depth=0, use_sitemap=False)
    pages = await _collect(c, "https://x.org/")
    assert len(pages) == 1
    assert [d.url for d in c.documents] == ["https://x.org/svc/GetItemList"]


@pytest.mark.offline
def test_filename_uses_content_type_extension():
    from context_graph.webingest.ingest import _filename_for
    assert _filename_for("https://x.org/svc.asmx/GetItemList", "application/json").endswith(".txt")
    assert _filename_for("https://x.org/policy", "application/pdf").endswith(".pdf")
    assert _filename_for("https://x.org/data.csv", "text/csv").endswith(".csv")


@pytest.mark.offline
@pytest.mark.asyncio
async def test_ingestor_saves_data_files(tmp_path):
    rag = AsyncMock()
    rag.ainsert = AsyncMock(return_value="t1")
    site = {"https://x.org/": _page("/policy.pdf", body="Home page.")}
    docs = {"https://x.org/policy.pdf": b"%PDF-1.7 real bytes"}
    dl = tmp_path / "inputs"
    summary = await WebIngestor(rag).ingest_site(
        "https://x.org/", fetcher=FakeFetcher(site, docs), use_sitemap=False, download_dir=dl,
    )
    assert summary.ingested == 1                       # the HTML home page
    assert len(summary.documents) == 1
    saved = list(dl.glob("*.pdf"))
    assert saved and saved[0].read_bytes() == b"%PDF-1.7 real bytes"


@pytest.mark.offline
@pytest.mark.asyncio
async def test_ingestor_no_pages_no_ainsert():
    rag = AsyncMock()
    rag.ainsert = AsyncMock()
    summary = await WebIngestor(rag).ingest_site(
        "https://x.org/nope", fetcher=FakeFetcher(SITE), use_sitemap=False,
    )
    assert summary.ingested == 0
    rag.ainsert.assert_not_awaited()
