"""Per-workspace persistence for the ontology (P2, step 2).

Mirrors ``context_graph.rules.store``: one :class:`~context_graph.ontology.Ontology`
per workspace, persisted as JSON. Saving **validates** first (the ontology must
be self-consistent — no link may reference an undefined object type) and bumps
the ontology's ``version``, so a broken schema is rejected at author time.

Two backends share all logic via :class:`OntologyStore`:

* :class:`JsonOntologyStore`     — one JSON file per workspace (the default).
* :class:`InMemoryOntologyStore` — ephemeral, for tests and the API layer.
"""

from __future__ import annotations

import json
import os
import re
import time
from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, List, Optional

from lightrag.utils import logger

from context_graph.ontology.schema import Ontology

_WS_SANITIZE_RE = re.compile(r"[^A-Za-z0-9_]")


def validate_ontology(ontology: Ontology) -> None:
    """Raise ``ValueError`` if the ontology is not safe to persist."""
    problems = ontology.lint()
    if problems:
        raise ValueError("ontology is not self-consistent: " + "; ".join(problems))


class OntologyStore(ABC):
    """Abstract per-workspace ontology store."""

    def __init__(self, *, now: Callable[[], float] = time.time) -> None:
        self._now = now

    # -- public API --------------------------------------------------------

    def save(self, workspace: str, ontology: Ontology) -> Ontology:
        """Validate and persist an ontology; bumps ``version`` on each save.

        Does not mutate the caller's ``ontology``; returns the stored copy.
        """
        validate_ontology(ontology)
        prev = self.load(workspace)
        version = (prev.version + 1) if prev is not None else 1
        data = ontology.to_dict()
        data["version"] = version
        self._write_raw(workspace, {
            "workspace": workspace,
            "updated_at": self._now(),
            "ontology": data,
        })
        logger.info(f"OntologyStore saved workspace '{workspace}' v{version}")
        return Ontology.from_dict(data)

    def load(self, workspace: str) -> Optional[Ontology]:
        raw = self._read_raw(workspace)
        if raw is None or "ontology" not in raw:
            return None
        return Ontology.from_dict(raw["ontology"])

    def delete(self, workspace: str) -> bool:
        return self._delete_raw(workspace)

    def list_workspaces(self) -> List[str]:
        return sorted(self._list_workspaces())

    # -- raw persistence (subclass) ----------------------------------------

    @abstractmethod
    def _read_raw(self, workspace: str) -> Optional[Dict[str, Any]]:
        ...

    @abstractmethod
    def _write_raw(self, workspace: str, data: Dict[str, Any]) -> None:
        ...

    @abstractmethod
    def _delete_raw(self, workspace: str) -> bool:
        ...

    @abstractmethod
    def _list_workspaces(self) -> List[str]:
        ...


class InMemoryOntologyStore(OntologyStore):
    """Ephemeral store — handy for tests and as an API-layer cache."""

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


class JsonOntologyStore(OntologyStore):
    """One JSON file per workspace under *base_dir* (the file-based default)."""

    def __init__(self, base_dir: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self._base_dir = base_dir

    def _path(self, workspace: str) -> str:
        name = _WS_SANITIZE_RE.sub("_", workspace) or "default"
        return os.path.join(self._base_dir, f"ontology_{name}.json")

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
            logger.warning(f"OntologyStore could not read {path}: {e}")
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

    def _list_workspaces(self) -> List[str]:
        if not os.path.isdir(self._base_dir):
            return []
        out = []
        for fn in os.listdir(self._base_dir):
            if fn.startswith("ontology_") and fn.endswith(".json"):
                raw = self._read_path(os.path.join(self._base_dir, fn))
                if raw and "workspace" in raw:
                    out.append(raw["workspace"])
        return out
