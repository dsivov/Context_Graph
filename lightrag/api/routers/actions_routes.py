"""Actions API — manage and invoke a workspace's action catalog (P3).

Endpoints (all workspace-scoped via the ``LIGHTRAG-WORKSPACE`` header):

  GET    /actions           — summary: name, version, the actions + their params
  POST   /actions           — set/replace the workspace action catalog (validated)
  DELETE /actions           — remove the workspace action catalog
  GET    /actions/{name}    — one action's full definition
  POST   /actions/invoke    — run an action: validate args → rules gate → side effect

Invoking an action authorizes and records it through the pre-emit rules gate
(``emit_decision_trace``), so an action is governed exactly like any other
decision and leaves an audit edge. A gate REJECT maps to HTTP 422 and never
runs the action's side effect.

Mirrors the /ontology and /rules routers. Available only in Context Graph mode.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from lightrag.api.utils_api import get_combined_auth_dependency, get_principal
from lightrag.utils import logger


# ─────────────────────────────────────────────────────────────────────────────
# Models
# ─────────────────────────────────────────────────────────────────────────────


class ActionCatalogRequest(BaseModel):
    catalog: Dict[str, Any] = Field(
        description="The action catalog as JSON (name, actions[]).")


class ActionParamInfo(BaseModel):
    name: str
    kind: str
    required: bool = False


class ActionInfo(BaseModel):
    name: str
    object_type: str = ""
    relation_type: str = ""
    description: str = ""
    effect: str = ""
    handler: str = "none"
    params: List[ActionParamInfo] = Field(default_factory=list)


class ActionSummaryResponse(BaseModel):
    workspace: str
    exists: bool
    name: Optional[str] = None
    version: Optional[int] = None
    updated_at: Optional[float] = None
    actions: List[ActionInfo] = Field(default_factory=list)
    lint: List[str] = Field(default_factory=list)
    catalog: Optional[Dict[str, Any]] = None


class InvokeActionRequest(BaseModel):
    action: str = Field(description="Name of the action to invoke.")
    object_ref: str = Field(description="The object instance acted upon (edge tail).")
    actor: str = Field(default="system", description="Who invokes it (edge head / approver).")
    args: Dict[str, Any] = Field(default_factory=dict,
                                 description="Typed arguments, validated against the action.")


# ─────────────────────────────────────────────────────────────────────────────
# Router factory
# ─────────────────────────────────────────────────────────────────────────────


def _require_cg(rag) -> None:
    # A ContextGraph instance carries a ``rules_gate`` slot; plain LightRAG doesn't.
    if not hasattr(rag, "rules_gate"):
        raise HTTPException(
            status_code=503,
            detail="Actions require Context Graph mode. Set USE_CONTEXT_GRAPH=true.",
        )


def create_actions_routes(rag, service, *, rbac_service=None, api_key: Optional[str] = None,
                          workspace_resolver=None):
    """Build the /actions router bound to *rag* and an ActionService.

    If *rbac_service* is provided, invoking an action is gated by a pre-check
    (``resolve principal → RBAC → rules gate → side effect``); a workspace with
    no RBAC policy stays permissive.
    """
    if workspace_resolver is None:
        from lightrag.api.workspace_pool import _current_workspace

        def workspace_resolver():
            return _current_workspace.get()

    router = APIRouter(tags=["actions"])
    combined_auth = get_combined_auth_dependency(api_key)

    def _ws() -> str:
        return workspace_resolver() or "default"

    @router.get("/actions", response_model=ActionSummaryResponse,
                dependencies=[Depends(combined_auth)],
                summary="Summary of the workspace's action catalog")
    async def get_actions():
        _require_cg(rag)
        return ActionSummaryResponse(**service.get_summary(_ws()))

    @router.post("/actions", response_model=ActionSummaryResponse,
                 dependencies=[Depends(combined_auth)],
                 summary="Set/replace the workspace's action catalog (validated)")
    async def set_actions(request: ActionCatalogRequest):
        _require_cg(rag)
        ws = _ws()
        try:
            service.save(ws, request.catalog)
        except (ValueError, KeyError, TypeError) as e:
            raise HTTPException(status_code=400, detail=str(e))
        return ActionSummaryResponse(**service.get_summary(ws))

    @router.delete("/actions", dependencies=[Depends(combined_auth)],
                   summary="Delete the workspace's action catalog")
    async def delete_actions():
        _require_cg(rag)
        ws = _ws()
        return {"deleted": service.delete(ws), "workspace": ws}

    @router.get("/actions/{name}", dependencies=[Depends(combined_auth)],
                summary="One action's full definition")
    async def get_action(name: str):
        _require_cg(rag)
        action = service.get_action(_ws(), name)
        if action is None:
            raise HTTPException(status_code=404, detail=f"No action '{name}'.")
        return action

    @router.post("/actions/invoke", dependencies=[Depends(combined_auth)],
                 summary="Invoke an action (RBAC → validate → rules gate → side effect)")
    async def invoke_action(request: InvokeActionRequest, http_request: Request):
        _require_cg(rag)
        # RBAC pre-check — may this authenticated principal invoke this action?
        # The role comes from the token, never from request.actor. No policy → permissive.
        if rbac_service is not None:
            principal = get_principal(http_request)
            role = principal.get("role") if principal else None
            decision = rbac_service.check(_ws(), role, "invoke", request.action,
                                          object_ref=request.object_ref, rag=rag)
            if not decision.allowed:
                raise HTTPException(status_code=403, detail=decision.reason)
        result = await service.invoke(
            rag, _ws(), request.action,
            actor=request.actor, object_ref=request.object_ref, args=request.args)
        if not result.get("ok"):
            err = result.get("error")
            if err == "unknown_action":
                raise HTTPException(status_code=404,
                                    detail=f"No action '{request.action}'.")
            if err == "invalid_arguments":
                raise HTTPException(status_code=400,
                                    detail="; ".join(result.get("errors", [])))
            if err == "rejected":
                raise HTTPException(status_code=422, detail=result)
        return result

    logger.info("Actions API routes registered")
    return router
