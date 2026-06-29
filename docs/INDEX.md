# Context Graph — Documentation

**Context Graph (CG)** is a decision-aware knowledge-graph system built on
[LightRAG](https://github.com/HKUDS/LightRAG). It extends triples `(h, r, t)` into
contextual quadruples `(h, r, t, rc)` — attaching a **RelationContext** (the decision
record: who/why/when/under-what-policy) to every edge — and adds CGR3 multi-hop
reasoning, runtime decision capture, precedent search, a REST API, and an MCP server.

## Start here

- **[CONTEXT_GRAPH_OVERVIEW.html](CONTEXT_GRAPH_OVERVIEW.html)** — illustrated, self-contained
  technical field guide (APIs, endpoints, usage examples, diagrams). Open in any browser.
- **[BLOG_THE_FOURTH_ELEMENT.html](BLOG_THE_FOURTH_ELEMENT.html)** — narrative intro: why decisions,
  not just facts, belong in the graph.

## Reference (this set)

| Doc | Covers |
|-----|--------|
| [architecture.md](architecture.md) | System layers, data flow, storage, server, multi-tenancy |
| [data-model.md](data-model.md) | The quadruple, RelationContext (11 fields), ContextNode/ContextEdge |
| [ingestion-and-querying.md](ingestion-and-querying.md) | Extraction & emission paths; query modes; CGR3; annotated context |
| [api-reference.md](api-reference.md) | REST endpoints, MCP tools, auth, workspace header, examples |
| [configuration.md](configuration.md) | Environment variables, storage backends, LLM providers, running |
| [CODE_REVIEW.md](CODE_REVIEW.md) | Findings from the code review (bugs + logic issues, ranked) |

## Existing background docs

- [Algorithm.md](Algorithm.md) — indexing & retrieval pipeline (with the 6-field extraction format)
- [CONTEXTGRAPH_PAPER.md](CONTEXTGRAPH_PAPER.md) — the long-form design paper
- [PaperComparison.md](PaperComparison.md) — alignment with the CGR3 paper (Liang et al., 2024)
- [DockerDeployment.md](DockerDeployment.md) · [OfflineDeployment.md](OfflineDeployment.md) ·
  [FrontendBuildGuide.md](FrontendBuildGuide.md) — operations

## One-paragraph mental model

Documents (or live application events) flow into an LLM extraction/emission step that writes
entities, relations, and a **RelationContext** onto each edge. Everything lands in four pluggable
storage layers (graph, vector, KV, doc-status), isolated per workspace. Queries run in one of six
modes — or through **CGR3** (Retrieve → Rank → Reason) for multi-hop questions — and the answer is
grounded in the decision context attached to each edge. The whole thing is exposed over REST and
an MCP server on port `9621`.

## Credits

Built on [LightRAG](https://github.com/HKUDS/LightRAG) (HKUDS), implementing the
[CGR3 paradigm](https://arxiv.org/abs/2406.11160) (Liang et al., 2024). Supported by
[Sellence](https://www.sellence.com/).
