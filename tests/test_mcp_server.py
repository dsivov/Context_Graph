"""Tests for MCP server — CR-018.

Covers:
- Tool registration (8 tools with correct names)
- Tool descriptions (non-empty, contain usage guidance)
- Parameter schemas for each tool
- All 8 tool calls with mocked rag
- Auth rejection (missing/invalid X-API-Key)
- Context Graph mode guard
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from starlette.testclient import TestClient

from lightrag.api.mcp_server import create_mcp_server


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

EXPECTED_TOOLS = [
    "query_knowledge_graph",
    "query_cgr3",
    "search_precedents",
    "list_decisions",
    "get_edge_context",
    "get_entity_context",
    "record_decision",
    "ingest_decision_summary",
    "query_data",
    "query_auto",
]


def _make_mock_rag(is_context_graph=True):
    """Create a mock rag (WorkspaceProxy) for testing."""
    mock_rag = MagicMock()

    if is_context_graph:
        from lightrag.context_graph import ContextGraph

        mock_inner = MagicMock(spec=ContextGraph)
        mock_rag._get_current_rag = MagicMock(return_value=mock_inner)
    else:
        from lightrag.lightrag import LightRAG

        mock_inner = MagicMock(spec=LightRAG)
        mock_rag._get_current_rag = MagicMock(return_value=mock_inner)

    return mock_rag


@pytest.fixture
def mcp_no_auth():
    """Create MCP server + app with mocked rag, no auth."""
    rag = _make_mock_rag(is_context_graph=True)
    mcp, app = create_mcp_server(rag=rag, api_key=None)
    return mcp, app, rag


@pytest.fixture
def mcp_with_auth():
    """Create MCP server + app with API key auth."""
    rag = _make_mock_rag(is_context_graph=True)
    mcp, app = create_mcp_server(rag=rag, api_key="test-secret-key")
    return mcp, app, rag


# ─────────────────────────────────────────────────────────────────────────────
# Tool registration tests
# ─────────────────────────────────────────────────────────────────────────────


class TestToolRegistration:
    def test_exactly_10_tools_registered(self, mcp_no_auth):
        mcp, _, _ = mcp_no_auth
        tools = mcp._tool_manager.list_tools()
        assert len(tools) == 10

    def test_tool_names_match(self, mcp_no_auth):
        mcp, _, _ = mcp_no_auth
        tools = mcp._tool_manager.list_tools()
        names = sorted(t.name for t in tools)
        assert names == sorted(EXPECTED_TOOLS)

    def test_tool_descriptions_non_empty(self, mcp_no_auth):
        mcp, _, _ = mcp_no_auth
        tools = mcp._tool_manager.list_tools()
        for tool in tools:
            assert tool.description, f"Tool {tool.name} has empty description"
            assert len(tool.description) > 20, (
                f"Tool {tool.name} description too short: {tool.description}"
            )

    def test_tool_descriptions_contain_usage_guidance(self, mcp_no_auth):
        """Each description should tell the agent WHEN to use the tool."""
        mcp, _, _ = mcp_no_auth
        tools = mcp._tool_manager.list_tools()
        guidance_keywords = ["use ", "when", "for ", "search", "answer", "find", "record", "ingest"]
        for tool in tools:
            desc_lower = tool.description.lower()
            has_guidance = any(kw in desc_lower for kw in guidance_keywords)
            assert has_guidance, (
                f"Tool {tool.name} description lacks usage guidance: {tool.description[:80]}"
            )


# ─────────────────────────────────────────────────────────────────────────────
# Parameter schema tests
# ─────────────────────────────────────────────────────────────────────────────


class TestParameterSchemas:
    def _get_tool(self, mcp, name):
        tools = mcp._tool_manager.list_tools()
        for t in tools:
            if t.name == name:
                return t
        raise ValueError(f"Tool {name} not found")

    def _get_props(self, mcp, name):
        tool = self._get_tool(mcp, name)
        return tool.parameters.get("properties", {})

    def test_query_knowledge_graph_params(self, mcp_no_auth):
        props = self._get_props(mcp_no_auth[0], "query_knowledge_graph")
        assert "query" in props
        assert "mode" in props
        assert "top_k" in props

    def test_query_cgr3_params(self, mcp_no_auth):
        props = self._get_props(mcp_no_auth[0], "query_cgr3")
        assert "query" in props
        assert "mode" in props
        assert "max_iterations" in props

    def test_search_precedents_params(self, mcp_no_auth):
        props = self._get_props(mcp_no_auth[0], "search_precedents")
        assert "query" in props
        assert "top_k" in props
        assert "min_confidence" in props

    def test_record_decision_params(self, mcp_no_auth):
        props = self._get_props(mcp_no_auth[0], "record_decision")
        assert "src" in props
        assert "tgt" in props
        assert "relation_type" in props
        assert "decision_trace" in props
        assert "approved_by" in props

    def test_get_edge_context_params(self, mcp_no_auth):
        props = self._get_props(mcp_no_auth[0], "get_edge_context")
        assert "src" in props
        assert "tgt" in props

    def test_get_entity_context_params(self, mcp_no_auth):
        props = self._get_props(mcp_no_auth[0], "get_entity_context")
        assert "entity_name" in props

    def test_ingest_decision_summary_params(self, mcp_no_auth):
        props = self._get_props(mcp_no_auth[0], "ingest_decision_summary")
        assert "text" in props
        assert "category" in props

    def test_list_decisions_params(self, mcp_no_auth):
        props = self._get_props(mcp_no_auth[0], "list_decisions")
        assert "approved_by" in props
        assert "approved_via" in props
        assert "policy_ref" in props


# ─────────────────────────────────────────────────────────────────────────────
# Tool call tests (via mcp.call_tool)
# ─────────────────────────────────────────────────────────────────────────────


class TestToolCalls:
    """Test each tool call by invoking via mcp.call_tool()."""

    @pytest.mark.asyncio
    async def test_query_knowledge_graph(self):
        rag = _make_mock_rag(is_context_graph=True)
        rag.aquery = AsyncMock(return_value="The answer is 42")
        mcp, _ = create_mcp_server(rag=rag)
        result = await mcp.call_tool(
            "query_knowledge_graph",
            {"query": "What is the meaning?", "mode": "mix"},
        )
        rag.aquery.assert_awaited_once()
        assert len(result) > 0
        assert "42" in result[0].text

    @pytest.mark.asyncio
    async def test_query_cgr3(self):
        rag = _make_mock_rag(is_context_graph=True)
        rag.cgr3_query = AsyncMock(return_value="Multi-hop answer")
        mcp, _ = create_mcp_server(rag=rag)
        result = await mcp.call_tool(
            "query_cgr3",
            {"query": "Why did we discount MegaCorp?"},
        )
        rag.cgr3_query.assert_awaited_once()
        assert "Multi-hop" in result[0].text

    @pytest.mark.asyncio
    async def test_search_precedents(self):
        rag = _make_mock_rag(is_context_graph=True)
        from lightrag.context_graph_types import RelationContext

        mock_rc = RelationContext(decision_trace="20% discount approved")
        rag.find_precedents = AsyncMock(
            return_value=[
                {"src_id": "Alice", "tgt_id": "MegaCorp", "relation_context": mock_rc}
            ]
        )
        mcp, _ = create_mcp_server(rag=rag)
        result = await mcp.call_tool(
            "search_precedents",
            {"query": "discount approval"},
        )
        rag.find_precedents.assert_awaited_once()
        assert "Alice" in result[0].text
        assert "MegaCorp" in result[0].text

    @pytest.mark.asyncio
    async def test_list_decisions(self):
        rag = _make_mock_rag(is_context_graph=True)
        from lightrag.context_graph_types import RelationContext

        mock_rc = RelationContext(decision_trace="Policy exception granted")
        rag.get_all_decisions = AsyncMock(
            return_value=[
                {"src_id": "VP", "tgt_id": "Deal", "relation_context": mock_rc}
            ]
        )
        mcp, _ = create_mcp_server(rag=rag)
        result = await mcp.call_tool("list_decisions", {})
        rag.get_all_decisions.assert_awaited_once()
        assert "VP" in result[0].text

    @pytest.mark.asyncio
    async def test_get_edge_context(self):
        rag = _make_mock_rag(is_context_graph=True)
        from lightrag.context_graph_types import RelationContext

        mock_rc = RelationContext(
            decision_trace="Approved by VP",
            confidence_score=0.95,
        )
        rag.get_edge_context = AsyncMock(return_value=mock_rc)
        mcp, _ = create_mcp_server(rag=rag)
        result = await mcp.call_tool(
            "get_edge_context",
            {"src": "Sarah", "tgt": "MegaCorp"},
        )
        assert "has_context" in result[0].text

    @pytest.mark.asyncio
    async def test_get_entity_context(self):
        rag = _make_mock_rag(is_context_graph=True)
        from lightrag.context_graph_types import RelationContext

        mock_rc = RelationContext(decision_trace="Discount approved")
        rag.get_edges_with_context = AsyncMock(
            return_value=[
                {
                    "src_id": "Sarah",
                    "tgt_id": "MegaCorp",
                    "keywords": "discount",
                    "description": "Approved discount",
                    "weight": 1.0,
                    "relation_context": mock_rc,
                }
            ]
        )
        mcp, _ = create_mcp_server(rag=rag)
        result = await mcp.call_tool(
            "get_entity_context",
            {"entity_name": "Sarah"},
        )
        assert "Sarah" in result[0].text
        assert "MegaCorp" in result[0].text

    @pytest.mark.asyncio
    async def test_record_decision(self):
        rag = _make_mock_rag(is_context_graph=True)
        rag.emit_decision_trace = AsyncMock()
        mcp, _ = create_mcp_server(rag=rag)
        result = await mcp.call_tool(
            "record_decision",
            {
                "src": "Sarah",
                "tgt": "MegaCorp",
                "relation_type": "discount_approval",
                "decision_trace": "VP approved 20% discount",
                "approved_by": "Sarah",
            },
        )
        rag.emit_decision_trace.assert_awaited_once()
        assert "ok" in result[0].text
        assert "Sarah -> MegaCorp" in result[0].text

    @pytest.mark.asyncio
    async def test_ingest_decision_summary(self):
        rag = _make_mock_rag(is_context_graph=True)
        rag.ingest_decision_summary = AsyncMock(return_value="insert_123_abc")
        mcp, _ = create_mcp_server(rag=rag)
        result = await mcp.call_tool(
            "ingest_decision_summary",
            {
                "text": "Q3 discount patterns: 80% approval rate for deals over $50k",
                "category": "quarterly_summary",
            },
        )
        rag.ingest_decision_summary.assert_awaited_once()
        assert "ok" in result[0].text
        assert "insert_123_abc" in result[0].text


# ─────────────────────────────────────────────────────────────────────────────
# Auth tests
# ─────────────────────────────────────────────────────────────────────────────


class TestAuth:
    def test_auth_rejects_missing_key(self, mcp_with_auth):
        _, app, _ = mcp_with_auth
        client = TestClient(app)
        response = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "method": "initialize", "id": 1},
        )
        assert response.status_code == 401

    def test_auth_rejects_wrong_key(self, mcp_with_auth):
        _, app, _ = mcp_with_auth
        client = TestClient(app)
        response = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "method": "initialize", "id": 1},
            headers={"X-API-Key": "wrong-key"},
        )
        assert response.status_code == 401

    def test_auth_accepts_correct_key(self, mcp_with_auth):
        _, app, _ = mcp_with_auth
        client = TestClient(app, raise_server_exceptions=False)
        response = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "method": "initialize",
                "id": 1,
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1.0"},
                },
            },
            headers={"X-API-Key": "test-secret-key"},
        )
        # Auth passed — not 401. May be 500 (session manager not running
        # in unit tests), but the key point is auth middleware didn't reject.
        assert response.status_code != 401, (
            f"Expected auth to pass, got {response.status_code}: {response.text}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Context Graph mode guard
# ─────────────────────────────────────────────────────────────────────────────


class TestContextGraphGuard:
    """Tools 2-7 should raise ToolError when rag is not ContextGraph."""

    @pytest.mark.asyncio
    async def test_cgr3_requires_context_graph(self):
        from mcp.server.fastmcp.exceptions import ToolError

        rag = _make_mock_rag(is_context_graph=False)
        mcp, _ = create_mcp_server(rag=rag)
        with pytest.raises(ToolError, match="Context Graph mode"):
            await mcp.call_tool(
                "query_cgr3",
                {"query": "Test question here"},
            )

    @pytest.mark.asyncio
    async def test_query_knowledge_graph_works_without_cg(self):
        """Tool 1 (query_knowledge_graph) should work even without CG mode."""
        rag = _make_mock_rag(is_context_graph=False)
        rag.aquery = AsyncMock(return_value="Some answer")
        mcp, _ = create_mcp_server(rag=rag)
        result = await mcp.call_tool(
            "query_knowledge_graph",
            {"query": "Basic question"},
        )
        assert "Some answer" in result[0].text

    @pytest.mark.asyncio
    @pytest.mark.parametrize("tool_name,args", [
        ("search_precedents", {"query": "test"}),
        ("list_decisions", {}),
        ("get_edge_context", {"src": "A", "tgt": "B"}),
        ("get_entity_context", {"entity_name": "A"}),
        ("record_decision", {"src": "A", "tgt": "B", "relation_type": "test", "decision_trace": "test"}),
        ("ingest_decision_summary", {"text": "test summary content"}),
    ])
    async def test_all_cg_tools_require_context_graph(self, tool_name, args):
        """Tools 2-8 (except query_knowledge_graph) require CG mode."""
        from mcp.server.fastmcp.exceptions import ToolError

        rag = _make_mock_rag(is_context_graph=False)
        mcp, _ = create_mcp_server(rag=rag)
        with pytest.raises(ToolError, match="Context Graph mode"):
            await mcp.call_tool(tool_name, args)


# ─────────────────────────────────────────────────────────────────────────────
# Error path tests
# ─────────────────────────────────────────────────────────────────────────────


class TestErrorPaths:
    """Test that errors from rag methods propagate as ToolError."""

    @pytest.mark.asyncio
    async def test_aquery_error_propagates(self):
        from mcp.server.fastmcp.exceptions import ToolError

        rag = _make_mock_rag(is_context_graph=True)
        rag.aquery = AsyncMock(side_effect=RuntimeError("DB connection failed"))
        mcp, _ = create_mcp_server(rag=rag)
        with pytest.raises(ToolError, match="DB connection failed"):
            await mcp.call_tool(
                "query_knowledge_graph",
                {"query": "test question"},
            )

    @pytest.mark.asyncio
    async def test_get_edge_context_none_both_directions(self):
        """get_edge_context returns has_context=False when both directions are None."""
        rag = _make_mock_rag(is_context_graph=True)
        rag.get_edge_context = AsyncMock(return_value=None)
        mcp, _ = create_mcp_server(rag=rag)
        result = await mcp.call_tool(
            "get_edge_context",
            {"src": "Alice", "tgt": "Bob"},
        )
        text = result[0].text
        assert '"has_context": false' in text or "'has_context': False" in text

    @pytest.mark.asyncio
    async def test_find_precedents_empty_list(self):
        rag = _make_mock_rag(is_context_graph=True)
        rag.find_precedents = AsyncMock(return_value=[])
        mcp, _ = create_mcp_server(rag=rag)
        result = await mcp.call_tool(
            "search_precedents",
            {"query": "something that doesn't exist"},
        )
        text = result[0].text
        assert '"total_count": 0' in text or "'total_count': 0" in text

    @pytest.mark.asyncio
    async def test_get_entity_context_empty_edges(self):
        rag = _make_mock_rag(is_context_graph=True)
        rag.get_edges_with_context = AsyncMock(return_value=[])
        mcp, _ = create_mcp_server(rag=rag)
        result = await mcp.call_tool(
            "get_entity_context",
            {"entity_name": "Unknown Entity"},
        )
        text = result[0].text
        assert '"total_count": 0' in text or "'total_count': 0" in text


# ─────────────────────────────────────────────────────────────────────────────
# Input validation tests (T-124)
# ─────────────────────────────────────────────────────────────────────────────


class TestInputValidation:
    """Test input validation added in T-124."""

    @pytest.mark.asyncio
    async def test_invalid_mode_rejected(self):
        from mcp.server.fastmcp.exceptions import ToolError

        rag = _make_mock_rag(is_context_graph=True)
        mcp, _ = create_mcp_server(rag=rag)
        with pytest.raises(ToolError, match="Invalid mode"):
            await mcp.call_tool(
                "query_knowledge_graph",
                {"query": "test", "mode": "invalid_mode"},
            )

    @pytest.mark.asyncio
    async def test_empty_query_rejected(self):
        from mcp.server.fastmcp.exceptions import ToolError

        rag = _make_mock_rag(is_context_graph=True)
        mcp, _ = create_mcp_server(rag=rag)
        with pytest.raises(ToolError, match="non-empty"):
            await mcp.call_tool(
                "query_knowledge_graph",
                {"query": "   ", "mode": "hybrid"},
            )

    @pytest.mark.asyncio
    async def test_empty_src_rejected_in_record_decision(self):
        from mcp.server.fastmcp.exceptions import ToolError

        rag = _make_mock_rag(is_context_graph=True)
        mcp, _ = create_mcp_server(rag=rag)
        with pytest.raises(ToolError, match="non-empty"):
            await mcp.call_tool(
                "record_decision",
                {"src": "", "tgt": "B", "relation_type": "test", "decision_trace": "test"},
            )

    @pytest.mark.asyncio
    async def test_confidence_score_clamped(self):
        """confidence_score > 1.0 should be clamped, not rejected."""
        rag = _make_mock_rag(is_context_graph=True)
        rag.emit_decision_trace = AsyncMock()
        mcp, _ = create_mcp_server(rag=rag)
        result = await mcp.call_tool(
            "record_decision",
            {
                "src": "A", "tgt": "B",
                "relation_type": "test",
                "decision_trace": "test decision",
                "confidence_score": 5.0,
            },
        )
        # Should succeed (clamped to 1.0), not error
        assert "ok" in result[0].text
