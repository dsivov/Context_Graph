"""LifecycleService — glue between the lifecycle store, the graph, and the API.

Checks transitions and reads/writes the object's current state on its graph node.
No lifecycle / no machine for a type → permissive.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from lightrag.utils import logger

from context_graph.lifecycle.schema import Decision, Lifecycle, StateMachine
from context_graph.lifecycle.store import LifecycleStore


class LifecycleService:
    def __init__(self, store: LifecycleStore) -> None:
        self._store = store

    @property
    def store(self) -> LifecycleStore:
        return self._store

    # -- queries -----------------------------------------------------------

    def get_summary(self, workspace: str) -> Dict[str, Any]:
        lc = self._store.load(workspace)
        if lc is None:
            return {"workspace": workspace, "exists": False, "machines": {}}
        meta = self._store.meta(workspace) or {}
        return {
            "workspace": workspace,
            "exists": True,
            "name": lc.name,
            "version": lc.version,
            "updated_at": meta.get("updated_at"),
            "machines": {ot: {"states": m.states, "initial": m.initial,
                              "transitions": [t.to_dict() for t in m.transitions]}
                         for ot, m in lc.machines.items()},
        }

    def machine_for(self, workspace: str, object_type: str) -> Optional[StateMachine]:
        lc = self._store.load(workspace)
        return lc.machine_for(object_type) if lc is not None else None

    def check(self, workspace: str, object_type: str, from_state: str, to: str,
              *, role: Optional[str] = None) -> Decision:
        lc = self._store.load(workspace)
        if lc is None:
            return Decision(True, "no lifecycle — permissive")
        return lc.check(object_type, from_state, to, role)

    # -- mutations ---------------------------------------------------------

    def save(self, workspace: str, lifecycle_dict: Dict[str, Any]) -> Lifecycle:
        lc = Lifecycle.from_dict(lifecycle_dict)
        return self._store.save(workspace, lc)

    def delete(self, workspace: str) -> bool:
        return self._store.delete(workspace)

    # -- graph-backed state ------------------------------------------------

    async def current_state(self, rag: Any, object_ref: str, machine: StateMachine) -> str:
        """Read the object's current state from its graph node, or the machine's
        ``initial`` if the node is absent or carries no state."""
        try:
            node = await rag.chunk_entity_relation_graph.get_node(object_ref)
        except Exception as e:  # pragma: no cover - defensive
            logger.warning(f"lifecycle current_state read failed for '{object_ref}': {e}")
            node = None
        return (node or {}).get(machine.prop) or machine.initial

    async def apply(self, rag: Any, object_ref: str, machine: StateMachine, to: str) -> None:
        """Persist the new state onto the object's graph node (read-modify-write)."""
        try:
            node = await rag.chunk_entity_relation_graph.get_node(object_ref) or {}
            node = {**node, machine.prop: to}
            await rag.chunk_entity_relation_graph.upsert_node(object_ref, node)
        except Exception as e:  # pragma: no cover - defensive
            logger.warning(f"lifecycle apply failed for '{object_ref}' -> {to}: {e}")
