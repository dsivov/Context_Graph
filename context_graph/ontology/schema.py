"""CG-native ontology schema (P2, step 1 — the schema model).

A lightweight, typed schema for Context Graph — object types with typed
properties, directed link types with cardinality, and validation. Deliberately
*not* RDF/OWL: this is our own dataclass model, easy to persist as JSON and to
wire into the rules engine's ``projection`` so rules reason over typed values
instead of whatever the LLM happened to extract (see
``docs/CLOSING_THE_GAPS.html`` §07). A standards (OWL/SHACL) export can be
layered on later.

Core types:

* :class:`Property`   — a typed, optionally-constrained attribute.
* :class:`ObjectType` — a named entity type with properties.
* :class:`LinkType`   — a named, directed relation between object types, with
  cardinality and its own typed properties (which map onto the edge ``rc``).
* :class:`Ontology`   — the container; validates entities/relations and
  serializes to/from JSON.

Validation is *coercing* — it returns cleaned values (e.g. ``"$25,000"`` →
``25000.0``) alongside errors — so the same pass that checks a record also
normalises it. Money/percent parsing reuses the rules-engine parsers so the two
layers agree on how free-text numbers are read.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional

from context_graph.rules.projection import parse_amount, parse_percent


# ─────────────────────────────────────────────────────────────────────────────
# Primitive kinds & cardinality
# ─────────────────────────────────────────────────────────────────────────────


class PropertyKind(str, Enum):
    """The primitive types a property value may take."""

    STRING = "string"      # short free text
    TEXT = "text"          # long free text
    INTEGER = "integer"
    FLOAT = "float"
    BOOLEAN = "boolean"
    DATE = "date"          # ISO-8601 YYYY-MM-DD
    ENUM = "enum"          # one of enum_values
    MONEY = "money"        # numeric amount (parses "$25,000")
    PERCENT = "percent"    # fraction 0..1 (parses "20%")


_NUMERIC_KINDS = {
    PropertyKind.INTEGER, PropertyKind.FLOAT, PropertyKind.MONEY, PropertyKind.PERCENT
}


class Cardinality(str, Enum):
    """Multiplicity of a link between a head (source) and tail (target)."""

    ONE_TO_ONE = "1:1"
    ONE_TO_MANY = "1:N"
    MANY_TO_ONE = "N:1"
    MANY_TO_MANY = "N:M"

    @property
    def source_is_unique(self) -> bool:
        """True if a given target may be linked from at most one source."""
        return self in (Cardinality.ONE_TO_ONE, Cardinality.ONE_TO_MANY)

    @property
    def target_is_unique(self) -> bool:
        """True if a given source may link to at most one target."""
        return self in (Cardinality.ONE_TO_ONE, Cardinality.MANY_TO_ONE)


# ─────────────────────────────────────────────────────────────────────────────
# Validation report
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class ValidationReport:
    """Outcome of validating a record against a type."""

    ok: bool = True
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    coerced: Dict[str, Any] = field(default_factory=dict)

    def _fail(self, msg: str) -> None:
        self.ok = False
        self.errors.append(msg)


# ─────────────────────────────────────────────────────────────────────────────
# Property
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class Property:
    """A typed, optionally-constrained attribute of an object or link type."""

    name: str
    kind: PropertyKind
    required: bool = False
    description: str = ""
    enum_values: Optional[List[str]] = None   # required when kind is ENUM
    minimum: Optional[float] = None            # numeric kinds only
    maximum: Optional[float] = None

    def __post_init__(self) -> None:
        if isinstance(self.kind, str):
            self.kind = PropertyKind(self.kind)
        if self.kind is PropertyKind.ENUM and not self.enum_values:
            raise ValueError(f"Property '{self.name}': ENUM requires enum_values")
        if (self.minimum is not None or self.maximum is not None) and self.kind not in _NUMERIC_KINDS:
            raise ValueError(f"Property '{self.name}': min/max only apply to numeric kinds")

    def coerce(self, value: Any) -> Any:
        """Coerce a raw value to this property's type, or raise ValueError."""
        k = self.kind
        if k in (PropertyKind.STRING, PropertyKind.TEXT):
            return str(value)
        if k is PropertyKind.INTEGER:
            iv = int(float(value)) if not isinstance(value, bool) else int(value)
            return iv
        if k is PropertyKind.FLOAT:
            return float(value)
        if k is PropertyKind.BOOLEAN:
            if isinstance(value, bool):
                return value
            s = str(value).strip().lower()
            if s in ("true", "1", "yes", "y"):
                return True
            if s in ("false", "0", "no", "n"):
                return False
            raise ValueError(f"not a boolean: {value!r}")
        if k is PropertyKind.DATE:
            # raises ValueError on a bad date
            return datetime.date.fromisoformat(str(value)).isoformat()
        if k is PropertyKind.ENUM:
            if str(value) not in (self.enum_values or []):
                raise ValueError(
                    f"{value!r} not in enum {self.enum_values}"
                )
            return str(value)
        if k is PropertyKind.MONEY:
            v = value if isinstance(value, (int, float)) else parse_amount(str(value))
            if v is None:
                raise ValueError(f"could not parse money from {value!r}")
            return float(v)
        if k is PropertyKind.PERCENT:
            if isinstance(value, (int, float)):
                v = float(value)
            else:
                v = parse_percent(str(value))
            if v is None:
                raise ValueError(f"could not parse percent from {value!r}")
            return v
        raise ValueError(f"unknown kind {k}")  # pragma: no cover

    def validate(self, value: Any) -> Any:
        """Coerce and range-check a value; raises ValueError on failure."""
        coerced = self.coerce(value)
        if self.kind in _NUMERIC_KINDS:
            if self.minimum is not None and coerced < self.minimum:
                raise ValueError(f"{coerced} < minimum {self.minimum}")
            if self.maximum is not None and coerced > self.maximum:
                raise ValueError(f"{coerced} > maximum {self.maximum}")
        return coerced

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"name": self.name, "kind": self.kind.value}
        if self.required:
            d["required"] = True
        if self.description:
            d["description"] = self.description
        if self.enum_values is not None:
            d["enum_values"] = list(self.enum_values)
        if self.minimum is not None:
            d["minimum"] = self.minimum
        if self.maximum is not None:
            d["maximum"] = self.maximum
        return d

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "Property":
        return cls(
            name=d["name"], kind=PropertyKind(d["kind"]),
            required=bool(d.get("required", False)),
            description=d.get("description", ""),
            enum_values=list(d["enum_values"]) if d.get("enum_values") is not None else None,
            minimum=d.get("minimum"), maximum=d.get("maximum"),
        )


def _validate_properties(
    props: Mapping[str, Property], attrs: Mapping[str, Any], *, label: str
) -> ValidationReport:
    """Validate a record's attributes against a property map."""
    report = ValidationReport()
    for name, prop in props.items():
        if name not in attrs or attrs[name] is None:
            if prop.required:
                report._fail(f"{label}: missing required property '{name}'")
            continue
        try:
            report.coerced[name] = prop.validate(attrs[name])
        except ValueError as e:
            report._fail(f"{label}.{name}: {e}")
    for name in attrs:
        if name not in props:
            report.warnings.append(f"{label}: unknown property '{name}'")
    return report


# ─────────────────────────────────────────────────────────────────────────────
# Object & link types
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class ObjectType:
    """A named entity type with typed properties (e.g. ``Order{value:money}``)."""

    name: str
    properties: Dict[str, Property] = field(default_factory=dict)
    description: str = ""

    def add(self, prop: Property) -> "ObjectType":
        self.properties[prop.name] = prop
        return self

    def validate(self, attrs: Mapping[str, Any]) -> ValidationReport:
        return _validate_properties(self.properties, attrs, label=self.name)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "properties": [p.to_dict() for p in self.properties.values()],
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "ObjectType":
        props = {p["name"]: Property.from_dict(p) for p in d.get("properties", [])}
        return cls(name=d["name"], properties=props, description=d.get("description", ""))


@dataclass
class LinkType:
    """A named, directed relation between object types, with cardinality.

    ``source_types`` / ``target_types`` list the allowed head/tail object types
    (empty = any). Link properties map onto the edge's relation context (rc).
    """

    name: str
    source_types: List[str] = field(default_factory=list)
    target_types: List[str] = field(default_factory=list)
    cardinality: Cardinality = Cardinality.MANY_TO_MANY
    directed: bool = True
    description: str = ""
    properties: Dict[str, Property] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.cardinality, str):
            self.cardinality = Cardinality(self.cardinality)

    def allows(self, src_type: Optional[str], tgt_type: Optional[str]) -> bool:
        """Whether a head/tail object-type pair is permitted by this link."""
        ok_src = (not self.source_types) or (src_type in self.source_types)
        ok_tgt = (not self.target_types) or (tgt_type in self.target_types)
        if ok_src and ok_tgt:
            return True
        # For an undirected link, accept the reversed pair too.
        if not self.directed:
            r_src = (not self.source_types) or (tgt_type in self.source_types)
            r_tgt = (not self.target_types) or (src_type in self.target_types)
            return r_src and r_tgt
        return False

    def validate(
        self, src_type: Optional[str], tgt_type: Optional[str],
        attrs: Optional[Mapping[str, Any]] = None,
    ) -> ValidationReport:
        report = _validate_properties(self.properties, attrs or {}, label=self.name)
        if not self.allows(src_type, tgt_type):
            report._fail(
                f"{self.name}: {src_type} → {tgt_type} not allowed "
                f"(source={self.source_types or 'any'}, target={self.target_types or 'any'})"
            )
        return report

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "source_types": list(self.source_types),
            "target_types": list(self.target_types),
            "cardinality": self.cardinality.value,
            "directed": self.directed,
            "description": self.description,
            "properties": [p.to_dict() for p in self.properties.values()],
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "LinkType":
        props = {p["name"]: Property.from_dict(p) for p in d.get("properties", [])}
        return cls(
            name=d["name"],
            source_types=list(d.get("source_types", [])),
            target_types=list(d.get("target_types", [])),
            cardinality=Cardinality(d.get("cardinality", "N:M")),
            directed=bool(d.get("directed", True)),
            description=d.get("description", ""),
            properties=props,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Ontology
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class Ontology:
    """A CG-native ontology: object types + link types, with validation."""

    name: str = ""
    version: int = 1
    object_types: Dict[str, ObjectType] = field(default_factory=dict)
    link_types: Dict[str, LinkType] = field(default_factory=dict)

    # -- authoring ---------------------------------------------------------

    def define_object(self, obj: ObjectType) -> "Ontology":
        self.object_types[obj.name] = obj
        return self

    def define_link(self, link: LinkType) -> "Ontology":
        self.link_types[link.name] = link
        return self

    def object_type_names(self) -> List[str]:
        return sorted(self.object_types)

    def link_type_names(self) -> List[str]:
        return sorted(self.link_types)

    def has_object(self, name: str) -> bool:
        return name in self.object_types

    def has_link(self, name: str) -> bool:
        return name in self.link_types

    # -- validation --------------------------------------------------------

    def validate_entity(self, type_name: str, attrs: Mapping[str, Any]) -> ValidationReport:
        obj = self.object_types.get(type_name)
        if obj is None:
            r = ValidationReport()
            r._fail(f"unknown object type '{type_name}'")
            return r
        return obj.validate(attrs)

    def validate_relation(
        self, link_name: str, src_type: Optional[str], tgt_type: Optional[str],
        attrs: Optional[Mapping[str, Any]] = None,
    ) -> ValidationReport:
        link = self.link_types.get(link_name)
        if link is None:
            r = ValidationReport()
            r._fail(f"unknown link type '{link_name}'")
            return r
        return link.validate(src_type, tgt_type, attrs)

    # -- self-consistency --------------------------------------------------

    def lint(self) -> List[str]:
        """Structural problems in the ontology itself (e.g. links referencing
        undefined object types). Empty list = consistent."""
        problems: List[str] = []
        for link in self.link_types.values():
            for t in list(link.source_types) + list(link.target_types):
                if t not in self.object_types:
                    problems.append(
                        f"link '{link.name}' references undefined object type '{t}'"
                    )
        return problems

    # -- serialization -----------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "object_types": [o.to_dict() for o in self.object_types.values()],
            "link_types": [lt.to_dict() for lt in self.link_types.values()],
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "Ontology":
        return cls(
            name=d.get("name", ""),
            version=int(d.get("version", 1)),
            object_types={o["name"]: ObjectType.from_dict(o) for o in d.get("object_types", [])},
            link_types={lt["name"]: LinkType.from_dict(lt) for lt in d.get("link_types", [])},
        )
