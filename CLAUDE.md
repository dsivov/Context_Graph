# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is **Context_Graph** — a fork of [LightRAG](https://github.com/HKUDS/LightRAG) that extends triple-based knowledge graphs `(h, r, t)` into contextual quadruples `(h, r, t, rc)`. The `RelationContext` (rc) captures decision lineage: who approved it, why, via which channel, under which policy, and validity period.

Upstream repo: https://github.com/dsivov/Context_Graph

## Environment

- **Python:** 3.12
- **Conda env:** `lightgraph_custom` (`/storage/conda/envs/lightgraph_custom`)
- **Package:** `lightrag-hku` v1.4.10 installed in editable mode **from this directory**
- **Server binary:** `lightrag-server` (in PATH when conda env active)
- **Server port:** 9621 (bind `--host 0.0.0.0` to reach it at `http://10.0.0.80:9621/webui/`)
- **Required extras for the governance gate:** the rules gate needs the `[rules]` extra
  (`model2vec` + `business_rule_engine`) and web ingest needs `[webingest]` (`lxml`,
  `playwright`). If `model2vec` is missing the similarity gate degrades to "similarity check
  unavailable" (rules using `sim()` are skipped) — install `pip install -e '.[rules]'`.
  `model2vec` downloads a small static model from HuggingFace on first gate evaluation.

### ⚠️ Setup gotcha — the editable install MUST point at this checkout

`lightrag-server` runs whatever path `lightrag-hku` is installed editable from — **not** the
current directory. A stale editable install pointing at another copy (e.g. an old
`/home/dimas/Work/lightrag`) silently runs the wrong code: original upstream WebUI, no
Context Graph routes (404s), no workspace selector, and none of this repo's changes. Symptoms:
`/health` reports the wrong `core_version` (expect **1.4.10**) and `/graph/decisions` 404s.

Verify and fix before running or testing:

```bash
# Which checkout does the env actually run? Must be THIS directory.
/storage/conda/envs/lightgraph_custom/bin/pip show lightrag-hku | grep -iE 'Version|Editable'
#   Version: 1.4.10
#   Editable project location: /storage/Work/Context_Graph   ← must match

# If it points elsewhere, repoint it (‑‑no-deps avoids starlette/mcp churn):
cd /storage/Work/Context_Graph
/storage/conda/envs/lightgraph_custom/bin/pip install -e . --no-deps
```

Do NOT rely on `python -c "import lightrag"` from inside this dir to check — cwd shadows the
install and hides the problem. Use `pip show` (above), which reports the real editable path.

### WebUI is a built bundle — rebuild after any `lightrag_webui/` change

The server serves a **pre-built** SPA from `lightrag/api/webui/` (package data). Editing
`lightrag_webui/src` has no effect until you rebuild; the build (`emptyOutDir: true`) wipes and
regenerates that bundle. A stale bundle is why CG tabs (Ontology, Rules, Get Started/Onboarding,
Workspace selector) can go missing. Served page title should be **Context Graph**, not "Lightrag".

## Common Commands

```bash
# Run server (bind 0.0.0.0 so the WebUI is reachable off-box)
lightrag-server --host 0.0.0.0 --port 9621

# Development mode with auto-reload
uvicorn lightrag.api.lightrag_server:app --reload

# Run tests (use the project env's interpreter)
/storage/conda/envs/lightgraph_custom/bin/python -m pytest tests context_graph/tests
python -m pytest tests --run-integration  # requires external services

# Lint
ruff check .

# Build/deploy the WebUI (outputs to lightrag/api/webui/, wiping the old bundle)
cd lightrag_webui && bun install --frozen-lockfile && bun run build && cd ..
```

## Architecture

### Core Files

- `lightrag/context_graph.py` — `ContextGraph` class extending `LightRAG` with RelationContext, CGR3 reasoning, emit_decision_trace, find_precedents, decision indexing/reindex, query-time decision blend + by-name node injection
- `lightrag/context_graph_types.py` — `RelationContext`, `ContextNode`, `ContextEdge` dataclasses
- `lightrag/lightrag.py` — Base `LightRAG` orchestrator (insert, query, storage management)
- `lightrag/operate.py` — Core extraction and query operations (6-field rc-aware extraction/merge/rebuild)
- `lightrag/base.py` — Abstract storage interfaces (`BaseKVStorage`, `BaseVectorStorage`, `BaseGraphStorage`)
- `lightrag/kg/` — Storage backends (JSON, NetworkX, Neo4j, PostgreSQL, MongoDB, Redis, Milvus, Qdrant, Faiss)
- `lightrag/llm/` — LLM provider bindings (OpenAI, Ollama, Azure, Gemini, Bedrock)
- `lightrag/api/lightrag_server.py` — FastAPI server, route registration, per-workspace instance pool, governance-service wiring
- `lightrag/api/workspace_pool.py` — Per-workspace `ContextGraph` pool + pure-ASGI workspace-routing middleware
- `lightrag/api/mcp_server.py` — MCP server (12 tools, X-API-Key auth) mounted into the FastAPI app
- `lightrag/api/routers/context_graph_routes.py` — Context Graph API endpoints (emit, precedents, decisions, reindex, CGR3)
- `lightrag/api/routers/` — Also: `workspace_routes.py` (manifest/onboarding), `rules_routes.py`, `ontology_routes.py`, `rbac_routes.py`, `lifecycle_routes.py`, `actions_routes.py`, `webingest_routes.py`, `query_routes.py`
- `lightrag/api/config.py` — Configuration parsing (env vars → args)
- `lightrag/kg/shared_storage.py` — Multi-process shared memory, workspace namespace isolation, pipeline locks
- `context_graph/` — Governance/agent package (top-level, shipped): `rules/` (DSL gate), `ontology/` (schema validation), `rbac/` (object-level RBAC), `lifecycle/` (state machines), `actions/` (invokable actions + webhooks), `webingest/` (crawl/clean/render ingestion)
- `presets/backfill_git.py` — Client-side git backfill CLI (`--docs`, `--code`, reindex)

### Storage Layer (4 pluggable backends)

- **KV_STORAGE** — docs, chunks, entities, relations, LLM cache
- **VECTOR_STORAGE** — entity/relation/chunk/decision embeddings
- **GRAPH_STORAGE** — entity-relation graph (NetworkX by default; Neo4j for production multi-tenancy)
- **DOC_STATUS_STORAGE** — document processing status

### Data Flow

```
Document Upload → Chunking (1200 tokens) → Entity/Relation Extraction (LLM)
  → RelationContext JSON extraction → Graph + Vector Storage → Ready for Query
```

### Query Modes

- `local` — entity-focused retrieval
- `global` — pattern/community analysis
- `hybrid` — local + global combined
- `naive` — vector search only
- `mix` — KG + vector (recommended with reranker)
- `bypass` — direct LLM, no retrieval

## Multi-Tenancy (Workspace Isolation)

Each company gets an isolated workspace. Isolation is per-request via HTTP header:

```bash
curl -H "LIGHTRAG-WORKSPACE: company_acme" http://localhost:9621/query -d '{"query": "..."}'
```

### What gets isolated per workspace:
- Neo4j nodes/edges (label-based: all nodes tagged with `:workspace_name`)
- Neo4j indexes (per-workspace B-tree and full-text indexes)
- Vector embeddings (separate collections)
- KV stores, document status, LLM cache
- Decision trace vector index

### Neo4j workspace mechanism:
- Nodes get workspace label: `MERGE (n:\`company_acme\` {entity_id: $id})`
- Queries filter: `WHERE node:\`company_acme\``
- Drop is workspace-scoped: `MATCH (n:\`company_acme\`) DETACH DELETE n`

## Context Graph API Endpoints

Ingestion is via `/documents/text`, `/documents/texts`, `/documents/upload`
(there is **no** `POST /insert`).

### Standard LightRAG endpoints (all workspace-aware):
- `POST /documents/text` · `POST /documents/texts` · `POST /documents/upload` — Ingest content
- `POST /query` — Query with mode selection (blends recorded decisions + by-name node injection in CG mode)
- `POST /query/stream` — Streaming query
- `POST /query/data` — Raw data retrieval
- `POST /query/auto` — Mode auto-classification
- `GET /health` — Health check
- Graph CRUD: `/graph/entity/create`, `/graph/relation/create`, etc.

### Context Graph decision endpoints (registered always; return 503 if USE_CONTEXT_GRAPH=false):
- `POST /cgr3/query` — Iterative multi-hop reasoning (Retrieve→Rank→Reason)
- `GET /graph/edge/context` — Get RelationContext for an edge
- `GET /graph/entity/edges-with-context` — All context-enriched edges for entity
- `POST /graph/decision/emit` — Record decision trace at runtime (422 on rules-gate REJECT)
- `POST /graph/decisions/ingest-summary` — Emit a decision as an ingested summary doc
- `POST /graph/decisions/reindex` — Rebuild derived decision indices from the graph
- `GET /graph/decisions/search` — Semantic precedent search over decision traces (`q`, `top_k`)
- `GET /graph/decisions` — Filter decisions by approver, channel, policy, confidence, date

### Governance / agent endpoints (mounted **only** when USE_CONTEXT_GRAPH=true → 404 otherwise):
- `/rules` (GET/POST/DELETE) · `/rules/evaluate|toggle|generate` — DSL rules manager + gate
- `/ontology` (GET/POST/DELETE) · `/ontology/generate|validate` — Ontology manager
- `/rbac` (GET/POST/DELETE) · `/rbac/check` — Object-level RBAC
- `/lifecycle` (GET/POST/DELETE) · `/lifecycle/check` — Declarative state machines
- `/actions` (GET/POST/DELETE) · `/actions/invoke` — Invokable actions + webhooks
- `POST /scrape` · `GET /scrape/{job_id}` · `GET /scrape` — Web ingestion jobs
- `GET /workspace/manifest|bootstrap|playbook|backfill-script` — Role-scoped agent manifest + bootstrap
- `POST /onboard` · `POST /onboard/chat` · `POST /onboard/apply` — Onboarding wizard (conversational)
- `GET /workspaces` · `POST /workspaces/{name}` · `GET /workspaces/{name}/health` — Workspace management

### MCP server (mounted when ENABLE_MCP=true):
12 tools incl. `query_knowledge_graph`, `query_cgr3`, `search_precedents`, `list_decisions`,
`record_decision`, `ingest_decision_summary`, `query_data`, `query_auto`, `invoke_action`,
`get_manifest` — authenticated via `X-API-Key`.

## RelationContext Fields

| Field | Type | Description |
|-------|------|-------------|
| `supporting_sentences` | List[str] | Verbatim document quotes |
| `temporal_info` | str | Validity periods |
| `quantitative_data` | str | Numbers, percentages, amounts |
| `decision_trace` | str | Rationale/approval reasoning |
| `approved_by` | str | Approver entity name |
| `approved_via` | str | Channel: slack, zoom, email, in_person, jira, system |
| `valid_from` / `valid_until` | str | ISO-8601 dates |
| `policy_ref` | str | Policy name/ID |
| `provenance` | str | Source reference |
| `confidence_score` | float | 0.0–1.0 extraction reliability |

## Current Configuration

Reflects the shipped defaults (`env.example`, `lightrag/api/config.py`). Neo4j is
supported and recommended for production multi-tenancy but is **not** the default.

- **Graph storage:** NetworkX (`LIGHTRAG_GRAPH_STORAGE=NetworkXStorage`) — code default; set `Neo4JStorage` for production
- **KV/Vector/DocStatus:** File-based (JsonKVStorage, NanoVectorDB) by default
- **LLM:** OpenAI `gpt-4o` (`LLM_BINDING=openai`); code fallback when unset is `mistral-nemo:latest`
- **Embedding:** `text-embedding-3-large` (dim 3072)
- **Reranking:** Disabled by default (`RERANK_BINDING=null`); `mix` mode benefits from a reranker when enabled
- **Context Graph:** Enabled (`USE_CONTEXT_GRAPH=true`)
- **CGR3:** `CGR3_MAX_ITERATIONS=3` (default); **MCP:** `ENABLE_MCP=true` (default)

> Note: `.env` (gitignored) is the live local config and may differ from `env.example`.

## Key Patterns

### Always initialize storages after instantiation:
```python
rag = ContextGraph(working_dir="./rag_storage", workspace="company_acme", ...)
await rag.initialize_storages()
# ... use rag ...
await rag.finalize_storages()
```

### Emit decisions from application code (no document ingestion):
```python
from lightrag.context_graph_types import RelationContext
rc = RelationContext(decision_trace="VP approved 20% discount", approved_by="Sarah Chen", ...)
await rag.emit_decision_trace("Sarah Chen", "MegaCorp", "discount_approval", rc)
```
When a rules gate is attached to the workspace, `emit_decision_trace` runs the
pre-emit gate first; a REJECT raises `RuleViolation` (REST `/graph/decision/emit`
returns 422 with an audit record) and no edge is written.

### Embedding model must stay consistent — changing it after ingestion breaks vector search.

## Important Notes

- `.env` contains API keys — never commit it (it is gitignored)
- `USE_CONTEXT_GRAPH=true` switches server from `LightRAG` to `ContextGraph` class and mounts the governance/onboarding routers
- Default graph backend is NetworkX (no external DB). If `LIGHTRAG_GRAPH_STORAGE=Neo4JStorage`, configure Neo4j credentials in `.env` before first run
- `context_graph.webingest` imports `lxml` eagerly — install the `webingest` extra (`pip install -e '.[webingest]'`) if using `/scrape`
- Workspace names: `a-z, A-Z, 0-9, _` only
- Use `lightrag.utils.logger` instead of print
- Code style: PEP 8, type annotations, async/await, dataclasses
