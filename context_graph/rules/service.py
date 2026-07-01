"""RulesService — the glue between the store, the gate, and the API (step 7).

Holds a :class:`~context_graph.rules.store.RuleStore`, caches a built
:class:`~context_graph.rules.gate.RulesGate` per workspace (rebuilt when the
bundle's version changes), and offers the operations the ``/rules`` router and
the server need:

* ``get_summary(ws)``  — bundle metadata + the parsed rule list (name/priority),
* ``save`` / ``set_enabled`` / ``delete`` — mutate, invalidating the gate cache,
* ``gate_for(ws)``     — the live gate (or ``None`` when absent/disabled),
* ``evaluate(ws, …)``  — dry-run the saved policy against one decision,
* ``attach(rag, ws)``  — push the current gate onto a workspace's rag instance.

The model is loaded lazily (only when a gate actually evaluates), so summaries
and saves stay fast and offline.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from lightrag.context_graph_types import RelationContext
from lightrag.utils import logger

from context_graph.rules.gate import GateDecision, RulesGate
from context_graph.rules.store import RuleBundle, RuleStore


class RulesService:
    def __init__(self, store: RuleStore, *, gate_backend: Any = None) -> None:
        self._store = store
        self._backend = gate_backend  # None → real model; injectable for tests
        self._cache: Dict[str, tuple] = {}  # workspace -> (version, RulesGate|None)

    @property
    def store(self) -> RuleStore:
        return self._store

    @property
    def gate_backend(self) -> Any:
        """The similarity backend used for gates (None → real model). Reused by
        the NL→DSL agent's dry-run so tests can inject a fake."""
        return self._backend

    # -- queries -----------------------------------------------------------

    def get_summary(self, workspace: str) -> Dict[str, Any]:
        bundle = self._store.load(workspace)
        if bundle is None:
            return {"workspace": workspace, "exists": False, "enabled": False,
                    "rules": [], "concepts": []}
        return {
            "workspace": workspace,
            "exists": True,
            "enabled": bundle.enabled,
            "version": bundle.version,
            "model_id": bundle.model_id,
            "updated_at": bundle.updated_at,
            "concepts": sorted(bundle.concepts),
            "rules": self._list_rules(bundle.dsl),
        }

    @staticmethod
    def _list_rules(dsl: str) -> List[Dict[str, Any]]:
        from business_rule_engine import RuleParser

        parser = RuleParser()
        parser.parsestr(dsl)
        return [
            {"name": getattr(r, "rulename", "?"), "priority": getattr(r, "priority", 0)}
            for r in parser.rules.values()
        ]

    # -- mutations ---------------------------------------------------------

    def save(self, workspace: str, dsl: str, concepts: Dict[str, List[str]],
             *, enabled: bool = True, model_id: Optional[str] = None) -> RuleBundle:
        bundle = self._store.save(workspace, dsl, concepts,
                                  enabled=enabled, model_id=model_id)
        self._cache.pop(workspace, None)
        return bundle

    def set_enabled(self, workspace: str, enabled: bool) -> RuleBundle:
        bundle = self._store.set_enabled(workspace, enabled)
        self._cache.pop(workspace, None)
        return bundle

    def delete(self, workspace: str) -> bool:
        ok = self._store.delete(workspace)
        self._cache.pop(workspace, None)
        return ok

    # -- gate ---------------------------------------------------------------

    def gate_for(self, workspace: str) -> Optional[RulesGate]:
        """The current gate for *workspace*, cached by bundle version."""
        bundle = self._store.load(workspace)
        if bundle is None or not bundle.enabled:
            self._cache.pop(workspace, None)
            return None
        cached = self._cache.get(workspace)
        if cached is not None and cached[0] == bundle.version and cached[1] is not None:
            return cached[1]
        gate = self._store.build_gate(workspace, backend=self._backend)
        self._cache[workspace] = (bundle.version, gate)
        return gate

    def evaluate(self, workspace: str, src: str, tgt: str,
                 relation_type: Optional[str], rc: RelationContext,
                 *, as_of: Optional[str] = None) -> Optional[GateDecision]:
        """Dry-run the saved policy against one decision; None if no active gate."""
        gate = self.gate_for(workspace)
        if gate is None:
            return None
        return gate.check(src, tgt, relation_type, rc, as_of=as_of)

    def attach(self, rag: Any, workspace: str) -> Optional[RulesGate]:
        """Set ``rag.rules_gate`` to the current gate (or None). Returns the gate.

        ``rag`` may be a ContextGraph instance or the request's WorkspaceProxy —
        either forwards the assignment to the workspace's real instance.
        """
        gate = self.gate_for(workspace)
        try:
            rag.rules_gate = gate
        except Exception as e:  # pragma: no cover - defensive (non-CG rag)
            logger.warning(f"Could not attach rules gate for '{workspace}': {e}")
        return gate
