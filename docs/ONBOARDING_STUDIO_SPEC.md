# Onboarding Studio — conversational project setup (spec / working draft)

**Status:** proposal, awaiting go-ahead. Written after the idea to add a chat-like
onboarding step to the WebUI. Not yet implemented.

## The idea

Add a WebUI tab — **"Get Started"** — where the human project lead has a short guided
conversation with an AI that ends in a fully set-up workspace: a tailored ontology +
methodology rules, seeded roles, an **onboarding brief**, and a **first change request**
so the dev agent opens its very first session with a concrete starting point instead of
a blank graph.

It is the human-facing front door to everything we already built for agents
(`/onboard`, the served playbook/bootstrap, the NL authors, the action layer).

## Why (the problem it solves)

`POST /onboard` today is a **one-shot**: hand it a paragraph, it guesses an ontology +
rules. That step is the highest-leverage in the system — reuse checks, guardrails, and
actions all inherit their quality from it — and a single paragraph is where it is
weakest. A conversation turns the guess into an **interview** ("who are the actors? what
states does a task move through? what's a decision you'd want flagged?"), producing a
much better config from the same downstream machinery.

It also cleanly separates two personas we have been blurring:

| Persona | Surface | When |
|---|---|---|
| **Human lead** | this chat (WebUI) | sets up the workspace, once |
| **Dev agent(s)** | MCP + the served playbook/bootstrap | reads entry points, runs the loop |

## Flow

```
 Chat interview ──▶ structured proposal ──▶ human reviews / edits ──▶ Install ──▶ Handoff
  (drives the        (object types, rules,    (never auto-install       │           │
   questions,         roles, first-CR draft,    a hallucination)         │           │
   server LLM)        backfill hint)                                     ▼           ▼
                                                          /onboard (ontology+rules) +   show bootstrap
                                                          seed roles/RBAC +              (mcp config +
                                                          CreateChangeRequest (first CR) playbook link);
                                                          + save/ingest the brief        offer backfill cmd
```

The **review gate is mandatory** — the proposal is shown for edit/approval before
anything is installed.

## Endpoint contract

Keep it stateless like the rest of the system: the client holds the transcript and
sends it each turn.

### `POST /onboard/chat`
Runs one interview turn using the server's `llm_model_func`.

Request:
```json
{
  "messages": [{"role": "user", "content": "..."}, {"role":"assistant","content":"..."}],
  "repo_present": true                    // hints the interviewer to offer backfill
}
```
Response:
```json
{
  "assistant": "next question or summary text",
  "ready": false,                          // true when enough gathered to propose
  "proposal": null                          // populated once ready (schema below)
}
```

The system prompt drives a bounded interview (project purpose → actors/roles →
object types & their lifecycle → what decisions matter / what to flag → the first
concrete piece of work). When it has enough, it returns `ready:true` with a `proposal`.

### `POST /onboard/apply`
Installs an approved (possibly human-edited) proposal. Reuses existing pieces — this is
orchestration, not new logic:
1. ontology + rules → the existing `/onboard` path (`OntologyAuthor` result can be
   passed through, or the proposal's object_types/rules installed directly);
2. roles → seed `Role` nodes (+ RBAC policy if multi-agent);
3. **first CR** → invoke the `CreateChangeRequest` action (so it is governed + audited
   exactly like an agent's own CR, not a raw upsert);
4. **brief** → save the onboarding document and ingest it (so it is itself queryable);
5. return the **bootstrap bundle** (`build_bootstrap`) for the handoff.

Returns the same shape as `/onboard` today, plus `first_cr` and `brief_id`.

## Proposal schema

```json
{
  "workspace": "myproj",
  "brief": "2–5 paragraph project summary the interview produced (the onboarding document)",
  "description": "the distilled NL description fed to OntologyAuthor",
  "policy": "the distilled NL policy fed to RuleAuthor (optional)",
  "roles": ["developer"],                  // empty ⇒ single-agent, no RBAC
  "object_types_preview": ["Module","Task","ChangeRequest","ArchitectureDecisionRecord","Commit"],
  "rules_preview": ["new module - confirm reuse", "architecture decision needs rationale"],
  "first_cr": {
    "id": "cr-initial-<slug>",
    "title": "…",
    "description": "the first concrete piece of work the agent should pick up"
  },
  "backfill": { "recommended": true, "reason": "existing repo detected" }
}
```

Everything in the proposal is editable in the review UI before `apply`.

## The onboarding document

The `brief` is a first-class artifact — the human-readable "why we set it up this way."
Store it (KV) and ingest it through the normal pipeline so `query_auto("what is this
project / how is it meant to work")` answers from it. It is the conversational analogue
of the hand-authored `WELCOME_MESSAGE.md`, but generated and queryable.

## UI (new tab: "Get Started")

```
┌───────────────────────────────────────────────────────────┐
│  Get Started · workspace: myproj                          │
├──────────────────────────────┬────────────────────────────┤
│  chat transcript             │  Proposal (appears when     │
│  ────────────────            │  ready) — editable:         │
│  AI: What are we building?   │   • object types  [edit]    │
│  You: an OSINT investigator… │   • rules         [edit]    │
│  AI: Who works on it — one   │   • roles         [edit]    │
│      agent or a team?        │   • first CR      [edit]    │
│  …                           │   • ☑ run backfill          │
│  [ type a message…      ▷ ]  │   [ Review & Install ]      │
└──────────────────────────────┴────────────────────────────┘
        after install → bootstrap panel: .mcp.json + playbook link + backfill cmd
```

Reuses the existing chat components from the Retrieval tab where possible.

## Reuse map (little new logic)

| Piece | Source |
|---|---|
| NL → ontology | `context_graph/ontology/agent.py` `OntologyAuthor` |
| NL → rules | `context_graph/rules/agent.py` `RuleAuthor` |
| install | `POST /onboard` (`workspace_routes.py`) |
| seed first CR | `CreateChangeRequest` action (`context_graph/actions`) |
| roles / RBAC | onboard role seeding + `rbac` service |
| handoff | `build_bootstrap` / `/workspace/playbook` |
| chat UI | Retrieval-tab chat components |

**New:** `POST /onboard/chat` (interview turn), `POST /onboard/apply` (orchestrated
install + seed + brief), and the WebUI tab.

## Genericity guardrail

The interview must not over-fit a bespoke ontology per conversation. The system prompt
should steer toward the installed **preset vocabulary** (agentic-dev, CRM, etc.) and
only extend it with clear justification — otherwise every project drifts into a private
schema and the reuse/rules layers lose their shared meaning. Pick a preset early in the
interview; treat additions as the exception.

## MVP vs later

**MVP:** the tab, `/onboard/chat`, `/onboard/apply`, review gate, first-CR seed, brief
save+ingest, bootstrap handoff. Single-agent path first (no RBAC).

**Later:** multi-agent role/RBAC design in-chat; seed a starter task + ADR (not just a
CR); wire backfill to run from the UI (needs the repo path — likely a copy-paste command
rather than server execution, since the server can't reach the agent's filesystem);
"re-onboard / evolve" mode that extends an existing workspace.

## Open questions

1. First seed = a single CR, or CR + first task + a framing ADR?
2. Where does the interview LLM come from — always the server's `llm_model_func`
   (recommended, self-contained), or the WebUI's configured chat model?
3. Is the brief stored as a normal document (queryable, but mixes into the doc graph) or
   a dedicated KV record surfaced separately?
4. Should `apply` be reversible (an "undo onboarding" that drops the seeded nodes) for
   safe experimentation?
