"""Tests for the per-workspace ontology store (P2, step 2). Fully offline."""

from __future__ import annotations

import pytest

from context_graph.ontology import (
    Cardinality,
    InMemoryOntologyStore,
    JsonOntologyStore,
    LinkType,
    ObjectType,
    Ontology,
    Property,
    PropertyKind,
)


def _ecommerce() -> Ontology:
    onto = Ontology(name="shop")
    onto.define_object(ObjectType("Customer").add(Property("name", PropertyKind.STRING)))
    onto.define_object(ObjectType("Order").add(Property("value", PropertyKind.MONEY, required=True)))
    onto.define_link(LinkType("PLACES", ["Customer"], ["Order"], Cardinality.ONE_TO_MANY))
    return onto


@pytest.fixture(params=["memory", "json"])
def store(request, tmp_path):
    if request.param == "memory":
        return InMemoryOntologyStore(now=lambda: 100.0)
    return JsonOntologyStore(str(tmp_path / "ontology"), now=lambda: 100.0)


@pytest.mark.offline
def test_save_and_load_roundtrip(store):
    saved = store.save("acme", _ecommerce())
    assert saved.version == 1
    loaded = store.load("acme")
    assert loaded is not None
    assert loaded.object_type_names() == ["Customer", "Order"]
    assert loaded.link_types["PLACES"].cardinality is Cardinality.ONE_TO_MANY
    # typed property survives + validates
    assert loaded.validate_entity("Order", {"value": "$99"}).coerced == {"value": 99.0}


@pytest.mark.offline
def test_version_bumps_on_resave(store):
    store.save("acme", _ecommerce())
    assert store.save("acme", _ecommerce()).version == 2


@pytest.mark.offline
def test_save_does_not_mutate_caller(store):
    onto = _ecommerce()               # version defaults to 1
    store.save("acme", onto)
    store.save("acme", onto)          # stored version now 2...
    assert onto.version == 1          # ...but the caller's object is untouched


@pytest.mark.offline
def test_load_missing_returns_none(store):
    assert store.load("nope") is None


@pytest.mark.offline
def test_save_rejects_inconsistent_ontology(store):
    onto = Ontology()
    onto.define_object(ObjectType("Customer"))
    onto.define_link(LinkType("PLACES", ["Customer"], ["Order"]))  # Order undefined
    with pytest.raises(ValueError, match="self-consistent"):
        store.save("acme", onto)
    assert store.load("acme") is None            # nothing persisted


@pytest.mark.offline
def test_delete_and_list(store):
    store.save("acme", _ecommerce())
    store.save("globex", _ecommerce())
    assert set(store.list_workspaces()) == {"acme", "globex"}
    assert store.delete("acme") is True
    assert store.delete("acme") is False
    assert store.list_workspaces() == ["globex"]


@pytest.mark.offline
def test_json_persists_across_instances(tmp_path):
    base = str(tmp_path / "ontology")
    JsonOntologyStore(base, now=lambda: 1.0).save("acme", _ecommerce())
    reloaded = JsonOntologyStore(base).load("acme")
    assert reloaded is not None
    assert reloaded.link_type_names() == ["PLACES"]
    assert reloaded.validate_relation("PLACES", "Customer", "Order").ok
