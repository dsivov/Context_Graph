"""LLM site analyst — relevance + document-URL extraction (P-web, phase 2).

Deterministic capture finds *everything* a page fetches (including analytics and
tracking noise). The analyst is the brain that tells signal from noise and pulls
the real document URLs out of API responses — so the graph fills with what
matters, on any site.

Given the resources a crawl collected (captured API responses + data links, each
with a URL, content-type, and a text sample), the analyst returns:

* which resources to **keep** (relevant data/documents) vs drop (noise), and
* **document URLs referenced inside** those resources (e.g. the PDF links listed
  in a policy-catalog JSON) — to fetch and ingest.

Provider-agnostic: uses the workspace's ``llm_model_func`` (no external agent
framework), exactly like ``context_graph.rules.RuleAuthor``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List

from context_graph.jsonio import _extract_json_object
from lightrag.utils import logger

LLMFunc = Callable[..., Awaitable[str]]

# content-types whose bytes are worth sampling as text for the LLM
_TEXT_CTYPES = ("json", "csv", "text", "xml", "javascript")

# (1) Deterministic noise: strong tracking/CDN/telemetry signals + tiny bodies.
# Conservative on purpose — anything ambiguous still goes to the LLM.
_MIN_BODY = 40   # bytes; smaller payloads are beacons/pings, not content
_NOISE_RE = re.compile(
    r"(/cdn-cgi/|-1\.2\.1\.1-|google-?analytics|googletagmanager|googletag"
    r"|doubleclick|/gtag|/gtm\.|/collect\b|hotjar|segment\.io|/telemetry|/beacon)",
    re.IGNORECASE,
)


def _looks_like_noise(res: Any) -> bool:
    """True for obvious tracking/analytics/CDN payloads (dropped before the LLM)."""
    body = getattr(res, "content", b"") or b""
    if len(body) < _MIN_BODY:
        return True
    return bool(_NOISE_RE.search(getattr(res, "url", "") or ""))


# Binary document types are content by definition — auto-kept, never LLM-judged
# (they carry no text sample to judge, and a connector fetched them deliberately).
_DOC_CTYPES = ("pdf", "msword", "officedocument", "ms-excel", "ms-powerpoint",
               "rtf", "opendocument")


def _is_binary_doc(res: Any) -> bool:
    ct = (getattr(res, "content_type", "") or "").lower()
    return any(d in ct for d in _DOC_CTYPES)


@dataclass
class AnalysisResult:
    keep: List[str] = field(default_factory=list)            # resource URLs to ingest
    extracted_urls: List[str] = field(default_factory=list)  # doc URLs found inside
    decisions: List[Dict[str, Any]] = field(default_factory=list)
    dropped_noise: int = 0                                    # pre-filtered before the LLM
    ok: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {"keep": self.keep, "extracted_urls": self.extracted_urls,
                "decisions": self.decisions, "dropped_noise": self.dropped_noise,
                "ok": self.ok}


def _sample(res: Any, limit: int) -> str:
    """A short text sample of a resource for the LLM (empty for binary docs)."""
    ct = (getattr(res, "content_type", "") or "").lower()
    if not any(t in ct for t in _TEXT_CTYPES):
        return ""   # pdf/office/binary — the LLM judges by URL + content-type
    body = getattr(res, "content", b"") or b""
    try:
        return body[:limit].decode("utf-8", errors="ignore")
    except Exception:
        return ""


class SiteAnalyst:
    """Filters crawled resources for relevance and extracts document URLs."""

    def __init__(self, llm: LLMFunc, *, sample_chars: int = 2500,
                 max_resources: int = 40) -> None:
        self._llm = llm
        self._sample_chars = sample_chars          # (3) larger API-response samples
        self._max_resources = max_resources

    def _system_prompt(self) -> str:
        return (
            "You filter web resources before they are ingested into a knowledge "
            "base, and you extract document links.\n\n"
            "You are given RESOURCES a web page fetched (URL, content-type, and a "
            "text sample). Decide which hold RELEVANT content — documents, policy "
            "or catalog data, articles, records — versus NOISE: analytics, tracking "
            "pixels, session/auth tokens, ads, CDN/bot-management, or empty/boilerplate.\n\n"
            "Also EXTRACT any document or file URLs referenced *inside* the samples "
            "(e.g. links to PDFs/Word/Excel files listed in an API/JSON response). "
            "Return absolute URLs where possible.\n\n"
            "Return STRICT JSON only:\n"
            '{ "decisions": [ {"url": "...", "keep": true, "reason": "..."} ], '
            '"document_urls": ["https://..."] }'
        )

    def _user_prompt(self, manifest: List[Dict[str, Any]]) -> str:
        return ("RESOURCES:\n" + json.dumps(manifest, ensure_ascii=False, indent=2)
                + "\n\nReturn the JSON.")

    async def analyze(self, resources: List[Any]) -> AnalysisResult:
        """Classify *resources* (data FetchResults) and extract document URLs."""
        if not resources:
            return AnalysisResult()

        # Binary documents (pdf/office) are content — keep them without the LLM.
        auto_keep = [r.url for r in resources if _is_binary_doc(r)]
        rest = [r for r in resources if not _is_binary_doc(r)]

        # (1) Drop obvious noise deterministically — never send it to the LLM.
        candidates = [r for r in rest if not _looks_like_noise(r)]
        dropped = len(rest) - len(candidates)
        if not candidates:
            logger.info(f"SiteAnalyst: kept {len(auto_keep)} document(s), "
                        f"{dropped} noise dropped, no text candidates")
            return AnalysisResult(keep=list(dict.fromkeys(auto_keep)),
                                  dropped_noise=dropped, ok=True)

        subset = candidates[: self._max_resources]
        manifest = [
            {"url": r.url, "content_type": getattr(r, "content_type", ""),
             "sample": _sample(r, self._sample_chars)}
            for r in subset
        ]
        # Default-drop is the analyst's contract: a resource is ingested only if it
        # is a binary document (auto-kept above) or the LLM explicitly keeps it. When
        # the LLM can't be consulted (call failed or unparseable output) the text
        # candidates are UNJUDGED — not approved — so they are dropped just as an
        # unmentioned candidate is on the success path. Keeping them would let noise
        # into the graph on a transient LLM hiccup (the inconsistency this avoids).
        # ok=False surfaces the degradation so the caller can re-run.
        def _degraded() -> AnalysisResult:
            return AnalysisResult(
                keep=list(dict.fromkeys(auto_keep)), dropped_noise=dropped, ok=False
            )

        try:
            raw = await self._llm(self._user_prompt(manifest),
                                  system_prompt=self._system_prompt())
        except Exception as e:
            logger.warning(
                f"SiteAnalyst LLM call failed: {e}; default-drop "
                f"({len(candidates)} unjudged candidate(s) skipped, {len(auto_keep)} document(s) kept)"
            )
            return _degraded()

        payload = _extract_json_object(raw)
        if payload is None:
            logger.warning(
                "SiteAnalyst: could not parse LLM JSON; default-drop "
                f"({len(candidates)} unjudged candidate(s) skipped)"
            )
            return _degraded()

        decisions = payload.get("decisions") or []
        # (2) Default-drop: only resources the LLM explicitly keeps are ingested
        # (binary documents are always kept, above).
        keep = auto_keep + [d["url"] for d in decisions
                            if isinstance(d, dict) and d.get("url") and d.get("keep")]
        extracted = [u for u in (payload.get("document_urls") or []) if isinstance(u, str)]
        logger.info(f"SiteAnalyst: kept {len(set(keep))}/{len(subset)} candidate(s), "
                    f"dropped {dropped} noise, extracted {len(extracted)} document URL(s)")
        return AnalysisResult(keep=list(dict.fromkeys(keep)),
                              extracted_urls=list(dict.fromkeys(extracted)),
                              decisions=decisions, dropped_noise=dropped)
