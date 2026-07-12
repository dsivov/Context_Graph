"""Context Graph event backbone — the platform ingress + dispatch layer (P0).

    from context_graph.events import Event, InProcessBus
    from context_graph.events.store import JsonIngressLog

    bus = InProcessBus()
    log = JsonIngressLog("./rag_storage")
    if await log.append("acme", event):   # False if a duplicate delivery
        await bus.publish(event)

See docs/PLATFORM_ARCHITECTURE.html (decision 2 — in-process bus + durable
ingress log).
"""

from context_graph.events.schema import Event
from context_graph.events.service import EventBus, Handler, InProcessBus, WILDCARD
from context_graph.events.store import (
    IngressLog,
    InMemoryIngressLog,
    JsonIngressLog,
)

__all__ = [
    "Event",
    "EventBus",
    "Handler",
    "InProcessBus",
    "WILDCARD",
    "IngressLog",
    "InMemoryIngressLog",
    "JsonIngressLog",
]
