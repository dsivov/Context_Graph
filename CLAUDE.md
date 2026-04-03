# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is **Context_Graph** — a fork of [LightRAG](https://github.com/HKUDS/LightRAG) that extends triple-based knowledge graphs `(h, r, t)` into contextual quadruples `(h, r, t, rc)`. The `RelationContext` (rc) captures decision lineage: who approved it, why, via which channel, under which policy, and validity period.

Upstream repo: https://github.com/dsivov/Context_Graph

## Environment

- **Python:** 3.12
- **Package:** `lightrag-hku` v1.4.10 installed in editable mode from this directory
- **Server binary:** `lightrag-server` (in PATH when conda env active)
- **Server port:** 9621

## Common Commands

```bash
# Run server
lightrag-server

# Development mode with auto-reload
uvicorn lightrag.api.lightrag_server:app --reload

# Run tests
python -m pytest tests
python -m pytest tests --run-integration  # requires external services

# Lint
ruff check .

# Build WebUI
cd lightrag_webui && bun install --frozen-lockfile && bun run build && cd ..
```

## Architecture

### Core Files

- `lightrag/context_graph.py` — `ContextGraph` class extending `LightRAG` with RelationContext, CGR3 reasoning, emit_decision_trace, find_precedents
- `lightrag/context_graph_types.py` — `RelationContext`, `ContextNode`, `ContextEdge` dataclasses
- `lightrag/lightrag.py` — Base `LightRAG` orchestrator (insert, query, storage management)
- `lightrag/operate.py` — Core extraction and query operations
- `lightrag/base.py` — Abstract storage interfaces (`BaseKVStorage`, `BaseVectorStorage`, `BaseGraphStorage`)
- `lightrag/kg/` — Storage backends (JSON, NetworkX, Neo4j, PostgreSQL, MongoDB, Redis, Milvus, Qdrant, Faiss)
- `lightrag/llm/` — LLM provider bindings (OpenAI, Ollama, Azure, Gemini, Bedrock)
- `lightrag/api/lightrag_server.py` — FastAPI server, route registration, workspace routing
- `lightrag/api/routers/context_graph_routes.py` — Context Graph API endpoints
- `lightrag/api/config.py` — Configuration parsing (env vars → args)
- `lightrag/kg/shared_storage.py` — Multi-process shared memory, workspace namespace isolation, pipeline locks

### Storage Layer (4 pluggable backends)

- **KV_STORAGE** — docs, chunks, entities, relations, LLM cache
- **VECTOR_STORAGE** — entity/relation/chunk/decision embeddings
- **GRAPH_STORAGE** — entity-relation graph (Neo4j in our config)
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

### Standard LightRAG endpoints (all workspace-aware):
- `POST /documents/upload` — Upload documents
- `POST /query` — Query with mode selection
- `POST /query/stream` — Streaming query
- `POST /query/data` — Raw data retrieval
- `GET /health` — Health check
- Graph CRUD: `/graph/entity/create`, `/graph/relation/create`, etc.

### Context Graph-specific (return 503 if USE_CONTEXT_GRAPH=false):
- `POST /cgr3/query` — Iterative multi-hop reasoning (Retrieve→Rank→Reason)
- `GET /graph/edge/context` — Get RelationContext for an edge
- `GET /graph/entity/edges-with-context` — All context-enriched edges for entity
- `POST /graph/decision/emit` — Record decision trace at runtime (no ingestion needed)
- `GET /graph/decisions/search` — Semantic precedent search over decision traces
- `GET /graph/decisions` — Filter decisions by approver, channel, policy, confidence, date

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

- **Graph storage:** Neo4j (`LIGHTRAG_GRAPH_STORAGE=Neo4JStorage`)
- **KV/Vector/DocStatus:** Default file-based (JsonKVStorage, NanoVectorDB)
- **LLM:** OpenAI `gpt-5-mini`
- **Embedding:** `text-embedding-3-large` (dim 3072)
- **Reranking:** Local server at `localhost:9000` via cohere binding
- **Context Graph:** Enabled (`USE_CONTEXT_GRAPH=true`)

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

### Embedding model must stay consistent — changing it after ingestion breaks vector search.

## Important Notes

- `.env` contains API keys — never commit it
- `USE_CONTEXT_GRAPH=true` switches server from `LightRAG` to `ContextGraph` class
- Neo4j credentials in `.env` need to be configured before first run
- Workspace names: `a-z, A-Z, 0-9, _` only
- Use `lightrag.utils.logger` instead of print
- Code style: PEP 8, type annotations, async/await, dataclasses
