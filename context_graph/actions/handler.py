"""Action side-effect handlers (P3).

The first slice supports two handler kinds:

* ``none``    — record-only. The audited decision trace *is* the effect.
* ``webhook`` — POST the invocation payload to a URL.

The webhook path is **SSRF-guarded**: the target host is resolved and rejected
if it maps to a loopback / private / link-local / reserved address, unless the
action's handler explicitly sets ``allow_internal`` (for trusted internal
automation). Only ``http``/``https`` schemes are allowed.
"""

from __future__ import annotations

import ipaddress
import socket
from typing import Any, Dict
from urllib.parse import urlparse

from lightrag.utils import logger

from context_graph.actions.schema import ActionHandler


class HandlerError(Exception):
    """Raised when a handler cannot or must not execute (e.g. a blocked URL)."""


def _assert_public_url(url: str, *, allow_internal: bool) -> None:
    """Reject non-http(s) URLs and, unless allowed, hosts that resolve to a
    non-public address (SSRF guard)."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise HandlerError(f"webhook url must be http(s), got '{parsed.scheme}'")
    host = parsed.hostname
    if not host:
        raise HandlerError("webhook url has no host")
    if allow_internal:
        return
    try:
        infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80),
                                   proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise HandlerError(f"could not resolve webhook host '{host}': {e}")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (ip.is_loopback or ip.is_private or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            raise HandlerError(
                f"webhook host '{host}' resolves to non-public address {ip}; "
                f"set handler.allow_internal=true to permit")


async def run_handler(handler: ActionHandler, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Execute a handler and return a JSON-serializable result record.

    Never raises for a *successful-but-non-2xx* webhook; raises
    :class:`HandlerError` only when the call must not or cannot be attempted
    (blocked URL, missing dependency, transport failure).
    """
    if handler.kind == "none":
        return {"kind": "none", "executed": False}

    if handler.kind == "webhook":
        _assert_public_url(handler.url or "", allow_internal=handler.allow_internal)
        try:
            import httpx
        except ImportError as e:  # pragma: no cover - httpx is an API dependency
            raise HandlerError(f"webhook handler requires httpx: {e}")
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.request(handler.method, handler.url, json=payload)
        except httpx.HTTPError as e:
            raise HandlerError(f"webhook request failed: {e}")
        logger.info(f"action webhook {handler.method} {handler.url} -> {resp.status_code}")
        return {"kind": "webhook", "executed": True, "status": resp.status_code,
                "ok": resp.is_success}

    raise HandlerError(f"unknown handler kind '{handler.kind}'")  # pragma: no cover
