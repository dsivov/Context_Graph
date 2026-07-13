"""P1 test gate (M1) — the loop: webhook → map → rule → decision quad.

Proves *events-in → deterministic-decision-out* end to end with no LLM on the
path: a webhook payload is normalized, deterministically mapped against the
ontology, appended to the durable ingress log (idempotent), published on the
bus, gated by the workspace rules, and written to the graph as a quad
``(h, r, t, rc)``. The same delivery twice yields ONE decision. Offline.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from context_graph.events import InProcessBus
from context_graph.events.store import InMemoryIngressLog
from context_graph.integration import (
    DecisionBinding,
    DecisionSubscriber,
    IngressService,
    MappingSpec,
)
from context_graph.ontology.schema import ObjectType, Ontology, Property, PropertyKind
from context_graph.rules import InMemoryRuleStore, RulesService
from context_graph.rules.gate import RuleViolation
from context_graph.types import RelationContext
from lightrag.api.routers.ingress_routes import create_ingress_routes

WS = "acme"

# Deterministic policy — no sim(), no model, no LLM.
DSL = """
rule "large discount needs review"  priority 10
when
    percent > 0.15
    and approved_via == "slack"
then
    flag("Discount >15% via Slack — route to Finance for review")
end

rule "half-off is forbidden"  priority 20
when
    percent > 0.5
then
    reject("Discounts above 50% are never approved automatically")
end
"""


def _ontology() -> Ontology:
    onto = Ontology(name="sales", version=1)
    onto.define_object(
        ObjectType(name="DiscountRequest")
        .add(Property(name="customer", kind=PropertyKind.STRING, required=True))
        .add(Property(name="requested_by", kind=PropertyKind.STRING, required=True))
        .add(Property(name="discount", kind=PropertyKind.PERCENT, required=True))
        .add(Property(name="channel", kind=PropertyKind.STRING))
    )
    return onto


def _make_cg(gate):
    """A ContextGraph stand-in with mocked storage and the REAL emit path
    (same technique as tests/test_context_graph.py)."""
    from context_graph.core import ContextGraph

    graph = AsyncMock()
    graph.has_edge = AsyncMock(return_value=False)
    graph.get_node = AsyncMock(return_value=None)

    cg = MagicMock()
    cg.chunk_entity_relation_graph = graph
    cg.decisions_vdb = AsyncMock()
    cg.relationships_vdb = AsyncMock()
    cg.rules_gate = gate
    cg.emit_decision_trace = ContextGraph.emit_decision_trace.__get__(cg, type(cg))
    cg._index_decision = ContextGraph._index_decision.__get__(cg, type(cg))
    cg._persist_decision_indices = ContextGraph._persist_decision_indices.__get__(
        cg, type(cg)
    )
    return cg, graph


def _make_loop():
    """Wire the full P1 loop against in-memory adapters. Returns
    (ingress_service, cg_mock, graph_mock)."""
    rules = RulesService(InMemoryRuleStore(now=lambda: 1.0))
    rules.save(WS, DSL, {})
    cg, graph = _make_cg(rules.gate_for(WS))

    onto = _ontology()
    ingress = IngressService(
        InMemoryIngressLog(),
        InProcessBus(),
        ontology_resolver=lambda ws: onto if ws == WS else None,
        now=lambda: 1_752_400_000.0,
    )
    ingress.set_mapping(WS, "webhook", MappingSpec(
        event_type="discount.requested",
        object_type="DiscountRequest",
        fields={"customer": "customer", "requested_by": "requested_by",
                "discount": "discount", "channel": "channel"},
        defaults={"channel": "slack"},
    ))
    ingress.bus.subscribe("*", DecisionSubscriber(
        lambda ws: cg,
        DecisionBinding(
            src_field="requested_by",
            tgt_field="customer",
            relation_type="requests_discount",
            rc_fields={
                "quantitative_data": "{discount:.0%} discount",
                "approved_via": "channel",
                "decision_trace": "Discount request: {discount:.0%} for {customer}",
            },
        ),
    ))
    return ingress, cg, graph


PAYLOAD = {"id": "d-1", "customer": "MegaCorp",
           "requested_by": "Sarah Chen", "discount": "25%"}


@pytest.mark.offline
@pytest.mark.asyncio
class TestLoopE2E:
    async def test_webhook_to_decision_quad(self):
        ingress, cg, graph = _make_loop()

        result = await ingress.receive(WS, "webhook", PAYLOAD)
        assert result.accepted and not result.duplicate

        # Mapped + coerced against the ontology.
        event = result.event
        assert event.type == "discount.requested"
        assert event.mapped is True
        assert event.payload["discount"] == 0.25
        assert event.idempotency_key == "d-1"

        # Logged durably.
        assert ingress.log.count(WS) == 1

        # The decision quad was written to the graph.
        graph.upsert_edge.assert_awaited_once()
        src, tgt = graph.upsert_edge.call_args.args[:2]
        edge_data = graph.upsert_edge.call_args.kwargs["edge_data"]
        assert (src, tgt) == ("Sarah Chen", "MegaCorp")
        assert edge_data["keywords"] == "requests_discount"

        # The rc carries the lineage, and the rule FLAGged it (>15% via slack).
        rc = RelationContext.from_json(edge_data["relation_context"])
        assert rc.quantitative_data == "25% discount"
        assert rc.approved_via == "slack"
        assert rc.provenance.startswith("ingress:webhook:")
        assert edge_data["needs_review"] is True
        audit = json.loads(edge_data["rules_audit"])
        assert audit["outcome"] == "FLAG"

    async def test_retry_is_one_decision_not_two(self):
        ingress, cg, graph = _make_loop()

        first = await ingress.receive(WS, "webhook", PAYLOAD)
        retry = await ingress.receive(WS, "webhook", dict(PAYLOAD))  # re-delivery

        assert not first.duplicate and retry.duplicate
        assert ingress.log.count(WS) == 1          # logged once
        graph.upsert_edge.assert_awaited_once()    # ONE decision, not two

    async def test_rules_gate_reject_blocks_the_quad(self):
        ingress, cg, graph = _make_loop()
        forbidden = {**PAYLOAD, "id": "d-2", "discount": "60%"}

        with pytest.raises(RuleViolation) as exc:
            await ingress.receive(WS, "webhook", forbidden)

        assert exc.value.decision.outcome == "REJECT"
        graph.upsert_edge.assert_not_awaited()     # nothing persisted
        assert ingress.log.count(WS) == 1          # the delivery itself IS logged

    async def test_unmapped_connector_flows_as_passthrough(self):
        rules = RulesService(InMemoryRuleStore(now=lambda: 1.0))
        rules.save(WS, DSL, {})
        cg, graph = _make_cg(rules.gate_for(WS))
        ingress = IngressService(InMemoryIngressLog(), InProcessBus(),
                                 now=lambda: 1_752_400_000.0)
        ingress.bus.subscribe("*", DecisionSubscriber(lambda ws: cg))  # defaults

        # No mapping spec: passthrough event; the default binding auto-maps
        # payload keys that share RelationContext field names.
        result = await ingress.receive(WS, "webhook", {
            "actor": "Ops Bot", "object": "Server42",
            "approved_by": "VP_Smith", "decision_trace": "restart approved",
        })
        assert result.event.mapped is False
        assert result.event.type == "webhook.received"
        edge_data = graph.upsert_edge.call_args.kwargs["edge_data"]
        rc = RelationContext.from_json(edge_data["relation_context"])
        assert rc.approved_by == "VP_Smith"
        assert rc.decision_trace == "restart approved"


# ── the same loop through the HTTP front door ────────────────────────────────


class _FakeRag:
    """Marks the app as CG-capable for the router's 503 guard."""
    rules_gate = None


@pytest.mark.offline
def test_ingress_routes_end_to_end():
    ingress, cg, graph = _make_loop()
    app = FastAPI()
    app.include_router(create_ingress_routes(
        _FakeRag(), ingress, workspace_resolver=lambda: WS))
    client = TestClient(app)

    # First delivery: accepted, mapped, decision written.
    r = client.post("/ingress/webhook/webhook", json=PAYLOAD)
    assert r.status_code == 200
    body = r.json()
    assert body["accepted"] and not body["duplicate"] and body["mapped"]
    assert body["event_type"] == "discount.requested"
    graph.upsert_edge.assert_awaited_once()

    # Retry: acknowledged as duplicate, not re-published.
    r = client.post("/ingress/webhook/webhook", json=PAYLOAD)
    assert r.status_code == 200 and r.json()["duplicate"] is True
    graph.upsert_edge.assert_awaited_once()

    # A REJECTed decision maps to 422 with the audit record.
    r = client.post("/ingress/webhook/webhook",
                    json={**PAYLOAD, "id": "d-9", "discount": "70%"})
    assert r.status_code == 422
    assert r.json()["detail"]["outcome"] == "REJECT"

    # The log replays both distinct deliveries; unknown connector is 404.
    r = client.get("/ingress/log")
    assert r.status_code == 200
    assert r.json()["count"] == 2
    assert [e["type"] for e in r.json()["events"]] == ["discount.requested"] * 2
    assert client.post("/ingress/webhook/nope", json={}).status_code == 404

    # Connector registry lists the default webhook connector.
    names = [c["name"] for c in client.get("/ingress/connectors").json()["connectors"]]
    assert "webhook" in names
