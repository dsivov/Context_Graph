"""Ingress API — the platform's event front door (P1).

Endpoints (all workspace-scoped via the ``LIGHTRAG-WORKSPACE`` header):

  POST /ingress/webhook/{connector} — accept one inbound delivery: normalize,
        map, append to the durable ingress log (idempotent), publish on the bus
  GET  /ingress/log                 — replay the workspace's logged events
  GET  /ingress/connectors          — the registered connectors

A duplicate delivery is acknowledged with ``duplicate=true`` and not
re-published. A rules-gate REJECT raised by a decision subscriber maps to 422
with the audit record (the delivery itself stays logged — the rejection *is*
part of the record).
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from lightrag.api.utils_api import get_combined_auth_dependency
from lightrag.utils import logger


class IngressAcceptResponse(BaseModel):
    accepted: bool
    duplicate: bool = Field(description="True if this delivery was already seen.")
    workspace: str
    event_type: str
    dedupe_key: str
    mapped: bool = Field(description="True if a mapping spec typed the payload.")
    mapping_meta: Optional[Dict[str, Any]] = None


class IngressLogResponse(BaseModel):
    workspace: str
    count: int
    since: str
    events: list[Dict[str, Any]] = Field(default_factory=list)


def _require_context_graph(rag) -> None:
    if not hasattr(rag, "rules_gate"):
        raise HTTPException(
            status_code=503,
            detail="Ingress requires Context Graph mode. Set USE_CONTEXT_GRAPH=true.",
        )


def create_ingress_routes(rag, service, *, api_key: Optional[str] = None,
                          workspace_resolver=None):
    """Build the /ingress router bound to *rag* and an IngressService.

    workspace_resolver: callable returning the current workspace name; defaults
    to the request-scoped ``LIGHTRAG-WORKSPACE`` contextvar.
    """
    if workspace_resolver is None:
        from lightrag.api.workspace_pool import _current_workspace

        def workspace_resolver():
            return _current_workspace.get()

    router = APIRouter(tags=["ingress"])
    combined_auth = get_combined_auth_dependency(api_key)

    def _ws() -> str:
        return workspace_resolver() or "default"

    @router.post("/ingress/webhook/{connector}", response_model=IngressAcceptResponse,
                 dependencies=[Depends(combined_auth)],
                 summary="Accept one inbound webhook delivery")
    async def ingress_webhook(connector: str, payload: Dict[str, Any],
                              request: Request):
        _require_context_graph(rag)
        from context_graph.integration.schema import MappingError
        from context_graph.rules.gate import RuleViolation

        ws = _ws()
        try:
            result = await service.receive(
                ws, connector, payload, headers=dict(request.headers)
            )
        except LookupError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except (MappingError, ValueError) as e:
            raise HTTPException(status_code=400, detail=str(e))
        except RuleViolation as e:
            # The delivery is logged; the gated decision was rejected.
            raise HTTPException(
                status_code=422,
                detail={"status": "rejected", "outcome": "REJECT",
                        "audit": e.decision.audit},
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"ingress error on '{connector}': {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))

        event = result.event
        return IngressAcceptResponse(
            accepted=result.accepted,
            duplicate=result.duplicate,
            workspace=ws,
            event_type=event.type,
            dedupe_key=event.dedupe_key(),
            mapped=event.mapped,
            mapping_meta=event.mapping_meta,
        )

    @router.get("/ingress/log", response_model=IngressLogResponse,
                dependencies=[Depends(combined_auth)],
                summary="Replay the workspace's ingress log")
    async def ingress_log(since: str = "0", limit: int = 100):
        _require_context_graph(rag)
        ws = _ws()
        events = []
        async for event in service.log.replay(ws, since=since):
            events.append(event.to_dict())
            if len(events) >= max(1, limit):
                break
        return IngressLogResponse(
            workspace=ws, count=service.log.count(ws), since=since, events=events
        )

    @router.get("/ingress/connectors", dependencies=[Depends(combined_auth)],
                summary="The registered ingress connectors")
    async def ingress_connectors():
        _require_context_graph(rag)
        return {"connectors": service.connectors()}

    logger.info("Ingress API routes registered")
    return router
