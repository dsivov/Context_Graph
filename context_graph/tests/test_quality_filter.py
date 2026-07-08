"""Tests for the node-quality filter + quarantine store (Topic 2, Layers 2+3)."""

from __future__ import annotations

import pytest

from context_graph.quality import (
    NodeFilter, ontology_from_types, InMemoryQuarantineStore, JsonQuarantineStore,
)
from context_graph.ontology.schema import Ontology, ObjectType


def _e(name, etype="Concept", desc="a meaningful description of the entity"):
    return {"entity_name": name, "entity_type": etype, "description": desc}


# ── NodeFilter ───────────────────────────────────────────────────────────────


@pytest.mark.offline
def test_partition_keeps_good_quarantines_garbage():
    f = NodeFilter()   # no ontology → DEFAULT_ENTITY_TYPES fallback (D11)
    ents = [_e("PostgreSQL", "Method"), _e("it"), _e("PostgreSQL", "Method", ""),
            _e("Sarah Chen", "Person")]
    r = f.partition(ents)
    kept = {e["entity_name"] for e in r.kept}
    assert kept == {"PostgreSQL", "Sarah Chen"}
    reasons = {q["reason"] for q in r.quarantined}
    assert any("pronoun" in x for x in reasons)          # "it"
    assert any("description" in x for x in reasons)       # empty desc


@pytest.mark.offline
def test_open_world_keeps_unknown_type_by_default():
    # D10: an unknown type (not in the fallback schema) is warned, not rejected.
    f = NodeFilter()
    r = f.partition([_e("Xenomorph", "Alien")])           # 'Alien' is not a default type
    assert len(r.kept) == 1 and not r.quarantined


@pytest.mark.offline
def test_closed_world_quarantines_unknown_type():
    ont = ontology_from_types(["Person", "Organization"])
    f = NodeFilter(ont, closed_world=True)
    r = f.partition([_e("Sarah Chen", "Person"), _e("Widget", "Product")])
    assert {e["entity_name"] for e in r.kept} == {"Sarah Chen"}
    assert r.quarantined[0]["entity_name"] == "Widget"    # unknown type rejected


@pytest.mark.offline
def test_summary_counts_reasons():
    f = NodeFilter()
    r = f.partition([_e("it"), _e("they"), _e("PostgreSQL", "Method")])
    s = r.summary()
    assert s["kept"] == 1 and s["quarantined"] == 2


@pytest.mark.offline
def test_check_returns_reason_or_none():
    f = NodeFilter()
    assert f.check("PostgreSQL", "a database", "Method") is None
    assert f.check("the system", "x", "Concept")          # a reason string


# ── QuarantineStore ──────────────────────────────────────────────────────────


@pytest.fixture(params=["memory", "json"])
def store(request, tmp_path):
    if request.param == "memory":
        return InMemoryQuarantineStore(now=lambda: 1.0)
    return JsonQuarantineStore(str(tmp_path / "q"), now=lambda: 1.0)


@pytest.mark.offline
def test_quarantine_add_list_pop(store):
    n = store.add("ws", [{"entity_name": "it", "reason": "pronoun"},
                         {"entity_name": "the system", "reason": "generic"}])
    assert n == 2
    assert store.add("ws", [{"entity_name": "it", "reason": "pronoun"}]) == 0  # dedupe
    assert {i["name"] for i in store.list("ws")} == {"it", "the system"}
    popped = store.pop("ws", "it")
    assert popped["reason"] == "pronoun"
    assert {i["name"] for i in store.list("ws")} == {"the system"}
    assert store.pop("ws", "nope") is None


@pytest.mark.offline
def test_quarantine_workspace_isolation_and_summary(store):
    store.add("a", [{"entity_name": "it", "reason": "pronoun"}])
    assert store.list("b") == []
    assert store.summary("a") == {"workspace": "a", "count": 1, "by_reason": {"pronoun": 1}}


@pytest.mark.offline
def test_json_persists(tmp_path):
    base = str(tmp_path / "q")
    JsonQuarantineStore(base).add("ws", [{"entity_name": "it", "reason": "pronoun"}])
    assert {i["name"] for i in JsonQuarantineStore(base).list("ws")} == {"it"}
