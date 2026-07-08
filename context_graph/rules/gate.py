"""The pre-emit rules gate (wiring step 5).

A thin, CG-owned object that a decision passes through before it is written:
it projects the decision to ``params`` (:mod:`context_graph.rules.projection`),
evaluates the workspace rule set (:class:`~context_graph.rules.RulesEngine`), and
returns a :class:`GateDecision` (``PASS`` / ``FLAG`` / ``REJECT``) plus the audit
record.

The decision path (e.g. ``ContextGraph.emit_decision_trace``) holds an optional
``rules_gate``; when present it calls :meth:`RulesGate.check` before persisting:

* ``REJECT`` → raise :class:`RuleViolation` (caller maps to HTTP 422; nothing written),
* ``FLAG``   → persist, annotated with ``needs_review`` + the audit record,
* ``PASS``   → persist unchanged.

Keeping this in ``context_graph/`` means the only thing the existing decision
path needs is a guarded call into here — the engine is a dependency it *calls
into*, not part of the ``lightrag`` library.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from context_graph.types import RelationContext

from context_graph.rules.engine import EvaluationResult, RulesEngine
from context_graph.rules.similarity import ConceptCatalog


@dataclass
class GateDecision:
    """The outcome of running one decision through the gate."""

    outcome: str                  # PASS | FLAG | REJECT
    audit: Dict[str, Any]         # the audit record (matched concept · score · threshold)
    result: EvaluationResult

    @property
    def blocked(self) -> bool:
        return self.outcome == "REJECT"

    @property
    def flagged(self) -> bool:
        return self.outcome == "FLAG"

    @property
    def passed(self) -> bool:
        return self.outcome == "PASS"


class RuleViolation(Exception):
    """Raised when the gate REJECTs a decision. Carries the :class:`GateDecision`."""

    def __init__(self, decision: GateDecision) -> None:
        self.decision = decision
        reason = decision.audit.get("reason") or "rejected by a business rule"
        super().__init__(reason)


class RulesGate:
    """Projects + evaluates a decision and returns a :class:`GateDecision`."""

    def __init__(self, engine: RulesEngine) -> None:
        self._engine = engine

    @classmethod
    def from_dsl(cls, catalog: ConceptCatalog, dsl: str) -> "RulesGate":
        """Build a gate from a concept catalog and a DSL rule set (convenience)."""
        return cls(RulesEngine(catalog).load(dsl))

    @property
    def engine(self) -> RulesEngine:
        return self._engine

    def check(
        self,
        src: str,
        tgt: str,
        relation_type: Optional[str],
        rc: RelationContext,
        *,
        as_of: Optional[str] = None,
    ) -> GateDecision:
        """Evaluate the rule set against one decision; never raises on its own.

        The caller decides what to do with the outcome (raise on ``blocked``,
        annotate on ``flagged``, persist on ``passed``).
        """
        # Imported here to avoid a hard import cycle at module load.
        from context_graph.rules.projection import project_decision

        params = project_decision(src, tgt, relation_type, rc, as_of=as_of)
        result = self._engine.evaluate(params)
        return GateDecision(outcome=result.outcome, audit=result.audit(), result=result)
