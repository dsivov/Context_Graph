"""Entity deduplication (Graph-Quality v-next, Phase 1).

A conservative, reversible, type-aware resolver in front of LightRAG's exact-name
merge. Layers (see docs/GRAPH_QUALITY_VNEXT.html, D1–D7):

* **A** ``canonical`` — deterministic canonical key (casefold, de-suffix, de-dot).
* **D** ``store`` — reversible alias→canonical map + audited merge log (the undo).
* **B** ``resolver`` — inline: rule/alias resolve, then high-threshold embedding
  auto-merge (type-aware); ambiguous pairs go to a review queue, never merged inline.
* **C/E** ``sweep`` — async LLM adjudication of the gray zone + canonical naming.
"""

from context_graph.dedup.canonical import (
    canonicalize,
    prefer_canonical_name,
    representativeness,
    is_acronym_of,
)
from context_graph.dedup.store import (
    DedupStore,
    InMemoryDedupStore,
    JsonDedupStore,
    MergeRecord,
)
from context_graph.dedup.resolver import (
    EntityResolver,
    Resolution,
    type_ok,
    name_ok,
    DEFAULT_HARD,
    DEFAULT_GRAY,
    NEW,
    MERGE,
    REVIEW,
)
from context_graph.dedup.sweep import DedupSweep

__all__ = [
    "canonicalize",
    "prefer_canonical_name",
    "representativeness",
    "is_acronym_of",
    "DedupStore",
    "InMemoryDedupStore",
    "JsonDedupStore",
    "MergeRecord",
    "EntityResolver",
    "Resolution",
    "type_ok",
    "name_ok",
    "DEFAULT_HARD",
    "DEFAULT_GRAY",
    "NEW",
    "MERGE",
    "REVIEW",
    "DedupSweep",
]
