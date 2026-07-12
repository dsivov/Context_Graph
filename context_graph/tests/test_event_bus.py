"""P0 test gate — the in-process event bus (decision 2).

Publish/subscribe round-trip, multiple subscribers per type, wildcard, and
type-filtering isolation. Offline.
"""

from __future__ import annotations

import pytest

from context_graph.events import Event, InProcessBus


@pytest.mark.offline
@pytest.mark.asyncio
async def test_publish_reaches_typed_subscriber():
    bus = InProcessBus()
    got: list[Event] = []

    async def handler(e: Event) -> None:
        got.append(e)

    bus.subscribe("request.submitted", handler)
    await bus.publish(Event(type="request.submitted", payload={"x": 1}))

    assert len(got) == 1
    assert got[0].payload["x"] == 1


@pytest.mark.offline
@pytest.mark.asyncio
async def test_multiple_subscribers_wildcard_and_filtering():
    bus = InProcessBus()
    a: list[Event] = []
    b: list[Event] = []
    allc: list[Event] = []

    async def ha(e): a.append(e)
    async def hb(e): b.append(e)
    async def hall(e): allc.append(e)

    bus.subscribe("A", ha)
    bus.subscribe("A", hb)      # two subscribers, same type
    bus.subscribe("*", hall)    # wildcard

    await bus.publish(Event(type="A"))
    await bus.publish(Event(type="B"))

    assert len(a) == 1 and len(b) == 1          # both A-subscribers fired once
    assert all(e.type == "A" for e in a)        # type filtering: no B leaked
    assert [e.type for e in allc] == ["A", "B"]  # wildcard saw both, in order


@pytest.mark.offline
@pytest.mark.asyncio
async def test_no_subscribers_is_safe():
    bus = InProcessBus()
    await bus.publish(Event(type="unheard"))    # must not raise
