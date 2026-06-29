# Configuration

Configuration is via environment variables (and `config.ini`). The full set is in
`env.example` / `config.ini.example`; the keys below are the ones that matter most for
Context Graph.

## Key environment variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `USE_CONTEXT_GRAPH` | `true` | Use `ContextGraph` instead of base `LightRAG`. CG-only endpoints return `503` when `false`. |
| `ENABLE_MCP` | `true` | Mount the MCP server. |
| `CONTEXT_FORMAT` | `annotated` | Context assembly format: `annotated` (rc inline) or `legacy`. |
| `LIGHTRAG_GRAPH_STORAGE` | `Neo4JStorage` | Graph backend. |
| `LIGHTRAG_KV_STORAGE` | `JsonKVStorage` | KV backend. |
| `LIGHTRAG_VECTOR_STORAGE` | `NanoVectorDBStorage` | Vector backend. |
| `LIGHTRAG_DOC_STATUS_STORAGE` | file-based | Doc-status backend. |
| `LLM_BINDING` / `LLM_MODEL` | openai / `gpt-5-mini` | LLM provider + model. |
| `EMBEDDING_MODEL` | `text-embedding-3-large` | Embedding model (dim 3072). |
| `NEO4J_URI` / `NEO4J_USERNAME` / `NEO4J_PASSWORD` | — | Neo4j credentials. |
| `LIGHTRAG_API_KEY` | — | API key required in the `X-API-Key` header. |

> ⚠️ `CONTEXT_FORMAT` is read raw from the environment and compared `== "annotated"`; a typo or
> wrong case silently falls back to legacy (CODE_REVIEW L5). Use exactly `annotated` or `legacy`.

> ⚠️ Embedding model must stay fixed after ingestion — changing it invalidates the vector indexes.

## Storage backends

Pluggable per layer: **JSON, NetworkX, Neo4j, PostgreSQL, MongoDB, Redis, Milvus, Qdrant, Faiss**.
The default configuration uses Neo4j for the graph and file-based stores for KV/vector/doc-status.

| Layer | Holds |
|-------|-------|
| GRAPH | entity/relation graph; edges carry `relation_context` |
| VECTOR | entity/relation/chunk embeddings + the decision-trace index |
| KV | documents, chunks, entities, relations, LLM cache |
| DOC_STATUS | document processing status |

## LLM & embedding providers

Bindings for **OpenAI, Ollama, Azure, Gemini, Bedrock**. An optional reranker (Cohere binding)
improves `mix` mode. Set the provider via `LLM_BINDING` and the matching credentials.

## Running

```bash
# server (port 9621)
lightrag-server

# development, auto-reload
uvicorn lightrag.api.lightrag_server:app --reload

# tests
python -m pytest tests
python -m pytest tests --run-integration   # requires external services

# lint
ruff check .

# WebUI
cd lightrag_webui && bun install --frozen-lockfile && bun run build && cd ..
```

## Per-tenant usage (workspaces)

One deployment serves many tenants; select a tenant with the `LIGHTRAG-WORKSPACE` header
(`a-z A-Z 0-9 _` only). In-process:

```python
cg = ContextGraph(working_dir="./rag_storage", workspace="company_acme",
                  llm_model_func=..., embedding_func=...)
await cg.initialize_storages()
# ...
await cg.finalize_storages()
```

> In a multi-tenant deployment, validate the tenancy items in CODE_REVIEW (S1 MCP contextvar
> propagation; S3 pre-auth workspace creation) before exposing the server publicly.

## Secrets

`.env` holds API keys and DB credentials — it is git-ignored; never commit it.
