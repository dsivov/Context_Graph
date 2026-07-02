"""Workspace API — onboarding + the role-scoped operating manifest (P3).

  POST /onboard              — tailor + install a workspace, return per-role manifests
  GET  /workspace/manifest   — one role's operating context (RBAC-filtered actions)

The manifest assembles a single, role-scoped view of a workspace's installed
config so an agent gets its operating context in one call: object types, the
**actions it may invoke** (filtered by the role's RBAC grants), guardrails
(rules), lifecycle state machines, live skills, and MCP tools. A live view —
re-fetch it when the config changes.

``/onboard`` is the wizard: it uses the NL authors (OntologyAuthor / RuleAuthor)
to draft a **tailored** ontology + rules from a plain-English description, saves
them, seeds Role nodes, and returns the manifests. Generic — core learns no
roles/object types; it reflects whatever the description produced. Both require
Context Graph mode. See ``AGENTIC_PROJECT_GRAPH.html`` § Onboarding & the playbook.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from lightrag.api.utils_api import get_combined_auth_dependency
from lightrag.utils import logger


class OnboardRequest(BaseModel):
    description: str = Field(description="The project/domain to model, in plain English.")
    policy: Optional[str] = Field(default=None, description="Optional NL policy → methodology rules.")
    roles: List[str] = Field(default_factory=list, description="Role nodes to seed (empty for single-agent).")
    extend: bool = Field(default=False, description="Extend the workspace's existing ontology if present.")
    max_repairs: int = Field(default=1, ge=0, le=3)


def _require_cg(rag) -> None:
    if not hasattr(rag, "rules_gate"):
        raise HTTPException(
            status_code=503,
            detail="This endpoint requires Context Graph mode. Set USE_CONTEXT_GRAPH=true.",
        )


async def _skills_for(rag, role: Optional[str]) -> List[str]:
    """Best-effort: the skills a role carries (``role -has_skill-> Skill``)."""
    if not role:
        return []
    try:
        graph = rag.chunk_entity_relation_graph
        edges = await graph.get_node_edges(role)
        if not edges:
            return []
        skills: List[str] = []
        for src, tgt in edges:
            other = tgt if src == role else src
            edge = await graph.get_edge(role, other)
            if edge and "has_skill" in (edge.get("keywords", "") or ""):
                skills.append(other)
        return skills
    except Exception:  # pragma: no cover - manifest is best-effort
        return []


async def build_manifest(rag, ws: str, role: Optional[str], *, ontology_service=None,
                         action_service=None, rules_service=None, lifecycle_service=None,
                         rbac_service=None) -> Dict[str, Any]:
    """Assemble a role-scoped manifest from the installed services."""
    out: Dict[str, Any] = {"workspace": ws, "role": role}

    if ontology_service is not None:
        s = ontology_service.get_summary(ws)
        out["object_types"] = [o["name"] for o in s.get("object_types", [])] if s.get("exists") else []

    actions: List[Dict[str, Any]] = []
    if action_service is not None:
        s = action_service.get_summary(ws)
        for a in s.get("actions", []):
            if rbac_service is not None and role is not None:
                if not rbac_service.check(ws, role, "invoke", a["name"], rag=rag).allowed:
                    continue
            actions.append({"name": a["name"], "object_type": a.get("object_type", ""),
                            "effect": a.get("effect", ""), "params": a.get("params", [])})
    out["actions"] = actions

    if rules_service is not None:
        s = rules_service.get_summary(ws)
        out["guardrails"] = [r["name"] for r in s.get("rules", [])] if s.get("exists") else []

    if lifecycle_service is not None:
        s = lifecycle_service.get_summary(ws)
        out["lifecycle"] = s.get("machines", {}) if s.get("exists") else {}

    out["skills"] = await _skills_for(rag, role)
    out["mcp"] = {"tools": ["query", "emit", "invoke"]}
    return out


def create_workspace_routes(rag, *, ontology_service=None, action_service=None,
                            rules_service=None, lifecycle_service=None, rbac_service=None,
                            api_key: Optional[str] = None, workspace_resolver=None):
    """Build the /workspace + /onboard router from the installed services."""
    if workspace_resolver is None:
        from lightrag.api.workspace_pool import _current_workspace

        def workspace_resolver():
            return _current_workspace.get()

    router = APIRouter(tags=["workspace"])
    combined_auth = get_combined_auth_dependency(api_key)

    def _ws() -> str:
        return workspace_resolver() or "default"

    def _services() -> Dict[str, Any]:
        return dict(ontology_service=ontology_service, action_service=action_service,
                    rules_service=rules_service, lifecycle_service=lifecycle_service,
                    rbac_service=rbac_service)

    @router.get("/workspace/manifest", dependencies=[Depends(combined_auth)],
                summary="Role-scoped operating manifest for the workspace")
    async def manifest(role: Optional[str] = None):
        _require_cg(rag)
        return await build_manifest(rag, _ws(), role, **_services())

    @router.post("/onboard", dependencies=[Depends(combined_auth)],
                 summary="Onboard a workspace: tailor + install config, return manifests")
    async def onboard(request: OnboardRequest):
        _require_cg(rag)
        ws = _ws()
        llm = getattr(rag, "llm_model_func", None)
        if llm is None or ontology_service is None:
            raise HTTPException(status_code=503,
                                detail="Onboarding needs an LLM and the ontology service.")

        # 1) Tailored ontology from the description (NL author).
        from context_graph.ontology.agent import OntologyAuthor
        base = ontology_service.store.load(ws) if request.extend else None
        onto = await OntologyAuthor(llm).generate(
            request.description, base=base, max_repairs=request.max_repairs)
        onto_saved = False
        if onto.valid:
            ontology_service.save(ws, onto.ontology)
            onto_saved = True

        # 2) Optional tailored rules from a plain-English policy.
        rules_out = None
        if request.policy and rules_service is not None:
            from context_graph.rules.agent import RuleAuthor
            r = await RuleAuthor(llm).generate(request.policy, max_repairs=request.max_repairs)
            rsaved = False
            if r.valid:
                rules_service.save(ws, {"dsl": r.dsl, "concepts": r.concepts, "enabled": True})
                rsaved = True
            rules_out = {"valid": r.valid, "saved": rsaved, "attempts": r.attempts, "errors": r.errors}

        # 3) Seed Role nodes (best-effort).
        seeded: List[str] = []
        for role in request.roles:
            try:
                await rag.chunk_entity_relation_graph.upsert_node(role, {
                    "entity_id": role, "entity_type": "Role", "source_id": "onboard",
                    "description": f"{role} role", "file_path": "onboard"})
                seeded.append(role)
            except Exception as e:  # pragma: no cover - best-effort
                logger.warning(f"onboard role seed failed for '{role}': {e}")

        # 4) Manifests: one per role, plus a default (no-role) view.
        manifests: Dict[str, Any] = {}
        for role in request.roles:
            manifests[role] = await build_manifest(rag, ws, role, **_services())
        manifests["_default"] = await build_manifest(rag, ws, None, **_services())

        onto_types = [o["name"] for o in (onto.ontology or {}).get("object_types", [])]
        return {
            "workspace": ws,
            "ontology": {"valid": onto.valid, "saved": onto_saved, "attempts": onto.attempts,
                         "object_types": onto_types, "errors": onto.errors},
            "rules": rules_out,
            "roles_seeded": seeded,
            "manifests": manifests,
        }

    logger.info("Workspace manifest + onboard API routes registered")
    return router
