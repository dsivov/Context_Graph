"""Context Graph ontology — CG-native typed schema (P2).

The typed vocabulary the graph and the rules engine reason over: object types
with typed properties, directed link types with cardinality, and coercing
validation. See ``docs/CLOSING_THE_GAPS.html`` §07.

    from context_graph.ontology import Ontology, ObjectType, LinkType, Property, PropertyKind

    onto = Ontology(name="acme").define_object(
        ObjectType("Order").add(Property("value", PropertyKind.MONEY))
    )
    onto.validate_entity("Order", {"value": "$25,000"})   # → coerced {"value": 25000.0}
"""

from context_graph.ontology.schema import (
    PropertyKind,
    Cardinality,
    Property,
    ObjectType,
    LinkType,
    Ontology,
    ValidationReport,
)
from context_graph.ontology.store import (
    OntologyStore,
    JsonOntologyStore,
    InMemoryOntologyStore,
    validate_ontology,
)

__all__ = [
    "PropertyKind",
    "Cardinality",
    "Property",
    "ObjectType",
    "LinkType",
    "Ontology",
    "ValidationReport",
    "OntologyStore",
    "JsonOntologyStore",
    "InMemoryOntologyStore",
    "validate_ontology",
]
