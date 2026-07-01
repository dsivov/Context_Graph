"""WordPress REST API connector.

WordPress powers a huge share of the web (incl. many public-sector/education
sites). Every WordPress site exposes a REST API at ``/wp-json/`` — the media
library (``/wp-json/wp/v2/media``) lists every uploaded file (PDFs, Office docs)
with a direct ``source_url``. So instead of hunting for links, we page the media
endpoint and download the document files. Generic across all WordPress sites.
"""

from __future__ import annotations

import re
from typing import Any, List, Optional

from lightrag.utils import logger

from context_graph.webingest.connectors.base import Connector, download_as_data
from context_graph.webingest.fetch import FetchResult

# WordPress advertises its API base in a <link rel="https://api.w.org/"> tag.
_WP_LINK = re.compile(
    r'<link[^>]+rel=["\']https://api\.w\.org/["\'][^>]+href=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
_DOC_MIME = ("pdf", "msword", "officedocument", "ms-excel", "ms-powerpoint", "rtf")


class WordPressConnector(Connector):
    name = "wordpress"

    def __init__(self, *, max_files: int = 500, per_page: int = 100,
                 max_pages: int = 50) -> None:
        self._max_files = max_files
        self._per_page = per_page
        self._max_pages = max_pages

    def detect(self, *, requests: List[Any], responses: List[Any],
               page_url: str, html: str) -> Optional[dict]:
        # 1) the rel="https://api.w.org/" <link> tag (present on virtually all WP pages)
        m = _WP_LINK.search(html or "")
        base = m.group(1).rstrip("/") if m else None
        # 2) fall back to any /wp-json/ request the page made
        if not base:
            for r in requests:
                u = getattr(r, "url", "")
                if "/wp-json/" in u:
                    base = u.split("/wp-json/")[0] + "/wp-json"
                    break
        if not base:
            return None
        return {"media": f"{base}/wp/v2/media"}

    async def resolve(self, request_context: Any, template: dict,
                      *, max_files: Optional[int] = None) -> List[FetchResult]:
        cap = max_files if max_files is not None else self._max_files
        media = template["media"]
        files: List[FetchResult] = []
        page = 1
        while len(files) < cap and page <= self._max_pages:
            url = (f"{media}?per_page={self._per_page}&page={page}"
                   f"&_fields=source_url,mime_type,title")
            try:
                resp = await request_context.get(url)
                if resp.status >= 400:
                    break
                items = await resp.json()
            except Exception as e:
                logger.debug(f"wordpress: media page {page} failed: {e}")
                break
            if not isinstance(items, list) or not items:
                break
            for it in items:
                if len(files) >= cap:
                    break
                mime = (it.get("mime_type") or "").lower()
                src = it.get("source_url")
                if src and any(d in mime for d in _DOC_MIME):
                    title = (it.get("title") or {}).get("rendered") or ""
                    fr = await download_as_data(request_context, src, filename=title)
                    if fr is not None:
                        files.append(fr)
            page += 1
        logger.info(f"wordpress connector: {len(files)} document(s)")
        return files
