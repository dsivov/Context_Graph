"""Layer 3 — async LLM isolate rescue (asserted edges only, D14).

For each isolated (degree-0) node, embedding proposes semantically-near existing
nodes as candidates, and the LLM adds only the relationships their descriptions
clearly support (membership, authorship, location, causation, part-of, use, …).
Conservative: if nothing clearly relates, the node stays isolated — we never invent
edges or add mechanical similarity links.

Provider-agnostic and dependency-injected (candidate finder, edge adder, LLM), so it
is unit-testable offline and reusable behind an API or a background job.
"""

from __future__ import annotations

import json
from typing import Any, Awaitable, Callable, Dict, List, Optional

from context_graph.jsonio import _extract_json_object
from lightrag.utils import logger

# name, description -> [{"name","description"}, ...] nearest existing nodes
FindCandidates = Callable[[str, str], Awaitable[List[Dict[str, Any]]]]
# src, tgt, keywords, description -> None
AddEdge = Callable[[str, str, str, str], Awaitable[None]]


class IsolateRescue:
    def __init__(
        self, llm: Callable[..., Awaitable[str]], *,
        find_candidates: FindCandidates, add_edge: AddEdge,
        max_candidates: int = 8,
    ) -> None:
        self._llm = llm
        self._find = find_candidates
        self._add = add_edge
        self._maxc = max(1, max_candidates)

    def _system_prompt(self) -> str:
        return (
            "You reconnect an isolated knowledge-graph entity. Given an ISOLATED "
            "entity and CANDIDATE entities (each with a description), identify only "
            "relationships the descriptions CLEARLY support — membership, authorship, "
            "location, causation, part-of, use, ownership, etc. Be conservative: if no "
            "candidate has a clear relationship, return an empty list. Never invent "
            "facts not supported by the descriptions.\n\n"
            "Return STRICT JSON only:\n"
            '{ "edges": [ {"target": "<exact candidate name>", "relation": "<short keywords>", '
            '"description": "<why, from the descriptions>"} ] }'
        )

    def _user_prompt(self, name: str, desc: str, candidates: List[Dict[str, Any]]) -> str:
        cand = [{"name": c.get("name"), "description": (c.get("description") or "")[:280]}
                for c in candidates]
        return (
            f"ISOLATED entity:\n{json.dumps({'name': name, 'description': desc[:280]}, ensure_ascii=False)}\n\n"
            f"CANDIDATE entities:\n{json.dumps(cand, ensure_ascii=False, indent=2)}\n\nReturn the JSON."
        )

    async def rescue(self, isolates: List[Dict[str, Any]]) -> dict:
        """Process a list of ``{name, description}`` isolates. Returns a summary."""
        summary = {"processed": 0, "edges_added": 0, "connected": 0, "errors": 0}
        for iso in isolates:
            summary["processed"] += 1
            name = iso.get("name") or ""
            desc = iso.get("description") or ""
            if not name:
                continue
            try:
                candidates = list(await self._find(name, desc) or [])[: self._maxc]
            except Exception as e:  # pragma: no cover
                logger.warning(f"isolate-rescue candidate lookup failed for {name}: {e}")
                candidates = []
            candidates = [c for c in candidates if c.get("name") and c["name"] != name]
            if not candidates:
                continue
            try:
                raw = await self._llm(
                    self._user_prompt(name, desc, candidates),
                    system_prompt=self._system_prompt(),
                )
            except Exception as e:  # pragma: no cover
                logger.warning(f"isolate-rescue LLM failed for {name}: {e}")
                summary["errors"] += 1
                continue
            payload = _extract_json_object(raw)
            edges = (payload or {}).get("edges") if isinstance(payload, dict) else None
            if not edges:
                continue
            cand_names = {c["name"] for c in candidates}
            added = 0
            for e in edges:
                if not isinstance(e, dict):
                    continue
                tgt = e.get("target")
                if not tgt or tgt == name or tgt not in cand_names:
                    continue
                rel = (e.get("relation") or "related to").strip()
                try:
                    await self._add(name, tgt, rel, (e.get("description") or "").strip())
                    added += 1
                except Exception as ex:  # pragma: no cover
                    logger.warning(f"isolate-rescue add_edge {name}->{tgt} failed: {ex}")
            summary["edges_added"] += added
            if added:
                summary["connected"] += 1
        logger.info(f"isolate_rescue: {summary}")
        return summary
