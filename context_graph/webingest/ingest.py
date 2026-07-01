"""Web ingestor — crawl a site and feed it into CG (P-web, step 5).

Ties the acquisition layer to CG's existing ingestion: crawl → clean → hand each
page's text to ``rag.ainsert`` with the page **URL as the file path**, so the
URL becomes ``rc.provenance`` on everything extracted. Representation (turning
prose into ``(h, r, t, rc)``) stays entirely in CG's pipeline — this module is
just the front door.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import unquote, urlsplit

from lightrag.utils import logger

from context_graph.webingest.crawl import SiteCrawler
from context_graph.webingest.fetch import DEFAULT_USER_AGENT, StaticFetcher

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")

# Content-type → binary document extension (CG's scan extracts these natively).
_CT_EXT = {
    "pdf": ".pdf", "msword": ".doc", "wordprocessingml": ".docx",
    "presentationml": ".pptx", "spreadsheetml": ".xlsx",
    "ms-excel": ".xls", "ms-powerpoint": ".ppt", "rtf": ".rtf",
    "opendocument": ".odt",
}


def _ext_for_content_type(ct: str) -> str:
    """Binary-doc extension for a content-type, else '.txt' (ingest as text)."""
    ct = (ct or "").lower()
    for key, ext in _CT_EXT.items():
        if key in ct:
            return ext
    return ".txt"   # json/csv/plain/xml/unknown → CG scan ingests as text


def _filename_for(url: str, content_type: str = "") -> str:
    """A safe, collision-resistant filename with a content-type-appropriate ext."""
    base = os.path.basename(urlsplit(unquote(url)).path) or "document"
    base = _SAFE.sub("_", base).strip("_") or "document"
    root, url_ext = os.path.splitext(base)
    ct_ext = _ext_for_content_type(content_type)
    # Prefer a real binary-doc ext from content-type; else keep the URL's ext; else .txt
    ext = ct_ext if ct_ext != ".txt" else (url_ext or ".txt")
    return f"{hashlib.md5(url.encode()).hexdigest()[:8]}_{root or 'document'}{ext}"


@dataclass
class IngestSummary:
    """What a site-ingest run did."""

    seed: str
    ingested: int = 0                                   # HTML pages ainserted
    urls: List[str] = field(default_factory=list)
    documents: List[Dict[str, str]] = field(default_factory=list)  # saved data files
    skipped: List[Dict[str, str]] = field(default_factory=list)
    track_ids: List[str] = field(default_factory=list)
    analyst: Optional[Dict[str, Any]] = None            # LLM relevance-filter summary

    def to_dict(self) -> Dict[str, Any]:
        return {
            "seed": self.seed,
            "ingested": self.ingested,
            "urls": self.urls,
            "documents": self.documents,
            "skipped": self.skipped,
            "track_ids": self.track_ids,
            "analyst": self.analyst,
        }


class WebIngestor:
    """Crawls a site and inserts its content into a ContextGraph/LightRAG rag."""

    def __init__(self, rag: Any) -> None:
        self._rag = rag

    async def ingest_site(
        self,
        seed_url: str,
        *,
        max_pages: int = 50,
        max_depth: int = 2,
        same_domain: bool = True,
        respect_robots: bool = True,
        use_sitemap: bool = True,
        min_interval: float = 1.0,
        max_documents: int = 200,
        render_js: bool = False,
        user_agent: str = DEFAULT_USER_AGENT,
        download_dir: Optional[Path] = None,
        analyst: Any = None,
        fetcher: Optional[StaticFetcher] = None,
        client: Any = None,
    ) -> IngestSummary:
        """Crawl *seed_url*: HTML pages → ``ainsert``; data files → saved to
        ``download_dir`` for CG's scan pipeline. Returns an :class:`IngestSummary`.

        Injecting ``fetcher`` (or a ``client``) keeps this offline-testable; in
        production it builds its own ``httpx.AsyncClient`` and closes it after.
        """
        own_client = None
        own_render = None
        if fetcher is None:
            if client is None:
                import httpx
                own_client = httpx.AsyncClient()
                client = own_client
            static = StaticFetcher(
                client, respect_robots=respect_robots,
                min_interval=min_interval, user_agent=user_agent,
            )
            if render_js:
                from context_graph.webingest.render import PlaywrightFetcher
                fetcher = own_render = PlaywrightFetcher(static, max_documents=max_documents)
            else:
                fetcher = static

        summary = IngestSummary(seed=seed_url)
        try:
            crawler = SiteCrawler(
                fetcher, max_pages=max_pages, max_depth=max_depth,
                same_domain=same_domain, use_sitemap=use_sitemap,
                max_documents=max_documents,
            )
            pages = []
            async for page in crawler.crawl(seed_url):
                pages.append(page)
            summary.skipped = [{"url": u, "reason": r} for u, r in crawler.skipped]

            # HTML pages → CG ingestion; each page URL becomes rc.provenance.
            if pages:
                track_id = await self._rag.ainsert(
                    [p.text for p in pages],
                    file_paths=[p.url for p in pages],
                )
                summary.ingested = len(pages)
                summary.urls = [p.url for p in pages]
                if track_id:
                    summary.track_ids.append(track_id)

            # Data resources (captured API responses + data links).
            documents = list(crawler.documents)

            # LLM analyst: keep only relevant resources + fetch document URLs it
            # extracted from inside them (e.g. PDF links listed in an API JSON).
            if analyst is not None and documents:
                result = await analyst.analyze(documents)
                keep = set(result.keep)
                documents = [d for d in documents if d.url in keep]
                for u in result.extracted_urls:
                    if len(documents) >= max_documents:
                        break
                    r = await fetcher.fetch(u)
                    if r.ok and r.kind == "data":
                        documents.append(r)
                summary.analyst = {
                    "kept": len(documents),
                    "extracted_urls": len(result.extracted_urls),
                    "ok": result.ok,
                }

            # Data files (pdf/doc/json/…) → save to the input dir for CG's scan.
            if documents and download_dir is not None:
                summary.documents = self._save_documents(documents, download_dir)

            logger.info(
                f"web-ingest '{seed_url}': {summary.ingested} page(s), "
                f"{len(summary.documents)} document(s) saved, {len(summary.skipped)} skipped"
            )
            return summary
        finally:
            if own_render is not None:
                await own_render.aclose()
            if own_client is not None:
                await own_client.aclose()

    @staticmethod
    def _save_documents(documents: list, download_dir: Path) -> List[Dict[str, str]]:
        """Write fetched data payloads to *download_dir*; returns [{url, file}]."""
        download_dir = Path(download_dir)
        download_dir.mkdir(parents=True, exist_ok=True)
        saved: List[Dict[str, str]] = []
        for res in documents:
            if not getattr(res, "content", b""):
                continue
            hint = getattr(res, "filename", "")
            if hint:
                base = _SAFE.sub("_", hint).strip("_") or "document"
                if not os.path.splitext(base)[1]:
                    base += ".pdf"
                fname = f"{hashlib.md5(res.url.encode()).hexdigest()[:8]}_{base}"
            else:
                fname = _filename_for(res.url, getattr(res, "content_type", ""))
            path = download_dir / fname
            try:
                path.write_bytes(res.content)
                saved.append({"url": res.url, "file": fname})
            except OSError as e:
                logger.warning(f"could not save {res.url} → {path}: {e}")
        return saved
