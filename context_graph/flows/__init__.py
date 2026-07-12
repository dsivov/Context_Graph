"""Context Graph flows — BPMN-lite flow definitions + run persistence (P0).

    from context_graph.flows import FlowDefinition, FlowNode, FlowEdge, Run
    from context_graph.flows.store import InMemoryRunStore

The executor (P2) walks a FlowDefinition's five node kinds — event, task,
gateway, timer, state — composing rules/lifecycle/actions. See
docs/PLATFORM_ARCHITECTURE.html and docs/PLATFORM_WORK_PLAN.md.
"""

from context_graph.flows.schema import (
    NODE_KINDS,
    RUN_STATUSES,
    FlowDefinition,
    FlowEdge,
    FlowNode,
    Run,
)
from context_graph.flows.store import (
    InMemoryRunStore,
    JsonRunStore,
    RunStore,
)

__all__ = [
    "NODE_KINDS",
    "RUN_STATUSES",
    "FlowDefinition",
    "FlowEdge",
    "FlowNode",
    "Run",
    "RunStore",
    "InMemoryRunStore",
    "JsonRunStore",
]
