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

    def test_merge_preserves_explicit_zero_confidence(self):
        # E4: a deliberately zero-confidence decision must NOT be promoted to 1.0.
        rc1 = RelationContext(decision_trace="d", confidence_score=0.0)
        rc2 = RelationContext(decision_trace="e", confidence_score=0.0)
        assert RelationContext.merge([rc1, rc2]).confidence_score == 0.0
        # max still wins across differing scores
        rc3 = RelationContext(decision_trace="f", confidence_score=0.7)
        assert RelationContext.merge([rc1, rc3]).confidence_score == 0.7
        # empty merge still defaults to 1.0
        assert RelationContext.merge([]).confidence_score == 1.0

    def test_is_active_ignores_time_component(self):
        # E5: as_of with a time part must still be active on the final valid day.
        rc = RelationContext(
            decision_trace="d", valid_from="2026-01-01", valid_until="2026-07-06"
        )
        assert rc.is_active("2026-07-06T10:00:00") is True   # last valid day, with time
        assert rc.is_active("2026-07-06") is True
        assert rc.is_active("2026-07-07T00:01") is False      # day after → expired
        assert rc.is_active("2025-12-31T23:59") is False      # before valid_from


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
        from unittest.mock import AsyncMock, MagicMock

        cg = MagicMock()
        cg.chunk_entity_relation_graph = graph_mock
        cg.decisions_vdb = vdb_mock
        # emit_decision_trace also projects into the retrieval fabric (_index_decision)
        cg.relationships_vdb = AsyncMock()
        # Bind real method implementations to the mock
        from lightrag.context_graph import ContextGraph

        cg.emit_decision_trace = ContextGraph.emit_decision_trace.__get__(cg, type(cg))
        cg._index_decision = ContextGraph._index_decision.__get__(cg, type(cg))
        cg._persist_decision_indices = ContextGraph._persist_decision_indices.__get__(
            cg, type(cg)
        )
        cg.find_precedents = ContextGraph.find_precedents.__get__(cg, type(cg))
        cg.get_all_decisions = ContextGraph.get_all_decisions.__get__(cg, type(cg))
        cg.reindex_decisions = ContextGraph.reindex_decisions.__get__(cg, type(cg))
        return cg

    async def test_emit_decision_trace_creates_edge(self):
        graph = AsyncMock()
        graph.has_edge = AsyncMock(return_value=False)
        graph.get_node = AsyncMock(return_value=None)  # nodes absent → created
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

        graph.upsert_node.assert_any_await("Sarah", {
            "entity_id": "Sarah",
            "entity_type": "ENTITY",
            "source_id": "emit_decision_trace",
            "description": "Sarah",
            "file_path": "agent_runtime",
        })
        graph.upsert_node.assert_any_await("MegaCorp", {
            "entity_id": "MegaCorp",
            "entity_type": "ENTITY",
            "source_id": "emit_decision_trace",
            "description": "MegaCorp",
            "file_path": "agent_runtime",
        })
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
        # B5: the NEW decision must win on scalar fields, not the stale one
        assert stored_rc.decision_trace == "Updated approval"

    async def test_emit_does_not_clobber_existing_node(self):
        """B6: emit must not overwrite a richer, already-extracted entity node."""
        graph = AsyncMock()
        graph.has_edge = AsyncMock(return_value=False)
        # Both endpoints already exist with rich extracted profiles
        graph.get_node = AsyncMock(
            return_value={"entity_id": "x", "entity_type": "Person",
                          "description": "A detailed extracted profile"}
        )
        graph.upsert_node = AsyncMock()
        graph.upsert_edge = AsyncMock()
        cg = self._make_cg(graph, AsyncMock())

        await cg.emit_decision_trace(
            "Sarah", "MegaCorp", "APPROVES",
            RelationContext(decision_trace="Approved"),
        )
        # Existing nodes are left untouched (no description/type clobber)
        graph.upsert_node.assert_not_awaited()
        graph.upsert_edge.assert_awaited_once()

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

    # ── P1: decision-index integrity ────────────────────────────────────────

    async def test_index_decision_uses_canonical_sorted_ids(self):
        """Both orientations of the same edge write ONE canonical record and
        delete the reverse-orientation id (B3)."""
        from lightrag.utils import compute_mdhash_id

        expected_rel = compute_mdhash_id("A" + "Z", prefix="rel-")
        expected_dec = compute_mdhash_id("A>Z", prefix="dec-")

        for src, tgt in [("A", "Z"), ("Z", "A")]:
            graph = AsyncMock()
            graph.has_edge = AsyncMock(return_value=False)
            graph.upsert_node = AsyncMock()
            graph.upsert_edge = AsyncMock()
            rel_vdb = AsyncMock()
            dec_vdb = AsyncMock()
            cg = self._make_cg(graph, dec_vdb)
            cg.relationships_vdb = rel_vdb

            rc = RelationContext(decision_trace="Approved", confidence_score=0.8)
            await cg.emit_decision_trace(src, tgt, "APPROVES", rc)

            # canonical rel-id, sorted src/tgt stored, reverse deleted
            rel_payload = rel_vdb.upsert.await_args.args[0]
            assert set(rel_payload) == {expected_rel}
            entry = rel_payload[expected_rel]
            assert (entry["src_id"], entry["tgt_id"]) == ("A", "Z")
            rel_vdb.delete.assert_awaited()  # reverse-id cleanup

            # canonical dec-id, sorted src/tgt stored
            dec_payload = dec_vdb.upsert.await_args.args[0]
            assert set(dec_payload) == {expected_dec}
            assert (dec_payload[expected_dec]["src_id"],
                    dec_payload[expected_dec]["tgt_id"]) == ("A", "Z")

    async def test_emit_persists_decision_indices(self):
        """Runtime emit flushes both derived indices to disk (B2)."""
        graph = AsyncMock()
        graph.has_edge = AsyncMock(return_value=False)
        graph.upsert_node = AsyncMock()
        graph.upsert_edge = AsyncMock()
        dec_vdb = AsyncMock()
        rel_vdb = AsyncMock()
        cg = self._make_cg(graph, dec_vdb)
        cg.relationships_vdb = rel_vdb

        await cg.emit_decision_trace(
            "A", "B", "APPROVES", RelationContext(decision_trace="ok")
        )
        dec_vdb.index_done_callback.assert_awaited()
        rel_vdb.index_done_callback.assert_awaited()
        # B2: the graph (source of truth) must be flushed too, else a file-based
        # backend loses the emitted edge on restart.
        graph.index_done_callback.assert_awaited()

    async def test_reindex_drops_then_repopulates(self):
        """Authoritative rebuild: decisions_vdb is dropped (orphan removal) then
        repopulated from the graph, and the result is persisted (B4)."""
        rc1 = RelationContext(decision_trace="Approved discount", confidence_score=0.9)
        edges = [
            {"source": "A", "target": "B", "keywords": "APPROVES",
             "relation_context": rc1.to_json()},
            {"source": "C", "target": "D", "keywords": "REJECTS",
             "relation_context": RelationContext(temporal_info="Q1").to_json()},  # no trace
        ]
        graph = AsyncMock()
        graph.get_edges_with_relation_context = AsyncMock(return_value=edges)
        dec_vdb = AsyncMock()
        rel_vdb = AsyncMock()
        cg = self._make_cg(graph, dec_vdb)
        cg.relationships_vdb = rel_vdb

        result = await cg.reindex_decisions()

        dec_vdb.drop.assert_awaited()  # orphan removal
        assert result == {"reindexed": 1}  # only the edge with a decision_trace
        dec_vdb.upsert.assert_awaited()  # repopulated
        dec_vdb.index_done_callback.assert_awaited()  # persisted

    # ── Graph-quality v-next · Phase 0: connectivity report ─────────────────

    async def test_connectivity_report(self):
        from lightrag.context_graph import ContextGraph

        # A→B→C is one component (3 nodes, 2 edges); D and E are isolates.
        labels = ["A", "B", "C", "D", "E"]
        edges = [
            {"source": "A", "target": "B"},
            {"source": "B", "target": "C"},
            {"source": "X", "target": "A"},  # dangling endpoint → ignored
            {"source": "D", "target": "D"},  # self-loop → ignored
        ]
        graph = AsyncMock()
        graph.get_all_labels = AsyncMock(return_value=labels)
        graph.get_all_edges = AsyncMock(return_value=edges)
        cg = self._make_cg(graph, AsyncMock())
        cg.connectivity_report = ContextGraph.connectivity_report.__get__(cg, type(cg))

        r = await cg.connectivity_report()
        assert r["total_nodes"] == 5
        assert r["total_edges"] == 2          # dangling + self-loop excluded
        assert r["isolated_nodes"] == 2       # D and E
        assert r["isolated_pct"] == 40.0
        assert r["connected_components"] == 3  # {A,B,C}, {D}, {E}
        assert r["largest_component_size"] == 3
        assert r["degree"]["max"] == 2        # B
        assert set(r["isolate_sample"]) == {"D", "E"}

    async def test_connectivity_report_empty(self):
        from lightrag.context_graph import ContextGraph

        graph = AsyncMock()
        graph.get_all_labels = AsyncMock(return_value=[])
        graph.get_all_edges = AsyncMock(return_value=[])
        cg = self._make_cg(graph, AsyncMock())
        cg.connectivity_report = ContextGraph.connectivity_report.__get__(cg, type(cg))
        r = await cg.connectivity_report()
        assert r["total_nodes"] == 0 and r["connected_components"] == 0


class TestDedupWiring:
    """ContextGraph dedup methods (Topic 1, increment 4) over injected deps."""

    def _cg(self, labels, query_fn, node_type="Organization"):
        from unittest.mock import AsyncMock
        from lightrag.context_graph import ContextGraph
        from context_graph.dedup import InMemoryDedupStore

        cg = ContextGraph.__new__(ContextGraph)
        cg.workspace = "ws"
        cg._dedup_store = InMemoryDedupStore()
        graph = AsyncMock()
        graph.get_all_labels = AsyncMock(return_value=labels)
        graph.get_node = AsyncMock(return_value={"entity_type": node_type})
        cg.chunk_entity_relation_graph = graph
        vdb = AsyncMock()
        vdb.query = query_fn
        cg.entities_vdb = vdb
        cg.amerge_entities = AsyncMock()
        for m in ("deduplicate_entities", "_apply_entity_merge", "_dedup_thresholds",
                  "run_dedup_sweep", "unmerge_entity"):
            setattr(cg, m, getattr(ContextGraph, m).__get__(cg, type(cg)))
        return cg

    async def test_scan_auto_merges_hard_and_records(self):
        async def q(text, top_k=5):
            other = "Apple" if text == "Apple Inc." else "Apple Inc."
            return [{"entity_name": other, "distance": 0.97, "entity_type": "Organization"}]

        cg = self._cg(["Apple Inc.", "Apple"], q)
        r = await cg.deduplicate_entities()
        assert r["merged"] == 1                      # Apple Inc. → Apple (second is skipped)
        cg.amerge_entities.assert_awaited()          # graph-level merge applied
        assert len(cg._dedup_store.list_merges("ws")) == 1

    async def test_scan_queues_gray_zone(self):
        async def q(text, top_k=5):
            other = "Apple" if text != "Apple" else "Apple Inc."
            return [{"entity_name": other, "distance": 0.88, "entity_type": "Organization"}]

        cg = self._cg(["Apple Inc.", "Apple"], q)
        r = await cg.deduplicate_entities()
        assert r["merged"] == 0 and r["queued"] >= 1
        cg.amerge_entities.assert_not_awaited()      # never merged inline
        assert len(cg._dedup_store.list_pending("ws")) >= 1

    async def test_scan_respects_type_conflict(self):
        async def q(text, top_k=5):
            return [{"entity_name": "Apple", "distance": 0.99, "entity_type": "Person"}]

        cg = self._cg(["Apple Inc."], q, node_type="Organization")
        r = await cg.deduplicate_entities()
        assert r["merged"] == 0                       # D4: known type conflict blocks

    async def test_apply_entity_merge_sets_canonical_name(self):
        cg = self._cg([], None)
        await cg._apply_entity_merge("IBM", "International Business Machines",
                                     "International Business Machines")
        cg.amerge_entities.assert_awaited_once()
        assert cg._dedup_store.canonical_name("ws", "International Business Machines") == \
            "International Business Machines"

    def test_master_switch(self, monkeypatch):
        from lightrag.context_graph import ContextGraph
        cg = ContextGraph.__new__(ContextGraph)
        monkeypatch.delenv("DEDUP_ENABLED", raising=False)
        assert cg.dedup_enabled is True                 # default on
        monkeypatch.setenv("DEDUP_ENABLED", "false")
        assert cg.dedup_enabled is False
        monkeypatch.setenv("DEDUP_ENABLED", "true")
        assert cg.dedup_enabled is True

    def test_sweep_batch_config(self, monkeypatch):
        from lightrag.context_graph import ContextGraph
        cg = ContextGraph.__new__(ContextGraph)
        monkeypatch.setenv("DEDUP_SWEEP_BATCH", "25")
        assert cg._dedup_sweep_batch() == 25
        monkeypatch.setenv("DEDUP_SWEEP_BATCH", "bad")
        assert cg._dedup_sweep_batch() == 10            # graceful fallback


class TestGarbageFilterWiring:
    """ContextGraph._filter_extracted quarantines garbage nodes + drops their edges."""

    def _cg(self):
        from lightrag.context_graph import ContextGraph
        from context_graph.quality import NodeFilter, InMemoryQuarantineStore
        cg = ContextGraph.__new__(ContextGraph)
        cg.workspace = "ws"
        cg._quarantine_store = InMemoryQuarantineStore()
        cg._node_filter_cache = NodeFilter()             # DEFAULT_ENTITY_TYPES fallback
        for m in ("_filter_extracted", "_garbage_filter_enabled", "_node_filter"):
            setattr(cg, m, getattr(ContextGraph, m).__get__(cg, type(cg)))
        return cg

    def test_quarantines_garbage_and_drops_edges(self, monkeypatch):
        monkeypatch.delenv("GARBAGE_FILTER_ENABLED", raising=False)
        cg = self._cg()
        nodes = {
            "PostgreSQL": [{"entity_name": "PostgreSQL", "entity_type": "Data",
                            "description": "a relational database"}],
            "it": [{"entity_name": "it", "entity_type": "Concept", "description": "x"}],
            "the system": [{"entity_name": "the system", "entity_type": "Concept",
                            "description": "y"}],
        }
        edges = {
            ("PostgreSQL", "it"): [{}],           # touches garbage → dropped
            ("PostgreSQL", "Redis"): [{}],        # clean → kept
        }
        out = cg._filter_extracted([(nodes, edges)])
        (kn, ke) = out[0]
        assert set(kn) == {"PostgreSQL"}                   # garbage removed
        assert set(ke) == {("PostgreSQL", "Redis")}        # edge to garbage dropped
        q = {i["name"] for i in cg._quarantine_store.list("ws")}
        assert q == {"it", "the system"}

    def test_disabled_is_noop(self, monkeypatch):
        monkeypatch.setenv("GARBAGE_FILTER_ENABLED", "false")
        cg = self._cg()
        nodes = {"it": [{"entity_name": "it", "entity_type": "Concept", "description": "x"}]}
        out = cg._filter_extracted([(nodes, {})])
        assert set(out[0][0]) == {"it"}                    # untouched
        assert cg._quarantine_store.list("ws") == []

    async def test_scan_garbage_preview_does_not_mutate(self, monkeypatch):
        from unittest.mock import AsyncMock
        from lightrag.context_graph import ContextGraph
        monkeypatch.delenv("GARBAGE_FILTER_ENABLED", raising=False)
        cg = self._cg()
        graph = AsyncMock()
        graph.get_all_labels = AsyncMock(return_value=["PostgreSQL", "37b04c1", "ALERT_THRESHOLD"])
        graph.get_node = AsyncMock(return_value={"entity_type": "Data", "description": "d"})
        graph.delete_node = AsyncMock()
        cg.chunk_entity_relation_graph = graph
        cg.entities_vdb = AsyncMock()
        for m in ("scan_garbage", "_remove_entity"):
            setattr(cg, m, getattr(ContextGraph, m).__get__(cg, type(cg)))
        # preview
        r = await cg.scan_garbage(apply=False)
        assert r["quarantined"] == 2 and r["removed"] == 0     # hash + env-var
        graph.delete_node.assert_not_awaited()
        assert cg._quarantine_store.list("ws") == []           # preview mutates nothing
        # apply
        r = await cg.scan_garbage(apply=True)
        assert r["removed"] == 2
        assert graph.delete_node.await_count == 2
        assert len(cg._quarantine_store.list("ws")) == 2


# ─────────────────────────────────────────────────────────────────────────────
# P3: query-time decision blend & by-name injection (aquery_llm)
# ─────────────────────────────────────────────────────────────────────────────


class TestQueryBlend:
    """Blend is injected via user_prompt (mode-agnostic, cache-keyed, non-clobbering)
    and the empty-retrieval fallback keys strictly on the [no-context] marker."""

    def _cg(self, precedents, named_nodes):
        from unittest.mock import AsyncMock
        from lightrag.context_graph import ContextGraph

        cg = ContextGraph.__new__(ContextGraph)
        cg.decisions_vdb = AsyncMock()
        cg._collect_blend_context = AsyncMock(return_value=(precedents, named_nodes))
        cg.llm_model_func = AsyncMock(return_value="fallback answer")
        return cg

    async def _run(self, cg, param, system_prompt=None, super_result=None):
        """Invoke ContextGraph.aquery_llm with LightRAG.aquery_llm patched to capture
        what the base receives and to return a controllable result."""
        from unittest.mock import patch
        from lightrag.context_graph import ContextGraph
        from lightrag.lightrag import LightRAG

        captured = {}

        async def fake_super(self, query, param=None, system_prompt=None):
            captured["user_prompt"] = getattr(param, "user_prompt", None)
            captured["system_prompt"] = system_prompt
            return super_result or {
                "status": "success",
                "llm_response": {"content": "base answer", "response_iterator": None},
            }

        with patch.object(LightRAG, "aquery_llm", fake_super):
            result = await ContextGraph.aquery_llm(
                cg, "explain adr_m2_concurrency latency", param, system_prompt=system_prompt
            )
        return result, captured

    async def test_blend_injects_user_prompt_and_preserves_system_prompt(self):
        from lightrag.base import QueryParam

        precedents = [{
            "src_id": "A", "tgt_id": "B",
            "relation_context": RelationContext(decision_trace="Chose X for latency"),
        }]
        cg = self._cg(precedents, [])
        _, captured = await self._run(
            cg, QueryParam(mode="naive"), system_prompt="CUSTOM SYSTEM PROMPT"
        )
        # C2/C4: block lands in user_prompt (cache-keyed), system_prompt untouched
        assert "Chose X for latency" in captured["user_prompt"]
        assert captured["system_prompt"] == "CUSTOM SYSTEM PROMPT"

    async def test_blend_appends_to_existing_user_prompt(self):
        from lightrag.base import QueryParam

        precedents = [{
            "src_id": "A", "tgt_id": "B",
            "relation_context": RelationContext(decision_trace="Decision text"),
        }]
        cg = self._cg(precedents, [])
        _, captured = await self._run(cg, QueryParam(mode="mix", user_prompt="be terse"))
        assert captured["user_prompt"].startswith("be terse")
        assert "Decision text" in captured["user_prompt"]

    async def test_bypass_mode_skips_blend(self):
        from lightrag.base import QueryParam

        precedents = [{
            "src_id": "A", "tgt_id": "B",
            "relation_context": RelationContext(decision_trace="d"),
        }]
        cg = self._cg(precedents, [])
        _, captured = await self._run(cg, QueryParam(mode="bypass"))
        assert captured["user_prompt"] is None  # nothing injected

    async def test_fallback_fires_only_on_no_context_marker(self):
        from lightrag.base import QueryParam

        precedents = [{
            "src_id": "A", "tgt_id": "B",
            "relation_context": RelationContext(decision_trace="The recorded decision"),
        }]
        cg = self._cg(precedents, [])
        # retrieval empty → fail_response marker → fallback answers from structural ctx
        result, _ = await self._run(
            cg, QueryParam(mode="mix"),
            super_result={
                "status": "failure",
                "llm_response": {
                    "content": "Sorry, I'm not able to provide an answer.[no-context]",
                    "response_iterator": None,
                },
            },
        )
        assert result["llm_response"]["content"] == "fallback answer"
        assert result["status"] == "success"

    async def test_fallback_does_not_clobber_legit_answer(self):
        from lightrag.base import QueryParam

        precedents = [{
            "src_id": "A", "tgt_id": "B",
            "relation_context": RelationContext(decision_trace="d"),
        }]
        cg = self._cg(precedents, [])
        # A real grounded answer mentioning "enough information" must NOT be replaced
        legit = "The team lacked enough information about latency, so they chose X."
        result, _ = await self._run(
            cg, QueryParam(mode="mix"),
            super_result={
                "status": "success",
                "llm_response": {"content": legit, "response_iterator": None},
            },
        )
        assert result["llm_response"]["content"] == legit
        cg.llm_model_func.assert_not_awaited()

    async def test_fallback_skipped_for_only_need_prompt(self):
        from lightrag.base import QueryParam

        precedents = [{
            "src_id": "A", "tgt_id": "B",
            "relation_context": RelationContext(decision_trace="d"),
        }]
        cg = self._cg(precedents, [])
        result, _ = await self._run(
            cg, QueryParam(mode="mix", only_need_prompt=True),
            super_result={
                "status": "success",
                "llm_response": {"content": "prompt...[no-context]", "response_iterator": None},
            },
        )
        cg.llm_model_func.assert_not_awaited()  # no extra LLM call for prompt-only

    def test_build_blend_block_respects_char_budget(self):
        from lightrag.context_graph import ContextGraph

        cg = ContextGraph.__new__(ContextGraph)
        precedents = [{
            "src_id": f"S{i}", "tgt_id": f"T{i}",
            "relation_context": RelationContext(decision_trace="x" * 500),
        } for i in range(50)]
        block = cg._build_blend_block(precedents, [])
        assert len(block) <= ContextGraph._BLEND_CHAR_BUDGET + 32  # + truncation marker
        assert "truncated" in block

    async def test_collect_blend_caps_graph_probes(self):
        from unittest.mock import AsyncMock
        from lightrag.context_graph import ContextGraph

        cg = ContextGraph.__new__(ContextGraph)
        cg.decisions_vdb = AsyncMock()
        cg.find_precedents = AsyncMock(return_value=[])
        graph = AsyncMock()
        graph.has_node = AsyncMock(return_value=False)  # no matches, just count probes
        cg.chunk_entity_relation_graph = graph
        # A query with far more than the scan cap of candidate tokens
        query = " ".join(f"token{i:03d}" for i in range(200))
        await cg._collect_blend_context(query)
        assert graph.has_node.await_count <= ContextGraph._BLEND_MAX_SCAN_TOKENS
