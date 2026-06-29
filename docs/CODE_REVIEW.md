# Context Graph — Code Review

**Scope:** the Context-Graph-specific code added on top of LightRAG — not the upstream
LightRAG core. Files reviewed:
`lightrag/context_graph.py`, `lightrag/context_graph_types.py`,
`lightrag/api/routers/context_graph_routes.py`, `lightrag/api/mcp_server.py`,
and the CG-specific regions of `lightrag/operate.py`, `lightrag/prompt.py`, `lightrag/base.py`.

Every finding below was confirmed by reading the cited code. Line numbers are from the
reviewed revision and may shift as the file changes. Severity reflects impact on
correctness, data integrity, or security.

**Summary:** 2 critical, 2 high, 9 medium, 11 low. The two most urgent are a silent
data-loss path on document deletion (**H2**) and a parsing bug that effectively disables
the headline CGR3 reasoning loop (**C1**).

---

## Critical

### C1 — CGR3 reason-step JSON fence parsing yields an empty string; iterative reasoning is silently disabled
`lightrag/context_graph.py:967-971`

```python
cleaned = reason_response.strip()
if cleaned.startswith("```"):
    cleaned = cleaned.split("```", 2)[-1].lstrip("json").strip()
    cleaned = cleaned.rsplit("```", 1)[0].strip()
parsed = json.loads(cleaned)
```

When the LLM returns its JSON inside a **closed** code fence (the common case — the
`cgr3_reason_prompt` examples in `prompt.py` literally show fenced JSON, priming the model
to do exactly this), `"```json\n{...}\n```".split("```", 2)` → `['', 'json\n{...}\n', '']`,
and `[-1]` selects the trailing **empty string**. `json.loads("")` then raises, is caught
at line 972, and the loop `break`s. Net effect: every CGR3 iteration's *reason* step fails,
`is_sufficient`/`follow_up_entities` are never read, and the method silently falls through to
a single-shot answer. The advertised Retrieve→Rank→Reason iteration does not run whenever the
model emits fenced JSON.

Secondary: `.lstrip("json")` strips the *character set* `{j,s,o,n}`, not the prefix `"json"`.

**Fix:** take the fenced body, not the trailing split segment:
```python
if cleaned.startswith("```"):
    cleaned = cleaned[3:]
    if cleaned[:4].lower() == "json":
        cleaned = cleaned[4:]
    cleaned = cleaned.split("```", 1)[0].strip()
```

### C2 — `is_empty()` ignores 5 of 10 content fields → the API/MCP silently drop decision-only context
`lightrag/context_graph_types.py:78-86`

```python
def is_empty(self) -> bool:
    return (
        not self.supporting_sentences
        and self.temporal_info is None
        and self.quantitative_data is None
        and self.decision_trace is None
        and self.provenance is None
    )
```

It never checks `approved_by`, `approved_via`, `valid_from`, `valid_until`, `policy_ref`.
A `RelationContext` whose only content is approval/validity/policy lineage — the entire point
of the system — reports as empty. The API uses this to decide whether to return the context
at all:

- `context_graph_routes.py:532-533` — `has_context = rc is not None and not rc.is_empty()`; `relation_context = ... if rc and not rc.is_empty() else None`
- `mcp_server.py:270` — same pattern

So `emit_decision_trace(..., RelationContext(approved_by="VP_Smith", valid_until="2024-12-31", policy_ref="..."))` stores the edge, but `GET /graph/edge/context` returns `has_context=False, relation_context=None`. The data is invisible. Marked critical because it silently drops correct data at the public boundary, and the test suite (`tests/test_context_graph.py:70-75`) doesn't cover a metadata-only rc, so it passes unnoticed.

**Fix:**
```python
return not any([
    self.supporting_sentences, self.temporal_info, self.quantitative_data,
    self.decision_trace, self.provenance, self.approved_by, self.approved_via,
    self.valid_from, self.valid_until, self.policy_ref,
])
```

---

## High

### H1 — `cgr3_query` crashes on valid-but-non-object JSON (`parsed.get` runs outside the `try`)
`lightrag/context_graph.py:971-982`

`json.loads` succeeds on a scalar/array (`"123"`, `"null"`, `["a"]`). Then `parsed` is an
`int`/`None`/`list`, and `parsed.get("is_sufficient", ...)` at line 982 — **outside** the
`try/except` — raises `AttributeError`, propagating out and failing the whole query.

**Fix:** guard the type inside the `try`: `if not isinstance(parsed, dict): break`.

### H2 — Document deletion rebuilds the graph with the 5-field parser, dropping all CG relations and their `rc`
`lightrag/operate.py:457-465` (reached from `lightrag.py` `rebuild_knowledge_from_chunks`, inherited unchanged by `ContextGraph`)

```python
if len(record_attributes) != 5 or "relation" not in record_attributes[0]:
    ...
    return None
```

CG ingestion caches **6-field** relation records (`relation|src|tgt|keywords|description|RC_JSON`).
On `adelete_by_doc_id` → `rebuild_knowledge_from_chunks`, the cached extraction is re-parsed by
the standard `_handle_single_relationship_extraction`, which requires **exactly 5 fields**, so
every CG relation fails the check and returns `None`. After deleting any document that shares
relations with surviving chunks, those relationships vanish from the graph and their
`relation_context` is permanently lost (entities survive — they're 4-field). Even if the count
check were relaxed, this parser never reads `record_attributes[5]`, so `rc` would still be dropped.

**Fix:** have `ContextGraph` route the rebuild path through the CG-aware parser
(`_process_cg_extraction_result`), or accept `len in (5, 6)` and capture the 6th field as the CG
parser does.

---

## Medium

### M1 — Edge `weight` is read from the wrong field; it's effectively always `1.0`
`lightrag/context_graph.py:124-128` — `float(record_attributes[-1]...)`. For 6-field CG records
`[-1]` is the **RelationContext JSON** (never a float → `1.0`); for 5-field records it's the
`description` (a numeric description would be silently mis-read as weight). All CG edges end up
weight `1.0`, degrading any weight-based ranking. **Fix:** drop weight parsing (set `1.0`
explicitly) or add a real weight field to the schema and index it correctly.

### M2 — `from_dict`/`from_json` do no type coercion → `confidence_score` `None`/str crashes comparisons & merge
`lightrag/context_graph_types.py:62-65`. Externally/legacy-written edges with
`"confidence_score": null` or `"0.9"` load verbatim. Then `context_graph.py:783`
(`rc.confidence_score < min_confidence`) and `merge` (`max(..., rc.confidence_score)`) raise
`TypeError`, taking down `find_precedents` / `get_edges_with_context`. **Fix:** coerce to float
with a `1.0` fallback in `from_dict`.

### M3 — `None` rendered literally as `"None"` in the annotated "Valid:" line
`lightrag/operate.py:4020-4023`. `_collect_relation_context` always emits all 11 keys, so
`valid_from`/`valid_until` exist with value `None`; `dict.get(key, "?")` returns `None` (default
applies only to *missing* keys). The prompt then shows `Valid: None → 2024-12-31`. **Fix:**
`vf = rc.get("valid_from") or "?"` (same for `vu`).

### M4 — `provenance` is captured and stored but never shown to the answering LLM
`lightrag/operate.py:4004-4035` (`_format_relation_context`). Renders 10 of 11 fields and omits
`provenance`, even though the extraction prompt works to capture it and the merge stores it.
Both annotated-chunk and orphan-relation rendering use this function, so the source reference
never reaches the model. **Fix:** add `if rc.get("provenance"): rc_parts.append(f"Source: {rc['provenance']}")`.

### M5 — Raw exception text returned to clients (information disclosure)
REST: `context_graph_routes.py:426, 539, 656, 715, 785, 857, 934` (`detail=str(e)`);
MCP: `mcp_server.py:162, 189, 220, 255, 281, 319, 365, 396, 419, 498` (`ToolError(str(e))`).
`str(e)` on a downstream Neo4j/Cypher/IO error can leak connection strings, file paths, and
internal structure to the caller. The full error is already logged server-side. **Fix:** return
a generic message; keep `logger.error(..., exc_info=True)`.

### M6 — Date inputs are never validated; filtering compares ISO strings lexicographically
`context_graph_routes.py:895-899` (`active_as_of`) and `context_graph_types.py:88-103`
(`is_active`). `active_as_of="last week"`, `"2024-13-99"`, or unpadded `"2024-9-1"` produce a
**silently wrong** boolean rather than a 400. The same applies to unvalidated `valid_from`/
`valid_until` written via `/graph/decision/emit`, which then poison future filtering. **Fix:**
`datetime.date.fromisoformat(...)` inside a `try`, raising `HTTPException(400)` / `ToolError` on
failure, on both read filters and write paths.

### M7 — MCP tools don't bound `top_k` / `min_confidence` (REST does)
`mcp_server.py:140, 170, 196-197, 404`. The REST equivalents use `Query(..., ge=1, le=100)` /
`ge=0.0, le=1.0` (`context_graph_routes.py:807-818`); the MCP tools accept any value. A client
or LLM passing `top_k=10_000_000` forces huge scans. **Fix:** clamp in the tools to mirror the
REST bounds.

### M8 — Merging all-zero confidence yields `1.0` (lowest → highest)
`lightrag/context_graph_types.py:178` and `lightrag/operate.py:1946`:
`confidence_score = max_confidence if max_confidence > 0 else 1.0`. If every source rc has
`0.0`, the merged value becomes `1.0`. The `> 0` sentinel can't distinguish "no values present"
from "all values are 0.0". **Fix:** track whether any score was seen (start at `None`) instead of
overloading `0.0` as unset.

### M9 — `finalize_storages` can leak `decisions_vdb` when the parent finalize raises
`lightrag/context_graph.py:544-549`. `await super().finalize_storages()` runs **before** and
outside the `try` that finalizes `decisions_vdb`; if the parent raises, the CG-specific store is
never finalized. **Fix:** finalize each store in its own guarded block (or `finally`).

---

## Security

> Several of these live just outside the two API files but are directly on the auth/tenant path; flagged because they affect a multi-tenant deployment.

### S1 — [UNCERTAIN, potentially high] MCP tenant isolation relies on a contextvar propagating through the MCP transport
`mcp_server.py` (all tools) read no `LIGHTRAG-WORKSPACE`; they rely on `_current_workspace`,
set by `WorkspaceMiddleware` on the **parent** app. The MCP app is a separately-mounted Starlette
sub-app whose Streamable-HTTP handler runs tools in a task group created at lifespan startup. If
the per-request contextvar does not propagate into that task, MCP calls either always hit the
`default` workspace or, worse, leak across tenants under concurrency. **Action:** add an
integration test (`LIGHTRAG-WORKSPACE: tenant_b` via MCP must return tenant_b data); if broken,
set the workspace contextvar inside MCP middleware before dispatch.

### S2 — REST API-key check is not timing-safe (and inconsistent with MCP)
`utils_api.py:229` uses `api_key_header_value == api_key` (plain `==`), while `mcp_server.py:510`
correctly uses `hmac.compare_digest`. **Fix:** use `hmac.compare_digest` on the REST path too.

### S3 — Pre-auth workspace creation (DoS)
`WorkspaceMiddleware` runs before route auth and calls `pool.get_rag(workspace)`, which lazily
**creates and initializes** a workspace (Neo4j labels, vector collections) for any well-formed
header value. An unauthenticated client can spray distinct `LIGHTRAG-WORKSPACE` values to force
unbounded tenant creation. **Fix:** authenticate before materializing a workspace, or validate
the workspace against an allow-list.

### S4 — REST `confidence_score` has no range validation
`context_graph_routes.py:137-140` — `Field(default=1.0, ...)` with no `ge=0.0, le=1.0`. Via
`POST /graph/decision/emit` a client can store `999`/`-5`, corrupting confidence filters. (The
MCP path clamps; REST doesn't.) **Fix:** `Field(default=1.0, ge=0.0, le=1.0)`.

### S5 — [UNCERTAIN] `query_auto` may dispatch `cgr3_query` with no CG guard
`mcp_server.py:464-475`. `classify_query_mode` can return `"cgr3"`; `query_auto`/`query_data`
skip `_require_context_graph`. On a non-CG server (`rag` is plain `LightRAG`), this raises
`AttributeError` surfaced as a cryptic `ToolError` instead of a clean "CG required". **Fix:**
guard the cgr3 branch or fall back to `hybrid`.

---

## Low

- **L1 — Scalar rc merge is "first-non-null wins"** (`operate.py:1912-1929`, `types.merge`): when
  the same edge recurs across chunks with different `decision_trace`/`policy_ref`/`approved_by`,
  all but the first are silently dropped (order-dependent). Only `supporting_sentences` (union)
  and `confidence_score` (max) survive. Acceptable if intended — at least log it.
- **L2 — `find_precedents` admits empty-rc edges** (`context_graph.py:782-783`): `from_json("{}")`
  → `confidence_score=1.0` ≥ default `min_confidence=0.0`, so context-less edges can return as
  precedents; same `(src,tgt)` hits aren't de-duplicated.
- **L3 — Cross-iteration dedup hashes only the first 500 chars** (`context_graph.py:937`): two
  different contexts sharing a 500-char prefix collide; the second is dropped as "seen".
- **L4 — `to_text()` omits the 5 approval/validity fields** (`types:105-118`): callers relying on
  it lose the decision lineage (the annotated query path uses `_format_relation_context` instead).
- **L5 — `context_format` env var not validated against its `Literal`** (`base.py:160-162`):
  `CONTEXT_FORMAT=Annotated` (wrong case) silently falls back to legacy. Normalize + whitelist.
- **L6 — `_validate_conversation_history` raises `TypeError` (not `ToolError`) on non-str
  `content`** (`mcp_server.py:39-54`), and runs before the `try`. Coerce/validate `content`.
- **L7 — MCP auth: `hmac.compare_digest` raises on a non-ASCII configured key; no OPTIONS
  exemption** (`mcp_server.py:506-515`). Encode to bytes; exempt preflight.
- **L8 — `except (ValueError, Exception)`** (`context_graph.py:171`): redundant (`ValueError ⊂
  Exception`) and over-broad — swallows all errors, returning `None` with only a warning.
- **L9 — dead `result.content` access** (`context_graph.py:904-908, 920-925, 1027-1031`):
  `aquery()` returns `str`; `hasattr(x, "content")` is always False. Harmless but misleading.
- **L10 — prompt example inconsistency** (`prompt.py`): some few-shot examples emit all 11 rc keys,
  others omit the approval/validity keys — may train the model to drop them.
- **L11 — `supporting_sentences` cap mismatch**: prompt asks for up to 3 (`prompt.py:532`), display
  caps at 2 (`operate.py:4031`). Cosmetic.

---

## Verified clean / positives

- **RelationContext survives token truncation** — `_apply_token_truncation` pops only
  `file_path`/`created_at` for the cost copy; `relation_context` is retained
  (`operate.py:3841-3857`). JSON parses on merge and query sides are `try/except`-wrapped.
- **Serialization round-trips all 11 fields** (`asdict` / filtered `from_dict`); dates are plain
  ISO strings, so JSON is lossless; `from_json` returns an empty `RelationContext()` on bad input.
- **No mutable-default-argument bugs** — all collections use `field(default_factory=...)`.
- **CGR3 loop bound is correct** — `for i in range(max_iterations)`, no off-by-one / infinite loop
  (also bounded by the dedup early-break).
- **`context_format` defaults to `annotated` consistently** across `base.py` and `QueryParam`; the
  API strips `None` via `model_dump(exclude_none=True)` so the default isn't bypassed.
- **All CG-only REST endpoints call `_require_context_graph`** (cgr3, edge/context,
  edges-with-context, decision/emit, decisions/search, decisions).
- **Pydantic v2 `@field_validator` referencing later-defined fields is valid** (validators bind
  after the class body).

---

## Suggested fix order

1. **C2 / H2** — stop dropping decision data (`is_empty`, rebuild parser). Data integrity first.
2. **C1 / H1** — fix CGR3 JSON parsing + type guard so the headline feature works and can't crash.
3. **S1 / S2 / S3** — verify MCP tenant isolation; make REST auth timing-safe; gate workspace
   creation behind auth.
4. **M-series** — weight, type coercion, `None`→`"None"`, provenance display, date validation,
   MCP bounds, exception leakage.
5. **L-series** — robustness and consistency clean-ups.

> Recommended: add regression tests alongside the fixes — especially a metadata-only-`rc`
> round-trip (C2), a fenced-JSON CGR3 reason response (C1), a delete-then-query rebuild check
> (H2), and an MCP cross-workspace isolation test (S1).
