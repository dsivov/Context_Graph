"""Per-workspace quarantine store for rejected nodes (D12).

Nodes the filter rejects are held here — not dropped — with the reason, so they can
be reviewed and either restored (re-created) or discarded. Mirrors the rules /
dedup stores: an abstract base + InMemory / Json backends, per-workspace isolation.
"""

from __future__ import annotations

import json
import os
import re
import time
from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, List, Optional

from lightrag.utils import logger

_WS_SANITIZE_RE = re.compile(r"[^A-Za-z0-9_]")


class QuarantineStore(ABC):
    """Holds rejected nodes ``{name, entity_type, description, reason, ts}``."""

    def __init__(self, *, now: Callable[[], float] = time.time) -> None:
        self._now = now

    def add(self, workspace: str, items: List[Dict[str, Any]]) -> int:
        """Add rejected nodes; dedupe on name. Returns how many were newly added."""
        if not items:
            return 0
        data = self._read_raw(workspace) or {"items": [], "version": 0}
        existing = {i.get("name") for i in data["items"]}
        added = 0
        for it in items:
            name = it.get("entity_name") or it.get("name")
            if not name or name in existing:
                continue
            data["items"].append({
                "name": name,
                "entity_type": it.get("entity_type") or it.get("type") or "",
                "description": it.get("description") or "",
                "reason": it.get("reason") or "",
                "ts": self._now(),
            })
            existing.add(name)
            added += 1
        if added:
            data["version"] = int(data.get("version", 0)) + 1
            self._write_raw(workspace, data)
        return added

    def list(self, workspace: str) -> List[Dict[str, Any]]:
        return list((self._read_raw(workspace) or {}).get("items", []))

    def pop(self, workspace: str, name: str) -> Optional[Dict[str, Any]]:
        """Remove and return one quarantined node by name (for restore/discard)."""
        data = self._read_raw(workspace)
        if not data:
            return None
        keep, found = [], None
        for i in data.get("items", []):
            if i.get("name") == name and found is None:
                found = i
            else:
                keep.append(i)
        if found is None:
            return None
        data["items"] = keep
        data["version"] = int(data.get("version", 0)) + 1
        self._write_raw(workspace, data)
        return found

    def summary(self, workspace: str) -> Dict[str, Any]:
        items = self.list(workspace)
        by_reason: Dict[str, int] = {}
        for i in items:
            r = i.get("reason", "?")
            by_reason[r] = by_reason.get(r, 0) + 1
        return {"workspace": workspace, "count": len(items), "by_reason": by_reason}

    @abstractmethod
    def _read_raw(self, workspace: str) -> Optional[Dict[str, Any]]: ...
    @abstractmethod
    def _write_raw(self, workspace: str, data: Dict[str, Any]) -> None: ...


class InMemoryQuarantineStore(QuarantineStore):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._data: Dict[str, Dict[str, Any]] = {}

    def _read_raw(self, workspace: str) -> Optional[Dict[str, Any]]:
        raw = self._data.get(workspace)
        return json.loads(json.dumps(raw)) if raw is not None else None

    def _write_raw(self, workspace: str, data: Dict[str, Any]) -> None:
        self._data[workspace] = json.loads(json.dumps(data))


class JsonQuarantineStore(QuarantineStore):
    def __init__(self, base_dir: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self._base_dir = base_dir

    def _path(self, workspace: str) -> str:
        name = _WS_SANITIZE_RE.sub("_", workspace) or "default"
        return os.path.join(self._base_dir, f"quarantine_{name}.json")

    def _read_raw(self, workspace: str) -> Optional[Dict[str, Any]]:
        path = self._path(workspace)
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"QuarantineStore could not read {path}: {e}")
            return None

    def _write_raw(self, workspace: str, data: Dict[str, Any]) -> None:
        os.makedirs(self._base_dir, exist_ok=True)
        path = self._path(workspace)
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
