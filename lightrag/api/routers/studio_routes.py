"""Studio API — diff-and-approve authoring of governed artifacts (P3).

Endpoints (all workspace-scoped via the ``LIGHTRAG-WORKSPACE`` header):

  POST /studio/propose            — draft the next version of an artifact → a diff
  POST /studio/assess             — (re)compute behaviour_changed for a diff
  POST /studio/apply              — persist + sign off a diff → a new version
  POST /studio/revert             — re-apply a prior version's snapshot (signed)
  GET  /studio/artifacts          — every artifact the Studio tracks here
  GET  /studio/history/{kind}/{id}— the signed version ledger for one artifact

``propose`` returns an already-assessed diff (behaviour_changed set) for the UI;
``apply`` re-assesses server-side before enforcing sign-off, so the flag can't be
tampered with in transit. Available only in Context Graph mode.
See docs/PLATFORM_WORK_PLAN.md (P3).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from lightrag.api.utils_api import get_combined_auth_dependency
from lightrag.utils import logger


class ProposeRequest(BaseModel):
    kind: str = Field(description="ontology | rule | flow | action")
    artifact_id: str = Field(description="Artifact key (flow id, or a label).")
    draft: Optional[Dict[str, Any]] = Field(
        default=None, description="A ready artifact dict (deterministic authoring).")
    spec: Optional[str] = Field(
        default=None, description="Natural-language spec (ontology/rule; needs an LLM).")
    concepts: Optional[Dict[str, List[str]]] = Field(default=None)
    origin: str = Field(default="authoring", description="authoring | migration | reapproval")


class DiffBody(BaseModel):
    diff: Dict[str, Any] = Field(description="An ArtifactDiff as returned by /studio/propose.")


class ApplyRequest(BaseModel):
    diff: Dict[str, Any]
    approver: Optional[str] = None
    reason: Optional[str] = None
    role: Optional[str] = None


class RevertRequest(BaseModel):
    kind: str
    artifact_id: str
    to_version: int
    approver: str
    reason: str
    role: Optional[str] = None


def _require_cg(rag) -> None:
    if not hasattr(rag, "rules_gate"):
        raise HTTPException(
            status_code=503,
            detail="Studio requires Context Graph mode. Set USE_CONTEXT_GRAPH=true.",
        )


def create_studio_routes(rag, engine, *, api_key: Optional[str] = None,
                         workspace_resolver=None):
    """Build the /studio router bound to *rag* and a DiffEngine."""
    if workspace_resolver is None:
        from lightrag.api.workspace_pool import _current_workspace

        def workspace_resolver():
            return _current_workspace.get()

    from context_graph.studio.schema import ArtifactDiff

    router = APIRouter(tags=["studio"])
    combined_auth = get_combined_auth_dependency(api_key)

    def _ws() -> str:
        return workspace_resolver() or "default"

    @router.post("/studio/propose", dependencies=[Depends(combined_auth)],
                 summary="Draft the next version of an artifact as a diff")
    async def propose(body: ProposeRequest):
        _require_cg(rag)
        ws = _ws()
        try:
            diff = await engine.propose(
                ws, body.kind, body.artifact_id,
                draft=body.draft, spec=body.spec, concepts=body.concepts,
                origin=body.origin)
            engine.assess(ws, diff)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return {"diff": diff.to_dict()}

    @router.post("/studio/assess", dependencies=[Depends(combined_auth)],
                 summary="Recompute behaviour_changed for a diff")
    async def assess(body: DiffBody):
        _require_cg(rag)
        diff = ArtifactDiff.from_dict(body.diff)
        engine.assess(_ws(), diff)
        return {"diff": diff.to_dict()}

    @router.post("/studio/apply", dependencies=[Depends(combined_auth)],
                 summary="Persist + sign off a diff → a new version")
    async def apply(body: ApplyRequest):
        _require_cg(rag)
        ws = _ws()
        from context_graph.rules.gate import RuleViolation

        diff = ArtifactDiff.from_dict(body.diff)
        engine.assess(ws, diff)                     # re-assess server-side (anti-tamper)
        try:
            result = await engine.apply(
                ws, diff, approver=body.approver, reason=body.reason, role=body.role)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        except RuleViolation as e:
            raise HTTPException(status_code=422, detail={
                "status": "rejected", "outcome": "REJECT", "audit": e.decision.audit})
        return result

    @router.post("/studio/revert", dependencies=[Depends(combined_auth)],
                 summary="Re-apply a prior version's snapshot as a new signed version")
    async def revert(body: RevertRequest):
        _require_cg(rag)
        try:
            return await engine.revert(
                _ws(), body.kind, body.artifact_id, body.to_version,
                approver=body.approver, reason=body.reason, role=body.role)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @router.get("/studio/artifacts", dependencies=[Depends(combined_auth)],
                summary="Every artifact the Studio tracks in this workspace")
    async def artifacts():
        _require_cg(rag)
        ws = _ws()
        return {"workspace": ws, "artifacts": engine.artifacts(ws)}

    @router.get("/studio/history/{kind}/{artifact_id}",
                dependencies=[Depends(combined_auth)],
                summary="The signed version ledger for one artifact")
    async def history(kind: str, artifact_id: str):
        _require_cg(rag)
        ws = _ws()
        return {"workspace": ws, "kind": kind, "artifact_id": artifact_id,
                "history": engine.history(ws, kind, artifact_id)}

    logger.info("Studio API routes registered")
    return router
