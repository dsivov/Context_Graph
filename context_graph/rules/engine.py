"""The Business Rules Engine wrapper for Context Graph (wiring step 3).

Wraps `business_rule_engine <https://github.com/manfred-kaiser/business-rule-engine>`_
with everything CG needs to gate a decision:

* registers the field-level ``sim()`` / ``similar()`` predicates (bound to a
  workspace :class:`~context_graph.rules.ConceptCatalog`) plus the action verbs
  ``reject`` / ``flag`` / ``notify``;
* evaluates rules **one at a time in priority order**, so each triggered rule's
  ``sim()`` calls can be attributed to *it* — that is what fills the audit record
  (matched concept · score · threshold);
* is **fail-open and isolated**: a rule that errors (e.g. a missing numeric in
  ``amount > 10000``, or an unknown concept) is skipped with a warning rather
  than taking down the gate;
* keeps workspaces isolated. ``business_rule_engine`` registers functions on a
  *class-level* dict shared by every parser, so this engine instead assigns its
  own functions dict to each parsed rule — two engines never clobber each other.

Output is an :class:`EvaluationResult` whose :meth:`~EvaluationResult.audit`
matches the record in ``docs/CLOSING_THE_GAPS.html`` §06.
"""

from __future__ import annotations

import re
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from lightrag.utils import logger

from context_graph.rules.similarity import ConceptCatalog, DEFAULT_MATCH_THRESHOLD

# ─────────────────────────────────────────────────────────────────────────────
# Outcomes / actions
# ─────────────────────────────────────────────────────────────────────────────

# Gate severity. NOTIFY is a side-channel (does not change persist/block).
_SEVERITY = {"NOTIFY": 0, "FLAG": 1, "REJECT": 2}
_OUTCOME_BY_SEVERITY = {0: "PASS", 1: "FLAG", 2: "REJECT"}


@dataclass
class Action:
    """The result of an action verb in a rule's ``then`` clause."""

    kind: str  # REJECT | FLAG | NOTIFY
    reason: str = ""


def _reject(reason: str = "") -> Action:
    return Action("REJECT", reason)


def _flag(reason: str = "") -> Action:
    return Action("FLAG", reason)


def _notify(reason: str = "") -> Action:
    return Action("NOTIFY", reason)


# Action verbs available in a rule's `then` clause, with one-line descriptions
# (used by the NL→DSL agent's capability manifest).
ACTION_VERBS: Dict[str, str] = {
    "reject": "block the decision — HTTP 422, nothing persisted",
    "flag": "persist the decision but mark it needs_review",
    "notify": "record a side note; does not change the gate outcome",
}


# Per-evaluation, per-rule recorder of sim()/similar() calls. A ContextVar so
# concurrent evaluate() calls (e.g. async server requests) never share state.
_RECORDER: "ContextVar[Optional[list]]" = ContextVar("cg_sim_recorder", default=None)

# sim(field, "CONCEPT") <op> <number>  — used to recover the threshold a rule
# compared a sim() score against, for the audit record.
_SIM_CMP_RE = re.compile(
    r"""sim\s*\(\s*[^,]+,\s*["']?(?P<concept>[A-Za-z0-9_]+)["']?\s*\)
        \s*(?:<=|>=|<|>|==)\s*(?P<thr>[0-9]*\.?[0-9]+)""",
    re.VERBOSE,
)


# ─────────────────────────────────────────────────────────────────────────────
# Result types
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class RuleMatch:
    """One triggered rule and why it triggered."""

    rule: str
    actions: List[Action]
    matches: List[Dict[str, Any]]  # [{concept, score, threshold}] from sim()/similar()
    priority: int = 0

    @property
    def severity(self) -> int:
        return max((_SEVERITY.get(a.kind, 0) for a in self.actions), default=0)

    @property
    def reason(self) -> str:
        """Reason of this rule's highest-severity action."""
        if not self.actions:
            return ""
        top = max(self.actions, key=lambda a: _SEVERITY.get(a.kind, 0))
        return top.reason

    def strongest_match(self) -> Optional[Dict[str, Any]]:
        """The decisive sim() match (highest score), or None for an all-hard rule."""
        return max(self.matches, key=lambda m: m["score"], default=None)


@dataclass
class EvaluationResult:
    """The outcome of evaluating a rule set against one decision's ``params``."""

    outcome: str  # PASS | FLAG | REJECT
    triggered: List[RuleMatch] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)       # notify() reasons
    warnings: List[str] = field(default_factory=list)     # rules skipped on error
    model_id: Optional[str] = None

    @property
    def blocked(self) -> bool:
        return self.outcome == "REJECT"

    def decisive(self) -> Optional[RuleMatch]:
        """The triggered rule that set the outcome (highest severity, then priority)."""
        gate = [m for m in self.triggered if m.severity > 0]
        if not gate:
            return None
        # self.triggered is already priority-desc, so max() keeps the first on ties.
        return max(gate, key=lambda m: m.severity)

    def audit(self) -> Dict[str, Any]:
        """The audit record (matches the §06 worked example)."""
        rec: Dict[str, Any] = {"outcome": self.outcome, "model_id": self.model_id}
        d = self.decisive()
        if d is not None:
            rec["rule"] = d.rule
            rec["reason"] = d.reason
            sm = d.strongest_match()
            if sm is not None:
                rec["matched_concept"] = sm["concept"]
                rec["score"] = round(sm["score"], 4)
                rec["threshold"] = sm["threshold"]
        if self.notes:
            rec["notes"] = list(self.notes)
        if self.warnings:
            rec["warnings"] = list(self.warnings)
        return rec


# ─────────────────────────────────────────────────────────────────────────────
# Engine
# ─────────────────────────────────────────────────────────────────────────────


class RulesEngine:
    """A compiled, workspace-scoped rule set with the CG predicates bound in.

    Usage::

        engine = RulesEngine(catalog)
        engine.load(dsl_text)
        result = engine.evaluate(params)   # params from project_decision()
        if result.blocked: ...
        audit = result.audit()
    """

    def __init__(self, catalog: ConceptCatalog) -> None:
        self._catalog = catalog
        self._funcs = self._build_funcs()
        self._rules: list = []                  # parsed rules, sorted priority desc
        self._threshold_cache: Dict[str, Dict[str, float]] = {}

    @property
    def model_id(self) -> str:
        return self._catalog.model_id

    # -- predicate / action registry ---------------------------------------

    def _build_funcs(self) -> Dict[str, Any]:
        catalog = self._catalog

        def sim(value, concept):
            score = catalog.score(value, concept)
            rec = _RECORDER.get()
            if rec is not None:
                rec.append(
                    {"concept": str(concept).strip().upper(),
                     "score": float(score), "threshold": None}
                )
            return score

        def similar(value, concept, threshold: float = DEFAULT_MATCH_THRESHOLD):
            score = catalog.score(value, concept)
            rec = _RECORDER.get()
            if rec is not None:
                rec.append(
                    {"concept": str(concept).strip().upper(),
                     "score": float(score), "threshold": float(threshold)}
                )
            return score >= threshold

        return {
            "sim": sim,
            "similar": similar,
            "reject": _reject,
            "flag": _flag,
            "notify": _notify,
        }

    # -- loading -----------------------------------------------------------

    def load(self, dsl: str) -> "RulesEngine":
        """Parse a DSL rule set and bind this engine's functions to every rule."""
        from business_rule_engine import RuleParser

        parser = RuleParser()
        parser.parsestr(dsl)
        rules = list(parser.rules.values())
        # Lint: business_rule_engine silently parses `when <cond>` (condition on
        # the same line as `when`) as an EMPTY condition — no parse error, but it
        # blows up at eval. Catch that authoring mistake here, loudly.
        empty = [getattr(r, "rulename", "?") for r in rules if not self._conditions_text(r).strip()]
        if empty:
            raise ValueError(
                f"Rule(s) {empty} have an empty condition. Put `when` and `then` on "
                f"their own lines, with the condition/action indented beneath them."
            )
        # Give each rule THIS engine's functions dict (not the shared class-level
        # one) so workspaces stay isolated.
        for rule in rules:
            rule._functions = self._funcs
        rules.sort(key=lambda r: getattr(r, "priority", 0), reverse=True)
        self._rules = rules
        self._threshold_cache.clear()
        logger.info(f"RulesEngine loaded {len(rules)} rule(s)")
        return self

    @staticmethod
    def _conditions_text(rule) -> str:
        conditions = getattr(rule, "conditions", "") or ""
        if isinstance(conditions, (list, tuple)):
            conditions = " ".join(str(c) for c in conditions)
        return str(conditions)

    def rule_names(self) -> List[str]:
        return [getattr(r, "rulename", "?") for r in self._rules]

    def __len__(self) -> int:
        return len(self._rules)

    # -- evaluation --------------------------------------------------------

    def evaluate(self, params: Dict[str, Any]) -> EvaluationResult:
        """Evaluate all enabled rules against ``params`` and gate the outcome.

        Rules run in priority order, each in isolation; a rule that raises
        (missing numeric, unknown concept, …) is skipped with a warning so one
        bad rule cannot break the gate.
        """
        from context_graph.rules.similarity import SimilarityUnavailable

        triggered: List[RuleMatch] = []
        warnings: List[str] = []
        similarity_down = False  # collapse the infra failure into ONE honest signal

        for rule in self._rules:
            if not getattr(rule, "enabled", True):
                continue
            token = _RECORDER.set([])
            try:
                fired, action_results = rule.execute(params)
            except SimilarityUnavailable:
                # Infrastructure failure, not a reuse signal: don't leak the raw
                # ImportError (with pip advice) as a per-rule warning that reads
                # like "new module — confirm reuse". Report it once, plainly.
                similarity_down = True
                fired, action_results = False, []
            except Exception as e:  # fail-open: skip this rule, keep the gate alive
                warnings.append(f"{getattr(rule, 'rulename', '?')}: {e}")
                fired, action_results = False, []
            finally:
                calls = _RECORDER.get() or []
                _RECORDER.reset(token)
            if fired:
                triggered.append(self._build_match(rule, action_results, calls))

        if similarity_down:
            warnings.append(
                "similarity check unavailable — sim()/similar() rules were skipped "
                "(model2vec not installed or model weights unreachable)"
            )

        return self._result(triggered, warnings)

    # -- internals ---------------------------------------------------------

    def _build_match(self, rule, action_results, calls) -> RuleMatch:
        actions = [a for a in (action_results or []) if isinstance(a, Action)]
        thresholds = self._thresholds_for(rule)
        for c in calls:
            if c["threshold"] is None:
                c["threshold"] = thresholds.get(c["concept"])
        return RuleMatch(
            rule=getattr(rule, "rulename", "?"),
            actions=actions,
            matches=calls,
            priority=getattr(rule, "priority", 0),
        )

    def _thresholds_for(self, rule) -> Dict[str, float]:
        """Recover ``concept -> threshold`` from a rule's condition text (cached)."""
        name = getattr(rule, "rulename", "?")
        if name in self._threshold_cache:
            return self._threshold_cache[name]
        conditions = self._conditions_text(rule)
        out: Dict[str, float] = {}
        for m in _SIM_CMP_RE.finditer(conditions):
            try:
                out[m.group("concept").strip().upper()] = float(m.group("thr"))
            except ValueError:
                continue
        self._threshold_cache[name] = out
        return out

    def _result(self, triggered: List[RuleMatch], warnings: List[str]) -> EvaluationResult:
        severity = max((m.severity for m in triggered), default=0)
        notes = [a.reason for m in triggered for a in m.actions if a.kind == "NOTIFY"]
        return EvaluationResult(
            outcome=_OUTCOME_BY_SEVERITY[severity],
            triggered=triggered,
            notes=notes,
            warnings=warnings,
            model_id=self.model_id,
        )
