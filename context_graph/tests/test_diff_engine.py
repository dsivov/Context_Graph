"""P3 test gate — the Studio DiffEngine: propose → assess → apply + revert.

Asserts:
* propose→assess→apply produces a new persisted version and a ledger entry.
* a cosmetic rule edit (rename + reworded reason) has behaviour_changed=False
  and applies lightweight (no approver required).
* a threshold change has behaviour_changed=True and requires a sign-off;
  the sign-off is recorded as a decision (who/why/when).
* flow / ontology / action structural changes are behavioural; description-only
  changes are cosmetic.
* revert re-applies a prior snapshot as a new signed version.

Offline: fixed clock, a fake rag records the sign-off decision, real
rules/flow/ontology/action services over in-memory stores.
"""

from __future__ import annotations

import pytest

from context_graph.actions import (
    ActionCatalog,
    ActionDefinition,
    ActionParam,
    ActionService,
    InMemoryActionStore,
)
from context_graph.flows import FlowDefinition, FlowEdge, FlowNode, InMemoryFlowStore
from context_graph.ontology import InMemoryOntologyStore, OntologyService
from context_graph.rules import InMemoryRuleStore, RulesService
from context_graph.studio import DiffEngine, InMemoryStudioStore
from context_graph.studio.schema import ArtifactDiff


class _FakeRag:
    def __init__(self):
        self.rules_gate = None
        self.decisions = []

    async def emit_decision_trace(self, src, tgt, relation_type, rc, upsert=True):
        self.decisions.append({"src": src, "tgt": tgt, "rt": relation_type,
                               "reason": rc.decision_trace, "by": rc.approved_by})

        class _GD:
            audit = {"outcome": "PASS", "rule": None}
        return _GD()


def _engine():
    rag = _FakeRag()
    rules = RulesService(InMemoryRuleStore(), gate_backend=None)
    engine = DiffEngine(
        studio_store=InMemoryStudioStore(),
        rules_service=rules,
        ontology_service=OntologyService(InMemoryOntologyStore()),
        flow_store=InMemoryFlowStore(),
        action_service=ActionService(InMemoryActionStore()),
        rag_resolver=lambda ws: rag,
        now=lambda: 1_800_000_000.0,      # fixed clock → deterministic timestamps
    )
    return engine, rag


# numeric-threshold rule (no sim → no model2vec needed); fixtures bracket 20%.
def _rule_draft(threshold: str, reason: str, name: str = "discount_threshold"):
    return {
        "dsl": f'rule "{name}"\nwhen\n    percent > {threshold}\nthen\n    flag("{reason}")\nend\n',
        "concepts": {},
        "enabled": True,
        "fixtures": [
            {"name": "at-25", "expect": "FLAG",
             "decision": {"src": "a", "tgt": "b", "quantitative_data": "discount 25%"}},
            {"name": "at-10", "expect": "PASS",
             "decision": {"src": "a", "tgt": "b", "quantitative_data": "discount 10%"}},
        ],
    }


# ── rules: the core gate ─────────────────────────────────────────────────────


@pytest.mark.offline
@pytest.mark.asyncio
async def test_first_rule_version_is_behavioural_and_persists():
    engine, rag = _engine()
    diff = await engine.propose("w", "rule", "policy", draft=_rule_draft("0.20", "exceeds"))
    engine.assess("w", diff)
    assert diff.behaviour_changed is True          # first version
    assert diff.from_version is None and diff.to_version == 1

    # behavioural → needs sign-off
    with pytest.raises(ValueError, match="sign-off"):
        await engine.apply("w", diff)

    res = await engine.apply("w", diff, approver="Sarah Chen", reason="new discount policy")
    assert res["version"] == 1
    # persisted to the real rules service
    assert engine._rules.store.load("w").dsl.strip().startswith('rule "discount_threshold"')
    # sign-off recorded as a decision
    assert rag.decisions[-1]["by"] == "Sarah Chen"
    assert rag.decisions[-1]["tgt"] == "rule:policy"


@pytest.mark.offline
@pytest.mark.asyncio
async def test_cosmetic_rule_edit_is_lightweight():
    engine, _ = _engine()
    v1 = await engine.propose("w", "rule", "policy", draft=_rule_draft("0.20", "exceeds"))
    engine.assess("w", v1)
    await engine.apply("w", v1, approver="Sarah", reason="initial")

    # rename the rule + reword the reason — no threshold/verb change
    cosmetic = _rule_draft("0.20", "discount is above the limit", name="discount_cap")
    diff = await engine.propose("w", "rule", "policy", draft=cosmetic)
    engine.assess("w", diff)
    assert diff.behaviour_changed is False
    # lightweight: applies with no approver
    res = await engine.apply("w", diff)
    assert res["version"] == 2
    assert res["sign_off"]["approver"] == "system"


@pytest.mark.offline
@pytest.mark.asyncio
async def test_threshold_change_is_behavioural_and_signed():
    engine, rag = _engine()
    v1 = await engine.propose("w", "rule", "policy", draft=_rule_draft("0.20", "exceeds"))
    engine.assess("w", v1)
    await engine.apply("w", v1, approver="Sarah", reason="initial")

    # 0.20 → 0.15 flips the 'at-10'... actually flips inputs in (0.15, 0.20]; the
    # signature (condition text) changes regardless → behavioural.
    diff = await engine.propose("w", "rule", "policy", draft=_rule_draft("0.15", "exceeds"))
    engine.assess("w", diff)
    assert diff.behaviour_changed is True

    with pytest.raises(ValueError):
        await engine.apply("w", diff)                # sign-off required

    res = await engine.apply("w", diff, approver="VP Ops", reason="tighten to 15%")
    assert res["version"] == 2                        # rules store is a ws singleton
    assert res["sign_off"] == {"approver": "VP Ops", "reason": "tighten to 15%",
                               "at": res["sign_off"]["at"], "role": None}
    assert any(d["tgt"] == "rule:policy" and d["by"] == "VP Ops" for d in rag.decisions)


# ── flow / ontology / action structural vs cosmetic ─────────────────────────


def _flow_draft(extra_node=False):
    nodes = [FlowNode("in", "event"), FlowNode("done", "state", ref="done")]
    edges = [FlowEdge("in", "done")]
    if extra_node:
        nodes.insert(1, FlowNode("t", "task", ref="noop"))
        edges = [FlowEdge("in", "t"), FlowEdge("t", "done")]
    return FlowDefinition(id="f", on_event="e", nodes=nodes, edges=edges).to_dict()


@pytest.mark.offline
@pytest.mark.asyncio
async def test_flow_structural_change_is_behavioural():
    engine, _ = _engine()
    v1 = await engine.propose("w", "flow", "f", draft=_flow_draft())
    engine.assess("w", v1)
    await engine.apply("w", v1, approver="A", reason="init")

    same = await engine.propose("w", "flow", "f", draft=_flow_draft())
    engine.assess("w", same)
    assert same.behaviour_changed is False           # identical graph

    changed = await engine.propose("w", "flow", "f", draft=_flow_draft(extra_node=True))
    engine.assess("w", changed)
    assert changed.behaviour_changed is True         # a node/edge was added


def _onto_draft(*, description: str, required: bool):
    from context_graph.ontology.schema import ObjectType, Ontology, Property, PropertyKind

    return (Ontology(name="sales")
            .define_object(ObjectType(name="Deal", description=description)
                           .add(Property(name="amt", kind=PropertyKind.MONEY,
                                         required=required)))).to_dict()


@pytest.mark.offline
@pytest.mark.asyncio
async def test_ontology_description_only_is_cosmetic():
    engine, _ = _engine()
    v1 = await engine.propose("w", "ontology", "onto",
                              draft=_onto_draft(description="a deal", required=True))
    engine.assess("w", v1)
    await engine.apply("w", v1, approver="A", reason="init")

    # reword the description only → cosmetic
    diff = await engine.propose("w", "ontology", "onto",
                                draft=_onto_draft(description="a customer deal", required=True))
    engine.assess("w", diff)
    assert diff.behaviour_changed is False

    # flip a required constraint → behavioural
    diff2 = await engine.propose("w", "ontology", "onto",
                                 draft=_onto_draft(description="a customer deal", required=False))
    engine.assess("w", diff2)
    assert diff2.behaviour_changed is True


@pytest.mark.offline
@pytest.mark.asyncio
async def test_action_param_change_is_behavioural():
    engine, _ = _engine()
    cat_a = ActionCatalog(name="sales").define(
        ActionDefinition("Approve", object_type="Deal", effect="approval")
        .add(ActionParam("discount", kind="percent", required=True))).to_dict()
    v1 = await engine.propose("w", "action", "catalog", draft=cat_a)
    engine.assess("w", v1)
    await engine.apply("w", v1, approver="A", reason="init")

    # only the effect text changes → cosmetic
    cat_b = ActionCatalog(name="sales").define(
        ActionDefinition("Approve", object_type="Deal", effect="grant approval")
        .add(ActionParam("discount", kind="percent", required=True))).to_dict()
    diff = await engine.propose("w", "action", "catalog", draft=cat_b)
    engine.assess("w", diff)
    assert diff.behaviour_changed is False

    # a new required param → behavioural
    cat_c = ActionCatalog(name="sales").define(
        ActionDefinition("Approve", object_type="Deal", effect="grant approval")
        .add(ActionParam("discount", kind="percent", required=True))
        .add(ActionParam("amount", kind="money", required=True))).to_dict()
    diff2 = await engine.propose("w", "action", "catalog", draft=cat_c)
    engine.assess("w", diff2)
    assert diff2.behaviour_changed is True


# ── revert ───────────────────────────────────────────────────────────────────


@pytest.mark.offline
@pytest.mark.asyncio
async def test_revert_reapplies_prior_snapshot():
    engine, _ = _engine()
    v1 = await engine.propose("w", "rule", "policy", draft=_rule_draft("0.20", "exceeds"))
    engine.assess("w", v1)
    await engine.apply("w", v1, approver="A", reason="v1")

    v2 = await engine.propose("w", "rule", "policy", draft=_rule_draft("0.15", "exceeds"))
    engine.assess("w", v2)
    await engine.apply("w", v2, approver="A", reason="v2 tighten")
    assert "0.15" in engine._rules.store.load("w").dsl

    # revert to v1's snapshot → forward-applied as v3, re-signed
    res = await engine.revert("w", "rule", "policy", 1, approver="A", reason="roll back")
    assert res["version"] == 3
    assert res["behaviour_changed"] is True          # 0.15 → 0.20 differs
    assert "0.20" in engine._rules.store.load("w").dsl
    assert res["sign_off"]["reason"] == "roll back"

    history = engine.history("w", "rule", "policy")
    assert [h["version"] for h in history] == [1, 2, 3]
    assert history[-1]["origin"] == "reapproval"
