"""Layer C — async LLM adjudication of the gray-zone review queue.

The core of D5: for each ambiguous ``(name, candidate)`` pair the inline resolver
queued, ask the LLM two things at once — *are these the same real-world entity?* and,
if so, *what is the cluster's canonical name?* Confirmed pairs are recorded in the
reversible store (and applied to the graph via an injected callback); rejected pairs
are dropped from the queue. Runs off the ingest path, so this expensive step never
adds write latency.

Provider-agnostic — uses the workspace ``llm_model_func`` (like ``SiteAnalyst`` and
``RuleAuthor``). Batched to amortise calls.
"""

from __future__ import annotations

import json
from typing import Any, Awaitable, Callable, List, Optional

from lightrag.context_graph import _extract_json_object
from lightrag.utils import logger

from context_graph.dedup.canonical import canonicalize, prefer_canonical_name
from context_graph.dedup.store import DedupStore

# alias → into graph merge: (alias_id, into_id, canonical_name) -> None
ApplyMerge = Callable[[str, str, str], Awaitable[None]]
# name -> mention count (frequency proxy) for representative-name scoring
CountGetter = Callable[[str], Awaitable[int]]


class DedupSweep:
    """Adjudicate the gray-zone queue with the LLM; record + optionally apply merges.

    The LLM decides only *same or not* (the hard part). The canonical **name** is
    chosen deterministically by frequency-weighted score (``prefer_canonical_name``),
    which is faster and good enough — pass ``get_count`` to feed real mention counts.
    """

    def __init__(
        self, store: DedupStore, workspace: str, llm: Callable[..., Awaitable[str]], *,
        apply_merge: Optional[ApplyMerge] = None,
        get_count: Optional[CountGetter] = None, batch_size: int = 10,
    ) -> None:
        self._store = store
        self._ws = workspace
        self._llm = llm
        self._apply = apply_merge
        self._get_count = get_count
        self._batch = max(1, batch_size)

    def _system_prompt(self) -> str:
        return (
            "You resolve entity duplicates for a knowledge graph. For each candidate "
            "pair of entity names, decide only whether they refer to the SAME "
            "real-world entity (e.g. an acronym and its expansion, a company and its "
            "legal name, the same person named two ways).\n\n"
            "Be conservative: if unsure, say not the same. Return STRICT JSON only:\n"
            '{ "verdicts": [ {"id": 0, "same": true, "reason": "..."} ] }'
        )

    def _user_prompt(self, pairs: List[dict]) -> str:
        items = [{"id": i, "a": p["name"], "b": p["candidate"]}
                 for i, p in enumerate(pairs)]
        return "PAIRS:\n" + json.dumps(items, ensure_ascii=False, indent=2) + "\n\nReturn the JSON."

    async def run(self) -> dict:
        """Process the whole pending queue. Returns a summary dict."""
        pending = self._store.list_pending(self._ws)
        summary = {"adjudicated": 0, "merged": 0, "rejected": 0, "errors": 0}
        for start in range(0, len(pending), self._batch):
            batch = pending[start:start + self._batch]
            verdicts = await self._adjudicate(batch)
            if verdicts is None:              # LLM/parse failure — leave the batch queued
                summary["errors"] += len(batch)
                continue
            by_id = {v.get("id"): v for v in verdicts if isinstance(v, dict)}
            for i, pair in enumerate(batch):
                summary["adjudicated"] += 1
                v = by_id.get(i) or {}
                name, cand = pair["name"], pair["candidate"]
                if v.get("same"):
                    canonical = await self._canonical_name(name, cand)
                    await self._merge(name, cand, canonical)
                    summary["merged"] += 1
                else:
                    summary["rejected"] += 1
                self._store.clear_pending(self._ws, name=name, candidate=cand)
        return summary

    async def _canonical_name(self, name: str, cand: str) -> str:
        """Representative display name by frequency-weighted score (not the LLM)."""
        counts = {}
        if self._get_count is not None:
            try:
                counts = {name: await self._get_count(name),
                          cand: await self._get_count(cand)}
            except Exception:  # pragma: no cover - fall back to non-freq scoring
                counts = {}
        return prefer_canonical_name([name, cand], counts=counts)

    async def _adjudicate(self, batch: List[dict]) -> Optional[List[dict]]:
        try:
            raw = await self._llm(self._user_prompt(batch),
                                  system_prompt=self._system_prompt())
        except Exception as e:  # pragma: no cover - never crash the sweep
            logger.warning(f"DedupSweep LLM call failed: {e}")
            return None
        payload = _extract_json_object(raw)
        if not isinstance(payload, dict):
            logger.warning("DedupSweep: unparseable LLM output; leaving batch queued")
            return None
        return payload.get("verdicts") or []

    async def _merge(self, name: str, cand: str, canonical: str) -> None:
        # The representative form survives as the node; the other folds into it.
        survivor, alias = (name, cand) if canonical.strip() == name.strip() else (cand, name)
        self._store.record_merge(
            self._ws, alias=alias, alias_key=canonicalize(alias), into=survivor,
            method="llm", score=None, canonical_name=canonical,
        )
        if self._apply is not None:
            try:
                await self._apply(alias, survivor, canonical)   # graph-level rewrite
            except Exception as e:  # pragma: no cover
                logger.warning(f"DedupSweep apply_merge failed for {alias}->{survivor}: {e}")
