"""Polite static page fetcher (P-web, step 3).

Async HTTP GET over ``httpx`` (already a dependency) with three manners a
well-behaved crawler must have: **robots.txt** compliance, a real **User-Agent**,
and **per-host rate limiting**. The httpx client is injected, so the whole thing
is unit-testable offline with ``httpx.MockTransport`` — no network.

JS rendering is intentionally out of scope for the MVP (static-first); a
Playwright-backed fetcher can implement the same ``fetch`` shape later.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Dict, List, Optional
import socket
from urllib import robotparser
from urllib.parse import urlsplit

from lightrag.utils import logger

from context_graph.webingest.urls import host_of, is_http, is_public_url

DEFAULT_USER_AGENT = "ContextGraphBot/1.0 (+https://github.com/dsivov/Context_Graph)"


# Content-types we ingest as data (CG's scan pipeline extracts these). Matched
# as substrings of the response content-type — extension-agnostic by design.
_DATA_CTYPES = (
    "pdf", "msword", "officedocument", "ms-excel", "ms-powerpoint",
    "application/json", "text/csv", "text/markdown", "text/plain", "rtf",
    "opendocument",
)


def classify_content_type(ctype: str) -> str:
    """Classify a content-type into 'html' | 'data' | 'other'."""
    ct = (ctype or "").lower()
    if "html" in ct or "xhtml" in ct or "xml" in ct:  # xml covers sitemaps/feeds
        return "html"
    if any(d in ct for d in _DATA_CTYPES):
        return "data"
    return "other"


@dataclass
class FetchResult:
    url: str                 # final URL (after redirects)
    status: int
    kind: str = "other"      # 'html' | 'data' | 'other'
    content_type: str = ""
    html: str = ""           # populated when kind == 'html'
    content: bytes = b""     # populated when kind == 'data'
    ok: bool = False
    reason: str = ""
    filename: str = ""       # optional human name for a data resource (from a connector)
    # Data resources captured from the page's network traffic during JS render
    # (e.g. an API/JSON the page fetched, or a PDF it loaded).
    captured: List["FetchResult"] = field(default_factory=list)


class StaticFetcher:
    """Fetches HTML pages politely. Inject ``client`` (an ``httpx.AsyncClient``)."""

    def __init__(
        self,
        client,
        *,
        user_agent: str = DEFAULT_USER_AGENT,
        timeout: float = 15.0,
        min_interval: float = 1.0,     # seconds between requests to the same host
        respect_robots: bool = True,
        max_bytes: int = 40 * 1024 * 1024,   # skip data payloads larger than this
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
        block_private_hosts: bool = True,      # SSRF guard (default on)
        host_resolver: Callable = socket.getaddrinfo,
    ) -> None:
        self._client = client
        self._ua = user_agent
        self._timeout = timeout
        self._min_interval = min_interval
        self._max_bytes = max_bytes
        self._respect_robots = respect_robots
        self._sleep = sleep
        self._clock = clock
        # SSRF guard: block URLs whose host resolves to a private/internal address.
        # On by default; offline tests that use fake hostnames disable it. The
        # resolver is injectable so a test can exercise the guard without real DNS.
        self._block_private_hosts = block_private_hosts
        self._host_resolver = host_resolver
        self._robots: Dict[str, Optional[robotparser.RobotFileParser]] = {}
        self._last_fetch: Dict[str, float] = {}

    def _ssrf_reason(self, url: str) -> Optional[str]:
        """Return a block reason if the SSRF guard rejects *url*, else None."""
        if not self._block_private_hosts:
            return None
        ok, reason = is_public_url(url, resolver=self._host_resolver)
        return None if ok else reason

    async def _robots_for(self, url: str) -> Optional[robotparser.RobotFileParser]:
        host = host_of(url)
        if host in self._robots:
            return self._robots[host]
        parts = urlsplit(url)
        robots_url = f"{parts.scheme}://{parts.netloc}/robots.txt"
        rp: Optional[robotparser.RobotFileParser] = None
        try:
            resp = await self._client.get(robots_url, timeout=self._timeout,
                                          headers={"User-Agent": self._ua})
            if 200 <= resp.status_code < 300:
                rp = robotparser.RobotFileParser()
                rp.parse(resp.text.splitlines())
        except Exception as e:  # robots unreachable → treat as allow-all
            logger.debug(f"robots.txt fetch failed for {host}: {e}")
        self._robots[host] = rp
        return rp

    async def allowed(self, url: str) -> bool:
        if not self._respect_robots:
            return True
        rp = await self._robots_for(url)
        return rp.can_fetch(self._ua, url) if rp is not None else True

    async def throttle(self, url: str) -> None:
        """Public per-host rate limit (reused by the Playwright renderer)."""
        await self._throttle(host_of(url))

    async def _throttle(self, host: str) -> None:
        last = self._last_fetch.get(host)
        if last is not None:
            wait = self._min_interval - (self._clock() - last)
            if wait > 0:
                await self._sleep(wait)
        self._last_fetch[host] = self._clock()

    async def fetch(self, url: str) -> FetchResult:
        """Fetch one URL. Never raises — failures come back as ``ok=False``."""
        if not is_http(url):
            return FetchResult(url, 0, reason="non-http URL")
        blocked = self._ssrf_reason(url)
        if blocked:
            logger.warning(f"SSRF guard blocked fetch of {url}: {blocked}")
            return FetchResult(url, 0, reason=f"blocked: {blocked}")
        if not await self.allowed(url):
            return FetchResult(url, 0, reason="blocked by robots.txt")
        await self._throttle(host_of(url))
        try:
            resp = await self._client.get(
                url, timeout=self._timeout, follow_redirects=True,
                headers={"User-Agent": self._ua},
            )
        except Exception as e:
            return FetchResult(url, 0, reason=f"fetch error: {e}")

        ctype = resp.headers.get("content-type", "").lower()
        final = str(resp.url)
        # Re-check after redirects: a public URL can 30x to an internal host.
        if final != url:
            blocked = self._ssrf_reason(final)
            if blocked:
                logger.warning(f"SSRF guard blocked redirect to {final}: {blocked}")
                return FetchResult(final, 0, reason=f"blocked redirect: {blocked}")
        if not (200 <= resp.status_code < 300):
            return FetchResult(final, resp.status_code, reason=f"HTTP {resp.status_code}")
        kind = classify_content_type(ctype)
        if kind == "html":
            return FetchResult(final, resp.status_code, kind="html",
                               content_type=ctype, html=resp.text, ok=True)
        if kind == "data":
            content = resp.content
            if len(content) > self._max_bytes:
                return FetchResult(final, resp.status_code, content_type=ctype,
                                   reason=f"too large ({len(content)} bytes)")
            return FetchResult(final, resp.status_code, kind="data",
                               content_type=ctype, content=content, ok=True)
        return FetchResult(final, resp.status_code, content_type=ctype,
                           reason=f"skip ({ctype or 'unknown'})")

    async def download(self, url: str) -> Optional[bytes]:
        """Fetch raw bytes (e.g. a PDF), honouring robots + rate limit. None on failure."""
        if not is_http(url) or self._ssrf_reason(url) or not await self.allowed(url):
            return None
        await self._throttle(host_of(url))
        try:
            resp = await self._client.get(
                url, timeout=self._timeout, follow_redirects=True,
                headers={"User-Agent": self._ua},
            )
        except Exception as e:
            logger.debug(f"download failed for {url}: {e}")
            return None
        if 200 <= resp.status_code < 300:
            return resp.content
        return None
