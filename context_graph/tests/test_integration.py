"""Unit tests for the P1 integration engine: webhook connector, dot-path
pluck, and the deterministic mapper (field map + ontology coercion). Offline."""

from __future__ import annotations

import pytest

from context_graph.integration import (
    DecisionBinding,
    MappingError,
    MappingSpec,
    WebhookConnector,
    pluck,
)
from context_graph.integration.mapper import DeterministicMapper
from context_graph.ontology.schema import ObjectType, Ontology, Property, PropertyKind


def _ontology() -> Ontology:
    onto = Ontology(name="sales", version=1)
    onto.define_object(
        ObjectType(name="DiscountRequest")
        .add(Property(name="customer", kind=PropertyKind.STRING, required=True))
        .add(Property(name="discount", kind=PropertyKind.PERCENT, maximum=1.0))
        .add(Property(name="amount", kind=PropertyKind.MONEY))
    )
    return onto


# ── WebhookConnector ─────────────────────────────────────────────────────────


@pytest.mark.offline
class TestWebhookConnector:
    def test_header_id_wins_over_payload(self):
        rec = WebhookConnector().receive(
            {"id": "payload-id", "a": 1},
            headers={"X-Delivery-Id": "hdr-id"},
        )
        assert rec.external_id == "hdr-id"
        assert rec.connector == "webhook"
        assert rec.data == {"id": "payload-id", "a": 1}

    def test_payload_id_fallback_and_ts(self):
        rec = WebhookConnector().receive(
            {"event_id": "e-7", "timestamp": "2026-07-13T10:00:00Z"}
        )
        assert rec.external_id == "e-7"
        assert rec.ts == "2026-07-13T10:00:00Z"

    def test_no_id_leaves_content_hash_fallback(self):
        rec = WebhookConnector().receive({"n": 1})
        assert rec.external_id == ""     # Event.dedupe_key() hashes content

    def test_non_object_payload_rejected(self):
        with pytest.raises(ValueError):
            WebhookConnector().receive([1, 2, 3])


# ── pluck ────────────────────────────────────────────────────────────────────


@pytest.mark.offline
def test_pluck_dot_paths():
    data = {"order": {"buyer": {"name": "ACME"}}, "flat": 1}
    assert pluck(data, "flat") == 1
    assert pluck(data, "order.buyer.name") == "ACME"
    assert pluck(data, "order.missing") is None
    assert pluck(data, "order.buyer.name.deeper") is None


# ── DeterministicMapper ──────────────────────────────────────────────────────


@pytest.mark.offline
@pytest.mark.asyncio
class TestDeterministicMapper:
    async def test_maps_and_coerces_against_ontology(self):
        spec = MappingSpec(
            event_type="discount.requested",
            object_type="DiscountRequest",
            fields={"customer": "cust.name", "discount": "disc", "amount": "amt"},
        )
        record = WebhookConnector().receive(
            {"cust": {"name": "MegaCorp"}, "disc": "25%", "amt": "$25,000"}
        )
        event = await DeterministicMapper(spec).map(record, _ontology())
        assert event.type == "discount.requested"
        assert event.mapped is True
        assert event.payload == {
            "customer": "MegaCorp", "discount": 0.25, "amount": 25000.0,
        }
        assert event.mapping_meta["object_type"] == "DiscountRequest"

    async def test_missing_required_property_rejects(self):
        spec = MappingSpec(
            event_type="discount.requested",
            object_type="DiscountRequest",
            fields={"discount": "disc"},   # no customer mapping
        )
        record = WebhookConnector().receive({"disc": "10%"})
        with pytest.raises(MappingError, match="customer"):
            await DeterministicMapper(spec).map(record, _ontology())

    async def test_failed_coercion_rejects(self):
        spec = MappingSpec(
            event_type="discount.requested",
            object_type="DiscountRequest",
            fields={"customer": "c", "discount": "disc"},
        )
        record = WebhookConnector().receive({"c": "ACME", "disc": "not a percent"})
        with pytest.raises(MappingError, match="discount"):
            await DeterministicMapper(spec).map(record, _ontology())

    async def test_object_type_without_ontology_rejects(self):
        spec = MappingSpec(event_type="t", object_type="DiscountRequest",
                           fields={"customer": "c"})
        record = WebhookConnector().receive({"c": "ACME"})
        with pytest.raises(MappingError, match="no ontology"):
            await DeterministicMapper(spec).map(record, None)

    async def test_defaults_fill_absent_paths(self):
        spec = MappingSpec(
            event_type="discount.requested",
            object_type="DiscountRequest",
            fields={"customer": "c", "discount": "disc"},
            defaults={"discount": "5%"},
        )
        record = WebhookConnector().receive({"c": "ACME"})
        event = await DeterministicMapper(spec).map(record, _ontology())
        assert event.payload["discount"] == 0.05

    async def test_untyped_spec_passes_fields_through(self):
        spec = MappingSpec(event_type="raw.thing", fields={"a": "x", "b": "y.z"})
        record = WebhookConnector().receive({"x": 1, "y": {"z": "two"}, "noise": 9})
        event = await DeterministicMapper(spec).map(record, None)
        assert event.payload == {"a": 1, "b": "two"}   # only declared fields cross

    async def test_event_type_required(self):
        with pytest.raises(ValueError):
            DeterministicMapper(MappingSpec(event_type=""))


# ── schema round-trips ───────────────────────────────────────────────────────


@pytest.mark.offline
def test_spec_and_binding_roundtrip():
    spec = MappingSpec(event_type="t", object_type="O",
                       fields={"a": "b.c"}, defaults={"a": 1})
    assert MappingSpec.from_dict(spec.to_dict()) == spec
    binding = DecisionBinding(src_field="requested_by", tgt_field="customer",
                              relation_type="requests_discount",
                              rc_fields={"approved_via": "channel"})
    assert DecisionBinding.from_dict(binding.to_dict()) == binding
