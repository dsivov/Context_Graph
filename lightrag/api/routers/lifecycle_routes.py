"""Lifecycle API — manage a workspace's object-type state machines (P3, Gap 2).

Endpoints (all workspace-scoped via ``LIGHTRAG-WORKSPACE``):

  GET    /lifecycle          — summary: object type → states / initial / transitions
  POST   /lifecycle          — set/replace the workspace lifecycle (validated)
  DELETE /lifecycle          — remove the lifecycle (→ transitions unrestricted)
  POST   /lifecycle/check    — dry-run a transition (object_type, from, to, role?)

No machine for a type → permissive. Enforcement happens as a guard on
``/actions/invoke`` for transition actions. Available only in Context Graph mode.
See docs/LIFECYCLE_SPEC.md.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from lightrag.api.utils_api import get_combined_auth_dependency
from lightrag.utils import logger


class LifecycleRequest(BaseModel):
    lifecycle: Dict[str, Any] = Field(
        description="The lifecycle as JSON (name, machines: {object_type: {...}}).")


class LifecycleSummaryResponse(BaseModel):
    workspace: str
    exists: bool
    name: Optional[str] = None
    version: Optional[int] = None
    updated_at: Optional[float] = None
    machines: Dict[str, Any] = Field(default_factory=dict)


class TransitionCheckRequest(BaseModel):
    object_type: str
    from_: str = Field(alias="from")
    to: str
    role: Optional[str] = None

    model_config = {"populate_by_name": True}


def _require_cg(rag) -> None:
    if not hasattr(rag, "rules_gate"):
        raise HTTPException(
            status_code=503,
            detail="Lifecycle requires Context Graph mode. Set USE_CONTEXT_GRAPH=true.",
        )


def create_lifecycle_routes(rag, service, *, api_key: Optional[str] = None,
                            workspace_resolver=None):
    """Build the /lifecycle router bound to *rag* and a LifecycleService."""
    if workspace_resolver is None:
        from lightrag.api.workspace_pool import _current_workspace

        def workspace_resolver():
            return _current_workspace.get()

    router = APIRouter(tags=["lifecycle"])
    combined_auth = get_combined_auth_dependency(api_key)

    def _ws() -> str:
        return workspace_resolver() or "default"

    @router.get("/lifecycle", response_model=LifecycleSummaryResponse,
                dependencies=[Depends(combined_auth)],
                summary="Summary of the workspace's lifecycle state machines")
    async def get_lifecycle():
        _require_cg(rag)
        return LifecycleSummaryResponse(**service.get_summary(_ws()))

    @router.post("/lifecycle", response_model=LifecycleSummaryResponse,
                 dependencies=[Depends(combined_auth)],
                 summary="Set/replace the workspace's lifecycle (validated)")
    async def set_lifecycle(request: LifecycleRequest):
        _require_cg(rag)
        ws = _ws()
        try:
            service.save(ws, request.lifecycle)
        except (ValueError, KeyError, TypeError) as e:
            raise HTTPException(status_code=400, detail=str(e))
        return LifecycleSummaryResponse(**service.get_summary(ws))

    @router.delete("/lifecycle", dependencies=[Depends(combined_auth)],
                   summary="Delete the workspace's lifecycle")
    async def delete_lifecycle():
        _require_cg(rag)
        ws = _ws()
        return {"deleted": service.delete(ws), "workspace": ws}

    @router.post("/lifecycle/check", dependencies=[Depends(combined_auth)],
                 summary="Dry-run a transition decision")
    async def check_lifecycle(request: TransitionCheckRequest):
        _require_cg(rag)
        d = service.check(_ws(), request.object_type, request.from_, request.to, role=request.role)
        return {"allowed": d.allowed, "reason": d.reason}

    logger.info("Lifecycle API routes registered")
    return router
