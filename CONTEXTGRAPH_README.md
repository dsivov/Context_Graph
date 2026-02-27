# Context Graph

A **Context Graph (CG)** is LightRAG extended into a *system of decision* — a living record of not just *what* relationships exist between entities, but *why* they exist: the approvals, rationale, temporal validity, and provenance behind every link.

Where a standard knowledge graph stores triples `(head, relation, tail)`, a Context Graph stores contextual quadruples `(h, r, t, rc)` where `rc` is a **RelationContext** that captures the full decision lineage of each relationship.

---

## Table of Contents

1. [Core Concept](#core-concept)
2. [RelationContext — The Fourth Component](#relationcontext--the-fourth-component)
3. [Quick Start](#quick-start)
4. [Configuration](#configuration)
5. [Document Ingestion](#document-ingestion)
6. [Real-Time Decision Capture](#real-time-decision-capture)
7. [Querying](#querying)
   - [Standard Query](#standard-query)
   - [CGR3 Iterative Reasoning](#cgr3-iterative-reasoning)
   - [Precedent Search](#precedent-search)
   - [Decision Filtering](#decision-filtering)
8. [REST API Reference](#rest-api-reference)
9. [CRM Integration Patterns](#crm-integration-patterns)
10. [Architecture Notes](#architecture-notes)

---

## Core Concept

Standard RAG systems answer questions from retrieved text chunks. Context Graph answers questions from a graph that knows *the decision history* — who approved what, why, when, and under which policy.

```
Standard KG triple:
  (Sarah Chen) --[APPROVES]--> (MegaCorp)

Context Graph quadruple:
  (Sarah Chen) --[APPROVES]--> (MegaCorp)
    rc = {
      decision_trace:  "Approved 20% discount citing 5-year relationship + Salesforce competition",
      approved_by:     "Sarah Chen",
      approved_via:    "in_person",
      valid_from:      "2024-08-14",
      valid_until:     "2024-12-31",
      policy_ref:      "DiscountPolicy_Standard",
      quantitative_data: "20% discount",
      supporting_sentences: ["VP of Sales approved a 20% discount for MegaCorp's enterprise deal"],
      provenance:      "Slack #deals-review, August 14 2024",
      confidence_score: 0.97
    }
```

This enables queries that are impossible with plain RAG:
- *"Who approved discounts above 15% in Q3 2024?"*
- *"Find all pricing exceptions approved via Slack"*
- *"Are there precedents for waiving the standard payment terms?"*
- *"Which deals approved by VP_Smith are still active?"*

---

## RelationContext — The Fourth Component

`RelationContext` ([lightrag/context_graph_types.py](lightrag/context_graph_types.py)) is a dataclass with 11 fields:

| Field | Type | Description |
|---|---|---|
| `supporting_sentences` | `List[str]` | Verbatim quotes from source documents supporting this relationship |
| `temporal_info` | `str \| None` | Free-form validity period (e.g., `"Q4 2026"`, `"since 2020"`) |
| `quantitative_data` | `str \| None` | Numerical metrics (discount %, budget amount, count) |
| `decision_trace` | `str \| None` | The **why** — rationale, exception, override, or approval narrative |
| `approved_by` | `str \| None` | Entity name of the approver (e.g., `"VP_Smith"`, `"Finance_Team"`) |
| `approved_via` | `str \| None` | Approval channel: `slack`, `zoom`, `email`, `in_person`, `jira`, `system` |
| `valid_from` | `str \| None` | ISO-8601 effective date (`"YYYY-MM-DD"`) |
| `valid_until` | `str \| None` | ISO-8601 expiry date (`"YYYY-MM-DD"`) |
| `policy_ref` | `str \| None` | Policy name/ID followed or overridden (e.g., `"DiscountPolicy_Standard"`) |
| `provenance` | `str \| None` | Source reference: Slack thread ID, document section, call timestamp |
| `confidence_score` | `float` | Extraction reliability 0.0–1.0 (default `1.0`) |

### Key methods

```python
rc.is_active(as_of="2024-09-01")  # True if valid on this date
rc.is_empty()                      # True if no meaningful content
rc.to_text()                       # Human-readable for LLM prompts
rc.to_json() / rc.from_json(s)    # Serialisation
RelationContext.merge([rc1, rc2])  # Union sentences, first-non-None scalars, max confidence
```

---

## Quick Start

### Installation

```bash
# Install with API support
uv sync --extra api
```

### Python SDK

```python
import asyncio
from lightrag.context_graph import ContextGraph
from lightrag.context_graph_types import RelationContext
from lightrag.llm.openai import gpt_4o_mini_complete, openai_embed
from lightrag import QueryParam

async def main():
    cg = ContextGraph(
        working_dir="./cg_storage",
        llm_model_func=gpt_4o_mini_complete,
        embedding_func=openai_embed,
    )
    await cg.initialize_storages()

    # 1. Ingest documents — RelationContext extracted automatically
    await cg.ainsert(
        "During the Q3 2024 business review, Sarah Chen (VP of Sales) approved "
        "a 20% discount for MegaCorp's enterprise deal, citing their five-year "
        "relationship and a competing offer from Salesforce. The discount was "
        "valid until December 31, 2024. This was discussed in Slack #deals-review."
    )

    # 2. Standard query enriched by RelationContext
    result = await cg.aquery(
        "What discount did Sarah Chen approve for MegaCorp?",
        param=QueryParam(mode="hybrid"),
    )
    print(result)

    # 3. CGR3 multi-hop iterative reasoning
    answer = await cg.cgr3_query(
        "Why did we discount the MegaCorp deal and is it still valid?",
        max_iterations=3,
    )
    print(answer)

    # 4. Real-time decision capture from agent code
    await cg.emit_decision_trace(
        src="Alice",
        tgt="AcmeCorp",
        relation_type="WAIVES_PAYMENT_TERMS",
        rc=RelationContext(
            decision_trace="Waived 30-day payment terms; client committed to 3-year renewal",
            approved_by="Alice",
            approved_via="zoom",
            valid_from="2024-11-01",
            valid_until="2025-10-31",
            policy_ref="PaymentPolicy_Standard",
            confidence_score=1.0,
        ),
    )

    # 5. Find precedents for a new scenario
    precedents = await cg.find_precedents(
        "payment terms waived for renewal commitment",
        top_k=5,
    )
    for p in precedents:
        print(p["src_id"], "->", p["tgt_id"])
        print(p["relation_context"].decision_trace)

    # 6. Filter decisions by approver and date
    active_approvals = await cg.get_all_decisions(
        approved_by="Alice",
        active_as_of="2024-12-01",
    )

    await cg.finalize_storages()

asyncio.run(main())
```

---

## Configuration

### Environment variables (`.env` / server)

```env
# Enable Context Graph mode (required for all CG features)
USE_CONTEXT_GRAPH=true

# Maximum CGR3 iterations (default: 3)
CGR3_MAX_ITERATIONS=3
```

### Server startup

```bash
# Copy and configure
cp env.example .env
# Edit .env: set USE_CONTEXT_GRAPH=true plus your LLM/embedding config

lightrag-server
```

When `USE_CONTEXT_GRAPH=true`, the server instantiates a `ContextGraph` instead of `LightRAG`. All CG-exclusive API endpoints become active. Plain LightRAG endpoints (`/query`, `/insert`, `/graph/*`) continue to work unchanged.

---

## Document Ingestion

`ainsert()` works identically to standard LightRAG. The difference is in what happens during extraction: the LLM is instructed to produce a 6-field relation record where the 6th field is a compact JSON RelationContext object.

```python
await cg.ainsert([
    "The finance committee approved an exception to the standard discount cap "
    "for GlobalTech, allowing 25% off the list price for Q1 2025. The decision "
    "was ratified by CFO Johnson via email on January 5, 2025.",

    "Per company policy DiscountPolicy_v2, maximum field discount is 20% without "
    "VP approval. Exceptions require Finance Committee sign-off and must be logged "
    "in Salesforce within 48 hours.",
])
```

The LLM will extract, among others:
```json
{
  "supporting_sentences": ["approved an exception to the standard discount cap for GlobalTech, allowing 25% off"],
  "temporal_info": "Q1 2025",
  "quantitative_data": "25% discount",
  "decision_trace": "Exception to discount cap approved by Finance Committee for GlobalTech",
  "approved_by": "CFO Johnson",
  "approved_via": "email",
  "valid_from": "2025-01-05",
  "valid_until": "2025-03-31",
  "policy_ref": "DiscountPolicy_v2",
  "provenance": "Finance Committee approval email, January 5 2025",
  "confidence_score": 0.94
}
```

Batch insertion and all other `ainsert()` options work identically:

```python
await cg.ainsert(texts, file_paths=["q1_review.pdf", "policy.pdf"])
```

---

## Real-Time Decision Capture

`emit_decision_trace()` records a decision **at the moment it is made** — from agent code, workflow automation, or any runtime event — without needing to write and re-ingest a document.

```python
rc = RelationContext(
    decision_trace="Approved 30% discount — strategic account, 5-year renewal at risk",
    approved_by="Regional_VP",
    approved_via="slack",
    valid_from="2025-01-15",
    valid_until="2025-06-30",
    policy_ref="DiscountPolicy_v2",
    quantitative_data="30% discount",
    supporting_sentences=["VP approved override pending legal review"],
    provenance="Slack #exec-deals, Jan 15 2025",
    confidence_score=1.0,
)

await cg.emit_decision_trace(
    src="Regional_VP",
    tgt="StrategicAccount_XYZ",
    relation_type="APPROVES_EXCEPTION",
    rc=rc,
    upsert=True,   # default: merge with existing RC rather than overwrite
)
```

**What happens internally:**
1. Source and target nodes are created in the graph if they don't exist.
2. If `upsert=True` and an edge already exists, the new RC is **merged** with the stored one (sentences are unioned, scalar fields use first-non-None, confidence is the maximum).
3. The edge is written to the graph storage.
4. The `decision_trace` text is indexed in the `decisions` vector store, making it retrievable by `find_precedents()`.

---

## Querying

### Standard Query

All standard LightRAG query modes work unchanged. Retrieved edges are enriched by their RelationContext, giving the LLM more grounding for its answer.

```python
from lightrag import QueryParam

result = await cg.aquery(
    "What pricing exceptions were approved in Q1 2025?",
    param=QueryParam(mode="hybrid", top_k=60),
)
```

### CGR3 Iterative Reasoning

CGR3 (Retrieve → Rank → Reason) is Context Graph's iterative multi-hop query loop. It repeats up to `max_iterations` times, each pass potentially focusing on new entities identified in the previous reasoning step.

```python
answer = await cg.cgr3_query(
    query="Why was the GlobalTech discount exception approved, who authorised it, "
          "and is it still in force?",
    mode="hybrid",         # retrieval mode per iteration
    max_iterations=3,      # stop early if context is sufficient
    top_k=60,
)
```

**Loop steps per iteration:**
1. **Retrieve** — call `aquery(only_need_context=True)` to gather candidate entities and edges with their RelationContext.
2. **Rank** — ask the LLM to order candidates by relevance to the query.
3. **Reason** — ask the LLM whether context is sufficient; if yes, return the answer; if not, use the top-ranked entities as seeds for the next iteration.

### Precedent Search

Semantic search over all indexed `decision_trace` texts. Returns edges ranked by similarity to the query — ideal for *"has this type of exception been approved before?"*

```python
precedents = await cg.find_precedents(
    query_text="discount exception approved for strategic renewal at risk",
    top_k=10,
    min_confidence=0.7,
)

for p in precedents:
    rc = p["relation_context"]
    print(f"{p['src_id']} -> {p['tgt_id']}")
    print(f"  Decision: {rc.decision_trace}")
    print(f"  Approved by: {rc.approved_by} via {rc.approved_via}")
    print(f"  Valid: {rc.valid_from} → {rc.valid_until}")
    print(f"  Policy: {rc.policy_ref}")
```

### Decision Filtering

Enumerate all decision-bearing edges with structured filters. All filter parameters are ANDed.

```python
# All decisions approved by a specific person that are currently active
decisions = await cg.get_all_decisions(
    approved_by="CFO_Johnson",
    active_as_of="2025-02-01",      # uses valid_from / valid_until
    min_confidence=0.8,
)

# All decisions approved via Slack referencing a specific policy
decisions = await cg.get_all_decisions(
    approved_via="slack",
    policy_ref="DiscountPolicy_v2",
)

# All decisions (no filters) — full audit log
all_decisions = await cg.get_all_decisions()
```

---

## REST API Reference

All endpoints below require `USE_CONTEXT_GRAPH=true`. They return HTTP **503** when the server is running plain LightRAG, so clients can detect capability at runtime.

### Existing endpoints (enhanced)

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/query` | Standard query — edges include RelationContext in LLM context |
| `POST` | `/cgr3/query` | CGR3 iterative Retrieve→Rank→Reason |
| `GET`  | `/graph/edge/context` | RelationContext for a specific edge |
| `GET`  | `/graph/entity/edges-with-context` | All context-enriched edges for an entity |

### New Phase 5 endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/graph/decision/emit` | Record a decision trace into the graph at runtime |
| `GET`  | `/graph/decisions/search` | Semantic precedent search over decision traces |
| `GET`  | `/graph/decisions` | List/filter all decision-bearing edges |

---

### `POST /cgr3/query`

Iterative multi-hop reasoning over the Context Graph.

**Request:**
```json
{
  "query": "Why was the MegaCorp deal discounted and who approved it?",
  "mode": "hybrid",
  "max_iterations": 3,
  "top_k": 60,
  "include_references": true
}
```

**Response:**
```json
{
  "response": "The MegaCorp deal received a 20% discount approved by Sarah Chen (VP of Sales) on August 14, 2024. The rationale was MegaCorp's five-year relationship and a competing offer from Salesforce. The discount was valid until December 31, 2024.",
  "references": null
}
```

---

### `POST /graph/decision/emit`

Write a structured decision directly into the graph at runtime — for agent orchestration code, workflow triggers, and CRM webhooks.

**Request:**
```json
{
  "src": "Regional_VP",
  "tgt": "StrategicAccount_XYZ",
  "relation_type": "APPROVES_EXCEPTION",
  "relation_context": {
    "decision_trace": "Approved 30% discount — strategic account, 5-year renewal at risk",
    "approved_by": "Regional_VP",
    "approved_via": "slack",
    "valid_from": "2025-01-15",
    "valid_until": "2025-06-30",
    "policy_ref": "DiscountPolicy_v2",
    "quantitative_data": "30% discount",
    "provenance": "Slack #exec-deals, Jan 15 2025",
    "confidence_score": 1.0
  }
}
```

**Response:**
```json
{
  "status": "ok",
  "edge": "Regional_VP -> StrategicAccount_XYZ"
}
```

If the edge already exists, the RelationContexts are **merged**: `supporting_sentences` are unioned, scalar fields use first-non-None, `confidence_score` is the maximum.

---

### `GET /graph/edge/context`

Retrieve the full RelationContext stored on a specific graph edge.

**Request:**
```
GET /graph/edge/context?src=Sarah+Chen&tgt=MegaCorp
```

**Response:**
```json
{
  "src_id": "Sarah Chen",
  "tgt_id": "MegaCorp",
  "has_context": true,
  "relation_context": {
    "supporting_sentences": ["Sarah Chen (VP of Sales) approved a 20% discount for MegaCorp's enterprise deal"],
    "temporal_info": "Valid until December 31, 2024",
    "quantitative_data": "20% discount",
    "decision_trace": "Approved citing five-year relationship and competing offer from Salesforce",
    "approved_by": "Sarah Chen",
    "approved_via": "in_person",
    "valid_from": null,
    "valid_until": "2024-12-31",
    "policy_ref": null,
    "provenance": "Slack #deals-review, August 14 2024",
    "confidence_score": 0.97
  }
}
```

---

### `GET /graph/entity/edges-with-context`

List all edges connected to an entity that carry a RelationContext.

**Request:**
```
GET /graph/entity/edges-with-context?entity_name=Sarah+Chen
```

**Response:**
```json
{
  "entity_name": "Sarah Chen",
  "total_count": 2,
  "edges": [
    {
      "src_id": "Sarah Chen",
      "tgt_id": "MegaCorp",
      "keywords": "discount approval, deal negotiation",
      "description": "Sarah Chen approved a 20% discount for MegaCorp.",
      "weight": 1.5,
      "relation_context": { "..." }
    }
  ]
}
```

---

### `GET /graph/decisions/search`

Semantic vector search over indexed decision traces. Returns results ranked by similarity.

**Request:**
```
GET /graph/decisions/search?q=discount+approved+for+strategic+renewal&top_k=5&min_confidence=0.7
```

**Response:**
```json
{
  "query": "discount approved for strategic renewal",
  "total_count": 2,
  "results": [
    {
      "src_id": "Regional_VP",
      "tgt_id": "StrategicAccount_XYZ",
      "relation_context": {
        "decision_trace": "Approved 30% discount — strategic account, 5-year renewal at risk",
        "approved_by": "Regional_VP",
        "approved_via": "slack",
        "valid_from": "2025-01-15",
        "valid_until": "2025-06-30",
        "policy_ref": "DiscountPolicy_v2",
        "confidence_score": 1.0
      }
    }
  ]
}
```

---

### `GET /graph/decisions`

List all decision-bearing edges with optional structured filters. All parameters are ANDed.

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `approved_by` | `string` | Filter by approver entity name |
| `approved_via` | `string` | Filter by channel: `slack`, `zoom`, `email`, `in_person`, `jira`, `system` |
| `policy_ref` | `string` | Filter by policy name/ID |
| `min_confidence` | `float` | Minimum confidence score (default `0.0`) |
| `active_as_of` | `string` | ISO-8601 date — only include decisions valid on this date |

**Request:**
```
GET /graph/decisions?approved_by=CFO_Johnson&active_as_of=2025-02-01
```

**Response:**
```json
{
  "total_count": 3,
  "decisions": [
    {
      "src_id": "CFO_Johnson",
      "tgt_id": "GlobalTech",
      "relation_context": {
        "decision_trace": "Exception to discount cap approved for GlobalTech, Q1 2025",
        "approved_by": "CFO_Johnson",
        "approved_via": "email",
        "valid_from": "2025-01-05",
        "valid_until": "2025-03-31",
        "policy_ref": "DiscountPolicy_v2",
        "quantitative_data": "25% discount",
        "confidence_score": 0.94
      }
    }
  ]
}
```

---

## CRM Integration Patterns

### Pattern 1 — Post-meeting webhook (Salesforce / HubSpot)

Trigger `emit_decision_trace` immediately after a deal decision is logged in your CRM, so the Context Graph stays current without batch re-ingestion.

```python
# Example: Salesforce Apex trigger → webhook → Python handler
async def on_opportunity_approval(event: dict):
    """Called when an Opportunity discount is approved in Salesforce."""
    await cg.emit_decision_trace(
        src=event["approved_by_name"],
        tgt=event["account_name"],
        relation_type="APPROVES_DISCOUNT",
        rc=RelationContext(
            decision_trace=event["approval_comment"],
            approved_by=event["approved_by_name"],
            approved_via="system",
            valid_from=event["effective_date"],
            valid_until=event["expiry_date"],
            policy_ref=event.get("policy_applied"),
            quantitative_data=f"{event['discount_pct']}% discount",
            provenance=f"Salesforce Opportunity {event['opportunity_id']}",
            confidence_score=1.0,  # structured source, always 1.0
        ),
    )
```

### Pattern 2 — Slack approval bot

Capture approvals made in Slack channels in real time.

```python
# Example: Slack Events API handler
async def on_slack_approval_message(message: dict):
    """Triggered when /approve slash command is used in #deals-review."""
    parsed = parse_approval_message(message["text"])
    await cg.emit_decision_trace(
        src=parsed.approver,
        tgt=parsed.account,
        relation_type=parsed.decision_type,
        rc=RelationContext(
            decision_trace=parsed.rationale,
            approved_by=parsed.approver,
            approved_via="slack",
            valid_until=parsed.expiry,
            provenance=f"Slack #{message['channel_name']}, {message['ts']}",
            confidence_score=1.0,
        ),
    )
```

### Pattern 3 — Nightly document sync

For teams that work in documents (email threads, call transcripts, meeting notes), batch-ingest on a schedule and let the LLM extract RelationContext automatically.

```python
import asyncio
from pathlib import Path

async def nightly_ingest(docs_dir: str):
    texts = []
    file_paths = []
    for p in Path(docs_dir).glob("*.txt"):
        texts.append(p.read_text())
        file_paths.append(str(p))

    await cg.ainsert(texts, file_paths=file_paths)

# Run via cron or Celery beat
asyncio.run(nightly_ingest("./crm_exports/today"))
```

### Pattern 4 — Sales rep assistant (agent loop)

An AI sales assistant queries the Context Graph before making recommendations, ensuring it respects current policy and cites actual precedents.

```python
async def advise_on_discount(account: str, requested_pct: float) -> str:
    # 1. Check if there are precedents for this level of discount
    precedents = await cg.find_precedents(
        f"{requested_pct}% discount approved for enterprise account",
        top_k=5,
        min_confidence=0.7,
    )

    # 2. Check if account has active approved exceptions
    active = await cg.get_all_decisions(
        active_as_of=datetime.date.today().isoformat()
    )
    account_decisions = [d for d in active if d["tgt_id"] == account]

    # 3. Ask CGR3 for a holistic recommendation
    context = "\n".join(
        f"Precedent: {p['relation_context'].decision_trace}"
        for p in precedents
    )
    answer = await cg.cgr3_query(
        f"Should we approve a {requested_pct}% discount for {account}? "
        f"Relevant precedents:\n{context}",
    )
    return answer
```

### Pattern 5 — Compliance audit trail

Generate a complete audit report of all pricing exceptions in a date range.

```python
async def pricing_audit(from_date: str, to_date: str) -> list[dict]:
    """Return all pricing decisions active within the given date range."""
    all_decisions = await cg.get_all_decisions(
        policy_ref="DiscountPolicy_v2",
        active_as_of=from_date,
        min_confidence=0.5,
    )
    # Further filter by to_date client-side if needed
    return [
        {
            "approver": d["relation_context"].approved_by,
            "account": d["tgt_id"],
            "via": d["relation_context"].approved_via,
            "decision": d["relation_context"].decision_trace,
            "valid": f"{d['relation_context'].valid_from} → {d['relation_context'].valid_until}",
            "policy": d["relation_context"].policy_ref,
        }
        for d in all_decisions
    ]
```

---

## Architecture Notes

### Storage

Context Graph adds one vector store namespace on top of standard LightRAG:

| Namespace | Purpose |
|-----------|---------|
| `entities` | Entity embeddings |
| `relationships` | Relationship embeddings |
| `chunks` | Text chunk embeddings |
| `decisions` | Decision trace embeddings (CG-only) |

The `decisions` namespace is created automatically when `USE_CONTEXT_GRAPH=true`. It indexes only edges that have a `decision_trace` — either extracted from documents or written via `emit_decision_trace()`. This keeps the vector store small and focused.

All other storages (KV, graph, doc status) are shared with standard LightRAG and can use any supported backend (PostgreSQL, Neo4j, Qdrant, etc.).

### Backward Compatibility

- All new `RelationContext` fields (`approved_by`, `approved_via`, `valid_from`, `valid_until`, `policy_ref`) default to `None`.
- Existing graph data without these fields loads cleanly — `from_json()` ignores unknown fields and defaults missing ones.
- Standard LightRAG endpoints are unaffected; Context Graph endpoints return HTTP 503 when CG mode is off.

### LLM Extraction Quality

The system prompt instructs the LLM to populate the new fields only when evidence is present in the text:

- `approved_by` / `approved_via`: filled when the text explicitly names who approved something and how.
- `valid_from` / `valid_until`: filled when explicit dates are given.
- `policy_ref`: filled when a policy name or ID is referenced.

The few-shot example in the prompt (`Sarah Chen approved a 20% discount...`) demonstrates all fields being populated correctly. For best results, use a model with at least 32B parameters and 32K context.

### Confidence Score

`confidence_score` (0.0–1.0) reflects extraction reliability:
- `1.0` — always set for decisions captured via `emit_decision_trace()` (structured source)
- `0.9+` — explicit statement in the source text
- `0.7–0.9` — implicit or inferred from context
- `< 0.7` — ambiguous; consider filtering with `min_confidence`
