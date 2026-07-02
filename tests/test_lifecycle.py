"""Tests for the Context Graph lifecycle layer (P3, Gap 2).

Covers:
- StateMachine / Lifecycle schema (legal/illegal transitions, role gate, lint)
- LifecycleStore (in-memory) versioning
- LifecycleService: permissive with no machine; current_state default; apply
- HTTP: /lifecycle CRUD + /lifecycle/check, and /actions/invoke transition gating
  (409 on an illegal jump, 200 + state applied on a legal one)
"""
from __future__ import annotations

import asyncio

import pytest

from context_graph.lifecycle import (
    Lifecycle,
    LifecycleService,
    InMemoryLifecycleStore,
)

_LC = {"machines": {"Task": {
    "states": ["proposed", "active", "blocked", "done"], "initial": "proposed",
    "transitions": [
        {"from": "proposed", "to": "active"},
        {"from": "active", "to": "blocked"},
        {"from": "blocked", "to": "active"},
        {"from": "active", "to": "done", "roles": ["integrator", "manager"]}]}}}


# ─────────────────────────────────────────────────────────────────────────────
# Schema
# ─────────────────────────────────────────────────────────────────────────────


class TestSchema:
    def test_legal_illegal_and_roles(self):
        lc = Lifecycle.from_dict(_LC)
        assert lc.check("Task", "proposed", "active").allowed
        assert not lc.check("Task", "proposed", "done").allowed          # undeclared jump
        assert not lc.check("Task", "active", "nope").allowed            # unknown state
        assert not lc.check("Task", "active", "done", role="developer").allowed  # role gate
        assert lc.check("Task", "active", "done", role="integrator").allowed
        assert lc.check("Widget", "x", "y").allowed                      # no machine → permissive

    def test_lint(self):
        assert Lifecycle().lint() == ["lifecycle defines no state machines"]
        bad = Lifecycle.from_dict({"machines": {"Task": {
            "states": ["a"], "initial": "z", "transitions": [{"from": "a", "to": "b"}]}}})
        problems = bad.lint()
        assert any("initial" in p for p in problems)
        assert any("unknown state 'b'" in p for p in problems)

    def test_roundtrip(self):
        lc = Lifecycle.from_dict(_LC)
        assert Lifecycle.from_dict(lc.to_dict()).to_dict() == lc.to_dict()


# ─────────────────────────────────────────────────────────────────────────────
# Store + service
# ─────────────────────────────────────────────────────────────────────────────


class _FakeGraph:
    """Minimal graph stub with node read/write for current_state/apply."""

    def __init__(self):
        self.nodes = {}

    async def get_node(self, nid):
        return dict(self.nodes[nid]) if nid in self.nodes else None

    async def upsert_node(self, nid, data):
        self.nodes[nid] = dict(data)


class _FakeRag:
    def __init__(self):
        self.chunk_entity_relation_graph = _FakeGraph()


class TestStoreService:
    def test_store_versions(self):
        store = InMemoryLifecycleStore()
        assert store.load("ws") is None
        assert store.save("ws", Lifecycle.from_dict(_LC)).version == 1
        assert store.save("ws", Lifecycle.from_dict(_LC)).version == 2
        assert store.delete("ws") is True and store.load("ws") is None

    def test_permissive_without_lifecycle(self):
        svc = LifecycleService(InMemoryLifecycleStore())
        assert svc.check("ws", "Task", "proposed", "done").allowed  # no lifecycle → allow

    def test_current_state_and_apply(self):
        svc = LifecycleService(InMemoryLifecycleStore())
        svc.save("ws", _LC)
        m = svc.machine_for("ws", "Task")
        rag = _FakeRag()
        # absent node → initial
        assert asyncio.run(svc.current_state(rag, "T-1", m)) == "proposed"
        # apply writes the state; a later read returns it (other props preserved)
        rag.chunk_entity_relation_graph.nodes["T-1"] = {"entity_type": "Task"}
        asyncio.run(svc.apply(rag, "T-1", m, "active"))
        assert rag.chunk_entity_relation_graph.nodes["T-1"] == {"entity_type": "Task", "state": "active"}
        assert asyncio.run(svc.current_state(rag, "T-1", m)) == "active"

    def test_save_rejects_empty(self):
        with pytest.raises(ValueError):
            LifecycleService(InMemoryLifecycleStore()).save("ws", {"machines": {}})


# ─────────────────────────────────────────────────────────────────────────────
# HTTP — /lifecycle + /actions/invoke transition gating
# ─────────────────────────────────────────────────────────────────────────────


class _GateRag:
    def __init__(self):
        self.rules_gate = object()
        self.chunk_entity_relation_graph = _FakeGraph()

    async def emit_decision_trace(self, src, tgt, rt, rc, upsert=True):
        # emulate node creation on emit
        cg = self.chunk_entity_relation_graph
        cg.nodes.setdefault(tgt, {"entity_type": "Task"})
        class _GD:
            outcome = "PASS"; audit = {"outcome": "PASS"}
        return _GD()


def _apps():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from lightrag.api.routers.lifecycle_routes import create_lifecycle_routes
    from lightrag.api.routers.actions_routes import create_actions_routes
    from context_graph.actions import (ActionService, InMemoryActionStore,
                                        ActionCatalog, ActionDefinition, ActionParam)
    from context_graph.actions.schema import ActionTransition

    rag = _GateRag()
    lifecycle = LifecycleService(InMemoryLifecycleStore())
    astore = InMemoryActionStore()
    adv = ActionDefinition("AdvanceTask", object_type="Task", relation_type="advances task state")
    adv.add(ActionParam("to", kind="enum", required=True,
                        enum_values=["proposed", "active", "blocked", "done"]))
    adv.transition = ActionTransition(to_param="to")
    astore.save("ws", ActionCatalog(name="t").define(adv))
    actions = ActionService(astore)

    app = FastAPI()
    app.include_router(create_lifecycle_routes(rag, lifecycle, api_key=None,
                                               workspace_resolver=lambda: "ws"))
    app.include_router(create_actions_routes(rag, actions, lifecycle_service=lifecycle,
                                             api_key=None, workspace_resolver=lambda: "ws"))
    return TestClient(app), rag


class TestApi:
    def test_crud_and_check(self):
        client, _ = _apps()
        assert client.get("/lifecycle").json()["exists"] is False
        assert client.post("/lifecycle", json={"lifecycle": _LC}).status_code == 200
        ok = client.post("/lifecycle/check", json={"object_type": "Task", "from": "proposed", "to": "active"})
        assert ok.json()["allowed"]
        bad = client.post("/lifecycle/check", json={"object_type": "Task", "from": "proposed", "to": "done"})
        assert not bad.json()["allowed"]
        assert client.request("DELETE", "/lifecycle").json()["deleted"] is True

    def test_bad_lifecycle_400(self):
        client, _ = _apps()
        assert client.post("/lifecycle", json={"lifecycle": {"machines": {}}}).status_code == 400

    def test_invoke_permissive_without_lifecycle(self):
        client, _ = _apps()
        r = client.post("/actions/invoke", json={"action": "AdvanceTask", "object_ref": "T-1",
                                                 "args": {"to": "done"}})
        assert r.status_code == 200  # no lifecycle installed → any transition allowed

    def test_invoke_illegal_transition_409(self):
        client, _ = _apps()
        client.post("/lifecycle", json={"lifecycle": _LC})
        # brand-new task (state → initial "proposed"); proposed→done is undeclared
        r = client.post("/actions/invoke", json={"action": "AdvanceTask", "object_ref": "T-9",
                                                 "args": {"to": "done"}})
        assert r.status_code == 409

    def test_invoke_legal_transition_applies_state(self):
        client, rag = _apps()
        client.post("/lifecycle", json={"lifecycle": _LC})
        r = client.post("/actions/invoke", json={"action": "AdvanceTask", "object_ref": "T-2",
                                                 "args": {"to": "active"}})
        assert r.status_code == 200
        body = r.json()
        assert body["from"] == "proposed" and body["to"] == "active"
        assert rag.chunk_entity_relation_graph.nodes["T-2"]["state"] == "active"
