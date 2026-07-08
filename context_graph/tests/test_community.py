"""Tests for community detection + summarization (Topic 3, Layer 4). Offline."""

from __future__ import annotations

import json

import pytest

from context_graph.community import detect_communities, CommunitySummarizer


# ── detection ────────────────────────────────────────────────────────────────


@pytest.mark.offline
def test_detects_two_communities():
    labels = ["A", "B", "C", "X", "Y", "Z"]
    edges = [{"source": "A", "target": "B"}, {"source": "B", "target": "C"},
             {"source": "A", "target": "C"}, {"source": "X", "target": "Y"},
             {"source": "Y", "target": "Z"}, {"source": "X", "target": "Z"}]
    comms = detect_communities(labels, edges)
    assert len(comms) == 2
    assert {frozenset(c) for c in comms} == {frozenset("ABC"), frozenset("XYZ")}


@pytest.mark.offline
def test_min_size_drops_singletons():
    labels = ["A", "B", "isolate"]
    edges = [{"source": "A", "target": "B"}]
    comms = detect_communities(labels, edges, min_size=2)
    assert comms == [["A", "B"]]                       # the isolate is dropped


@pytest.mark.offline
def test_deterministic():
    labels = list("ABCDEFGH")
    edges = [{"source": a, "target": b} for a, b in
             [("A", "B"), ("B", "C"), ("C", "A"), ("E", "F"), ("F", "G"), ("G", "E"), ("D", "H")]]
    assert detect_communities(labels, edges) == detect_communities(labels, edges)


@pytest.mark.offline
def test_empty_graph():
    assert detect_communities([], []) == []


# ── summarization ────────────────────────────────────────────────────────────


def _llm(payload):
    async def llm(prompt, system_prompt=None, **kwargs):
        return json.dumps(payload) if not isinstance(payload, str) else payload
    return llm


@pytest.mark.offline
@pytest.mark.asyncio
async def test_summarize_returns_title_and_summary():
    members = [{"name": "Hamas", "type": "Organization", "description": "a group"},
               {"name": "Al-Qassam Brigades", "type": "Organization", "description": "armed wing"}]
    s = await CommunitySummarizer(_llm({"title": "Hamas & affiliates",
                                        "summary": "The organisation and its armed wing."})
                                  ).summarize(members)
    assert s["title"] == "Hamas & affiliates"
    assert "armed wing" in s["summary"]


@pytest.mark.offline
@pytest.mark.asyncio
async def test_summarize_falls_back_on_bad_output():
    members = [{"name": "A", "description": "x"}, {"name": "B", "description": "y"}]
    s = await CommunitySummarizer(_llm("not json")).summarize(members)
    assert s["title"] and "A" in s["summary"]           # graceful fallback


@pytest.mark.offline
@pytest.mark.asyncio
async def test_summarize_llm_error_falls_back():
    async def boom(prompt, system_prompt=None, **kwargs):
        raise RuntimeError("down")
    s = await CommunitySummarizer(boom).summarize([{"name": "A", "description": "x"}])
    assert s["title"] and s["summary"]
