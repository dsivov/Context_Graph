"""Extraction validation — conformance of LLM-extracted graph data to the ontology.

CG's extraction step turns a document into entities (``entity_name`` + a loose
``entity_type``) and relations (source/target + keywords). This module checks a
*batch* of those against a stored :class:`~context_graph.ontology.Ontology` and
returns an actionable report: which extractions conform, which reference a type
the ontology doesn't define, and which carry properties that fail validation.

Two policies:

* **open-world** (default) — an extracted type the ontology doesn't define is a
  *warning*, not a failure. Known types are validated fully. Suits a graph that
  is richer than the curated ontology.
* **closed-world** — an unknown type is a *violation*. Suits a governed graph
  where only ontology-defined types may enter.

The extracted records come in many key shapes (``entity_name``/``name``,
``src_id``/``source``, ``keywords``/``type``…), so the dataclasses provide
tolerant ``from_dict`` adapters over CG/LightRAG extraction output.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional

from context_graph.ontology.schema import LinkType, Ontology, _validate_properties

# Keys CG/LightRAG extraction has used for the same concept, most-specific first.
_ENTITY_NAME_KEYS = ("entity_name", "name", "id", "entity_id")
_ENTITY_TYPE_KEYS = ("entity_type", "type", "category")
_REL_SOURCE_KEYS = ("src_id", "source", "src", "source_name", "from")
_REL_TARGET_KEYS = ("tgt_id", "target", "tgt", "target_name", "to")
_REL_TYPE_KEYS = ("rel_type", "type", "keywords", "keyword", "relation")
_RESERVED_ENTITY = set(_ENTITY_NAME_KEYS) | set(_ENTITY_TYPE_KEYS) | {"description",
                                                                      "source_id"}
_RESERVED_REL = (set(_REL_SOURCE_KEYS) | set(_REL_TARGET_KEYS) | set(_REL_TYPE_KEYS)
                 | {"description", "source_id", "weight", "source_type", "target_type"})


def _side_ok(allowed: List[str], value: Optional[str]) -> bool:
    """A link endpoint is fine if unconstrained, unknown (unverifiable), or allowed."""
    return (not allowed) or (value is None) or (value in allowed)


def _endpoints_ok(link: LinkType, src: Optional[str], tgt: Optional[str]) -> bool:
    """Domain/range check that treats an unknown endpoint type as unverifiable
    rather than disallowed (mirrors LinkType.allows, incl. undirected reversal)."""
    if _side_ok(link.source_types, src) and _side_ok(link.target_types, tgt):
        return True
    if not link.directed:
        return _side_ok(link.source_types, tgt) and _side_ok(link.target_types, src)
    return False


def _first(d: Mapping[str, Any], keys: Iterable[str]) -> Optional[Any]:
    for k in keys:
        v = d.get(k)
        if v not in (None, ""):
            return v
    return None


@dataclass
class ExtractedEntity:
    name: str
    type: str
    attributes: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "ExtractedEntity":
        name = _first(d, _ENTITY_NAME_KEYS) or ""
        etype = _first(d, _ENTITY_TYPE_KEYS) or ""
        attrs = d.get("attributes")
        if not isinstance(attrs, Mapping):
            # treat any non-reserved top-level keys as properties
            attrs = {k: v for k, v in d.items() if k not in _RESERVED_ENTITY}
        return cls(name=str(name), type=str(etype), attributes=dict(attrs))


@dataclass
class ExtractedRelation:
    type: str
    source: str
    target: str
    source_type: Optional[str] = None
    target_type: Optional[str] = None
    attributes: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: Mapping[str, Any],
                  type_of: Optional[Mapping[str, str]] = None) -> "ExtractedRelation":
        """Build from an extracted relation dict. ``type_of`` (entity name -> type)
        lets domain/range checks run even when the relation dict omits endpoint types."""
        source = str(_first(d, _REL_SOURCE_KEYS) or "")
        target = str(_first(d, _REL_TARGET_KEYS) or "")
        rtype = _first(d, _REL_TYPE_KEYS) or ""
        if isinstance(rtype, (list, tuple)):
            rtype = rtype[0] if rtype else ""
        st = d.get("source_type")
        tt = d.get("target_type")
        if type_of is not None:
            st = st or type_of.get(source)
            tt = tt or type_of.get(target)
        attrs = d.get("attributes")
        if not isinstance(attrs, Mapping):
            attrs = {k: v for k, v in d.items() if k not in _RESERVED_REL}
        return cls(type=str(rtype), source=source, target=target,
                   source_type=st, target_type=tt, attributes=dict(attrs))


# Per-item outcome status.
CONFORMS = "conforms"
UNKNOWN_TYPE = "unknown_type"
INVALID = "invalid"


@dataclass
class ItemValidation:
    kind: str            # "entity" | "relation"
    ref: str             # human reference, e.g. "Person:Sarah Chen"
    status: str          # CONFORMS | UNKNOWN_TYPE | INVALID
    ok: bool             # acceptable under the active policy
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    coerced: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ExtractionReport:
    items: List[ItemValidation] = field(default_factory=list)
    closed_world: bool = False

    @property
    def ok(self) -> bool:
        return all(i.ok for i in self.items)

    def violations(self) -> List[ItemValidation]:
        return [i for i in self.items if not i.ok]

    def unknown_types(self) -> List[str]:
        return sorted({i.ref.split(":", 1)[0] for i in self.items
                       if i.status == UNKNOWN_TYPE})

    def summary(self) -> Dict[str, Any]:
        by_status: Dict[str, int] = {}
        for i in self.items:
            by_status[i.status] = by_status.get(i.status, 0) + 1
        return {
            "total": len(self.items),
            "ok": self.ok,
            "conforming": sum(1 for i in self.items if i.status == CONFORMS),
            "violations": len(self.violations()),
            "by_status": by_status,
            "unknown_types": self.unknown_types(),
            "policy": "closed_world" if self.closed_world else "open_world",
        }


class ExtractionValidator:
    """Checks batches of extracted entities/relations against an ontology."""

    def __init__(self, ontology: Ontology, *, closed_world: bool = False) -> None:
        self.ontology = ontology
        self.closed_world = closed_world

    # -- single records ----------------------------------------------------

    def check_entity(self, e: ExtractedEntity) -> ItemValidation:
        ref = f"{e.type or '?'}:{e.name}"
        if not self.ontology.has_object(e.type):
            return self._unknown("entity", ref, f"object type '{e.type}'")
        rep = self.ontology.validate_entity(e.type, e.attributes)
        return ItemValidation("entity", ref, CONFORMS if rep.ok else INVALID,
                              rep.ok, list(rep.errors), list(rep.warnings),
                              dict(rep.coerced))

    def check_relation(self, r: ExtractedRelation) -> ItemValidation:
        ref = f"{r.type or '?'}:{r.source}->{r.target}"
        link = self.ontology.link_types.get(r.type)
        if link is None:
            return self._unknown("relation", ref, f"link type '{r.type}'")

        # Properties always validated.
        rep = _validate_properties(link.properties, r.attributes or {}, label=link.name)
        errors, warnings = list(rep.errors), list(rep.warnings)

        # Domain/range: an *unknown* endpoint type can't be verified (warn, don't
        # fail); only a *known-but-disallowed* endpoint is a violation.
        if r.source_type is None and link.source_types:
            warnings.append(f"{link.name}: source type unknown — domain not verified")
        if r.target_type is None and link.target_types:
            warnings.append(f"{link.name}: target type unknown — range not verified")
        if not _endpoints_ok(link, r.source_type, r.target_type):
            errors.append(
                f"{link.name}: {r.source_type} → {r.target_type} not allowed "
                f"(source={link.source_types or 'any'}, target={link.target_types or 'any'})")

        ok = not errors
        return ItemValidation("relation", ref, CONFORMS if ok else INVALID,
                              ok, errors, warnings, dict(rep.coerced))

    def _unknown(self, kind: str, ref: str, what: str) -> ItemValidation:
        msg = f"{what} not defined in ontology '{self.ontology.name}'"
        if self.closed_world:
            return ItemValidation(kind, ref, UNKNOWN_TYPE, False, [msg], [], {})
        return ItemValidation(kind, ref, UNKNOWN_TYPE, True, [], [msg], {})

    # -- batches -----------------------------------------------------------

    def validate(self, entities: Iterable[Any] = (),
                 relations: Iterable[Any] = ()) -> ExtractionReport:
        """Validate a batch. Entities/relations may be the dataclasses or raw
        extraction dicts (adapted via ``from_dict``)."""
        ents = [e if isinstance(e, ExtractedEntity) else ExtractedEntity.from_dict(e)
                for e in entities]
        type_of = {e.name: e.type for e in ents if e.name and e.type}
        rels = [r if isinstance(r, ExtractedRelation)
                else ExtractedRelation.from_dict(r, type_of=type_of)
                for r in relations]
        # Backfill endpoint types from the batch's entities for domain/range checks
        # (covers pre-built relations that omitted source_type/target_type).
        for r in rels:
            if r.source_type is None:
                r.source_type = type_of.get(r.source)
            if r.target_type is None:
                r.target_type = type_of.get(r.target)
        items = [self.check_entity(e) for e in ents]
        items += [self.check_relation(r) for r in rels]
        return ExtractionReport(items=items, closed_world=self.closed_world)
