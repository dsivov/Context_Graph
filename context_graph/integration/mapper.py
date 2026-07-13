"""Mappers — raw record → ontology-typed Event (P1, decision 3).

:class:`Mapper` is the port. :class:`DeterministicMapper` is the structured
head: a declared field map plus :meth:`Property.validate` coercion against the
domain ontology — no LLM, same input → same event, forever. The unstructured
tail (``AgentMapper``, P5) will sit behind the same port with the AgentSpec
contract and confidence-routing.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Protocol, runtime_checkable

from context_graph.events.schema import Event
from context_graph.integration.schema import MappingError, MappingSpec, RawRecord, pluck
from context_graph.ontology.schema import Ontology


@runtime_checkable
class Mapper(Protocol):
    async def map(
        self, record: RawRecord, ontology: Optional[Ontology] = None
    ) -> Event: ...


class DeterministicMapper:
    """Field-map + coerce. Only declared fields cross the boundary; when the
    spec names an ``object_type``, the ontology's typed properties coerce and
    range-check them (a failure rejects the delivery)."""

    name = "deterministic"

    def __init__(self, spec: MappingSpec) -> None:
        if not spec.event_type:
            raise ValueError("MappingSpec.event_type is required")
        self._spec = spec

    @property
    def spec(self) -> MappingSpec:
        return self._spec

    async def map(
        self, record: RawRecord, ontology: Optional[Ontology] = None
    ) -> Event:
        spec = self._spec
        attrs: Dict[str, Any] = {}
        missing: list[str] = []
        for prop, path in spec.fields.items():
            value = pluck(record.data, path)
            if value is None:
                if prop in spec.defaults:
                    attrs[prop] = spec.defaults[prop]
                else:
                    missing.append(f"{prop} (from '{path}')")
            else:
                attrs[prop] = value
        for prop, value in spec.defaults.items():
            attrs.setdefault(prop, value)

        warnings: list[str] = []
        if spec.object_type:
            if ontology is None:
                raise MappingError(
                    f"mapping targets object type '{spec.object_type}' but the "
                    "workspace has no ontology"
                )
            report = ontology.validate_entity(spec.object_type, attrs)
            if not report.ok:
                raise MappingError("; ".join(report.errors))
            warnings = list(report.warnings)
            # Coerced values win; spec fields unknown to the type pass through
            # as-is (the report already flagged them).
            attrs = {**attrs, **report.coerced}
        if missing:
            warnings.append(f"unmapped source fields: {', '.join(sorted(missing))}")

        return Event(
            type=spec.event_type,
            payload=attrs,
            source=record.source or record.connector,
            ts=record.ts,
            idempotency_key=record.external_id,
            mapped=True,
            mapping_meta={
                "mapper": self.name,
                "connector": record.connector,
                "object_type": spec.object_type,
                "fields": sorted(spec.fields),
                "warnings": warnings,
            },
        )
