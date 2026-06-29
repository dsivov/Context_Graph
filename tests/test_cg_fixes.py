"""Regression tests for three Context Graph fixes:

- C1: CGR3 reason-step JSON parsing (fenced / prose / non-object output)
- C2: RelationContext.is_empty() must consider all content fields
- H2: the base relation parser must preserve a 6th RelationContext field so
      graph rebuilds (e.g. after document deletion) don't drop decision lineage
"""
import json

import pytest

from lightrag.context_graph import _extract_json_object
from lightrag.context_graph_types import RelationContext
from lightrag.operate import _handle_single_relationship_extraction


# ── C2: is_empty() considers every content field ─────────────────────────────

class TestIsEmptyAllFields:
    def test_truly_empty(self):
        assert RelationContext().is_empty() is True

    def test_confidence_only_is_still_empty(self):
        # confidence_score is metadata, not content
        assert RelationContext(confidence_score=0.9).is_empty() is True

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"approved_by": "VP_Smith"},
            {"approved_via": "slack"},
            {"valid_from": "2024-01-01"},
            {"valid_until": "2024-12-31"},
            {"policy_ref": "DiscountPolicy_Standard"},
            {"decision_trace": "approved 20% discount"},
            {"quantitative_data": "20%"},
            {"temporal_info": "Q4 2024"},
            {"provenance": "Slack #deals"},
            {"supporting_sentences": ["a quote"]},
        ],
    )
    def test_any_content_field_makes_it_nonempty(self, kwargs):
        assert RelationContext(**kwargs).is_empty() is False

    def test_decision_only_rc_survives_api_emptiness_check(self):
        # The exact C2 scenario: a decision recorded only via approval lineage.
        rc = RelationContext(
            approved_by="Sarah Chen", valid_until="2024-12-31",
            policy_ref="DiscountPolicy_Standard",
        )
        assert rc.is_empty() is False


# ── C1: robust JSON extraction for the CGR3 reason step ──────────────────────

class TestExtractJsonObject:
    def test_plain_object(self):
        assert _extract_json_object('{"is_sufficient": true}') == {"is_sufficient": True}

    def test_fenced_json_block(self):
        # The previously-broken case: a closed ```json fence yielded "" → crash.
        text = '```json\n{"is_sufficient": true, "answer": "yes"}\n```'
        assert _extract_json_object(text) == {"is_sufficient": True, "answer": "yes"}

    def test_fenced_block_no_lang(self):
        text = '```\n{"is_sufficient": false, "follow_up_entities": ["X"]}\n```'
        assert _extract_json_object(text) == {
            "is_sufficient": False,
            "follow_up_entities": ["X"],
        }

    def test_prose_wrapped_object(self):
        text = 'Here is my answer:\n{"is_sufficient": true}\nHope that helps!'
        assert _extract_json_object(text) == {"is_sufficient": True}

    @pytest.mark.parametrize("text", ["[1, 2, 3]", "123", '"a string"', "null", "true"])
    def test_non_object_returns_none(self, text):
        # Valid JSON but not an object → None (so callers don't crash on .get()).
        assert _extract_json_object(text) is None

    @pytest.mark.parametrize("text", ["", "   ", None, "not json at all", "```json\n```"])
    def test_unparseable_returns_none(self, text):
        assert _extract_json_object(text) is None

    def test_get_on_result_is_safe(self):
        # Mirrors the cgr3_query usage: parsed may be None → caller checks first.
        for text in ["[1,2]", "garbage", '```json\n{"is_sufficient": true}\n```']:
            parsed = _extract_json_object(text)
            assert parsed is None or isinstance(parsed, dict)


# ── H2: base parser preserves the 6th RelationContext field ──────────────────

REL_CTX = '{"decision_trace": "Approved 20% discount", "approved_by": "Sarah Chen", "confidence_score": 0.97}'


class TestRelationParserPreservesContext:
    @pytest.mark.asyncio
    async def test_six_field_record_keeps_relation_context(self):
        rec = ["relation", "Sarah Chen", "MegaCorp", "discount,approval",
               "Approved a 20% discount", REL_CTX]
        edge = await _handle_single_relationship_extraction(rec, "chunk-1", 0)
        assert edge is not None
        assert edge["src_id"] == "Sarah Chen" and edge["tgt_id"] == "MegaCorp"
        assert "relation_context" in edge
        rc = json.loads(edge["relation_context"])
        assert rc["approved_by"] == "Sarah Chen"
        assert rc["confidence_score"] == 0.97

    @pytest.mark.asyncio
    async def test_five_field_record_has_no_relation_context(self):
        rec = ["relation", "A", "B", "kw", "a description"]
        edge = await _handle_single_relationship_extraction(rec, "chunk-1", 0)
        assert edge is not None
        assert "relation_context" not in edge

    @pytest.mark.asyncio
    async def test_confidence_is_normalized(self):
        rec = ["relation", "A", "B", "kw", "desc",
               '{"decision_trace": "x", "confidence_score": 1.5}']
        edge = await _handle_single_relationship_extraction(rec, "c", 0)
        assert json.loads(edge["relation_context"])["confidence_score"] == 1.0

    @pytest.mark.asyncio
    async def test_bad_confidence_falls_back_to_one(self):
        rec = ["relation", "A", "B", "kw", "desc",
               '{"decision_trace": "x", "confidence_score": "oops"}']
        edge = await _handle_single_relationship_extraction(rec, "c", 0)
        assert json.loads(edge["relation_context"])["confidence_score"] == 1.0

    @pytest.mark.asyncio
    async def test_malformed_rc_json_is_ignored_but_edge_survives(self):
        rec = ["relation", "A", "B", "kw", "desc", "{not valid json}"]
        edge = await _handle_single_relationship_extraction(rec, "c", 0)
        assert edge is not None
        assert "relation_context" not in edge

    @pytest.mark.asyncio
    async def test_wrong_field_count_rejected(self):
        too_few = ["relation", "A", "B", "kw"]            # 4
        too_many = ["relation", "A", "B", "kw", "d", REL_CTX, "extra"]  # 7
        assert await _handle_single_relationship_extraction(too_few, "c", 0) is None
        assert await _handle_single_relationship_extraction(too_many, "c", 0) is None


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
