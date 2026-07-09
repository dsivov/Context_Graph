# Context Graph

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.12](https://img.shields.io/badge/Python-3.12-3776AB.svg?logo=python&logoColor=white)](pyproject.toml)
[![FastAPI](https://img.shields.io/badge/API-FastAPI-009688.svg?logo=fastapi&logoColor=white)](lightrag/api)
[![WebUI: React](https://img.shields.io/badge/WebUI-React%2019-61DAFB.svg?logo=react&logoColor=black)](lightrag_webui)
[![Built on LightRAG](https://img.shields.io/badge/built%20on-LightRAG-6E56CF.svg)](https://github.com/HKUDS/LightRAG)

**A decision-aware knowledge graph system built on LightRAG.**

Context Graph extends the standard triple-based knowledge graph `(head, relation, tail)` into contextual quadruples `(h, r, t, rc)` — where `rc` is a **RelationContext** that captures the full decision lineage behind every graph edge: who approved it, why, via which channel, under which policy, and for how long.

The result is a *system of decision*, not just a system of record: a living, queryable archive of organizational knowledge that knows not only what relationships exist, but the operational reality behind them.

<p align="center">
  <img src="docs/screenshots/dashboard.png" alt="Context Graph dashboard — graph health, quality metrics, and the decision ledger" width="900">
</p>
<p align="center"><em>The dashboard: graph health, quality, and the decision ledger — every edge backed by its <code>(h, r, t, rc)</code> quadruple.</em></p>

---

## Table of Contents

- [Why Context Graph](#why-context-graph)
- [Core Concepts](#core-concepts)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [Configuration](#configuration)
- [Document Ingestion](#document-ingestion)
- [Web Ingestion](#web-ingestion)
- [Real-Time Decision Capture](#real-time-decision-capture)
- [Governance & Actions](#governance--actions)
- [AI Agent Development](#ai-agent-development)
- [Querying](#querying)
- [REST API](#rest-api)
- [Integration Patterns](#integration-patterns)
- [Storage Backends](#storage-backends)
- [LLM Providers](#llm-providers)
- [Testing](#testing)
- [Architecture](#architecture)
- [Based On](#based-on)

---

## Why Context Graph

Standard RAG retrieves relevant text chunks and lets the LLM answer from them. Standard knowledge graphs store `(subject, predicate, object)` triples. Neither captures *why* the relationship exists.

Consider a pricing decision:

```
Standard KG:    Sarah Chen  --APPROVES-->  MegaCorp

Context Graph:  Sarah Chen  --APPROVES-->  MegaCorp
                  rc = {
                    decision_trace:  "Approved 20% discount citing 5-year relationship
                                      and Salesforce competitive pressure",
                    approved_by:     "Sarah Chen",
                    approved_via:    "in_person",
                    valid_from:      "2024-08-14",
                    valid_until:     "2024-12-31",
                    policy_ref:      "DiscountPolicy_Standard",
                    quantitative_data: "20% discount",
                    provenance:      "Slack #deals-review, Aug 14 2024",
                    confidence_score: 0.97
                  }
```

<p align="center">
  <img src="docs/assets/fig1-triple-vs-quadruple.svg" alt="Standard triple versus a Context Graph quadruple" width="820">
</p>
<p align="center"><em><b>Figure 1.</b> Same edge, two memories. The triple knows an approval happened; the quadruple knows the decision behind it — and can be queried on any of those fields.</em></p>

With Context Graph you can answer questions like:

- *"Who approved discounts above 15% in Q3 2024, and are those approvals still valid?"*
- *"Find all pricing exceptions approved via Slack in the last 6 months"*
- *"Are there precedents for waiving standard payment terms for a renewal commitment?"*
- *"Which deals approved by the Regional VP are still active today?"*
- *"Show me every decision that references DiscountPolicy_v2"*

None of these are answerable from text chunks alone.

---

## Core Concepts

### The Contextual Quadruple `(h, r, t, rc)`

Every relationship in a Context Graph is a four-component structure:

| Component | Meaning |
|-----------|---------|
| `h` | Head entity (source) |
| `r` | Relation type / keyword |
| `t` | Tail entity (target) |
| `rc` | **RelationContext** — the decision record |

### RelationContext

`RelationContext` ([context_graph/types.py](context_graph/types.py)) is a dataclass with 11 fields:

| Field | Type | Description |
|-------|------|-------------|
| `supporting_sentences` | `List[str]` | Verbatim quotes from source documents |
| `temporal_info` | `str \| None` | Free-form validity period (`"Q4 2026"`, `"since 2020"`) |
| `quantitative_data` | `str \| None` | Numerical metrics (discount %, budget, count) |
| `decision_trace` | `str \| None` | The **why** — rationale, exception, override, approval |
| `approved_by` | `str \| None` | Approver entity name (`"VP_Smith"`, `"Finance_Team"`) |
| `approved_via` | `str \| None` | Approval channel: `slack` `zoom` `email` `in_person` `jira` `system` |
| `valid_from` | `str \| None` | ISO-8601 effective date (`"YYYY-MM-DD"`) |
| `valid_until` | `str \| None` | ISO-8601 expiry date (`"YYYY-MM-DD"`) |
| `policy_ref` | `str \| None` | Policy name/ID followed or overridden |
| `provenance` | `str \| None` | Source reference: thread ID, doc section, call timestamp |
| `confidence_score` | `float` | Extraction reliability 0.0–1.0 (default `1.0`) |

<p align="center">
  <img src="docs/assets/fig2-relationcontext-anatomy.svg" alt="RelationContext anatomy — the 11 fields grouped by what they answer" width="820">
</p>
<p align="center"><em><b>Figure 2.</b> The 11 RelationContext fields, grouped by what they answer — where it came from, who decided, and how long it holds.</em></p>

### Two Paths to RelationContext

**Extraction path** — ingest prose documents via `ainsert()`. The LLM extracts entities, relationships, and a RelationContext JSON object from each sentence. Suitable for historical records, call transcripts, email threads, meeting notes.

**Emission path** — call `emit_decision_trace()` from agent or application code at the exact moment a decision is made. The RelationContext is written directly and atomically into the graph. Suitable for webhooks, approval bots, workflow systems.

Both paths feed the same graph and the same vector indexes.

<p align="center">
  <img src="docs/assets/fig3-two-capture-paths.svg" alt="Two capture paths — extraction from prose and direct emission" width="820">
</p>
<p align="center"><em><b>Figure 3.</b> Extraction mines RelationContext from prose at ingestion; emission writes it directly at decision time. Both land in the same graph and indexes.</em></p>

### CGR3 Reasoning

CGR3 (Retrieve → Rank → Reason) is Context Graph's iterative multi-hop query loop. It repeats up to `max_iterations` times, each pass potentially discovering new entities that become seeds for the next retrieval:

1. **Retrieve** — fetch candidate entities and edges with their RelationContext
2. **Rank** — ask the LLM to order candidates by relevance
3. **Reason** — ask the LLM whether the accumulated context is sufficient to answer; if not, identify follow-up entities and repeat

<p align="center">
  <img src="docs/assets/fig4-cgr3-loop.svg" alt="The CGR3 Retrieve-Rank-Reason loop" width="800">
</p>
<p align="center"><em><b>Figure 4.</b> Each pass can surface new entities that seed the next retrieval, so the graph walk deepens until the context is sufficient (or <code>max_iterations</code> is hit).</em></p>

---

## Installation

### Docker (recommended)

The repo ships a `Dockerfile` and `docker-compose.yml` — the fastest way to a running server + WebUI:

```bash
git clone https://github.com/dsivov/Context_Graph.git
cd Context_Graph
cp env.example .env          # set USE_CONTEXT_GRAPH=true + your LLM/embedding keys
docker compose up -d         # server + WebUI on http://localhost:9621/webui/
```

See [`docs/DockerDeployment.md`](docs/DockerDeployment.md) for storage backends and production notes.

### From source (Python 3.12)

```bash
git clone https://github.com/dsivov/Context_Graph.git
cd Context_Graph

uv sync --extra api                     # API server support (recommended)
# or: uv sync --extra api --extra offline-storage --extra offline-llm --extra test
source .venv/bin/activate
lightrag-server --host 0.0.0.0 --port 9621
```

**Requirements:**
- Python 3.12
- An LLM with at least 32B parameters and 32K context window (e.g. `gpt-4o`)
- An embedding model (e.g., `text-embedding-3-large`, `BAAI/bge-m3`)

---

## Quick Start

### Python SDK

```python
import asyncio
from lightrag import ContextGraph, QueryParam, RelationContext
from lightrag.llm.openai import gpt_4o_mini_complete, openai_embed

async def main():
    cg = ContextGraph(
        working_dir="./cg_storage",
        llm_model_func=gpt_4o_mini_complete,
        embedding_func=openai_embed,
    )
    await cg.initialize_storages()

    # ── 1. Ingest a document ─────────────────────────────────────────────
    await cg.ainsert(
        "During the Q3 2024 business review, Sarah Chen (VP of Sales) approved "
        "a 20% discount for MegaCorp's enterprise deal, citing their five-year "
        "relationship and a competing offer from Salesforce. The discount was "
        "valid until December 31, 2024. Discussed in Slack #deals-review."
    )

    # ── 2. Standard query (edges enriched by RelationContext) ────────────
    result = await cg.aquery(
        "What discount did Sarah Chen approve for MegaCorp?",
        param=QueryParam(mode="hybrid"),
    )
    print(result)

    # ── 3. CGR3 iterative multi-hop reasoning ────────────────────────────
    answer = await cg.cgr3_query(
        "Why was the MegaCorp deal discounted and is the approval still valid?",
        max_iterations=3,
    )
    print(answer)

    # ── 4. Real-time decision capture ────────────────────────────────────
    await cg.emit_decision_trace(
        src="Regional_VP",
        tgt="StrategicAccount_XYZ",
        relation_type="APPROVES_EXCEPTION",
        rc=RelationContext(
            decision_trace="Approved 30% discount — 5-year renewal at risk",
            approved_by="Regional_VP",
            approved_via="slack",
            valid_from="2025-01-15",
            valid_until="2025-06-30",
            policy_ref="DiscountPolicy_v2",
            quantitative_data="30% discount",
            provenance="Slack #exec-deals, Jan 15 2025",
            confidence_score=1.0,
        ),
    )

    # ── 5. Semantic precedent search ─────────────────────────────────────
    precedents = await cg.find_precedents(
        "discount approved for strategic renewal at risk",
        top_k=5,
        min_confidence=0.7,
    )
    for p in precedents:
        rc = p["relation_context"]
        print(f"{p['src_id']} -> {p['tgt_id']}: {rc.decision_trace}")

    # ── 6. Structured decision filter ────────────────────────────────────
    active = await cg.get_all_decisions(
        approved_by="Regional_VP",
        active_as_of="2025-03-01",
    )
    print(f"{len(active)} active decisions by Regional_VP")

    await cg.finalize_storages()

asyncio.run(main())
```

### API Server

```bash
# Configure
cp env.example .env
# Edit .env — set USE_CONTEXT_GRAPH=true and your LLM/embedding credentials

# Start server
lightrag-server

# Or with hot reload for development
uvicorn lightrag.api.lightrag_server:app --reload
```

---

## Configuration

### Environment Variables

Create a `.env` file (see `env.example` for the full template):

```env
# ── Context Graph ─────────────────────────────────────────────────────────
USE_CONTEXT_GRAPH=true          # Enable CG mode (required for CG features)
CGR3_MAX_ITERATIONS=3           # Max CGR3 reasoning iterations (default: 3)

# ── LLM ───────────────────────────────────────────────────────────────────
LLM_BINDING=openai
LLM_MODEL=gpt-4o
LLM_BINDING_HOST=https://api.openai.com/v1
LLM_API_KEY=sk-...

# ── Embedding ─────────────────────────────────────────────────────────────
EMBEDDING_BINDING=openai
EMBEDDING_MODEL=text-embedding-3-large
EMBEDDING_DIM=3072
EMBEDDING_API_KEY=sk-...

# ── Storage ───────────────────────────────────────────────────────────────
# File-based (default, no setup needed):
KV_STORAGE=JsonKVStorage
VECTOR_STORAGE=NanoVectorDBStorage
GRAPH_STORAGE=NetworkXStorage
DOC_STATUS_STORAGE=JsonDocStatusStorage

# PostgreSQL (production):
# KV_STORAGE=PGKVStorage
# VECTOR_STORAGE=PGVectorStorage
# GRAPH_STORAGE=Neo4JStorage
# POSTGRES_URL=postgresql://user:pass@host:5432/db

# ── Query Defaults ────────────────────────────────────────────────────────
TOP_K=60
MAX_TOTAL_TOKENS=32768
DEFAULT_QUERY_MODE=hybrid
```

### Python Constructor

```python
cg = ContextGraph(
    working_dir="./cg_storage",
    workspace="project_name",           # namespace isolation
    llm_model_func=gpt_4o_mini_complete,
    embedding_func=openai_embed,
    kv_storage="PGKVStorage",           # override defaults
    vector_storage="PGVectorStorage",
    graph_storage="Neo4JStorage",
    doc_status_storage="PGDocStatusStorage",
)
```

---

## Document Ingestion

`ainsert()` accepts text strings, lists, and optional file path metadata for citation:

```python
# Single document
await cg.ainsert("Meeting notes text...")

# Batch
await cg.ainsert([
    "Q3 review transcript...",
    "Policy document text...",
    "Email thread content...",
])

# With file path metadata (used in provenance / citations)
await cg.ainsert(
    ["Call transcript...", "Approval email..."],
    file_paths=["calls/2024-08-14.txt", "emails/approval-123.eml"],
)
```

During ingestion the LLM extracts a 6-field record per relationship, where the 6th field is a compact RelationContext JSON:

```
relation<|#|>Sarah Chen<|#|>MegaCorp<|#|>discount approval<|#|>
Sarah Chen approved a 20% discount for MegaCorp.<|#|>
{"supporting_sentences":["VP of Sales approved 20% discount"],"temporal_info":
"Valid until December 31, 2024","quantitative_data":"20% discount","decision_trace":
"Approved citing five-year relationship and Salesforce competition","approved_by":
"Sarah Chen","approved_via":"in_person","valid_from":null,"valid_until":"2024-12-31",
"policy_ref":null,"provenance":"Slack #deals-review Aug 14 2024","confidence_score":0.97}
```

The LLM fills `approved_by` / `approved_via` when an approver and channel are named, `valid_from` / `valid_until` when explicit dates appear, and `policy_ref` when a policy name is referenced. Fields not evidenced in the text are set to `null`.

---

## Web Ingestion

Point Context Graph at a public website and it crawls the content straight into the graph. `POST /scrape` starts an async job that walks the same domain breadth-first — respecting `robots.txt`, rate-limited, bounded by depth and page count — strips each page to its readable main text, and inserts it **with the page URL as the file path**, so that URL becomes the `provenance` on every quadruple extracted from it. Binary/structured files (PDFs, Office docs, JSON) found while crawling are downloaded and picked up by the standard document scan.

<p align="center">
  <img src="docs/assets/fig6-web-ingestion-pipeline.svg" alt="Web ingestion pipeline — crawl, read, ingest" width="860">
</p>
<p align="center"><em><b>Figure 5.</b> Point it at a URL; it crawls politely, reads any format, and hands the content to Context Graph's ingestion with the URL preserved as provenance.</em></p>

```bash
# Start a JS-rendered, LLM-filtered crawl (workspace-scoped)
curl -X POST http://localhost:9621/scrape \
  -H "LIGHTRAG-WORKSPACE: district_acme" -H "Content-Type: application/json" \
  -d '{"url":"https://acme.k12.org","render_js":true,"analyze":true,"max_depth":3}'
# -> {"job_id":"a1b2c3d4e5f6","state":"running"}

curl http://localhost:9621/scrape/a1b2c3d4e5f6 -H "LIGHTRAG-WORKSPACE: district_acme"
```

- **Polite crawler** — same-domain BFS, `robots.txt`, per-host rate limit, sitemap seeding, main-text extraction.
- **Pluggable connectors** — platform plugins that crack sites hiding files behind a JS widget or private API. WordPress, Finalsite/Blackboard, and BoardDocs ship enabled; adding one is copy-a-template.
- **LLM-guided acquisition** — with `analyze=true`, an LLM selects the right connector and filters which resources are worth ingesting. Fails open to deterministic behavior if no LLM is configured.

<p align="center">
  <img src="docs/assets/fig7-llm-connector-selection.svg" alt="LLM connector selection from a site's own signals" width="860">
</p>
<p align="center"><em><b>Figure 6.</b> The LLM chooses the connector from the site's own signals — so a new platform is a new plugin with a one-line hint, not a rewrite.</em></p>

> 📖 Full guide: [`docs/SCRAPER.html`](https://dsivov.github.io/Context_Graph/SCRAPER.html)

---

## Real-Time Decision Capture

`emit_decision_trace()` writes a decision into the graph **at the moment it is made**, from agent code or a webhook handler — no document ingestion round-trip required.

```python
rc = RelationContext(
    decision_trace="Waived 30-day payment terms; client committed to 3-year renewal",
    approved_by="Alice",
    approved_via="zoom",
    valid_from="2024-11-01",
    valid_until="2025-10-31",
    policy_ref="PaymentPolicy_Standard",
    quantitative_data="Net-30 waived",
    provenance="Zoom call recording 2024-11-01",
    confidence_score=1.0,
)

await cg.emit_decision_trace(
    src="Alice",
    tgt="AcmeCorp",
    relation_type="WAIVES_PAYMENT_TERMS",
    rc=rc,
    upsert=True,   # merge with existing RC if edge already exists (default)
)
```

**Merge semantics when `upsert=True`:**
- `supporting_sentences`: union (deduplicated, order preserved)
- All scalar string fields (`decision_trace`, `approved_by`, etc.): first-non-None wins
- `confidence_score`: maximum across all versions

The `decision_trace` text is immediately indexed in the `decisions` vector store, making this decision discoverable via `find_precedents()` without any delay.

---

## Governance & Actions

Workspace-scoped layers turn a graph that *remembers* decisions into one that *governs* them — and, with the action layer, one you *operate from*. Rules and Ontology are managed from the WebUI (the **Rules** and **Ontology** tabs) or over REST; actions are managed and invoked over REST.

### Business Rules Engine

A **pre-emit governance gate**: before any decision is written, the workspace's rules evaluate it and return one of three outcomes — `PASS` (persist), `FLAG` (persist, mark `needs_review`), or `REJECT` (abort, nothing persisted). Rules are authored in a small DSL that matches structured fields with exact operators and free-text fields *by meaning* via a semantic `sim()` predicate (local `minishlab/potion-retrieval-32M` embeddings — no API cost). Evaluation is **fail-open**: a misconfigured rule is skipped with a warning, never crashing ingestion.

```text
rule "large discount needs finance review"  priority 10
when
    sim(relation_type, "APPROVAL") > 0.4
    and percent > 0.15
    and approved_via == "slack"
then
    flag("Discount >15% approved over Slack — route to Finance")
end
```

Describe a policy in plain English and `POST /rules/generate` writes the DSL, defines the concepts, and **proves it** with a dry-run self-test before you save. See [`docs/RULES_ENGINE.html`](https://dsivov.github.io/Context_Graph/RULES_ENGINE.html).

### Ontology

A **typed schema** for the workspace — object types and directed link types, each with typed properties — that every extraction is validated against. Validation is *coercing*: the same pass that checks a record normalizes it (`"$25,000"` → `25000.0`, `"20%"` → `0.2`), which is what gives the rules engine real numbers to reason over instead of free text. Open-world by default (unknown types warn); closed-world rejects them. `POST /ontology/generate` drafts a schema from a domain description. See [`docs/ONTOLOGY.html`](https://dsivov.github.io/Context_Graph/ONTOLOGY.html).

### Action Layer

**Executable operations bound to object types** — `ApproveOrder`, `CancelShipment`. Invoking one turns a graph you reason *from* into one you operate *from*: it validates the typed arguments (reusing the ontology's coercing kinds), **authorizes and records the execution through the rules gate** (`emit_decision_trace`), and — only on `PASS`/`FLAG` — runs an optional side effect. The audit edge written to the graph *is* the record of the executed action, so actions are governed exactly like any other decision.

```bash
# Register a catalog, then invoke — the rules gate authorizes before the side effect
curl -X POST http://localhost:9621/actions -H "LIGHTRAG-WORKSPACE: sales" \
  -H "Content-Type: application/json" -d '{"catalog":{"name":"sales","actions":[
    {"name":"ApproveOrder","object_type":"Order","relation_type":"APPROVED",
     "params":[{"name":"discount","kind":"percent","required":true}]}]}}'

curl -X POST http://localhost:9621/actions/invoke -H "LIGHTRAG-WORKSPACE: sales" \
  -H "Content-Type: application/json" \
  -d '{"action":"ApproveOrder","actor":"Sarah","object_ref":"Order#123","args":{"discount":"20%"}}'
# -> outcome FLAG · audit rule "large discount review" · edge Sarah -APPROVED-> Order#123
```

- **Governed by construction** — the gate runs *first*, so a `REJECT` (mapped to HTTP 422) never fires the side effect.
- **Typed arguments** — money/percent args are coerced and rendered into `quantitative_data`, so rules reason over them.
- **SSRF-guarded handlers** — `none` (record-only) or `webhook`; webhooks refuse loopback/private hosts unless `allow_internal`.

> 📖 Full guide: [`docs/ACTIONS.html`](https://dsivov.github.io/Context_Graph/ACTIONS.html)

> These features require `USE_CONTEXT_GRAPH=true` and return **HTTP 503** otherwise.

---

## AI Agent Development

Point a coding agent — or a whole dev team of agents — at Context Graph and it stops rebuilding what already exists, stops losing the *why* behind past choices, and follows a methodology the graph **enforces** (rules, RBAC, lifecycle). The methodology itself is **data** — an `agentic-dev` preset of ontology/rules/actions/RBAC/lifecycle — so the core stays generic. This is a management system on a decision-aware platform, not a code index bolted onto the IDE.

**Greenfield onboarding is one URL.** The server serves the agent's playbook and bootstrap bundle, generated *live* from the workspace's installed config (real object types, actions, guardrails) — no guide files to hand-copy into the repo:

```bash
# 1) Everything the agent needs to init itself — mcp config, backfill cmd, playbook/manifest links
curl -s http://localhost:9621/workspace/bootstrap -H "LIGHTRAG-WORKSPACE: myproj"

# 2) Backfill an existing repo/fork — the server serves the script; the agent runs it locally
#    (imports modules, author, commits, docs, and with --code the source itself)
curl -s http://localhost:9621/workspace/backfill-script | python - --repo . --workspace myproj --code

# 3) The live operating guide (Markdown) — the three habits, your actions, the signals
curl -s http://localhost:9621/workspace/playbook -H "LIGHTRAG-WORKSPACE: myproj"
```

Onboarding a *brand-new* workspace? `POST /onboard` with a plain-English description tailors the ontology + rules and returns the same bootstrap bundle, handing the agent its entry points in one call.

> 📖 Full use case: [`docs/AGENTIC_DEVELOPMENT.html`](https://dsivov.github.io/Context_Graph/AGENTIC_DEVELOPMENT.html) · design discussion: [`docs/AGENTIC_PROJECT_GRAPH.html`](https://dsivov.github.io/Context_Graph/AGENTIC_PROJECT_GRAPH.html)

> Requires `USE_CONTEXT_GRAPH=true`.

---

## Querying

Context Graph keeps every standard retrieval mode and adds an `auto` router that picks the best one for you — classify once, cache the verdict, and answer with the reasoning attached.

<p align="center">
  <img src="docs/assets/fig5-auto-query-router.svg" alt="Auto query router — classify once, route to the best mode" width="860">
</p>
<p align="center"><em><b>Figure 7.</b> The <code>auto</code> router classifies the question once (and caches it), routes to the best mode, and returns the answer with its reasoning.</em></p>

### Standard Query

All standard LightRAG query modes work unchanged. Retrieved edges include their RelationContext in the LLM's context window, grounding answers in actual decision records.

```python
from lightrag import QueryParam

result = await cg.aquery(
    "What pricing exceptions were approved in Q1 2025?",
    param=QueryParam(
        mode="hybrid",       # local | global | hybrid | naive | mix
        top_k=60,
        enable_rerank=True,
    ),
)
```

### CGR3 Iterative Reasoning

```python
answer = await cg.cgr3_query(
    query="Why was the GlobalTech exception approved, who authorised it, "
          "and is it still in force?",
    mode="hybrid",
    max_iterations=3,   # stops early if sufficient context found
    top_k=60,
)
```

Each iteration:
1. Retrieves candidate entities/edges with RelationContext
2. Ranks by LLM-assessed relevance to the query
3. Reasons whether context is sufficient — returns answer if yes, seeds next iteration if no

### Precedent Search

Semantic vector similarity over indexed `decision_trace` text:

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
    print(f"  Approved by {rc.approved_by} via {rc.approved_via}")
    print(f"  Valid: {rc.valid_from} → {rc.valid_until}")
    print(f"  Policy: {rc.policy_ref}")
    print(f"  Active today: {rc.is_active()}")
```

### Decision Filtering

Enumerate all decision-bearing edges with structured filters (all parameters are ANDed):

```python
# All decisions by a specific approver that are currently active
decisions = await cg.get_all_decisions(
    approved_by="CFO_Johnson",
    active_as_of="2025-04-01",
    min_confidence=0.8,
)

# All Slack-approved exceptions referencing a specific policy
decisions = await cg.get_all_decisions(
    approved_via="slack",
    policy_ref="DiscountPolicy_v2",
)

# Full audit log — no filters
all_decisions = await cg.get_all_decisions()
```

### Temporal Validity

`RelationContext.is_active()` performs a date-range check:

```python
rc = RelationContext(valid_from="2024-01-01", valid_until="2024-12-31")

rc.is_active()                      # True/False based on today
rc.is_active(as_of="2024-06-15")    # True
rc.is_active(as_of="2025-01-01")    # False — expired
rc.is_active(as_of="2023-12-31")    # False — not yet effective
```

Returns `True` when neither date is set (validity unknown).

---

## REST API

Start the server with `USE_CONTEXT_GRAPH=true`. All endpoints below return **HTTP 503** when the server is running without Context Graph mode, so clients can detect capability at runtime.

### Endpoint Overview

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/query` | Standard RAG query — edges enriched by RelationContext |
| `POST` | `/documents/text` · `/documents/texts` · `/documents/upload` | Ingest content and extract RelationContext |
| `POST` | `/cgr3/query` | CGR3 iterative Retrieve→Rank→Reason |
| `GET`  | `/graph/edge/context` | RelationContext for a specific edge |
| `GET`  | `/graph/entity/edges-with-context` | All context-enriched edges for an entity |
| `POST` | `/graph/decision/emit` | Write a decision directly into the graph |
| `GET`  | `/graph/decisions/search` | Semantic precedent search |
| `GET`  | `/graph/decisions` | List/filter all decision-bearing edges |
| `POST` | `/scrape` | Crawl a website into the graph (async job) — see [Web Ingestion](#web-ingestion) |
| `GET`  | `/scrape/{job_id}` · `/scrape` | Poll / list web-ingest jobs |
| `GET`/`POST`/`DELETE` | `/rules` | Get, save, or delete the workspace rules policy |
| `POST` | `/rules/evaluate` · `/rules/toggle` · `/rules/generate` | Dry-run, enable/disable, or author rules from plain English |
| `GET`/`POST`/`DELETE` | `/ontology` | Get, save, or delete the workspace ontology |
| `POST` | `/ontology/generate` · `/ontology/validate` | Author a schema from a description; validate extractions |
| `GET`/`POST`/`DELETE` | `/actions` | Get, save, or delete the workspace action catalog |
| `GET`/`POST` | `/actions/{name}` · `/actions/invoke` | Fetch one action; invoke an action (validate → gate → side effect) |

---

### `POST /cgr3/query`

Iterative multi-hop reasoning.

```http
POST /cgr3/query
Content-Type: application/json

{
  "query": "Why was the MegaCorp deal discounted and who approved it?",
  "mode": "hybrid",
  "max_iterations": 3,
  "top_k": 60,
  "include_references": true
}
```

```json
{
  "response": "The MegaCorp deal received a 20% discount approved by Sarah Chen (VP of Sales) on August 14, 2024. The rationale was MegaCorp's five-year relationship and a competing offer from Salesforce. The discount was valid until December 31, 2024.",
  "references": null
}
```

**Request fields:**

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `query` | string | required | Natural language question (min 3 chars) |
| `mode` | string | `"hybrid"` | Retrieval mode: `local` `global` `hybrid` `naive` `mix` |
| `max_iterations` | int | `3` | CGR3 iterations (1–10) |
| `top_k` | int | server default | Entities/relations per iteration |
| `include_references` | bool | `true` | Include source references in response |

---

### `POST /graph/decision/emit`

Write a decision directly into the graph at runtime.

```http
POST /graph/decision/emit
Content-Type: application/json

{
  "src": "Regional_VP",
  "tgt": "StrategicAccount_XYZ",
  "relation_type": "APPROVES_EXCEPTION",
  "relation_context": {
    "decision_trace": "Approved 30% discount — 5-year renewal at risk",
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

```json
{
  "status": "ok",
  "edge": "Regional_VP -> StrategicAccount_XYZ"
}
```

If the edge already exists the RelationContexts are merged. The `decision_trace` is immediately indexed in the decisions vector store.

---

### `GET /graph/edge/context`

Retrieve the RelationContext stored on a specific graph edge.

```http
GET /graph/edge/context?src=Sarah+Chen&tgt=MegaCorp
```

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

All context-enriched edges connected to an entity.

```http
GET /graph/entity/edges-with-context?entity_name=Sarah+Chen
```

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

Semantic vector search over indexed decision traces.

```http
GET /graph/decisions/search?q=discount+approved+strategic+renewal&top_k=5&min_confidence=0.7
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `q` | string | required | Natural language query |
| `top_k` | int | `10` | Max results (1–100) |
| `min_confidence` | float | `0.0` | Confidence threshold (0.0–1.0) |

```json
{
  "query": "discount approved strategic renewal",
  "total_count": 2,
  "results": [
    {
      "src_id": "Regional_VP",
      "tgt_id": "StrategicAccount_XYZ",
      "relation_context": {
        "decision_trace": "Approved 30% discount — 5-year renewal at risk",
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

```http
GET /graph/decisions?approved_by=CFO_Johnson&active_as_of=2025-04-01&min_confidence=0.8
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `approved_by` | string | — | Filter by approver entity name |
| `approved_via` | string | — | Filter by channel: `slack` `zoom` `email` `in_person` `jira` `system` |
| `policy_ref` | string | — | Filter by policy name/ID |
| `min_confidence` | float | `0.0` | Confidence threshold |
| `active_as_of` | string | — | ISO-8601 date — only valid decisions on this date |

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

## Integration Patterns

### 1. Salesforce / HubSpot Approval Webhook

Trigger `emit_decision_trace` when a deal approval is logged in your CRM, keeping the graph current without batch re-ingestion.

```python
async def on_opportunity_approval(event: dict):
    """Called by Salesforce webhook on discount approval."""
    await cg.emit_decision_trace(
        src=event["approver_name"],
        tgt=event["account_name"],
        relation_type="APPROVES_DISCOUNT",
        rc=RelationContext(
            decision_trace=event["approval_comment"],
            approved_by=event["approver_name"],
            approved_via="system",
            valid_from=event["effective_date"],
            valid_until=event["expiry_date"],
            policy_ref=event.get("policy_applied"),
            quantitative_data=f"{event['discount_pct']}% discount",
            provenance=f"Salesforce Opportunity {event['opportunity_id']}",
            confidence_score=1.0,
        ),
    )
```

### 2. Slack Approval Bot

Capture approvals made in Slack channels in real time via the Events API.

```python
async def on_slack_approval(message: dict):
    """/approve slash command handler in #deals-review."""
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

### 3. Nightly Document Sync

Batch-ingest call transcripts, meeting notes, and email threads on a schedule. The LLM extracts RelationContext automatically.

```python
async def nightly_ingest(docs_dir: str):
    from pathlib import Path
    texts, paths = [], []
    for p in Path(docs_dir).glob("*.txt"):
        texts.append(p.read_text())
        paths.append(str(p))
    await cg.ainsert(texts, file_paths=paths)
```

### 4. AI Sales Rep Assistant

An assistant that respects current policy and cites actual precedents before recommending a discount.

```python
import datetime

async def advise_on_discount(account: str, requested_pct: float) -> str:
    # Find similar past decisions
    precedents = await cg.find_precedents(
        f"{requested_pct}% discount approved for enterprise account",
        top_k=5, min_confidence=0.7,
    )

    # Check existing active approvals for this account
    active = await cg.get_all_decisions(
        active_as_of=datetime.date.today().isoformat()
    )
    account_active = [d for d in active if d["tgt_id"] == account]

    # Build context string
    context_lines = [
        f"- {p['relation_context'].decision_trace} "
        f"(approved by {p['relation_context'].approved_by}, "
        f"confidence {p['relation_context'].confidence_score:.0%})"
        for p in precedents
    ]

    return await cg.cgr3_query(
        f"Should we approve {requested_pct}% discount for {account}? "
        f"Existing active approvals: {len(account_active)}. "
        f"Relevant precedents:\n" + "\n".join(context_lines),
    )
```

### 5. Compliance Audit Trail

Generate a complete audit report of all pricing exceptions in a period.

```python
async def pricing_audit_report(as_of: str) -> list[dict]:
    """All decisions active on a given date, referencing pricing policy."""
    decisions = await cg.get_all_decisions(
        policy_ref="DiscountPolicy_v2",
        active_as_of=as_of,
        min_confidence=0.5,
    )
    return [
        {
            "approver":  d["relation_context"].approved_by,
            "account":   d["tgt_id"],
            "channel":   d["relation_context"].approved_via,
            "decision":  d["relation_context"].decision_trace,
            "period":    f"{d['relation_context'].valid_from} → {d['relation_context'].valid_until}",
            "policy":    d["relation_context"].policy_ref,
            "confidence": d["relation_context"].confidence_score,
        }
        for d in decisions
    ]
```

### 6. HTTP API Integration (any language)

```bash
# Emit a decision from a shell script or CI pipeline
curl -s -X POST http://localhost:9621/graph/decision/emit \
  -H "Content-Type: application/json" \
  -d '{
    "src": "CTO",
    "tgt": "VendorABC",
    "relation_type": "APPROVES_CONTRACT",
    "relation_context": {
      "decision_trace": "Approved 3-year vendor contract with exit clause",
      "approved_by": "CTO",
      "approved_via": "email",
      "valid_from": "2025-03-01",
      "valid_until": "2028-02-28",
      "policy_ref": "ProcurementPolicy_v3",
      "confidence_score": 1.0
    }
  }'

# Find precedents
curl -s "http://localhost:9621/graph/decisions/search?q=vendor+contract+approved&top_k=5"

# List all decisions by approver
curl -s "http://localhost:9621/graph/decisions?approved_by=CTO&active_as_of=2025-06-01"
```

---

## Storage Backends

Context Graph supports all LightRAG storage backends. Configure via `.env` or constructor parameters.

### Graph Storage

| Backend | Class | Use Case |
|---------|-------|----------|
| NetworkX (default) | `NetworkXStorage` | Development, single-process |
| Neo4j | `Neo4JStorage` | Production, complex graph queries |
| Memgraph | `MemgraphStorage` | High-throughput graph workloads |

### Vector Storage

| Backend | Class | Use Case |
|---------|-------|----------|
| NanoVectorDB (default) | `NanoVectorDBStorage` | Development, no setup |
| PostgreSQL pgvector | `PGVectorStorage` | Production, unified DB |
| Qdrant | `QdrantStorage` | High-scale vector search |
| Milvus | `MilvusStorage` | Large-scale deployments |
| Faiss | `FaissStorage` | Local, CPU-optimised |

The `decisions` vector namespace (new in Context Graph) uses the same configured vector backend automatically.

### Key-Value Storage

| Backend | Class | Use Case |
|---------|-------|----------|
| JSON files (default) | `JsonKVStorage` | Development |
| PostgreSQL | `PGKVStorage` | Production |
| Redis | `RedisKVStorage` | High-throughput caching |
| MongoDB | `MongoKVStorage` | Document-oriented workloads |

---

## LLM Providers

| Provider | Module |
|----------|--------|
| OpenAI / compatible | `lightrag.llm.openai` |
| Azure OpenAI | `lightrag.llm.azure_openai` |
| Ollama | `lightrag.llm.ollama` |
| Anthropic | `lightrag.llm.anthropic` |
| Google Gemini | `lightrag.llm.gemini` |
| AWS Bedrock | `lightrag.llm.bedrock` |
| Hugging Face | `lightrag.llm.hf` |
| NVIDIA NIM | `lightrag.llm.nvidia_openai` |

**Minimum requirements for good extraction quality:**
- 32B+ parameters
- 32K context window (64K recommended)
- Avoid reasoning/chain-of-thought models during indexing — they produce unreliable structured output

---

## Testing

```bash
# Run the full offline suite (no external services needed) — 711 passing
python -m pytest tests context_graph/tests -q

# Run only the core Context Graph tests
python -m pytest tests/test_context_graph.py tests/test_context_graph_api.py -v --tb=short

# Run integration tests (requires configured external services)
python -m pytest tests --run-integration

# Keep artifacts for debugging
python -m pytest tests --keep-artifacts
```

**Test suite coverage** (711 passing / 35 skipped):

| Area | Where | Coverage |
|------|-------|---------|
| Core CG | `tests/test_context_graph.py` (75) | RelationContext fields, `is_active()`, `merge()`, extraction pipeline, `emit_decision_trace`, `find_precedents`, `get_all_decisions` |
| CG API | `tests/test_context_graph_api.py` (36) | CG endpoints, Pydantic models, 503 on plain LightRAG, config parsing |
| Governance & quality | `context_graph/tests/` | Rules engine/gate/store, ontology, RBAC, lifecycle, actions, dedup, garbage filter, connectivity, communities |

---

## Architecture

### Class Hierarchy

```
LightRAG                        — base RAG engine (ainsert, aquery, storage lifecycle)
  └── ContextGraph              — CG extension
        ├── __post_init__       — creates decisions_vdb on top of standard storages
        ├── initialize_storages — calls super() + decisions_vdb.initialize()
        ├── finalize_storages   — calls super() + decisions_vdb.finalize()
        ├── _process_extract_entities  — overrides to use CG 6-field prompts
        ├── get_edge_context    — fetch RelationContext for a specific edge
        ├── get_edges_with_context     — all context-enriched edges for an entity
        ├── emit_decision_trace — write RC directly at decision time
        ├── find_precedents     — semantic search over decisions_vdb
        ├── get_all_decisions   — structured filter over all decision edges
        └── cgr3_query          — iterative Retrieve→Rank→Reason loop
```

### Storage Namespaces

| Namespace | Type | Contents |
|-----------|------|---------|
| `full_docs` | KV | Raw inserted documents |
| `text_chunks` | KV | Chunked document segments |
| `llm_response_cache` | KV | LLM extraction cache |
| `entities` | Vector | Entity embeddings |
| `relationships` | Vector | Relationship embeddings |
| `chunks` | Vector | Text chunk embeddings |
| `decisions` | Vector | Decision trace embeddings **(CG-only)** |
| `chunk_entity_relation` | Graph | Entity-relation graph with RelationContext on edges |
| `doc_status` | Doc Status | Per-document processing state |

### Extraction Format

The LLM produces 6-field relation records during CG extraction:

```
relation<|#|>source_entity<|#|>target_entity<|#|>keywords<|#|>description<|#|>RC_JSON
```

Where `RC_JSON` is a compact single-line JSON object. Standard LightRAG extraction (5-field format) is also accepted, with `relation_context` defaulting to `null` — ensuring backward compatibility with any existing graph data.

### Backward Compatibility

- All new RelationContext fields default to `None` — existing graph data loads without modification
- Standard 5-field extraction records are parsed identically to before
- All standard LightRAG endpoints (`/query`, `/documents/*`, `/graph/...`) work unchanged
- Context Graph endpoints return HTTP 503 when `USE_CONTEXT_GRAPH=false`, so clients degrade gracefully

### Confidence Score Guidelines

| Score | Meaning |
|-------|---------|
| `1.0` | Always set for `emit_decision_trace()` — structured source, no ambiguity |
| `0.9+` | Explicit, unambiguous statement in source text |
| `0.7–0.9` | Clear but implicit or inferred |
| `< 0.7` | Ambiguous — consider filtering with `min_confidence` |

---

## Privacy by Design: Pattern-Based Enrichment

Context Graph follows a **patterns, not identities** principle for conversation enrichment. When ingesting real customer conversations (sales transcripts, support chats), the system anonymizes all PII before the data enters the knowledge graph.

### Anonymization Pipeline

| Data Type | Treatment | Example |
|-----------|-----------|---------|
| Customer names | Removed entirely | `"Hi Natalie"` → `"Hi customer"` |
| Exact amounts | Bucketed into ranges | `₪4,386.06` → `"3,000-5,000"` |
| Exact dates | Month/year only | `2025-11-14` → `"November 2025"` |
| Phone numbers | Regex scrubbed | `054-123-4567` → `[phone]` |
| Email addresses | Regex scrubbed | `user@example.com` → `[email]` |
| Israeli ID numbers | Regex scrubbed | `123456789` → `[id]` |
| Credit card numbers | Regex scrubbed | `4580-1234-5678-9012` → `[card]` |
| Street addresses | Regex scrubbed | `12 רחוב הרצל` → `[address]` |
| Opportunity IDs | SHA-256 hashed | `abc-123-def` → `anon-7f3a2b1c9e4d` |

### What the Graph Learns

The anonymized conversation documents teach the graph **behavioral patterns**:

- **Cross-sell patterns** — what products customers buy together
- **Objection handling** — what concerns arise and how they're resolved
- **Sales techniques** — what messaging drives conversions
- **Product comparisons** — how products differ in real customer conversations
- **Use-case mappings** — what customers actually need products for

### What the Graph Does NOT Store

- Customer names or identifiers
- Exact transaction amounts or dates
- Phone numbers, emails, physical addresses
- Medical conditions, disabilities, or personal circumstances
- Any data that could identify a specific individual

This approach is inspired by the [Context Graphs](https://foundationcapital.com/ideas/context-graphs-ais-trillion-dollar-opportunity) paradigm: the value is in the **patterns** (what products go together, what objections arise) — not in the **identities** (who bought what).

> This is a recommended **client-side** pattern: scrub and bucket PII before it reaches `ainsert()` / `emit_decision_trace()`, so identifying data never enters the graph in the first place.

---

## Research Foundations

Context Graph implements the **CGR3 (Context Graphs with Retrieve-Rank-Reason)** paradigm from [Liang et al., 2024](https://arxiv.org/abs/2406.11160), built on the [LightRAG](https://github.com/HKUDS/LightRAG) graph-based RAG engine by HKUDS.

### Paper Alignment

| Paper Concept | Our Implementation | Notes |
|---|---|---|
| Factual quadruples `(h,r,t,rc)` | `RelationContext` dataclass (11 fields) | Faithful — extended with decision-specific fields |
| Relation context (temporal, quantitative, provenance, confidence) | `temporal_info`, `quantitative_data`, `provenance`, `confidence_score` | Direct mapping |
| CGR3: Retrieve → Rank → Reason | `cgr3_query()` with iterative loop | Faithful — max 3 iterations, early stopping |
| Entity context from Wikidata | LLM extraction from ingested documents | Adapted for document-first workflows |
| KGE embeddings (ComplEx, RotatE) | Text embeddings (text-embedding-3-large, 3072d) | Adapted — semantic similarity over learned structure |
| Beam search (width M, depth D=3) | Iterative entity seed refinement per CGR3 pass | Adapted |
| +33% Hits@1 from adding context | Hybrid mode 78.1% vs naive 70.4% relevance | Consistent improvement |

### Extensions Beyond the Paper

| Extension | Description |
|-----------|-------------|
| **Multi-tenancy** | WorkspacePool with per-tenant Neo4j label isolation, lazy init |
| **Real-time decision capture** | `emit_decision_trace()` — write decisions without document re-ingestion |
| **Privacy-by-design** | PII scrubbing, amount bucketing, name removal, hashed source IDs |
| **Size-aware routing** | `catalog_bypass` for small catalogs (<100 products), `auto` router |
| **Precedent search** | `find_precedents()` — semantic search over decision traces |
| **Temporal filtering** | `is_active(as_of)` for date-range validity checks |
| **Business Rules Engine** | Pre-emit governance gate — DSL + semantic `sim()`, PASS/FLAG/REJECT, NL policy author |
| **Ontology** | Per-workspace typed schema, coercing extraction validation, NL schema author |
| **Web ingestion** | Polite crawler + pluggable connectors that pull any site into the graph with URL provenance |
| **Action layer** | Executable operations bound to object types; invoke → rules gate → audit edge, with SSRF-guarded handlers |

---

## Project Structure

All Context Graph code lives in the top-level **`context_graph/`** package; **`lightrag/`** is the reused base RAG engine and HTTP server it plugs into.

```
context_graph/                  — All Context Graph code (the fork's own package)
├── core.py                     — ContextGraph class + CG extraction pipeline (main entry point)
├── types.py                    — RelationContext, ContextNode, ContextEdge dataclasses
├── jsonio.py                   — Dependency-free LLM-JSON parser, shared by every CG module
├── api/
│   └── routes.py               — CG API endpoints (CGR3, decisions, dedup/quality/community)
├── rules/                      — Business Rules Engine (DSL, gate, sim(), NL author)
├── ontology/                   — Typed schema, coercing validation, NL author
├── rbac/                       — Object-level role-based access control
├── lifecycle/                  — Declarative object state machines
├── actions/                    — Executable operations bound to object types, governed by the gate
├── webingest/                  — Polite crawler + pluggable site connectors
├── dedup/                      — Entity deduplication (canonicalize, resolve, async sweep)
├── quality/                    — Garbage-node filtering + quarantine
├── connectivity/               — Isolate rescue + low-degree pruning
├── community/                  — Louvain community detection + summaries (global mode)
└── tests/                      — Governance/quality test suites

lightrag/                       — Base LightRAG engine + FastAPI server (reused dependency)
├── lightrag.py                 — Base LightRAG RAG engine (ainsert, aquery, storage lifecycle)
├── operate.py                  — Extraction and merge pipeline (rc-aware)
├── prompt.py                   — CG-tightened extraction prompts (RelationContext schema)
├── base.py · namespace.py      — Abstract storage interfaces, namespace constants
├── api/
│   ├── lightrag_server.py      — FastAPI app; registers CG + governance routers
│   ├── config.py               — Environment config (USE_CONTEXT_GRAPH etc.)
│   ├── workspace_pool.py       — Multi-tenant workspace pool and ASGI middleware
│   └── routers/                — query · document · graph · rules · ontology · rbac ·
│                                 lifecycle · actions · webingest · workspace routers
├── kg/                         — Storage backends (Neo4j, NetworkX, NanoVectorDB,
│                                 JSON KV, PostgreSQL, Redis, MongoDB, Qdrant, Milvus, Faiss)
└── llm/                        — LLM provider bindings (OpenAI, Ollama, Anthropic,
                                  Azure, Gemini, Bedrock, HF, NVIDIA)

tests/                          — Core CG + API test suites (run with pytest)
docs/                           — Illustrated HTML field guides + Markdown references
lightrag_webui/                 — Context Graph branded WebUI (React/TS; built into lightrag/api/webui/)
```

---

## What Context Graph is NOT

To set expectations honestly:

- **Not a standalone graph database.** It layers decision context, governance, and CGR3 retrieval on top of LightRAG's pluggable storage (NetworkX / Neo4j / PostgreSQL). Bring your own backend.
- **Not a drop-in LightRAG replacement.** It's a fork that adds a decision/governance layer. It stays API-compatible with LightRAG's endpoints, but the value is the additions.
- **Not an authenticated multi-tenant SaaS out of the box.** Workspaces isolate data per tenant, but production authentication and object-level access enforcement are opt-in and still hardening — do not expose it to untrusted users without adding auth.
- **Not hardened for untrusted input yet.** Some security items (SSRF edge cases in web ingest, verbose error responses) are known and tracked; review before public exposure.
- **Not an IDE plugin or code index.** The agentic-dev preset governs how coding agents work, but Context Graph is a knowledge/decision platform, not an editor extension.

---

## Based On

Context Graph implements the [CGR3 paradigm](https://arxiv.org/abs/2406.11160) (Liang et al., 2024) — extending knowledge graph triples into contextual quadruples `(h, r, t, rc)` with Retrieve→Rank→Reason iterative query processing. Built on [LightRAG](https://github.com/HKUDS/LightRAG) by HKUDS, with production extensions for multi-tenancy, real-time decision capture, a Business Rules Engine, a typed ontology layer, web ingestion, and a governed action layer.
