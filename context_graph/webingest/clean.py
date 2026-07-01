"""HTML → clean main text + discovered links (P-web, step 2).

The single biggest lever on downstream extraction quality is stripping the
boilerplate (nav, header, footer, scripts) and keeping the readable main
content. This uses ``lxml`` (already a dependency) with a pragmatic heuristic —
good enough to feed CG's ingestion. A readability-grade extractor can be swapped
in later behind the same :class:`CleanPage` interface.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List

import lxml.html

from context_graph.webingest.urls import is_http, resolve

# Structural / non-content tags whose subtrees we drop before reading text.
_NOISE_TAGS = (
    "script", "style", "noscript", "template", "svg", "iframe", "form",
    "nav", "header", "footer", "aside",
)
# Block tags we read text from, in document order, to preserve paragraphing.
_BLOCK_TAGS = ("h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "blockquote",
               "pre", "td", "th", "dd", "dt", "figcaption")

_WS = re.compile(r"[ \t\r\f\v]+")
_MULTINL = re.compile(r"\n{3,}")


@dataclass
class CleanPage:
    """The readable content extracted from one HTML page."""

    url: str
    title: str = ""
    text: str = ""
    links: List[str] = field(default_factory=list)   # absolute, http(s), deduped

    @property
    def is_empty(self) -> bool:
        return not self.text.strip()


def _norm(s: str) -> str:
    return _WS.sub(" ", s).strip()


def extract_main_text(html: str, base_url: str) -> CleanPage:
    """Parse *html* and return a :class:`CleanPage` (title, main text, links)."""
    if not html or not html.strip():
        return CleanPage(url=base_url)
    try:
        doc = lxml.html.fromstring(html)
    except (ValueError, lxml.etree.ParserError):
        return CleanPage(url=base_url)

    # Collect links before we prune noise (nav links can still be crawl targets).
    links: List[str] = []
    seen = set()
    for a in doc.iter("a"):
        href = a.get("href")
        if not href:
            continue
        absu = resolve(base_url, href)
        if is_http(absu) and absu not in seen:
            seen.add(absu)
            links.append(absu)

    title = ""
    t = doc.find(".//title")
    if t is not None and t.text_content().strip():
        title = _norm(t.text_content())

    # Prune boilerplate subtrees.
    for el in doc.iter():
        if el.tag in _NOISE_TAGS:
            el.drop_tree()

    # Prefer <main>/<article> if present, else fall back to the whole document.
    root = None
    for sel in ("main", "article"):
        found = doc.find(f".//{sel}")
        if found is not None:
            root = found
            break
    if root is None:
        root = doc

    blocks: List[str] = []
    for el in root.iter(*_BLOCK_TAGS):
        txt = _norm(el.text_content())
        if txt:
            blocks.append(txt)
    if not blocks:  # nothing structured — fall back to the raw text
        blocks = [_norm(root.text_content())]

    text = _MULTINL.sub("\n\n", "\n\n".join(blocks)).strip()
    if title and not text.startswith(title):
        text = f"{title}\n\n{text}"
    return CleanPage(url=base_url, title=title, text=text, links=links)
