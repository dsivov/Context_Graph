# Ingestion & querying

## Ingestion — two paths, one graph

### Extraction path (documents)

Best for historical records — transcripts, email threads, meeting notes. The LLM extracts
entities, relations, and a RelationContext per relationship during `ainsert()`.

```python
from lightrag.context_graph import ContextGraph
from lightrag import QueryParam

cg = ContextGraph(working_dir="./cg_storage", llm_model_func=..., embedding_func=...)
await cg.initialize_storages()
await cg.ainsert("In August 2024, Sarah Chen approved a 20% discount for MegaCorp …")
```

Pipeline: chunk (1200 tokens, 100 overlap) → extract entities (4-field) + relations
(5-field, or 6-field with rc JSON) → dedup/merge → write graph edges (`relation_context`) +
vector embeddings + KV.

### Emission path (runtime decisions)

Best for the moment a decision is made — approval bots, webhooks, workflow systems. No document
ingestion; rc is written directly.

```python
from lightrag.context_graph_types import RelationContext

rc = RelationContext(
    decision_trace="VP approved 20% discount; 5-yr relationship + competitive pressure",
    approved_by="Sarah Chen", approved_via="in_person",
    valid_until="2024-12-31", policy_ref="DiscountPolicy_Standard",
    quantitative_data="20% discount", confidence_score=0.97,
)
await cg.emit_decision_trace("Sarah Chen", "MegaCorp", "discount_approval", rc)
```

Both paths feed the same graph and the same vector indexes, so extracted and emitted decisions
are queried together. Emitted decisions are also embedded into the **decision-trace index** for
precedent search.

> Note (see [CODE_REVIEW.md](CODE_REVIEW.md) H2): deleting a document currently rebuilds the graph
> through the 5-field parser, which drops CG relations + rc. Treat document deletion with caution
> until that path is CG-aware.

## Querying

### Query modes

| Mode | Strategy |
|------|----------|
| `local` | Low-level keywords → entities → connected relations → chunks |
| `global` | High-level keywords → relations → connected entities → chunks |
| `hybrid` | local + global combined |
| `naive` | Pure vector similarity over chunks (no graph) |
| `mix` | KG retrieval + vector chunks + reranking (recommended with a reranker) |
| `bypass` | Skip retrieval; query straight to the LLM |

```python
res = await cg.aquery("Who approved discounts above 15% in Q3?",
                      param=QueryParam(mode="hybrid"))
```

In CG mode, retrieved edges carry their RelationContext, and the context is assembled in the
**annotated** format by default — rc rendered inline so the LLM can ground its answer in the
decision lineage. `context_format="legacy"` reverts to the plain format.

### CGR3 — Retrieve → Rank → Reason

For multi-hop questions whose answer depends on entities not yet retrieved:

```python
answer = await cg.cgr3_query(
    "Are there precedents for waiving standard payment terms for a renewal commitment?")
```

1. **Retrieve** — fetch candidate entities + edges (with rc).
2. **Rank** — the LLM orders candidates by relevance.
3. **Reason** — the LLM decides whether the accumulated context is sufficient; if not, it names
   `follow_up_entities` that seed the next retrieval.

Repeats up to `max_iterations` (default 3); context accumulates and is deduped across passes.

> Note (see [CODE_REVIEW.md](CODE_REVIEW.md) C1/H1): the reason-step JSON parser mishandles fenced
> output and can crash on non-object JSON. Apply those fixes for CGR3 iteration to work reliably.

## Decisions & precedents

- `emit_decision_trace(h, t, relation, rc)` — record a decision at runtime.
- `find_precedents(query, top_k=10, min_confidence=0.0)` — semantic search over decision traces.
- `is_active(as_of)` on a RelationContext — check temporal validity.

These surface over REST as `/graph/decision/emit`, `/graph/decisions/search`, and `/graph/decisions`
(filter by approver, channel, policy, confidence, date) — see [api-reference.md](api-reference.md).
