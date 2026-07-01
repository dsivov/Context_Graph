"""ExampleConnector — a copy-paste TEMPLATE for adding a new platform connector.

To support a new site technology whose documents hide behind a JS widget or API:

1. Copy this file to ``connectors/<yourplatform>.py`` and rename the class.
2. Fill in ``detect()`` — recognise the platform from the render signals (a
   request URL pattern, a captured response, an HTML marker, or the page URL).
   Return a small ``template`` dict with whatever ``resolve()`` will need, or
   ``None`` if this platform isn't present.
3. Fill in ``resolve()`` — use ``request_context`` (Playwright's ``page.request``,
   which carries the browser's cookies/session) to call the platform's API,
   enumerate the files, and download each with :func:`download_as_data`. Set
   ``filename`` on each result for a readable name. Respect ``max_files``.
4. Add it to ``DEFAULT_CONNECTORS`` in ``connectors/__init__.py``.

The example below handles a hypothetical platform that lists documents at a
``/api/docs/list`` JSON endpoint returning ``{"documents":[{"url","name"}]}``.
"""

from __future__ import annotations

from typing import Any, List, Optional

from lightrag.utils import logger

from context_graph.webingest.connectors.base import Connector, download_as_data
from context_graph.webingest.fetch import FetchResult


class ExampleConnector(Connector):
    name = "example"

    def __init__(self, *, max_files: int = 500) -> None:
        self._max_files = max_files

    def detect(self, *, requests: List[Any], responses: List[Any],
               page_url: str, html: str) -> Optional[dict]:
        # Recognise the platform. Here: did the page call a docs-list endpoint?
        for r in requests:
            url = getattr(r, "url", "")
            if "/api/docs/list" in url:
                return {"list_url": url}     # whatever resolve() needs
        # Other detection options you might use instead / as well:
        #   - an HTML marker:            if 'data-my-widget' in (html or ""): ...
        #   - the page URL:              if "/documents/" in page_url: ...
        #   - a captured response body:  inspect `responses`
        return None

    async def resolve(self, request_context: Any, template: dict,
                      *, max_files: Optional[int] = None) -> List[FetchResult]:
        cap = max_files if max_files is not None else self._max_files
        files: List[FetchResult] = []
        try:
            resp = await request_context.get(template["list_url"])
            data = await resp.json()
        except Exception as e:
            logger.debug(f"example connector: list failed: {e}")
            return files
        for item in (data.get("documents") or []):
            if len(files) >= cap:
                break
            url = item.get("url")
            if not url:
                continue
            fr = await download_as_data(request_context, url, filename=item.get("name", ""))
            if fr is not None:
                files.append(fr)
        logger.info(f"example connector: {len(files)} document(s)")
        return files
