"""Tests for the per-workspace rule store (wiring step 4). Fully offline."""

from __future__ import annotations

import numpy as np
import pytest

from context_graph.types import RelationContext
from context_graph.rules import (
    InMemoryRuleStore,
    JsonRuleStore,
    RuleBundle,
    referenced_concepts,
    validate_policy,
)


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


CONCEPTS = {"APPROVAL": ["approved", "authorized", "granted approval", "go ahead"]}
DSL = """
rule "large discount needs finance review"  priority 10
when
    sim(relation_type, "APPROVAL") > 0.4
    and percent > 0.15
    and approved_via == "slack"
then
    flag("Discount >15% approved over Slack — route to Finance for review")
end
"""


@pytest.fixture(params=["memory", "json"])
def store(request, tmp_path):
    if request.param == "memory":
        return InMemoryRuleStore(now=lambda: 100.0)
    return JsonRuleStore(str(tmp_path / "rules"), now=lambda: 100.0)


# ── helpers ──────────────────────────────────────────────────────────────────


@pytest.mark.offline
def test_referenced_concepts_extraction():
    assert referenced_concepts('sim(x, "APPROVAL") > 0.4 and similar(y, "REFUND", 0.5)') == {
        "APPROVAL", "REFUND"
    }


@pytest.mark.offline
def test_validate_rejects_empty_condition():
    with pytest.raises(ValueError, match="empty condition"):
        validate_policy('rule "x"\nwhen sim(r, "APPROVAL") > 0.4\nthen\n    flag("y")\nend', CONCEPTS)


@pytest.mark.offline
def test_validate_rejects_undefined_concept():
    with pytest.raises(ValueError, match="undefined concept"):
        validate_policy(DSL, {"NOPE": ["x"]})


@pytest.mark.offline
def test_validate_rejects_unknown_field():
    """D3: a typo'd/unknown decision field is rejected at save time, so the gate
    fails closed instead of silently skipping the rule at eval."""
    typo_dsl = """
rule "block large unapproved spend"  priority 10
when
    amont > 10000
then
    reject("too large")
end
"""
    with pytest.raises(ValueError, match="unknown decision field"):
        validate_policy(typo_dsl, CONCEPTS)


@pytest.mark.offline
def test_validate_accepts_known_fields():
    """A rule over real projected fields (amount, approved_via) validates clean."""
    ok_dsl = """
rule "large slack approval"  priority 10
when
    amount > 10000
    and approved_via == "slack"
then
    flag("review")
end
"""
    validate_policy(ok_dsl, CONCEPTS)  # must not raise


@pytest.mark.offline
def test_referenced_fields_extraction():
    from context_graph.rules.store import referenced_fields

    fields = referenced_fields(
        ['sim(relation_type, "APPROVAL") > 0.4 and percent > 0.15 and approved_via == "slack"']
    )
    assert fields == {"relation_type", "percent", "approved_via"}


# ── save / load / version ────────────────────────────────────────────────────


@pytest.mark.offline
def test_save_and_load_roundtrip(store):
    b = store.save("acme", DSL, CONCEPTS)
    assert isinstance(b, RuleBundle)
    assert b.version == 1 and b.enabled is True
    loaded = store.load("acme")
    assert loaded.dsl == DSL
    assert loaded.concepts == CONCEPTS
    assert loaded.model_id  # pinned
    assert loaded.updated_at == 100.0


@pytest.mark.offline
def test_version_bumps_on_resave(store):
    store.save("acme", DSL, CONCEPTS)
    b2 = store.save("acme", DSL, CONCEPTS)
    assert b2.version == 2


@pytest.mark.offline
def test_load_missing_returns_none(store):
    assert store.load("nope") is None


@pytest.mark.offline
def test_save_rejects_bad_policy_before_persisting(store):
    with pytest.raises(ValueError):
        store.save("acme", DSL, {"NOPE": ["x"]})    # undefined concept
    assert store.load("acme") is None               # nothing persisted


@pytest.mark.offline
def test_delete_and_list(store):
    store.save("acme", DSL, CONCEPTS)
    store.save("globex", DSL, CONCEPTS)
    assert set(store.list_workspaces()) == {"acme", "globex"}
    assert store.delete("acme") is True
    assert store.delete("acme") is False
    assert store.list_workspaces() == ["globex"]


@pytest.mark.offline
def test_set_enabled_toggle(store):
    store.save("acme", DSL, CONCEPTS)
    b = store.set_enabled("acme", False)
    assert b.enabled is False
    assert store.load("acme").enabled is False
    with pytest.raises(KeyError):
        store.set_enabled("missing", False)


# ── build_gate (end-to-end through the store, offline backend) ───────────────


@pytest.mark.offline
def test_build_gate_flags_through_store(store):
    store.save("acme", DSL, CONCEPTS)
    gate = store.build_gate("acme", backend=FakeBackend())
    assert gate is not None
    d = gate.check("Sarah", "MegaCorp", "GRANTED_APPROVAL",
                   RelationContext(quantitative_data="20% discount", approved_via="slack"))
    assert d.outcome == "FLAG"
    assert d.audit["matched_concept"] == "APPROVAL"


@pytest.mark.offline
def test_build_gate_none_when_disabled(store):
    store.save("acme", DSL, CONCEPTS, enabled=False)
    assert store.build_gate("acme", backend=FakeBackend()) is None


@pytest.mark.offline
def test_build_gate_none_when_absent(store):
    assert store.build_gate("ghost", backend=FakeBackend()) is None


# ── JSON persistence survives a new store instance ───────────────────────────


@pytest.mark.offline
def test_json_persists_across_instances(tmp_path):
    base = str(tmp_path / "rules")
    JsonRuleStore(base, now=lambda: 1.0).save("acme", DSL, CONCEPTS)
    # a fresh store instance reads the file written by the first
    reloaded = JsonRuleStore(base).load("acme")
    assert reloaded is not None
    assert reloaded.workspace == "acme"
    assert reloaded.dsl == DSL
