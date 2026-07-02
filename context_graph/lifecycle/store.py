"""Per-workspace persistence for the lifecycle definition (P3, Gap 2).

Mirrors ``context_graph.rbac.store``: one :class:`Lifecycle` per workspace,
validated (lint) and versioned on save.
"""

from __future__ import annotations

import json
import os
import re
import time
from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, List, Optional

from lightrag.utils import logger

from context_graph.lifecycle.schema import Lifecycle

_WS_SANITIZE_RE = re.compile(r"[^A-Za-z0-9_]")


def validate_lifecycle(lifecycle: Lifecycle) -> None:
    problems = lifecycle.lint()
    if problems:
        raise ValueError("lifecycle is not valid: " + "; ".join(problems))


class LifecycleStore(ABC):
    def __init__(self, *, now: Callable[[], float] = time.time) -> None:
        self._now = now

    def save(self, workspace: str, lifecycle: Lifecycle) -> Lifecycle:
        validate_lifecycle(lifecycle)
        prev = self.load(workspace)
        version = (prev.version + 1) if prev is not None else 1
        data = lifecycle.to_dict()
        data["version"] = version
        self._write_raw(workspace, {
            "workspace": workspace,
            "updated_at": self._now(),
            "lifecycle": data,
        })
        logger.info(f"LifecycleStore saved workspace '{workspace}' v{version}")
        return Lifecycle.from_dict(data)

    def load(self, workspace: str) -> Optional[Lifecycle]:
        raw = self._read_raw(workspace)
        if raw is None or "lifecycle" not in raw:
            return None
        return Lifecycle.from_dict(raw["lifecycle"])

    def meta(self, workspace: str) -> Optional[Dict[str, Any]]:
        raw = self._read_raw(workspace)
        if raw is None:
            return None
        return {"workspace": raw.get("workspace", workspace),
                "updated_at": raw.get("updated_at")}

    def delete(self, workspace: str) -> bool:
        return self._delete_raw(workspace)

    def list_workspaces(self) -> List[str]:
        return sorted(self._list_workspaces())

    @abstractmethod
    def _read_raw(self, workspace: str) -> Optional[Dict[str, Any]]: ...
    @abstractmethod
    def _write_raw(self, workspace: str, data: Dict[str, Any]) -> None: ...
    @abstractmethod
    def _delete_raw(self, workspace: str) -> bool: ...
    @abstractmethod
    def _list_workspaces(self) -> List[str]: ...


class InMemoryLifecycleStore(LifecycleStore):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._data: Dict[str, Dict[str, Any]] = {}

    def _read_raw(self, workspace: str) -> Optional[Dict[str, Any]]:
        raw = self._data.get(workspace)
        return dict(raw) if raw is not None else None

    def _write_raw(self, workspace: str, data: Dict[str, Any]) -> None:
        self._data[workspace] = dict(data)

    def _delete_raw(self, workspace: str) -> bool:
        return self._data.pop(workspace, None) is not None

    def _list_workspaces(self) -> List[str]:
        return list(self._data)


class JsonLifecycleStore(LifecycleStore):
    def __init__(self, base_dir: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self._base_dir = base_dir

    def _path(self, workspace: str) -> str:
        name = _WS_SANITIZE_RE.sub("_", workspace) or "default"
        return os.path.join(self._base_dir, f"lifecycle_{name}.json")

    def _read_raw(self, workspace: str) -> Optional[Dict[str, Any]]:
        return self._read_path(self._path(workspace))

    @staticmethod
    def _read_path(path: str) -> Optional[Dict[str, Any]]:
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"LifecycleStore could not read {path}: {e}")
            return None

    def _write_raw(self, workspace: str, data: Dict[str, Any]) -> None:
        os.makedirs(self._base_dir, exist_ok=True)
        path = self._path(workspace)
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)

    def _delete_raw(self, workspace: str) -> bool:
        path = self._path(workspace)
        if os.path.exists(path):
            os.remove(path)
            return True
        return False

    def _list_workspaces(self) -> List[str]:
        if not os.path.isdir(self._base_dir):
            return []
        out = []
        for fn in os.listdir(self._base_dir):
            if fn.startswith("lifecycle_") and fn.endswith(".json"):
                raw = self._read_path(os.path.join(self._base_dir, fn))
                if raw and "workspace" in raw:
                    out.append(raw["workspace"])
        return out
