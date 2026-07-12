"""P0 test gate — the run store / durability seam (decision 1).

save/get round-trip, list filters by app_id and status, and due_timers selects
only past-due waiting runs. Both backends. Offline.
"""

from __future__ import annotations

import pytest

from context_graph.flows import Run
from context_graph.flows.store import InMemoryRunStore, JsonRunStore


def _make(backend: str, tmp_path):
    return InMemoryRunStore() if backend == "mem" else JsonRunStore(str(tmp_path))


@pytest.mark.offline
@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["mem", "json"])
async def test_save_get_list_filters(backend, tmp_path):
    store = _make(backend, tmp_path)
    r1 = Run(run_id="r1", app_id="app", flow_id="f", flow_version=1, status="running")
    r2 = Run(run_id="r2", app_id="app", flow_id="f", flow_version=1, status="done")
    r3 = Run(run_id="r3", app_id="other", flow_id="f", flow_version=1, status="running")
    for r in (r1, r2, r3):
        await store.save("w", r)

    got = await store.get("w", "r1")
    assert got is not None and got.run_id == "r1" and got.flow_version == 1

    assert {r.run_id for r in await store.list("w", app_id="app")} == {"r1", "r2"}
    assert {r.run_id for r in await store.list("w", status="running")} == {"r1", "r3"}
    assert await store.get("w", "missing") is None


@pytest.mark.offline
@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["mem", "json"])
async def test_round_trip_preserves_history_and_vars(backend, tmp_path):
    store = _make(backend, tmp_path)
    r = Run(run_id="r", app_id="a", flow_id="f", flow_version=2,
            vars={"discount": 22}, state="submitted")
    r.record("gateway1", "gateway", {"branch": "exceeds"})
    await store.save("w", r)

    got = await store.get("w", "r")
    assert got.vars == {"discount": 22}
    assert got.state == "submitted"
    assert got.history == [{"node": "gateway1", "kind": "gateway", "detail": {"branch": "exceeds"}}]


@pytest.mark.offline
@pytest.mark.asyncio
async def test_due_timers_selects_past_due_waiting():
    store = InMemoryRunStore()
    await store.save("w", Run(run_id="a", app_id="x", flow_id="f", flow_version=1,
                              status="waiting", wake_at="2026-01-01T00:00:00"))
    await store.save("w", Run(run_id="b", app_id="x", flow_id="f", flow_version=1,
                              status="waiting", wake_at="2027-01-01T00:00:00"))
    await store.save("w", Run(run_id="c", app_id="x", flow_id="f", flow_version=1,
                              status="running", wake_at="2026-01-01T00:00:00"))

    due = await store.due_timers("w", now="2026-06-01T00:00:00")
    assert {r.run_id for r in due} == {"a"}   # b is future; c is not waiting
