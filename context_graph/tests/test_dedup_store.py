"""Tests for dedup Layer D — the reversible alias/audit store. Offline."""

from __future__ import annotations

import pytest

from context_graph.dedup import InMemoryDedupStore, JsonDedupStore, canonicalize


@pytest.fixture(params=["memory", "json"])
def store(request, tmp_path):
    clock = {"t": 100.0}
    now = lambda: clock["t"]
    if request.param == "memory":
        return InMemoryDedupStore(now=now)
    return JsonDedupStore(str(tmp_path / "dedup"), now=now)


@pytest.mark.offline
def test_record_and_resolve(store):
    key = canonicalize("I.B.M.")
    store.record_merge("ws", alias="I.B.M.", alias_key=key,
                       into="International Business Machines", method="llm", score=0.9,
                       canonical_name="International Business Machines")
    assert store.resolve_key("ws", key) == "International Business Machines"
    assert store.canonical_name("ws", "International Business Machines") == \
        "International Business Machines"
    assert store.resolve_key("ws", "nope") is None


@pytest.mark.offline
def test_workspace_isolation(store):
    store.record_merge("a", alias="X", alias_key="x", into="Xcanon", method="rule")
    assert store.resolve_key("a", "x") == "Xcanon"
    assert store.resolve_key("b", "x") is None


@pytest.mark.offline
def test_unmerge_reverses_resolution(store):
    rec = store.record_merge("ws", alias="Apple Inc.", alias_key="apple",
                             into="Apple", method="embedding", score=0.95)
    assert store.resolve_key("ws", "apple") == "Apple"
    assert store.unmerge("ws", rec.id) is True
    assert store.resolve_key("ws", "apple") is None            # resolution reversed
    assert store.unmerge("ws", rec.id) is False                # already undone
    # audit trail is preserved
    assert len(store.list_merges("ws", include_undone=True)) == 1
    assert store.list_merges("ws") == []                       # none live


@pytest.mark.offline
def test_summary_counts_by_method(store):
    store.record_merge("ws", alias="a", alias_key="a", into="C", method="rule")
    store.record_merge("ws", alias="b", alias_key="b", into="C", method="embedding")
    store.record_merge("ws", alias="c", alias_key="c", into="C", method="rule")
    s = store.summary("ws")
    assert s["merges_live"] == 3
    assert s["by_method"] == {"rule": 2, "embedding": 1}


@pytest.mark.offline
def test_version_bumps_on_write(store):
    store.record_merge("ws", alias="a", alias_key="a", into="C", method="rule")
    assert store.summary("ws")["version"] == 1
    store.record_merge("ws", alias="b", alias_key="b", into="C", method="rule")
    assert store.summary("ws")["version"] == 2


@pytest.mark.offline
def test_json_persists_across_instances(tmp_path):
    base = str(tmp_path / "dedup")
    s1 = JsonDedupStore(base)
    s1.record_merge("ws", alias="I.B.M.", alias_key="ibm", into="IBM Corp",
                    method="llm", canonical_name="IBM Corp")
    s2 = JsonDedupStore(base)                                   # fresh instance
    assert s2.resolve_key("ws", "ibm") == "IBM Corp"
    assert s2.canonical_name("ws", "IBM Corp") == "IBM Corp"
