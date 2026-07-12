"""P0 test gate — the durable ingress log (decision 2).

Append is durable; a repeated dedupe key returns False (idempotency); replay
yields in append order and honours the offset cursor. Both backends. Offline.
"""

from __future__ import annotations

import pytest

from context_graph.events import Event
from context_graph.events.store import InMemoryIngressLog, JsonIngressLog


def _make(backend: str, tmp_path):
    return InMemoryIngressLog() if backend == "mem" else JsonIngressLog(str(tmp_path))


@pytest.mark.offline
@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["mem", "json"])
async def test_append_dedupe_and_replay(backend, tmp_path):
    log = _make(backend, tmp_path)
    e1 = Event(type="A", source="s", idempotency_key="k1", workspace="w")
    e2 = Event(type="B", source="s", idempotency_key="k2", workspace="w")

    assert await log.append("w", e1) is True
    assert await log.append("w", e1) is False    # dedupe on repeated key
    assert await log.append("w", e2) is True
    assert log.count("w") == 2

    events = [e async for e in log.replay("w")]
    assert [e.type for e in events] == ["A", "B"]

    tail = [e async for e in log.replay("w", since="1")]
    assert [e.type for e in tail] == ["B"]        # offset cursor


@pytest.mark.offline
@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["mem", "json"])
async def test_dedupe_key_fallback_content_hash(backend, tmp_path):
    log = _make(backend, tmp_path)
    e = Event(type="A", source="s", payload={"n": 1}, workspace="w")
    same = Event(type="A", source="s", payload={"n": 1}, workspace="w")
    diff = Event(type="A", source="s", payload={"n": 2}, workspace="w")

    assert await log.append("w", e) is True
    assert await log.append("w", same) is False   # identical content deduped
    assert await log.append("w", diff) is True     # different payload stored


@pytest.mark.offline
@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["mem", "json"])
async def test_workspaces_isolated(backend, tmp_path):
    log = _make(backend, tmp_path)
    await log.append("w1", Event(type="A", idempotency_key="k", workspace="w1"))
    # same key in a different workspace is NOT a duplicate
    assert await log.append("w2", Event(type="A", idempotency_key="k", workspace="w2")) is True
    assert log.count("w1") == 1 and log.count("w2") == 1
