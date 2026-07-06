"""Tests for the Context Graph action layer (P3).

Covers:
- ActionParam / ActionHandler / ActionDefinition schema + arg validation
- ActionCatalog round-trip and lint
- ActionStore (in-memory) save/version/load/delete
- Handler SSRF guard (_assert_public_url)
- ActionService.invoke: PASS/FLAG, REJECT (RuleViolation), no-gate, unknown
  action, invalid args, and blocked-webhook handling
"""
from __future__ import annotations

import asyncio

import pytest

from context_graph.actions import (
    ActionCatalog,
    ActionDefinition,
    ActionHandler,
    ActionParam,
    ActionService,
    InMemoryActionStore,
    HandlerError,
)
from context_graph.actions.handler import _assert_public_url
from context_graph.rules.gate import GateDecision, RuleViolation


# ─────────────────────────────────────────────────────────────────────────────
# Schema
# ─────────────────────────────────────────────────────────────────────────────


class TestSchema:
    def test_param_bad_kind_rejected(self):
        with pytest.raises(ValueError):
            ActionParam("x", kind="not-a-kind")

    def test_handler_webhook_requires_url(self):
        with pytest.raises(ValueError):
            ActionHandler(kind="webhook")
        with pytest.raises(ValueError):
            ActionHandler(kind="bogus")
        ActionHandler(kind="webhook", url="https://example.com/hook")  # ok

    def test_edge_relation_defaults_to_upper_name(self):
        assert ActionDefinition("CancelShipment").edge_relation == "CANCELSHIPMENT"
        assert ActionDefinition("x", relation_type="CANCELLED").edge_relation == "CANCELLED"

    def test_validate_args_coerces_and_reports(self):
        a = (ActionDefinition("ApproveOrder")
             .add(ActionParam("discount", kind="percent", required=True))
             .add(ActionParam("amount", kind="money"))
             .add(ActionParam("channel", kind="enum", enum_values=["slack", "email"])))
        coerced, errors = a.validate_args({"discount": "20%", "amount": "$25,000", "channel": "slack"})
        assert coerced == {"discount": 0.20, "amount": 25000.0, "channel": "slack"}
        assert errors == []

    def test_validate_args_missing_required_and_bad_value(self):
        a = (ActionDefinition("ApproveOrder")
             .add(ActionParam("discount", kind="percent", required=True))
             .add(ActionParam("amount", kind="money")))
        coerced, errors = a.validate_args({"amount": "not money"})
        assert coerced == {}
        assert any("missing required argument 'discount'" in e for e in errors)
        assert any(e.startswith("amount:") for e in errors)

    def test_enum_and_range_violations(self):
        a = (ActionDefinition("Set")
             .add(ActionParam("tier", kind="enum", enum_values=["a", "b"]))
             .add(ActionParam("score", kind="integer", minimum=0, maximum=10)))
        _, errors = a.validate_args({"tier": "z", "score": 99})
        assert len(errors) == 2

    def test_catalog_roundtrip(self):
        cat = ActionCatalog(name="sales").define(
            ActionDefinition("ApproveOrder", object_type="Order", relation_type="APPROVED",
                             effect="approval", policy_ref="DiscountPolicy")
            .add(ActionParam("discount", kind="percent", required=True)))
        again = ActionCatalog.from_dict(cat.to_dict())
        assert again.to_dict() == cat.to_dict()
        assert again.get("ApproveOrder").edge_relation == "APPROVED"
        assert again.lint() == []


# ─────────────────────────────────────────────────────────────────────────────
# Store
# ─────────────────────────────────────────────────────────────────────────────


class TestStore:
    def _cat(self):
        return ActionCatalog(name="sales").define(ActionDefinition("Approve"))

    def test_save_bumps_version_and_roundtrips(self):
        store = InMemoryActionStore()
        assert store.load("ws") is None
        v1 = store.save("ws", self._cat())
        assert v1.version == 1
        v2 = store.save("ws", self._cat())
        assert v2.version == 2
        loaded = store.load("ws")
        assert loaded.version == 2
        assert "Approve" in loaded.actions

    def test_delete(self):
        store = InMemoryActionStore()
        store.save("ws", self._cat())
        assert store.delete("ws") is True
        assert store.load("ws") is None
        assert store.delete("ws") is False


# ─────────────────────────────────────────────────────────────────────────────
# SSRF guard
# ─────────────────────────────────────────────────────────────────────────────


class TestSsrfGuard:
    def test_rejects_non_http_scheme(self):
        with pytest.raises(HandlerError):
            _assert_public_url("ftp://example.com/x", allow_internal=False)

    def test_rejects_loopback_and_private(self):
        with pytest.raises(HandlerError):
            _assert_public_url("http://127.0.0.1/hook", allow_internal=False)
        with pytest.raises(HandlerError):
            _assert_public_url("http://10.0.0.5/hook", allow_internal=False)

    def test_allow_internal_bypasses(self):
        _assert_public_url("http://127.0.0.1/hook", allow_internal=True)  # no raise

    def test_public_ip_allowed(self):
        _assert_public_url("http://8.8.8.8/hook", allow_internal=False)  # no raise


# ─────────────────────────────────────────────────────────────────────────────
# Service.invoke  (fake rag standing in for ContextGraph.emit_decision_trace)
# ─────────────────────────────────────────────────────────────────────────────


class _FakeGate:
    def __init__(self, outcome):
        self.outcome = outcome
        self.audit = {"outcome": outcome, "rule": "r1"}


class _FakeRag:
    """Records emit_decision_trace calls and returns a configurable outcome."""

    def __init__(self, outcome="PASS", reject=False):
        self.rules_gate = object()
        self._outcome = outcome
        self._reject = reject
        self.calls = []

    async def emit_decision_trace(self, src, tgt, relation_type, rc, upsert=True):
        self.calls.append((src, tgt, relation_type, rc))
        if self._reject:
            gd = GateDecision(outcome="REJECT", audit={"reason": "blocked", "rule": "r1"}, result=None)
            raise RuleViolation(gd)
        return _FakeGate(self._outcome) if self._outcome is not None else None


def _svc_with_action(handler=None):
    store = InMemoryActionStore()
    action = ActionDefinition("ApproveOrder", object_type="Order", relation_type="APPROVED",
                              effect="approval")
    action.add(ActionParam("discount", kind="percent", required=True))
    if handler is not None:
        action.handler = handler
    store.save("sales", ActionCatalog(name="sales").define(action))
    return ActionService(store)


class TestInvoke:
    """Driven via asyncio.run so they execute without the pytest-asyncio plugin."""

    def test_flag_outcome_records_and_reports(self):
        svc = _svc_with_action()
        rag = _FakeRag(outcome="FLAG")
        res = asyncio.run(svc.invoke(rag, "sales", "ApproveOrder", actor="Sarah",
                                     object_ref="Order#123", args={"discount": "20%"}))
        assert res["ok"] is True
        assert res["outcome"] == "FLAG" and res["flagged"] is True
        assert res["edge"] == {"src": "Sarah", "tgt": "Order#123", "relation_type": "APPROVED"}
        assert res["coerced"] == {"discount": 0.20}
        # rc carries parseable quantitative_data + action provenance
        rc = rag.calls[0][3]
        assert rc.provenance == "action:ApproveOrder"
        assert "20%" in rc.quantitative_data
        assert res["handler"] == {"kind": "none", "executed": False}

    def test_no_gate_is_recorded(self):
        svc = _svc_with_action()
        rag = _FakeRag(outcome=None)  # emit returns None (no gate attached)
        res = asyncio.run(svc.invoke(rag, "sales", "ApproveOrder", object_ref="Order#1",
                                     args={"discount": "5%"}))
        assert res["ok"] is True and res["outcome"] == "RECORDED"

    def test_reject_does_not_run_handler(self):
        # webhook to a public IP would "execute" — but a REJECT must short-circuit first
        svc = _svc_with_action(ActionHandler(kind="webhook", url="http://8.8.8.8/hook"))
        rag = _FakeRag(reject=True)
        res = asyncio.run(svc.invoke(rag, "sales", "ApproveOrder", object_ref="Order#9",
                                     args={"discount": "50%"}))
        assert res["ok"] is False and res["outcome"] == "REJECT"
        assert res["audit"]["reason"] == "blocked"
        assert "handler" not in res  # side effect never attempted

    def test_unknown_action(self):
        svc = _svc_with_action()
        res = asyncio.run(svc.invoke(_FakeRag(), "sales", "Nope", object_ref="x"))
        assert res["ok"] is False and res["error"] == "unknown_action"

    def test_invalid_arguments(self):
        svc = _svc_with_action()
        res = asyncio.run(svc.invoke(_FakeRag(), "sales", "ApproveOrder", object_ref="x", args={}))
        assert res["ok"] is False and res["error"] == "invalid_arguments"
        assert any("discount" in e for e in res["errors"])

    def test_blocked_webhook_reported_not_raised(self):
        # PASS gate, but the webhook targets loopback → handler blocked, invoke still ok
        svc = _svc_with_action(ActionHandler(kind="webhook", url="http://127.0.0.1/hook"))
        rag = _FakeRag(outcome="PASS")
        res = asyncio.run(svc.invoke(rag, "sales", "ApproveOrder", object_ref="Order#7",
                                     args={"discount": "10%"}))
        assert res["ok"] is True and res["outcome"] == "PASS"
        assert res["handler"]["executed"] is False
        assert "error" in res["handler"]


class TestTransitionSerialization:
    """D5: a transition invoke serializes its read-validate-apply under the
    per-object lock so concurrent invokes can't race the state machine."""

    def _transition_svc(self):
        from context_graph.actions.schema import ActionTransition

        store = InMemoryActionStore()
        action = ActionDefinition("MoveOrder", object_type="Order",
                                  relation_type="MOVED", effect="transition")
        action.add(ActionParam("to", kind="text", required=True))
        action.transition = ActionTransition(to_param="to")
        store.save("sales", ActionCatalog(name="sales").define(action))
        return ActionService(store)

    def test_transition_invoke_acquires_object_lock(self):
        entered = []

        class _SpyLock:
            async def __aenter__(self):
                entered.append(True)
                return self

            async def __aexit__(self, *a):
                return False

        class _FakeMachine:
            def can(self, frm, to, role):
                from context_graph.lifecycle.schema import Decision
                return Decision(allowed=True, reason="")

        class _FakeLifecycle:
            def __init__(self):
                self.applied = []

            def machine_for(self, ws, obj_type):
                return _FakeMachine()

            async def current_state(self, rag, ref, machine):
                return "open"

            async def apply(self, rag, ref, machine, to):
                self.applied.append((ref, to))

        svc = self._transition_svc()
        svc._object_lock = lambda ws, ref: _SpyLock()  # spy on lock acquisition
        lc = _FakeLifecycle()
        res = asyncio.run(svc.invoke(
            _FakeRag(outcome="PASS"), "sales", "MoveOrder",
            object_ref="Order#1", args={"to": "closed"},
            lifecycle=lc, principal_role="manager",
        ))
        assert res["ok"] is True
        assert entered == [True]            # the object lock WAS acquired
        assert lc.applied == [("Order#1", "closed")]  # transition applied inside it


# ─────────────────────────────────────────────────────────────────────────────
# HTTP layer  (real router via FastAPI TestClient; fake rag)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture()
def client_and_rag():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from lightrag.api.routers.actions_routes import create_actions_routes

    rag = _FakeRag(outcome="PASS")
    service = ActionService(InMemoryActionStore())
    app = FastAPI()
    app.include_router(create_actions_routes(
        rag, service, api_key=None, workspace_resolver=lambda: "sales"))
    return TestClient(app), rag


_CATALOG = {
    "name": "sales",
    "actions": [{
        "name": "ApproveOrder", "object_type": "Order", "relation_type": "APPROVED",
        "effect": "approval",
        "params": [{"name": "discount", "kind": "percent", "required": True}],
        "handler": {"kind": "none"},
    }],
}


class TestApi:
    def test_empty_then_save_then_summary(self, client_and_rag):
        client, _ = client_and_rag
        assert client.get("/actions").json()["exists"] is False
        r = client.post("/actions", json={"catalog": _CATALOG})
        assert r.status_code == 200
        body = r.json()
        assert body["exists"] and body["version"] == 1
        assert body["actions"][0]["name"] == "ApproveOrder"

    def test_bad_catalog_is_400(self, client_and_rag):
        client, _ = client_and_rag
        bad = {"name": "x", "actions": [{"name": "y",
               "params": [{"name": "p", "kind": "not-a-kind"}]}]}
        assert client.post("/actions", json={"catalog": bad}).status_code == 400

    def test_get_one_and_404(self, client_and_rag):
        client, _ = client_and_rag
        client.post("/actions", json={"catalog": _CATALOG})
        assert client.get("/actions/ApproveOrder").json()["relation_type"] == "APPROVED"
        assert client.get("/actions/Nope").status_code == 404

    def test_invoke_ok(self, client_and_rag):
        client, rag = client_and_rag
        client.post("/actions", json={"catalog": _CATALOG})
        r = client.post("/actions/invoke", json={
            "action": "ApproveOrder", "actor": "Sarah", "object_ref": "Order#1",
            "args": {"discount": "20%"}})
        assert r.status_code == 200
        assert r.json()["outcome"] == "PASS"
        assert rag.calls  # emit_decision_trace was called

    def test_invoke_unknown_404_and_bad_args_400(self, client_and_rag):
        client, _ = client_and_rag
        client.post("/actions", json={"catalog": _CATALOG})
        assert client.post("/actions/invoke", json={
            "action": "Nope", "object_ref": "x"}).status_code == 404
        assert client.post("/actions/invoke", json={
            "action": "ApproveOrder", "object_ref": "x", "args": {}}).status_code == 400

    def test_invoke_reject_is_422(self, client_and_rag):
        client, rag = client_and_rag
        client.post("/actions", json={"catalog": _CATALOG})
        rag._reject = True
        r = client.post("/actions/invoke", json={
            "action": "ApproveOrder", "object_ref": "x", "args": {"discount": "50%"}})
        assert r.status_code == 422

    def test_delete(self, client_and_rag):
        client, _ = client_and_rag
        client.post("/actions", json={"catalog": _CATALOG})
        assert client.request("DELETE", "/actions").json()["deleted"] is True
        assert client.get("/actions").json()["exists"] is False

    def test_503_without_cg_mode(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from lightrag.api.routers.actions_routes import create_actions_routes

        class PlainRag:  # no rules_gate attribute → not CG mode
            pass

        app = FastAPI()
        app.include_router(create_actions_routes(
            PlainRag(), ActionService(InMemoryActionStore()),
            api_key=None, workspace_resolver=lambda: "sales"))
        assert TestClient(app).get("/actions").status_code == 503
