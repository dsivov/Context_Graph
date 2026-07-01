"""Playwright JS-rendering fetcher (P-web, phase 1b).

Implements the same ``fetch(url) -> FetchResult`` interface as
:class:`StaticFetcher`, but renders HTML pages in a headless Chromium so
JavaScript-injected content and links become visible (e.g. an embedded document
viewer). It **wraps** a :class:`StaticFetcher` and reuses it for robots +
rate-limiting and for fetching non-HTML data payloads (a browser is only needed
to render HTML) — so a JS crawl still downloads PDFs/docs the same way.

The navigation step is injectable (``navigate=``) so the routing logic is
unit-testable without launching a real browser; the default uses Playwright.
Requires ``pip install playwright && playwright install chromium``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, List, Optional

from lightrag.utils import logger

from context_graph.webingest.fetch import (
    FetchResult,
    StaticFetcher,
    classify_content_type,
)
from context_graph.webingest.urls import is_http


@dataclass
class NavResult:
    status: int
    content_type: str
    final_url: str
    html: str
    captured: List[FetchResult] = field(default_factory=list)


class PlaywrightFetcher:
    """Renders HTML with a headless browser; delegates everything else to static."""

    def __init__(
        self,
        static: StaticFetcher,
        *,
        navigate: Optional[Callable[[str], Awaitable[Optional[NavResult]]]] = None,
        wait_until: str = "load",          # 'networkidle' hangs on sites with live connections
        nav_timeout_ms: int = 20000,
        settle_ms: int = 1500,             # let JS run after load before reading the DOM
        capture_responses: bool = True,    # collect data payloads from network traffic
        max_captures: int = 100,
        max_bytes: int = 40 * 1024 * 1024,
        connectors: Any = None,            # pluggable resolvers for API-driven widgets
        connector_selector: Any = None,    # LLM picks which connectors apply to the site
        max_documents: Optional[int] = None,   # per-run cap passed to connectors
    ) -> None:
        self._static = static
        self._navigate = navigate
        self._wait_until = wait_until
        self._nav_timeout = nav_timeout_ms
        self._settle_ms = settle_ms
        self._capture = capture_responses
        self._max_captures = max_captures
        self._max_bytes = max_bytes
        self._max_documents = max_documents
        if connectors is None:
            from context_graph.webingest.connectors import DEFAULT_CONNECTORS
            connectors = DEFAULT_CONNECTORS
        self._connectors = connectors
        self._connector_selector = connector_selector
        self._pw = None
        self._browser = None
        self._ctx = None

    async def _ensure_browser(self) -> None:
        if self._browser is None:
            from playwright.async_api import async_playwright  # lazy: heavy import

            self._pw = await async_playwright().start()
            self._browser = await self._pw.chromium.launch(headless=True)
            self._ctx = await self._browser.new_context(
                user_agent=self._static._ua  # match the polite UA
            )
            logger.info("Playwright Chromium launched for JS rendering")

    async def _default_navigate(self, url: str) -> Optional[NavResult]:
        await self._ensure_browser()
        page = await self._ctx.new_page()
        responses = []
        requests = []
        if self._capture:
            page.on("response", lambda r: responses.append(r))
            page.on("request", lambda r: requests.append(r))
        try:
            resp = await page.goto(url, wait_until=self._wait_until, timeout=self._nav_timeout)
            if self._settle_ms:
                await page.wait_for_timeout(self._settle_ms)   # let JS widgets populate
            ctype = resp.headers.get("content-type", "") if resp is not None else ""
            html = await page.content()
            captured = await self._read_captures(responses) if self._capture else []
            # Connectors: resolve data hidden behind JS document widgets (e.g. a
            # Finalsite container that lists files only via a recursive API).
            # The LLM selector picks which connector(s) fit THIS real site; without
            # a selector we try all (each connector's detect() safely self-gates).
            chosen = self._connectors
            if self._connector_selector is not None:
                from context_graph.webingest.connectors.select import signals_from
                signals = signals_from(page.url, html, requests, responses)
                try:
                    chosen = await self._connector_selector.select(signals, self._connectors)
                except Exception as e:
                    logger.warning(f"connector selector failed: {e}; trying all")
                    chosen = self._connectors
            for conn in chosen:
                template = conn.detect(requests=requests, responses=responses,
                                       page_url=page.url, html=html)
                if template:
                    try:
                        captured.extend(await conn.resolve(
                            page.request, template, max_files=self._max_documents))
                    except Exception as e:
                        logger.warning(f"connector '{conn.name}' failed: {e}")
            return NavResult(resp.status if resp else 0, ctype, page.url, html, captured)
        finally:
            await page.close()

    async def _read_captures(self, responses) -> List[FetchResult]:
        """Turn captured data-type network responses into data FetchResults."""
        out: List[FetchResult] = []
        seen = set()
        for r in responses:
            if len(out) >= self._max_captures:
                break
            ct = r.headers.get("content-type", "")
            if classify_content_type(ct) != "data" or r.url in seen:
                continue
            try:
                body = await r.body()
            except Exception:
                continue
            if 0 < len(body) <= self._max_bytes:
                seen.add(r.url)
                out.append(FetchResult(r.url, r.status, kind="data",
                                       content_type=ct, content=body, ok=True))
        return out

    async def fetch(self, url: str) -> FetchResult:
        if not is_http(url):
            return FetchResult(url, 0, reason="non-http URL")
        if not await self._static.allowed(url):
            return FetchResult(url, 0, reason="blocked by robots.txt")
        await self._static.throttle(url)

        navigate = self._navigate or self._default_navigate
        try:
            nav = await navigate(url)
        except Exception as e:
            return FetchResult(url, 0, reason=f"render error: {e}")
        if nav is None:
            return FetchResult(url, 0, reason="render failed")

        if classify_content_type(nav.content_type) == "html":
            return FetchResult(nav.final_url, nav.status, kind="html",
                               content_type=nav.content_type, html=nav.html, ok=True,
                               captured=nav.captured)
        # The browser hit a non-HTML resource (PDF/doc/…) — fetch its bytes via static.
        return await self._static.fetch(url)

    async def aclose(self) -> None:
        try:
            if self._ctx is not None:
                await self._ctx.close()
            if self._browser is not None:
                await self._browser.close()
            if self._pw is not None:
                await self._pw.stop()
        except Exception as e:  # pragma: no cover - best-effort teardown
            logger.debug(f"Playwright teardown: {e}")
