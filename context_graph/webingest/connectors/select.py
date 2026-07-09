"""LLM-driven connector selection.

Instead of mechanically trying every connector by config order, the LLM looks at
the *real* site's signals (its ``<meta generator>``, page markers, and the API
requests it actually made) plus each connector's self-description, and picks the
connector(s) whose platform matches. The chosen connector's deterministic
``detect()`` then extracts the platform's exact parameters and ``resolve()``
downloads the files.

If the LLM call fails, we fall back to trying all connectors (their ``detect()``
still safely gates on a real platform signature).
"""

from __future__ import annotations

import json
import re
from typing import Any, List

from context_graph.jsonio import _extract_json_object
from lightrag.utils import logger

from context_graph.webingest.connectors.base import Connector

_GENERATOR = re.compile(
    r'<meta[^>]+name=["\']generator["\'][^>]+content=["\']([^"\']+)["\']', re.IGNORECASE)
_WP_MARKER = re.compile(r'rel=["\']https://api\.w\.org/["\']', re.IGNORECASE)
# Request URLs worth showing the selector — API/data endpoints, not static assets.
_HINT = re.compile(
    r'(asmx|\.nsf|wp-json|/api/|/svc/|/rest|/jsonapi|/ajax|graphql|boarddocs|finalsite)',
    re.IGNORECASE)


def signals_from(page_url: str, html: str, requests: List[Any], responses: List[Any],
                 *, max_urls: int = 25) -> dict:
    """Compact, LLM-friendly summary of what a rendered page revealed."""
    gen = _GENERATOR.search(html or "")
    markers = []
    if _WP_MARKER.search(html or ""):
        markers.append("has api.w.org link tag (WordPress)")

    api_urls: List[str] = []
    seen = set()
    for r in requests:
        u = getattr(r, "url", "") or ""
        key = u.split("?")[0]
        if _HINT.search(u) and key not in seen:
            seen.add(key)
            api_urls.append(u[:160])
        if len(api_urls) >= max_urls:
            break

    return {
        "page_url": page_url,
        "generator": gen.group(1) if gen else None,
        "markers": markers,
        "api_request_samples": api_urls,
    }


class LLMConnectorSelector:
    """Selects which connector plugins apply to a site, using the LLM."""

    def __init__(self, llm) -> None:
        self._llm = llm

    async def select(self, signals: dict, connectors: List[Connector]) -> List[Connector]:
        described = [c for c in connectors if getattr(c, "description", "")]
        if not described:
            return list(connectors)
        manifest = [{"name": c.name, "handles": c.description} for c in described]
        prompt = (
            f"A web page was rendered. Signals:\n{json.dumps(signals, indent=2)}\n\n"
            f"Available site-technology connectors:\n{json.dumps(manifest, indent=2)}\n\n"
            f"Which connector(s) match THIS site's platform? Judge from the generator, "
            f"markers, and API request URLs. Return STRICT JSON "
            f'{{"connectors": ["name", ...], "reason": "..."}} — empty list if none clearly match.'
        )
        try:
            raw = await self._llm(
                prompt,
                system_prompt=("You identify a website's CMS/platform from its signals and "
                               "select matching connectors by name. Output strict JSON only."),
            )
        except Exception as e:
            logger.warning(f"connector selector LLM failed ({e}); trying all connectors")
            return list(connectors)

        payload = _extract_json_object(raw)
        if payload is None:
            logger.warning("connector selector: unparseable LLM output; trying all connectors")
            return list(connectors)

        names = [n for n in (payload.get("connectors") or []) if isinstance(n, str)]
        by_name = {c.name: c for c in connectors}
        chosen = [by_name[n] for n in names if n in by_name]
        logger.info(f"connector selector chose {[c.name for c in chosen] or 'none'} "
                    f"— {str(payload.get('reason', ''))[:100]}")
        return chosen   # honour the LLM (empty = it decided no connector applies)
