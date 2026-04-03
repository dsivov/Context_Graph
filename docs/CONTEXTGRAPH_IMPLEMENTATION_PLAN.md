
Slide 1: Title & Vision
Project: Next-Gen CRM Context Graph (CG)
• Objective: Transform unstructured sales conversations and CRM data into a "System of Decision".
• Goal: Capture the "Decision Traces" (the why) that traditional Systems of Record (Salesforce, etc.) fail to store.
• Foundation: Built on LightRAG for fast retrieval and CGR3 for contextual reasoning.

--------------------------------------------------------------------------------
Slide 2: The Critical Gap in CRM RAG
Problem: Traditional RAG is "Context-Blind"
• Missing Decision Traces: CRM records show the result (e.g., "20% discount") but lose the rationale (VP approval based on past service impact).
• Tribal Knowledge: Logic like "extra 10% for healthcare companies" lives in Slack threads or heads, not the database.
• Flat Representations: Traditional RAG retrieves fragmented chunks, failing to synthesize multi-hop relationships between leads and past precedents.

--------------------------------------------------------------------------------
Slide 3: The Context Graph Architecture
Moving from Triples to Contextual Quadruples
• Standard KG: (h,r,t) — (Steve Jobs, Chairman of, Apple).
• Context Graph (CG): (h,r,t,rc) — Includes Relation Context (rc) like temporal dynamics, geographic location, and provenance.
• LightRAG Integration: Uses Dual-Level Retrieval (Low-level for specific lead details; High-level for abstract sales themes).

--------------------------------------------------------------------------------
Slide 4: Technical Data Model (Implementation)
Defining the Core Python Classes To replace default LightRAG structures, we implement the following [Source: Conversation History]:
@dataclass
class RelationContext:
    """The 'rc' component capturing decision traces from transcripts."""
    supporting_sentences: List[str] # Direct quotes from Slack/Calls
    temporal_info: Optional[str]    # e.g., 'Valid until Q4 2026'
    decision_trace: Optional[str]   # The 'why' behind an exception
    provenance: str                 # Link to specific CRM transcript

@dataclass
class Node:
    """Represents CRM entities: Lead, Opportunity, or Stakeholder."""
    entity_name: str                # 'Opportunity_X'
    entity_type: str                # 'OPPORTUNITY'
    description: str                # Profiling via LightRAG P(·)

@dataclass
class Edge:
    """Links entities with the full Context Graph quadruple."""
    source_id: str; target_id: str; relation_type: str
    context: RelationContext        # Embedded contextual metadata

--------------------------------------------------------------------------------
Slide 5: Historical Conversation Ingestion
Turning "Tribal Knowledge" into Searchable Precedent
• Sources: Ingest Slack DMs, Zoom transcripts, and email chains using textract or RAG-Anything.
• Decision Extraction: Identify exceptions, overrides, and cross-system synthesis (e.g., connecting a Jira ticket to a sales objection).
• Execution Path: The agent orchestration layer captures context at the moment of decision, not after the fact via ETL.


Technical Plan: CRM Context Graph (CG) Implementation via LightRAG
1. Project Overview
This project transforms a standard CRM into a System of Decision by implementing a Context Graph (CG) using the LightRAG framework and the CGR3 reasoning paradigm. By capturing "Decision Traces"—the exceptions, overrides, and precedents currently buried in historical conversations (Slack, Zoom, transcripts)—the system will provide nuanced opportunity handling that traditional systems of record miss.
2. Core Architecture & Data Models
We will replace LightRAG's default triple structure (h,r,t) with factual quadruples (h,r,t,rc) to include Relation Context (rc).
2.1. Python Class Definitions
These structures will be implemented to support enriched entity representation (e,ec) and contextual relationships.
from dataclasses import dataclass, field
from typing import List, Optional, Dict

@dataclass
class RelationContext:
    """The 'rc' component: Captures operational reality and decision lineage."""
    supporting_sentences: List[str] = field(default_factory=list) # Extracted via Sentence-BERT
    temporal_info: Optional[str] = None      # Validity periods (e.g., 'Valid for Q4') [8]
    quantitative_data: Optional[str] = None   # Budget figures, discount percentages [9]
    decision_trace: Optional[str] = None     # The 'why': exceptions or VP overrides [3]
    provenance: Optional[str] = None          # Link to source Slack thread or call transcript [10]
    confidence_score: float = 1.0             # Extraction reliability signal [8]

@dataclass
class Node:
    """Represents CRM entities with enriched Entity Context (ec)."""
    entity_name: str                         # Unique ID (e.g., 'Lead_Alpha')
    entity_type: str                         # [LEAD, OPPORTUNITY, STAKEHOLDER, COMPETITOR] [11]
    attributes: Dict[str, str] = field(default_factory=dict) # Current state (e.g., Lead Score)
    description: str = ""                    # Multi-source summary via LLM Profiling P(·) [12]
    reference_links: List[str] = field(default_factory=list) # Links to LinkedIn/Wikidata [13]

@dataclass
class Edge:
    """Links CRM entities via contextual relationships."""
    source_id: str                           # Head entity (h)
    target_id: str                           # Tail entity (t)
    relation_type: str                       # [QUALIFIES, OBJECTS_TO, APPROVES]
    weight: float = 1.0                      # Relationship strength/frequency [11]
    context: RelationContext = field(default_factory=RelationContext)
3. Knowledge Extraction & Indexing Pipeline
3.1. Data Ingestion
• Historical Sources: Export Slack threads, Zoom transcripts (VTT/JSON), and email chains.
• Multimodal Handling: Use RAG-Anything to parse PDFs, images of whiteboards, and Office documents directly into the pipeline.
• CRM Sync: Pull current opportunity states from Salesforce/Dynamics to establish the "baseline" graph nodes.
3.2. Modified Graph Construct Prompt
The extraction prompt R(⋅) must be updated to capture quadruples.
• Goal: "Identify all entities and contextual quadruples (h,r,t,rc) that capture the decision lineage, including approvals and temporal validity" [Source: Conversation History].
• JSON Context Output: For every relation, the LLM must generate a JSON object for RelationContext containing:
    ◦ rationale: Why this link exists (e.g., "Discount granted due to competitor pressure").
    ◦ verbatim_quote: The specific supporting sentence from the transcript.
    ◦ temporal: Deadlines or timestamps.
3.3. Semantic Indexing
• LLM Profiling P(⋅): Use the profiling function to generate summaries for each node. The "Value" in the KV-store will concatenate the entity description with its associated decision traces.
• Sentence-BERT Integration: Use Sentence-BERT to identify the top−γ supporting sentences from conversations that best reflect the relationship semantics.
4. Dual-Level Retrieval & CGR3 Reasoning
4.1. Retrieval Mechanism
The system will employ LightRAG’s Dual-Level Retrieval:
• Low-Level (Specific): "What was the specific objection from Lead X in last week's call?" Retrieves one-hop neighbors and direct verbatim_quote contexts.
• High-Level (Abstract): "What are the common reasons we lose opportunities in the healthcare sector?" Aggregates global themes across the graph.
4.2. The CGR3 Iterative Loop
We will implement the Retrieve-Rank-Reason paradigm:
1. Retrieve: Gather candidate opportunities and their RelationContext.
2. Rank: Use a fine-tuned LLM (e.g., Llama-3-8B via LoRA) to re-order candidates based on contextual relevance to the query.
3. Reason (Sufficiency Check): Prompt the LLM: "Is the historical context sufficient to qualify this lead?".
4. Iteration: If information is insufficient, use top candidates as new topic entities for multi-hop exploration to find related stakeholder conversations.
5. Implementation Roadmap (Phased)
Phase
Focus
Duration
Key Actions
Phase 1
Modern Metadata Foundation
2-4 Weeks
Deploy LightRAG Core; connect Salesforce/Slack APIs; establish asset inventory.
Phase 2
Contextual Lineage Capture
2-3 Months
Implement quadruple extraction prompt; integrate Sentence-BERT for quote mapping.
Phase 3
Semantic & Policy Integration
4-6 Months
Map governance policies as nodes; automate exception routing based on "why" links.
Phase 4
AI Activation & CGR3
6+ Months
Enable iterative reasoning loop; deploy context-aware lead scoring agents.
6. Technical Stack & Deployment
• Graph Storage: Neo4j (Recommended for production performance over PostgreSQL/AGE).
• Vector Database: Milvus or NanoVectorDB for managing conversational embeddings.
• LLM Requirements: Minimum 32B parameters (e.g., Qwen-32B or Llama-3) for extraction; GPT-4o or equivalent for final answer generation.
• Observability: Integrate Langfuse to trace LLM decision chains and monitor token costs.
• Package Management: Use uv for fast dependency resolution.
7. Critical Implementation Notes
• Initialization: Always call await rag.initialize_storages() after instantiation to avoid AttributeError.
• Incremental Updates: Leverage the incremental update algorithm to integrate new Slack messages/calls without rebuilding the entire graph index.
• Context Density: Pay attention to "constraint density regions"—areas of the graph with high concentrations of policy nodes (e.g., legal/compliance)—which require extra reasoning steps.

--------------------------------------------------------------------------------
--------------------------------------------------------------------------------
PHASE 5 — GAP CLOSURE: FROM PASSIVE EXTRACTION TO SYSTEM OF DECISION
--------------------------------------------------------------------------------
--------------------------------------------------------------------------------

What follows documents nine implementation gaps identified by comparing the
current codebase against the "AI's Trillion-Dollar Opportunity — Context Graphs"
article.  The article's core claim: the structural advantage of a context graph
comes from being IN the execution path at decision time, not from ETL after the
fact.  Our implementation today only captures decision traces passively (from
ingested documents).  The gaps below close that structural deficit.

Gaps are ordered by leverage: fix the top three and the system crosses the
threshold from "enriched RAG" to "system of decision."

================================================================================
GAP 1 (CRITICAL): emit_decision_trace() — Real-Time Agent Capture
================================================================================

WHY IT MATTERS
--------------
Every piece of context we currently extract comes from AFTER the decision was
made (Slack exports, call transcripts ingested as documents).  The article's
structural advantage is being in the execution path: "at decision time, not
after the fact via ETL."

A single API call from an agent at the moment it acts — "I approved this
discount for this reason" — creates a first-class decision record that can
never be lost to ETL latency or transcript gaps.

IMPLEMENTATION
--------------

1. Add `emit_decision_trace()` to ContextGraph:

   File: lightrag/context_graph.py

   async def emit_decision_trace(
       self,
       src: str,
       tgt: str,
       relation_type: str,
       rc: RelationContext,
       *,
       upsert: bool = True,
   ) -> None:
       """Record a decision trace directly into the graph at execution time.

       Unlike ainsert() which extracts context from prose, this method writes
       a structured RelationContext directly to an edge — suitable for
       calling from agent orchestration code at the moment of decision.

       Args:
           src:           Head entity (e.g., "VP_Smith", "DiscountPolicy_Standard").
           tgt:           Tail entity (e.g., "MegaCorp_Renewal_Q4").
           relation_type: Relationship keyword (e.g., "APPROVED_EXCEPTION").
           rc:            RelationContext with decision_trace, approved_by,
                          temporal_info, provenance, etc.
           upsert:        If True, merge with any existing rc on the edge.
       """
       # Ensure both entity nodes exist
       await self.chunk_entity_relation_graph.upsert_node(src, {"entity_type": "ENTITY"})
       await self.chunk_entity_relation_graph.upsert_node(tgt, {"entity_type": "ENTITY"})

       existing = None
       if upsert and await self.chunk_entity_relation_graph.has_edge(src, tgt):
           existing = await self.chunk_entity_relation_graph.get_edge(src, tgt)

       if upsert and existing and existing.get("relation_context"):
           existing_rc = RelationContext.from_json(existing["relation_context"])
           rc = RelationContext.merge([existing_rc, rc])

       edge_data = dict(
           keywords=relation_type,
           description=rc.decision_trace or relation_type,
           weight=rc.confidence_score,
           source_id="emit_decision_trace",
           file_path=rc.provenance or "agent_runtime",
           timestamp=int(time.time()),
           relation_context=rc.to_json(),
       )
       await self.chunk_entity_relation_graph.upsert_edge(src, tgt, edge_data=edge_data)

2. Expose as API endpoint:

   File: lightrag/api/routers/context_graph_routes.py

   POST /graph/decision/emit

   Request body:
   {
     "src": "VP_Smith",
     "tgt": "MegaCorp_Renewal_Q4",
     "relation_type": "APPROVED_EXCEPTION",
     "relation_context": {
       "decision_trace": "20% discount approved citing 3 SEV-1 incidents and 5-year relationship",
       "approved_by": "VP_Smith",
       "approved_via": "slack",
       "temporal_info": "Valid until 2025-12-31",
       "quantitative_data": "20% discount",
       "provenance": "slack://C01234#2024-08-14T14:22",
       "confidence_score": 1.0
     }
   }

   Response: { "status": "ok", "edge": "VP_Smith -> MegaCorp_Renewal_Q4" }

   This is the "being in the execution path" primitive.  Any agent framework
   (LangGraph, AutoGen, custom) can call this after an approval, override, or
   exception is granted.

3. Tests to add: tests/test_context_graph.py
   - test_emit_decision_trace_creates_edge()
   - test_emit_decision_trace_merges_with_existing_rc()
   - test_emit_decision_trace_api_endpoint_200()


================================================================================
GAP 2 (CRITICAL): Precedent Search — find_similar_decisions()
================================================================================

WHY IT MATTERS
--------------
"We structured a similar deal for Company X last quarter — we should be
consistent."  No system currently links those two deals or records why the
structure was chosen.  Without a precedent-search primitive, decision traces
accumulate but remain unsearchable for similarity.

IMPLEMENTATION
--------------

1. Vector-index decision_trace text at write time:

   File: lightrag/context_graph.py

   In both _collect_relation_context (operate.py) and emit_decision_trace,
   after writing the edge, embed the decision_trace string using
   self.entities_vdb (or a new dedicated "decisions_vdb") and upsert the
   vector keyed by (src, tgt).

   This reuses LightRAG's existing vector storage infrastructure.

2. Add find_precedents() to ContextGraph:

   async def find_precedents(
       self,
       query_text: str,
       top_k: int = 10,
       min_confidence: float = 0.0,
       entity_type_filter: str | None = None,
   ) -> list[dict]:
       """Find past decisions semantically similar to query_text.

       Returns a ranked list of edges whose decision_trace is most similar,
       enabling "find me precedents for this type of exception."

       Each result dict contains:
           src_id, tgt_id, relation_type, similarity_score,
           relation_context (RelationContext object)
       """
       # 1. Embed query_text
       query_vec = await self.embedding_func([query_text])
       # 2. Search decisions_vdb
       hits = await self.decisions_vdb.query(query_vec[0], top_k=top_k * 2)
       # 3. Fetch full edge data, filter by confidence and entity_type
       results = []
       for hit in hits:
           edge = await self.chunk_entity_relation_graph.get_edge(
               hit["src_id"], hit["tgt_id"]
           )
           if edge is None:
               continue
           rc = RelationContext.from_json(edge.get("relation_context", "{}"))
           if rc.confidence_score < min_confidence:
               continue
           results.append({
               "src_id": hit["src_id"],
               "tgt_id": hit["tgt_id"],
               "similarity_score": hit["distance"],
               "relation_context": rc,
           })
       return results[:top_k]

3. New vector storage instance:

   decisions_vdb needs its own namespace so it does not pollute the entity/
   relation/chunk vector stores.  Add to ContextGraph.__init__ (or
   initialize_storages):

       self.decisions_vdb = self._create_storage(
           self.vector_storage,
           namespace="decision_traces",
           embedding_func=self.embedding_func,
       )

4. Expose as API endpoint:

   GET /graph/decisions/search?q=20%25+discount+VP+exception&top_k=5&min_confidence=0.7

   Response:
   {
     "query": "20% discount VP exception",
     "results": [
       {
         "src_id": "VP_Smith", "tgt_id": "MegaCorp_Renewal_Q3",
         "similarity_score": 0.94,
         "relation_context": { "decision_trace": "...", "temporal_info": "..." }
       }
     ]
   }

5. Tests to add: tests/test_context_graph.py
   - test_find_precedents_returns_ranked_results()
   - test_find_precedents_respects_min_confidence()
   - test_find_precedents_api_endpoint_200()


================================================================================
GAP 3 (HIGH): Structured Approval Chain Fields in RelationContext
================================================================================

WHY IT MATTERS
--------------
"A VP approves a discount on a Zoom call.  The opportunity record shows the
final price.  It doesn't show who approved the deviation or why."

Currently approval metadata is prose inside decision_trace.  Structured fields
make approval-chain queries possible without NLP: "find all exceptions approved
by VP Smith via Slack in Q3."

IMPLEMENTATION
--------------

1. Extend RelationContext dataclass:

   File: lightrag/context_graph_types.py

   Add the following fields to RelationContext (all Optional to preserve
   backward compatibility with edges extracted without them):

       approved_by: Optional[str] = None
       """Entity name of the approver (e.g., 'VP_Smith', 'Finance_Team')."""

       approved_via: Optional[str] = None
       """Channel: 'slack', 'zoom', 'email', 'in_person', 'jira', 'system'."""

       valid_from: Optional[str] = None
       """ISO-8601 date string: when this decision became effective."""

       valid_until: Optional[str] = None
       """ISO-8601 date string: when this decision expires or lapses."""

       policy_ref: Optional[str] = None
       """ID or name of the policy this decision follows or overrides
       (e.g., 'DiscountPolicy_v3.2', 'SLA_Healthcare_Exception')."""

   Keep temporal_info as the human-readable free-text fallback; valid_from /
   valid_until are the machine-queryable equivalents.

2. Update merge() to handle the new fields (same first-non-None strategy).

3. Update the CG extraction system prompt:

   File: lightrag/prompt.py

   Extend PROMPTS["cg_entity_extraction_system_prompt"] to instruct the LLM
   to populate approved_by and approved_via when detectable from the text.
   Add to the JSON schema example in the prompt:

       "approved_by": "VP Smith",
       "approved_via": "slack",
       "valid_from": "2024-08-14",
       "valid_until": "2024-12-31",
       "policy_ref": "DiscountPolicy_Standard"

4. Update RelationContextData Pydantic model:

   File: lightrag/api/routers/context_graph_routes.py

   Add the five new Optional[str] fields to RelationContextData so they are
   exposed through all three context-graph API endpoints.

5. Tests to add: tests/test_context_graph.py
   - test_relation_context_approved_by_field()
   - test_relation_context_valid_until_field()
   - test_relation_context_policy_ref_field()
   - test_merge_preserves_approved_by()


================================================================================
GAP 4 (HIGH): Decision Trace Search Endpoint
================================================================================

WHY IT MATTERS
--------------
The precedent search (Gap 2) covers semantic similarity over decision_trace
text.  This gap covers structured / filtered queries that do not require an
embedding: "show me all VP-approved exceptions in Q3 above 15% discount."

IMPLEMENTATION
--------------

1. Add get_all_decisions() to ContextGraph:

   async def get_all_decisions(
       self,
       approved_by: str | None = None,
       approved_via: str | None = None,
       policy_ref: str | None = None,
       min_confidence: float = 0.0,
       has_decision_trace: bool = True,
   ) -> list[dict]:
       """Return all edges that carry a RelationContext, optionally filtered.

       Iterates the full graph edge set; suitable for admin/audit use cases.
       For semantic search, use find_precedents() instead.
       """
       # graph.get_all_edges() — add this method to BaseGraphStorage if missing
       all_edges = await self.chunk_entity_relation_graph.get_all_edges()
       results = []
       for src, tgt, edge in all_edges:
           rc_json = edge.get("relation_context")
           if not rc_json:
               continue
           rc = RelationContext.from_json(rc_json)
           if has_decision_trace and not rc.decision_trace:
               continue
           if rc.confidence_score < min_confidence:
               continue
           if approved_by and rc.approved_by != approved_by:
               continue
           if approved_via and rc.approved_via != approved_via:
               continue
           if policy_ref and rc.policy_ref != policy_ref:
               continue
           results.append({"src_id": src, "tgt_id": tgt, "relation_context": rc})
       return results

2. Expose as API endpoint:

   GET /graph/decisions?approved_by=VP_Smith&approved_via=slack&min_confidence=0.8

   Response:
   {
     "total_count": 3,
     "decisions": [ { "src_id": ..., "tgt_id": ..., "relation_context": {...} } ]
   }

3. Note: requires get_all_edges() on the graph storage backend.  For
   NetworkXStorage this is straightforward (nx.Graph.edges()).  For Neo4j /
   PostgreSQL, a MATCH (a)-[r]->(b) WHERE r.relation_context IS NOT NULL
   query suffices.

4. Tests to add: tests/test_context_graph_api.py
   - test_get_decisions_filter_by_approved_by()
   - test_get_decisions_filter_by_approved_via()
   - test_get_decisions_returns_503_for_plain_lightrag()


================================================================================
GAP 5 (HIGH): Queryable Temporal Fields
================================================================================

WHY IT MATTERS
--------------
temporal_info is currently a free-text string ("Valid until Q4 2024").
You cannot query "show me all active decisions as of today" without NLP.

This is resolved as part of Gap 3 (valid_from / valid_until fields).  The
additional work here is filtering support on the search endpoints.

IMPLEMENTATION
--------------

1. Add temporal filtering to get_all_decisions() (Gap 4):

   active_as_of: str | None = None   # ISO-8601 date; filters valid_from <= date <= valid_until

2. Add temporal filtering to the GET /graph/decisions endpoint:

   GET /graph/decisions?active_as_of=2024-10-01

3. Add is_active() helper to RelationContext:

   def is_active(self, as_of: str | None = None) -> bool:
       """Return True if this decision is currently valid.

       Uses valid_from / valid_until if present; falls back to True (unknown).
       """
       import datetime
       if not self.valid_from and not self.valid_until:
           return True
       today = as_of or datetime.date.today().isoformat()
       if self.valid_from and today < self.valid_from:
           return False
       if self.valid_until and today > self.valid_until:
           return False
       return True

4. Tests to add: tests/test_context_graph.py
   - test_is_active_within_range()
   - test_is_active_expired()
   - test_is_active_no_dates_returns_true()


================================================================================
GAP 6 (MEDIUM): Multi-Source Attribution
================================================================================

WHY IT MATTERS
--------------
"The support lead checks ARR in Salesforce, sees escalations in Zendesk, reads
a Slack thread flagging churn risk."  A single decision is synthesised from
multiple systems.  Currently provenance is one free-text string.

IMPLEMENTATION
--------------

1. Add sources list to RelationContext:

   File: lightrag/context_graph_types.py

   @dataclass
   class SourceRef:
       system: str        # 'salesforce', 'zendesk', 'slack', 'pagerduty', 'zoom'
       record_id: str     # CRM ID, ticket ID, thread ID, incident ID
       url: Optional[str] = None

   Add to RelationContext:
       sources: List[SourceRef] = field(default_factory=list)

   Keep provenance as the free-text fallback.  sources is the structured
   equivalent for programmatic cross-system queries.

2. Update merge() to union sources lists (deduplicate by (system, record_id)).

3. Update extraction prompt to emit sources array where system names are
   detectable (e.g., "Slack #deals-review" → system: slack).

4. Update API Pydantic models to serialize/deserialize sources.

5. Tests: test_relation_context_sources_merge_deduplicates()


================================================================================
GAP 7 (MEDIUM): Policy Nodes
================================================================================

WHY IT MATTERS
--------------
Implementation Plan Phase 3: "Map governance policies as nodes; automate
exception routing based on 'why' links."  The article: "Rules tell an agent
what SHOULD happen... Decision traces capture what happened in THIS case."

Without policy nodes, there is no way to traverse "which exceptions overrode
DiscountPolicy_Standard?" as a graph query.

IMPLEMENTATION
--------------

1. Add POLICY entity type to extraction prompt:

   File: lightrag/prompt.py

   In cg_entity_extraction_system_prompt, extend entity_types to include
   "Policy".  Example extraction:

       entity<|#|>DiscountPolicy_Standard<|#|>Policy<|#|>
       Standard discount policy: maximum 10% without VP approval.

2. Add policy extraction examples to cg_entity_extraction_examples:

   Show an exception that references a policy node:

       relation<|#|>VP_Smith<|#|>MegaCorp_Renewal_Q4<|#|>APPROVED_EXCEPTION<|#|>
       VP Smith approved 20% discount overriding standard policy.<|#|>
       {"decision_trace": "VP approved citing 3 SEV-1s", "policy_ref":
        "DiscountPolicy_Standard", "confidence_score": 0.97}

       relation<|#|>VP_Smith<|#|>DiscountPolicy_Standard<|#|>OVERRODE<|#|>
       VP Smith overrode the standard discount policy.<|#|>
       {"decision_trace": "Exception granted for healthcare vertical",
        "confidence_score": 0.92}

3. Add GET /graph/policy/{policy_name}/exceptions endpoint:

   Returns all edges where policy_ref == policy_name, showing every
   decision that deviated from that policy.

4. Tests: test_policy_node_extracted(), test_get_policy_exceptions_endpoint()


================================================================================
GAP 8 (MEDIUM): Agent Run Tracking
================================================================================

WHY IT MATTERS
--------------
The article: entities include "agent runs."  Every cgr3_query() execution
should be a first-class node recording: query, context retrieved, answer,
iterations used, edges traversed.  This creates the feedback loop: "every
automated decision adds another trace to the graph."

IMPLEMENTATION
--------------

1. Add AgentRun persistence to cgr3_query():

   File: lightrag/context_graph.py

   At the start of cgr3_query(), generate a run_id (UUID).  At the end,
   persist an AgentRun node and emit_decision_trace() edges from the run
   to every entity it concluded was relevant.

   @dataclass
   class AgentRun:
       run_id: str
       query: str
       mode: str
       iterations_used: int
       answer: str
       entities_consulted: List[str]
       timestamp: str   # ISO-8601

   Persist as a graph node:
       entity_name = f"AgentRun_{run_id}"
       entity_type = "AGENT_RUN"
       description = f"CGR3 query: {query[:200]}"

   Emit edges from the run to each consulted entity:
       INFORMED_BY edge from AgentRun node → each entity/relation that
       contributed to the final answer.

2. Expose as API endpoint:

   GET /graph/agent-runs?limit=20

   Returns recent agent run nodes with their query, answer, and entity links.

3. This is the "feedback loop" from the article: after enough runs, patterns
   emerge — which entities are consulted for which query types — enabling
   proactive context pre-fetching.

4. Tests: test_cgr3_query_persists_agent_run_node()


================================================================================
GAP 9 (LOW): Sentence-BERT for Supporting Sentence Selection
================================================================================

WHY IT MATTERS
--------------
Implementation Plan Phase 2: "Sentence-BERT Integration: Use Sentence-BERT to
identify the top-γ supporting sentences."  Currently supporting_sentences are
whatever the LLM extracts verbatim.  Sentence-BERT would provide semantic
deduplication and quality filtering.

IMPLEMENTATION
--------------

1. After extraction, post-process supporting_sentences with semantic similarity:

   File: lightrag/context_graph.py  (in _process_cg_extraction_result)

   If sentence_transformer is configured, embed each candidate sentence,
   embed the relation description, and keep only top-γ by cosine similarity.

2. Configuration:
   Add sentence_transformer_model: Optional[str] = None to ContextGraph.
   If None, skip (default behavior preserved).

3. This is a quality improvement, not a capability gap.  Deprioritise until
   Gaps 1-4 are closed.


================================================================================
UPDATED ROADMAP
================================================================================

Phase  | Focus                          | Gaps Closed  | Prerequisites
-------|--------------------------------|--------------|------------------
5A     | Real-time capture              | Gap 1, 3     | None — pure additions
5B     | Precedent search               | Gap 2, 4, 5  | Gap 3 (new fields)
5C     | Cross-system & policies        | Gap 6, 7     | Gap 3 (SourceRef)
5D     | Feedback loop                  | Gap 8        | Gap 1 (emit_decision_trace)
5E     | Semantic quality               | Gap 9        | Optional / last

Phase 5A and 5B are the highest-leverage work and can run in parallel.

File change summary for Phase 5A + 5B:
  lightrag/context_graph_types.py  — add 5 fields + SourceRef + is_active()
  lightrag/context_graph.py        — emit_decision_trace(), find_precedents(),
                                     get_all_decisions(), decisions_vdb init
  lightrag/prompt.py               — extend rc JSON schema in system prompt
  lightrag/api/routers/context_graph_routes.py
                                   — POST /graph/decision/emit
                                   — GET  /graph/decisions/search
                                   — GET  /graph/decisions
  tests/test_context_graph.py      — ~12 new unit tests
  tests/test_context_graph_api.py  — ~8 new API tests