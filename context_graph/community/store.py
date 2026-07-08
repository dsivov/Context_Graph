"""Per-workspace store of community records (id, title, summary, members, size).

The retrieval index lives in ``communities_vdb`` (vectors over summaries); this store
keeps the full, enumerable list for a ``/graph/communities`` view. Mirrors the other
CG stores.
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


class CommunityStore(ABC):
    def __init__(self, *, now: Callable[[], float] = time.time) -> None:
        self._now = now

    def replace(self, workspace: str, communities: List[Dict[str, Any]]) -> None:
        """Replace the workspace's community set (a rebuild is authoritative)."""
        self._write_raw(workspace, {"communities": communities, "updated": self._now()})

    def list(self, workspace: str) -> List[Dict[str, Any]]:
        return list((self._read_raw(workspace) or {}).get("communities", []))

    def get(self, workspace: str, community_id: str) -> Optional[Dict[str, Any]]:
        for c in self.list(workspace):
            if c.get("id") == community_id:
                return c
        return None

    def summary(self, workspace: str) -> Dict[str, Any]:
        comms = self.list(workspace)
        sizes = sorted((len(c.get("members", [])) for c in comms), reverse=True)
        return {"workspace": workspace, "communities": len(comms),
                "largest": sizes[0] if sizes else 0,
                "members_covered": sum(sizes)}

    @abstractmethod
    def _read_raw(self, workspace: str) -> Optional[Dict[str, Any]]: ...
    @abstractmethod
    def _write_raw(self, workspace: str, data: Dict[str, Any]) -> None: ...


class InMemoryCommunityStore(CommunityStore):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._data: Dict[str, Dict[str, Any]] = {}

    def _read_raw(self, workspace: str) -> Optional[Dict[str, Any]]:
        raw = self._data.get(workspace)
        return json.loads(json.dumps(raw)) if raw is not None else None

    def _write_raw(self, workspace: str, data: Dict[str, Any]) -> None:
        self._data[workspace] = json.loads(json.dumps(data))


class JsonCommunityStore(CommunityStore):
    def __init__(self, base_dir: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self._base_dir = base_dir

    def _path(self, workspace: str) -> str:
        name = _WS_SANITIZE_RE.sub("_", workspace) or "default"
        return os.path.join(self._base_dir, f"community_{name}.json")

    def _read_raw(self, workspace: str) -> Optional[Dict[str, Any]]:
        path = self._path(workspace)
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"CommunityStore could not read {path}: {e}")
            return None

    def _write_raw(self, workspace: str, data: Dict[str, Any]) -> None:
        os.makedirs(self._base_dir, exist_ok=True)
        path = self._path(workspace)
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
