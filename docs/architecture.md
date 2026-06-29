# Architecture

Context Graph is a subclass layer over LightRAG. It keeps LightRAG's storage, chunking,
extraction, embedding, and query machinery, and adds a decision layer: RelationContext on every
edge, CGR3 reasoning, runtime decision capture, and the supporting API.

## Layers

```
            ┌─────────────────────────────────────────────────────────────┐
  Clients → │  API  — FastAPI (REST) + MCP server  ·  :9621  ·  X-API-Key  │
            │         WorkspaceMiddleware (per-request tenant isolation)    │
            └───────────────┬─────────────────────────────────────────────┘
                            │
            ┌───────────────▼───────────────┐
            │  ContextGraph (extends LightRAG)│  insert · query · cgr3_query
            │  emit_decision_trace · find_precedents
            └───────────────┬───────────────┘
                            │
   ┌─────────────┬──────────┴───────────┬──────────────┐
   │ GRAPH       │ VECTOR               │ KV           │ DOC_STATUS
   │ (Neo4j)     │ entity/rel/chunk     │ docs/chunks  │ processing state
   │ (h,r,t,rc)  │ + decision-trace idx │ + LLM cache  │
   └─────────────┴──────────────────────┴──────────────┴──────────────┘
```

### Core files

| File | Responsibility |
|------|----------------|
| `lightrag/context_graph.py` | `ContextGraph` class — RelationContext extraction, CGR3, `emit_decision_trace`, `find_precedents` |
| `lightrag/context_graph_types.py` | `RelationContext`, `ContextNode`, `ContextEdge` dataclasses |
| `lightrag/lightrag.py` | Base `LightRAG` orchestrator (insert/query/storage management) |
| `lightrag/operate.py` | Extraction, entity/relation merge, annotated-context assembly |
| `lightrag/prompt.py` | CG extraction prompts + annotated-response templates |
| `lightrag/base.py` | Abstract storage interfaces; `QueryParam` (incl. `context_format`) |
| `lightrag/kg/` | Storage backends (JSON, NetworkX, Neo4j, PostgreSQL, MongoDB, Redis, Milvus, Qdrant, Faiss) |
| `lightrag/llm/` | LLM bindings (OpenAI, Ollama, Azure, Gemini, Bedrock) |
| `lightrag/api/lightrag_server.py` | FastAPI app, route registration, workspace routing |
| `lightrag/api/routers/context_graph_routes.py` | CG REST endpoints |
| `lightrag/api/mcp_server.py` | MCP server (8 tools, X-API-Key auth) |

## Storage layers

| Layer | Holds | Default backend |
|-------|-------|-----------------|
| **GRAPH** | entity/relation graph; edges carry `relation_context` JSON | Neo4j |
| **VECTOR** | entity/relation/chunk embeddings **+ a decision-trace index** | NanoVectorDB |
| **KV** | documents, chunks, entities, relations, LLM cache | JsonKVStorage |
| **DOC_STATUS** | document processing status | file-based |

The decision-trace vector index is CG-specific: it powers semantic precedent search
(`find_precedents` / `/graph/decisions/search`).

## Data flow

**Ingestion (extraction path)**

```
Document → chunk (1200 tokens, 100 overlap) → LLM extraction
  → entities (4-field) + relations (5-field, or 6-field with RelationContext JSON)
  → dedup/merge → graph edges (with relation_context) + vector embeddings + KV
```

**Ingestion (emission path)**

```
Application/agent → emit_decision_trace(h, t, relation, rc)
  → edge written atomically with relation_context + decision-trace embedding
```

**Query**

```
Query → keyword extraction → graph + vector retrieval (mode-dependent)
  → context assembly (annotated: rc rendered inline) → LLM synthesis → answer
        └── or CGR3: Retrieve → Rank → Reason, iterating up to 3 times
```

## The server

- Binary: `lightrag-server`, port `9621`.
- `USE_CONTEXT_GRAPH=true` swaps the base `LightRAG` for `ContextGraph`. CG-only endpoints return
  `503` (REST) / an MCP error when it is `false`.
- The MCP sub-app is mounted last (after `/webui`, `/static`) so it doesn't shadow named mounts.

## Multi-tenancy (workspaces)

Each tenant is an isolated workspace selected per request via the `LIGHTRAG-WORKSPACE` header.
Isolation spans Neo4j nodes/edges (label-based, e.g. `MERGE (n:`company_acme` {...})`),
per-workspace indexes, vector collections, KV stores, doc status, LLM cache, and the
decision-trace index. Workspace names allow `a-z A-Z 0-9 _` only.

> See [CODE_REVIEW.md](CODE_REVIEW.md) S1/S3 for two tenancy items worth validating in a
> multi-tenant deployment (MCP contextvar propagation; pre-auth workspace creation).

## Lifecycle

```python
cg = ContextGraph(working_dir="./rag_storage", workspace="company_acme",
                  llm_model_func=..., embedding_func=...)
await cg.initialize_storages()
# ... insert / query / emit ...
await cg.finalize_storages()
```
