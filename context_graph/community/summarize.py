"""LLM summarisation of a community — a short title + a thematic summary.

The summary is what the "global" mode retrieves and reasons over, so it should name
the community's theme and its key entities/relationships. Provider-agnostic
(injected ``llm``); parses fence-tolerant JSON.
"""

from __future__ import annotations

import json
from typing import Any, Awaitable, Callable, Dict, List

from context_graph.jsonio import _extract_json_object
from lightrag.utils import logger


class CommunitySummarizer:
    def __init__(self, llm: Callable[..., Awaitable[str]], *, max_members: int = 40) -> None:
        self._llm = llm
        self._max = max_members

    def _system_prompt(self) -> str:
        return (
            "You summarise a community of related entities from a knowledge graph. "
            "Given the entities (name, type, description) that cluster together, write "
            "a concise TITLE naming the theme and a 2–4 sentence SUMMARY of what this "
            "community is collectively about — its main entities, how they relate, and "
            "why they belong together. Base it only on the given descriptions.\n\n"
            'Return STRICT JSON only: { "title": "...", "summary": "..." }'
        )

    def _user_prompt(self, members: List[Dict[str, Any]]) -> str:
        rows = [{"name": m.get("name"), "type": m.get("type") or m.get("entity_type"),
                 "description": (m.get("description") or "")[:240]}
                for m in members[: self._max]]
        return "ENTITIES:\n" + json.dumps(rows, ensure_ascii=False, indent=2) + "\n\nReturn the JSON."

    async def summarize(self, members: List[Dict[str, Any]]) -> Dict[str, str]:
        """Return ``{"title", "summary"}`` for a community; falls back gracefully."""
        names = [m.get("name") for m in members if m.get("name")]
        fallback = {
            "title": (", ".join(names[:3]) + ("…" if len(names) > 3 else "")) or "Community",
            "summary": f"A group of {len(names)} related entities: {', '.join(names[:8])}.",
        }
        if not members:
            return fallback
        try:
            raw = await self._llm(self._user_prompt(members),
                                  system_prompt=self._system_prompt())
        except Exception as e:  # pragma: no cover - never break a build
            logger.warning(f"CommunitySummarizer LLM failed: {e}")
            return fallback
        payload = _extract_json_object(raw)
        if not isinstance(payload, dict):
            return fallback
        return {
            "title": (payload.get("title") or fallback["title"]).strip(),
            "summary": (payload.get("summary") or fallback["summary"]).strip(),
        }
