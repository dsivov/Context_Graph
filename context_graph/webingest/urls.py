"""URL helpers for the web-ingest crawler (P-web, step 1).

Small, dependency-free utilities for normalising URLs, resolving links, and
deciding whether a URL is on the same site as the seed. Kept separate so the
crawler's scope logic is unit-testable in isolation.
"""

from __future__ import annotations

from urllib.parse import urljoin, urldefrag, urlsplit, urlunsplit

_DEFAULT_PORTS = {"http": "80", "https": "443"}


def is_http(url: str) -> bool:
    """True for http/https URLs (skips mailto:, tel:, javascript:, data:, …)."""
    return urlsplit(url).scheme in ("http", "https")


def host_of(url: str) -> str:
    """Lower-cased hostname without a leading ``www.`` or default port."""
    netloc = urlsplit(url).netloc.lower()
    if "@" in netloc:
        netloc = netloc.rsplit("@", 1)[1]
    host = netloc.split(":", 1)[0]
    return host[4:] if host.startswith("www.") else host


def normalize_url(url: str) -> str:
    """Canonicalise a URL for dedup: drop fragment, default port, trailing slash.

    Query strings are preserved (they often select content); only the fragment
    and cosmetic differences are removed.
    """
    url, _ = urldefrag(url)
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    host = parts.hostname or ""
    netloc = host.lower()
    if parts.port and str(parts.port) != _DEFAULT_PORTS.get(scheme):
        netloc = f"{netloc}:{parts.port}"
    path = parts.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    return urlunsplit((scheme, netloc, path, parts.query, ""))


def resolve(base_url: str, href: str) -> str:
    """Resolve a possibly-relative ``href`` against ``base_url`` (fragment dropped)."""
    return urldefrag(urljoin(base_url, href))[0]


def same_site(url: str, seed_url: str) -> bool:
    """True if ``url`` is on the same registered host as ``seed_url`` (www-insensitive)."""
    return is_http(url) and host_of(url) == host_of(seed_url)
