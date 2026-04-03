# Context Graph — Changelog

All notable work on the Context Graph project, from initial fork through production readiness.

---

## [2026-03-16] MCP Server & Production Readiness

### MCP Server (CR-018)
- Created `lightrag/api/mcp_server.py` with 8 MCP tools + X-API-Key auth middleware
- Integrated into FastAPI server via `AsyncExitStack` lifespan and `app.mount()`
- Added `ENABLE_MCP` env var to `config.py` (default: `true`)
- 25 MCP-specific tests: registration, parameter schemas, tool calls, auth, CG mode guard

### Context Formatting Quality (CR-011)
- `_apply_token_truncation()` preserves `relation_context` during truncation
- `_build_annotated_chunk()` renders inline rc annotations
- `_build_context_str()` supports `annotated` | `legacy` output modes
- Annotated format dominates legacy: completeness (10-2), usefulness (10-2), synthesis (9-3)
- Extracted `_format_relation_context()` helper — orphan relations now surface all 10 rc fields

---

## [2026-03-14] Model Optimization

- Model comparison across workspaces revealed gpt-4.1-mini hallucination issues
- Switched production LLM from gpt-4.1-mini to gpt-4.1 for better accuracy
- Measured impact of context formatting on answer quality

---

## [2026-03-12] Benchmarking Framework

### Product Benchmark Suite
- 9 companies x 5 queries x 6 retrieval modes = 270 queries
- **Result:** Hybrid mode best at 69.6% relevance, 80% hit rate

### Auto Router vs Bypass Benchmark
- 45 queries comparing auto-routed vs catalog bypass
- **Result:** Auto wins 47% vs bypass 36%, ties 18%

---

## [2026-03-09] Project Foundation

### Context Graph Fork
- Forked LightRAG v1.4.10 as "Context Graph" with contextual quadruples `(h, r, t, rc)`
- Core CG implementation: relation_context fields for decision traces, temporal info, provenance

### Multi-Tenant Architecture
- `WorkspacePool` — Lazy instance-per-workspace cache with proxy pattern
- `WorkspaceMiddleware` — HTTP middleware routing requests by `LIGHTRAG-WORKSPACE` header
- Label-based Neo4j workspace isolation (each company gets separate graph labels)

### Infrastructure
- Neo4j 5 Community standalone install with APOC plugin
- CGR3 multi-strategy retrieval (rewritten with combined rank+reason step)
- WebUI with workspace selector and CGR3 mode toggle
- Server on port 9621 with 8 gunicorn workers

### Custom Entity Types
- Extended `DEFAULT_ENTITY_TYPES` with domain-specific types:
  - Standard: Person, Creature, Organization, Location, Event, Concept, Method, Content, Data, Artifact, NaturalObject
  - Sales intelligence: LossReason, Objection, Competitor

### Documentation
- `CONTEXTGRAPH_PAPER.md` — Research paper on context graph approach
- `CONTEXTGRAPH_IMPLEMENTATION_PLAN.md` — Implementation plan
- `CONTEXTGRAPH_README.md` — Context Graph specific documentation
- `CLAUDE.md` — Development guidance for AI assistants
- `SECURITY.md` — Security considerations
