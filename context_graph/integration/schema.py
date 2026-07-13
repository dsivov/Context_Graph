"""Integration-engine contracts (P1) — raw records and declarative mappings.

A connector normalizes an inbound delivery (webhook push, poll batch, message)
into a :class:`RawRecord`; a mapper turns that record into an ontology-typed
:class:`~context_graph.events.schema.Event` under a declared
:class:`MappingSpec`. Both artifacts are deliberately declarative — a mapping
is data, versionable and diffable in the Studio later, never code.

The decision loop closes with :class:`DecisionBinding`: which payload fields
name the head/tail entities of the decision quad and how the rest of the
payload projects onto :class:`~context_graph.types.RelationContext`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional


class MappingError(ValueError):
    """A raw record could not be deterministically mapped (missing required
    field, failed coercion, unknown object type). The delivery is rejected —
    nothing is logged or published."""


@dataclass
class RawRecord:
    """One normalized inbound delivery, as produced by a connector.

    ``external_id`` is the source system's delivery/record id and becomes the
    event's ``idempotency_key`` (empty → content-hash fallback). ``ts`` is the
    source timestamp — connectors supply time, consumers never take a clock.
    """

    connector: str
    data: Dict[str, Any] = field(default_factory=dict)
    external_id: str = ""
    ts: str = ""
    source: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "connector": self.connector,
            "data": self.data,
            "external_id": self.external_id,
            "ts": self.ts,
            "source": self.source,
        }


@dataclass
class MappingSpec:
    """A declarative field map: raw record → typed event payload.

    ``fields`` maps a payload property name to a dot-path into the raw
    record's data (``"customer"`` or ``"order.buyer.name"``). ``defaults``
    fill properties whose source path is absent. When ``object_type`` names a
    type in the workspace ontology, the mapped attributes are validated and
    coerced against it (:meth:`Property.validate`) — a failure is a
    :class:`MappingError`, not a warning.
    """

    event_type: str
    fields: Dict[str, str] = field(default_factory=dict)
    object_type: str = ""
    defaults: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_type": self.event_type,
            "fields": dict(self.fields),
            "object_type": self.object_type,
            "defaults": dict(self.defaults),
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "MappingSpec":
        return cls(
            event_type=d["event_type"],
            fields=dict(d.get("fields") or {}),
            object_type=d.get("object_type", ""),
            defaults=dict(d.get("defaults") or {}),
        )


@dataclass
class DecisionBinding:
    """How an event's payload becomes a decision quad ``(h, r, t, rc)``.

    ``src_field`` / ``tgt_field`` name the payload keys holding the head and
    tail entity names. ``relation_type`` is the relation keyword (empty → the
    event's ``type``). ``rc_fields`` maps a ``RelationContext`` field to either
    a payload key (``"approved_by": "approver"``) or, when the value contains
    ``{``, a ``str.format`` template over the payload
    (``"quantitative_data": "{discount:.0%} discount"``). Empty ``rc_fields``
    auto-maps payload keys that share a ``RelationContext`` field name.
    """

    src_field: str = "actor"
    tgt_field: str = "object"
    relation_type: str = ""
    rc_fields: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "src_field": self.src_field,
            "tgt_field": self.tgt_field,
            "relation_type": self.relation_type,
            "rc_fields": dict(self.rc_fields),
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "DecisionBinding":
        return cls(
            src_field=d.get("src_field", "actor"),
            tgt_field=d.get("tgt_field", "object"),
            relation_type=d.get("relation_type", ""),
            rc_fields=dict(d.get("rc_fields") or {}),
        )


def pluck(data: Mapping[str, Any], path: str) -> Optional[Any]:
    """Resolve a dot-path into nested mappings; ``None`` when any hop is absent."""
    cur: Any = data
    for part in path.split("."):
        if not isinstance(cur, Mapping) or part not in cur:
            return None
        cur = cur[part]
    return cur
