"""Community detection over the entity graph (Louvain, via networkx — no new dep).

Backend-agnostic: builds an undirected graph from ``get_all_labels`` +
``get_all_edges`` and partitions it. Deterministic (fixed seed) so a rebuild is
reproducible. Tiny communities (below ``min_size``) are dropped — singletons are
isolates, which are the connectivity pass's job, not a "theme".
"""

from __future__ import annotations

from typing import Any, Dict, List

import networkx as nx


def detect_communities(
    labels: List[str], edges: List[Dict[str, Any]], *,
    min_size: int = 2, resolution: float = 1.0, seed: int = 42,
) -> List[List[str]]:
    """Partition entities into communities. Returns member lists, largest first."""
    graph = nx.Graph()
    graph.add_nodes_from(labels)
    for e in edges:
        a, b = e.get("source"), e.get("target")
        if a in graph and b in graph and a != b:
            graph.add_edge(a, b)
    if graph.number_of_nodes() == 0:
        return []
    communities = nx.community.louvain_communities(
        graph, resolution=resolution, seed=seed
    )
    out = [sorted(c) for c in communities if len(c) >= min_size]
    out.sort(key=len, reverse=True)
    return out
