"""Same-domain site crawler (P-web, step 4).

Breadth-first crawl from a seed URL, staying on the same host, bounded by depth
and page caps, de-duplicating by normalised URL, optionally seeded from
``sitemap.xml``. Fetching + politeness live in :class:`StaticFetcher`; this layer
is pure scope/queue logic and is unit-testable with an injected fetcher.
"""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, List, Tuple
from urllib.parse import urlsplit

from lightrag.utils import logger

from context_graph.webingest.clean import CleanPage, extract_main_text
from context_graph.webingest.fetch import StaticFetcher
from context_graph.webingest.urls import is_http, normalize_url, same_site

_LOC_RE = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.IGNORECASE)

# Obvious non-content assets we never enqueue (cheap pre-filter — everything
# else is fetched and classified by CONTENT-TYPE, not by extension, so any data
# format is discovered generically).
_ASSET_EXTS = (
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".ico", ".bmp",
    ".css", ".js", ".mjs", ".woff", ".woff2", ".ttf", ".eot", ".map",
    ".mp4", ".webm", ".mov", ".mp3", ".wav", ".zip", ".gz", ".tar",
)


def is_asset(url: str) -> bool:
    """True for obvious static assets we should not enqueue."""
    return urlsplit(url).path.lower().endswith(_ASSET_EXTS)


@dataclass
class SiteCrawler:
    """Yields :class:`CleanPage` objects for a site, honouring scope + caps."""

    fetcher: StaticFetcher
    max_pages: int = 50
    max_depth: int = 2
    same_domain: bool = True
    use_sitemap: bool = True

    max_documents: int = 200

    skipped: List[Tuple[str, str]] = field(default_factory=list)   # (url, reason)
    documents: List[Any] = field(default_factory=list)             # data FetchResults
    fetched: int = 0

    async def _sitemap_seeds(self, seed_url: str) -> List[str]:
        parts = urlsplit(seed_url)
        sm_url = f"{parts.scheme}://{parts.netloc}/sitemap.xml"
        res = await self.fetcher.fetch(sm_url)
        if not res.ok and not res.html:
            return []
        locs = [u for u in _LOC_RE.findall(res.html or "") if is_http(u)]
        if locs:
            logger.info(f"sitemap.xml: {len(locs)} URL(s) discovered")
        return locs

    def _in_scope(self, url: str, seed_url: str) -> bool:
        return is_http(url) and (not self.same_domain or same_site(url, seed_url))

    async def crawl(self, seed_url: str) -> AsyncIterator[CleanPage]:
        self.skipped = []
        self.documents = []
        self.fetched = 0
        seen = set()
        doc_seen = set()
        queue: deque = deque()

        def enqueue(url: str, depth: int) -> None:
            n = normalize_url(url)
            if n not in seen and self._in_scope(url, seed_url):
                seen.add(n)
                queue.append((url, depth))

        def note_links(links: List[str], depth: int) -> None:
            # Enqueue in-scope, non-asset links. We do NOT classify by extension —
            # each link is fetched and classified by content-type (html vs data).
            if depth >= self.max_depth:
                return
            for link in links:
                if not is_asset(link):
                    enqueue(link, depth + 1)

        enqueue(seed_url, 0)
        if self.use_sitemap:
            for u in await self._sitemap_seeds(seed_url):
                enqueue(u, 0)

        yielded = 0
        fetch_cap = (self.max_pages + self.max_documents) * 5
        while queue and self.fetched < fetch_cap:
            if yielded >= self.max_pages and len(self.documents) >= self.max_documents:
                break
            url, depth = queue.popleft()
            res = await self.fetcher.fetch(url)
            self.fetched += 1
            seen.add(normalize_url(res.url))

            if not res.ok:
                self.skipped.append((url, res.reason))
                continue

            if res.kind == "data":
                if url not in doc_seen and len(self.documents) < self.max_documents:
                    doc_seen.add(url)
                    self.documents.append(res)
                continue

            # Data payloads captured from the page's network traffic during JS
            # render (e.g. an API/JSON the widget fetched) — collect them too.
            for cap in getattr(res, "captured", None) or []:
                if cap.url not in doc_seen and len(self.documents) < self.max_documents:
                    doc_seen.add(cap.url)
                    self.documents.append(cap)

            page = extract_main_text(res.html, res.url)
            if page.is_empty:
                self.skipped.append((res.url, "empty content"))
            elif yielded < self.max_pages:
                yielded += 1
                yield page
            # Keep discovering links (pages AND data resources) even past
            # max_pages, so we find data sources across the site.
            note_links(page.links, depth)

        if queue:
            logger.info(f"crawl stopped with {len(queue)} URL(s) unvisited "
                        f"({yielded} pages, {len(self.documents)} documents)")
