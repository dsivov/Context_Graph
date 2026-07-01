"""Site connectors — resolve data hidden behind JS document-management widgets.

Some platforms load their documents from a paginated/recursive JSON API rather
than as links: the page renders a viewer, and the files only appear if you
replay the API. A connector recognises such a platform from the requests a page
made during render, replays the API (reusing the browser's session), walks the
tree, and returns the real files as :class:`FetchResult` data resources.

:class:`FinalsiteConnector` handles the **Finalsite / Blackboard "Content Item"**
document container (``ContentItemSvc.asmx/GetItemList``) used by thousands of K-12
district sites — the East Hartford policy binder is one. It recurses the folder
tree by ``parentId`` and downloads each file item's ``DownloadLink``.
"""

from __future__ import annotations

import json
from typing import Any, List, Optional

from lightrag.utils import logger

from context_graph.webingest.fetch import FetchResult


class FinalsiteConnector:
    """Resolves a Finalsite/Blackboard document container into its files."""

    name = "finalsite"

    def __init__(self, *, max_files: int = 500, max_folders: int = 400) -> None:
        self._max_files = max_files
        self._max_folders = max_folders

    def detect(self, requests: List[Any]) -> Optional[dict]:
        """Find a GetItemList POST among captured requests; return a replay template."""
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
        """Recurse the folder tree and download every file. Returns data FetchResults.

        ``max_files`` (e.g. the scrape request's ``max_documents``) overrides the
        connector's own cap for this run.
        """
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
                    fr = await self._download(request_context, it, dl)
                    if fr is not None:
                        files.append(fr)
                elif it.get("Type") == "content_folder" and depth < 8:
                    await _recurse(str(it.get("ItemId")), depth + 1)

        await _recurse(root, 0)
        logger.info(f"finalsite connector: {len(files)} file(s) from {folders[0]} folder(s)")
        return files

    @staticmethod
    async def _download(request_context: Any, item: dict, dl: str) -> Optional[FetchResult]:
        try:
            resp = await request_context.get(dl)
            if not (200 <= resp.status < 300):
                return None
            body = await resp.body()
        except Exception as e:
            logger.debug(f"finalsite: download {dl} failed: {e}")
            return None
        if not body:
            return None
        ct = resp.headers.get("content-type", "application/octet-stream")
        name = (item.get("Name") or item.get("Title") or "document").strip()
        ext = (item.get("Extension") or "").lstrip(".")
        filename = f"{name}.{ext}" if ext and not name.lower().endswith("." + ext) else name
        return FetchResult(dl, resp.status, kind="data", content_type=ct,
                           content=body, ok=True, filename=filename)


# The per-run cap comes from the scrape request's max_documents (passed to
# resolve()); the constructor default is just an upper safety bound.
DEFAULT_CONNECTORS = [FinalsiteConnector()]
