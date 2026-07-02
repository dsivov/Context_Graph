# Solution Packs (Presets)

A **solution pack** turns generic Context Graph into a domain solution — an
agentic dev team, a deal desk, a compliance workflow — **using only data**. A
preset is a directory of JSON that you install into a workspace; the CG core
never learns the domain's vocabulary.

> **The genericity contract.** Core ships generic *mechanisms* (ontology, rules,
> actions, and — coming with P3 — RBAC and lifecycle). A use case is *config*.
> Core knows `object type · action · principal · grant · state machine`; it must
> never know `Task · Feature · Sprint · manager`. Those live in a preset.
>
> Litmus test for anything you're tempted to add to core: *would a different
> domain use it too?* If no, it's a preset, not code.

## Anatomy of a preset

A preset directory provides any of these files; the installer applies whichever
are present, **in dependency order**:

| File | Installs via | Depends on | What it defines |
|------|--------------|-----------|-----------------|
| `ontology.json` | `POST /ontology` | — | Object types, link types, typed properties (the vocabulary) |
| `rules.json` | `POST /rules` | ontology | The governance gate — DSL + concept catalog (advisory / blocking) |
| `actions.json` | `POST /actions` | ontology | Governed operations bound to object types |
| `seed.json` | `POST /graph/entity/create` · `/graph/relation/create` | ontology | Starter entities + relations so a fresh workspace has something to reason over |
| `rbac.json` | *(P3 RBAC layer)* | ontology, actions | Role → grants, opt-in and deny-by-default within a policy |
| `lifecycle.json` | *(P3 lifecycle layer)* | ontology, actions, rbac | State machines over object types + transition guards |

Each JSON may carry a top-level `_comment` (documentation only; the installer
strips it). Everything is **per-workspace and opt-in** — a single-agent project
can install just `ontology.json`, or nothing at all.

## Install

```bash
# from the repo root, with lightrag-server running
python presets/install.py --workspace my_project                 # apply everything present
python presets/install.py --workspace my_project --dry-run       # preview, no calls
python presets/install.py --workspace acme --preset agentic-dev --url http://localhost:9621
```

Auth: `--api-key` or `LIGHTRAG_API_KEY`. Ontology/rules/actions
replace-and-version on re-run; seed creation tolerates "already exists". The
installer is generic — it only reads which files a preset provides.

## Onboarding an existing project

Two paths turn a live workspace on:

- **Tailored** — `POST /onboard` with a plain-English description. The NL authors
  (OntologyAuthor / RuleAuthor) draft a project-specific ontology + rules, save
  them, seed Role nodes, and return a role-scoped manifest per role. Single-agent
  is zero-config (no roles, no RBAC).
- **Preset** — `python presets/install.py --workspace <ws>` applies a fixed pack.

For a project that predates the flow, **backfill** its history so a fresh
workspace already knows the reality:

```bash
python presets/backfill_git.py --repo /path/to/project --workspace <ws>
```

Two layers:

- **Structural** (deterministic, no LLM): source dirs → `Module`
  (`Developer -owns-> Module`), module dependencies, the git author →
  `Developer`, and recent commits → `Commit` with `Commit -touches-> Module`
  from the files each commit changed.
- **Semantic** (LLM extraction): `README` + `docs/*.md` are ingested through the
  normal pipeline, so the graph gains embedded, query-able chunks and the "why"
  (architecture, design records) is extracted with per-file provenance. Skip
  with `--no-docs`; cap with `--max-docs`.

## Authoring a new preset

1. `mkdir presets/<name>/` and add an `ontology.json` (start there — everything
   depends on it). The NL author can draft it: `POST /ontology/generate`.
2. Add `rules.json` (governance) and `actions.json` (operations) as needed —
   `POST /rules/generate` drafts rules from plain English.
3. Optionally add `seed.json` fixtures.
4. Validate without touching the graph: `POST /ontology/validate` for seed +
   extractions, `POST /rules/evaluate` for rule behavior.
5. `python presets/install.py --workspace <ws> --preset <name>`.

## Presets in this repo

| Preset | For | Status |
|--------|-----|--------|
| [`agentic-dev/`](agentic-dev/) | A multi-agent software-development team (grounded in [dsivov/ai_development_team](https://github.com/dsivov/ai_development_team/)) | ontology · rules · actions · seed ✅ · rbac · lifecycle ⏳ |

The design rationale lives in
[`../docs/AGENTIC_PROJECT_GRAPH.html`](../docs/AGENTIC_PROJECT_GRAPH.html) with a
visual [walkthrough](../docs/AGENTIC_DEV_WALKTHROUGH.html).
