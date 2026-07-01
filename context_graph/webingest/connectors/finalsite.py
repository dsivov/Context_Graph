"""Finalsite / Blackboard "Content Item" document-container connector.

Powers thousands of K-12 district sites (East Hartford's policy binder is one).
The policies load via a recursive JSON API (``ContentItemSvc.asmx/GetItemList``)
that returns a folder tree; files live at the leaves with a ``DownloadLink``.
Generic across the platform — nothing about any specific district is hardcoded;
the ``Params`` blob and folder ids are read from what the page actually sent.
"""

from __future__ import annotations

import json
from typing import Any, List, Optional

from lightrag.utils import logger

from context_graph.webingest.connectors.base import Connector, download_as_data
from context_graph.webingest.fetch import FetchResult


class FinalsiteConnector(Connector):
    name = "finalsite"

    def __init__(self, *, max_files: int = 500, max_folders: int = 400) -> None:
        self._max_files = max_files
        self._max_folders = max_folders

    def detect(self, *, requests: List[Any], responses: List[Any],
               page_url: str, html: str) -> Optional[dict]:
        for req in requests:
            url = getattr(req, "url", "")
            body = getattr(req, "post_data", None)
            if "ContentItemSvc.asmx/GetItemList" in url and body and "parentId" in body:
                try:
                    parsed = json.loads(body)
                    return {"url": url, "params": parsed["Params"],
                            "root": str(parsed["parentId"])}
                except (json.JSONDecodeError, KeyError):
                    continue
        return None

    async def resolve(self, request_context: Any, template: dict,
                      *, max_files: Optional[int] = None) -> List[FetchResult]:
        cap = max_files if max_files is not None else self._max_files
        url, params, root = template["url"], template["params"], template["root"]
        files: List[FetchResult] = []
        folders = [0]
        headers = {"content-type": "application/json; charset=UTF-8"}

        async def _list(parent_id: str) -> List[dict]:
            resp = await request_context.post(
                url, data=json.dumps({"parentId": str(parent_id), "Params": params}),
                headers=headers,
            )
            data = await resp.json()
            items = (data.get("d") or {}).get("DataObject")
            if isinstance(items, dict):
                items = [items]
            return items or []

        async def _recurse(parent_id: str, depth: int) -> None:
            if folders[0] >= self._max_folders or len(files) >= cap:
                return
            folders[0] += 1
            try:
                items = await _list(parent_id)
            except Exception as e:
                logger.debug(f"finalsite: list({parent_id}) failed: {e}")
                return
            for it in items:
                if len(files) >= cap:
                    return
                dl = it.get("DownloadLink")
                is_file = it.get("Type") == "content_file" or it.get("Extension") or dl
                if is_file and dl:
                    fr = await download_as_data(request_context, dl,
                                                filename=self._filename(it))
                    if fr is not None:
                        files.append(fr)
                elif it.get("Type") == "content_folder" and depth < 8:
                    await _recurse(str(it.get("ItemId")), depth + 1)

        await _recurse(root, 0)
        logger.info(f"finalsite connector: {len(files)} file(s) from {folders[0]} folder(s)")
        return files

    @staticmethod
    def _filename(item: dict) -> str:
        name = (item.get("Name") or item.get("Title") or "document").strip()
        ext = (item.get("Extension") or "").lstrip(".")
        return f"{name}.{ext}" if ext and not name.lower().endswith("." + ext) else name
