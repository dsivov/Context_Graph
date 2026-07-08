"""Tests for Context Graph API routes.

Covers:
- Pydantic models (CGR3QueryRequest, EdgeContextResponse, EdgesWithContextResponse)
- create_context_graph_routes factory with a plain LightRAG → expects HTTP 503
- create_context_graph_routes factory with a ContextGraph mock → expects success
- Config: use_context_graph and cgr3_max_iterations parsed from env
"""
from __future__ import annotations

import json
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from lightrag.api.routers.context_graph_routes import (
    CGR3QueryRequest,
    CGR3QueryResponse,
    EdgeContextResponse,
    EdgesWithContextResponse,
    RelationContextData,
    create_context_graph_routes,
)
from context_graph.types import RelationContext


# ─────────────────────────────────────────────────────────────────────────────
# Model validation tests
# ─────────────────────────────────────────────────────────────────────────────


class TestCGR3QueryRequestModel:
    def test_defaults(self):
        req = CGR3QueryRequest(query="What was approved?")
        assert req.mode == "hybrid"
        assert req.max_iterations == 3
        assert req.top_k is None
        assert req.include_references is True

    def test_strips_whitespace(self):
        req = CGR3QueryRequest(query="  Why was the discount approved?  ")
        assert req.query == "Why was the discount approved?"

    def test_query_too_short_raises(self):
        with pytest.raises(ValidationError):
            CGR3QueryRequest(query="Hi")

    def test_max_iterations_bounds(self):
        with pytest.raises(ValidationError):
            CGR3QueryRequest(query="Valid query", max_iterations=0)
        with pytest.raises(ValidationError):
            CGR3QueryRequest(query="Valid query", max_iterations=11)

    def test_valid_modes(self):
        for mode in ("local", "global", "hybrid", "naive", "mix"):
            req = CGR3QueryRequest(query="Valid question?", mode=mode)
            assert req.mode == mode

    def test_invalid_mode_raises(self):
        with pytest.raises(ValidationError):
            CGR3QueryRequest(query="Valid question?", mode="bypass")


class TestRelationContextData:
    def test_defaults(self):
        rc = RelationContextData()
        assert rc.supporting_sentences == []
        assert rc.temporal_info is None
        assert rc.confidence_score == 1.0

    def test_full_construction(self):
        rc = RelationContextData(
            supporting_sentences=["Quote A"],
            temporal_info="Q4 2026",
            quantitative_data="20%",
            decision_trace="VP approved",
            provenance="slack-123",
            confidence_score=0.9,
        )
        assert rc.temporal_info == "Q4 2026"
        assert rc.confidence_score == 0.9


class TestEdgeContextResponse:
    def test_no_context(self):
        resp = EdgeContextResponse(
            src_id="Alice", tgt_id="Bob", has_context=False, relation_context=None
        )
        assert resp.has_context is False
        assert resp.relation_context is None

    def test_with_context(self):
        rc = RelationContextData(temporal_info="Q1 2025", confidence_score=0.85)
        resp = EdgeContextResponse(
            src_id="Alice", tgt_id="Bob", has_context=True, relation_context=rc
        )
        assert resp.has_context is True
        assert resp.relation_context.temporal_info == "Q1 2025"


# ─────────────────────────────────────────────────────────────────────────────
# Helper: build a minimal test FastAPI app
# ─────────────────────────────────────────────────────────────────────────────


def _make_app(rag_instance) -> TestClient:
    """Build a FastAPI test app with context_graph_routes bound to rag_instance."""
    app = FastAPI()
    app.include_router(
        create_context_graph_routes(
            rag_instance,
            api_key=None,
            cgr3_max_iterations=3,
            top_k=60,
        )
    )
    return TestClient(app, raise_server_exceptions=False)


# ─────────────────────────────────────────────────────────────────────────────
# Behaviour when rag is plain LightRAG (not ContextGraph) → HTTP 503
# ─────────────────────────────────────────────────────────────────────────────


class TestRoutesWithPlainLightRAG:
    """All CG endpoints must return 503 when USE_CONTEXT_GRAPH is off."""

    def _plain_rag(self):
        """Return a mock that is NOT a ContextGraph instance."""
        from lightrag import LightRAG

        mock = MagicMock(spec=LightRAG)
        return mock

    def test_cgr3_query_returns_503(self):
        client = _make_app(self._plain_rag())
        resp = client.post("/cgr3/query", json={"query": "Why the discount?"})
        assert resp.status_code == 503
        assert "USE_CONTEXT_GRAPH" in resp.json()["detail"]

    def test_get_edge_context_returns_503(self):
        client = _make_app(self._plain_rag())
        resp = client.get("/graph/edge/context", params={"src": "Alice", "tgt": "Bob"})
        assert resp.status_code == 503

    def test_get_entity_edges_returns_503(self):
        client = _make_app(self._plain_rag())
        resp = client.get(
            "/graph/entity/edges-with-context",
            params={"entity_name": "Alice"},
        )
        assert resp.status_code == 503


# ─────────────────────────────────────────────────────────────────────────────
# Behaviour with a ContextGraph mock → success paths
# ─────────────────────────────────────────────────────────────────────────────


def _make_context_graph_mock(
    cgr3_answer: str = "The discount was approved by the VP.",
    edge_rc: Optional[RelationContext] = None,
    entity_edges: Optional[list] = None,
    entity_exists: bool = True,
    edge_exists: bool = True,
):
    """Return a mock that IS-A ContextGraph, with configurable behaviour."""
    from lightrag.context_graph import ContextGraph

    mock = MagicMock(spec=ContextGraph)

    # Make isinstance(mock, ContextGraph) return True
    mock.__class__ = ContextGraph

    # cgr3_query
    mock.cgr3_query = AsyncMock(return_value=cgr3_answer)

    # Graph storage mock
    graph_mock = AsyncMock()
    graph_mock.has_edge = AsyncMock(return_value=edge_exists)
    graph_mock.has_node = AsyncMock(return_value=entity_exists)
    mock.chunk_entity_relation_graph = graph_mock

    # get_edge_context
    mock.get_edge_context = AsyncMock(return_value=edge_rc)

    # get_edges_with_context
    if entity_edges is None:
        entity_edges = []
    mock.get_edges_with_context = AsyncMock(return_value=entity_edges)

    return mock


@pytest.mark.offline
class TestCGR3QuerySuccess:
    def test_basic_cgr3_query(self):
        mock_rag = _make_context_graph_mock(
            cgr3_answer="The discount was VP-approved due to competitive pressure."
        )
        client = _make_app(mock_rag)
        resp = client.post(
            "/cgr3/query",
            json={"query": "Why was the discount approved?", "max_iterations": 2},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "discount" in body["response"].lower()
        # cgr3_query called with correct args
        mock_rag.cgr3_query.assert_called_once()
        call_kwargs = mock_rag.cgr3_query.call_args
        assert call_kwargs.kwargs.get("max_iterations") == 2 or (
            len(call_kwargs.args) >= 2 and call_kwargs.args[1] == 2
        )

    def test_cgr3_respects_top_k(self):
        mock_rag = _make_context_graph_mock()
        client = _make_app(mock_rag)
        client.post(
            "/cgr3/query",
            json={"query": "Why the discount?", "top_k": 30},
        )
        call = mock_rag.cgr3_query.call_args
        assert call.kwargs.get("top_k") == 30

    def test_cgr3_defaults_to_server_top_k_when_not_specified(self):
        mock_rag = _make_context_graph_mock()
        client = _make_app(mock_rag)
        client.post("/cgr3/query", json={"query": "Why the discount?"})
        call = mock_rag.cgr3_query.call_args
        # Should use the factory's default top_k=60
        assert call.kwargs.get("top_k") == 60


@pytest.mark.offline
class TestGetEdgeContextSuccess:
    def test_edge_with_context(self):
        rc = RelationContext(
            supporting_sentences=["CEO approved the deal"],
            temporal_info="Q1 2025",
            quantitative_data="15% discount",
            decision_trace="Strategic partnership",
            provenance="meeting-2025-01",
            confidence_score=0.92,
        )
        mock_rag = _make_context_graph_mock(edge_rc=rc, edge_exists=True)
        client = _make_app(mock_rag)
        resp = client.get(
            "/graph/edge/context",
            params={"src": "Acme Corp", "tgt": "MegaCorp"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["has_context"] is True
        assert body["relation_context"]["temporal_info"] == "Q1 2025"
        assert body["relation_context"]["quantitative_data"] == "15% discount"
        assert body["relation_context"]["confidence_score"] == pytest.approx(0.92)

    def test_edge_without_context_returns_has_context_false(self):
        # get_edge_context returns an empty RelationContext
        empty_rc = RelationContext()
        mock_rag = _make_context_graph_mock(edge_rc=empty_rc, edge_exists=True)
        client = _make_app(mock_rag)
        resp = client.get(
            "/graph/edge/context",
            params={"src": "Alice", "tgt": "Bob"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["has_context"] is False
        assert body["relation_context"] is None

    def test_edge_not_found_returns_404(self):
        mock_rag = _make_context_graph_mock(edge_exists=False)
        client = _make_app(mock_rag)
        resp = client.get(
            "/graph/edge/context",
            params={"src": "Nobody", "tgt": "Nothing"},
        )
        assert resp.status_code == 404


@pytest.mark.offline
class TestGetEntityEdgesWithContextSuccess:
    def _make_edge_dict(self, src, tgt, rc: RelationContext):
        return {
            "src_id": src,
            "tgt_id": tgt,
            "keywords": "collaboration",
            "description": f"{src} and {tgt} collaborate.",
            "weight": 1.5,
            "relation_context": rc,
        }

    def test_entity_with_two_context_edges(self):
        rc1 = RelationContext(
            supporting_sentences=["Worked together since 2022"],
            temporal_info="2022–present",
            confidence_score=0.88,
        )
        rc2 = RelationContext(
            decision_trace="Approved due to strategic fit",
            confidence_score=0.75,
        )
        edges = [
            self._make_edge_dict("Alice", "Bob", rc1),
            self._make_edge_dict("Alice", "Carol", rc2),
        ]
        mock_rag = _make_context_graph_mock(
            entity_edges=edges, entity_exists=True
        )
        client = _make_app(mock_rag)
        resp = client.get(
            "/graph/entity/edges-with-context",
            params={"entity_name": "Alice"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["entity_name"] == "Alice"
        assert body["total_count"] == 2
        assert len(body["edges"]) == 2
        # Verify context data is serialised correctly
        first_edge = body["edges"][0]
        assert first_edge["relation_context"]["temporal_info"] == "2022–present"

    def test_entity_with_no_context_edges(self):
        mock_rag = _make_context_graph_mock(entity_edges=[], entity_exists=True)
        client = _make_app(mock_rag)
        resp = client.get(
            "/graph/entity/edges-with-context",
            params={"entity_name": "Alice"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total_count"] == 0
        assert body["edges"] == []

    def test_entity_not_found_returns_404(self):
        mock_rag = _make_context_graph_mock(entity_exists=False)
        client = _make_app(mock_rag)
        resp = client.get(
            "/graph/entity/edges-with-context",
            params={"entity_name": "Ghost"},
        )
        assert resp.status_code == 404


# ─────────────────────────────────────────────────────────────────────────────
# Config tests
# ─────────────────────────────────────────────────────────────────────────────


class TestContextGraphConfig:
    def test_use_context_graph_defaults_false(self, monkeypatch):
        monkeypatch.delenv("USE_CONTEXT_GRAPH", raising=False)
        from lightrag.api.config import parse_args
        import sys

        with patch.object(sys, "argv", ["lightrag-server"]):
            args = parse_args()
        assert args.use_context_graph is False

    def test_use_context_graph_env_true(self, monkeypatch):
        monkeypatch.setenv("USE_CONTEXT_GRAPH", "true")
        from lightrag.api.config import parse_args
        import sys

        with patch.object(sys, "argv", ["lightrag-server"]):
            args = parse_args()
        assert args.use_context_graph is True

    def test_cgr3_max_iterations_default(self, monkeypatch):
        monkeypatch.delenv("CGR3_MAX_ITERATIONS", raising=False)
        from lightrag.api.config import parse_args
        import sys

        with patch.object(sys, "argv", ["lightrag-server"]):
            args = parse_args()
        assert args.cgr3_max_iterations == 3

    def test_cgr3_max_iterations_env(self, monkeypatch):
        monkeypatch.setenv("CGR3_MAX_ITERATIONS", "5")
        from lightrag.api.config import parse_args
        import sys

        with patch.object(sys, "argv", ["lightrag-server"]):
            args = parse_args()
        assert args.cgr3_max_iterations == 5


# ─────────────────────────────────────────────────────────────────────────────
# Phase 5A/5B: New API endpoint tests
# ─────────────────────────────────────────────────────────────────────────────


def _make_cg_mock_with_decisions(
    emit_result=None,
    find_precedents_result=None,
    get_all_decisions_result=None,
):
    """Extend the base CG mock with Phase 5 methods."""
    from lightrag.context_graph import ContextGraph

    mock = MagicMock(spec=ContextGraph)
    mock.__class__ = ContextGraph

    mock.emit_decision_trace = AsyncMock(return_value=emit_result)
    mock.find_precedents = AsyncMock(return_value=find_precedents_result or [])
    mock.get_all_decisions = AsyncMock(return_value=get_all_decisions_result or [])

    # Needed by _require_context_graph (isinstance check via __class__)
    return mock


@pytest.mark.offline
class TestEmitDecisionEndpoint:
    """Tests for POST /graph/decision/emit."""

    def test_emit_decision_returns_200(self):
        mock_rag = _make_cg_mock_with_decisions()
        client = _make_app(mock_rag)
        resp = client.post(
            "/graph/decision/emit",
            json={
                "src": "Sarah Chen",
                "tgt": "MegaCorp",
                "relation_type": "APPROVES",
                "relation_context": {
                    "decision_trace": "VP approved 20% discount",
                    "approved_by": "VP_Smith",
                    "approved_via": "slack",
                    "valid_from": "2024-08-14",
                    "valid_until": "2024-12-31",
                    "confidence_score": 0.95,
                },
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert "Sarah Chen" in body["edge"]
        assert "MegaCorp" in body["edge"]
        mock_rag.emit_decision_trace.assert_awaited_once()
        call_kwargs = mock_rag.emit_decision_trace.call_args.kwargs
        assert call_kwargs["src"] == "Sarah Chen"
        assert call_kwargs["tgt"] == "MegaCorp"
        assert call_kwargs["relation_type"] == "APPROVES"
        rc_arg = call_kwargs["rc"]
        assert rc_arg.approved_by == "VP_Smith"
        assert rc_arg.approved_via == "slack"
        assert rc_arg.valid_from == "2024-08-14"

    def test_emit_decision_returns_503_plain_rag(self):
        from lightrag import LightRAG

        plain = MagicMock(spec=LightRAG)
        client = _make_app(plain)
        resp = client.post(
            "/graph/decision/emit",
            json={
                "src": "A",
                "tgt": "B",
                "relation_type": "LINKS",
                "relation_context": {"confidence_score": 0.8},
            },
        )
        assert resp.status_code == 503

    def test_emit_decision_missing_required_fields_returns_422(self):
        mock_rag = _make_cg_mock_with_decisions()
        client = _make_app(mock_rag)
        # Missing 'tgt' and 'relation_type'
        resp = client.post(
            "/graph/decision/emit",
            json={"src": "Alice", "relation_context": {}},
        )
        assert resp.status_code == 422


@pytest.mark.offline
class TestSearchDecisionsEndpoint:
    """Tests for GET /graph/decisions/search."""

    def test_search_decisions_returns_200_with_results(self):
        precedent_rc = RelationContext(
            decision_trace="20% discount approved due to competitive pressure",
            approved_by="VP_Smith",
            confidence_score=0.9,
        )
        mock_rag = _make_cg_mock_with_decisions(
            find_precedents_result=[
                {"src_id": "Sarah", "tgt_id": "MegaCorp", "relation_context": precedent_rc}
            ]
        )
        client = _make_app(mock_rag)
        resp = client.get(
            "/graph/decisions/search",
            params={"q": "discount approved for long-term client"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total_count"] == 1
        assert body["results"][0]["src_id"] == "Sarah"
        assert body["results"][0]["relation_context"]["approved_by"] == "VP_Smith"
        mock_rag.find_precedents.assert_awaited_once_with(
            query_text="discount approved for long-term client",
            top_k=10,
            min_confidence=0.0,
        )

    def test_search_decisions_passes_top_k_and_min_confidence(self):
        mock_rag = _make_cg_mock_with_decisions()
        client = _make_app(mock_rag)
        client.get(
            "/graph/decisions/search",
            params={"q": "any query", "top_k": 5, "min_confidence": 0.8},
        )
        mock_rag.find_precedents.assert_awaited_once_with(
            query_text="any query",
            top_k=5,
            min_confidence=0.8,
        )

    def test_search_decisions_returns_503_plain_rag(self):
        from lightrag import LightRAG

        plain = MagicMock(spec=LightRAG)
        client = _make_app(plain)
        resp = client.get("/graph/decisions/search", params={"q": "test"})
        assert resp.status_code == 503

    def test_search_decisions_missing_q_returns_422(self):
        mock_rag = _make_cg_mock_with_decisions()
        client = _make_app(mock_rag)
        resp = client.get("/graph/decisions/search")
        assert resp.status_code == 422


@pytest.mark.offline
class TestGetDecisionsEndpoint:
    """Tests for GET /graph/decisions."""

    def test_get_decisions_returns_200(self):
        rc1 = RelationContext(
            decision_trace="Approved discount",
            approved_by="Alice",
            policy_ref="PolicyX",
            confidence_score=0.9,
        )
        rc2 = RelationContext(
            decision_trace="Rejected bid",
            approved_by="Bob",
            confidence_score=0.7,
        )
        mock_rag = _make_cg_mock_with_decisions(
            get_all_decisions_result=[
                {"src_id": "A", "tgt_id": "B", "relation_context": rc1},
                {"src_id": "C", "tgt_id": "D", "relation_context": rc2},
            ]
        )
        client = _make_app(mock_rag)
        resp = client.get("/graph/decisions")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total_count"] == 2
        assert body["decisions"][0]["relation_context"]["approved_by"] == "Alice"

    def test_get_decisions_passes_filters(self):
        mock_rag = _make_cg_mock_with_decisions()
        client = _make_app(mock_rag)
        client.get(
            "/graph/decisions",
            params={
                "approved_by": "VP_Smith",
                "approved_via": "slack",
                "policy_ref": "DiscountPolicy",
                "min_confidence": 0.8,
                "active_as_of": "2024-09-01",
            },
        )
        mock_rag.get_all_decisions.assert_awaited_once_with(
            approved_by="VP_Smith",
            approved_via="slack",
            policy_ref="DiscountPolicy",
            min_confidence=0.8,
            active_as_of="2024-09-01",
        )

    def test_get_decisions_returns_503_plain_rag(self):
        from lightrag import LightRAG

        plain = MagicMock(spec=LightRAG)
        client = _make_app(plain)
        resp = client.get("/graph/decisions")
        assert resp.status_code == 503
