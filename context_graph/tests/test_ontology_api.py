"""Tests for OntologyService and the /ontology router (P2). Offline."""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from context_graph.ontology import InMemoryOntologyStore, OntologyService
from lightrag.api.routers.ontology_routes import create_ontology_routes


ONTO = {
    "name": "sales",
    "object_types": [
        {"name": "Person", "properties": [{"name": "email", "kind": "string"}]},
        {"name": "Order", "properties": [
            {"name": "value", "kind": "money", "required": True}]},
    ],
    "link_types": [
        {"name": "approved", "source_types": ["Person"], "target_types": ["Order"],
         "cardinality": "1:N"},
    ],
}


def _service():
    return OntologyService(InMemoryOntologyStore(now=lambda: 1.0))


# ── service unit ─────────────────────────────────────────────────────────────


@pytest.mark.offline
def test_service_save_and_summary():
    svc = _service()
    assert svc.get_summary("acme")["exists"] is False
    svc.save("acme", ONTO)
    s = svc.get_summary("acme")
    assert s["exists"] and s["name"] == "sales" and s["version"] == 1
    assert {o["name"] for o in s["object_types"]} == {"Person", "Order"}
    assert s["link_types"][0]["cardinality"] == "1:N"
    assert s["lint"] == []


@pytest.mark.offline
def test_service_rejects_inconsistent_ontology():
    svc = _service()
    bad = {"name": "x", "object_types": [{"name": "Person", "properties": []}],
           "link_types": [{"name": "r", "source_types": ["Person"],
                           "target_types": ["Ghost"]}]}   # Ghost undefined
    with pytest.raises(ValueError):
        svc.save("acme", bad)


@pytest.mark.offline
def test_service_validate_extraction():
    svc = _service()
    svc.save("acme", ONTO)
    res = svc.validate_extraction(
        "acme",
        entities=[{"entity_name": "Sarah", "entity_type": "Person"},
                  {"entity_name": "Deal", "entity_type": "Order", "value": "$9,000"}],
        relations=[{"src_id": "Sarah", "tgt_id": "Deal", "keywords": "approved"}],
    )
    assert res["exists"] and res["ok"] and res["conforming"] == 3


# ── API ──────────────────────────────────────────────────────────────────────


class FakeRag:
    """Stand-in for a workspace ContextGraph instance."""

    def __init__(self, llm=None):
        self.rules_gate = None      # marks CG-capability
        if llm is not None:
            self.llm_model_func = llm


def _client(rag=None, svc=None):
    svc = svc or _service()
    rag = rag or FakeRag()
    app = FastAPI()
    app.include_router(
        create_ontology_routes(rag, svc, api_key=None, workspace_resolver=lambda: "acme"))
    return TestClient(app), rag, svc


@pytest.mark.offline
def test_api_lifecycle():
    client, _, _ = _client()

    assert client.get("/ontology").json()["exists"] is False

    r = client.post("/ontology", json={"ontology": ONTO})
    assert r.status_code == 200
    body = r.json()
    assert body["exists"] and body["version"] == 1
    assert {o["name"] for o in body["object_types"]} == {"Person", "Order"}
    assert body["ontology"]["link_types"][0]["name"] == "approved"

    # re-save bumps the version
    assert client.post("/ontology", json={"ontology": ONTO}).json()["version"] == 2

    r = client.request("DELETE", "/ontology")
    assert r.status_code == 200 and r.json()["deleted"] is True
    assert client.get("/ontology").json()["exists"] is False


@pytest.mark.offline
def test_api_rejects_inconsistent_ontology():
    client, _, _ = _client()
    bad = {"name": "x", "object_types": [{"name": "Person", "properties": []}],
           "link_types": [{"name": "r", "source_types": ["Person"],
                           "target_types": ["Ghost"]}]}
    r = client.post("/ontology", json={"ontology": bad})
    assert r.status_code == 400 and "Ghost" in r.json()["detail"]


@pytest.mark.offline
def test_api_validate_endpoint():
    client, _, svc = _client()
    svc.save("acme", ONTO)
    r = client.post("/ontology/validate", json={
        "entities": [{"entity_name": "HQ", "entity_type": "Building"}],
        "closed_world": True,
    })
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False and body["unknown_types"] == ["Building"]


@pytest.mark.offline
def test_api_validate_404_without_ontology():
    client, _, _ = _client()
    r = client.post("/ontology/validate", json={"entities": []})
    assert r.status_code == 404


@pytest.mark.offline
def test_api_generate_and_save():
    reply = {
        "ontology": ONTO,
        "samples": {"entities": [{"name": "S", "type": "Person"}], "relations": []},
        "explanation": "people and orders",
    }

    async def fake_llm(prompt, system_prompt=None, **kw):
        return json.dumps(reply)

    client, rag, svc = _client(rag=FakeRag(llm=fake_llm))
    r = client.post("/ontology/generate", json={"description": "sales domain", "save": True})
    assert r.status_code == 200
    body = r.json()
    assert body["valid"] and body["saved"] is True
    # it was persisted
    assert svc.get_summary("acme")["exists"] is True


@pytest.mark.offline
def test_api_generate_503_without_llm():
    client, _, _ = _client()      # FakeRag with no llm_model_func
    r = client.post("/ontology/generate", json={"description": "x"})
    assert r.status_code == 503


@pytest.mark.offline
def test_api_503_when_not_context_graph():
    class PlainRag:               # no rules_gate → not CG-capable
        pass

    app = FastAPI()
    app.include_router(create_ontology_routes(
        PlainRag(), _service(), api_key=None, workspace_resolver=lambda: "acme"))
    client = TestClient(app)
    assert client.get("/ontology").status_code == 503
