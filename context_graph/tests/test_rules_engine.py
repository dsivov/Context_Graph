"""Tests for RulesEngine (wiring step 3).

Offline tests use a deterministic FakeBackend so the whole gate — parse,
evaluate, outcome, audit — runs without the model. One integration test
reproduces the §06 worked example with the real model + projection.
"""

from __future__ import annotations

import numpy as np
import pytest

from context_graph.types import RelationContext
from context_graph.rules import (
    ConceptCatalog,
    RulesEngine,
    project_decision,
)


# ── deterministic backend (approval axis / denial axis / other) ──────────────


class FakeBackend:
    model_id = "fake/deterministic"
    dim = 4

    def encode(self, texts):
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, t in enumerate(texts):
            t = t.lower()
            if "approv" in t or "authoriz" in t or "go ahead" in t or "grant" in t:
                out[i, 0] = 1.0
            elif "den" in t or "reject" in t:
                out[i, 1] = 1.0
            else:
                out[i, 2] = 1.0
            out[i, 3] = 0.01
        return out


@pytest.fixture
def catalog():
    return ConceptCatalog(backend=FakeBackend()).define(
        "APPROVAL", ["approved", "authorized", "granted approval", "go ahead"]
    )


def _engine(catalog, dsl):
    return RulesEngine(catalog).load(dsl)


DISCOUNT_RULE = """
rule "large discount needs finance review"  priority 10
when
    sim(relation_type, "APPROVAL") > 0.4
    and percent > 0.15
    and approved_via == "slack"
then
    flag("Discount >15% approved over Slack — route to Finance for review")
end
"""


# ── basic flow ───────────────────────────────────────────────────────────────


@pytest.mark.offline
def test_flag_rule_fires_and_audits(catalog):
    eng = _engine(catalog, DISCOUNT_RULE)
    params = project_decision(
        "Sarah Chen", "MegaCorp", "GRANTED_APPROVAL",
        RelationContext(quantitative_data="20% discount", approved_via="slack"),
        as_of="2026-06-30",
    )
    res = eng.evaluate(params)
    assert res.outcome == "FLAG"
    assert not res.blocked
    audit = res.audit()
    assert audit["outcome"] == "FLAG"
    assert audit["rule"] == "large discount needs finance review"
    assert audit["matched_concept"] == "APPROVAL"
    assert audit["score"] > 0.9
    assert audit["threshold"] == 0.4          # recovered from the condition text
    assert "Finance" in audit["reason"]
    assert audit["model_id"] == "fake/deterministic"


@pytest.mark.offline
def test_no_fire_when_hard_predicate_fails(catalog):
    eng = _engine(catalog, DISCOUNT_RULE)
    # same approval relation, but channel is jira (not slack) → no trigger
    params = project_decision(
        "a", "b", "GRANTED_APPROVAL",
        RelationContext(quantitative_data="20% discount", approved_via="jira"),
    )
    res = eng.evaluate(params)
    assert res.outcome == "PASS"
    assert res.triggered == []


@pytest.mark.offline
def test_no_fire_when_soft_predicate_misses(catalog):
    eng = _engine(catalog, DISCOUNT_RULE)
    # relation is unrelated to APPROVAL → sim low → no trigger even though channel matches
    params = project_decision(
        "a", "b", "PLACES_ORDER",
        RelationContext(quantitative_data="20% discount", approved_via="slack"),
    )
    res = eng.evaluate(params)
    assert res.outcome == "PASS"


# ── outcome severity / precedence ────────────────────────────────────────────


@pytest.mark.offline
def test_reject_outranks_flag(catalog):
    dsl = """
rule "flag big" priority 5
when
    sim(relation_type, "APPROVAL") > 0.4
    and percent > 0.1
then
    flag("big discount")
end

rule "reject huge" priority 10
when
    sim(relation_type, "APPROVAL") > 0.4
    and percent > 0.5
then
    reject("discount over 50% is never allowed")
end
"""
    eng = _engine(catalog, dsl)
    params = project_decision(
        "a", "b", "GRANTED_APPROVAL",
        RelationContext(quantitative_data="60% discount", approved_via="slack"),
    )
    res = eng.evaluate(params)
    assert res.outcome == "REJECT"
    assert res.blocked
    assert len(res.triggered) == 2            # both fired
    assert res.audit()["rule"] == "reject huge"   # decisive = highest severity


# ── robustness: fail-open ────────────────────────────────────────────────────


@pytest.mark.offline
def test_missing_numeric_skips_rule_with_warning(catalog):
    # amount is None ("20% discount" has no money) → `amount > 10000` raises →
    # rule skipped, gate survives (PASS), warning surfaced.
    dsl = """
rule "amount rule"
when
    amount > 10000
then
    reject("too big")
end
"""
    eng = _engine(catalog, dsl)
    params = project_decision("a", "b", "X",
                              RelationContext(quantitative_data="20% discount"))
    assert params["amount"] is None
    res = eng.evaluate(params)
    assert res.outcome == "PASS"
    assert any("amount rule" in w for w in res.warnings)


@pytest.mark.offline
def test_unknown_concept_skips_rule_with_warning(catalog):
    dsl = """
rule "bad concept"
when
    sim(relation_type, "NOPE") > 0.4
then
    flag("x")
end
"""
    eng = _engine(catalog, dsl)
    params = project_decision("a", "b", "GRANTED_APPROVAL", RelationContext())
    res = eng.evaluate(params)
    assert res.outcome == "PASS"
    assert any("bad concept" in w for w in res.warnings)


@pytest.mark.offline
def test_similarity_unavailable_degrades_honestly():
    """When the similarity backend can't run (model2vec missing), the gate reports
    ONE honest 'unavailable' warning instead of leaking the pip-install ImportError
    per-rule as if it were reuse advice, and it does not block (PASS)."""
    from context_graph.rules.similarity import SimilarityUnavailable

    class DownBackend:
        model_id = "down"

        def encode(self, texts):
            raise SimilarityUnavailable("model2vec not installed")

    dsl = """
rule "new module - confirm reuse"  priority 10
when
    sim(relation_type, "MODULE_CREATION") > 0.4
then
    flag("confirm reuse")
end
"""
    cat = ConceptCatalog(backend=DownBackend()).define("MODULE_CREATION", ["x"])
    res = RulesEngine(cat).load(dsl).evaluate(
        project_decision("a", "b", "create module", RelationContext())
    )
    assert res.outcome == "PASS"  # infra failure must not block
    assert any("similarity check unavailable" in w for w in res.warnings)
    assert not any("pip install" in w for w in res.warnings)  # no leaked advice
    assert not any("confirm reuse" in w for w in res.warnings)  # not misdirected


@pytest.mark.offline
def test_empty_condition_rule_is_rejected_at_load(catalog):
    # `when <cond>` on one line → business_rule_engine parses an empty condition;
    # load() must catch this loudly rather than fail at eval.
    bad_dsl = 'rule "oops"\nwhen sim(relation_type, "APPROVAL") > 0.4\nthen\n    flag("x")\nend'
    with pytest.raises(ValueError, match="empty condition"):
        RulesEngine(catalog).load(bad_dsl)


@pytest.mark.offline
def test_notify_does_not_block_or_flag(catalog):
    dsl = """
rule "note it"
when
    sim(relation_type, "APPROVAL") > 0.4
then
    notify("an approval happened")
end
"""
    eng = _engine(catalog, dsl)
    params = project_decision("a", "b", "GRANTED_APPROVAL", RelationContext())
    res = eng.evaluate(params)
    assert res.outcome == "PASS"            # NOTIFY is a side-channel
    assert "an approval happened" in res.notes


# ── workspace isolation (the class-level-functions trap) ─────────────────────


@pytest.mark.offline
def test_two_engines_have_isolated_catalogs():
    cat_a = ConceptCatalog(backend=FakeBackend()).define("APPROVAL", ["approved"])
    cat_b = ConceptCatalog(backend=FakeBackend()).define("DENIAL", ["denied", "rejected"])
    rule_a = 'rule "a"\nwhen\n    sim(relation_type, "APPROVAL") > 0.4\nthen\n    flag("a")\nend'
    rule_b = 'rule "b"\nwhen\n    sim(relation_type, "DENIAL") > 0.4\nthen\n    flag("b")\nend'
    eng_a = _engine(cat_a, rule_a)
    eng_b = _engine(cat_b, rule_b)
    p_app = project_decision("x", "y", "approved", RelationContext())
    p_den = project_decision("x", "y", "rejected", RelationContext())
    # engine A knows APPROVAL only; engine B knows DENIAL only — no clobbering
    assert eng_a.evaluate(p_app).outcome == "FLAG"
    assert eng_a.evaluate(p_den).outcome == "PASS"
    assert eng_b.evaluate(p_den).outcome == "FLAG"
    assert eng_b.evaluate(p_app).outcome == "PASS"


# ── integration: real model, the §06 worked example end to end ───────────────


@pytest.mark.integration
def test_worked_example_end_to_end_real_model():
    cat = ConceptCatalog().define(
        "APPROVAL",
        ["approved", "approval granted", "signed off on", "authorized",
         "gave the go-ahead", "grants approval"],
    )
    eng = RulesEngine(cat).load(DISCOUNT_RULE)
    rc = RelationContext(
        decision_trace="Approved 20% discount; retention risk (Salesforce)",
        quantitative_data="20% discount",
        approved_by="Sarah Chen", approved_via="slack",
        valid_until="2026-12-31", confidence_score=0.96,
    )
    params = project_decision("Sarah Chen", "MegaCorp", "GRANTED_APPROVAL", rc,
                              as_of="2026-06-30")
    res = eng.evaluate(params)
    audit = res.audit()
    assert res.outcome == "FLAG"
    assert audit["matched_concept"] == "APPROVAL"
    assert audit["score"] >= 0.5            # real GRANTED_APPROVAL match
    assert audit["threshold"] == 0.4
    assert audit["model_id"] == "minishlab/potion-retrieval-32M"
