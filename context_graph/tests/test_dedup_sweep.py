"""Tests for dedup Layer C — async LLM adjudication + canonical naming. Offline."""

from __future__ import annotations

import json

import pytest

from context_graph.dedup import DedupSweep, InMemoryDedupStore, canonicalize


def _llm(payload):
    async def llm(prompt, system_prompt=None, **kwargs):
        return json.dumps(payload) if not isinstance(payload, str) else payload
    return llm


def _seed(store, pairs):
    for name, cand, score in pairs:
        store.enqueue_review("ws", name=name, candidate=cand, score=score)


@pytest.mark.offline
@pytest.mark.asyncio
async def test_confirmed_pair_merges_with_canonical_name():
    store = InMemoryDedupStore()
    _seed(store, [("IBM", "International Business Machines", 0.88)])
    applied = []

    async def apply(alias, into, canonical):
        applied.append((alias, into, canonical))

    payload = {"verdicts": [{"id": 0, "same": True,
                             "canonical": "International Business Machines"}]}
    r = await DedupSweep(store, "ws", _llm(payload), apply_merge=apply).run()

    assert r == {"adjudicated": 1, "merged": 1, "rejected": 0, "errors": 0}
    # reversible record + canonical name + graph apply
    assert store.resolve_key("ws", canonicalize("IBM")) == "International Business Machines"
    assert store.canonical_name("ws", "International Business Machines") == \
        "International Business Machines"
    assert applied == [("IBM", "International Business Machines",
                        "International Business Machines")]
    assert store.list_pending("ws") == []                       # dequeued


@pytest.mark.offline
@pytest.mark.asyncio
async def test_rejected_pair_is_not_merged_but_dequeued():
    store = InMemoryDedupStore()
    _seed(store, [("Apple", "Apple Records", 0.86)])
    payload = {"verdicts": [{"id": 0, "same": False, "reason": "company vs label"}]}
    r = await DedupSweep(store, "ws", _llm(payload)).run()
    assert r["merged"] == 0 and r["rejected"] == 1
    assert store.list_merges("ws") == []                        # nothing merged
    assert store.list_pending("ws") == []                       # still dequeued


@pytest.mark.offline
@pytest.mark.asyncio
async def test_llm_error_leaves_batch_queued():
    store = InMemoryDedupStore()
    _seed(store, [("A", "B", 0.87)])

    async def boom(prompt, system_prompt=None, **kwargs):
        raise RuntimeError("llm down")

    r = await DedupSweep(store, "ws", boom).run()
    assert r["errors"] == 1 and r["merged"] == 0
    assert len(store.list_pending("ws")) == 1                   # NOT dropped — retry later


@pytest.mark.offline
@pytest.mark.asyncio
async def test_unparseable_output_leaves_batch_queued():
    store = InMemoryDedupStore()
    _seed(store, [("A", "B", 0.87)])
    r = await DedupSweep(store, "ws", _llm("no json")).run()
    assert r["errors"] == 1
    assert len(store.list_pending("ws")) == 1


@pytest.mark.offline
@pytest.mark.asyncio
async def test_missing_canonical_falls_back_to_rule():
    store = InMemoryDedupStore()
    _seed(store, [("IBM", "International Business Machines", 0.88)])
    payload = {"verdicts": [{"id": 0, "same": True}]}           # no canonical field
    await DedupSweep(store, "ws", _llm(payload)).run()
    # prefer_canonical_name picks the fuller form
    assert store.canonical_name("ws", "International Business Machines") == \
        "International Business Machines"


@pytest.mark.offline
@pytest.mark.asyncio
async def test_canonical_name_driven_by_frequency_not_llm():
    # The LLM only says same/not; the name comes from the frequency score. Here the
    # acronym is far more common, so it wins over the fuller form.
    store = InMemoryDedupStore()
    _seed(store, [("IBM", "International Business Machines", 0.88)])
    freq = {"IBM": 500, "International Business Machines": 3}

    async def get_count(name):
        return freq.get(name, 1)

    payload = {"verdicts": [{"id": 0, "same": True}]}   # no 'canonical' from the LLM
    await DedupSweep(store, "ws", _llm(payload), get_count=get_count).run()
    assert store.canonical_name("ws", "International Business Machines") == "IBM"


@pytest.mark.offline
@pytest.mark.asyncio
async def test_batching_covers_all_pairs():
    store = InMemoryDedupStore()
    _seed(store, [(f"n{i}", f"c{i}", 0.87) for i in range(5)])
    payload = {"verdicts": [{"id": i, "same": False} for i in range(2)]}
    r = await DedupSweep(store, "ws", _llm(payload), batch_size=2).run()
    assert r["adjudicated"] == 5                                # all 5 across 3 batches
    assert store.list_pending("ws") == []
