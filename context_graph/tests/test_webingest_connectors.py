"""Tests for site connectors (Finalsite + WordPress + example template). Offline."""

from __future__ import annotations

import json

import pytest

from context_graph.webingest.connectors import (
    BoardDocsConnector,
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


# ── BoardDocs ────────────────────────────────────────────────────────────────


class _BDTextResp:
    def __init__(self, text, status=200):
        self._text, self.status = text, status

    async def text(self):
        return self._text


class _BDCtx:
    def __init__(self, meetings_json, agenda_by_id, files):
        self.meetings_json, self.agenda_by_id, self.files = meetings_json, agenda_by_id, files

    async def post(self, url, data, headers):
        if url.endswith("BD-GetMeetingsList"):
            return _BDTextResp(json.dumps(self.meetings_json))
        if url.endswith("BD-GetAgenda"):
            import re
            mid = re.search(r"id=([A-Za-z0-9]+)", data).group(1)
            return _BDTextResp(self.agenda_by_id.get(mid, ""))
        return _BDTextResp("", status=404)

    async def get(self, url):
        return _Resp(body=self.files.get(url, b""), status=200,
                     headers={"content-type": "application/pdf"})


BD_HOST = "https://go.boarddocs.com"
BD_BASE = f"{BD_HOST}/pa/nebr/Board.nsf"


@pytest.mark.offline
def test_boarddocs_detect_from_request_and_from_page():
    # from a captured BD- request (base + committee id come verbatim)
    req = [_Req(f"{BD_BASE}/BD-GetMeetingsList", "current_committee_id=A8F3")]
    t = _detect(BoardDocsConnector(), requests=req, page_url=f"{BD_BASE}/Public")
    assert t == {"base": BD_BASE, "cid": "A8F3"}
    # from the page URL + committee id embedded in the JS
    html = '<script>PublicPortal({ current_committee_id: "B9K2" })</script>'
    t2 = _detect(BoardDocsConnector(), page_url=f"{BD_BASE}/Public", html=html)
    assert t2 == {"base": BD_BASE, "cid": "B9K2"}
    # non-BoardDocs site → None
    assert _detect(BoardDocsConnector(), page_url="https://example.org/x") is None


@pytest.mark.offline
@pytest.mark.asyncio
async def test_boarddocs_resolve_walks_meetings_to_files():
    meetings = [{"unique": "M1", "name": "Sept Board Meeting"},
                {"unique": "M2", "name": "Oct Board Meeting"}]
    agendas = {
        "M1": f'<a href="{BD_BASE}/files/AB12/$file/Budget.pdf">Budget</a>',
        "M2": '<a href="/pa/nebr/Board.nsf/files/CD34/$FILE/Minutes.pdf">Minutes</a>',
    }
    files = {f"{BD_BASE}/files/AB12/$file/Budget.pdf": b"%PDF-budget",
             f"{BD_HOST}/pa/nebr/Board.nsf/files/CD34/$FILE/Minutes.pdf": b"%PDF-min"}
    got = await BoardDocsConnector().resolve(
        _BDCtx(meetings, agendas, files), {"base": BD_BASE, "cid": "A8F3"})
    names = {f.filename for f in got}
    assert names == {"Budget.pdf", "Minutes.pdf"}
    assert all(f.content.startswith(b"%PDF") for f in got)


@pytest.mark.offline
@pytest.mark.asyncio
async def test_boarddocs_resolve_respects_cap():
    meetings = [{"unique": "M1"}]
    agendas = {"M1": f'<a href="{BD_BASE}/files/A/$file/one.pdf">1</a>'
                     f'<a href="{BD_BASE}/files/B/$file/two.pdf">2</a>'}
    files = {f"{BD_BASE}/files/A/$file/one.pdf": b"%PDF-1",
             f"{BD_BASE}/files/B/$file/two.pdf": b"%PDF-2"}
    got = await BoardDocsConnector().resolve(
        _BDCtx(meetings, agendas, files), {"base": BD_BASE, "cid": "X"}, max_files=1)
    assert len(got) == 1


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


# ── LLM connector selection ──────────────────────────────────────────────────

from context_graph.webingest.connectors.select import (  # noqa: E402
    LLMConnectorSelector,
    signals_from,
)


def _scripted_llm(reply):
    async def _llm(prompt, system_prompt=None, **kw):
        _llm.prompt = prompt
        return reply
    return _llm


@pytest.mark.offline
def test_signals_from_extracts_generator_markers_and_api_urls():
    html = ('<meta name="generator" content="WordPress 6.5" />'
            '<link rel="https://api.w.org/" href="https://b.org/wp-json/" />')
    reqs = [_Req("https://b.org/wp-json/wp/v2/posts"),
            _Req("https://b.org/style.css"),          # asset → dropped
            _Req("https://b.org/logo.png")]           # asset → dropped
    sig = signals_from("https://b.org/", html, reqs, [])
    assert sig["generator"] == "WordPress 6.5"
    assert any("api.w.org" in m for m in sig["markers"])
    assert sig["api_request_samples"] == ["https://b.org/wp-json/wp/v2/posts"]


@pytest.mark.offline
@pytest.mark.asyncio
async def test_selector_picks_named_connector():
    conns = [FinalsiteConnector(), WordPressConnector()]
    sel = LLMConnectorSelector(_scripted_llm('{"connectors": ["wordpress"], "reason": "wp-json"}'))
    chosen = await sel.select({"generator": "WordPress"}, conns)
    assert [c.name for c in chosen] == ["wordpress"]


@pytest.mark.offline
@pytest.mark.asyncio
async def test_selector_empty_list_is_honoured():
    conns = [FinalsiteConnector(), WordPressConnector()]
    sel = LLMConnectorSelector(_scripted_llm('{"connectors": [], "reason": "static site"}'))
    assert await sel.select({}, conns) == []


@pytest.mark.offline
@pytest.mark.asyncio
async def test_selector_falls_back_to_all_when_llm_unparseable_or_errors():
    conns = [FinalsiteConnector(), WordPressConnector()]
    bad = LLMConnectorSelector(_scripted_llm("not json at all"))
    assert len(await bad.select({}, conns)) == 2

    async def _boom(prompt, system_prompt=None, **kw):
        raise RuntimeError("llm down")
    assert len(await LLMConnectorSelector(_boom).select({}, conns)) == 2
