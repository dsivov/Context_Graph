"""Workspace API — the role-scoped operating manifest for agents (P3).

  GET /workspace/manifest?role=<role>

Assembles a single, role-scoped view of a workspace's installed config so an
agent gets its operating context in one call: the object types it works with,
the **actions it may invoke** (filtered by the role's RBAC grants), the
guardrails that may flag/reject it, the lifecycle state machines, and the MCP
tools. A live view — re-fetch it when the config changes. See
``AGENTIC_PROJECT_GRAPH.html`` § Onboarding & the playbook.

Generic: it knows nothing about specific roles or object types — it reflects
whatever the preset installed. Available only in Context Graph mode.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException

from lightrag.api.utils_api import get_combined_auth_dependency
from lightrag.utils import logger


def _require_cg(rag) -> None:
    if not hasattr(rag, "rules_gate"):
        raise HTTPException(
            status_code=503,
            detail="The manifest requires Context Graph mode. Set USE_CONTEXT_GRAPH=true.",
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


def create_workspace_routes(rag, *, ontology_service=None, action_service=None,
                            rules_service=None, lifecycle_service=None, rbac_service=None,
                            api_key: Optional[str] = None, workspace_resolver=None):
    """Build the /workspace router, assembling the manifest from the installed services."""
    if workspace_resolver is None:
        from lightrag.api.workspace_pool import _current_workspace

        def workspace_resolver():
            return _current_workspace.get()

    router = APIRouter(tags=["workspace"])
    combined_auth = get_combined_auth_dependency(api_key)

    def _ws() -> str:
        return workspace_resolver() or "default"

    @router.get("/workspace/manifest", dependencies=[Depends(combined_auth)],
                summary="Role-scoped operating manifest for the workspace")
    async def manifest(role: Optional[str] = None):
        _require_cg(rag)
        ws = _ws()
        out: Dict[str, Any] = {"workspace": ws, "role": role}

        # object types the workspace defines
        if ontology_service is not None:
            s = ontology_service.get_summary(ws)
            out["object_types"] = [o["name"] for o in s.get("object_types", [])] if s.get("exists") else []

        # actions — filtered by the role's RBAC grants (permissive if no policy / no role)
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

        # guardrails — the rules that may flag/reject a decision
        if rules_service is not None:
            s = rules_service.get_summary(ws)
            out["guardrails"] = [r["name"] for r in s.get("rules", [])] if s.get("exists") else []

        # lifecycle — the state machines the role's transitions must obey
        if lifecycle_service is not None:
            s = lifecycle_service.get_summary(ws)
            out["lifecycle"] = s.get("machines", {}) if s.get("exists") else {}

        out["skills"] = await _skills_for(rag, role)
        out["mcp"] = {"tools": ["query", "emit", "invoke"]}
        return out

    logger.info("Workspace manifest API routes registered")
    return router
