"""Connector base — the pluggable interface for site-technology plugins.

A connector cracks a *platform* that hides its documents behind a JS widget or an
API, so the files never appear as ``<a href>`` links (ordinary crawling and even
JS rendering miss them). Each connector is generic across its platform — it keys
off the platform's signature, never a specific site.

Write a connector in two methods (see ``example.py`` for a copy-paste start):

* :meth:`Connector.detect` — recognise the platform from the render signals
  (a request URL pattern, a response, an HTML marker, or the page URL) and return
  a small ``template`` dict (whatever ``resolve`` needs), or ``None``.
* :meth:`Connector.resolve` — using the browser's session (``request_context``, a
  Playwright ``APIRequestContext``), replay the platform's API to enumerate and
  download the files. Return each as a data :class:`FetchResult` (set
  ``filename`` for a readable name). Respect ``max_files``.

Register the connector in ``connectors/__init__.py``'s ``DEFAULT_CONNECTORS``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, List, Optional

from lightrag.utils import logger

from context_graph.webingest.fetch import FetchResult


class Connector(ABC):
    """A pluggable resolver for documents hidden behind a site platform's API."""

    name: str = "connector"

    @abstractmethod
    def detect(self, *, requests: List[Any], responses: List[Any],
               page_url: str, html: str) -> Optional[dict]:
        """Return a replay ``template`` if this connector's platform is present, else None."""

    @abstractmethod
    async def resolve(self, request_context: Any, template: dict,
                      *, max_files: Optional[int] = None) -> List[FetchResult]:
        """Enumerate + download the platform's files as data :class:`FetchResult`s."""


async def download_as_data(request_context: Any, url: str, *, filename: str = "",
                           fallback_ct: str = "application/octet-stream") -> Optional[FetchResult]:
    """GET *url* with the browser session and wrap it as a data FetchResult (or None).

    Shared helper so connectors don't re-implement downloading. ``request_context``
    is Playwright's ``page.request`` (carries cookies/session for authed downloads).
    """
    try:
        resp = await request_context.get(url)
        if not (200 <= resp.status < 300):
            return None
        body = await resp.body()
    except Exception as e:
        logger.debug(f"connector download {url} failed: {e}")
        return None
    if not body:
        return None
    ct = resp.headers.get("content-type", fallback_ct)
    return FetchResult(url, resp.status, kind="data", content_type=ct,
                       content=body, ok=True, filename=filename)
