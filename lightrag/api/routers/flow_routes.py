"""Flow engine API — author flows, run them, inspect and replay runs (P2).

Endpoints (all workspace-scoped via the ``LIGHTRAG-WORKSPACE`` header):

  GET    /flows              — the latest version of every authored flow
  POST   /flows              — save a flow (validated + lint); assigns next version
  GET    /flows/{id}         — one flow (``?version=`` pins an old version)
  DELETE /flows/{id}         — remove a flow (all versions)
  POST   /flows/{id}/dry-run — start a run from a synthetic event/vars, no wait
  GET    /runs               — list runs (``?status=`` / ``?app_id=`` filters)
  GET    /runs/{run_id}      — one run (cursor, status, state, history)
  GET    /runs/{run_id}/replay — re-walk the recorded history; reproduce or diff

A flow starts automatically when an ingress event matches its ``on_event`` (see
:class:`~context_graph.flows.trigger.FlowTrigger`); ``dry-run`` is the manual
path for authoring/testing. Available only in Context Graph mode.
See docs/PLATFORM_WORK_PLAN.md (P2).
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from lightrag.api.utils_api import get_combined_auth_dependency
from lightrag.utils import logger


class FlowRequest(BaseModel):
    flow: Dict[str, Any] = Field(
        description="The flow as JSON (id, on_event, nodes, edges).")


class DryRunRequest(BaseModel):
    event_type: Optional[str] = Field(
        default=None, description="Event type; defaults to the flow's on_event.")
    payload: Dict[str, Any] = Field(
        default_factory=dict, description="Synthetic event payload → run vars.")
    vars: Dict[str, Any] = Field(
        default_factory=dict, description="Extra run vars (override payload).")


def _require_cg(rag) -> None:
    if not hasattr(rag, "rules_gate"):
        raise HTTPException(
            status_code=503,
            detail="Flow engine requires Context Graph mode. Set USE_CONTEXT_GRAPH=true.",
        )


def create_flow_routes(rag, flow_store, executor, *, api_key: Optional[str] = None,
                       workspace_resolver=None):
    """Build the /flows + /runs router bound to *rag*, a FlowStore, and a
    FlowExecutor."""
    if workspace_resolver is None:
        from lightrag.api.workspace_pool import _current_workspace

        def workspace_resolver():
            return _current_workspace.get()

    from context_graph.events.schema import Event
    from context_graph.flows.schema import FlowDefinition

    router = APIRouter(tags=["flows"])
    combined_auth = get_combined_auth_dependency(api_key)

    def _ws() -> str:
        return workspace_resolver() or "default"

    # -- authoring ----------------------------------------------------------

    @router.get("/flows", dependencies=[Depends(combined_auth)],
                summary="List authored flows (latest version each)")
    async def list_flows():
        _require_cg(rag)
        ws = _ws()
        return {"workspace": ws,
                "flows": [f.to_dict() for f in flow_store.list(ws)]}

    @router.post("/flows", dependencies=[Depends(combined_auth)],
                 summary="Save a flow (validated); assigns the next version")
    async def save_flow(body: FlowRequest):
        _require_cg(rag)
        ws = _ws()
        try:
            flow = FlowDefinition.from_dict(body.flow)
            stored = flow_store.save(ws, flow)
        except (KeyError, ValueError) as e:
            raise HTTPException(status_code=400, detail=str(e))
        return {"workspace": ws, "saved": True, "flow": stored.to_dict()}

    @router.get("/flows/{flow_id}", dependencies=[Depends(combined_auth)],
                summary="Get one flow (optionally a pinned version)")
    async def get_flow(flow_id: str, version: Optional[int] = None):
        _require_cg(rag)
        ws = _ws()
        flow = flow_store.get(ws, flow_id, version)
        if flow is None:
            raise HTTPException(status_code=404, detail=f"no flow '{flow_id}'")
        return flow.to_dict()

    @router.delete("/flows/{flow_id}", dependencies=[Depends(combined_auth)],
                   summary="Delete a flow (all versions)")
    async def delete_flow(flow_id: str):
        _require_cg(rag)
        return {"deleted": flow_store.delete(_ws(), flow_id)}

    @router.post("/flows/{flow_id}/dry-run", dependencies=[Depends(combined_auth)],
                 summary="Start a run from a synthetic event")
    async def dry_run(flow_id: str, body: DryRunRequest):
        _require_cg(rag)
        ws = _ws()
        flow = flow_store.get(ws, flow_id)
        if flow is None:
            raise HTTPException(status_code=404, detail=f"no flow '{flow_id}'")
        event = Event(
            type=body.event_type or flow.on_event,
            payload=dict(body.payload),
            source="dry-run",
            workspace=ws,
        )
        try:
            run = await executor.start(ws, flow, event=event, vars=body.vars)
        except Exception as e:
            logger.error(f"flow dry-run '{flow_id}' failed: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))
        return run.to_dict()

    # -- runs ---------------------------------------------------------------

    @router.get("/runs", dependencies=[Depends(combined_auth)],
                summary="List runs (filter by status / app_id)")
    async def list_runs(status: Optional[str] = None, app_id: Optional[str] = None):
        _require_cg(rag)
        ws = _ws()
        runs = await executor.run_store.list(ws, app_id=app_id, status=status)
        return {"workspace": ws, "count": len(runs),
                "runs": [r.to_dict() for r in runs]}

    @router.get("/runs/{run_id}", dependencies=[Depends(combined_auth)],
                summary="Get one run")
    async def get_run(run_id: str):
        _require_cg(rag)
        run = await executor.run_store.get(_ws(), run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"no run '{run_id}'")
        return run.to_dict()

    @router.get("/runs/{run_id}/replay", dependencies=[Depends(combined_auth)],
                summary="Replay a run's history against its pinned flow")
    async def replay_run(run_id: str):
        _require_cg(rag)
        ws = _ws()
        run = await executor.run_store.get(ws, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"no run '{run_id}'")
        result = await executor.replay(ws, run)
        return {
            "run_id": run_id,
            "ok": result.ok,
            "status": result.status,
            "state": result.state,
            "path": result.path,
            "mismatches": result.mismatches,
        }

    logger.info("Flow engine API routes registered")
    return router
