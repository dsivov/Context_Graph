"""Node-quality filtering (Graph-Quality v-next, Phase 2 / Topic 2).

A deterministic, conservative gate that keeps the obvious garbage out of the graph
without needing an ontology (D12: conservative strictness). Pronoun/deictic/stop-word
names and empty descriptions are rejected; everything else passes. Failing nodes are
quarantined (not dropped) by the caller.
"""

from context_graph.quality.gate import (
    QualityVerdict,
    quality_check,
    is_garbage_name,
)

__all__ = ["QualityVerdict", "quality_check", "is_garbage_name"]
