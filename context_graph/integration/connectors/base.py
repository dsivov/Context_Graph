"""Ingress connector base — the pluggable interface for event sources (P1).

Mirrors the ``webingest/connectors`` plugin pattern (named class + one-line
``description`` + a registry in ``__init__.py``), but points the other way:
webingest connectors *pull documents out of* a site platform, ingress
connectors *normalize deliveries into* :class:`RawRecord`s for the event
backbone. Push sources implement :meth:`receive` (the webhook route calls it);
poll sources override :meth:`poll` (the P5 scheduler drives it).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, List, Mapping, Optional

from context_graph.integration.schema import RawRecord


class IngressConnector(ABC):
    """A named source of raw records."""

    name: str = "connector"
    # One line: what platform/shape this connector accepts. Shown in API
    # summaries and, later, to the Studio when binding connectors to apps.
    description: str = ""

    @abstractmethod
    def receive(
        self,
        payload: Any,
        *,
        headers: Optional[Mapping[str, str]] = None,
        source: str = "",
        ts: str = "",
    ) -> RawRecord:
        """Normalize one pushed delivery. Raises ``ValueError`` on a payload
        this connector cannot accept (the route maps it to 400)."""

    async def poll(self) -> List[RawRecord]:
        """Fetch pending records from a pull source. Push-only connectors
        keep the default (empty)."""
        return []
