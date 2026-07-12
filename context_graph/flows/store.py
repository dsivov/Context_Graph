"""Run persistence — the durability seam (P0, decision 1).

:class:`RunStore` is the port the flow executor saves to at every wait/terminal.
The lean defaults (:class:`InMemoryRunStore` / :class:`JsonRunStore`) are enough
for the demo's minutes-to-hours runs; a durable engine can slot in behind this
port later. :meth:`RunStore.due_timers` drives the timer scheduler (P5) — it
selects waiting runs whose ``wake_at`` is due.
"""

from __future__ import annotations

import json
import os
import re
import time
from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, List, Optional

from lightrag.utils import logger

from context_graph.flows.schema import Run

_WS_SANITIZE_RE = re.compile(r"[^A-Za-z0-9_]")


class RunStore(ABC):
    def __init__(self, *, now: Callable[[], float] = time.time) -> None:
        self._now = now

    async def save(self, workspace: str, run: Run) -> None:
        self._write_run(workspace, run.run_id, run.to_dict())

    async def get(self, workspace: str, run_id: str) -> Optional[Run]:
        d = self._read_run(workspace, run_id)
        return Run.from_dict(d) if d is not None else None

    async def list(
        self,
        workspace: str,
        *,
        app_id: Optional[str] = None,
        status: Optional[str] = None,
    ) -> List[Run]:
        out: List[Run] = []
        for d in self._all_runs(workspace):
            if app_id is not None and d.get("app_id") != app_id:
                continue
            if status is not None and d.get("status") != status:
                continue
            out.append(Run.from_dict(d))
        return out

    async def due_timers(self, workspace: str, now: str) -> List[Run]:
        """Waiting runs whose ``wake_at`` is at or before ``now`` (ISO-8601)."""
        out: List[Run] = []
        for d in self._all_runs(workspace):
            if d.get("status") != "waiting":
                continue
            wake = d.get("wake_at")
            if wake and wake <= now:
                out.append(Run.from_dict(d))
        return out

    def delete(self, workspace: str, run_id: str) -> bool:
        return self._delete_run(workspace, run_id)

    @abstractmethod
    def _write_run(self, ws: str, run_id: str, d: Dict[str, Any]) -> None: ...
    @abstractmethod
    def _read_run(self, ws: str, run_id: str) -> Optional[Dict[str, Any]]: ...
    @abstractmethod
    def _all_runs(self, ws: str) -> List[Dict[str, Any]]: ...
    @abstractmethod
    def _delete_run(self, ws: str, run_id: str) -> bool: ...


class InMemoryRunStore(RunStore):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._runs: Dict[str, Dict[str, Dict[str, Any]]] = {}

    def _ws(self, ws: str) -> Dict[str, Dict[str, Any]]:
        return self._runs.setdefault(ws, {})

    def _write_run(self, ws: str, run_id: str, d: Dict[str, Any]) -> None:
        self._ws(ws)[run_id] = dict(d)

    def _read_run(self, ws: str, run_id: str) -> Optional[Dict[str, Any]]:
        d = self._ws(ws).get(run_id)
        return dict(d) if d is not None else None

    def _all_runs(self, ws: str) -> List[Dict[str, Any]]:
        return [dict(d) for d in self._ws(ws).values()]

    def _delete_run(self, ws: str, run_id: str) -> bool:
        return self._ws(ws).pop(run_id, None) is not None


class JsonRunStore(RunStore):
    def __init__(self, base_dir: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self._base_dir = base_dir

    def _dir(self, ws: str) -> str:
        name = _WS_SANITIZE_RE.sub("_", ws) or "default"
        return os.path.join(self._base_dir, f"runs_{name}")

    def _path(self, ws: str, run_id: str) -> str:
        safe = _WS_SANITIZE_RE.sub("_", run_id) or "run"
        return os.path.join(self._dir(ws), f"{safe}.json")

    def _write_run(self, ws: str, run_id: str, d: Dict[str, Any]) -> None:
        os.makedirs(self._dir(ws), exist_ok=True)
        p = self._path(ws, run_id)
        tmp = f"{p}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(d, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, p)

    def _read_run(self, ws: str, run_id: str) -> Optional[Dict[str, Any]]:
        return self._read_path(self._path(ws, run_id))

    @staticmethod
    def _read_path(p: str) -> Optional[Dict[str, Any]]:
        if not os.path.exists(p):
            return None
        try:
            with open(p, encoding="utf-8") as fh:
                return json.load(fh)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"RunStore could not read {p}: {e}")
            return None

    def _all_runs(self, ws: str) -> List[Dict[str, Any]]:
        d = self._dir(ws)
        if not os.path.isdir(d):
            return []
        out: List[Dict[str, Any]] = []
        for fn in sorted(os.listdir(d)):
            if fn.endswith(".json"):
                r = self._read_path(os.path.join(d, fn))
                if r is not None:
                    out.append(r)
        return out

    def _delete_run(self, ws: str, run_id: str) -> bool:
        p = self._path(ws, run_id)
        if os.path.exists(p):
            os.remove(p)
            return True
        return False
