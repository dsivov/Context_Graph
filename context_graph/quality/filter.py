"""The node-quality filter — combines the deterministic gate (Layer 3) with the
ontology validator (Layer 2) and partitions extracted entities into keep vs
quarantine (D9–D12).

Policy (the decisions):
* **Open-world by default** (D10): an unknown entity type is a warning, not a
  rejection — kept and coerced. ``closed_world=True`` flips that per workspace.
* **No-ontology fallback** (D11): when a workspace hasn't authored an ontology, the
  built-in ``DEFAULT_ENTITY_TYPES`` act as the implicit schema.
* **Quarantine, don't drop** (D12): rejects are returned separately (with a reason)
  so the caller can hold them for review instead of losing them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from lightrag.constants import DEFAULT_ENTITY_TYPES

from context_graph.ontology.schema import ObjectType, Ontology
from context_graph.ontology.validate import ExtractedEntity, ExtractionValidator
from context_graph.quality.gate import quality_check


def ontology_from_types(types) -> Ontology:
    """Build a minimal ontology (property-less object types) from a name list —
    the D11 fallback schema when a workspace has none of its own."""
    o = Ontology()
    for t in types or ():
        if t:
            o.define_object(ObjectType(name=str(t)))
    return o


@dataclass
class FilterResult:
    kept: List[Dict[str, Any]] = field(default_factory=list)
    quarantined: List[Dict[str, Any]] = field(default_factory=list)   # + 'reason'

    def summary(self) -> Dict[str, Any]:
        reasons: Dict[str, int] = {}
        for q in self.quarantined:
            r = q.get("reason", "?")
            reasons[r] = reasons.get(r, 0) + 1
        return {
            "kept": len(self.kept),
            "quarantined": len(self.quarantined),
            "by_reason": reasons,
        }


def _field(e: Dict[str, Any], *keys: str) -> str:
    for k in keys:
        v = e.get(k)
        if v:
            return str(v)
    return ""


class NodeFilter:
    """Partition extracted entities into keep vs quarantine (Layers 2 + 3)."""

    def __init__(
        self, ontology: Optional[Ontology] = None, *,
        closed_world: bool = False, fallback_types=None,
    ) -> None:
        self._ontology = ontology or ontology_from_types(
            fallback_types or DEFAULT_ENTITY_TYPES
        )
        self._validator = ExtractionValidator(self._ontology, closed_world=closed_world)
        self.closed_world = closed_world

    def check(self, name: str, description: str = "", entity_type: str = "") -> Optional[str]:
        """Return a quarantine reason for one entity, or None if it passes."""
        v = quality_check(name, description, entity_type)   # Layer 3 (cheap, ontology-free)
        if not v.ok:
            return v.reason
        iv = self._validator.check_entity(                  # Layer 2 (ontology / policy)
            ExtractedEntity(name=name, type=entity_type or "")
        )
        if not iv.ok:  # closed-world type violation / missing required property
            return "; ".join(iv.errors) or "ontology violation"
        return None

    def partition(self, entities: List[Dict[str, Any]]) -> FilterResult:
        """Split *entities* (dicts with entity_name / entity_type / description)."""
        res = FilterResult()
        for e in entities:
            name = _field(e, "entity_name", "name")
            etype = _field(e, "entity_type", "type")
            desc = _field(e, "description")
            reason = self.check(name, desc, etype)
            if reason:
                res.quarantined.append({**e, "reason": reason})
            else:
                res.kept.append(e)
        return res
