"""P2 test gate — the flow engine walks the golden 'governed request' flow.

The flow (docs/GOVERNED_WORKFLOW_PLATFORM.html §the flow):

    event submitted (on_event: request.submitted)
      → task map        (normalize_to_ontology)
      → gateway decide  (rule discount_threshold: exceeds → vp_review, else → auto)
          exceeds → task vp_review (request_signoff)
          else    → task auto      (auto_approve)
      vp_review ┐
      auto      ┴→ task book (book_deal)
      → state booked   (guarded DealRequest transition submitted → booked)

Asserts:
* a >20% discount routes through ``vp_review``; a ≤20% discount through ``auto``;
  both terminate ``done`` in state ``booked``.
* every hop wrote a quad (the run is reconstructable from the graph alone).
* replay re-walks the recorded history to the identical terminal state + trace.

Offline: a fake rag records ``emit_decision_trace`` and backs the lifecycle node
read/write, standing in for a real ContextGraph.
"""

from __future__ import annotations

import pytest

from context_graph.actions import (
    ActionCatalog,
    ActionDefinition,
    ActionService,
    InMemoryActionStore,
)
from context_graph.events.schema import Event
from context_graph.flows import (
    FlowDefinition,
    FlowEdge,
    FlowExecutor,
    FlowNode,
    InMemoryFlowStore,
    InMemoryRunStore,
)
from context_graph.lifecycle import InMemoryLifecycleStore, LifecycleService
from context_graph.rules.gate import GateDecision, RulesGate
from context_graph.rules.similarity import ConceptCatalog


# ── fakes ────────────────────────────────────────────────────────────────────


class _FakeGraph:
    def __init__(self):
        self.nodes = {}

    async def get_node(self, nid):
        return dict(self.nodes[nid]) if nid in self.nodes else None

    async def upsert_node(self, nid, data):
        self.nodes[nid] = dict(data)


class _FakeRag:
    """Records every emit_decision_trace and backs lifecycle node state."""

    def __init__(self):
        self.rules_gate = object()
        self.chunk_entity_relation_graph = _FakeGraph()
        self.quads = []

    async def emit_decision_trace(self, src, tgt, relation_type, rc, upsert=True):
        self.quads.append((src, tgt, relation_type))
        self.chunk_entity_relation_graph.nodes.setdefault(tgt, {"entity_type": "node"})
        return GateDecision(outcome="PASS", audit={"outcome": "PASS"}, result=None)


class _Rules:
    """Minimal rules_service exposing the one method the gateway calls."""

    def __init__(self, gate):
        self._gate = gate

    def gate_for(self, workspace):
        return self._gate


_DSL = """rule "discount_threshold"
when
    discount > 0.20
then
    notify("exceeds")
"""

_LIFECYCLE = {"machines": {"DealRequest": {
    "states": ["submitted", "booked"],
    "initial": "submitted",
    "transitions": [{"from": "submitted", "to": "booked"}],
}}}


def _actions() -> ActionService:
    store = InMemoryActionStore()
    cat = ActionCatalog(name="sales")
    for name, effect in [
        ("normalize_to_ontology", "normalize"),
        ("request_signoff", "signoff"),
        ("auto_approve", "auto-approve"),
        ("book_deal", "booking"),
    ]:
        cat.define(ActionDefinition(name, object_type="DealRequest", effect=effect))
    store.save("sales", cat)
    return ActionService(store)


def _flow() -> FlowDefinition:
    return FlowDefinition(
        id="intake",
        version=1,
        on_event="request.submitted",
        nodes=[
            FlowNode("submitted", "event"),
            FlowNode("map", "task", ref="normalize_to_ontology",
                     config={"object": "$deal"}),
            FlowNode("decide", "gateway", ref="discount_threshold"),
            FlowNode("vp_review", "task", ref="request_signoff",
                     config={"object": "$deal"}),
            FlowNode("auto", "task", ref="auto_approve",
                     config={"object": "$deal"}),
            FlowNode("book", "task", ref="book_deal",
                     config={"object": "$deal"}),
            FlowNode("booked", "state", ref="booked",
                     config={"object": "$deal", "object_type": "DealRequest"}),
        ],
        edges=[
            FlowEdge("submitted", "map"),
            FlowEdge("map", "decide"),
            FlowEdge("decide", "vp_review", when="exceeds"),
            FlowEdge("decide", "auto", when="else"),
            FlowEdge("vp_review", "book"),
            FlowEdge("auto", "book"),
            FlowEdge("book", "booked"),
        ],
    )


def _executor(rag):
    flows = InMemoryFlowStore()
    flows.save("sales", _flow())
    lifecycle = LifecycleService(InMemoryLifecycleStore())
    lifecycle.save("sales", _LIFECYCLE)
    gate = RulesGate.from_dsl(ConceptCatalog(), _DSL)
    ex = FlowExecutor(
        flows, InMemoryRunStore(),
        rag_resolver=lambda ws: rag,
        rules_service=_Rules(gate),
        action_service=_actions(),
        lifecycle_service=lifecycle,
    )
    return ex, flows


def _event(discount: float) -> Event:
    return Event(
        type="request.submitted",
        payload={"customer": "MegaCorp", "deal": f"Deal-{discount}",
                 "discount": discount, "amount": 100000.0},
        source="test",
        idempotency_key=f"deal-{discount}",
    )


# ── tests ──────────────────────────────────────────────────────────────────


@pytest.mark.offline
@pytest.mark.asyncio
async def test_high_discount_routes_through_vp_review():
    rag = _FakeRag()
    ex, flows = _executor(rag)
    run = await ex.start("sales", flows.get("sales", "intake"), event=_event(0.25))

    assert run.status == "done"
    assert run.state == "booked"
    path = [h["node"] for h in run.history]
    assert path == ["submitted", "map", "decide", "vp_review", "book", "booked"]
    # the gateway recorded the branch it took
    gw = next(h for h in run.history if h["kind"] == "gateway")
    assert gw["detail"]["branch"] == "exceeds"
    # the object reached booked in the graph
    assert rag.chunk_entity_relation_graph.nodes["Deal-0.25"]["state"] == "booked"


@pytest.mark.offline
@pytest.mark.asyncio
async def test_low_discount_routes_through_auto():
    rag = _FakeRag()
    ex, flows = _executor(rag)
    run = await ex.start("sales", flows.get("sales", "intake"), event=_event(0.10))

    assert run.status == "done"
    assert run.state == "booked"
    path = [h["node"] for h in run.history]
    assert path == ["submitted", "map", "decide", "auto", "book", "booked"]
    gw = next(h for h in run.history if h["kind"] == "gateway")
    assert gw["detail"]["branch"] == "else"


@pytest.mark.offline
@pytest.mark.asyncio
async def test_every_hop_wrote_a_quad():
    rag = _FakeRag()
    ex, flows = _executor(rag)
    run = await ex.start("sales", flows.get("sales", "intake"), event=_event(0.25))

    # 6 hops: event, map(task), decide(gateway), vp_review(task), book(task), booked(state).
    # Non-task hops emit a run-scoped flow_step quad; task hops carry the action's quad.
    assert len(run.history) == 6
    hop_quads = [q for q in rag.quads if q[0] == f"run:{run.run_id}"]
    assert {q[2] for q in hop_quads} == {
        "flow_step:event", "flow_step:gateway", "flow_step:state"
    }
    # task hops produced the action's own decision quad (actor -> object)
    action_quads = [q for q in rag.quads if q[0] == "system" and q[1] == "Deal-0.25"]
    assert len(action_quads) == 3        # map, vp_review, book
    assert len(rag.quads) == len(run.history)   # exactly one quad per hop


@pytest.mark.offline
@pytest.mark.asyncio
async def test_replay_reproduces_terminal_state_and_trace():
    rag = _FakeRag()
    ex, flows = _executor(rag)
    for discount in (0.25, 0.10):
        run = await ex.start("sales", flows.get("sales", "intake"), event=_event(discount))
        replay = await ex.replay("sales", run)
        assert replay.ok, replay.mismatches
        assert replay.status == run.status == "done"
        assert replay.state == run.state == "booked"
        # replay walked the same node path (excluding the event's own entry record)
        assert replay.path == [h["node"] for h in run.history]


@pytest.mark.offline
@pytest.mark.asyncio
async def test_halted_run_replays_to_the_same_failure():
    # A task referencing an unknown action halts the run; replay must reproduce
    # the failed terminal, not walk structurally past the halt.
    rag = _FakeRag()
    flows = InMemoryFlowStore()
    flows.save("sales", FlowDefinition(
        id="halt", version=1, on_event="t",
        nodes=[FlowNode("in", "event"),
               FlowNode("bad", "task", ref="does_not_exist"),
               FlowNode("done", "state", ref="done")],
        edges=[FlowEdge("in", "bad"), FlowEdge("bad", "done")],
    ))
    ex = FlowExecutor(
        flows, InMemoryRunStore(), rag_resolver=lambda ws: rag,
        action_service=_actions(),          # present → unknown action halts
    )
    run = await ex.start("sales", flows.get("sales", "halt"),
                         event=Event(type="t", payload={}, idempotency_key="h1"))
    assert run.status == "failed" and run.state is None

    replay = await ex.replay("sales", run)
    assert replay.status == "failed"
    assert replay.state is None
    assert replay.path == ["in", "bad"]     # stopped at the halted task
    assert replay.ok                         # reproduced the run faithfully


@pytest.mark.offline
@pytest.mark.asyncio
async def test_redelivered_event_is_idempotent():
    rag = _FakeRag()
    ex, flows = _executor(rag)
    flow = flows.get("sales", "intake")
    first = await ex.start("sales", flow, event=_event(0.25))
    quads_after_first = len(rag.quads)
    again = await ex.start("sales", flow, event=_event(0.25))

    assert again.run_id == first.run_id
    assert len(rag.quads) == quads_after_first     # no second walk, no new quads
