"""Tests for Context Graph (CG) implementation.

Covers:
- RelationContext data class: construction, JSON serialisation, merging
- CG relationship extraction parser (5 and 6 fields)
- CG extraction result processor
- _collect_relation_context helper in operate.py
- ContextGraph class import and _process_extract_entities override
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from lightrag.context_graph_types import RelationContext, ContextNode, ContextEdge


# ─────────────────────────────────────────────────────────────────────────────
# RelationContext unit tests
# ─────────────────────────────────────────────────────────────────────────────


class TestRelationContext:
    def test_default_construction(self):
        rc = RelationContext()
        assert rc.supporting_sentences == []
        assert rc.temporal_info is None
        assert rc.quantitative_data is None
        assert rc.decision_trace is None
        assert rc.provenance is None
        assert rc.confidence_score == 1.0

    def test_to_json_roundtrip(self):
        rc = RelationContext(
            supporting_sentences=["Quote A", "Quote B"],
            temporal_info="Q4 2026",
            quantitative_data="20%",
            decision_trace="VP approved",
            provenance="slack-123",
            confidence_score=0.9,
        )
        json_str = rc.to_json()
        parsed = json.loads(json_str)
        assert parsed["supporting_sentences"] == ["Quote A", "Quote B"]
        assert parsed["temporal_info"] == "Q4 2026"
        assert parsed["confidence_score"] == 0.9

    def test_from_json_roundtrip(self):
        rc = RelationContext(
            supporting_sentences=["Evidence X"],
            decision_trace="Approved by committee",
            confidence_score=0.85,
        )
        rc2 = RelationContext.from_json(rc.to_json())
        assert rc2.supporting_sentences == rc.supporting_sentences
        assert rc2.decision_trace == rc.decision_trace
        assert rc2.confidence_score == rc.confidence_score

    def test_from_json_invalid(self):
        rc = RelationContext.from_json("not json at all")
        assert rc.is_empty()
        assert rc.confidence_score == 1.0

    def test_from_json_non_dict(self):
        rc = RelationContext.from_json('["list", "not", "dict"]')
        assert rc.is_empty()

    def test_is_empty(self):
        assert RelationContext().is_empty()
        rc = RelationContext(temporal_info="2024")
        assert not rc.is_empty()
        rc2 = RelationContext(supporting_sentences=["quote"])
        assert not rc2.is_empty()

    def test_to_text(self):
        rc = RelationContext(
            supporting_sentences=["quote1", "quote2"],
            temporal_info="Q1 2025",
            decision_trace="budget approved",
            provenance="slack-thread-42",
        )
        text = rc.to_text()
        assert "quote1" in text
        assert "Q1 2025" in text
        assert "budget approved" in text
        assert "slack-thread-42" in text

    def test_to_text_empty(self):
        assert RelationContext().to_text() == ""

    def test_merge_basic(self):
        rc1 = RelationContext(
            supporting_sentences=["sent A"],
            temporal_info="Q1",
            confidence_score=0.7,
        )
        rc2 = RelationContext(
            supporting_sentences=["sent B"],
            quantitative_data="$1M",
            confidence_score=0.9,
        )
        merged = RelationContext.merge([rc1, rc2])
        assert "sent A" in merged.supporting_sentences
        assert "sent B" in merged.supporting_sentences
        assert merged.temporal_info == "Q1"
        assert merged.quantitative_data == "$1M"
        assert merged.confidence_score == 0.9

    def test_merge_deduplicates_sentences(self):
        rc1 = RelationContext(supporting_sentences=["dup", "unique1"])
        rc2 = RelationContext(supporting_sentences=["dup", "unique2"])
        merged = RelationContext.merge([rc1, rc2])
        assert merged.supporting_sentences.count("dup") == 1
        assert "unique1" in merged.supporting_sentences
        assert "unique2" in merged.supporting_sentences

    def test_merge_empty_list(self):
        merged = RelationContext.merge([])
        assert merged.is_empty()


class TestContextNode:
    def test_construction(self):
        node = ContextNode(
            entity_name="Lead_Alpha",
            entity_type="LEAD",
            description="Top healthcare lead",
            attributes={"score": "85", "stage": "negotiation"},
            reference_links=["https://linkedin.com/xyz"],
        )
        assert node.entity_name == "Lead_Alpha"
        assert node.attributes["stage"] == "negotiation"


class TestContextEdge:
    def test_construction_with_context(self):
        rc = RelationContext(
            supporting_sentences=["quote"],
            decision_trace="VP approved",
            confidence_score=0.95,
        )
        edge = ContextEdge(
            source_id="Lead_Alpha",
            target_id="Opportunity_X",
            relation_type="QUALIFIES",
            weight=2.0,
            context=rc,
        )
        assert edge.weight == 2.0
        assert edge.context.decision_trace == "VP approved"


# ─────────────────────────────────────────────────────────────────────────────
# CG extraction parser tests
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.offline
@pytest.mark.asyncio
async def test_cg_relation_5_fields_no_context():
    """5-field relation (standard format) should parse without relation_context."""
    from lightrag.context_graph import _handle_single_cg_relationship_extraction

    attrs = [
        "relation",
        "Alice",
        "Bob",
        "collaboration,partnership",
        "Alice and Bob work together on the project.",
    ]
    result = await _handle_single_cg_relationship_extraction(
        attrs, "chunk-001", 1700000000
    )
    assert result is not None
    assert result["src_id"] == "Alice"
    assert result["tgt_id"] == "Bob"
    assert "relation_context" not in result


@pytest.mark.offline
@pytest.mark.asyncio
async def test_cg_relation_6_fields_with_valid_context():
    """6-field relation should parse and include a valid relation_context JSON."""
    from lightrag.context_graph import _handle_single_cg_relationship_extraction

    rc_dict = {
        "supporting_sentences": ["Alice and Bob collaborated closely"],
        "temporal_info": "Q3 2024",
        "quantitative_data": None,
        "decision_trace": "Leadership approved the partnership",
        "provenance": "meeting-notes-2024-09",
        "confidence_score": 0.92,
    }
    attrs = [
        "relation",
        "Alice",
        "Bob",
        "collaboration",
        "Alice and Bob work together.",
        json.dumps(rc_dict),
    ]
    result = await _handle_single_cg_relationship_extraction(
        attrs, "chunk-001", 1700000000
    )
    assert result is not None
    assert "relation_context" in result
    rc = RelationContext.from_json(result["relation_context"])
    assert rc.temporal_info == "Q3 2024"
    assert rc.decision_trace == "Leadership approved the partnership"
    assert abs(rc.confidence_score - 0.92) < 0.001


@pytest.mark.offline
@pytest.mark.asyncio
async def test_cg_relation_6_fields_invalid_json_context():
    """Malformed JSON in 6th field should be silently dropped (no relation_context key)."""
    from lightrag.context_graph import _handle_single_cg_relationship_extraction

    attrs = [
        "relation",
        "Alice",
        "Bob",
        "collaboration",
        "Alice and Bob work together.",
        "{invalid json}",
    ]
    result = await _handle_single_cg_relationship_extraction(
        attrs, "chunk-001", 1700000000
    )
    assert result is not None
    assert "relation_context" not in result


@pytest.mark.offline
@pytest.mark.asyncio
async def test_cg_relation_self_loop_returns_none():
    from lightrag.context_graph import _handle_single_cg_relationship_extraction

    attrs = ["relation", "Alice", "Alice", "self-ref", "Alice refers to herself."]
    result = await _handle_single_cg_relationship_extraction(
        attrs, "chunk-001", 1700000000
    )
    assert result is None


@pytest.mark.offline
@pytest.mark.asyncio
async def test_process_cg_extraction_result_entities_and_relations():
    """_process_cg_extraction_result should parse both entities and 6-field relations."""
    from lightrag.context_graph import _process_cg_extraction_result

    rc_json = json.dumps(
        {
            "supporting_sentences": ["They worked together all year"],
            "temporal_info": "2024",
            "quantitative_data": None,
            "decision_trace": None,
            "provenance": None,
            "confidence_score": 0.8,
        }
    )
    extraction_output = (
        f"entity<|#|>Alice<|#|>person<|#|>Alice is a researcher.\n"
        f"entity<|#|>Bob<|#|>person<|#|>Bob is an engineer.\n"
        f"relation<|#|>Alice<|#|>Bob<|#|>collaboration<|#|>They collaborate on research.<|#|>{rc_json}\n"
        f"<|COMPLETE|>"
    )
    nodes, edges = await _process_cg_extraction_result(
        extraction_output, "chunk-test", int(1700000000)
    )
    assert "Alice" in nodes
    assert "Bob" in nodes
    assert ("Alice", "Bob") in edges or ("Bob", "Alice") in edges
    edge_key = ("Alice", "Bob") if ("Alice", "Bob") in edges else ("Bob", "Alice")
    edge = edges[edge_key][0]
    assert "relation_context" in edge
    rc = RelationContext.from_json(edge["relation_context"])
    assert rc.temporal_info == "2024"
    assert abs(rc.confidence_score - 0.8) < 0.001


# ─────────────────────────────────────────────────────────────────────────────
# _collect_relation_context tests
# ─────────────────────────────────────────────────────────────────────────────


class TestCollectRelationContext:
    def test_no_context_returns_none(self):
        from lightrag.operate import _collect_relation_context

        edges = [{"src_id": "A", "tgt_id": "B", "description": "no context"}]
        assert _collect_relation_context(edges, None) is None

    def test_single_context_preserved(self):
        from lightrag.operate import _collect_relation_context

        rc = RelationContext(
            supporting_sentences=["quote"],
            temporal_info="Q1 2025",
            confidence_score=0.9,
        )
        edges = [{"relation_context": rc.to_json()}]
        result = _collect_relation_context(edges, None)
        assert result is not None
        merged = RelationContext.from_json(result)
        assert merged.temporal_info == "Q1 2025"
        assert "quote" in merged.supporting_sentences

    def test_merges_existing_and_new(self):
        from lightrag.operate import _collect_relation_context

        existing = {"relation_context": json.dumps(
            {"supporting_sentences": ["old quote"], "temporal_info": "Q1",
             "quantitative_data": None, "decision_trace": None,
             "provenance": None, "confidence_score": 0.7}
        )}
        new_edges = [{"relation_context": json.dumps(
            {"supporting_sentences": ["new quote"], "temporal_info": None,
             "quantitative_data": "20%", "decision_trace": None,
             "provenance": None, "confidence_score": 0.95}
        )}]
        result = _collect_relation_context(new_edges, existing)
        merged = RelationContext.from_json(result)
        assert "old quote" in merged.supporting_sentences
        assert "new quote" in merged.supporting_sentences
        assert merged.temporal_info == "Q1"
        assert merged.quantitative_data == "20%"
        assert merged.confidence_score == 0.95

    def test_invalid_json_in_existing_is_ignored(self):
        from lightrag.operate import _collect_relation_context

        existing = {"relation_context": "not json"}
        new_edges = [{"relation_context": json.dumps(
            {"supporting_sentences": ["valid"], "temporal_info": None,
             "quantitative_data": None, "decision_trace": None,
             "provenance": None, "confidence_score": 0.8}
        )}]
        result = _collect_relation_context(new_edges, existing)
        merged = RelationContext.from_json(result)
        assert "valid" in merged.supporting_sentences


# ─────────────────────────────────────────────────────────────────────────────
# ContextGraph class smoke tests
# ─────────────────────────────────────────────────────────────────────────────


class TestContextGraphClass:
    def test_import(self):
        from lightrag import ContextGraph, RelationContext, ContextNode, ContextEdge

        assert ContextGraph is not None
        assert RelationContext is not None
        assert ContextNode is not None
        assert ContextEdge is not None

    def test_is_subclass_of_lightrag(self):
        from lightrag import ContextGraph, LightRAG

        assert issubclass(ContextGraph, LightRAG)

    @pytest.mark.offline
    @pytest.mark.asyncio
    async def test_extract_entities_with_context_uses_cg_prompts(self, tmp_path):
        """extract_entities_with_context should call LLM with CG system prompt."""
        from lightrag.context_graph import extract_entities_with_context
        from lightrag.utils import Tokenizer

        class DummyTokenizer:
            def encode(self, text):
                return list(text.encode("utf-8"))

        call_args_store = {}

        async def mock_llm(prompt, system_prompt=None, history_messages=None, **kwargs):
            call_args_store["system_prompt"] = system_prompt
            rc_json = json.dumps(
                {"supporting_sentences": [], "temporal_info": None,
                 "quantitative_data": None, "decision_trace": None,
                 "provenance": None, "confidence_score": 0.8}
            )
            return (
                f"entity<|#|>Alice<|#|>person<|#|>Alice is a researcher.\n"
                f"relation<|#|>Alice<|#|>Bob<|#|>collaboration<|#|>Work together.<|#|>{rc_json}\n"
                f"<|COMPLETE|>"
            )

        chunks = {
            "chunk-001": {
                "tokens": 10,
                "content": "Alice and Bob collaborate.",
                "full_doc_id": "doc-001",
                "chunk_order_index": 0,
                "file_path": "test.txt",
            }
        }
        global_config = {
            "llm_model_func": mock_llm,
            "entity_extract_max_gleaning": 0,
            "addon_params": {"language": "English"},
            "tokenizer": Tokenizer("dummy", DummyTokenizer()),
            "max_extract_input_tokens": 32768,
            "llm_model_max_async": 1,
        }

        results = await extract_entities_with_context(chunks, global_config)
        assert len(results) == 1

        # Confirm the CG system prompt was used
        sp = call_args_store.get("system_prompt", "")
        assert "Relation Context" in sp or "relation_context" in sp.lower() or "rc" in sp.lower()


# ─────────────────────────────────────────────────────────────────────────────
# Phase 5A: New RelationContext fields and is_active()
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.offline
class TestRelationContextNewFields:
    """Tests for Phase 5A structured approval-chain fields and is_active()."""

    def test_new_fields_default_none(self):
        rc = RelationContext()
        assert rc.approved_by is None
        assert rc.approved_via is None
        assert rc.valid_from is None
        assert rc.valid_until is None
        assert rc.policy_ref is None

    def test_new_fields_roundtrip_json(self):
        rc = RelationContext(
            decision_trace="VP approved 20% discount",
            approved_by="VP_Smith",
            approved_via="slack",
            valid_from="2024-08-14",
            valid_until="2024-12-31",
            policy_ref="DiscountPolicy_Standard",
            confidence_score=0.95,
        )
        rc2 = RelationContext.from_json(rc.to_json())
        assert rc2.approved_by == "VP_Smith"
        assert rc2.approved_via == "slack"
        assert rc2.valid_from == "2024-08-14"
        assert rc2.valid_until == "2024-12-31"
        assert rc2.policy_ref == "DiscountPolicy_Standard"

    def test_merge_preserves_approved_by_first_non_none(self):
        rc1 = RelationContext(decision_trace="First", approved_by="Alice")
        rc2 = RelationContext(decision_trace="Second", approved_by="Bob")
        merged = RelationContext.merge([rc1, rc2])
        assert merged.approved_by == "Alice"

    def test_merge_picks_first_non_none_new_fields(self):
        rc1 = RelationContext(valid_from=None, policy_ref=None)
        rc2 = RelationContext(valid_from="2024-01-01", policy_ref="PolicyX")
        merged = RelationContext.merge([rc1, rc2])
        assert merged.valid_from == "2024-01-01"
        assert merged.policy_ref == "PolicyX"

    def test_merge_combines_new_fields_alongside_existing(self):
        rc1 = RelationContext(
            supporting_sentences=["Evidence A"],
            decision_trace="Approved",
            approved_by="VP",
            valid_until="2024-12-31",
        )
        rc2 = RelationContext(
            supporting_sentences=["Evidence B"],
            approved_via="email",
            valid_from="2024-08-01",
        )
        merged = RelationContext.merge([rc1, rc2])
        assert set(merged.supporting_sentences) == {"Evidence A", "Evidence B"}
        assert merged.approved_by == "VP"
        assert merged.approved_via == "email"
        assert merged.valid_from == "2024-08-01"
        assert merged.valid_until == "2024-12-31"

    def test_is_active_no_dates_returns_true(self):
        rc = RelationContext()
        assert rc.is_active() is True

    def test_is_active_within_range(self):
        rc = RelationContext(valid_from="2020-01-01", valid_until="2099-12-31")
        assert rc.is_active() is True

    def test_is_active_expired(self):
        rc = RelationContext(valid_from="2020-01-01", valid_until="2020-12-31")
        assert rc.is_active() is False

    def test_is_active_not_yet_effective(self):
        rc = RelationContext(valid_from="2099-01-01")
        assert rc.is_active() is False

    def test_is_active_only_valid_until_in_past(self):
        rc = RelationContext(valid_until="2020-01-01")
        assert rc.is_active() is False

    def test_is_active_only_valid_from_in_future(self):
        rc = RelationContext(valid_from="2099-01-01")
        assert rc.is_active() is False

    def test_is_active_with_explicit_as_of(self):
        rc = RelationContext(valid_from="2024-01-01", valid_until="2024-12-31")
        assert rc.is_active(as_of="2024-06-15") is True
        assert rc.is_active(as_of="2025-01-01") is False
        assert rc.is_active(as_of="2023-12-31") is False


# ─────────────────────────────────────────────────────────────────────────────
# Phase 5A/5B: ContextGraph new methods (offline, mocked graph)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.offline
@pytest.mark.asyncio
class TestContextGraphNewMethods:
    """Tests for emit_decision_trace, find_precedents, get_all_decisions."""

    def _make_cg(self, graph_mock, vdb_mock):
        """Return a minimal ContextGraph-like object with mocked storage."""
        from unittest.mock import MagicMock

        cg = MagicMock()
        cg.chunk_entity_relation_graph = graph_mock
        cg.decisions_vdb = vdb_mock
        # Bind real method implementations to the mock
        from lightrag.context_graph import ContextGraph

        cg.emit_decision_trace = ContextGraph.emit_decision_trace.__get__(cg, type(cg))
        cg.find_precedents = ContextGraph.find_precedents.__get__(cg, type(cg))
        cg.get_all_decisions = ContextGraph.get_all_decisions.__get__(cg, type(cg))
        return cg

    async def test_emit_decision_trace_creates_edge(self):
        graph = AsyncMock()
        graph.has_edge = AsyncMock(return_value=False)
        graph.upsert_node = AsyncMock()
        graph.upsert_edge = AsyncMock()

        vdb = AsyncMock()
        vdb.upsert = AsyncMock()

        cg = self._make_cg(graph, vdb)

        rc = RelationContext(
            decision_trace="Approved 20% discount",
            approved_by="VP_Smith",
            confidence_score=0.9,
        )
        await cg.emit_decision_trace("Sarah", "MegaCorp", "APPROVES", rc)

        graph.upsert_node.assert_any_await("Sarah", {"entity_type": "ENTITY"})
        graph.upsert_node.assert_any_await("MegaCorp", {"entity_type": "ENTITY"})
        graph.upsert_edge.assert_awaited_once()
        call_kwargs = graph.upsert_edge.call_args
        edge_data = call_kwargs.kwargs.get("edge_data") or call_kwargs.args[2]
        assert edge_data["keywords"] == "APPROVES"
        assert "relation_context" in edge_data
        # Verify RC round-trips
        stored_rc = RelationContext.from_json(edge_data["relation_context"])
        assert stored_rc.approved_by == "VP_Smith"

    async def test_emit_decision_trace_merges_with_existing(self):
        existing_rc = RelationContext(
            decision_trace="Original approval",
            supporting_sentences=["Old evidence"],
        )
        graph = AsyncMock()
        graph.has_edge = AsyncMock(return_value=True)
        graph.get_edge = AsyncMock(return_value={"relation_context": existing_rc.to_json()})
        graph.upsert_node = AsyncMock()
        graph.upsert_edge = AsyncMock()

        vdb = AsyncMock()
        vdb.upsert = AsyncMock()

        cg = self._make_cg(graph, vdb)

        new_rc = RelationContext(
            decision_trace="Updated approval",
            supporting_sentences=["New evidence"],
        )
        await cg.emit_decision_trace("A", "B", "UPDATES", new_rc)

        edge_data = graph.upsert_edge.call_args.kwargs.get("edge_data") or graph.upsert_edge.call_args.args[2]
        stored_rc = RelationContext.from_json(edge_data["relation_context"])
        # Merge should union supporting_sentences
        assert "Old evidence" in stored_rc.supporting_sentences
        assert "New evidence" in stored_rc.supporting_sentences

    async def test_emit_decision_trace_indexes_in_vdb(self):
        graph = AsyncMock()
        graph.has_edge = AsyncMock(return_value=False)
        graph.upsert_node = AsyncMock()
        graph.upsert_edge = AsyncMock()

        vdb = AsyncMock()
        vdb.upsert = AsyncMock()

        cg = self._make_cg(graph, vdb)

        rc = RelationContext(decision_trace="Some decision", confidence_score=0.8)
        await cg.emit_decision_trace("X", "Y", "DECIDES", rc)

        # decisions_vdb.upsert should have been called
        vdb.upsert.assert_awaited_once()
        upsert_arg = vdb.upsert.call_args.args[0]
        # upsert dict has one entry whose value contains src_id/tgt_id
        entry = next(iter(upsert_arg.values()))
        assert entry["src_id"] == "X"
        assert entry["tgt_id"] == "Y"
        assert "Some decision" in entry["content"]

    async def test_emit_decision_trace_no_vdb_upsert_without_decision_trace(self):
        graph = AsyncMock()
        graph.has_edge = AsyncMock(return_value=False)
        graph.upsert_node = AsyncMock()
        graph.upsert_edge = AsyncMock()

        vdb = AsyncMock()
        vdb.upsert = AsyncMock()

        cg = self._make_cg(graph, vdb)

        rc = RelationContext(temporal_info="Q4 2025")  # no decision_trace
        await cg.emit_decision_trace("A", "B", "LINKS", rc)

        vdb.upsert.assert_not_awaited()

    async def test_get_all_decisions_no_filter(self):
        rc1 = RelationContext(decision_trace="Approved discount", confidence_score=0.9)
        rc2 = RelationContext(decision_trace="Rejected proposal", confidence_score=0.7)
        rc_no_trace = RelationContext(temporal_info="Q1 2025")

        all_edges = [
            {"source": "A", "target": "B", "relation_context": rc1.to_json()},
            {"source": "C", "target": "D", "relation_context": rc2.to_json()},
            {"source": "E", "target": "F", "relation_context": rc_no_trace.to_json()},
            {"source": "G", "target": "H"},  # no relation_context
        ]

        graph = AsyncMock()
        graph.get_all_edges = AsyncMock(return_value=all_edges)

        vdb = AsyncMock()
        cg = self._make_cg(graph, vdb)

        results = await cg.get_all_decisions()
        assert len(results) == 2
        src_ids = {r["src_id"] for r in results}
        assert src_ids == {"A", "C"}

    async def test_get_all_decisions_filter_approved_by(self):
        rc1 = RelationContext(decision_trace="Approved", approved_by="Alice")
        rc2 = RelationContext(decision_trace="Also approved", approved_by="Bob")

        all_edges = [
            {"source": "A", "target": "B", "relation_context": rc1.to_json()},
            {"source": "C", "target": "D", "relation_context": rc2.to_json()},
        ]

        graph = AsyncMock()
        graph.get_all_edges = AsyncMock(return_value=all_edges)

        vdb = AsyncMock()
        cg = self._make_cg(graph, vdb)

        results = await cg.get_all_decisions(approved_by="Alice")
        assert len(results) == 1
        assert results[0]["src_id"] == "A"

    async def test_get_all_decisions_filter_active_as_of(self):
        rc_active = RelationContext(
            decision_trace="Active decision",
            valid_from="2024-01-01",
            valid_until="2024-12-31",
        )
        rc_expired = RelationContext(
            decision_trace="Expired decision",
            valid_from="2020-01-01",
            valid_until="2020-12-31",
        )

        all_edges = [
            {"source": "A", "target": "B", "relation_context": rc_active.to_json()},
            {"source": "C", "target": "D", "relation_context": rc_expired.to_json()},
        ]

        graph = AsyncMock()
        graph.get_all_edges = AsyncMock(return_value=all_edges)

        vdb = AsyncMock()
        cg = self._make_cg(graph, vdb)

        results = await cg.get_all_decisions(active_as_of="2024-06-15")
        assert len(results) == 1
        assert results[0]["src_id"] == "A"
