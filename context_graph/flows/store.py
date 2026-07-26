"""Flow + run persistence — the durability seam (P0/P2, decision 1).

:class:`RunStore` is the port the flow executor saves to at every wait/terminal.
The lean defaults (:class:`InMemoryRunStore` / :class:`JsonRunStore`) are enough
for the demo's minutes-to-hours runs; a durable engine can slot in behind this
port later. :meth:`RunStore.due_timers` drives the timer scheduler (P5) — it
selects waiting runs whose ``wake_at`` is due.

:class:`FlowStore` (P2) keeps the authored :class:`FlowDefinition` artifacts.
It is **versioned and append-only**: every save assigns the next version and
older versions stay readable, because runs pin ``flow_version`` at start
(decision 4) and replay must resolve the exact definition a run walked.
"""

from __future__ import annotations

import json
import os
import re
import time
from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, List, Optional

from lightrag.utils import logger

from context_graph.flows.schema import FlowDefinition, Run

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


# ─────────────────────────────────────────────────────────────────────────────
# FlowStore (P2) — versioned, append-only flow definitions
# ─────────────────────────────────────────────────────────────────────────────


class FlowStore(ABC):
    """Versioned storage for authored flow definitions.

    Raw layout per workspace: ``{flow_id: {str(version): flow_dict}}``. Saving a
    flow assigns the next version and never rewrites an older one — a running
    :class:`Run` pins ``flow_version`` at start, and replay must resolve the
    exact definition that run walked (decision 4).
    """

    def __init__(self, *, now: Callable[[], float] = time.time) -> None:
        self._now = now

    def save(self, workspace: str, flow: FlowDefinition) -> FlowDefinition:
        """Validate and store *flow* as the next version. Raises ``ValueError``
        on lint problems — an inconsistent flow is never persisted."""
        problems = flow.lint()
        if problems:
            raise ValueError("; ".join(problems))
        data = self._read_flows(workspace)
        versions = data.get(flow.id) or {}
        next_version = max((int(v) for v in versions), default=0) + 1
        stored = FlowDefinition.from_dict(flow.to_dict())
        stored.version = next_version
        versions[str(next_version)] = stored.to_dict()
        data[flow.id] = versions
        self._write_flows(workspace, data)
        return stored

    def get(self, workspace: str, flow_id: str,
            version: Optional[int] = None) -> Optional[FlowDefinition]:
        """One flow — the exact *version* (runs pin it) or the latest."""
        versions = self._read_flows(workspace).get(flow_id) or {}
        if not versions:
            return None
        key = str(version) if version is not None else str(
            max(int(v) for v in versions)
        )
        d = versions.get(key)
        return FlowDefinition.from_dict(d) if d is not None else None

    def list(self, workspace: str) -> List[FlowDefinition]:
        """The latest version of every flow, sorted by id."""
        out: List[FlowDefinition] = []
        for flow_id in sorted(self._read_flows(workspace)):
            f = self.get(workspace, flow_id)
            if f is not None:
                out.append(f)
        return out

    def for_event(self, workspace: str, event_type: str) -> List[FlowDefinition]:
        """Latest-version flows subscribed to *event_type* (the bus trigger)."""
        return [f for f in self.list(workspace) if f.on_event == event_type]

    def delete(self, workspace: str, flow_id: str) -> bool:
        data = self._read_flows(workspace)
        if flow_id not in data:
            return False
        del data[flow_id]
        self._write_flows(workspace, data)
        return True

    @abstractmethod
    def _read_flows(self, ws: str) -> Dict[str, Dict[str, Any]]: ...
    @abstractmethod
    def _write_flows(self, ws: str, data: Dict[str, Dict[str, Any]]) -> None: ...


class InMemoryFlowStore(FlowStore):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._flows: Dict[str, Dict[str, Dict[str, Any]]] = {}

    def _read_flows(self, ws: str) -> Dict[str, Dict[str, Any]]:
        # Deep-ish copy so callers can't mutate stored versions in place.
        return {fid: dict(vers) for fid, vers in self._flows.get(ws, {}).items()}

    def _write_flows(self, ws: str, data: Dict[str, Dict[str, Any]]) -> None:
        self._flows[ws] = {fid: dict(vers) for fid, vers in data.items()}


class JsonFlowStore(FlowStore):
    def __init__(self, base_dir: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self._base_dir = base_dir

    def _path(self, ws: str) -> str:
        name = _WS_SANITIZE_RE.sub("_", ws) or "default"
        return os.path.join(self._base_dir, f"flows_{name}.json")

    def _read_flows(self, ws: str) -> Dict[str, Dict[str, Any]]:
        p = self._path(ws)
        if not os.path.exists(p):
            return {}
        try:
            with open(p, encoding="utf-8") as fh:
                return json.load(fh)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"FlowStore could not read {p}: {e}")
            return {}

    def _write_flows(self, ws: str, data: Dict[str, Dict[str, Any]]) -> None:
        os.makedirs(self._base_dir, exist_ok=True)
        p = self._path(ws)
        tmp = f"{p}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, p)
