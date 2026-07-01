"""Ontology API — manage a workspace's typed schema (P2).

Endpoints (all workspace-scoped via the ``LIGHTRAG-WORKSPACE`` header):

  GET    /ontology            — summary: name, version, object/link types, lint
  POST   /ontology            — set/replace the workspace ontology (validated)
  DELETE /ontology            — remove the workspace ontology
  POST   /ontology/generate   — NL → ontology (OntologyAuthor; optionally save)
  POST   /ontology/validate   — check extracted entities/relations vs the ontology

Mirrors the /rules router. Ontology is available only in Context Graph mode.
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


class OntologyRequest(BaseModel):
    ontology: Dict[str, Any] = Field(
        description="The ontology as JSON (name, object_types[], link_types[]).")


class PropertyInfo(BaseModel):
    name: str
    kind: str
    required: bool = False


class ObjectTypeInfo(BaseModel):
    name: str
    description: str = ""
    properties: List[PropertyInfo] = Field(default_factory=list)


class LinkTypeInfo(BaseModel):
    name: str
    source_types: List[str] = Field(default_factory=list)
    target_types: List[str] = Field(default_factory=list)
    cardinality: str = "N:M"
    property_count: int = 0


class OntologySummaryResponse(BaseModel):
    workspace: str
    exists: bool
    name: Optional[str] = None
    version: Optional[int] = None
    updated_at: Optional[float] = None
    object_types: List[ObjectTypeInfo] = Field(default_factory=list)
    link_types: List[LinkTypeInfo] = Field(default_factory=list)
    lint: List[str] = Field(default_factory=list)
    ontology: Optional[Dict[str, Any]] = None


class GenerateOntologyRequest(BaseModel):
    description: str = Field(description="The domain to model, in plain English.")
    extend: bool = Field(
        default=True, description="Extend the workspace's existing ontology if present.")
    max_repairs: int = Field(default=1, ge=0, le=3,
                             description="Auto-repair rounds if a draft is invalid.")
    save: bool = Field(
        default=False,
        description="If true and the draft is valid, persist it (default: review-only).")


class ValidateExtractionRequest(BaseModel):
    entities: List[Dict[str, Any]] = Field(default_factory=list)
    relations: List[Dict[str, Any]] = Field(default_factory=list)
    closed_world: bool = Field(
        default=False, description="If true, types not in the ontology are violations.")


# ─────────────────────────────────────────────────────────────────────────────
# Router factory
# ─────────────────────────────────────────────────────────────────────────────


def _require_cg(rag) -> None:
    # A ContextGraph instance carries a ``rules_gate`` slot; plain LightRAG doesn't.
    if not hasattr(rag, "rules_gate"):
        raise HTTPException(
            status_code=503,
            detail="Ontology requires Context Graph mode. Set USE_CONTEXT_GRAPH=true.",
        )


def create_ontology_routes(rag, service, *, api_key: Optional[str] = None,
                           workspace_resolver=None):
    """Build the /ontology router bound to *rag* and an OntologyService."""
    if workspace_resolver is None:
        from lightrag.api.workspace_pool import _current_workspace

        def workspace_resolver():
            return _current_workspace.get()

    router = APIRouter(tags=["ontology"])
    combined_auth = get_combined_auth_dependency(api_key)

    def _ws() -> str:
        return workspace_resolver() or "default"

    @router.get("/ontology", response_model=OntologySummaryResponse,
                dependencies=[Depends(combined_auth)],
                summary="Summary of the workspace's ontology")
    async def get_ontology():
        _require_cg(rag)
        return OntologySummaryResponse(**service.get_summary(_ws()))

    @router.post("/ontology", response_model=OntologySummaryResponse,
                 dependencies=[Depends(combined_auth)],
                 summary="Set/replace the workspace's ontology (validated)")
    async def set_ontology(request: OntologyRequest):
        _require_cg(rag)
        ws = _ws()
        try:
            service.save(ws, request.ontology)
        except (ValueError, KeyError, TypeError) as e:
            raise HTTPException(status_code=400, detail=str(e))
        return OntologySummaryResponse(**service.get_summary(ws))

    @router.delete("/ontology", dependencies=[Depends(combined_auth)],
                   summary="Delete the workspace's ontology")
    async def delete_ontology():
        _require_cg(rag)
        ws = _ws()
        return {"deleted": service.delete(ws), "workspace": ws}

    @router.post("/ontology/generate", dependencies=[Depends(combined_auth)],
                 summary="Generate (and optionally save) an ontology from a description")
    async def generate_ontology(request: GenerateOntologyRequest):
        _require_cg(rag)
        from context_graph.ontology.agent import OntologyAuthor

        llm = getattr(rag, "llm_model_func", None)
        if llm is None:
            raise HTTPException(status_code=503, detail="No LLM is configured for this workspace.")

        ws = _ws()
        base = service.store.load(ws) if request.extend else None
        result = await OntologyAuthor(llm).generate(
            request.description, base=base, max_repairs=request.max_repairs)

        saved = False
        if request.save and result.valid:
            service.save(ws, result.ontology)
            saved = True
        return {**result.to_dict(), "saved": saved}

    @router.post("/ontology/validate", dependencies=[Depends(combined_auth)],
                 summary="Validate extracted entities/relations against the ontology")
    async def validate_extraction(request: ValidateExtractionRequest):
        _require_cg(rag)
        result = service.validate_extraction(
            _ws(), request.entities, request.relations,
            closed_world=request.closed_world)
        if not result.get("exists"):
            raise HTTPException(status_code=404,
                                detail=f"No ontology for workspace '{_ws()}'.")
        return result

    logger.info("Ontology API routes registered")
    return router
