"""Tests for the web-ingest job service (P-web, step 6). Offline."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from context_graph.webingest.fetch import FetchResult
from context_graph.webingest.service import WebIngestService
from context_graph.webingest.urls import normalize_url


class FakeFetcher:
    def __init__(self, pages):
        self._pages = {normalize_url(u): html for u, html in pages.items()}

    async def fetch(self, url):
        html = self._pages.get(normalize_url(url))
        if html is None:
            return FetchResult(url, 404, reason="HTTP 404")
        return FetchResult(url, 200, kind="html", html=html, ok=True)


def _page(*hrefs, body="Readable content."):
    links = "".join(f'<a href="{h}">l</a>' for h in hrefs)
    return f"<html><body><main><p>{body}</p>{links}</main></body></html>"


SITE = {
    "https://x.org/": _page("/a", body="Home."),
    "https://x.org/a": _page(body="Page A."),
}

PARAMS = {
    "seed_url": "https://x.org/", "max_pages": 50, "max_depth": 2,
    "same_domain": True, "respect_robots": True, "use_sitemap": False,
    "min_interval": 0.0,
}


@pytest.mark.offline
@pytest.mark.asyncio
async def test_service_runs_job_to_completion():
    rag = AsyncMock()
    rag.ainsert = AsyncMock(return_value="t1")
    svc = WebIngestService(rag, fetcher=FakeFetcher(SITE), id_factory=lambda: "job1")

    job = svc.start("acme", dict(PARAMS))
    assert job["state"] == "running" and job["job_id"] == "job1"

    done = await svc.wait("job1")
    assert done["state"] == "done"
    assert done["summary"]["ingested"] == 2
    assert done["summary"]["track_ids"] == ["t1"]
    rag.ainsert.assert_awaited_once()


@pytest.mark.offline
@pytest.mark.asyncio
async def test_service_error_is_captured_not_raised():
    rag = AsyncMock()
    rag.ainsert = AsyncMock(side_effect=RuntimeError("boom"))
    svc = WebIngestService(rag, fetcher=FakeFetcher(SITE), id_factory=lambda: "job2")
    svc.start("acme", dict(PARAMS))
    done = await svc.wait("job2")
    assert done["state"] == "error" and "boom" in done["error"]


@pytest.mark.offline
@pytest.mark.asyncio
async def test_service_get_and_list():
    rag = AsyncMock()
    rag.ainsert = AsyncMock(return_value="t")
    svc = WebIngestService(rag, fetcher=FakeFetcher(SITE), id_factory=lambda: "j")
    assert svc.get("nope") is None
    svc.start("acme", dict(PARAMS))
    await svc.wait("j")
    assert svc.get("j")["state"] == "done"
    assert len(svc.list()) == 1
