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

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from lightrag.api.utils_api import get_combined_auth_dependency
from lightrag.utils import logger


class OnboardRequest(BaseModel):
    description: str = Field(description="The project/domain to model, in plain English.")
    policy: Optional[str] = Field(default=None, description="Optional NL policy → methodology rules.")
    roles: List[str] = Field(default_factory=list, description="Role nodes to seed (empty for single-agent).")
    extend: bool = Field(default=False, description="Extend the workspace's existing ontology if present.")
    max_repairs: int = Field(default=1, ge=0, le=3)


# --- Conversational onboarding (the "Get Started" tab) ---------------------- #

class ChatMessage(BaseModel):
    role: str
    content: str


class OnboardChatRequest(BaseModel):
    messages: List[ChatMessage] = Field(default_factory=list, description="Transcript so far (client-held).")
    repo_present: bool = Field(default=False, description="Hint the interviewer to offer backfill.")


class FirstCR(BaseModel):
    id: str
    title: str
    description: str = ""


class OnboardProposal(BaseModel):
    workspace: Optional[str] = None
    brief: str = ""
    description: str = ""
    policy: Optional[str] = None
    roles: List[str] = Field(default_factory=list)
    object_types_preview: List[str] = Field(default_factory=list)
    rules_preview: List[str] = Field(default_factory=list)
    first_cr: Optional[FirstCR] = None
    backfill: Dict[str, Any] = Field(default_factory=dict)


class OnboardApplyRequest(BaseModel):
    proposal: OnboardProposal


_INTERVIEW_PROMPT = """You are the onboarding interviewer for Context Graph — a \
decision-aware knowledge graph that gives a project shared memory and a methodology it \
enforces. You are talking to the human lead setting up the workspace `{ws}`.

Through a SHORT conversation (aim for 4–6 exchanges), gather just enough to set the \
project up:
- what the project is and its purpose;
- who works on it — a single agent, or a team (and the role names);
- the main object types and their lifecycle (e.g. Task: pending→completed; \
ChangeRequest: open→in progress→closed);
- what decisions matter / what should be flagged for review;
- ONE concrete first piece of work to seed as the first change request.

Guidance:
- Ask ONE focused question at a time. Be concise and friendly.
- Prefer a standard preset vocabulary (for software teams: Module, Task, ChangeRequest, \
ArchitectureDecisionRecord, Commit, Developer). Only introduce a bespoke type with a \
clear reason — genericity matters.
- {repo_hint}

When — and only when — you have enough, STOP asking questions. Write a one-sentence \
summary, then output a single fenced json block EXACTLY in this shape and nothing after it:

```json
{{
  "brief": "2-4 sentence project summary (the onboarding document)",
  "description": "a plain-English paragraph describing the project's object types and \
relationships, to author the ontology from",
  "policy": "a plain-English paragraph of the methodology rules to enforce, or null",
  "roles": ["developer"],
  "object_types_preview": ["Module", "Task", "ChangeRequest"],
  "rules_preview": ["new module - confirm reuse"],
  "first_cr": {{"id": "cr-initial-<slug>", "title": "...", "description": "..."}},
  "backfill": {{"recommended": false, "reason": ""}}
}}
```
Never emit the json block while still asking questions."""


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


# --------------------------------------------------------------------------- #
# Greenfield onboarding: the server serves its own playbook + bootstrap bundle #
# so a brand-new team's agent can pull everything it needs from one URL,       #
# instead of us hand-copying guide files into every repo.                      #
# --------------------------------------------------------------------------- #

def _server_url(request: Request) -> str:
    """Public base URL to embed in the mcp config / script commands.

    Honors ``LIGHTRAG_PUBLIC_URL`` (set it when the server sits behind a proxy),
    else derives from the incoming request.
    """
    override = os.environ.get("LIGHTRAG_PUBLIC_URL")
    return (override or str(request.base_url)).rstrip("/")


def _split_proposal(text: str) -> tuple[str, Optional[Dict[str, Any]]]:
    """Split an interview reply into (prose, proposal-or-None).

    The interviewer emits a fenced ```json block once it has gathered enough. Return
    the text before the block plus the parsed proposal; if there's no valid block the
    turn is still a question, so proposal is None.
    """
    import json
    import re

    m = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
    if not m:
        return text.strip(), None
    try:
        proposal = json.loads(m.group(1))
    except (ValueError, TypeError):
        return text.strip(), None
    prose = text[: m.start()].strip()
    return prose, proposal


def build_bootstrap(ws: str, role: Optional[str], server_url: str) -> Dict[str, Any]:
    """Machine-readable bootstrap bundle — what an agent needs to init itself."""
    q = f"?role={role}" if role else ""
    return {
        "workspace": ws,
        "role": role,
        "server_url": server_url,
        "mcp_config": {
            "mcpServers": {
                "context-graph": {
                    "type": "http",
                    "url": f"{server_url}/mcp",
                    "headers": {"LIGHTRAG-WORKSPACE": ws},
                }
            }
        },
        "playbook_url": f"{server_url}/workspace/playbook{q}",
        "manifest_url": f"{server_url}/workspace/manifest{q}",
        "backfill": {
            "script_url": f"{server_url}/workspace/backfill-script",
            "cmd": (f"curl -s {server_url}/workspace/backfill-script | "
                    f"python - --repo . --workspace {ws} --code"),
            "when": ("Run only if this repo already has source/docs (a fork, or an "
                     "in-progress codebase). It imports modules, the git author, recent "
                     "commits, docs, and — with --code — the source itself. Skip for an "
                     "empty repo."),
        },
        "next_steps": [
            "Write mcp_config to .mcp.json at your repo root, then (re)connect the MCP client.",
            "If this repo already has code/docs, run backfill.cmd so the graph knows your project from day one.",
            "Read playbook_url — it lists the object types, actions, and guardrails installed for THIS workspace.",
            "Start the loop: query before you build, record the why, use governed actions.",
        ],
    }


def _playbook_md(ws: str, role: Optional[str], m: Dict[str, Any], server_url: str) -> str:
    """Render a live, workspace-accurate operating playbook as Markdown."""
    q = f"?role={role}" if role else ""
    mcp_block = (
        "{\n"
        '  "mcpServers": {\n'
        '    "context-graph": {\n'
        '      "type": "http",\n'
        f'      "url": "{server_url}/mcp",\n'
        '      "headers": { "LIGHTRAG-WORKSPACE": "' + ws + '" }\n'
        "    }\n"
        "  }\n"
        "}"
    )

    object_types = m.get("object_types") or []
    actions = m.get("actions") or []
    guardrails = m.get("guardrails") or []
    lifecycle = m.get("lifecycle") or {}

    def _bullets(items: List[str], empty: str) -> str:
        return "\n".join(f"- `{i}`" for i in items) if items else f"_{empty}_"

    # actions rendered with their params so the agent knows the call shape
    if actions:
        arows = []
        for a in actions:
            params = ", ".join(
                f"{p['name']}{'*' if p.get('required') else ''}:{p.get('kind', 'text')}"
                for p in a.get("params", [])
            ) or "—"
            arows.append(f"| `{a['name']}` | `{a.get('object_type', '') or '—'}` | {params} | {a.get('effect', '') or ''} |")
        actions_tbl = ("| Action | Object type | Params (`*`=required) | Effect |\n"
                       "|---|---|---|---|\n" + "\n".join(arows))
    else:
        actions_tbl = "_No actions installed yet. Run `POST /onboard` or install a preset._"

    if lifecycle:
        lc_lines = []
        for obj, machine in lifecycle.items():
            states = machine.get("states") if isinstance(machine, dict) else None
            state_str = ", ".join(f"`{s}`" for s in states) if states else "state machine installed"
            lc_lines.append(f"- **{obj}**: {state_str}")
        lifecycle_md = "\n".join(lc_lines)
    else:
        lifecycle_md = "_No lifecycle state machines installed._"

    role_line = f" · role **{role}**" if role else " · single-agent (no role)"

    return f"""# Context Graph — Start here

**Workspace:** `{ws}`{role_line}

Context Graph (CG) is this project's **shared, decision-aware memory**. It already
knows (or can be told) your modules, commits, and architecture, and it records *why*
things were decided so a later session inherits the reasoning instead of rebuilding
or second-guessing it. This playbook is generated live from **your** workspace's
installed config — the object types, actions, and guardrails below are the real ones.

---

## 1 · Wire up MCP (one time)

Everything — querying, recording decisions, discovering what you can do, invoking
governed actions — is a single **MCP** tool surface. Write this to `.mcp.json` at your
repo root, then (re)connect your MCP client:

```json
{mcp_block}
```

(The same operations also exist as REST endpoints under `{server_url}` if you ever want to `curl` them.)

## 2 · Backfill existing work — *if this isn't an empty repo*

Starting from a fork, or a codebase already in progress? Import its reality so the graph
knows it from day one (modules, the git author, recent commits and the modules they
touched, README/docs, and with `--code` the source itself):

```bash
curl -s {server_url}/workspace/backfill-script | python - --repo . --workspace {ws} --code
```

Skip this step for a brand-new empty project. Re-running is safe (idempotent upserts).

## 3 · Your operating manifest

Machine-readable view: `GET {server_url}/workspace/manifest{q}`

**Object types you work with:**
{_bullets(object_types, "None installed yet — run POST /onboard to tailor an ontology.")}

**Actions you may invoke** (via the `invoke_action` MCP tool; discover live with `get_manifest`):

{actions_tbl}

**Guardrails — the methodology gate** (advisory unless a rule rejects):
{_bullets(guardrails, "No rules installed — decisions are recorded without gating.")}

**Lifecycle state machines** (illegal transitions are refused with `409`):
{lifecycle_md}

## 4 · The three habits (in order of importance)

1. **Query before you build.** Never assume something doesn't exist — ask CG first.
   `query_auto("Is there already a module that does X? Why is it like this?")`.
   A recorded decision is query-able immediately: `query_auto("explain <adr-slug>")`
   returns it with full rationale (CG blends the decision store into every answer).
2. **Record the why, not the keystroke.** When you make a choice worth remembering —
   a design decision, a tech pick, an API contract, a **rejected** option — capture the
   *why* via `record_decision(...)` or a typed action. Filter: *if you can't say who
   decided it and why, it's telemetry, not memory.*
3. **Use governed actions for standard moves.** Discover them with `get_manifest`, then
   `invoke_action(...)` — each call is validated, may be flagged by the gate, and is
   written to the graph as an audit record.

## 5 · Read the signals

- **`PASS`** — recorded, nothing to review.
- **`FLAG`** — *advice*, not a block (e.g. a reuse check saying "confirm nothing already
  covers this"). Run a `query_auto` to check, then proceed.
- **`409` illegal transition** — the lifecycle state machine refused an illegal jump.
- **`403`** — RBAC: your role may not invoke that action.

## 6 · Help us validate the methodology 🙏

This may be an early run for your team. Use CG naturally, and **tell us when it gets in
your way** — a tool that errors or confuses, a query that *should* have found something
but didn't, a `FLAG`/`409` that felt wrong, a missing or awkwardly-named action, or this
playbook being unclear. Keep a short running `cg/FEEDBACK.md` — ten small annoyances beat
none. Friction is exactly the data we need.

Build well — and grumble freely.
"""


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

    @router.get("/workspace/bootstrap", dependencies=[Depends(combined_auth)],
                summary="Everything a new agent needs to init itself (mcp config, backfill, links)")
    async def bootstrap(request: Request, role: Optional[str] = None):
        _require_cg(rag)
        return build_bootstrap(_ws(), role, _server_url(request))

    @router.get("/workspace/playbook", dependencies=[Depends(combined_auth)],
                summary="Live, workspace-accurate operating playbook (Markdown)")
    async def playbook(request: Request, role: Optional[str] = None, format: str = "md"):
        _require_cg(rag)
        ws = _ws()
        m = await build_manifest(rag, ws, role, **_services())
        md = _playbook_md(ws, role, m, _server_url(request))
        if format == "json":
            return {"workspace": ws, "role": role, "playbook_md": md}
        return PlainTextResponse(md, media_type="text/markdown; charset=utf-8")

    @router.get("/workspace/backfill-script",
                summary="The backfill script an agent runs against its own repo")
    async def backfill_script():
        path = Path(__file__).resolve().parents[3] / "presets" / "backfill_git.py"
        if not path.is_file():
            raise HTTPException(
                status_code=404,
                detail="backfill script not found on the server; fetch presets/backfill_git.py "
                       "from the Context Graph repository instead.")
        return PlainTextResponse(path.read_text(encoding="utf-8"),
                                 media_type="text/x-python; charset=utf-8")

    @router.post("/onboard/chat", dependencies=[Depends(combined_auth)],
                 summary="One turn of the conversational onboarding interview")
    async def onboard_chat(req: OnboardChatRequest):
        _require_cg(rag)
        llm = getattr(rag, "llm_model_func", None)
        if llm is None:
            raise HTTPException(status_code=503, detail="Onboarding chat needs an LLM.")
        ws = _ws()
        repo_hint = ("This project already has a repo — when you propose, set "
                     "backfill.recommended true so its history gets imported."
                     if req.repo_present else
                     "If the project already has code/docs, ask, and set backfill.recommended accordingly.")
        system = _INTERVIEW_PROMPT.format(ws=ws, repo_hint=repo_hint)

        history = [{"role": m.role, "content": m.content} for m in req.messages]
        prompt = history.pop()["content"] if history else "Begin the onboarding interview."
        try:
            reply = await llm(prompt, system_prompt=system, history_messages=history)
        except Exception as e:  # pragma: no cover - surface LLM errors cleanly
            raise HTTPException(status_code=502, detail=f"interview LLM error: {e}")

        assistant, proposal = _split_proposal(reply if isinstance(reply, str) else str(reply))
        if proposal is not None:
            proposal["workspace"] = ws  # never trust a model-invented workspace
        return {"assistant": assistant, "ready": proposal is not None, "proposal": proposal}

    @router.post("/onboard/apply", dependencies=[Depends(combined_auth)],
                 summary="Install an approved onboarding proposal (config + first CR + brief)")
    async def onboard_apply(req: OnboardApplyRequest, http_request: Request):
        _require_cg(rag)
        ws = _ws()
        p = req.proposal
        llm = getattr(rag, "llm_model_func", None)
        if llm is None or ontology_service is None:
            raise HTTPException(status_code=503, detail="Apply needs an LLM and the ontology service.")

        # 1) Tailored ontology from the distilled description.
        from context_graph.ontology.agent import OntologyAuthor
        onto = await OntologyAuthor(llm).generate(p.description, max_repairs=1)
        onto_saved = False
        if onto.valid:
            ontology_service.save(ws, onto.ontology)
            onto_saved = True

        # 2) Optional tailored rules from the distilled policy.
        rules_out = None
        if p.policy and rules_service is not None:
            from context_graph.rules.agent import RuleAuthor
            r = await RuleAuthor(llm).generate(p.policy, max_repairs=1)
            rsaved = False
            if r.valid:
                rules_service.save(ws, r.dsl, r.concepts, enabled=True)
                rsaved = True
            rules_out = {"valid": r.valid, "saved": rsaved, "errors": r.errors}

        # 3) Seed Role nodes.
        seeded: List[str] = []
        for role in p.roles:
            try:
                await rag.chunk_entity_relation_graph.upsert_node(role, {
                    "entity_id": role, "entity_type": "Role", "source_id": "onboard",
                    "description": f"{role} role", "file_path": "onboard"})
                seeded.append(role)
            except Exception as e:  # pragma: no cover
                logger.warning(f"onboard role seed failed for '{role}': {e}")

        # 4) Seed the first change request as a governed, audited edge so the agent
        #    opens its first session with a concrete starting point.
        first_cr = None
        if p.first_cr is not None:
            from lightrag.context_graph_types import RelationContext
            cr = p.first_cr
            trace = cr.title if not cr.description else f"{cr.title} — {cr.description}"
            rc = RelationContext(
                decision_trace=trace, approved_by="onboarding", approved_via="system",
                provenance="action:CreateChangeRequest",
                supporting_sentences=[cr.description] if cr.description else [],
                confidence_score=1.0)
            try:
                await rag.emit_decision_trace("lead", cr.id, "cr-created", rc)
                first_cr = {"id": cr.id, "title": cr.title}
            except Exception as e:  # pragma: no cover - never fail apply on the seed
                logger.warning(f"onboard first-CR seed failed: {e}")

        # 5) Save + ingest the onboarding brief so it's itself query-able.
        brief_id = None
        if p.brief and hasattr(rag, "ingest_decision_summary"):
            try:
                brief_id = await rag.ingest_decision_summary(p.brief, category="onboarding")
            except Exception as e:  # pragma: no cover
                logger.warning(f"onboard brief ingest failed: {e}")

        server_url = _server_url(http_request)
        return {
            "workspace": ws,
            "ontology": {"valid": onto.valid, "saved": onto_saved,
                         "object_types": [o["name"] for o in (onto.ontology or {}).get("object_types", [])]},
            "rules": rules_out,
            "roles_seeded": seeded,
            "first_cr": first_cr,
            "brief_id": brief_id,
            "bootstrap": build_bootstrap(ws, p.roles[0] if p.roles else None, server_url),
        }

    @router.post("/onboard", dependencies=[Depends(combined_auth)],
                 summary="Onboard a workspace: tailor + install config, return manifests")
    async def onboard(request: OnboardRequest, http_request: Request):
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
                rules_service.save(ws, r.dsl, r.concepts, enabled=True)
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
        server_url = _server_url(http_request)
        primary_role = request.roles[0] if request.roles else None
        return {
            "workspace": ws,
            "ontology": {"valid": onto.valid, "saved": onto_saved, "attempts": onto.attempts,
                         "object_types": onto_types, "errors": onto.errors},
            "rules": rules_out,
            "roles_seeded": seeded,
            "manifests": manifests,
            # Hand the agent its entry points: read the playbook, init from bootstrap.
            "bootstrap": build_bootstrap(ws, primary_role, server_url),
        }

    logger.info("Workspace manifest + onboard API routes registered")
    return router
