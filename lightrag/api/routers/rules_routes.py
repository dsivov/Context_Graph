"""Business Rules API — manage and dry-run a workspace's rules gate (step 7).

Endpoints (all workspace-scoped via the ``LIGHTRAG-WORKSPACE`` header):

  GET    /rules            — summary: enabled, version, concepts, parsed rules
  POST   /rules            — set/replace the workspace policy (dsl + concepts); validates
  POST   /rules/evaluate   — dry-run the saved policy against a sample decision
  POST   /rules/toggle     — enable/disable the gate for the workspace
  DELETE /rules            — remove the workspace policy
  POST   /rules/generate   — NL → DSL (step 6; returns 501 until built)

Mutations attach the rebuilt gate to the live workspace instance, so the next
``/graph/decision/emit`` enforces the new policy immediately.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from lightrag.api.utils_api import get_combined_auth_dependency
from lightrag.api.routers.context_graph_routes import (
    RelationContextData,
    _pydantic_to_rc,
)
from lightrag.utils import logger


# ─────────────────────────────────────────────────────────────────────────────
# Models
# ─────────────────────────────────────────────────────────────────────────────


class RulePolicyRequest(BaseModel):
    dsl: str = Field(description="The business-rule DSL (when/then rules).")
    concepts: Dict[str, List[str]] = Field(
        default_factory=dict,
        description="Concept catalog: name → example phrases for sim() matching.",
    )
    enabled: bool = Field(default=True, description="Whether the gate is active.")
    model_id: Optional[str] = Field(
        default=None, description="Override the pinned similarity model id."
    )


class RuleInfo(BaseModel):
    name: str
    priority: int = 0


class RuleSummaryResponse(BaseModel):
    workspace: str
    exists: bool
    enabled: bool
    version: Optional[int] = None
    model_id: Optional[str] = None
    updated_at: Optional[float] = None
    concepts: List[str] = Field(default_factory=list)
    rules: List[RuleInfo] = Field(default_factory=list)


class EvaluateRequest(BaseModel):
    src: str
    tgt: str
    relation_type: str
    relation_context: RelationContextData
    as_of: Optional[str] = Field(
        default=None, description="ISO date for is_active evaluation (default today)."
    )


class EvaluateResponse(BaseModel):
    active: bool = Field(description="False if no enabled gate exists for the workspace.")
    outcome: Optional[str] = Field(default=None, description="PASS / FLAG / REJECT.")
    audit: Optional[Dict[str, Any]] = None
    triggered: List[Dict[str, Any]] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    notes: List[str] = Field(default_factory=list)


class ToggleRequest(BaseModel):
    enabled: bool


class GenerateRequest(BaseModel):
    policy: str = Field(description="The policy to encode, in plain English.")
    concepts: Dict[str, List[str]] = Field(
        default_factory=dict,
        description="Seed concept catalog; merged with the workspace's existing concepts.",
    )
    use_stored_concepts: bool = Field(
        default=True, description="Seed generation with the workspace's saved concepts."
    )
    max_repairs: int = Field(default=1, ge=0, le=3,
                             description="Auto-repair rounds if a draft fails validation.")
    save: bool = Field(
        default=False,
        description="If true and the draft is valid, persist + enable it (default: review-only).",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Router factory
# ─────────────────────────────────────────────────────────────────────────────


def _require_rules_capable(rag) -> None:
    # A ContextGraph instance carries a ``rules_gate`` slot; plain LightRAG doesn't.
    if not hasattr(rag, "rules_gate"):
        raise HTTPException(
            status_code=503,
            detail="Rules require Context Graph mode. Set USE_CONTEXT_GRAPH=true.",
        )


def create_rules_routes(rag, service, *, api_key: Optional[str] = None,
                        workspace_resolver=None):
    """Build the /rules router bound to *rag* and a RulesService.

    workspace_resolver: callable returning the current workspace name; defaults
    to the request-scoped ``LIGHTRAG-WORKSPACE`` contextvar.
    """
    if workspace_resolver is None:
        from lightrag.api.workspace_pool import _current_workspace

        def workspace_resolver():
            return _current_workspace.get()

    router = APIRouter(tags=["rules"])
    combined_auth = get_combined_auth_dependency(api_key)

    def _ws() -> str:
        return workspace_resolver() or "default"

    @router.get("/rules", response_model=RuleSummaryResponse,
                dependencies=[Depends(combined_auth)],
                summary="Summary of the workspace's rules policy")
    async def get_rules():
        _require_rules_capable(rag)
        return RuleSummaryResponse(**service.get_summary(_ws()))

    @router.post("/rules", response_model=RuleSummaryResponse,
                 dependencies=[Depends(combined_auth)],
                 summary="Set/replace the workspace's rules policy (validated)")
    async def set_rules(request: RulePolicyRequest):
        _require_rules_capable(rag)
        ws = _ws()
        try:
            service.save(ws, request.dsl, request.concepts,
                         enabled=request.enabled, model_id=request.model_id)
        except ValueError as e:
            # Invalid DSL / undefined concept → 400 (author error)
            raise HTTPException(status_code=400, detail=str(e))
        service.attach(rag, ws)  # live enforcement on the next emit
        return RuleSummaryResponse(**service.get_summary(ws))

    @router.post("/rules/evaluate", response_model=EvaluateResponse,
                 dependencies=[Depends(combined_auth)],
                 summary="Dry-run the saved policy against a sample decision")
    async def evaluate_rules(request: EvaluateRequest):
        _require_rules_capable(rag)
        rc = _pydantic_to_rc(request.relation_context)
        decision = service.evaluate(_ws(), request.src, request.tgt,
                                    request.relation_type, rc, as_of=request.as_of)
        if decision is None:
            return EvaluateResponse(active=False)
        result = decision.result
        triggered = [
            {"rule": m.rule, "severity": m.severity, "reason": m.reason,
             "matches": m.matches}
            for m in result.triggered
        ]
        return EvaluateResponse(
            active=True, outcome=decision.outcome, audit=decision.audit,
            triggered=triggered, warnings=result.warnings, notes=result.notes,
        )

    @router.post("/rules/toggle", response_model=RuleSummaryResponse,
                 dependencies=[Depends(combined_auth)],
                 summary="Enable/disable the workspace gate")
    async def toggle_rules(request: ToggleRequest):
        _require_rules_capable(rag)
        ws = _ws()
        try:
            service.set_enabled(ws, request.enabled)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"No policy for workspace '{ws}'.")
        service.attach(rag, ws)
        return RuleSummaryResponse(**service.get_summary(ws))

    @router.delete("/rules", dependencies=[Depends(combined_auth)],
                   summary="Delete the workspace's rules policy")
    async def delete_rules():
        _require_rules_capable(rag)
        ws = _ws()
        deleted = service.delete(ws)
        service.attach(rag, ws)  # detaches (gate becomes None)
        return {"deleted": deleted, "workspace": ws}

    @router.post("/rules/generate", dependencies=[Depends(combined_auth)],
                 summary="Generate (and optionally apply) DSL from a natural-language policy")
    async def generate_rules(request: GenerateRequest):
        _require_rules_capable(rag)
        from context_graph.rules.agent import RuleAuthor

        llm = getattr(rag, "llm_model_func", None)
        if llm is None:
            raise HTTPException(status_code=503, detail="No LLM is configured for this workspace.")

        ws = _ws()
        seed = dict(request.concepts)
        if request.use_stored_concepts:
            existing = service.store.load(ws)
            if existing is not None:
                seed = {**existing.concepts, **seed}

        author = RuleAuthor(llm, gate_backend=service.gate_backend)
        result = await author.generate(request.policy, concepts=seed,
                                       max_repairs=request.max_repairs)

        saved = False
        if request.save and result.valid:
            service.save(ws, result.dsl, result.concepts)
            service.attach(rag, ws)
            saved = True

        return {**result.to_dict(), "saved": saved}

    logger.info("Rules API routes registered")
    return router
