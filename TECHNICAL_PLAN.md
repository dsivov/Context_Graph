# Context Graph — Active Technical Plans

## CR-012: Product Variants as First-Class Entities (T-109)

**Full plan**: `/home/dimas/development_team/technical_plans/CR-012-product-variants.md`

### Quick Reference for Developer

**Two-phase approach: Phase C (document restructuring) → Phase A (entity extraction)**

**Phase C — Document Restructuring (start here)**:
- Restructure variant listing from flat bullet points to markdown table with explicit headers in product document formatting
- Add "Available Colors" / "Available Sizes" summary lines
- Each variant row prefixed with parent product name
- Re-extract test workspace, run variant benchmark to measure impact
- Expected: 50-70% variant hit rate with zero extraction code changes

**Phase A — Entity Extraction Enhancement**:
1. `lightrag/constants.py:29` — add `"ProductVariant"` to `DEFAULT_ENTITY_TYPES`
2. `lightrag/prompt.py` — add variant extraction instructions + few-shot example to both standard and CG extraction prompts (~25 lines each)
   - Naming: `"{Parent} — {VariantKey}"` (e.g., "Hybrid Mattress — King 160×200")
   - Relations: `HAS_VARIANT` from parent to variant, `VARIANT_ATTRIBUTE` for properties
3. `lightrag/operate.py` — add `_merge_variant_descriptions()` helper in entity merge section (~40 lines)
   - Unions attributes across chunks, latest price wins, dedup SKUs

**Re-extraction**: Pilot on a few workspaces first. Batch remaining workspaces overnight after pilot validates.

**Key patterns**:
- Variant naming: `"{Parent} — {Key}"` ensures uniqueness and parent traceability
- Phase C is additive (no extraction changes) — safe to deploy independently
- Phase A builds on Phase C — both doc format and extraction prompt work together

---

## CR-011: Context Formatting Quality (T-085)

**Full plan**: `/home/dimas/development_team/technical_plans/CR-011-context-formatting-quality.md`

### Key Finding: Implementation Already Complete

All code-level acceptance criteria (7/8) are already implemented:
- `_apply_token_truncation()` preserves `relation_context` → `operate.py:3797-3814`
- `_build_annotated_chunk()` renders inline rc annotations → `operate.py:4004-4066`
- `_build_context_str()` supports `annotated` | `legacy` → `operate.py:4112-4324`
- `kg_annotated_context` prompt template → `prompt.py`
- `rag_response_annotated` system prompt → `prompt.py:278-332`
- `QueryParam.context_format` defaults to `"annotated"` → `base.py:160-166`
- CGR3 gets annotated format via default `QueryParam()` → `context_graph.py:898`

### What Remains
1. ~~**Benchmark validation**~~ **Done (T-094)** — `benchmark_gap1_v2.py` comparative: Annotated wins 10/12 overall, 56% criterion win rate vs 21.4% legacy
2. ~~**A/B comparison**~~ **Done (T-095)** — Same benchmark covers A/B. Annotated dominates completeness (10-2), usefulness (10-2), synthesis (9-3), grounding (6-0)
3. ~~**CGR3 validation**~~ **Done (T-096)** — 5 multi-hop queries via `/cgr3/query`. All pass: answers reference approval chains, decision traces, temporal validity, competitors, objections
4. ~~**Fix R-1** — orphan relations only surface 3/10 rc fields~~ **Done (T-097)** — extracted `_format_relation_context()` helper, orphans now surface all 10 rc fields
5. **Prompt tuning** — only if benchmark < 4.7

---

## CR-019: MCP Agent Acceptance Tests (T-093)

**Full plan**: `/home/dimas/development_team/technical_plans/CR-019-mcp-agent-acceptance-tests.md`

### Quick Reference for Developer

**New file**: `tests/test_mcp_agent_acceptance.py` (~600 lines)
- `MCPAgentEmulator` — GPT-4.1 + MCP SDK client (`streamable_http_client` + `ClientSession`), function calling agent loop
- `RestBaselineCache` — Caches REST responses to JSON, avoids re-running benchmarks
- `LLMJudge` — 5-dimension scoring (Product Knowledge, Helpfulness, Completeness, Accuracy, Actionability), parity check within 0.3 points
- 5 test suites, 108 questions total, 8 workspaces

**Test suites**:
1. Complex queries (14q, 7 workspaces)
2. Decision endpoints (8q), translated to NL questions
3. Auto benchmark subset (20q, 9 workspaces)
4. Quality benchmark subset (50q)
5. Tool selection accuracy (16q, mixed) — new questions mapping to specific tools

**Pass criteria**: quality within 0.3 points, tool selection ≥75% (12/16), latency overhead <500ms

**Key patterns**:
- MCP client: `streamable_http_client(url, http_client=httpx_client)` + `ClientSession`
- Tool schema conversion: MCP `list_tools()` → OpenAI function-calling format
- Agent loop: max 3 tool calls per question, then synthesize
- REST baseline: cache to `test_results/rest_baseline_cache.json`
- Judge: `gpt-4.1-mini` at `temperature=0`

---

## CR-018: MCP Server (T-084)

**Full plan**: `/home/dimas/development_team/technical_plans/CR-018-mcp-server.md`

### Quick Reference for Developer

**New file**: `lightrag/api/mcp_server.py` (~180 lines)
- Factory function `create_mcp_server(rag, api_key, top_k, cgr3_max_iterations)` → returns `(FastMCP, Starlette)`
- 8 `@mcp.tool()` functions, each calling one existing `rag` method via WorkspaceProxy
- Auth middleware on the MCP Starlette sub-app: validates `X-API-Key` header
- Tools requiring ContextGraph mode: raise `McpError` (not HTTP 503) if `USE_CONTEXT_GRAPH=false`

**Modified files**:
1. `lightrag/api/lightrag_server.py` — import, lifespan (AsyncExitStack for session_manager), mount at `app.mount("", mcp_app)`
2. `pyproject.toml` — add `mcp>=1.26.0,<2.0`
3. `lightrag/api/config.py` — optional `ENABLE_MCP` env var (default: `true`)

**Implementation order**: ~~dependency~~ → mcp_server.py → lightrag_server.py integration → config → tests

### Progress
- [x] **T-086** — Added `mcp>=1.26.0,<2.0` to `pyproject.toml` `[api]` deps. 74 tests pass (1 pre-existing failure in `test_emit_decision_trace_creates_edge`). (2026-03-16)
- [x] **T-087** — Created `lightrag/api/mcp_server.py` (~280 lines). Factory `create_mcp_server()` with 8 MCP tools + X-API-Key auth middleware. Tools use `ToolError` (not HTTP 503) for CG mode guard. Tool descriptions from CR-018. 177 tests pass, 0 failures. (2026-03-16)
- [x] **T-088** — Modified `lightrag_server.py`: (1) import `create_mcp_server`, (2) `AsyncExitStack` in lifespan for MCP session manager, (3) `app.mount("", mcp_app)` placed **last** (after /webui, /static mounts) to avoid shadowing named mounts. 177 tests pass. (2026-03-16)
- [x] **T-089** — Added `ENABLE_MCP` env var to `config.py` (default: `true`). Server skips MCP mount when `false`. (2026-03-16)
- [x] **T-090** — Created `tests/test_mcp_server.py` with 25 tests: 4 registration, 8 parameter schemas, 8 tool calls, 3 auth, 2 CG mode guard. All pass. (2026-03-16)

**Key patterns to follow**:
- Same closure-over-`rag` pattern as `create_context_graph_routes()` in `context_graph_routes.py`
- Use `FastMCP(name="ContextGraph", stateless_http=True)` — no session state
- Use `contextlib.AsyncExitStack` in lifespan to manage MCP session_manager alongside existing startup
- Workspace isolation is automatic: WorkspaceMiddleware on parent app sets `_current_workspace` contextvar
