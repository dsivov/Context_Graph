# CGR3 Paper Alignment Analysis

**Paper:** Liang et al., "Context Graphs: A Framework for Knowledge Graph Completion and Question Answering with Contextual Information" ([arXiv:2406.11160v3](https://arxiv.org/abs/2406.11160))

**Implementation:** Context Graph (this repository) — a production fork of LightRAG implementing the CGR3 paradigm.

---

## Paper Summary

The paper introduces **Context Graphs (CGs)** — an extension of standard knowledge graphs that augments triples `(h, r, t)` with contextual metadata to form **factual quadruples** `(h, r, t, rc)`. The key insight is that traditional KGs lose critical context (temporal validity, provenance, quantitative data, confidence) when reducing unstructured text to triples.

The paper proposes the **CGR3 paradigm** (Context Graphs with Retrieve, Rank, Reason) for two tasks:
- **KG Completion (KGC):** Single-hop — retrieve supporting triples, rank candidates
- **KGQA:** Multi-hop iterative — retrieve, rank, reason about sufficiency, iterate

Key results: **+33% Hits@1** on FB15k237 and **+43.6% EM** on KGQA tasks when incorporating contextual information.

---

## Concept-by-Concept Alignment

### 1. Factual Quadruples `(h, r, t, rc)`

**Paper:** Defines `CG = {E, R, Q, EC, RC}` where each quadruple `(h, r, t, rc) ∈ Q` carries relation context `rc ∈ RC`.

**Our implementation:** Every graph edge stores a `RelationContext` dataclass with 11 fields:

| Paper Context Type | Our Field | Example |
|---|---|---|
| Temporal information | `temporal_info`, `valid_from`, `valid_until` | `"Q4 2024"`, `"2024-08-14"`, `"2024-12-31"` |
| Quantitative data | `quantitative_data` | `"20% discount"`, `"₪5,000-8,000 range"` |
| Provenance | `provenance` | `"Slack #deals-review, Aug 14 2024"` |
| Confidence level | `confidence_score` | `0.97` |
| Supporting sentences | `supporting_sentences` | Verbatim quotes from source documents |

**Extensions beyond paper:**

| Our Field | Purpose | Rationale |
|---|---|---|
| `decision_trace` | Captures *why* a relationship exists | Production decision systems need rationale, not just facts |
| `approved_by` | Names the approver entity | Audit trail for compliance |
| `approved_via` | Approval channel (slack, email, zoom...) | Channel analytics |
| `policy_ref` | Policy name/ID referenced | Compliance verification |

**Verdict:** Faithful implementation with production-oriented extensions.

---

### 2. CGR3: Retrieve → Rank → Reason

**Paper:** Three-step iterative process for KGQA:
1. **Retrieve** — gather supporting triples and textual contexts using embedding similarity
2. **Rank** — re-rank candidates using fine-tuned LLM (LoRA-adapted LLaMA-3)
3. **Reason** — determine if context is sufficient; if not, identify follow-up entities and iterate (beam search with width M, max depth D=3)

**Our implementation** (`context_graph.py::cgr3_query()`):
1. **Retrieve** — call `aquery(only_need_context=True)` with configurable mode (local/global/hybrid)
2. **Rank** — LLM prompt to order candidates by relevance to query
3. **Reason** — LLM evaluates context sufficiency; if insufficient, extracts `follow_up_entities` for next iteration. Max 3 iterations with early stopping.

| Aspect | Paper | Ours |
|---|---|---|
| Retrieval method | KGE embeddings (ComplEx, RotatE, GIE) | Semantic text embeddings (text-embedding-3-large) |
| Ranking method | Fine-tuned LLM (LoRA on LLaMA-3) | Zero-shot LLM prompt (gpt-4.1-mini) |
| Iteration control | Beam search (width M, depth D_max=3) | Iterative seed refinement (max_iterations=3) |
| Stopping criterion | LLM determines sufficiency | LLM determines sufficiency |
| Context assembly | Supporting triples + Wikipedia text | KG entities + relations + text chunks + RelationContext |

**Key difference:** The paper uses learned KGE embeddings for initial retrieval and LoRA fine-tuning for ranking. We use general-purpose text embeddings and zero-shot LLM prompting. This trades some task-specific accuracy for deployment simplicity (no training pipeline needed).

**Verdict:** Faithful in structure and intent. Adapted for production use without task-specific training.

---

### 3. Context Extraction

**Paper:** Entity contexts from Wikidata (labels, descriptions, aliases, Wikipedia pages). Relation contexts from Sentence-BERT similarity over head/tail entity Wikipedia pages — top-γ supporting sentences.

**Our implementation:** LLM-based extraction from ingested documents. The LLM produces 6-field relation records where the 6th field is a compact JSON RelationContext:

```
relation<|#|>source_entity<|#|>target_entity<|#|>keywords<|#|>description<|#|>RC_JSON
```

**Key difference:** The paper assumes a structured knowledge source (Wikidata) for context extraction. We extract context from unstructured documents using an LLM, which is more general but relies on extraction quality.

**Verdict:** Adapted for document-first workflows (no Wikidata dependency).

---

### 4. Entity Context

**Paper:** Rich entity representations from Wikidata: types, descriptions, aliases, Wikipedia pages, images, speeches, videos.

**Our implementation:** Entity descriptions extracted by LLM from ingested documents. Entity embeddings stored in vector DB for semantic retrieval.

**Key difference:** We don't have a structured entity database like Wikidata. Instead, entity context is built incrementally from ingested documents.

**Verdict:** Simplified — sufficient for domain-specific applications where entity descriptions emerge from document content.

---

### 5. Embedding Strategy

**Paper:** Task-specific KGE models (ComplEx, RotatE, GIE) trained on the graph structure. Entity descriptions encoded with Sentence-BERT for re-ranking.

**Our implementation:** General-purpose text embeddings (OpenAI text-embedding-3-large, 3072 dimensions) for entities, relationships, text chunks, and decision traces. No training required.

| Aspect | Paper | Ours |
|---|---|---|
| Entity embeddings | ComplEx/RotatE (learned from graph structure) | Text embedding of entity description |
| Relation embeddings | Part of KGE model | Text embedding of relation description |
| Context embeddings | Sentence-BERT | Same embedding model as entities |
| Training needed | Yes (KGE model training) | No |
| Similarity measure | Learned distance function | Cosine similarity |

**Verdict:** Adapted — general-purpose embeddings trade task-specific accuracy for zero-training deployment.

---

### 6. Temporal Filtering

**Paper:** Mentions temporal information as a context type but does not describe explicit filtering mechanisms.

**Our implementation:** `RelationContext.is_active(as_of)` performs date-range checks against `valid_from` and `valid_until` fields. `get_all_decisions(active_as_of=...)` uses this for temporal filtering.

**Verdict:** Extension — we implement what the paper describes but doesn't operationalize.

---

### 7. Incremental Updates

**Paper:** Not addressed.

**Our implementation:** Two update paths:
1. **Document ingestion** — `ainsert()` processes new documents incrementally (deduplication by document hash)
2. **Real-time emission** — `emit_decision_trace()` writes decisions directly to the graph with merge semantics

**Verdict:** Extension — production systems need incremental updates, which the paper doesn't address.

---

## Extensions Beyond the Paper

These features are not present in the CGR3 paper but are essential for production deployment:

### Multi-Tenancy
`WorkspacePool` with lazy initialization, per-tenant Neo4j label isolation, separate vector and KV stores per workspace. Enables serving ~50 companies from a single server instance.

### Privacy-Preserving Conversation Enrichment
Anonymization pipeline that strips PII (names, amounts, dates, phone numbers) from customer conversations before ingestion. Documents are framed as behavioral patterns rather than individual events. See [ConversationEnrichment.md](ConversationEnrichment.md).

### Size-Aware Query Routing
For small catalogs (<100 products), bypasses the graph entirely and sends the full catalog to the LLM. An `auto` router uses LLM classification to select the optimal query mode per request.

### Real-Time Decision Capture
`emit_decision_trace()` enables writing decisions into the graph at the moment they're made, from agent code, webhooks, or workflow automation — without document re-ingestion.

### Precedent Search
`find_precedents()` performs semantic vector search over indexed `decision_trace` texts, enabling "has this been done before?" queries.

---

## Evaluation Comparison

| Metric | Paper (FB15k237) | Our System (Production) |
|---|---|---|
| Baseline vs context | +33% Hits@1 | +11% relevance (hybrid vs naive) |
| Best retrieval mode | N/A (single mode) | hybrid (78.1% relevance) |
| Worst retrieval mode | N/A | naive (70.4% relevance) |
| Enrichment impact | +43.6% EM on KGQA | Enables entirely new query types |
| Latency | Not reported | 3.2s (hybrid), 8.1s (cgr3) |

---

## Conclusion

Our implementation is **faithful to the CGR3 paper's core paradigm** — factual quadruples with Retrieve→Rank→Reason iterative reasoning. We adapt the academic approach for production by replacing task-specific embeddings with general-purpose ones, LLM-based extraction instead of Wikidata, and zero-shot prompting instead of LoRA fine-tuning. We extend significantly with multi-tenancy, privacy, real-time decision capture, and conversation enrichment — features essential for production deployment that the paper's academic scope does not address.
