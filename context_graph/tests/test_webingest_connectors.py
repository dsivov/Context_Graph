"""Tests for site connectors (Finalsite document container). Offline."""

from __future__ import annotations

import json

import pytest

from context_graph.webingest.connectors import FinalsiteConnector


class _Req:
    def __init__(self, url, post_data):
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


class _FakeReqCtx:
    """Stand-in for Playwright's page.request: serves a folder tree + files."""

    def __init__(self, tree, files):
        self.tree = tree
        self.files = files

    async def post(self, url, data, headers):
        parent = json.loads(data)["parentId"]
        return _Resp(json_data={"d": {"DataObject": self.tree.get(parent, [])}})

    async def get(self, dl):
        return _Resp(body=self.files.get(dl, b""), status=200,
                     headers={"content-type": "application/pdf"})


TREE = {
    "root": [
        {"Type": "content_folder", "ItemId": "a", "Name": "Series A"},
        {"Type": "content_file", "Extension": "pdf", "Name": "Policy X",
         "DownloadLink": "https://s/GetFile?key=x"},
    ],
    "a": [
        {"Type": "content_file", "Extension": "pdf", "Name": "Policy Y",
         "DownloadLink": "https://s/GetFile?key=y"},
    ],
}
FILES = {"https://s/GetFile?key=x": b"%PDF-X", "https://s/GetFile?key=y": b"%PDF-Y"}

GETITEMLIST = "https://s/portal/svc/ContentItemSvc.asmx/GetItemList"


@pytest.mark.offline
def test_detect_finds_getitemlist_template():
    reqs = [_Req("https://s/other", None),
            _Req(GETITEMLIST, '{"parentId":"root","Params":"PARAMBLOB"}')]
    t = FinalsiteConnector().detect(reqs)
    assert t is not None
    assert t["root"] == "root" and t["params"] == "PARAMBLOB" and t["url"] == GETITEMLIST


@pytest.mark.offline
def test_detect_none_without_getitemlist():
    assert FinalsiteConnector().detect([_Req("https://s/x", None)]) is None


@pytest.mark.offline
@pytest.mark.asyncio
async def test_resolve_recurses_tree_and_downloads_files():
    conn = FinalsiteConnector()
    template = {"url": GETITEMLIST, "params": "P", "root": "root"}
    files = await conn.resolve(_FakeReqCtx(TREE, FILES), template)
    assert len(files) == 2                                  # X (root) + Y (folder A)
    assert {f.filename for f in files} == {"Policy X.pdf", "Policy Y.pdf"}
    assert all(f.kind == "data" and f.content.startswith(b"%PDF") for f in files)
    assert all(f.content_type == "application/pdf" for f in files)


@pytest.mark.offline
@pytest.mark.asyncio
async def test_resolve_cap_from_constructor_and_call():
    template = {"url": GETITEMLIST, "params": "P", "root": "root"}
    # constructor cap
    assert len(await FinalsiteConnector(max_files=1).resolve(_FakeReqCtx(TREE, FILES), template)) == 1
    # per-run cap (max_documents) overrides the constructor default
    assert len(await FinalsiteConnector().resolve(_FakeReqCtx(TREE, FILES), template, max_files=1)) == 1
