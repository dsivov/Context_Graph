"""Per-workspace persistence for the RBAC policy (P3, Gap 1).

Mirrors ``context_graph.actions.store``: one :class:`RbacPolicy` per workspace,
persisted as JSON, validated (lint) and versioned on save.
"""

from __future__ import annotations

import json
import os
import re
import time
from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, List, Optional

from lightrag.utils import logger

from context_graph.rbac.schema import RbacPolicy

_WS_SANITIZE_RE = re.compile(r"[^A-Za-z0-9_]")


def validate_policy(policy: RbacPolicy) -> None:
    """Raise ``ValueError`` if the policy is not safe to persist."""
    problems = policy.lint()
    if problems:
        raise ValueError("rbac policy is not valid: " + "; ".join(problems))


class RbacStore(ABC):
    """Abstract per-workspace RBAC-policy store."""

    def __init__(self, *, now: Callable[[], float] = time.time) -> None:
        self._now = now

    def save(self, workspace: str, policy: RbacPolicy) -> RbacPolicy:
        validate_policy(policy)
        prev = self.load(workspace)
        version = (prev.version + 1) if prev is not None else 1
        data = policy.to_dict()
        data["version"] = version
        self._write_raw(workspace, {
            "workspace": workspace,
            "updated_at": self._now(),
            "policy": data,
        })
        logger.info(f"RbacStore saved workspace '{workspace}' v{version}")
        return RbacPolicy.from_dict(data)

    def load(self, workspace: str) -> Optional[RbacPolicy]:
        raw = self._read_raw(workspace)
        if raw is None or "policy" not in raw:
            return None
        return RbacPolicy.from_dict(raw["policy"])

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


class InMemoryRbacStore(RbacStore):
    """Ephemeral store — tests and the API cache."""

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


class JsonRbacStore(RbacStore):
    """One JSON file per workspace under *base_dir* (the file-based default)."""

    def __init__(self, base_dir: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self._base_dir = base_dir

    def _path(self, workspace: str) -> str:
        name = _WS_SANITIZE_RE.sub("_", workspace) or "default"
        return os.path.join(self._base_dir, f"rbac_{name}.json")

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
            logger.warning(f"RbacStore could not read {path}: {e}")
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
            if fn.startswith("rbac_") and fn.endswith(".json"):
                raw = self._read_path(os.path.join(self._base_dir, fn))
                if raw and "workspace" in raw:
                    out.append(raw["workspace"])
        return out
