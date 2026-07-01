"""Tests for RulesService and the /rules router (wiring step 7). Offline."""

from __future__ import annotations

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lightrag.context_graph_types import RelationContext
from context_graph.rules import InMemoryRuleStore, RulesService
from lightrag.api.routers.rules_routes import create_rules_routes


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


def _service():
    return RulesService(InMemoryRuleStore(now=lambda: 1.0), gate_backend=FakeBackend())


# ── service unit ─────────────────────────────────────────────────────────────


@pytest.mark.offline
def test_service_summary_and_save():
    svc = _service()
    assert svc.get_summary("acme")["exists"] is False
    svc.save("acme", DSL, CONCEPTS)
    summary = svc.get_summary("acme")
    assert summary["exists"] and summary["enabled"]
    assert summary["concepts"] == ["APPROVAL"]
    assert summary["rules"][0]["name"] == "large discount needs finance review"
    assert summary["rules"][0]["priority"] == 10


@pytest.mark.offline
def test_service_gate_cache_rebuilds_on_version_change():
    svc = _service()
    svc.save("acme", DSL, CONCEPTS)
    g1 = svc.gate_for("acme")
    assert svc.gate_for("acme") is g1            # cached
    svc.save("acme", DSL, CONCEPTS)              # version bump → cache invalidated
    assert svc.gate_for("acme") is not g1


@pytest.mark.offline
def test_service_evaluate_and_disabled():
    svc = _service()
    svc.save("acme", DSL, CONCEPTS)
    rc = RelationContext(quantitative_data="20% discount", approved_via="slack")
    d = svc.evaluate("acme", "S", "M", "GRANTED_APPROVAL", rc)
    assert d.outcome == "FLAG"
    svc.set_enabled("acme", False)
    assert svc.evaluate("acme", "S", "M", "GRANTED_APPROVAL", rc) is None


# ── router via TestClient ────────────────────────────────────────────────────


class FakeRag:
    """Stand-in for a workspace ContextGraph instance — just needs rules_gate."""

    def __init__(self):
        self.rules_gate = None


@pytest.fixture
def client_and_rag():
    svc = _service()
    rag = FakeRag()
    app = FastAPI()
    app.include_router(
        create_rules_routes(rag, svc, api_key=None, workspace_resolver=lambda: "acme")
    )
    return TestClient(app), rag


@pytest.mark.offline
def test_api_lifecycle(client_and_rag):
    client, rag = client_and_rag

    # empty
    r = client.get("/rules")
    assert r.status_code == 200 and r.json()["exists"] is False

    # set policy → gate attached to the rag instance
    r = client.post("/rules", json={"dsl": DSL, "concepts": CONCEPTS})
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is True and body["rules"][0]["priority"] == 10
    assert rag.rules_gate is not None             # live enforcement attached

    # evaluate (FLAG)
    r = client.post("/rules/evaluate", json={
        "src": "Sarah", "tgt": "MegaCorp", "relation_type": "GRANTED_APPROVAL",
        "relation_context": {"quantitative_data": "20% discount", "approved_via": "slack"},
    })
    assert r.status_code == 200
    ev = r.json()
    assert ev["active"] and ev["outcome"] == "FLAG"
    assert ev["audit"]["matched_concept"] == "APPROVAL"
    assert ev["triggered"][0]["rule"] == "large discount needs finance review"

    # toggle off → gate detached
    r = client.post("/rules/toggle", json={"enabled": False})
    assert r.status_code == 200 and r.json()["enabled"] is False
    assert rag.rules_gate is None

    # evaluate while disabled → inactive
    r = client.post("/rules/evaluate", json={
        "src": "a", "tgt": "b", "relation_type": "GRANTED_APPROVAL",
        "relation_context": {"quantitative_data": "20% discount", "approved_via": "slack"},
    })
    assert r.json()["active"] is False

    # delete
    r = client.request("DELETE", "/rules")
    assert r.status_code == 200 and r.json()["deleted"] is True
    assert client.get("/rules").json()["exists"] is False


@pytest.mark.offline
def test_api_rejects_invalid_policy(client_and_rag):
    client, _ = client_and_rag
    r = client.post("/rules", json={"dsl": DSL, "concepts": {"NOPE": ["x"]}})
    assert r.status_code == 400
    assert "undefined concept" in r.json()["detail"]


@pytest.mark.offline
def test_api_503_when_not_context_graph():
    svc = _service()

    class PlainRag:  # no rules_gate attribute → not CG-capable
        pass

    app = FastAPI()
    app.include_router(
        create_rules_routes(PlainRag(), svc, api_key=None, workspace_resolver=lambda: "acme")
    )
    client = TestClient(app)
    assert client.get("/rules").status_code == 503
