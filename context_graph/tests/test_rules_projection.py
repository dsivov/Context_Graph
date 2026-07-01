"""Tests for project_decision() / project_edge() (lightrag-free, fully offline).

Covers free-text numeric parsing, the full projection, and — most importantly —
that both input shapes (emit tuple vs stored edge dict) yield identical params.
"""

from __future__ import annotations

import pytest

from lightrag.context_graph_types import RelationContext
from context_graph.rules import (
    project_decision,
    project_edge,
    parse_amount,
    parse_percent,
)


# ── parse_percent ────────────────────────────────────────────────────────────


@pytest.mark.offline
@pytest.mark.parametrize(
    "text,expected",
    [
        ("20% discount", 0.20),
        ("20 %", 0.20),
        ("15 percent off", 0.15),
        ("a 7.5% rebate", 0.075),
        ("$25,000", None),
        ("0.2", None),         # no % sign → not a percentage
        ("", None),
        (None, None),
    ],
)
def test_parse_percent(text, expected):
    assert parse_percent(text) == expected


# ── parse_amount ─────────────────────────────────────────────────────────────


@pytest.mark.offline
@pytest.mark.parametrize(
    "text,expected",
    [
        ("$25,000", 25000.0),
        ("€8k", 8000.0),
        ("1.2M", 1_200_000.0),
        ("8,200", 8200.0),
        ("£1,234.56", 1234.56),
        ("3B in revenue", 3_000_000_000.0),
        ("20% discount", None),     # percent only → not an amount
        ("valid for 5 days", None), # bare small int → not money-like
        ("no numbers here", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_amount(text, expected):
    assert parse_amount(text) == expected


@pytest.mark.offline
def test_amount_ignores_percent_digits():
    # "20% discount on $25,000" → percent=0.20, amount=25000 (not 20)
    assert parse_percent("20% discount on $25,000") == 0.20
    assert parse_amount("20% discount on $25,000") == 25000.0


# ── project_decision ─────────────────────────────────────────────────────────


@pytest.fixture
def discount_rc():
    return RelationContext(
        decision_trace="Approved 20% discount; retention risk (Salesforce)",
        quantitative_data="20% discount",
        approved_by="Sarah Chen",
        approved_via="slack",
        valid_until="2026-12-31",
        confidence_score=0.96,
        supporting_sentences=["Approved the 20% discount for MegaCorp"],
    )


@pytest.mark.offline
def test_project_decision_maps_fields(discount_rc):
    p = project_decision("Sarah Chen", "MegaCorp", "GRANTED_APPROVAL", discount_rc,
                         as_of="2026-06-30")
    assert p["src"] == "Sarah Chen"
    assert p["tgt"] == "MegaCorp"
    assert p["relation_type"] == "GRANTED_APPROVAL"
    assert p["approved_via"] == "slack"
    assert p["approved_by"] == "Sarah Chen"
    assert p["percent"] == 0.20
    assert p["amount"] is None              # "20% discount" has no monetary amount
    assert p["confidence"] == 0.96
    assert p["evidence_count"] == 1
    assert p["is_active"] is True           # valid_until 2026-12-31 ≥ 2026-06-30


@pytest.mark.offline
def test_project_decision_is_active_respects_as_of(discount_rc):
    # after valid_until → inactive
    p = project_decision("a", "b", "X", discount_rc, as_of="2027-01-01")
    assert p["is_active"] is False


@pytest.mark.offline
def test_project_decision_handles_none_rc():
    p = project_decision("a", "b", "REL", None)
    assert p["amount"] is None and p["percent"] is None
    assert p["approved_by"] is None
    assert p["confidence"] == 1.0           # RelationContext default
    assert p["is_active"] is True           # no validity window → active


@pytest.mark.offline
def test_project_decision_accepts_dict_and_json(discount_rc):
    from_obj = project_decision("a", "b", "X", discount_rc, as_of="2026-06-30")
    from_dict = project_decision("a", "b", "X", discount_rc.to_dict(), as_of="2026-06-30")
    from_json = project_decision("a", "b", "X", discount_rc.to_json(), as_of="2026-06-30")
    assert from_obj == from_dict == from_json


# ── project_edge convergence (the key property) ──────────────────────────────


@pytest.mark.offline
def test_emit_and_ingestion_paths_yield_identical_params(discount_rc):
    """Both write paths must project to the same params for the same data.

    emit path  → project_decision(src, tgt, relation_type, rc)
    ingest path → project_edge(src, tgt, stored_edge_dict)
    where the stored edge carries keywords + relation_context JSON.
    """
    emit_params = project_decision(
        "Sarah Chen", "MegaCorp", "GRANTED_APPROVAL", discount_rc, as_of="2026-06-30"
    )
    stored_edge = {                      # shape produced at upsert_edge time
        "keywords": "GRANTED_APPROVAL",
        "relation_context": discount_rc.to_json(),
        "description": "...",
        "weight": 1.0,
    }
    ingest_params = project_edge("Sarah Chen", "MegaCorp", stored_edge, as_of="2026-06-30")
    assert emit_params == ingest_params


@pytest.mark.offline
def test_project_edge_missing_context_is_safe():
    p = project_edge("a", "b", {"keywords": "REL"})  # no relation_context
    assert p["relation_type"] == "REL"
    assert p["amount"] is None and p["approved_by"] is None
