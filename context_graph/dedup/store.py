"""Layer D — the reversible alias / canonical store + merge audit log.

Every merge (rule, embedding, or LLM) is recorded here, which is what makes dedup
**reversible and audited** (D3) — the graph's whole pitch is traceable memory, so a
wrong merge must be undoable. The store maps a canonical *key* (from Layer A) to the
canonical node id, remembers each cluster's canonical display name, and keeps an
append-only merge log that ``unmerge`` walks back.

Backends mirror the rules/ontology stores: an abstract :class:`DedupStore` with the
per-workspace bundle logic, and :class:`InMemoryDedupStore` / :class:`JsonDedupStore`
implementations. Per-workspace isolation via a sanitized filename.
"""

from __future__ import annotations

import json
import os
import re
import time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional

from lightrag.utils import logger

_WS_SANITIZE_RE = re.compile(r"[^A-Za-z0-9_]")


@dataclass
class MergeRecord:
    """One audited, reversible merge: alias *name* → canonical node *into*."""

    id: str
    alias: str                 # the raw variant name that was merged away
    alias_key: str             # its canonical key (Layer A)
    into: str                  # canonical node id it now resolves to
    method: str                # 'rule' | 'embedding' | 'llm' | 'manual'
    score: Optional[float] = None
    ts: float = 0.0
    undone: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "MergeRecord":
        return cls(**{k: d.get(k) for k in cls.__dataclass_fields__})


@dataclass
class _Bundle:
    aliases: Dict[str, str] = field(default_factory=dict)          # key → canonical id
    canonical_names: Dict[str, str] = field(default_factory=dict)  # canonical id → display
    merges: List[MergeRecord] = field(default_factory=list)
    pending: List[Dict[str, Any]] = field(default_factory=list)    # gray-zone review queue
    version: int = 0
    updated: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "aliases": self.aliases,
            "canonical_names": self.canonical_names,
            "merges": [m.to_dict() for m in self.merges],
            "pending": self.pending,
            "version": self.version,
            "updated": self.updated,
        }

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> "_Bundle":
        d = d or {}
        return cls(
            aliases=dict(d.get("aliases") or {}),
            canonical_names=dict(d.get("canonical_names") or {}),
            merges=[MergeRecord.from_dict(m) for m in (d.get("merges") or [])],
            pending=list(d.get("pending") or []),
            version=int(d.get("version") or 0),
            updated=float(d.get("updated") or 0.0),
        )


class DedupStore(ABC):
    """Per-workspace alias/canonical store with an audited, reversible merge log."""

    def __init__(self, *, now: Callable[[], float] = time.time) -> None:
        self._now = now

    # -- resolution --------------------------------------------------------

    def resolve_key(self, workspace: str, key: str) -> Optional[str]:
        """Return the canonical node id an already-computed *key* resolves to, or None."""
        if not key:
            return None
        return self._load(workspace).aliases.get(key)

    def canonical_name(self, workspace: str, canonical_id: str) -> Optional[str]:
        return self._load(workspace).canonical_names.get(canonical_id)

    def aliases_of(self, workspace: str, canonical_id: str) -> List[str]:
        """Raw variant names currently resolving to *canonical_id* (live merges only)."""
        b = self._load(workspace)
        live = {m.alias_key for m in b.merges if not m.undone and m.into == canonical_id}
        return [m.alias for m in b.merges
                if not m.undone and m.into == canonical_id and m.alias_key in live]

    # -- mutation ----------------------------------------------------------

    def record_merge(
        self, workspace: str, *, alias: str, alias_key: str, into: str,
        method: str, score: Optional[float] = None,
        canonical_name: Optional[str] = None,
    ) -> MergeRecord:
        """Record (and apply) a merge: ``alias_key`` now resolves to ``into``.

        Idempotent on ``(alias_key → into)``; optionally sets the cluster's canonical
        display name. Returns the :class:`MergeRecord`.
        """
        b = self._load(workspace)
        rec = MergeRecord(
            id=f"m{len(b.merges) + 1}", alias=alias, alias_key=alias_key,
            into=into, method=method, score=score, ts=self._now(),
        )
        b.aliases[alias_key] = into
        if canonical_name:
            b.canonical_names[into] = canonical_name
        b.merges.append(rec)
        self._commit(workspace, b)
        return rec

    def set_canonical_name(self, workspace: str, canonical_id: str, name: str) -> None:
        b = self._load(workspace)
        b.canonical_names[canonical_id] = name
        self._commit(workspace, b)

    def unmerge(self, workspace: str, merge_id: str) -> bool:
        """Reverse a merge by id: drop its alias mapping, mark the record undone.

        Returns False if the id is unknown or already undone. The graph-level split
        (rewiring edges back) is the caller's job — this reverses the *resolution*.
        """
        b = self._load(workspace)
        rec = next((m for m in b.merges if m.id == merge_id and not m.undone), None)
        if rec is None:
            return False
        rec.undone = True
        # Only drop the alias if no other live record still asserts it.
        if not any(m.alias_key == rec.alias_key and not m.undone
                   and m.id != rec.id for m in b.merges):
            b.aliases.pop(rec.alias_key, None)
        self._commit(workspace, b)
        return True

    # -- gray-zone review queue -------------------------------------------

    def enqueue_review(self, workspace: str, *, name: str, candidate: str,
                       score: float) -> None:
        """Queue an ambiguous (name, suspected-duplicate) pair for the async sweep.
        Deduplicated on the unordered pair so re-ingest doesn't pile up."""
        b = self._load(workspace)
        pair = {name, candidate}
        if any({p.get("name"), p.get("candidate")} == pair for p in b.pending):
            return
        b.pending.append({"name": name, "candidate": candidate,
                          "score": float(score), "ts": self._now()})
        self._commit(workspace, b)

    def list_pending(self, workspace: str) -> List[Dict[str, Any]]:
        return list(self._load(workspace).pending)

    def clear_pending(self, workspace: str, *, name: str, candidate: str) -> bool:
        """Remove one resolved pair from the queue (the sweep calls this)."""
        b = self._load(workspace)
        pair = {name, candidate}
        before = len(b.pending)
        b.pending = [p for p in b.pending
                     if {p.get("name"), p.get("candidate")} != pair]
        if len(b.pending) == before:
            return False
        self._commit(workspace, b)
        return True

    # -- reporting ---------------------------------------------------------

    def list_merges(self, workspace: str, *, include_undone: bool = False) -> List[MergeRecord]:
        return [m for m in self._load(workspace).merges
                if include_undone or not m.undone]

    def summary(self, workspace: str) -> Dict[str, Any]:
        b = self._load(workspace)
        live = [m for m in b.merges if not m.undone]
        by_method: Dict[str, int] = {}
        for m in live:
            by_method[m.method] = by_method.get(m.method, 0) + 1
        return {
            "workspace": workspace, "version": b.version,
            "alias_count": len(b.aliases), "canonical_count": len(b.canonical_names),
            "merges_live": len(live), "merges_total": len(b.merges),
            "pending_review": len(b.pending), "by_method": by_method,
        }

    # -- persistence -------------------------------------------------------

    def _load(self, workspace: str) -> _Bundle:
        return _Bundle.from_dict(self._read_raw(workspace))

    def _commit(self, workspace: str, b: _Bundle) -> None:
        b.version += 1
        b.updated = self._now()
        self._write_raw(workspace, b.to_dict())

    @abstractmethod
    def _read_raw(self, workspace: str) -> Optional[Dict[str, Any]]: ...
    @abstractmethod
    def _write_raw(self, workspace: str, data: Dict[str, Any]) -> None: ...
    @abstractmethod
    def _delete_raw(self, workspace: str) -> bool: ...


class InMemoryDedupStore(DedupStore):
    """Ephemeral store — tests and API-layer cache."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._data: Dict[str, Dict[str, Any]] = {}

    def _read_raw(self, workspace: str) -> Optional[Dict[str, Any]]:
        raw = self._data.get(workspace)
        return json.loads(json.dumps(raw)) if raw is not None else None

    def _write_raw(self, workspace: str, data: Dict[str, Any]) -> None:
        self._data[workspace] = json.loads(json.dumps(data))

    def _delete_raw(self, workspace: str) -> bool:
        return self._data.pop(workspace, None) is not None


class JsonDedupStore(DedupStore):
    """One JSON file per workspace under *base_dir* (the file-based default)."""

    def __init__(self, base_dir: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self._base_dir = base_dir

    def _path(self, workspace: str) -> str:
        name = _WS_SANITIZE_RE.sub("_", workspace) or "default"
        return os.path.join(self._base_dir, f"dedup_{name}.json")

    def _read_raw(self, workspace: str) -> Optional[Dict[str, Any]]:
        path = self._path(workspace)
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"DedupStore could not read {path}: {e}")
            return None

    def _write_raw(self, workspace: str, data: Dict[str, Any]) -> None:
        os.makedirs(self._base_dir, exist_ok=True)
        path = self._path(workspace)
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)  # atomic write

    def _delete_raw(self, workspace: str) -> bool:
        path = self._path(workspace)
        if os.path.exists(path):
            os.remove(path)
            return True
        return False
