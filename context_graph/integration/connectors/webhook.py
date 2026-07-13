"""Generic inbound-webhook connector (P1) — the platform's first event source.

Accepts any JSON-object POST body. The delivery id (→ idempotency key) is
taken from the standard retry headers first, then common payload keys; when
neither exists the event backbone's content-hash fallback still collapses
identical re-deliveries. The source timestamp comes from the payload — a
webhook that doesn't stamp its deliveries gets stamped once at the ingress
edge (by the service), never deeper in.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

from context_graph.integration.connectors.base import IngressConnector
from context_graph.integration.schema import RawRecord

# Checked in order, lowercase. Deliberately small: idempotency/delivery ids
# only — request ids vary per retry and would defeat the dedupe.
_ID_HEADERS = ("x-idempotency-key", "x-delivery-id", "x-github-delivery")
_ID_KEYS = ("idempotency_key", "delivery_id", "event_id", "id")
_TS_KEYS = ("ts", "timestamp", "occurred_at", "created_at")


class WebhookConnector(IngressConnector):
    name = "webhook"
    description = "Generic JSON webhook: POST /ingress/webhook/webhook"

    def receive(
        self,
        payload: Any,
        *,
        headers: Optional[Mapping[str, str]] = None,
        source: str = "",
        ts: str = "",
    ) -> RawRecord:
        if not isinstance(payload, Mapping):
            raise ValueError("webhook payload must be a JSON object")
        low = {k.lower(): v for k, v in (headers or {}).items()}
        external_id = next(
            (str(low[h]) for h in _ID_HEADERS if low.get(h)), ""
        ) or next((str(payload[k]) for k in _ID_KEYS if payload.get(k)), "")
        ts = ts or next((str(payload[k]) for k in _TS_KEYS if payload.get(k)), "")
        return RawRecord(
            connector=self.name,
            data=dict(payload),
            external_id=external_id,
            ts=ts,
            source=source or self.name,
        )
