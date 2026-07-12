"""P0 — schema round-trips + lint for the new platform dataclasses.

FlowDefinition/Run, AppBundle, ArtifactDiff, AgentSpec, Event. Offline.
"""

from __future__ import annotations

import pytest

from context_graph.actions import AgentSpec
from context_graph.apps import AppBundle
from context_graph.events import Event
from context_graph.flows import FlowDefinition, FlowEdge, FlowNode, Run
from context_graph.studio import ArtifactDiff


@pytest.mark.offline
def test_event_round_trip_and_dedupe_key():
    e = Event(type="request.submitted", payload={"a": 1}, source="wh",
              idempotency_key="k", workspace="acme")
    assert Event.from_dict(e.to_dict()).to_dict() == e.to_dict()
    assert e.dedupe_key() == "k"
    # fallback hash is stable + payload-sensitive
    a = Event(type="t", payload={"x": 1})
    b = Event(type="t", payload={"x": 1})
    c = Event(type="t", payload={"x": 2})
    assert a.dedupe_key() == b.dedupe_key() != c.dedupe_key()


@pytest.mark.offline
def test_flow_definition_lint_ok_and_round_trip():
    flow = FlowDefinition(
        id="intake", version=1, on_event="request.submitted",
        nodes=[
            FlowNode("start", "event"),
            FlowNode("decide", "gateway", ref="discount_rule"),
            FlowNode("vp", "task", ref="request_signoff"),
            FlowNode("auto", "task", ref="auto_approve"),
            FlowNode("booked", "state", ref="booked"),
        ],
        edges=[
            FlowEdge("start", "decide"),
            FlowEdge("decide", "vp", when="exceeds"),
            FlowEdge("decide", "auto", when="else"),
            FlowEdge("vp", "booked"),
            FlowEdge("auto", "booked"),
        ],
    )
    assert flow.lint() == []
    assert FlowDefinition.from_dict(flow.to_dict()).to_dict() == flow.to_dict()
    assert flow.entry().id == "start"


@pytest.mark.offline
def test_flow_lint_catches_bad_graphs():
    # no event node, dangling edge, gateway branch without a label
    bad = FlowDefinition(
        id="x", on_event="",
        nodes=[FlowNode("g", "gateway")],
        edges=[FlowEdge("g", "nowhere"), FlowEdge("g", "g")],
    )
    problems = bad.lint()
    assert any("event node" in p for p in problems)
    assert any("on_event" in p for p in problems)
    assert any("not a node" in p for p in problems)
    assert any("when" in p for p in problems)


@pytest.mark.offline
def test_run_round_trip():
    r = Run(run_id="r", app_id="a", flow_id="f", flow_version=3, vars={"n": 1})
    assert Run.from_dict(r.to_dict()).to_dict() == r.to_dict()


@pytest.mark.offline
def test_app_bundle_lint_and_signed():
    ab = AppBundle(domain="acme", project="ops", app_id="intake", flow_ids=["intake"])
    assert ab.lint() == []
    assert ab.signed is False
    ab.signed_by, ab.signed_at = "alice", "2026-07-12T00:00:00"
    assert ab.signed is True
    assert AppBundle.from_dict(ab.to_dict()).to_dict() == ab.to_dict()
    assert "at least one flow" in " ".join(AppBundle(domain="d", project="p", app_id="a").lint())


@pytest.mark.offline
def test_artifact_diff_and_agent_spec():
    d = ArtifactDiff(kind="rule", artifact_id="r1", to_version=2, origin="authoring")
    assert d.lint() == []
    assert ArtifactDiff.from_dict(d.to_dict()).to_dict() == d.to_dict()
    assert "unknown kind" in " ".join(ArtifactDiff(kind="bogus", artifact_id="x", to_version=1).lint())

    spec = AgentSpec(role_prompt="classify", output_schema="Category", min_confidence=0.8)
    assert spec.lint() == []
    assert AgentSpec.from_dict(spec.to_dict()).to_dict() == spec.to_dict()
    assert "min_confidence" in " ".join(AgentSpec(role_prompt="x", output_schema="y", min_confidence=2).lint())
