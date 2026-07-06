# API reference

Server: `lightrag-server` on port `9621`. Authenticate with the `X-API-Key` header; select the
tenant with the `LIGHTRAG-WORKSPACE` header. CG-specific endpoints return `503` when
`USE_CONTEXT_GRAPH=false`.

> Exact request/response schemas live in `lightrag/api/routers/context_graph_routes.py`; payloads
> below are representative.

## REST — standard (inherited from LightRAG)

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/documents/upload` | Upload & ingest a document |
| POST | `/query` | Query with `mode` selection |
| POST | `/query/stream` | Streaming query |
| POST | `/query/data` | Raw retrieved data (no synthesis) |
| POST | `/graph/entity/create` · `/graph/relation/create` | Graph CRUD (+ update/delete variants) |
| GET | `/health` | Health check |

## REST — Context Graph-specific

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/cgr3/query` | Iterative multi-hop reasoning (Retrieve→Rank→Reason) |
| GET | `/graph/edge/context` | RelationContext for a specific edge |
| GET | `/graph/entity/edges-with-context` | All context-enriched edges for an entity |
| POST | `/graph/decision/emit` | Record a decision trace at runtime |
| GET | `/graph/decisions/search` | Semantic precedent search over decision traces |
| GET | `/graph/decisions` | Filter decisions by approver · channel · policy · confidence · date |

### `/graph/decisions` query parameters

`approved_by`, `approved_via`, `policy_ref`, `min_confidence` (0.0–1.0), `active_as_of`
(ISO `YYYY-MM-DD`), and pagination (`top_k`, bounded 1–100 on the REST surface).

## Examples (curl)

```bash
# standard query against a workspace
curl -X POST http://localhost:9621/query \
  -H "X-API-Key: $LIGHTRAG_API_KEY" \
  -H "LIGHTRAG-WORKSPACE: company_acme" \
  -H "Content-Type: application/json" \
  -d '{"query": "Who approved the MegaCorp discount?", "mode": "hybrid"}'

# CGR3 multi-hop reasoning
curl -X POST http://localhost:9621/cgr3/query \
  -H "X-API-Key: $LIGHTRAG_API_KEY" -H "LIGHTRAG-WORKSPACE: company_acme" \
  -H "Content-Type: application/json" \
  -d '{"query": "Precedents for waiving payment terms on a renewal?"}'

# emit a decision at runtime
curl -X POST http://localhost:9621/graph/decision/emit \
  -H "X-API-Key: $LIGHTRAG_API_KEY" -H "LIGHTRAG-WORKSPACE: company_acme" \
  -H "Content-Type: application/json" \
  -d '{
        "src": "Sarah Chen", "tgt": "MegaCorp", "relation_type": "discount_approval",
        "relation_context": {
          "decision_trace": "VP approved 20% discount",
          "approved_by": "Sarah Chen", "approved_via": "in_person",
          "valid_until": "2024-12-31", "policy_ref": "DiscountPolicy_Standard",
          "confidence_score": 0.97
        }
      }'

# filter decisions
curl "http://localhost:9621/graph/decisions?approved_via=slack&min_confidence=0.8&active_as_of=2024-10-01" \
  -H "X-API-Key: $LIGHTRAG_API_KEY" -H "LIGHTRAG-WORKSPACE: company_acme"

# semantic precedent search
curl "http://localhost:9621/graph/decisions/search?q=waive%20setup%20fee&top_k=5" \
  -H "X-API-Key: $LIGHTRAG_API_KEY" -H "LIGHTRAG-WORKSPACE: company_acme"
```

## MCP server

When `ENABLE_MCP=true` (default), the server mounts a
[Model Context Protocol](https://modelcontextprotocol.io) sub-app exposing **12 tools**, each
wrapping one `rag` method. Properties:

- `FastMCP(name="ContextGraph", stateless_http=True)` — no session state.
- `X-API-Key` validated on the MCP sub-app (timing-safe `hmac.compare_digest`).
- Tools that require CG mode raise an MCP error (not HTTP 503) when `USE_CONTEXT_GRAPH=false`.
- Workspace isolation is inherited from the parent app's middleware (see CODE_REVIEW S1 —
  validate contextvar propagation in multi-tenant setups).

Representative tools: query (mode-selectable), query-data, entity context, edges-with-context,
record-decision (emit), search-precedents, list-decisions, auto-routed query.

> Caveats worth knowing before relying on the API in production are catalogued in
> [CODE_REVIEW.md](CODE_REVIEW.md) — notably exception-message leakage (M5), date validation (M6),
> MCP `top_k` bounds (M7), REST `confidence_score` range (S4), and timing-safe REST auth (S2).
