"""Tests for Layer 3 — async LLM isolate rescue. Offline (scripted LLM)."""

from __future__ import annotations

import json

import pytest

from context_graph.connectivity import IsolateRescue


def _llm(payload):
    async def llm(prompt, system_prompt=None, **kwargs):
        return json.dumps(payload) if not isinstance(payload, str) else payload
    return llm


def _finder(cands):
    async def find(name, desc):
        return cands
    return find


@pytest.mark.offline
@pytest.mark.asyncio
async def test_adds_confirmed_edge():
    added = []

    async def add(src, tgt, kw, desc):
        added.append((src, tgt, kw))

    cands = [{"name": "Hamas", "description": "Palestinian org"},
             {"name": "Gaza", "description": "a territory"}]
    payload = {"edges": [{"target": "Hamas", "relation": "armed wing of",
                          "description": "the brigades are the armed wing of Hamas"}]}
    r = await IsolateRescue(_llm(payload), find_candidates=_finder(cands),
                            add_edge=add).rescue([{"name": "Al-Qassam Brigades",
                                                   "description": "militant group"}])
    assert r == {"processed": 1, "edges_added": 1, "connected": 1, "errors": 0}
    assert added == [("Al-Qassam Brigades", "Hamas", "armed wing of")]


@pytest.mark.offline
@pytest.mark.asyncio
async def test_no_candidates_leaves_isolated():
    added = []
    r = await IsolateRescue(_llm({"edges": []}), find_candidates=_finder([]),
                            add_edge=lambda *a: added.append(a)).rescue(
        [{"name": "Belief", "description": "x"}])
    assert r["edges_added"] == 0 and r["connected"] == 0


@pytest.mark.offline
@pytest.mark.asyncio
async def test_conservative_empty_verdict_adds_nothing():
    async def add(*a):
        raise AssertionError("should not add")
    cands = [{"name": "Redis", "description": "cache"}]
    r = await IsolateRescue(_llm({"edges": []}), find_candidates=_finder(cands),
                            add_edge=add).rescue([{"name": "Belief", "description": "x"}])
    assert r["edges_added"] == 0


@pytest.mark.offline
@pytest.mark.asyncio
async def test_target_must_be_a_real_candidate():
    added = []

    async def add(src, tgt, kw, desc):
        added.append(tgt)

    cands = [{"name": "Hamas", "description": "org"}]
    # LLM hallucinates a target not in the candidate set → dropped
    payload = {"edges": [{"target": "Hezbollah", "relation": "allied with"}]}
    r = await IsolateRescue(_llm(payload), find_candidates=_finder(cands),
                            add_edge=add).rescue([{"name": "X", "description": "y"}])
    assert added == [] and r["edges_added"] == 0


@pytest.mark.offline
@pytest.mark.asyncio
async def test_llm_error_counts_and_continues():
    async def boom(prompt, system_prompt=None, **kwargs):
        raise RuntimeError("down")

    cands = [{"name": "Hamas", "description": "org"}]
    r = await IsolateRescue(boom, find_candidates=_finder(cands),
                            add_edge=lambda *a: None).rescue(
        [{"name": "A", "description": "a"}, {"name": "B", "description": "b"}])
    assert r["errors"] == 2 and r["edges_added"] == 0


@pytest.mark.offline
@pytest.mark.asyncio
async def test_max_candidates_capped():
    seen = {}

    async def find(name, desc):
        return [{"name": f"c{i}", "description": "d"} for i in range(20)]

    async def llm(prompt, system_prompt=None, **kwargs):
        seen["n"] = prompt.count('"name"')       # rough: candidates rendered
        return json.dumps({"edges": []})

    await IsolateRescue(llm, find_candidates=find, add_edge=lambda *a: None,
                        max_candidates=5).rescue([{"name": "x", "description": "y"}])
    # 5 candidates + the isolate itself ⇒ at most 6 "name" keys in the prompt
    assert seen["n"] <= 6
