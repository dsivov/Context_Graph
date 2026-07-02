"""Lifecycle schema — declarative state machines over object types (P3, Gap 2).

See ``docs/LIFECYCLE_SPEC.md``. A thin FSM per object type: legal states and the
declared transitions between them (optionally role-restricted). No machine for a
type → permissive; an undeclared ``from → to`` is rejected.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Decision:
    allowed: bool
    reason: str


@dataclass
class Transition:
    from_state: str
    to: str
    roles: List[str] = field(default_factory=list)      # empty = any role
    requires: List[str] = field(default_factory=list)   # cross-object guards (phase 2)

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"from": self.from_state, "to": self.to}
        if self.roles:
            d["roles"] = list(self.roles)
        if self.requires:
            d["requires"] = list(self.requires)
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Transition":
        return cls(from_state=d["from"], to=d["to"],
                   roles=list(d.get("roles", [])), requires=list(d.get("requires", [])))


@dataclass
class StateMachine:
    object_type: str
    prop: str = "state"            # the node property that holds the current state
    states: List[str] = field(default_factory=list)
    initial: str = ""
    transitions: List[Transition] = field(default_factory=list)

    def is_state(self, s: str) -> bool:
        return s in self.states

    def can(self, from_state: str, to: str, role: Optional[str]) -> Decision:
        if not self.is_state(to):
            return Decision(False, f"'{to}' is not a state of {self.object_type}")
        for t in self.transitions:
            if t.from_state == from_state and t.to == to:
                if t.roles and (role is None or role not in t.roles):
                    return Decision(False,
                                    f"{self.object_type} {from_state}→{to} requires role in {t.roles}")
                return Decision(True, "legal transition")
        return Decision(False, f"{self.object_type} has no transition {from_state}→{to}")

    def lint(self) -> List[str]:
        problems: List[str] = []
        if not self.states:
            problems.append(f"{self.object_type}: no states")
        if self.initial and self.initial not in self.states:
            problems.append(f"{self.object_type}: initial '{self.initial}' not in states")
        for t in self.transitions:
            for s in (t.from_state, t.to):
                if s not in self.states:
                    problems.append(f"{self.object_type}: transition references unknown state '{s}'")
        return problems

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"states": list(self.states), "initial": self.initial,
                             "transitions": [t.to_dict() for t in self.transitions]}
        if self.prop != "state":
            d["prop"] = self.prop
        return d

    @classmethod
    def from_dict(cls, object_type: str, d: Dict[str, Any]) -> "StateMachine":
        return cls(
            object_type=object_type,
            prop=d.get("prop", "state"),
            states=list(d.get("states", [])),
            initial=d.get("initial", ""),
            transitions=[Transition.from_dict(t) for t in d.get("transitions", [])],
        )


@dataclass
class Lifecycle:
    name: str = "lifecycle"
    version: int = 1
    machines: Dict[str, StateMachine] = field(default_factory=dict)

    def machine_for(self, object_type: str) -> Optional[StateMachine]:
        return self.machines.get(object_type)

    def check(self, object_type: str, from_state: str, to: str,
              role: Optional[str] = None) -> Decision:
        m = self.machine_for(object_type)
        if m is None:
            return Decision(True, f"no state machine for {object_type}")
        return m.can(from_state, to, role)

    def lint(self) -> List[str]:
        problems: List[str] = []
        if not self.machines:
            problems.append("lifecycle defines no state machines")
        for m in self.machines.values():
            problems.extend(m.lint())
        return problems

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "version": self.version,
                "machines": {ot: m.to_dict() for ot, m in self.machines.items()}}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Lifecycle":
        lc = cls(name=d.get("name", "lifecycle"), version=int(d.get("version", 1)))
        for ot, md in (d.get("machines") or {}).items():
            lc.machines[ot] = StateMachine.from_dict(ot, md)
        return lc
