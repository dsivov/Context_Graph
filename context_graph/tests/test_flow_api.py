"""P2 — flow store, the bus trigger, and the /flows + /runs HTTP surface.

* FlowStore: versioned append-only save, get (pinned/latest), for_event, delete.
* FlowTrigger: an event on the bus starts the subscribed flow, idempotently.
* HTTP: author a flow → dry-run → inspect the run → replay it.
Offline; a fake rag records emit_decision_trace and backs lifecycle node state.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from context_graph.events.schema import Event
from context_graph.flows import (
    FlowDefinition,
    FlowEdge,
    FlowExecutor,
    FlowNode,
    InMemoryFlowStore,
    InMemoryRunStore,
)
from context_graph.flows.trigger import FlowTrigger


class _FakeGraph:
    def __init__(self):
        self.nodes = {}

    async def get_node(self, nid):
        return dict(self.nodes[nid]) if nid in self.nodes else None

    async def upsert_node(self, nid, data):
        self.nodes[nid] = dict(data)


class _FakeRag:
    def __init__(self):
        self.rules_gate = object()
        self.chunk_entity_relation_graph = _FakeGraph()
        self.quads = []

    async def emit_decision_trace(self, src, tgt, relation_type, rc, upsert=True):
        self.quads.append((src, tgt, relation_type))
        return None


def _simple_flow() -> FlowDefinition:
    """A no-service flow: event → task(no action svc, no-op) → state done."""
    return FlowDefinition(
        id="ping",
        version=1,
        on_event="ping.received",
        nodes=[
            FlowNode("in", "event"),
            FlowNode("noop", "task", ref="noop"),   # no action service → recorded no-op
            FlowNode("done", "state", ref="done"),
        ],
        edges=[FlowEdge("in", "noop"), FlowEdge("noop", "done")],
    )


def _executor(flow_store, rag):
    return FlowExecutor(
        flow_store, InMemoryRunStore(), rag_resolver=lambda ws: rag,
    )


# ── FlowStore ────────────────────────────────────────────────────────────────


@pytest.mark.offline
class TestFlowStore:
    def test_save_versions_append_only(self):
        store = InMemoryFlowStore()
        v1 = store.save("w", _simple_flow())
        assert v1.version == 1
        v2 = store.save("w", _simple_flow())
        assert v2.version == 2
        # old version still resolvable (runs pin it)
        assert store.get("w", "ping", version=1).version == 1
        assert store.get("w", "ping").version == 2      # latest by default

    def test_for_event_and_delete(self):
        store = InMemoryFlowStore()
        store.save("w", _simple_flow())
        assert [f.id for f in store.for_event("w", "ping.received")] == ["ping"]
        assert store.for_event("w", "other") == []
        assert store.delete("w", "ping") is True
        assert store.get("w", "ping") is None

    def test_save_rejects_invalid_flow(self):
        store = InMemoryFlowStore()
        bad = FlowDefinition(id="x", on_event="", nodes=[], edges=[])
        with pytest.raises(ValueError):
            store.save("w", bad)


# ── FlowTrigger ──────────────────────────────────────────────────────────────


@pytest.mark.offline
@pytest.mark.asyncio
async def test_trigger_starts_flow_and_is_idempotent():
    store = InMemoryFlowStore()
    store.save("w", _simple_flow())
    rag = _FakeRag()
    ex = _executor(store, rag)
    trigger = FlowTrigger(store, ex)

    event = Event(type="ping.received", payload={}, workspace="w",
                  idempotency_key="e1")
    await trigger(event)
    await trigger(event)                     # re-delivery

    runs = await ex.run_store.list("w")
    assert len(runs) == 1                     # idempotent — one run only
    assert runs[0].status == "done" and runs[0].state == "done"


@pytest.mark.offline
@pytest.mark.asyncio
async def test_trigger_ignores_unsubscribed_event():
    store = InMemoryFlowStore()
    store.save("w", _simple_flow())
    ex = _executor(store, _FakeRag())
    await FlowTrigger(store, ex)(Event(type="nope", workspace="w"))
    assert await ex.run_store.list("w") == []


# ── HTTP ─────────────────────────────────────────────────────────────────────


def _client():
    from lightrag.api.routers.flow_routes import create_flow_routes

    store = InMemoryFlowStore()
    rag = _FakeRag()
    ex = _executor(store, rag)
    app = FastAPI()
    app.include_router(create_flow_routes(
        rag, store, ex, api_key=None, workspace_resolver=lambda: "w"))
    return TestClient(app), store


@pytest.mark.offline
class TestFlowApi:
    def test_author_dry_run_inspect_replay(self):
        client, _ = _client()

        # save
        r = client.post("/flows", json={"flow": _simple_flow().to_dict()})
        assert r.status_code == 200 and r.json()["flow"]["version"] == 1

        # list + get
        assert [f["id"] for f in client.get("/flows").json()["flows"]] == ["ping"]
        assert client.get("/flows/ping").json()["on_event"] == "ping.received"
        assert client.get("/flows/missing").status_code == 404

        # dry-run → a completed run
        dr = client.post("/flows/ping/dry-run", json={"payload": {"x": 1}})
        assert dr.status_code == 200
        run = dr.json()
        assert run["status"] == "done" and run["state"] == "done"
        run_id = run["run_id"]

        # inspect
        assert client.get(f"/runs/{run_id}").json()["cursor"] == "done"
        assert client.get("/runs").json()["count"] == 1
        assert client.get("/runs?status=done").json()["count"] == 1
        assert client.get("/runs?status=running").json()["count"] == 0

        # replay reproduces
        rep = client.get(f"/runs/{run_id}/replay").json()
        assert rep["ok"] is True
        assert rep["status"] == "done" and rep["state"] == "done"

    def test_save_bad_flow_400(self):
        client, _ = _client()
        r = client.post("/flows", json={"flow": {"id": "x", "on_event": "",
                                                 "nodes": [], "edges": []}})
        assert r.status_code == 400

    def test_missing_run_and_flow_404(self):
        client, _ = _client()
        assert client.get("/runs/nope").status_code == 404
        assert client.get("/runs/nope/replay").status_code == 404
        assert client.post("/flows/nope/dry-run", json={}).status_code == 404
