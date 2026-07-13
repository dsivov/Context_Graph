"""Ingress service — connector → mapper → ingress log → bus (P1).

The one write path into the event backbone. Every accepted delivery is
appended to the durable :class:`IngressLog` *before* it is published
(at-least-once, replayable); a duplicate delivery is acknowledged but neither
stored nor re-published (idempotent). Mapping specs are registered per
``(workspace, connector)``; a connector with no spec still flows, as an
unmapped passthrough event.

:class:`DecisionSubscriber` closes the P1 loop: on each event it projects the
payload onto a decision quad via a declared :class:`DecisionBinding` and calls
``emit_decision_trace`` — which runs the workspace's rules gate (REJECT raises
``RuleViolation`` up through ``receive``) and writes the ``(h, r, t, rc)``
quad to the graph.
"""

from __future__ import annotations

import dataclasses
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from lightrag.utils import logger

from context_graph.events.schema import Event
from context_graph.events.service import EventBus
from context_graph.events.store import IngressLog
from context_graph.integration.connectors import DEFAULT_CONNECTORS, IngressConnector
from context_graph.integration.mapper import DeterministicMapper
from context_graph.integration.schema import DecisionBinding, MappingSpec
from context_graph.ontology.schema import Ontology
from context_graph.types import RelationContext

_RC_FIELDS = {f.name for f in dataclasses.fields(RelationContext)}


@dataclass
class IngressResult:
    """Outcome of one delivery: acknowledged always, published once."""

    accepted: bool
    duplicate: bool
    event: Event


class IngressService:
    def __init__(
        self,
        log: IngressLog,
        bus: EventBus,
        *,
        ontology_resolver: Optional[Callable[[str], Optional[Ontology]]] = None,
        now: Callable[[], float] = time.time,
    ) -> None:
        self._log = log
        self._bus = bus
        self._resolve_ontology = ontology_resolver
        self._now = now
        self._connectors: Dict[str, IngressConnector] = {}
        self._mappings: Dict[Tuple[str, str], MappingSpec] = {}
        for cls in DEFAULT_CONNECTORS:
            self.register_connector(cls())

    @property
    def log(self) -> IngressLog:
        return self._log

    @property
    def bus(self) -> EventBus:
        return self._bus

    # -- registry ------------------------------------------------------------

    def register_connector(self, connector: IngressConnector) -> None:
        self._connectors[connector.name] = connector

    def connector(self, name: str) -> Optional[IngressConnector]:
        return self._connectors.get(name)

    def connectors(self) -> List[Dict[str, str]]:
        return [
            {"name": c.name, "description": c.description}
            for c in self._connectors.values()
        ]

    def set_mapping(self, workspace: str, connector: str, spec: MappingSpec) -> None:
        self._mappings[(workspace, connector)] = spec

    def get_mapping(self, workspace: str, connector: str) -> Optional[MappingSpec]:
        return self._mappings.get((workspace, connector))

    # -- the write path --------------------------------------------------------

    async def receive(
        self,
        workspace: str,
        connector_name: str,
        payload: Any,
        *,
        headers: Optional[Dict[str, str]] = None,
        source: str = "",
    ) -> IngressResult:
        """Normalize, map, log, publish. Raises ``LookupError`` for an unknown
        connector, ``ValueError``/``MappingError`` for a bad delivery, and
        propagates ``RuleViolation`` from downstream subscribers (nothing was
        persisted for the rejected decision; the delivery itself stays logged)."""
        connector = self._connectors.get(connector_name)
        if connector is None:
            raise LookupError(f"unknown ingress connector '{connector_name}'")

        record = connector.receive(payload, headers=headers, source=source)
        if not record.ts:
            # The ingress edge is the only place the platform takes a clock.
            record.ts = datetime.fromtimestamp(
                self._now(), tz=timezone.utc
            ).isoformat()

        spec = self._mappings.get((workspace, connector_name))
        if spec is not None:
            ontology = (
                self._resolve_ontology(workspace) if self._resolve_ontology else None
            )
            event = await DeterministicMapper(spec).map(record, ontology)
        else:
            event = Event(
                type=f"{connector_name}.received",
                payload=dict(record.data),
                source=record.source,
                ts=record.ts,
                idempotency_key=record.external_id,
                mapped=False,
            )
        event.workspace = workspace
        event.ts = event.ts or record.ts

        stored = await self._log.append(workspace, event)
        if not stored:
            logger.debug(
                f"ingress duplicate delivery on '{connector_name}' "
                f"({event.dedupe_key()}) — acknowledged, not re-published"
            )
            return IngressResult(accepted=True, duplicate=True, event=event)
        await self._bus.publish(event)
        return IngressResult(accepted=True, duplicate=False, event=event)


class DecisionSubscriber:
    """The P1 demo loop-closer: event → rules gate → decision quad.

    ``rag_resolver`` returns the workspace's ContextGraph-like instance (the
    server passes its request-scoped proxy). Events missing the bound head or
    tail field are skipped, so unrelated traffic on the bus is harmless.
    """

    def __init__(
        self,
        rag_resolver: Callable[[str], Any],
        binding: Optional[DecisionBinding] = None,
    ) -> None:
        self._resolve_rag = rag_resolver
        self._binding = binding or DecisionBinding()

    def _rc_kwargs(self, event: Event) -> Dict[str, Any]:
        payload = event.payload
        mapping = self._binding.rc_fields or {
            name: name for name in payload if name in _RC_FIELDS
        }
        kwargs: Dict[str, Any] = {}
        for rc_field, ref in mapping.items():
            if rc_field not in _RC_FIELDS:
                logger.warning(f"DecisionBinding: unknown rc field '{rc_field}'")
                continue
            if "{" in ref:  # a str.format template over the payload
                try:
                    kwargs[rc_field] = ref.format(**payload)
                except (KeyError, IndexError, ValueError):
                    continue
            elif ref in payload and payload[ref] is not None:
                kwargs[rc_field] = payload[ref]
        if "confidence_score" in kwargs:
            try:
                kwargs["confidence_score"] = float(kwargs["confidence_score"])
            except (TypeError, ValueError):
                del kwargs["confidence_score"]
        if "supporting_sentences" in kwargs and isinstance(
            kwargs["supporting_sentences"], str
        ):
            kwargs["supporting_sentences"] = [kwargs["supporting_sentences"]]
        kwargs.setdefault(
            "provenance", f"ingress:{event.source}:{event.dedupe_key()}"
        )
        return kwargs

    async def __call__(self, event: Event) -> None:
        b = self._binding
        src = event.payload.get(b.src_field)
        tgt = event.payload.get(b.tgt_field)
        if not src or not tgt:
            logger.debug(
                f"DecisionSubscriber: event '{event.type}' has no "
                f"'{b.src_field}'/'{b.tgt_field}' — skipped"
            )
            return
        rc = RelationContext(**self._rc_kwargs(event))
        rag = self._resolve_rag(event.workspace)
        decision = await rag.emit_decision_trace(
            str(src), str(tgt), b.relation_type or event.type, rc
        )
        outcome = decision.outcome if decision is not None else "no-gate"
        logger.info(
            f"DecisionSubscriber: {src} -[{b.relation_type or event.type}]-> "
            f"{tgt} recorded ({outcome})"
        )
