"""Tests for site connectors (Finalsite + WordPress + example template). Offline."""

from __future__ import annotations

import json

import pytest

from context_graph.webingest.connectors import (
    ExampleConnector,
    FinalsiteConnector,
    WordPressConnector,
)


class _Req:
    def __init__(self, url, post_data=None):
        self.url = url
        self.post_data = post_data


class _Resp:
    def __init__(self, json_data=None, body=None, status=200, headers=None):
        self._json = json_data
        self._body = body
        self.status = status
        self.headers = headers or {}

    async def json(self):
        return self._json

    async def body(self):
        return self._body


def _detect(conn, *, requests=(), responses=(), page_url="", html=""):
    return conn.detect(requests=list(requests), responses=list(responses),
                       page_url=page_url, html=html)


# ── Finalsite ────────────────────────────────────────────────────────────────


class _FinalsiteCtx:
    def __init__(self, tree, files):
        self.tree, self.files = tree, files

    async def post(self, url, data, headers):
        parent = json.loads(data)["parentId"]
        return _Resp(json_data={"d": {"DataObject": self.tree.get(parent, [])}})

    async def get(self, dl):
        return _Resp(body=self.files.get(dl, b""), status=200,
                     headers={"content-type": "application/pdf"})


F_TREE = {
    "root": [
        {"Type": "content_folder", "ItemId": "a", "Name": "Series A"},
        {"Type": "content_file", "Extension": "pdf", "Name": "Policy X",
         "DownloadLink": "https://s/GetFile?key=x"},
    ],
    "a": [{"Type": "content_file", "Extension": "pdf", "Name": "Policy Y",
           "DownloadLink": "https://s/GetFile?key=y"}],
}
F_FILES = {"https://s/GetFile?key=x": b"%PDF-X", "https://s/GetFile?key=y": b"%PDF-Y"}
GETITEMLIST = "https://s/portal/svc/ContentItemSvc.asmx/GetItemList"


@pytest.mark.offline
def test_finalsite_detect():
    reqs = [_Req("https://s/other"), _Req(GETITEMLIST, '{"parentId":"root","Params":"P"}')]
    t = _detect(FinalsiteConnector(), requests=reqs)
    assert t and t["root"] == "root" and t["params"] == "P"
    assert _detect(FinalsiteConnector(), requests=[_Req("https://s/x")]) is None


@pytest.mark.offline
@pytest.mark.asyncio
async def test_finalsite_resolve_recurses_and_caps():
    template = {"url": GETITEMLIST, "params": "P", "root": "root"}
    files = await FinalsiteConnector().resolve(_FinalsiteCtx(F_TREE, F_FILES), template)
    assert {f.filename for f in files} == {"Policy X.pdf", "Policy Y.pdf"}
    assert all(f.content.startswith(b"%PDF") for f in files)
    # per-run cap (max_documents) overrides the default
    one = await FinalsiteConnector().resolve(_FinalsiteCtx(F_TREE, F_FILES), template, max_files=1)
    assert len(one) == 1


# ── WordPress ────────────────────────────────────────────────────────────────


class _WPCtx:
    def __init__(self, pages, files):
        self.pages, self.files = pages, files   # page-number -> list of media items

    async def get(self, url):
        if "/wp/v2/media" in url:
            import re
            page = int(re.search(r"[?&]page=(\d+)", url).group(1))
            items = self.pages.get(page, [])
            return _Resp(json_data=items, status=200 if items or page == 1 else 400)
        return _Resp(body=self.files.get(url, b""), status=200,
                     headers={"content-type": "application/pdf"})


@pytest.mark.offline
def test_wordpress_detect_from_link_tag_and_request():
    html = '<link rel="https://api.w.org/" href="https://blog.org/wp-json/" />'
    assert _detect(WordPressConnector(), html=html)["media"] == "https://blog.org/wp-json/wp/v2/media"
    req = [_Req("https://blog.org/wp-json/wp/v2/posts?per_page=1")]
    assert _detect(WordPressConnector(), requests=req)["media"] == "https://blog.org/wp-json/wp/v2/media"
    assert _detect(WordPressConnector(), html="<html></html>") is None


@pytest.mark.offline
@pytest.mark.asyncio
async def test_wordpress_resolve_pages_media_and_filters_docs():
    pages = {
        1: [
            {"source_url": "https://blog.org/a.pdf", "mime_type": "application/pdf",
             "title": {"rendered": "Handbook"}},
            {"source_url": "https://blog.org/photo.jpg", "mime_type": "image/jpeg",
             "title": {"rendered": "Photo"}},   # not a doc → skipped
        ],
        2: [],   # end of pages
    }
    files = {"https://blog.org/a.pdf": b"%PDF-handbook"}
    got = await WordPressConnector(per_page=100).resolve(
        _WPCtx(pages, files), {"media": "https://blog.org/wp-json/wp/v2/media"})
    assert len(got) == 1
    assert got[0].filename == "Handbook" and got[0].content == b"%PDF-handbook"


# ── Example template ─────────────────────────────────────────────────────────


class _ExCtx:
    async def get(self, url):
        if url.endswith("/api/docs/list"):
            return _Resp(json_data={"documents": [
                {"url": "https://s/doc1.pdf", "name": "Doc One"}]})
        return _Resp(body=b"%PDF-1", status=200, headers={"content-type": "application/pdf"})


@pytest.mark.offline
@pytest.mark.asyncio
async def test_example_connector_detect_and_resolve():
    conn = ExampleConnector()
    t = _detect(conn, requests=[_Req("https://s/api/docs/list")])
    assert t == {"list_url": "https://s/api/docs/list"}
    files = await conn.resolve(_ExCtx(), t)
    assert len(files) == 1 and files[0].filename == "Doc One"
