# Context Graph — Checkpoint Review (2026-07-06)

Full-project review before a session gap. Covers `lightrag/context_graph*`, the API layer,
the `context_graph/` governance package, recent query/decision features, docs, and the test
suite. Findings were produced by parallel focused reviews and de-duplicated here.

**Status at checkpoint:** branch `feat/dspy-graph-builder`, even with `origin/main` (clean tree
before this review). Test suite **582 passed / 35 skipped / 0 errors** after the fixes below.

> **Update (2026-07-06, same session):** P1–P4 below are now **fixed** with regression tests
> (P0 auth/tenancy deferred by request). See "P1–P4 remediation" at the end of this document for
> what changed. The "Open findings" list is kept verbatim as the record of what was found; each
> fixed item is marked ✅.

---

## What was fixed in this review pass

These are already applied to the working tree:

1. **Test suite could not run green.** Missing env deps (`pytest-asyncio`, `mcp`,
   `business_rule_engine`, `lxml`) plus two stale fixtures and one orphaned test.
   - `tests/test_context_graph.py` / `context_graph/tests/test_rules_gate.py`: the `_make_cg`
     helpers mocked `decisions_vdb` but not `relationships_vdb`, and never bound the real
     `_index_decision` — so every `emit_decision_trace` test hit
     `TypeError: MagicMock can't be used in 'await'` after commit `37422cb2` unified decision
     indexing. Fixed both helpers.
   - `tests/test_variant_extraction.py` imported `sync_all` from a `scripts/` dir that does not
     exist in the open-source tree (CR-012 was never shipped here) → collection error broke
     `pytest tests`. Wrapped in `pytest.importorskip`.
2. **Packaging gap:** `context_graph/webingest/clean.py` imports `lxml` at module load, but
   `lxml`/`playwright` were declared nowhere in `pyproject.toml`. A clean install therefore
   breaks `import context_graph.webingest`. Added a `webingest` optional-dependencies extra.
3. **Docs:** refreshed `CLAUDE.md` (config defaults NetworkX/gpt-4o/no-reranker, full endpoint
   map incl. governance/onboarding/MCP surfaces, core-files list incl. the `context_graph/`
   package, rules-gate note on emit); fixed two copy-pasteable broken examples in
   `docs/api-reference.md` (`src/tgt/relation_type`, `?q=`, 12 MCP tools); corrected stale
   "known issue" caveats in `docs/data-model.md` and `docs/ingestion-and-querying.md` that
   described bugs already fixed in `058a26e3`.

**Prior-fix verification:** the three `fix/cg-criticals` fixes (`058a26e3`) are present and
correct — fence-tolerant CGR3 JSON parse (C1/H1), `is_empty()` over all content fields (C2), and
rc preserved through graph rebuild (H2). One residual gap remains in gleaning (see B7).

---

## Open findings — prioritized

Severity is engineering impact, not exploit difficulty. IDs are stable references for follow-up.

### P0 — Security / multi-tenancy (address before any real multi-tenant deployment)

The per-workspace **storage** isolation is genuinely well built (contextvar proxy, pure-ASGI
routing middleware, per-workspace vector/KV/cache). The gap is that **authentication was never
joined to tenancy** — the boundary, not the plumbing.

- **A1 — No principal→workspace binding.** Workspace is chosen purely by the `LIGHTRAG-WORKSPACE`
  header; one API key / any JWT can read and write *every* tenant's graph, decisions, and docs by
  changing the header. `lightrag_server.py:~1199`. This is the root of A2–A6.
- **A2 — Default `WHITELIST_PATHS="/health,/api/*"` short-circuits auth** before key/token checks,
  and the Ollama-emulation routes (`/api/chat`, `/api/generate`) run real RAG. Unauthenticated
  cross-tenant read via one header. `config.py:~393`, `utils_api.py:117`.
- **A3 — Guest-token bypass.** `GET /auth-status` (no auth) mints a guest JWT whenever
  `AUTH_ACCOUNTS` is unset, *even when an API key is configured*, and the combined dependency
  accepts guest tokens — so an API-key-only deployment is effectively open. `lightrag_server.py:~1397`.
- **A4 — MCP mount is API-key-only.** With JWT-only auth (`AUTH_ACCOUNTS` set, no
  `LIGHTRAG_API_KEY`) all 12 MCP tools — incl. `record_decision`, `invoke_action` — are
  unauthenticated against any workspace. `ENABLE_MCP` defaults true. `mcp_server.py:~564`.
- **A5 — Unauthenticated workspace create/enumerate.** `WorkspaceMiddleware` builds a full
  instance (Neo4j labels+indexes, vector collections, dirs, cached forever) on *any* request with
  a novel header, before auth. `/workspaces`, `POST /workspaces/{name}`, `/workspaces/{name}/health`
  have no auth dependency → enumeration + unbounded resource growth. `workspace_pool.py:~196`.
- **A6 — Shared `doc_manager` breaks document isolation.** One `DocumentManager` for the *default*
  workspace is shared by all tenants while `rag` is per-request proxied. `POST /documents/scan`
  ingests every tenant's uploaded files into the caller's graph; uploads collide across tenants
  and leak filenames. The webingest wiring already builds a per-workspace manager — document
  routes didn't get that fix. `lightrag_server.py:~355,1220`.

Also here: **A7** — onboarding saves rules but never `attach()`es the gate, so a freshly onboarded
tenant runs *ungated* until a server restart or a `/rules` mutation (`workspace_routes.py:492,572`);
**A8** — MCP `BaseHTTPMiddleware` auth wrapper reintroduces the exact SSE/large-body streaming
corruption the codebase switched to pure-ASGI middleware to avoid (`mcp_server.py:565`).

### P1 — Decision-index integrity (data correctness on the configured Neo4j backend)  ✅ FIXED

- **B1 — Neo4j returns/indexes every decision twice.** `get_edges_with_relation_context` and
  `get_all_edges` use undirected `MATCH (a)-[r]-(b)`; `DISTINCT` can't dedupe swapped src/tgt.
  `reindex_decisions` then writes two `dec-`/`rel-` records per edge (ids differ by orientation),
  `GET /graph/decisions` lists each twice, and duplicates crowd out real hits in `find_precedents`.
  The commit's "365 rc-edges" was likely ~182. `neo4j_impl.py:~1648`, `context_graph.py:~807`.
  *(Reported independently by two reviewers — high confidence.)*
- **B2 — `decisions_vdb` is never persisted.** It's absent from `_insert_done`, no
  `index_done_callback` is ever called on it, and base `finalize()` is a no-op — so with
  NanoVectorDB every emitted decision vector is lost on restart, silently. `find_precedents` and
  the query blend return nothing after a bounce. `reindex_decisions` can repair but nothing calls
  it automatically. `context_graph.py:~565`, `lightrag.py:~2231`.
- **B3 — Emit `rel-` id isn't canonicalized.** Pipeline sorts (src,tgt) before hashing;
  `_index_decision` doesn't → two divergent `relationships_vdb` records for one edge whenever
  `src > tgt`. `context_graph.py:770` vs `operate.py:~2477`.
- **B4 — `reindex_decisions` is upsert-only.** Never removes `dec-*`/`rel-*` orphans for deleted
  edges, contradicting its "repairs drift / rebuild" docstring. `clear_documents` also drops 11
  stores but not `decisions_vdb`. Orphans permanently consume `top_k` slots. `context_graph.py:~796`,
  `document_routes.py:~2481`.

### P2 — `emit_decision_trace` correctness  ✅ FIXED

- **B5 — Merge silently discards the *new* decision.** `RelationContext.merge([existing, new])` is
  "first non-None wins", so re-emitting on an existing edge keeps the *old* trace/valid_until/
  approver and re-indexes the old text under the fixed `dec-` id. Inverts the pipeline's own
  new-wins convention. `context_graph.py:~698`, `context_graph_types.py:~135`.
- **B6 — Emit clobbers existing entity nodes.** Unconditional `upsert_node(description=name,
  entity_type="ENTITY")` overwrites a rich LLM-extracted profile with the literal name and type
  "ENTITY". `context_graph.py:~719`.
- **B7 — Gleaning can drop rc before merge.** When a glean pass re-emits an edge with a longer
  description but no 6th field, the record list is replaced and the rc is lost — residual gap in
  the H2 fix family. `context_graph.py:~442`.
- **B8 — `ingest_decision_summary` is a silent no-op on re-ingest** (deletes `full_docs` but
  `doc_status` dedups the re-add → filed as FAILED duplicate; old doc + update both lost). Should
  use `adelete_by_doc_id`. `context_graph.py:~869`.
- **B9 — Unlocked read-merge-write race** on edge rc in emit (pipeline serializes via
  `graph_db_lock`; emit doesn't) → concurrent emits lose one rc. `context_graph.py:~698`.

### P3 — Query-time blend & by-name injection (`aquery_llm`)  ✅ FIXED

- **C1 — `mode="naive"` blend crashes with `KeyError('context_data')`.** The override formats a
  `context_data`-keyed template, but `naive_query` supplies `content_data` → any naive query in a
  workspace with ≥1 decision 500s. `context_graph.py:~1041` vs `operate.py:~5257`.
- **C2 — Blend defeated by the LLM query cache.** The cache key excludes the system prompt, so the
  read-your-writes scenario the feature targets still serves the stale pre-decision answer.
  `context_graph.py:~1046` vs `operate.py:~3284`.
- **C3 — Empty-retrieval fallback never fires on the real empty path** (`fail_response` text
  contains neither trigger string) and the `"enough information"` substring heuristic clobbers
  legitimate partially-grounded answers, breaks `only_need_prompt`, and can flip a real failure to
  `success`. `context_graph.py:~1057`.
- **C4 — Caller `system_prompt` silently overwritten** despite the docstring saying blend is
  skipped when one is supplied — no such check exists. `context_graph.py:~1038`.
- **C5 — No token budget on injected blocks** (up to 8 traces + 6 node descriptions appended
  *after* context truncation) → can blow the context window. **C6** — by-name pass issues a Neo4j
  roundtrip per ≥4-char query word with no cap → hundreds of sequential calls on a paragraph query,
  worse against `--code` hub nodes. `context_graph.py:~929,1000`.

### P4 — Governance package (`context_graph/`)  ✅ FIXED

- **D1 — SSRF in the crawler.** The webhook path is guarded, but `/scrape` validates only
  `is_http()` and follows redirects — no private-IP/metadata block. An authenticated tenant can
  pull `169.254.169.254` / internal hosts into the graph. `webingest/fetch.py:~125`,
  `webingest_routes.py:~78`. (Webhook guard itself is TOCTOU/rebinding-vulnerable — **D2**,
  `actions/handler.py:~41`.)
- **D3 — Rules gate fails open with no field-name validation.** `validate_policy` checks concepts
  but not condition field names, and eval wraps every rule in `except→skip`. A typo'd field
  (`amont > 10000`) saves clean then silently never fires — a REJECT rule meant to block is a
  no-op with only a `warnings` audit entry. `rules/engine.py:~270`, `rules/store.py:~72`.
  Contradicts the documented `None→0` coercion contract (**D4**, `rules/projection.py:~20`).
- **D5 — Lifecycle transition is an unsynchronized read-modify-write.** Two concurrent `invoke`s
  on one object both validate against the same start state and both write → illegal combined
  transition + lost update, violating the spec's "hard 409" invariant. `actions/service.py:~120`,
  `lifecycle/service.py:~65`.

### P5 — Misc correctness / robustness  ✅ FIXED (except E7)

- **E1** `backfill_git.py` `detect_modules` ingests `venv/`/`dist/`/`vendor/`, mis-anchors the
  dependency graph on the biggest (often non-source) dir, ignores `.gitignore`, and counts all
  files; `URLError` kills the run mid-backfill; `errors="ignore"` mangles non-UTF-8/UTF-16 files.
- **E2** Background reindex `asyncio.create_task(_run())` is unreferenced (GC-able mid-run) and
  unguarded against concurrent rebuilds. `context_graph_routes.py:~1015`.
- **E3** `EmitDecisionRequest` (REST) has no field validation — empty `src`/`tgt` → empty-id nodes
  or 500; `confidence_score` unbounded (MCP clamps, REST doesn't) → `42` defeats every
  `min_confidence` filter. `context_graph_routes.py:~185`.
- **E4** `merge()` promotes an explicit `confidence_score=0.0` to `1.0`
  (`max if max>0 else 1.0`), and a corrupt-rc parse in `find_precedents` yields an all-None rc at
  default confidence `1.0` — both defeat confidence filtering. `context_graph_types.py:~192`,
  `context_graph.py:~915`.
- **E5** `is_active` string-compares dates, so an `as_of` with a time component reads as expired on
  the final valid day. `context_graph_types.py:~112`.
- **E6** Service-store dirs (`rules/`, `ontology/`, …) surface as tenants in `GET /workspaces`;
  a workspace literally named `rules` collides with the store. `lightrag_server.py:~1321`.
- **E7** `HTTPException(500, detail=str(e))` throughout leaks backend internals (Neo4j errors,
  paths) to clients.

---

## Recommended sequence (next steps)

1. **Decide the tenancy trust model, then close P0 as one workstream.** Bind principal→workspace,
   drop `/api/*` from the default whitelist (or gate it), stop guest tokens when an API key is set,
   add auth to `/workspaces*`, unify MCP auth with the REST combined dependency (and move it to
   pure-ASGI per A8), and give document routes a per-workspace `DocumentManager`. Write the missing
   two-workspace + auth integration test first — it reproduces A2–A6 and A7 and becomes the
   regression guard. *This is the single highest-value block.*
2. **Fix decision-index integrity (P1) together:** make the Neo4j edge queries directed/deduped
   (B1), add `decisions_vdb` to persist/drop/finalize (B2), canonicalize the emit `rel-` id (B3),
   and make reindex authoritative (delete orphans, B4). Add one integration test: emit → reindex →
   query → assert single, current, retrievable.
3. **Unify `emit_decision_trace` onto the pipeline merge helper (P2).** Reusing
   `_merge_edges_then_upsert`/`_collect_relation_context` collapses B5, B6, B8, B9 at once (new-wins
   merge, non-destructive node upsert, canonical ids, shared locking).
4. **Rework the blend as a context block, not prompt-string splicing (P3).** Pass decisions as a
   dedicated context section instead of rewriting `system_prompt`; fixes C1/C4 structurally,
   include the block in the cache key (C2), replace the substring fallback with an explicit
   empty-retrieval signal (C3), and add a token budget (C5) + a token cap on the by-name pass (C6).
5. **Harden governance boundaries (P4):** add a private-IP guard to the crawler (D1), validate rule
   field names at save time so the gate fails *closed* on authoring errors (D3), and lock the
   lifecycle read-modify-write (D5).
6. **Documentation sweep (remaining):** README still advertises a non-existent `POST /insert` and
   wrong env-var names / `DEFAULT_QUERY_MODE`; `docs/configuration.md` still lists Neo4j/gpt-5-mini
   defaults; `docs/api-reference.md`, `architecture.md`, `INDEX.md` omit the governance/onboarding
   surface; the two IMPLEMENTATION/TECHNICAL plans mark shipped Gaps 1–5 as pending. Reconcile
   against code (this pass fixed CLAUDE.md + the highest-risk copy-paste examples).
7. **Housekeeping:** rotate the OpenAI key currently in `.env` (real-looking, gitignored but
   surfaced during review); register the `asyncio` pytest marker to clear 100+ warnings; either
   commit `scripts/sync_all.py` or delete the CR-012 variant test; migrate the Pydantic
   class-based `Config` usages flagged as deprecated.

## Non-issues confirmed (checked, clean)

Storage-level workspace isolation (vector/KV/cache all proxy-scoped); no path traversal in backfill
or document routes; RBAC/lifecycle deny-by-default logic and JWT-only principal resolution (no
header spoofing); the blend's brace-escaping/template placeholders; ontology multiplicity
non-enforcement (documented as intentional). The `USE_CONTEXT_GRAPH=false` path is clean except the
`acreate_entity` fallback-description change (E-class, upstream-visible, untested).

---

## P1–P4 remediation (2026-07-06)

P0 (auth/tenancy) deferred by request. P1–P4 fixed with regression tests; suite is green at
**582 passed / 35 skipped**. Verify with
`/storage/conda/envs/lightgraph_custom/bin/python -m pytest tests context_graph/tests`.

**P1 — decision-index integrity**
- B1: `get_edges_with_relation_context` / `get_all_edges` now use a **directed** match
  (`-[r:DIRECTED]->`) so each edge returns once (`lightrag/kg/neo4j_impl.py`).
- B2: `decisions_vdb` added to a `_insert_done` override, and `emit`/`reindex` now call a new
  `_persist_decision_indices()` (flushes `decisions_vdb` + `relationships_vdb`) so runtime writes
  survive restart (`lightrag/context_graph.py`).
- B3: `_index_decision` canonicalizes the pair (sorted src/tgt) for both `rel-` and `dec-` ids and
  deletes the reverse-orientation record — one record per undirected edge, matching the pipeline.
- B4: `reindex_decisions` now **drops** `decisions_vdb` (orphan removal) then repopulates and
  persists; `clear_documents` also drops `decisions_vdb` (`lightrag/api/routers/document_routes.py`).

**P2 — `emit_decision_trace` correctness**
- B5: merge order flipped to `merge([new, existing])` — the new decision wins on scalar fields.
- B6: endpoint nodes are only created when absent (`get_node` check) — never clobber an extracted node.
- B7: gleaning preserves the first pass's `relation_context` when a longer glean description replaces
  the edge record.
- B8: `ingest_decision_summary` re-ingest uses `adelete_by_doc_id` (clears doc_status/chunks/graph),
  so an updated summary is no longer dropped as a duplicate.
- B9: the read-merge-write is serialized under the pipeline's per-edge keyed lock
  (`_graph_edge_lock`, degrades to no-op without shared storage).

**P3 — query blend & by-name injection (`aquery_llm`)**
- C1/C2/C4: the blend is injected into `param.user_prompt` instead of splicing the system-prompt
  template — mode-agnostic (no `naive` KeyError), cache-keyed (a new decision invalidates the cached
  answer), and it no longer overwrites a caller's `system_prompt`.
- C3: the empty-retrieval fallback keys strictly on the `[no-context]` marker (not the substring
  "enough information") and is skipped for `only_need_prompt`/`only_need_context`/streaming.
- C5: injected text is bounded (`_BLEND_CHAR_BUDGET`). C6: the by-name pass caps graph probes
  (`_BLEND_MAX_SCAN_TOKENS`) and stops at the precedent/named-node caps.

**P4 — governance boundaries**
- D1: added an SSRF guard (`is_public_url`) to the crawler — `StaticFetcher` blocks URLs (and
  post-redirect URLs) resolving to private/loopback/link-local/metadata addresses, on by default,
  resolver injectable for offline tests (`context_graph/webingest/`).
- D3: `validate_policy` now rejects rules referencing unknown decision fields at save time, so the
  gate fails **closed** on an authoring typo instead of silently skipping the rule
  (`context_graph/rules/store.py`).
- D5: transition invokes serialize their read-validate-apply under a per-object `Lifecycle` keyed
  lock (distinct from the `GraphDB` edge locks to avoid deadlock) (`context_graph/actions/service.py`).

**P5 (fixed):** E1 backfill now skips venv/dist/vendor/build dirs, prunes them in module
detection, and tolerates `URLError`; E2 the background reindex task is referenced (no GC) and
guarded against concurrent runs per workspace; E3 `EmitDecisionRequest` validates non-blank
src/tgt/relation_type and bounds `confidence_score` (422, verified live); E4 `merge()` keeps an
explicit `0.0` confidence; E5 `is_active` compares by calendar date (ignores a time component);
E6 governance store dirs (rules/ontology/actions/rbac/lifecycle) are excluded from `/workspaces`.

**Verified end-to-end against a live server** (NetworkX + gpt-4o, fresh workspace): insert →
extraction produced 4 RelationContext edges; all six retrieval modes (naive/local/global/hybrid/
mix/bypass) plus CGR3 and the `/query/auto` smart router return the decision context; emit +
re-emit confirmed B5 new-wins; **B2 persistence confirmed across a real restart** (this surfaced a
gap the mocked unit tests missed — `_persist_decision_indices` must flush the graph store too, now
fixed); read-your-writes and cache-aware blend (P3 C2) confirmed.

**Still open / not addressed here:** all of P0 (A1–A8, deferred by request), E7 (500 responses leak
`str(e)` — pervasive info-leak hardening), and D2 (webhook DNS-rebinding TOCTOU; the crawler guard
shares the same resolve-time limitation, noted in `is_public_url`'s docstring).
