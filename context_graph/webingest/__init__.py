"""Context Graph web-ingest — a smart, polite site → CG acquisition front-end.

Crawls a website (same-domain, static-first), cleans each page to its readable
main content, and feeds it into CG's existing ingestion so it becomes
``(h, r, t, rc)`` with the page URL as provenance. See the discussion in
``docs/CLOSING_THE_GAPS.html`` / the project overview.

Acquisition primitives (this step):

    from context_graph.webingest import StaticFetcher, extract_main_text, same_site
"""

from context_graph.webingest.urls import (
    is_http,
    host_of,
    normalize_url,
    resolve,
    same_site,
)
from context_graph.webingest.clean import CleanPage, extract_main_text
from context_graph.webingest.fetch import (
    StaticFetcher,
    FetchResult,
    DEFAULT_USER_AGENT,
)
from context_graph.webingest.crawl import SiteCrawler
from context_graph.webingest.ingest import WebIngestor, IngestSummary

__all__ = [
    "is_http",
    "host_of",
    "normalize_url",
    "resolve",
    "same_site",
    "CleanPage",
    "extract_main_text",
    "StaticFetcher",
    "FetchResult",
    "DEFAULT_USER_AGENT",
    "SiteCrawler",
    "WebIngestor",
    "IngestSummary",
]
