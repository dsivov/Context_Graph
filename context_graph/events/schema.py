"""Event contract for the platform ingress backbone (P0).

An :class:`Event` is the single normalized message that crosses from the
integration engine into the flow runtime. Connectors supply an
``idempotency_key`` (they re-deliver — webhook retries, poller overlaps);
consumers dedupe on :meth:`Event.dedupe_key`, which falls back to a stable
content hash when no explicit key is given. See docs/PLATFORM_ARCHITECTURE.html
(decision 2).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class Event:
    """A normalized ingress event. ``ts`` is supplied by the connector (pure
    consumers take no server clock)."""

    type: str
    payload: Dict[str, Any] = field(default_factory=dict)
    source: str = ""
    ts: str = ""
    idempotency_key: str = ""
    workspace: str = "default"
    app_id: Optional[str] = None
    mapped: bool = False
    mapping_meta: Optional[Dict[str, Any]] = None

    def dedupe_key(self) -> str:
        """The key consumers dedupe on. Prefer the connector-supplied
        ``idempotency_key``; otherwise a deterministic content hash so identical
        re-deliveries collapse and distinct payloads do not."""
        if self.idempotency_key:
            return self.idempotency_key
        blob = json.dumps(
            {"type": self.type, "source": self.source, "payload": self.payload},
            sort_keys=True,
            ensure_ascii=False,
            default=str,
        )
        return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type,
            "payload": self.payload,
            "source": self.source,
            "ts": self.ts,
            "idempotency_key": self.idempotency_key,
            "workspace": self.workspace,
            "app_id": self.app_id,
            "mapped": self.mapped,
            "mapping_meta": self.mapping_meta,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Event":
        return cls(
            type=d.get("type", ""),
            payload=dict(d.get("payload") or {}),
            source=d.get("source", ""),
            ts=d.get("ts", ""),
            idempotency_key=d.get("idempotency_key", ""),
            workspace=d.get("workspace", "default"),
            app_id=d.get("app_id"),
            mapped=bool(d.get("mapped", False)),
            mapping_meta=d.get("mapping_meta"),
        )
