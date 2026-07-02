# Preset: `agentic-dev`

A **solution pack** that configures a Context Graph workspace for a multi-agent
software-development methodology (grounded in
[dsivov/ai_development_team](https://github.com/dsivov/ai_development_team/)).

This is **data, not core code** — Context Graph stays a generic product; the
agentic use case lives entirely as configuration installed into a workspace.
See the design discussion in [`../../docs/AGENTIC_PROJECT_GRAPH.html`](../../docs/AGENTIC_PROJECT_GRAPH.html).

## Pieces

| File | Installs via | Status |
|------|--------------|--------|
| `ontology.json` | `POST /ontology` | ✅ 10 object types, 14 link types, lints clean (identity is `entity_name`, so no redundant `name` property on Module/API/Skill) |
| `rules.json` | `POST /rules` | ✅ 4-rule methodology gate (reuse nudge, deprecation, arch rationale, low-confidence); validated live. Advisory by default (FLAG/NOTIFY) — hard invariants go to the lifecycle layer, access control to RBAC |
| `actions.json` | `POST /actions` | ✅ 6 governed operations (ProposeAPI, AdvanceTask, CreateChangeRequest, ApproveArchitecture, DeprecateAPI, MergeToMain). Relation types chosen so the gate fires; validated live end-to-end |
| `seed.json` | `POST /graph/entity/create` + `/graph/relation/create` | ✅ 22 entities (6 Roles, 6 Skills, 4 sample Modules, 6 sample APIs) + 21 relations (owns · exposes · depends_on · has_skill · applies_to). Validates 43/43 against the ontology |
| `rbac.json` | `POST /rbac` | ✅ role → grants for the six roles (manager `*`, production-engineer `invoke:MergeToMain`, …). Opt-in + deny-by-default within the policy; absent = permissive. Gates `/actions/invoke`; validated live |
| `lifecycle.json` | (P3 lifecycle) | ⏳ pending the lifecycle layer |

## Install

One command applies every piece present, in dependency order
(ontology → rules → actions → seed) via the generic installer:

```bash
# from the repo root, with lightrag-server running
python presets/install.py --workspace my_project
python presets/install.py --workspace my_project --dry-run    # preview, no calls
```

Options: `--preset <dir>` (default `agentic-dev`), `--url` (default
`http://localhost:9621`), `--api-key` (or `LIGHTRAG_API_KEY`). The installer is
generic — it knows nothing about roles or tasks, only which JSON files a preset
provides. Ontology/rules/actions replace-and-version on re-run; seed
entity/relation creation tolerates "already exists".

Everything here is opt-in and per-workspace; single-agent projects can ignore it.
