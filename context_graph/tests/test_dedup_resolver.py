"""Tests for dedup Layer B — the inline resolver decision logic. Offline (mock vdb)."""

from __future__ import annotations

import pytest

from context_graph.dedup import (
    EntityResolver, InMemoryDedupStore, canonicalize, type_ok, name_ok,
    NEW, MERGE, REVIEW,
)


class _VDB:
    """Mock entities_vdb.query returning scripted (name, score, type) hits."""
    def __init__(self, hits):
        self._hits = hits

    async def query(self, text, top_k=5):
        return [{"entity_name": n, "distance": s, "entity_type": t}
                for (n, s, t) in self._hits]


# ── type_ok / name_ok units ──────────────────────────────────────────────────


@pytest.mark.offline
def test_type_ok_blocks_known_different_types():
    assert type_ok("Person", "Person")
    assert type_ok("Person", "UNKNOWN")        # unknown is permissive
    assert type_ok("Person", None)
    assert type_ok("Person", "Other")
    assert not type_ok("Person", "Organization")


@pytest.mark.offline
def test_name_ok_backstop():
    assert name_ok("Apple", "Apple Inc.")       # shared token / substring
    assert name_ok("IBM", "International Business Machines")  # acronym
    assert not name_ok("Apple", "Microsoft")    # unrelated → block a false embedding hit


# ── resolver ─────────────────────────────────────────────────────────────────


@pytest.mark.offline
@pytest.mark.asyncio
async def test_warm_start_alias_merge():
    store = InMemoryDedupStore()
    store.record_merge("ws", alias="Apple Inc.", alias_key=canonicalize("Apple Inc."),
                       into="Apple", method="llm")
    r = await EntityResolver(store, "ws").resolve("Apple Inc.")
    assert r.action == MERGE and r.canonical_id == "Apple" and r.method == "rule-alias"


@pytest.mark.offline
@pytest.mark.asyncio
async def test_hard_threshold_auto_merges_and_records():
    store = InMemoryDedupStore()
    vdb = _VDB([("Apple", 0.97, "Organization")])
    r = await EntityResolver(store, "ws", entities_vdb=vdb).resolve("Apple Inc.", "Organization")
    assert r.action == MERGE and r.canonical_id == "Apple" and r.method == "embedding"
    # audited & reversible
    assert store.list_merges("ws")[0].method == "embedding"
    assert store.resolve_key("ws", canonicalize("Apple Inc.")) == "Apple"


@pytest.mark.offline
@pytest.mark.asyncio
async def test_gray_zone_queues_not_merges():
    store = InMemoryDedupStore()
    vdb = _VDB([("Apple", 0.88, "Organization")])
    r = await EntityResolver(store, "ws", entities_vdb=vdb).resolve("Apple Inc.", "Organization")
    assert r.action == REVIEW and r.candidate == "Apple"
    assert store.list_merges("ws") == []                       # NOT merged
    assert len(store.list_pending("ws")) == 1                   # queued for the sweep


@pytest.mark.offline
@pytest.mark.asyncio
async def test_type_conflict_blocks_hard_merge():
    store = InMemoryDedupStore()
    vdb = _VDB([("Apple", 0.99, "Person")])                    # same name, wrong type
    r = await EntityResolver(store, "ws", entities_vdb=vdb).resolve("Apple", "Organization")
    assert r.action == NEW                                      # D4: never cross known types
    assert store.list_merges("ws") == []


@pytest.mark.offline
@pytest.mark.asyncio
async def test_name_mismatch_blocks_auto_merge():
    store = InMemoryDedupStore()
    vdb = _VDB([("Microsoft", 0.95, "Organization")])          # high cosine, unrelated name
    r = await EntityResolver(store, "ws", entities_vdb=vdb).resolve("Apple", "Organization")
    # name_ok blocks the silent auto-merge; the pair is adjudicated by the LLM, not merged
    assert r.action == REVIEW
    assert store.list_merges("ws") == []                       # never auto-merged inline

@pytest.mark.offline
@pytest.mark.asyncio
async def test_no_hits_is_new():
    store = InMemoryDedupStore()
    r = await EntityResolver(store, "ws", entities_vdb=_VDB([])).resolve("Brand New Thing")
    assert r.action == NEW and r.canonical_id == "Brand New Thing"


@pytest.mark.offline
@pytest.mark.asyncio
async def test_get_type_callback_used_when_vdb_lacks_type():
    store = InMemoryDedupStore()

    class _VDBNoType:
        async def query(self, text, top_k=5):
            return [{"entity_name": "Apple", "distance": 0.99}]   # no entity_type

    async def get_type(cand):
        return "Person"                                          # conflicts with Organization

    r = await EntityResolver(store, "ws", entities_vdb=_VDBNoType()).resolve(
        "Apple", "Organization", get_type=get_type)
    assert r.action == NEW                                        # callback supplied the conflict
