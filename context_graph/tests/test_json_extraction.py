"""Step 4 prototype — JSON extraction parser.

_process_cg_json_result converts a parsed {"entities":[...], "relationships":[...]}
dict into the SAME (maybe_nodes, maybe_edges) structure the delimiter parser
produces, with relation_context as a first-class JSON key. Offline.
"""

from __future__ import annotations

import json
import pytest

from context_graph.core import _process_cg_json_result, _rc_json_from_obj


@pytest.mark.offline
def test_rc_normalizer_clamps_and_serializes():
    out = _rc_json_from_obj({"decision_trace": "x", "confidence_score": 1.7}, "c", "A", "B")
    d = json.loads(out)
    assert d["decision_trace"] == "x"
    assert d["confidence_score"] == 1.0                 # clamped to [0,1]
    assert _rc_json_from_obj(None, "c", "A", "B") is None
    assert _rc_json_from_obj("not json", "c", "A", "B") is None
    assert _rc_json_from_obj("[1,2]", "c", "A", "B") is None   # not an object


@pytest.mark.offline
@pytest.mark.asyncio
async def test_parses_entities_and_relationships_with_rc():
    parsed = {
        "entities": [
            {"entity_name": "Sarah Chen", "entity_type": "Person", "description": "a VP"},
            {"name": "MegaCorp", "type": "Org", "description": "a customer"},  # alt field names
        ],
        "relationships": [
            {
                "src_id": "Sarah Chen", "tgt_id": "MegaCorp",
                "keywords": "APPROVES", "description": "approved a discount", "weight": 2.0,
                "relation_context": {"decision_trace": "20% discount", "approved_via": "slack",
                                     "confidence_score": 0.97},
            }
        ],
    }
    nodes, edges = await _process_cg_json_result(parsed, "chunk-1", 123, "deals.md")

    assert set(nodes) == {"Sarah Chen", "MegaCorp"}
    n = nodes["Sarah Chen"][0]
    assert n["entity_type"] == "Person" and n["source_id"] == "chunk-1" and n["file_path"] == "deals.md"

    e = edges[("Sarah Chen", "MegaCorp")][0]
    assert e["src_id"] == "Sarah Chen" and e["tgt_id"] == "MegaCorp"
    assert e["keywords"] == "APPROVES" and e["weight"] == 2.0
    assert e["source_id"] == "chunk-1"
    rc = json.loads(e["relation_context"])
    assert rc["approved_via"] == "slack" and rc["confidence_score"] == 0.97


@pytest.mark.offline
@pytest.mark.asyncio
async def test_alt_field_names_and_drops_bad_records():
    parsed = {
        "entities": [{"name": "", "description": "empty name dropped"}],
        "relations": [                                   # alt top-level key
            {"source": "A", "target": "A", "keywords": "k", "description": "self-loop dropped"},
            {"source": "X", "target": "Y", "relationship_keywords": "LINKS",
             "relationship_description": "x links y"},   # alt field names, no rc
        ],
    }
    nodes, edges = await _process_cg_json_result(parsed, "c", 1)
    assert nodes == {}                                   # empty-name entity dropped
    assert ("A", "A") not in edges                       # self-loop dropped
    e = edges[("X", "Y")][0]
    assert e["keywords"] == "LINKS" and "relation_context" not in e


@pytest.mark.offline
@pytest.mark.asyncio
async def test_non_dict_input_is_safe():
    nodes, edges = await _process_cg_json_result("not a dict", "c", 1)
    assert nodes == {} and edges == {}
