"""Tests for the LLM site analyst (P-web, phase 2). Offline (scripted LLM)."""

from __future__ import annotations

import json

import pytest

from context_graph.webingest import WebIngestor
from context_graph.webingest.analyst import SiteAnalyst, _sample
from context_graph.webingest.fetch import FetchResult
from context_graph.webingest.urls import normalize_url


def _llm(payload):
    async def llm(prompt, system_prompt=None, **kwargs):
        return json.dumps(payload) if not isinstance(payload, str) else payload
    return llm


def _data(url, ct, body):
    return FetchResult(url, 200, kind="data", content_type=ct, content=body, ok=True)


CATALOG = _data("https://x.org/svc/GetItemList", "application/json",
                b'{"items":[{"file":"https://x.org/p1.pdf"}]}')
BEACON = _data("https://cdn.x.org/track-abc123", "application/json", b'{"beacon":1}')


# ── analyst unit ─────────────────────────────────────────────────────────────


@pytest.mark.offline
@pytest.mark.asyncio
async def test_analyst_keeps_relevant_drops_noise_and_extracts():
    payload = {
        "decisions": [
            {"url": CATALOG.url, "keep": True, "reason": "policy catalog"},
            {"url": BEACON.url, "keep": False, "reason": "tracking beacon"},
        ],
        "document_urls": ["https://x.org/p1.pdf"],
    }
    r = await SiteAnalyst(_llm(payload)).analyze([CATALOG, BEACON])
    assert r.keep == [CATALOG.url]
    assert r.extracted_urls == ["https://x.org/p1.pdf"]
    assert r.ok


@pytest.mark.offline
@pytest.mark.asyncio
async def test_analyst_unmentioned_candidate_is_dropped():
    # two real candidates; the LLM only keeps one — the other (unmentioned) is dropped
    A = _data("https://x.org/a.json", "application/json", b'{"x":"' + b"a" * 60 + b'"}')
    B = _data("https://x.org/b.json", "application/json", b'{"x":"' + b"b" * 60 + b'"}')
    payload = {"decisions": [{"url": A.url, "keep": True, "reason": "relevant"}]}
    r = await SiteAnalyst(_llm(payload)).analyze([A, B])
    assert A.url in r.keep
    assert B.url not in r.keep            # default-drop, not fail-open


@pytest.mark.offline
@pytest.mark.asyncio
async def test_analyst_auto_keeps_binary_documents():
    pdf = _data("https://x.org/GetFile?key=x", "application/pdf", b"%PDF-1.7" + b"x" * 80)
    # even if the LLM keeps nothing, a PDF (content by definition) is auto-kept
    r = await SiteAnalyst(_llm({"decisions": []})).analyze([pdf])
    assert pdf.url in r.keep


@pytest.mark.offline
def test_looks_like_noise_prefilter():
    from context_graph.webingest.analyst import _looks_like_noise
    assert _looks_like_noise(_data("https://x.org/x", "application/json", b"{}"))         # tiny
    assert _looks_like_noise(_data("https://x.org/cdn-cgi/rum", "application/json",
                                   b'{"' + b"a" * 60 + b'":1}'))                          # cloudflare
    assert not _looks_like_noise(CATALOG)                                                 # real API json


@pytest.mark.offline
@pytest.mark.asyncio
async def test_analyst_non_json_keeps_candidates_only():
    # BEACON is tiny → pre-filtered as noise; CATALOG is a candidate kept on parse failure
    r = await SiteAnalyst(_llm("no json here")).analyze([CATALOG, BEACON])
    assert not r.ok
    assert r.keep == [CATALOG.url]        # noise dropped, candidate kept
    assert r.dropped_noise == 1


@pytest.mark.offline
@pytest.mark.asyncio
async def test_analyst_empty():
    r = await SiteAnalyst(_llm({})).analyze([])
    assert r.keep == [] and r.extracted_urls == []


@pytest.mark.offline
def test_sample_only_text_content_types():
    assert "beacon" in _sample(BEACON, 100)                        # json → sampled
    assert _sample(_data("u", "application/pdf", b"%PDF..."), 100) == ""   # binary → empty


# ── ingestor with analyst (end-to-end, offline) ──────────────────────────────


class _Fake:
    async def fetch(self, url):
        n = normalize_url(url)
        if n == "https://x.org/":
            cap = _data("https://x.org/svc/GetItemList", "application/json",
                        b'{"items":[{"url":"https://x.org/p1.pdf"}]}')
            noise = _data("https://cdn.x.org/track-xyz", "application/json", b'{"b":1}')
            return FetchResult(url, 200, kind="html",
                               html="<html><body><main><p>home</p></main></body></html>",
                               ok=True, captured=[cap, noise])
        if n == "https://x.org/p1.pdf":
            return _data("https://x.org/p1.pdf", "application/pdf", b"%PDF policy one")
        return FetchResult(url, 404, reason="404")


@pytest.mark.offline
@pytest.mark.asyncio
async def test_ingestor_analyst_filters_and_fetches_extracted(tmp_path):
    from unittest.mock import AsyncMock
    rag = AsyncMock()
    rag.ainsert = AsyncMock(return_value="t1")
    payload = {
        "decisions": [
            {"url": "https://x.org/svc/GetItemList", "keep": True, "reason": "catalog"},
            {"url": "https://cdn.x.org/track-xyz", "keep": False, "reason": "tracking"},
        ],
        "document_urls": ["https://x.org/p1.pdf"],
    }
    dl = tmp_path / "inputs"
    summary = await WebIngestor(rag).ingest_site(
        "https://x.org/", fetcher=_Fake(), use_sitemap=False, max_depth=0,
        download_dir=dl, analyst=SiteAnalyst(_llm(payload)),
    )
    # tracking beacon dropped; catalog kept; extracted PDF fetched → 2 saved
    assert summary.analyst["kept"] == 2
    files = {f.name: f.read_bytes() for f in dl.iterdir()}
    assert any(n.endswith(".pdf") and b == b"%PDF policy one" for n, b in files.items())
    assert not any("track" in n for n in files)     # noise not saved
