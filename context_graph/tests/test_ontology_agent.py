"""Tests for the NL→ontology author (P2 AI-assisted authoring). Fully offline.

Uses a scripted fake LLM (canned JSON replies) so the full draft→build→lint→
dry-run→repair loop runs without a real model.
"""

from __future__ import annotations

import json

import pytest

from context_graph.ontology import (
    ObjectType,
    Ontology,
    OntologyAuthor,
)


class ScriptedLLM:
    """Returns queued replies in order; records prompts."""

    def __init__(self, replies):
        self._replies = [json.dumps(r) if isinstance(r, dict) else r for r in replies]
        self.calls = 0

    async def __call__(self, prompt, system_prompt=None, **kw):
        self.calls += 1
        return self._replies.pop(0) if self._replies else "{}"


GOOD = {
    "ontology": {
        "name": "sales",
        "object_types": [
            {"name": "Person", "description": "a human", "properties": [
                {"name": "email", "kind": "string"}]},
            {"name": "Order", "description": "a deal", "properties": [
                {"name": "value", "kind": "money", "required": True},
                {"name": "status", "kind": "enum", "enum_values": ["open", "won", "lost"]}]},
        ],
        "link_types": [
            {"name": "approved", "source_types": ["Person"], "target_types": ["Order"],
             "cardinality": "1:N", "properties": []},
        ],
    },
    "samples": {
        "entities": [
            {"name": "Sarah", "type": "Person", "email": "s@acme.com"},
            {"name": "Q3 Deal", "type": "Order", "value": "$25,000", "status": "won"},
        ],
        "relations": [{"type": "approved", "source": "Sarah", "target": "Q3 Deal"}],
    },
    "explanation": "People approve Orders.",
}


@pytest.mark.offline
@pytest.mark.asyncio
async def test_generate_valid_first_try():
    llm = ScriptedLLM([GOOD])
    res = await OntologyAuthor(llm).generate("People approve sales orders.")
    assert res.valid and res.attempts == 1
    assert set(o["name"] for o in res.ontology["object_types"]) == {"Person", "Order"}
    assert res.lint == []
    # the model's own samples were dry-run and conform (2 entities + 1 relation)
    assert res.dry_run["ok"] and res.dry_run["conforming"] == 3


@pytest.mark.offline
@pytest.mark.asyncio
async def test_repairs_when_link_references_undefined_type():
    broken = {
        "ontology": {
            "name": "x",
            "object_types": [{"name": "Person", "properties": []}],
            "link_types": [{"name": "approved", "source_types": ["Person"],
                            "target_types": ["Order"], "cardinality": "1:N"}],  # Order undefined
        },
        "samples": {"entities": [], "relations": []},
        "explanation": "broken",
    }
    llm = ScriptedLLM([broken, GOOD])
    res = await OntologyAuthor(llm).generate("people approve orders", max_repairs=1)
    assert res.valid and res.attempts == 2
    # the repair prompt carried the lint error back to the model
    assert any("undefined object type" in e for e in res.errors)


@pytest.mark.offline
@pytest.mark.asyncio
async def test_invalid_property_kind_is_caught():
    bad_kind = {
        "ontology": {"name": "x", "object_types": [
            {"name": "Order", "properties": [{"name": "v", "kind": "dollars"}]}], "link_types": []},
        "samples": {}, "explanation": "",
    }
    res = await OntologyAuthor(ScriptedLLM([bad_kind])).generate("orders", max_repairs=0)
    assert not res.valid
    assert any("did not build" in e for e in res.errors)


@pytest.mark.offline
@pytest.mark.asyncio
async def test_extends_a_base_ontology():
    base = Ontology(name="sales").define_object(ObjectType("Person"))
    add = {
        "ontology": {"name": "sales", "object_types": [
            {"name": "Invoice", "properties": [{"name": "total", "kind": "money"}]}],
            "link_types": []},
        "samples": {"entities": [{"name": "INV-1", "type": "Invoice", "total": "$10"}],
                    "relations": []},
        "explanation": "add invoices",
    }
    res = await OntologyAuthor(ScriptedLLM([add])).generate("add invoices", base=base)
    assert res.valid
    names = {o["name"] for o in res.ontology["object_types"]}
    assert names == {"Person", "Invoice"}   # base type kept, new type merged in


@pytest.mark.offline
@pytest.mark.asyncio
async def test_gives_up_after_repairs_exhausted():
    junk = "not json"
    res = await OntologyAuthor(ScriptedLLM([junk, junk])).generate("x", max_repairs=1)
    assert not res.valid and res.attempts == 2
    assert res.errors
