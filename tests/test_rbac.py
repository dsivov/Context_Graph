"""Tests for the Context Graph RBAC layer (P3, Gap 1).

Covers:
- Grant / RoleGrants / RbacPolicy schema (parse, wildcards, deny-by-default)
- RbacStore (in-memory) save/version/load/delete
- RbacService.check — permissive with no policy, deny-default with a policy
- HTTP: /rbac CRUD + /rbac/check, and /actions/invoke gating (403 / permissive)
"""
from __future__ import annotations

import asyncio

import pytest

from context_graph.rbac import (
    Grant,
    RbacPolicy,
    RbacService,
    InMemoryRbacStore,
)


# ─────────────────────────────────────────────────────────────────────────────
# Schema
# ─────────────────────────────────────────────────────────────────────────────


class TestSchema:
    def test_grant_parse_and_match(self):
        assert Grant.parse("*").matches("invoke", "X")
        g = Grant.parse("invoke:ProposeAPI")
        assert g.matches("invoke", "ProposeAPI")
        assert not g.matches("invoke", "MergeToMain")
        assert not g.matches("delete", "ProposeAPI")
        assert Grant.parse("invoke").matches("invoke", "anything")  # target defaults to *

    def test_bad_verb_rejected(self):
        with pytest.raises(ValueError):
            Grant.parse("frobnicate:X")

    def test_policy_check_deny_by_default(self):
        pol = RbacPolicy.from_dict({"roles": {
            "manager": ["*"],
            "developer": ["invoke:ProposeAPI", "invoke:AdvanceTask"],
        }})
        assert pol.check("manager", "invoke", "MergeToMain").allowed
        assert pol.check("developer", "invoke", "ProposeAPI").allowed
        assert not pol.check("developer", "invoke", "MergeToMain").allowed
        assert not pol.check("intern", "invoke", "ProposeAPI").allowed   # unknown role
        assert not pol.check(None, "invoke", "ProposeAPI").allowed       # no role

    def test_lint_and_roundtrip(self):
        assert RbacPolicy().lint() == ["policy defines no roles"]
        pol = RbacPolicy.from_dict({"name": "rbac", "roles": {"manager": ["*"],
                                    "developer": ["invoke:ProposeAPI"]}})
        assert pol.lint() == []
        assert RbacPolicy.from_dict(pol.to_dict()).to_dict() == pol.to_dict()

    def test_rebac_grant_preserved(self):
        pol = RbacPolicy.from_dict({"roles": {"developer": {
            "grants": ["invoke:ProposeAPI"],
            "rebac": [{"verb": "invoke", "target": "DeprecateAPI", "via": "owns", "of": "Module"}]}}})
        rg = pol.roles["developer"]
        assert rg.allows("invoke", "ProposeAPI") and len(rg.rebac) == 1
        # rebac round-trips
        assert RbacPolicy.from_dict(pol.to_dict()).roles["developer"].rebac[0].via == "owns"


# ─────────────────────────────────────────────────────────────────────────────
# Store + service
# ─────────────────────────────────────────────────────────────────────────────


class TestStoreService:
    def _pol(self):
        return {"roles": {"manager": ["*"], "developer": ["invoke:ProposeAPI"]}}

    def test_store_versions(self):
        store = InMemoryRbacStore()
        assert store.load("ws") is None
        assert store.save("ws", RbacPolicy.from_dict(self._pol())).version == 1
        assert store.save("ws", RbacPolicy.from_dict(self._pol())).version == 2
        assert store.delete("ws") is True and store.load("ws") is None

    def test_service_permissive_without_policy(self):
        svc = RbacService(InMemoryRbacStore())
        assert svc.check("ws", "developer", "invoke", "MergeToMain").allowed  # no policy → allow

    def test_service_deny_default_with_policy(self):
        svc = RbacService(InMemoryRbacStore())
        svc.save("ws", self._pol())
        assert svc.check("ws", "manager", "invoke", "MergeToMain").allowed
        assert not svc.check("ws", "developer", "invoke", "MergeToMain").allowed
        assert list(svc.get_summary("ws")["roles"]) == ["manager", "developer"]

    def test_service_save_rejects_empty(self):
        svc = RbacService(InMemoryRbacStore())
        with pytest.raises(ValueError):
            svc.save("ws", {"roles": {}})


# ─────────────────────────────────────────────────────────────────────────────
# HTTP — /rbac + /actions/invoke gating (real routers, fake rag)
# ─────────────────────────────────────────────────────────────────────────────


class _FakeGate:
    def __init__(self, outcome="PASS"):
        self.outcome = outcome
        self.audit = {"outcome": outcome}


class _FakeRag:
    def __init__(self):
        self.rules_gate = object()
        self.invoked = []

    async def emit_decision_trace(self, src, tgt, rt, rc, upsert=True):
        self.invoked.append((src, tgt, rt))
        return _FakeGate("PASS")


def _apps():
    """Build a TestClient with /rbac + /actions wired to a shared RbacService."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from lightrag.api.routers.rbac_routes import create_rbac_routes
    from lightrag.api.routers.actions_routes import create_actions_routes
    from context_graph.actions import ActionService, InMemoryActionStore, ActionCatalog, ActionDefinition, ActionParam

    rag = _FakeRag()
    rbac = RbacService(InMemoryRbacStore())
    astore = InMemoryActionStore()
    astore.save("ws", ActionCatalog(name="t").define(
        ActionDefinition("ProposeAPI", relation_type="proposes a new API")
        .add(ActionParam("name", kind="string", required=True))))
    actions = ActionService(astore)

    app = FastAPI()
    app.include_router(create_rbac_routes(rag, rbac, api_key=None, workspace_resolver=lambda: "ws"))
    app.include_router(create_actions_routes(rag, actions, rbac_service=rbac,
                                             api_key=None, workspace_resolver=lambda: "ws"))
    return TestClient(app), rbac


_POLICY = {"policy": {"name": "rbac", "roles": {
    "manager": ["*"], "developer": ["invoke:ProposeAPI"]}}}


class TestApi:
    def test_crud_and_check(self):
        client, _ = _apps()
        assert client.get("/rbac").json()["exists"] is False
        r = client.post("/rbac", json=_POLICY)
        assert r.status_code == 200 and r.json()["version"] == 1
        assert client.post("/rbac/check", json={"role": "developer", "target": "ProposeAPI"}).json()["allowed"]
        assert not client.post("/rbac/check", json={"role": "developer", "target": "MergeToMain"}).json()["allowed"]
        assert client.request("DELETE", "/rbac").json()["deleted"] is True

    def test_bad_policy_400(self):
        client, _ = _apps()
        assert client.post("/rbac", json={"policy": {"roles": {}}}).status_code == 400

    def test_invoke_permissive_without_policy(self):
        # no RBAC policy installed → invoke passes (unauthenticated is fine)
        client, _ = _apps()
        r = client.post("/actions/invoke", json={"action": "ProposeAPI", "object_ref": "X",
                                                 "args": {"name": "Foo"}})
        assert r.status_code == 200 and r.json()["outcome"] == "PASS"

    def test_invoke_forbidden_when_role_lacks_grant(self):
        # policy present + no authenticated role (no token) → 403
        client, _ = _apps()
        client.post("/rbac", json=_POLICY)
        r = client.post("/actions/invoke", json={"action": "ProposeAPI", "object_ref": "X",
                                                 "args": {"name": "Foo"}})
        assert r.status_code == 403

    def test_503_without_cg_mode(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from lightrag.api.routers.rbac_routes import create_rbac_routes

        class PlainRag:
            pass

        app = FastAPI()
        app.include_router(create_rbac_routes(PlainRag(), RbacService(InMemoryRbacStore()),
                                              api_key=None, workspace_resolver=lambda: "ws"))
        assert TestClient(app).get("/rbac").status_code == 503
