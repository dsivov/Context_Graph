"""Tests for the pre-emit rules gate (wiring step 5).

Two layers:
* RulesGate.check() — PASS / FLAG / REJECT and the audit record (offline).
* the emit_decision_trace hook — using the same mocked-cg pattern as
  tests/test_context_graph.py: PASS persists, FLAG annotates the edge, REJECT
  raises RuleViolation *before* any write.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest

from lightrag.context_graph_types import RelationContext
from context_graph.rules import ConceptCatalog, RulesGate, RuleViolation


# ── deterministic backend ────────────────────────────────────────────────────


class FakeBackend:
    model_id = "fake/deterministic"
    dim = 4

    def encode(self, texts):
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, t in enumerate(texts):
            t = t.lower()
            if any(k in t for k in ("approv", "authoriz", "grant", "go ahead")):
                out[i, 0] = 1.0
            elif any(k in t for k in ("den", "reject")):
                out[i, 1] = 1.0
            else:
                out[i, 2] = 1.0
            out[i, 3] = 0.01
        return out


FLAG_DSL = """
rule "large discount needs finance review"  priority 10
when
    sim(relation_type, "APPROVAL") > 0.4
    and percent > 0.15
    and approved_via == "slack"
then
    flag("Discount >15% approved over Slack — route to Finance for review")
end
"""

REJECT_DSL = """
rule "discount cap"  priority 20
when
    sim(relation_type, "APPROVAL") > 0.4
    and percent > 0.5
then
    reject("Discounts over 50% are never allowed")
end
"""


def _gate(dsl):
    catalog = ConceptCatalog(backend=FakeBackend()).define(
        "APPROVAL", ["approved", "authorized", "granted approval", "go ahead"]
    )
    return RulesGate.from_dsl(catalog, dsl)


def _rc(percent="20% discount", via="slack"):
    return RelationContext(quantitative_data=percent, approved_via=via,
                           decision_trace="d")


# ── RulesGate.check ──────────────────────────────────────────────────────────


@pytest.mark.offline
def test_gate_flag():
    gate = _gate(FLAG_DSL)
    d = gate.check("Sarah", "MegaCorp", "GRANTED_APPROVAL", _rc(), as_of="2026-06-30")
    assert d.flagged and not d.blocked
    assert d.outcome == "FLAG"
    assert d.audit["matched_concept"] == "APPROVAL"
    assert d.audit["threshold"] == 0.4


@pytest.mark.offline
def test_gate_pass_when_channel_differs():
    gate = _gate(FLAG_DSL)
    d = gate.check("a", "b", "GRANTED_APPROVAL", _rc(via="jira"))
    assert d.passed and d.outcome == "PASS"


@pytest.mark.offline
def test_gate_reject():
    gate = _gate(REJECT_DSL)
    d = gate.check("a", "b", "GRANTED_APPROVAL", _rc(percent="60% discount"))
    assert d.blocked and d.outcome == "REJECT"
    assert d.audit["rule"] == "discount cap"


# ── emit_decision_trace hook ─────────────────────────────────────────────────


def _make_cg(graph, vdb, gate):
    """ContextGraph-like mock with the real emit_decision_trace bound (mirrors
    tests/test_context_graph.py)."""
    from lightrag.context_graph import ContextGraph

    cg = MagicMock()
    cg.chunk_entity_relation_graph = graph
    cg.decisions_vdb = vdb
    cg.rules_gate = gate
    cg.emit_decision_trace = ContextGraph.emit_decision_trace.__get__(cg, type(cg))
    return cg


def _graph(has_edge=False):
    g = AsyncMock()
    g.has_edge = AsyncMock(return_value=has_edge)
    g.get_edge = AsyncMock(return_value=None)
    g.upsert_node = AsyncMock()
    g.upsert_edge = AsyncMock()
    return g


@pytest.mark.offline
@pytest.mark.asyncio
async def test_hook_pass_persists_normally():
    graph, vdb = _graph(), AsyncMock()
    cg = _make_cg(graph, vdb, _gate(FLAG_DSL))
    # channel jira → PASS
    decision = await cg.emit_decision_trace("a", "b", "GRANTED_APPROVAL", _rc(via="jira"))
    assert decision.outcome == "PASS"
    graph.upsert_edge.assert_awaited_once()
    edge_data = graph.upsert_edge.call_args.kwargs["edge_data"]
    assert "needs_review" not in edge_data
    vdb.upsert.assert_awaited()                # decision_trace present → indexed


@pytest.mark.offline
@pytest.mark.asyncio
async def test_hook_flag_annotates_edge():
    graph, vdb = _graph(), AsyncMock()
    cg = _make_cg(graph, vdb, _gate(FLAG_DSL))
    decision = await cg.emit_decision_trace("Sarah", "MegaCorp", "GRANTED_APPROVAL", _rc())
    assert decision.outcome == "FLAG"
    graph.upsert_edge.assert_awaited_once()
    edge_data = graph.upsert_edge.call_args.kwargs["edge_data"]
    assert edge_data["needs_review"] is True
    audit = json.loads(edge_data["rules_audit"])
    assert audit["matched_concept"] == "APPROVAL"
    assert audit["outcome"] == "FLAG"


@pytest.mark.offline
@pytest.mark.asyncio
async def test_hook_reject_raises_before_any_write():
    graph, vdb = _graph(), AsyncMock()
    cg = _make_cg(graph, vdb, _gate(REJECT_DSL))
    with pytest.raises(RuleViolation) as ei:
        await cg.emit_decision_trace("a", "b", "GRANTED_APPROVAL", _rc(percent="60% discount"))
    assert ei.value.decision.outcome == "REJECT"
    # nothing persisted — gate runs before node/edge writes and vdb indexing
    graph.upsert_node.assert_not_awaited()
    graph.upsert_edge.assert_not_awaited()
    vdb.upsert.assert_not_awaited()


@pytest.mark.offline
@pytest.mark.asyncio
async def test_hook_no_gate_behaves_as_before():
    graph, vdb = _graph(), AsyncMock()
    cg = _make_cg(graph, vdb, None)            # no gate attached
    decision = await cg.emit_decision_trace("a", "b", "APPROVES", _rc())
    assert decision is None                    # no gate → no decision
    graph.upsert_edge.assert_awaited_once()
    edge_data = graph.upsert_edge.call_args.kwargs["edge_data"]
    assert "needs_review" not in edge_data
