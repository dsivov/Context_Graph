# Context Graph Algorithm

## Indexing Pipeline

![LightRAG Indexing Flowchart](https://learnopencv.com/wp-content/uploads/2024/11/LightRAG-VectorDB-Json-KV-Store-Indexing-Flowchart-scaled.jpg)
*Figure 1: LightRAG Indexing Flowchart — [Source](https://learnopencv.com/lightrag/)*

### Steps

1. **Document chunking** — Split input text into chunks (default 1200 tokens, 100 overlap)
2. **Entity/relation extraction** — LLM extracts entities (4-field) and relations (5-field standard, 6-field CG with RelationContext JSON)
3. **Deduplication** — Merge entities/relations by normalized name; LLM summarizes merged descriptions when count exceeds threshold
4. **Graph insertion** — Entities become graph nodes, relations become edges. In CG mode, edges carry `relation_context` JSON property
5. **Vector indexing** — Entity descriptions, relation descriptions, text chunks, and (CG-only) decision traces are embedded and stored in vector DB
6. **KV storage** — Full documents and chunks stored in key-value store for citation retrieval

### Context Graph Extension (6-field extraction)

Standard LightRAG extracts 5 fields per relation:
```
relation<|#|>source<|#|>target<|#|>keywords<|#|>description
```

Context Graph extraction adds a 6th field — a compact JSON `RelationContext`:
```
relation<|#|>source<|#|>target<|#|>keywords<|#|>description<|#|>{"supporting_sentences":[...],"temporal_info":"...","decision_trace":"...","confidence_score":0.95}
```

Both formats are accepted — 5-field relations get `relation_context = null`, ensuring backward compatibility.

---

## Retrieval Pipeline

![LightRAG Retrieval Flowchart](https://learnopencv.com/wp-content/uploads/2024/11/LightRAG-Querying-Flowchart-Dual-Level-Retrieval-Generation-Knowledge-Graphs-scaled.jpg)
*Figure 2: LightRAG Dual-Level Retrieval — [Source](https://learnopencv.com/lightrag/)*

### Query Modes

| Mode | Retrieval Strategy |
|---|---|
| **local** | Extract low-level keywords → match entities → expand to connected relations → fetch chunks |
| **global** | Extract high-level keywords → match relations → expand to connected entities → fetch chunks |
| **hybrid** | Run both local and global, combine results |
| **naive** | Pure vector similarity search over text chunks (no graph) |
| **mix** | KG retrieval + vector chunk search + reranking |
| **bypass** | Skip retrieval, send query directly to LLM |

### Context Assembly

For each mode, the retrieval pipeline builds a context string:
1. **Entity context** — descriptions and properties of matched entities (budget: `max_entity_tokens`)
2. **Relation context** — descriptions of matched relationships, including RelationContext in CG mode (budget: `max_relation_tokens`)
3. **Chunk context** — relevant text chunks for grounding (budget: `max_total_tokens - entity - relation`)

### CGR3 Iterative Reasoning

```
Query ──▶ [Retrieve] ──▶ [Rank] ──▶ [Reason] ──▶ Answer
              ▲                         │
              │      follow_up_entities │
              └─────────────────────────┘
              (repeat up to max_iterations)
```

1. **Retrieve** — Standard mode retrieval (local/global/hybrid) returns candidate entities and relations
2. **Rank** — LLM orders candidates by relevance to the query
3. **Reason** — LLM evaluates if context is sufficient:
   - If yes → synthesize final answer
   - If no → extract `follow_up_entities`, add to seed list, repeat

Max 3 iterations. Context accumulates across iterations (deduped).

---

## Research Reference

Based on [CGR3: Context Graphs](https://arxiv.org/abs/2406.11160) (Liang et al., 2024). See [PaperComparison.md](PaperComparison.md) for detailed alignment analysis.
