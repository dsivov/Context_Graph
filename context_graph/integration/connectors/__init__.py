"""Ingress connector registry (P1).

Register new connectors here, mirroring ``webingest/connectors``. The ingress
service seeds itself from :data:`DEFAULT_CONNECTORS`.
"""

from context_graph.integration.connectors.base import IngressConnector
from context_graph.integration.connectors.webhook import WebhookConnector

DEFAULT_CONNECTORS = [WebhookConnector]

__all__ = ["IngressConnector", "WebhookConnector", "DEFAULT_CONNECTORS"]
