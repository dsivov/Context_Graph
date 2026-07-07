"""Layer B — the inline entity resolver (conservative, type-aware).

For each freshly extracted entity, decide: reuse an existing canonical node
(``merge``), flag an ambiguous near-duplicate for the async sweep (``review``), or
create a new node (``new``). Cheap enough for the write path — a warm-start alias
lookup, then at most one ANN query over the existing ``entities_vdb``.

Conservative (D1): auto-merge only above a **hard** cosine threshold, and only when
the types are compatible (D4) and the names share a lexical link (a backstop against
embedding false positives). Anything in the **gray** band is queued, never merged
inline. Every auto-merge is recorded in the reversible store (D3).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

from context_graph.dedup.canonical import canonicalize, is_acronym_of
from context_graph.dedup.store import DedupStore

DEFAULT_HARD = 0.93   # cosine ≥ this → inline auto-merge (conservative)
DEFAULT_GRAY = 0.85   # cosine in [gray, hard) → queue for LLM adjudication

NEW = "new"
MERGE = "merge"
REVIEW = "review"


@dataclass
class Resolution:
    action: str                       # 'new' | 'merge' | 'review'
    canonical_id: str                 # node id to use (self for new/review; target for merge)
    method: str = ""                  # 'rule-alias' | 'embedding' | 'gray'
    score: Optional[float] = None
    candidate: Optional[str] = None   # review: the suspected duplicate id to adjudicate


def type_ok(a: Optional[str], b: Optional[str]) -> bool:
    """D4 — never merge two KNOWN, different types. UNKNOWN/Other/None are permissive."""
    def norm(t: Optional[str]) -> str:
        t = (t or "").strip().lower()
        return "" if t in ("", "unknown", "other") else t
    na, nb = norm(a), norm(b)
    return not na or not nb or na == nb


def name_ok(a: str, b: str) -> bool:
    """Backstop against embedding false positives: require *some* lexical link —
    equal canonical key, acronym relationship, a shared token, or substring."""
    ka, kb = canonicalize(a), canonicalize(b)
    if not ka or not kb:
        return False
    if ka == kb or is_acronym_of(a, b) or is_acronym_of(b, a):
        return True
    if set(ka.split()) & set(kb.split()):
        return True
    return ka in kb or kb in ka


class EntityResolver:
    """Workspace-scoped resolver over a :class:`DedupStore` and ``entities_vdb``."""

    def __init__(
        self, store: DedupStore, workspace: str, *,
        entities_vdb: Any = None,
        hard: float = DEFAULT_HARD, gray: float = DEFAULT_GRAY,
    ) -> None:
        self._store = store
        self._ws = workspace
        self._vdb = entities_vdb
        self._hard = hard
        self._gray = gray

    async def resolve(
        self, name: str, entity_type: Optional[str] = None, *,
        get_type: Optional[Callable[[str], Awaitable[Optional[str]]]] = None,
    ) -> Resolution:
        """Resolve one extracted entity. ``get_type(id)`` optionally supplies a
        candidate's type when the vdb record doesn't carry it (keeps D4 exact)."""
        key = canonicalize(name)

        # D — warm start: a prior merge already maps this canonical key.
        if key:
            existing = self._store.resolve_key(self._ws, key)
            if existing and existing != name:
                return Resolution(MERGE, existing, "rule-alias", 1.0)

        # B — embedding blocking against existing nodes (top compatible hit only).
        if self._vdb is not None:
            try:
                hits = await self._vdb.query(name, top_k=5) or []
            except Exception:
                hits = []
            top = next(
                (h for h in hits
                 if (h.get("entity_name") or h.get("id")) not in (None, "", name)),
                None,
            )
            if top is not None:
                cand = top.get("entity_name") or top.get("id")
                score = float(top.get("distance") or 0.0)
                ctype = top.get("entity_type")
                if ctype is None and get_type is not None:
                    ctype = await get_type(cand)
                if score >= self._hard and type_ok(entity_type, ctype) and name_ok(name, cand):
                    self._store.record_merge(
                        self._ws, alias=name, alias_key=key, into=cand,
                        method="embedding", score=score,
                    )
                    return Resolution(MERGE, cand, "embedding", score)
                if score >= self._gray and type_ok(entity_type, ctype):
                    self._store.enqueue_review(
                        self._ws, name=name, candidate=cand, score=score,
                    )
                    return Resolution(REVIEW, name, "gray", score, candidate=cand)

        return Resolution(NEW, name)
