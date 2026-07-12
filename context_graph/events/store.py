"""Durable ingress log with idempotency dedupe (P0, decision 2).

Append-then-dispatch: every inbound :class:`Event` is appended here **before**
it is published, giving at-least-once, replayable ingress without a broker.
Dedupe is on :meth:`Event.dedupe_key` — connectors re-deliver, so a repeated key
returns ``False`` and is not stored twice. ``since`` in :meth:`replay` is an
append-offset cursor (a stringified integer), not a timestamp.

Two backends share all logic: :class:`InMemoryIngressLog` (tests / API layer) and
:class:`JsonIngressLog` (one file per workspace, the default).
"""

from __future__ import annotations

import json
import os
import re
import time
from abc import ABC, abstractmethod
from typing import Any, AsyncIterator, Callable, Dict, List

from lightrag.utils import logger

from context_graph.events.schema import Event

_WS_SANITIZE_RE = re.compile(r"[^A-Za-z0-9_]")


class IngressLog(ABC):
    def __init__(self, *, now: Callable[[], float] = time.time) -> None:
        self._now = now

    async def append(self, workspace: str, event: Event) -> bool:
        """Append unless the dedupe key was already seen. Returns ``True`` if
        stored, ``False`` if it was a duplicate."""
        key = event.dedupe_key()
        if self._seen(workspace, key):
            logger.debug(f"IngressLog dedupe: '{key}' already seen in '{workspace}'")
            return False
        self._append_raw(workspace, key, event.to_dict())
        return True

    async def replay(self, workspace: str, since: str = "0") -> AsyncIterator[Event]:
        try:
            offset = int(since) if since else 0
        except (TypeError, ValueError):
            offset = 0
        for d in self._entries(workspace)[offset:]:
            yield Event.from_dict(d)

    def count(self, workspace: str) -> int:
        return len(self._entries(workspace))

    def delete(self, workspace: str) -> bool:
        return self._delete_raw(workspace)

    @abstractmethod
    def _seen(self, workspace: str, key: str) -> bool: ...
    @abstractmethod
    def _append_raw(self, workspace: str, key: str, event_dict: Dict[str, Any]) -> None: ...
    @abstractmethod
    def _entries(self, workspace: str) -> List[Dict[str, Any]]: ...
    @abstractmethod
    def _delete_raw(self, workspace: str) -> bool: ...


class InMemoryIngressLog(IngressLog):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._log: Dict[str, Dict[str, Any]] = {}

    def _bucket(self, ws: str) -> Dict[str, Any]:
        return self._log.setdefault(ws, {"entries": [], "seen": set()})

    def _seen(self, ws: str, key: str) -> bool:
        return key in self._bucket(ws)["seen"]

    def _append_raw(self, ws: str, key: str, ed: Dict[str, Any]) -> None:
        b = self._bucket(ws)
        b["seen"].add(key)
        b["entries"].append(dict(ed))

    def _entries(self, ws: str) -> List[Dict[str, Any]]:
        return list(self._bucket(ws)["entries"])

    def _delete_raw(self, ws: str) -> bool:
        return self._log.pop(ws, None) is not None


class JsonIngressLog(IngressLog):
    def __init__(self, base_dir: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self._base_dir = base_dir

    def _path(self, ws: str) -> str:
        name = _WS_SANITIZE_RE.sub("_", ws) or "default"
        return os.path.join(self._base_dir, f"ingress_{name}.json")

    def _read(self, ws: str) -> Dict[str, Any]:
        p = self._path(ws)
        if not os.path.exists(p):
            return {"workspace": ws, "entries": [], "seen": []}
        try:
            with open(p, encoding="utf-8") as fh:
                return json.load(fh)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"IngressLog could not read {p}: {e}")
            return {"workspace": ws, "entries": [], "seen": []}

    def _write(self, ws: str, data: Dict[str, Any]) -> None:
        os.makedirs(self._base_dir, exist_ok=True)
        p = self._path(ws)
        tmp = f"{p}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, p)

    def _seen(self, ws: str, key: str) -> bool:
        return key in set(self._read(ws).get("seen", []))

    def _append_raw(self, ws: str, key: str, ed: Dict[str, Any]) -> None:
        data = self._read(ws)
        data.setdefault("entries", []).append(dict(ed))
        seen = set(data.get("seen", []))
        seen.add(key)
        data["seen"] = sorted(seen)
        self._write(ws, data)

    def _entries(self, ws: str) -> List[Dict[str, Any]]:
        return list(self._read(ws).get("entries", []))

    def _delete_raw(self, ws: str) -> bool:
        p = self._path(ws)
        if os.path.exists(p):
            os.remove(p)
            return True
        return False
