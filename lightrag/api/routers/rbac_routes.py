"""RBAC API — manage a workspace's role-based access policy (P3, Gap 1).

Endpoints (all workspace-scoped via the ``LIGHTRAG-WORKSPACE`` header):

  GET    /rbac          — summary: roles → grants, version
  POST   /rbac          — set/replace the workspace policy (validated)
  DELETE /rbac          — remove the policy (workspace reverts to permissive)
  POST   /rbac/check    — dry-run an access decision (role, verb, target)

Opt-in and deny-by-default *within* a policy; a workspace with no policy is
permissive. Enforcement of the policy happens as a pre-check on
``/actions/invoke``. Available only in Context Graph mode. See docs/RBAC_SPEC.md.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from lightrag.api.utils_api import get_combined_auth_dependency
from lightrag.utils import logger


# ─────────────────────────────────────────────────────────────────────────────
# Models
# ─────────────────────────────────────────────────────────────────────────────


class RbacPolicyRequest(BaseModel):
    policy: Dict[str, Any] = Field(
        description="The RBAC policy as JSON (name, roles: {role: [grants]}).")


class RbacSummaryResponse(BaseModel):
    workspace: str
    exists: bool
    name: Optional[str] = None
    version: Optional[int] = None
    updated_at: Optional[float] = None
    roles: Dict[str, List[str]] = Field(default_factory=dict)


class CheckRequest(BaseModel):
    role: Optional[str] = Field(default=None, description="The principal's role.")
    verb: str = Field(default="invoke", description="invoke | create | update | delete | read")
    target: str = Field(description="Action name or object type.")
    object_ref: Optional[str] = None


# ─────────────────────────────────────────────────────────────────────────────
# Router factory
# ─────────────────────────────────────────────────────────────────────────────


def _require_cg(rag) -> None:
    if not hasattr(rag, "rules_gate"):
        raise HTTPException(
            status_code=503,
            detail="RBAC requires Context Graph mode. Set USE_CONTEXT_GRAPH=true.",
        )


def create_rbac_routes(rag, service, *, api_key: Optional[str] = None,
                       workspace_resolver=None):
    """Build the /rbac router bound to *rag* and an RbacService."""
    if workspace_resolver is None:
        from lightrag.api.workspace_pool import _current_workspace

        def workspace_resolver():
            return _current_workspace.get()

    router = APIRouter(tags=["rbac"])
    combined_auth = get_combined_auth_dependency(api_key)

    def _ws() -> str:
        return workspace_resolver() or "default"

    @router.get("/rbac", response_model=RbacSummaryResponse,
                dependencies=[Depends(combined_auth)],
                summary="Summary of the workspace's RBAC policy")
    async def get_rbac():
        _require_cg(rag)
        return RbacSummaryResponse(**service.get_summary(_ws()))

    @router.post("/rbac", response_model=RbacSummaryResponse,
                 dependencies=[Depends(combined_auth)],
                 summary="Set/replace the workspace's RBAC policy (validated)")
    async def set_rbac(request: RbacPolicyRequest):
        _require_cg(rag)
        ws = _ws()
        try:
            service.save(ws, request.policy)
        except (ValueError, KeyError, TypeError) as e:
            raise HTTPException(status_code=400, detail=str(e))
        return RbacSummaryResponse(**service.get_summary(ws))

    @router.delete("/rbac", dependencies=[Depends(combined_auth)],
                   summary="Delete the workspace's RBAC policy (→ permissive)")
    async def delete_rbac():
        _require_cg(rag)
        ws = _ws()
        return {"deleted": service.delete(ws), "workspace": ws}

    @router.post("/rbac/check", dependencies=[Depends(combined_auth)],
                 summary="Dry-run an access decision")
    async def check_rbac(request: CheckRequest):
        _require_cg(rag)
        d = service.check(_ws(), request.role, request.verb, request.target,
                          object_ref=request.object_ref, rag=rag)
        return {"allowed": d.allowed, "reason": d.reason}

    logger.info("RBAC API routes registered")
    return router
