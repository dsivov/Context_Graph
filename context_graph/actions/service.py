"""ActionService — glue between the action store, the rules gate, and the API.

Holds a per-workspace :class:`~context_graph.actions.store.ActionStore` and
offers what the router and server need:

* ``get_summary(ws)``  — metadata + action overview + lint,
* ``get_action(ws, n)``— one action's full definition,
* ``save`` / ``delete``— persist / remove (validated on save),
* ``invoke(...)``      — validate args, **authorize + record via the rules
                          gate** (``emit_decision_trace``), then run the side
                          effect. The gate runs first, so a REJECTed action
                          never fires its handler.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from lightrag.context_graph_types import RelationContext

from context_graph.actions.schema import ActionCatalog, ActionDefinition
from context_graph.actions.store import ActionStore


class ActionService:
    def __init__(self, store: ActionStore) -> None:
        self._store = store

    @property
    def store(self) -> ActionStore:
        return self._store

    # -- queries -----------------------------------------------------------

    def get_summary(self, workspace: str) -> Dict[str, Any]:
        cat = self._store.load(workspace)
        if cat is None:
            return {"workspace": workspace, "exists": False, "actions": []}
        meta = self._store.meta(workspace) or {}
        return {
            "workspace": workspace,
            "exists": True,
            "name": cat.name,
            "version": cat.version,
            "updated_at": meta.get("updated_at"),
            "actions": [self._action_info(a) for a in cat.actions.values()],
            "lint": cat.lint(),
            "catalog": cat.to_dict(),
        }

    def get_action(self, workspace: str, name: str) -> Optional[Dict[str, Any]]:
        cat = self._store.load(workspace)
        if cat is None:
            return None
        action = cat.get(name)
        return action.to_dict() if action is not None else None

    @staticmethod
    def _action_info(a: ActionDefinition) -> Dict[str, Any]:
        return {
            "name": a.name,
            "object_type": a.object_type,
            "relation_type": a.edge_relation,
            "description": a.description,
            "effect": a.effect,
            "handler": a.handler.kind,
            "params": [{"name": p.name, "kind": p.kind, "required": p.required}
                       for p in a.params.values()],
        }

    # -- mutations ---------------------------------------------------------

    def save(self, workspace: str, catalog_dict: Dict[str, Any]) -> ActionCatalog:
        """Deserialize + persist. Raises ValueError on a malformed catalog
        (bad shape, invalid handler/param kind, or failed lint)."""
        cat = ActionCatalog.from_dict(catalog_dict)   # KeyError/ValueError on bad shape
        return self._store.save(workspace, cat)       # validate_catalog + version bump

    def delete(self, workspace: str) -> bool:
        return self._store.delete(workspace)

    # -- invocation --------------------------------------------------------

    async def invoke(self, rag, workspace: str, action_name: str, *,
                     actor: str = "system", object_ref: str,
                     args: Optional[Dict[str, Any]] = None,
                     principal_role: Optional[str] = None,
                     lifecycle=None) -> Dict[str, Any]:
        """Run an action end to end.

        Order matters: arguments are validated, then the rules gate authorizes
        and records the decision (``emit_decision_trace``), and only on
        PASS/FLAG does the side-effecting handler run. A REJECTed action leaves
        nothing persisted and never fires its handler.

        Returns a JSON-serializable result. ``ok`` is False for an unknown
        action, invalid arguments, or a gate REJECT; the caller maps those to
        the appropriate HTTP status.
        """
        cat = self._store.load(workspace)
        action = cat.get(action_name) if cat is not None else None
        if action is None:
            return {"ok": False, "error": "unknown_action", "action": action_name}

        coerced, errors = action.validate_args(args)
        if errors:
            return {"ok": False, "error": "invalid_arguments", "errors": errors,
                    "action": action_name}

        src = actor or "system"
        tgt = object_ref
        relation_type = action.edge_relation
        rc = self._build_rc(action, coerced, actor=src)
        edge = {"src": src, "tgt": tgt, "relation_type": relation_type}

        # 1) Lifecycle guard (for transition actions): is from → to a legal move
        #    for this role? Runs before the write; illegal transitions never persist.
        machine = None
        current = target = None
        if action.transition is not None and lifecycle is not None:
            machine = lifecycle.machine_for(workspace, action.object_type)
            if machine is not None:
                target = coerced.get(action.transition.to_param)
                current = await lifecycle.current_state(rag, tgt, machine)
                d = machine.can(current, target, principal_role)
                if not d.allowed:
                    return {"ok": False, "error": "illegal_transition", "reason": d.reason,
                            "action": action.name, "from": current, "to": target, "edge": edge}

        # 2) Authorize + record via the pre-emit rules gate (writes the audit edge).
        from context_graph.rules.gate import RuleViolation
        try:
            gate_decision = await rag.emit_decision_trace(src, tgt, relation_type, rc)
        except RuleViolation as e:
            return {"ok": False, "error": "rejected", "outcome": "REJECT",
                    "action": action.name, "edge": edge, "audit": e.decision.audit}

        outcome = gate_decision.outcome if gate_decision is not None else "RECORDED"
        audit = gate_decision.audit if gate_decision is not None else None

        # 3) Apply the state transition on the object's node (after a PASS/FLAG emit).
        if machine is not None and target is not None:
            await lifecycle.apply(rag, tgt, machine, target)

        # 4) Side effect only after authorization.
        from context_graph.actions.handler import run_handler, HandlerError
        payload = {"action": action.name, "actor": src, "object": tgt,
                   "relation_type": relation_type, "args": coerced,
                   "outcome": outcome, "workspace": workspace}
        try:
            handler_result = await run_handler(action.handler, payload)
        except HandlerError as e:
            handler_result = {"kind": action.handler.kind, "executed": False, "error": str(e)}

        result = {"ok": True, "action": action.name, "outcome": outcome,
                  "flagged": outcome == "FLAG", "edge": edge, "coerced": coerced,
                  "audit": audit, "handler": handler_result}
        if machine is not None and target is not None:
            result["from"], result["to"] = current, target
        return result

    @staticmethod
    def _build_rc(action: ActionDefinition, coerced: Dict[str, Any], *, actor: str) -> RelationContext:
        """Build the RelationContext recorded for an action execution.

        Money/percent params are rendered into ``quantitative_data`` as human
        text (``"discount 20%"``) so the rules gate's ``amount``/``percent``
        projection (which parses that field) can reason over action arguments.
        """
        quant_bits: List[str] = []
        for name, param in action.params.items():
            if name not in coerced:
                continue
            val = coerced[name]
            if param.kind == "percent":
                quant_bits.append(f"{name} {val * 100:g}%")
            elif param.kind == "money":
                quant_bits.append(f"{name} ${val:g}")

        arg_summary = ", ".join(f"{k}={v}" for k, v in coerced.items())
        trace = f"Executed action {action.name}"
        if arg_summary:
            trace += f"({arg_summary})"
        if action.effect:
            trace += f" — {action.effect}"

        return RelationContext(
            decision_trace=trace,
            approved_by=actor,
            approved_via="system",
            provenance=f"action:{action.name}",
            quantitative_data="; ".join(quant_bits) or None,
            policy_ref=action.policy_ref,
            confidence_score=1.0,
        )
