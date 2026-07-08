"""Graph connectivity repair (Graph-Quality v-next, Topic 3).

Asserted-edges-only (D14): no mechanical co-occurrence/embedding edges. Instead an
async LLM **isolate rescue** — for each disconnected node, embedding finds candidate
existing nodes and the LLM adds only relationships their descriptions clearly support.
"""

from context_graph.connectivity.rescue import IsolateRescue

__all__ = ["IsolateRescue"]
