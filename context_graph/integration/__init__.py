"""Context Graph integration engine — events in, typed and deduped (P1).

    from context_graph.integration import IngressService, MappingSpec
    from context_graph.events import InProcessBus
    from context_graph.events.store import JsonIngressLog

    svc = IngressService(JsonIngressLog("./rag_storage/ingress"), InProcessBus())
    svc.set_mapping("acme", "webhook", MappingSpec(event_type="discount.requested",
                                                   fields={"customer": "customer"}))
    result = await svc.receive("acme", "webhook", payload)

See docs/PLATFORM_ARCHITECTURE.html (integration engine; decisions 2/3).
"""

from context_graph.integration.connectors import (
    DEFAULT_CONNECTORS,
    IngressConnector,
    WebhookConnector,
)
from context_graph.integration.mapper import DeterministicMapper, Mapper
from context_graph.integration.schema import (
    DecisionBinding,
    MappingError,
    MappingSpec,
    RawRecord,
    pluck,
)
from context_graph.integration.service import (
    DecisionSubscriber,
    IngressResult,
    IngressService,
)

__all__ = [
    "DEFAULT_CONNECTORS",
    "IngressConnector",
    "WebhookConnector",
    "Mapper",
    "DeterministicMapper",
    "DecisionBinding",
    "MappingError",
    "MappingSpec",
    "RawRecord",
    "pluck",
    "DecisionSubscriber",
    "IngressResult",
    "IngressService",
]
