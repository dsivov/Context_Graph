"""Action Layer schema — executable operations bound to object types (P3).

An *action* is a named, typed operation an agent or application can invoke
against an ontology object type — ``ApproveOrder``, ``CancelShipment``. Invoking
it validates typed arguments (reusing the ontology's coercing :class:`Property`),
runs an optional side-effecting handler, and records the execution as a decision
trace via ``ContextGraph.emit_decision_trace`` — so every action flows through
the business-rules gate and leaves an audit edge on the graph.

This is the first slice of the P3 "action / kinetic layer" documented in
``docs/CLOSING_THE_GAPS.html`` §08. It depends on P2 (object types) and builds on
P1 (the rules gate).

    from context_graph.actions import ActionCatalog, ActionDefinition, ActionParam

    cat = ActionCatalog(name="sales").define(
        ActionDefinition("ApproveOrder", object_type="Order", relation_type="APPROVED")
            .add(ActionParam("discount", kind="percent", required=True))
    )
    coerced, errors = cat.get("ApproveOrder").validate_args({"discount": "20%"})
    #  → coerced == {"discount": 0.20}, errors == []
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from context_graph.ontology.schema import Property, PropertyKind

_HANDLER_KINDS = ("none", "webhook")


@dataclass
class ActionParam:
    """A typed argument of an action. Mirrors an ontology :class:`Property` and
    validates identically (coerce + enum/range check)."""

    name: str
    kind: str = PropertyKind.STRING.value
    required: bool = False
    description: str = ""
    enum_values: Optional[List[str]] = None
    minimum: Optional[float] = None
    maximum: Optional[float] = None

    def __post_init__(self) -> None:
        # Validate the kind eagerly; full enum/range constraints are enforced
        # when a Property is built in ``to_property``.
        PropertyKind(self.kind)

    def to_property(self) -> Property:
        """Build the ontology Property used to coerce/validate a value."""
        return Property.from_dict(self.to_dict())

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"name": self.name, "kind": self.kind, "required": self.required}
        if self.description:
            d["description"] = self.description
        if self.enum_values is not None:
            d["enum_values"] = list(self.enum_values)
        if self.minimum is not None:
            d["minimum"] = self.minimum
        if self.maximum is not None:
            d["maximum"] = self.maximum
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ActionParam":
        return cls(
            name=d["name"],
            kind=d.get("kind", PropertyKind.STRING.value),
            required=bool(d.get("required", False)),
            description=d.get("description", ""),
            enum_values=list(d["enum_values"]) if d.get("enum_values") is not None else None,
            minimum=d.get("minimum"),
            maximum=d.get("maximum"),
        )


@dataclass
class ActionHandler:
    """How an action's side effect executes. ``none`` records the action only
    (the audited decision trace *is* the effect); ``webhook`` POSTs the payload
    to a URL (SSRF-guarded at invoke time)."""

    kind: str = "none"                # none | webhook
    url: Optional[str] = None         # required for webhook
    method: str = "POST"
    allow_internal: bool = False      # allow webhooks to private/loopback hosts

    def __post_init__(self) -> None:
        if self.kind not in _HANDLER_KINDS:
            raise ValueError(f"unknown handler kind '{self.kind}' (expected one of {_HANDLER_KINDS})")
        if self.kind == "webhook" and not self.url:
            raise ValueError("webhook handler requires a 'url'")

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"kind": self.kind}
        if self.url:
            d["url"] = self.url
        if self.method != "POST":
            d["method"] = self.method
        if self.allow_internal:
            d["allow_internal"] = True
        return d

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> "ActionHandler":
        if not d:
            return cls()
        return cls(
            kind=d.get("kind", "none"),
            url=d.get("url"),
            method=d.get("method", "POST"),
            allow_internal=bool(d.get("allow_internal", False)),
        )


@dataclass
class ActionTransition:
    """Marks an action as a lifecycle transition of its ``object_type``.

    ``to_param`` names the argument holding the target state. When set, invoking
    the action is checked against the workspace lifecycle (is ``from → to`` legal
    for this role?) and, on success, the object's state is advanced.
    """

    to_param: str

    def to_dict(self) -> Dict[str, Any]:
        return {"to_param": self.to_param}

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> Optional["ActionTransition"]:
        if not d:
            return None
        return cls(to_param=d["to_param"])


@dataclass
class ActionDefinition:
    """A named, typed operation bound to an object type."""

    name: str
    object_type: str = ""             # ontology object type acted upon ("" = any)
    relation_type: str = ""           # edge keyword written on invoke (default: NAME upper)
    description: str = ""
    effect: str = ""                  # human label of the side effect
    policy_ref: Optional[str] = None
    params: Dict[str, ActionParam] = field(default_factory=dict)  # insertion-ordered
    handler: ActionHandler = field(default_factory=ActionHandler)
    transition: Optional[ActionTransition] = None   # lifecycle transition, if any

    def add(self, param: ActionParam) -> "ActionDefinition":
        self.params[param.name] = param
        return self

    @property
    def edge_relation(self) -> str:
        """The relationship keyword written to the graph edge on invoke."""
        return self.relation_type or self.name.upper()

    def validate_args(self, args: Optional[Dict[str, Any]]) -> Tuple[Dict[str, Any], List[str]]:
        """Coerce and check provided args against the typed params.

        Returns ``(coerced, errors)``. Required params that are missing, and
        values that fail coercion/enum/range checks, appear in ``errors``.
        Arguments not declared on the action are ignored (kept out of ``coerced``).
        """
        args = args or {}
        coerced: Dict[str, Any] = {}
        errors: List[str] = []
        for name, param in self.params.items():
            if name not in args or args[name] is None:
                if param.required:
                    errors.append(f"missing required argument '{name}'")
                continue
            try:
                coerced[name] = param.to_property().validate(args[name])
            except ValueError as e:
                errors.append(f"{name}: {e}")
        return coerced, errors

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"name": self.name}
        if self.object_type:
            d["object_type"] = self.object_type
        if self.relation_type:
            d["relation_type"] = self.relation_type
        if self.description:
            d["description"] = self.description
        if self.effect:
            d["effect"] = self.effect
        if self.policy_ref:
            d["policy_ref"] = self.policy_ref
        d["params"] = [p.to_dict() for p in self.params.values()]
        d["handler"] = self.handler.to_dict()
        if self.transition is not None:
            d["transition"] = self.transition.to_dict()
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ActionDefinition":
        params: Dict[str, ActionParam] = {}
        for pd in d.get("params", []):
            p = ActionParam.from_dict(pd)
            params[p.name] = p
        return cls(
            name=d["name"],
            object_type=d.get("object_type", ""),
            relation_type=d.get("relation_type", ""),
            description=d.get("description", ""),
            effect=d.get("effect", ""),
            policy_ref=d.get("policy_ref"),
            params=params,
            handler=ActionHandler.from_dict(d.get("handler")),
            transition=ActionTransition.from_dict(d.get("transition")),
        )


@dataclass
class ActionCatalog:
    """A workspace's set of actions (the persisted unit, versioned by the store)."""

    name: str = "actions"
    version: int = 1
    actions: Dict[str, ActionDefinition] = field(default_factory=dict)

    def define(self, action: ActionDefinition) -> "ActionCatalog":
        self.actions[action.name] = action
        return self

    def get(self, name: str) -> Optional[ActionDefinition]:
        return self.actions.get(name)

    def lint(self) -> List[str]:
        """Return human-readable problems, or an empty list if self-consistent.

        Handler and param-kind constraints are enforced at construction; this
        catches only what can't be caught there.
        """
        problems: List[str] = []
        for a in self.actions.values():
            if not a.edge_relation:
                problems.append(f"action '{a.name}' resolves to an empty relation_type")
        return problems

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "actions": [a.to_dict() for a in self.actions.values()],
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ActionCatalog":
        cat = cls(name=d.get("name", "actions"), version=int(d.get("version", 1)))
        for ad in d.get("actions", []):
            a = ActionDefinition.from_dict(ad)
            cat.actions[a.name] = a
        return cat
