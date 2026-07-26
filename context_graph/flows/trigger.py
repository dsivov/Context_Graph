"""FlowTrigger — the bus subscriber that starts flows from events (P2).

Mirrors :class:`~context_graph.integration.service.DecisionSubscriber`: it sits
on the ingress bus and, for each event, starts every flow in that workspace
subscribed to the event's type. :meth:`FlowExecutor.start` is idempotent on the
run id, so a re-delivered event never double-starts a run.

Flow orchestration is deliberately *isolated* from the ingress response: a flow
that halts (a gate REJECT on one of its task actions, a bad definition) is
logged, not raised, so it never turns a successful delivery into an error. The
primary decision quad is the DecisionSubscriber's concern; the flow is the
conductor layered on top.
"""

from __future__ import annotations

from typing import Any

from lightrag.utils import logger

from context_graph.events.schema import Event


class FlowTrigger:
    def __init__(self, flow_store: Any, executor: Any) -> None:
        self._flows = flow_store
        self._executor = executor

    async def __call__(self, event: Event) -> None:
        workspace = event.workspace or "default"
        try:
            flows = self._flows.for_event(workspace, event.type)
        except Exception as e:  # pragma: no cover - defensive
            logger.warning(f"FlowTrigger could not list flows for '{event.type}': {e}")
            return

        for flow in flows:
            try:
                run = await self._executor.start(workspace, flow, event=event)
                logger.info(
                    f"FlowTrigger: '{event.type}' → flow '{flow.id}' "
                    f"run '{run.run_id}' ({run.status})"
                )
            except Exception as e:
                logger.warning(
                    f"FlowTrigger: flow '{flow.id}' failed to start on "
                    f"'{event.type}': {e}"
                )
