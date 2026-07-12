"""In-process event bus (P0, decision 2).

:class:`EventBus` is the port; :class:`InProcessBus` is the lean default adapter
("in-process behind an interface, swap to a broker later"). Handlers are async
callables; :meth:`InProcessBus.publish` awaits matching handlers in registration
order. Subscribe to a concrete event type or to ``"*"`` for all types.

The bus is **not** durable — pair it with :class:`context_graph.events.store.IngressLog`
(append-then-publish) for at-least-once, replayable ingress.
"""

from __future__ import annotations

from typing import Awaitable, Callable, Dict, List, Protocol, runtime_checkable

from lightrag.utils import logger

from context_graph.events.schema import Event

Handler = Callable[[Event], Awaitable[None]]
WILDCARD = "*"


@runtime_checkable
class EventBus(Protocol):
    def subscribe(self, event_type: str, handler: Handler) -> None: ...
    async def publish(self, event: Event) -> None: ...


class InProcessBus:
    """Single-process async pub/sub. Handlers for the event's exact type run
    first (in registration order), then wildcard handlers."""

    def __init__(self) -> None:
        self._subs: Dict[str, List[Handler]] = {}

    def subscribe(self, event_type: str, handler: Handler) -> None:
        self._subs.setdefault(event_type, []).append(handler)

    async def publish(self, event: Event) -> None:
        handlers = list(self._subs.get(event.type, [])) + list(
            self._subs.get(WILDCARD, [])
        )
        if not handlers:
            logger.debug(f"InProcessBus: no subscribers for '{event.type}'")
        for handler in handlers:
            await handler(event)
