"""Graph communities & thematic "global" retrieval (Graph-Quality v-next, Topic 3, Layer 4).

Real community detection (Louvain, via networkx) partitions the entity graph into
themes; the LLM summarises each; a query retrieves the relevant community summaries
and synthesises a holistic answer — a genuine GraphRAG-style "global" mode, unlike
the current vector-search-over-edges "global".
"""

from context_graph.community.detect import detect_communities
from context_graph.community.summarize import CommunitySummarizer
from context_graph.community.store import (
    CommunityStore,
    InMemoryCommunityStore,
    JsonCommunityStore,
)

__all__ = [
    "detect_communities",
    "CommunitySummarizer",
    "CommunityStore",
    "InMemoryCommunityStore",
    "JsonCommunityStore",
]
