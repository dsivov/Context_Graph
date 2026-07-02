"""Tests for the /workspace/manifest endpoint (P3).

Focus: actions advertised to a role are filtered by that role's RBAC grants.
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from context_graph.actions import (ActionService, InMemoryActionStore,
                                    ActionCatalog, ActionDefinition, ActionParam)
from context_graph.rbac import RbacService, InMemoryRbacStore
from lightrag.api.routers.workspace_routes import create_workspace_routes


class _Graph:
    async def get_node_edges(self, nid):
        return None  # no skills wired in this test


class _Rag:
    def __init__(self):
        self.rules_gate = object()
        self.chunk_entity_relation_graph = _Graph()


def _client(with_policy: bool):
    rag = _Rag()
    astore = InMemoryActionStore()
    astore.save("ws", ActionCatalog(name="t")
                .define(ActionDefinition("ProposeAPI", object_type="API").add(ActionParam("name", required=True)))
                .define(ActionDefinition("MergeToMain", object_type="Feature")))
    actions = ActionService(astore)
    rbac = RbacService(InMemoryRbacStore())
    if with_policy:
        rbac.save("ws", {"roles": {"manager": ["*"], "developer": ["invoke:ProposeAPI"]}})

    app = FastAPI()
    app.include_router(create_workspace_routes(
        rag, action_service=actions, rbac_service=rbac,
        api_key=None, workspace_resolver=lambda: "ws"))
    return TestClient(app)


def _action_names(resp):
    return [a["name"] for a in resp.json()["actions"]]


class TestManifest:
    def test_actions_filtered_by_grants(self):
        client = _client(with_policy=True)
        assert _action_names(client.get("/workspace/manifest?role=developer")) == ["ProposeAPI"]
        assert set(_action_names(client.get("/workspace/manifest?role=manager"))) == {"ProposeAPI", "MergeToMain"}

    def test_no_role_shows_all(self):
        client = _client(with_policy=True)
        assert set(_action_names(client.get("/workspace/manifest"))) == {"ProposeAPI", "MergeToMain"}

    def test_no_policy_permissive(self):
        client = _client(with_policy=False)
        assert set(_action_names(client.get("/workspace/manifest?role=developer"))) == {"ProposeAPI", "MergeToMain"}

    def test_shape_and_mcp(self):
        client = _client(with_policy=True)
        body = client.get("/workspace/manifest?role=developer").json()
        assert body["workspace"] == "ws" and body["role"] == "developer"
        assert body["skills"] == [] and "invoke" in body["mcp"]["tools"]

    def test_503_without_cg_mode(self):
        class Plain:
            pass
        app = FastAPI()
        app.include_router(create_workspace_routes(Plain(), workspace_resolver=lambda: "ws"))
        assert TestClient(app).get("/workspace/manifest").status_code == 503
