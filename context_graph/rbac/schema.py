"""RBAC schema — per-workspace role-based access control (P3, Gap 1).

See ``docs/RBAC_SPEC.md``. Opt-in and deny-by-default *within* a policy: a
workspace with no policy is permissive; a workspace with a policy allows only
granted operations. The principal (role) is the authenticated identity, never a
client-supplied field.

Grant grammar — a wildcard-friendly string ``"<verb>:<target>"`` or ``"*"``:

* ``verb``   ∈ ``invoke | create | update | delete | read | *``
* ``target`` = an action name, an object-type name, or ``*``
* ``"*"`` alone = superuser (e.g. the central ``manager`` role).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

_VERBS = ("invoke", "create", "update", "delete", "read")


@dataclass
class Decision:
    """The outcome of an access check."""

    allowed: bool
    reason: str


@dataclass
class Grant:
    """A static permission: a verb on a target (both wildcardable)."""

    verb: str
    target: str = "*"

    def __post_init__(self) -> None:
        if self.verb not in _VERBS and self.verb != "*":
            raise ValueError(
                f"unknown grant verb '{self.verb}' (expected one of {_VERBS} or '*')")

    @classmethod
    def parse(cls, s: str) -> "Grant":
        s = s.strip()
        if s == "*":
            return cls("*", "*")
        verb, sep, target = s.partition(":")
        target = target.strip() if sep else "*"
        return cls(verb.strip(), target or "*")

    def matches(self, verb: str, target: str) -> bool:
        return self.verb in ("*", verb) and self.target in ("*", target)

    def to_str(self) -> str:
        return "*" if (self.verb == "*" and self.target == "*") else f"{self.verb}:{self.target}"


@dataclass
class RebacGrant:
    """Relationship-derived grant (phase 2 — parsed and preserved, not yet enforced).

    "May ``verb`` ``target`` on a ``of``-typed object reachable from the
    principal's Role node along the ``via`` edge."
    """

    via: str
    of: str
    verb: str = "*"
    target: str = "*"

    def to_dict(self) -> Dict[str, Any]:
        return {"verb": self.verb, "target": self.target, "via": self.via, "of": self.of}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "RebacGrant":
        return cls(via=d["via"], of=d["of"],
                   verb=d.get("verb", "*"), target=d.get("target", "*"))


@dataclass
class RoleGrants:
    grants: List[Grant] = field(default_factory=list)
    rebac: List[RebacGrant] = field(default_factory=list)

    def allows(self, verb: str, target: str) -> bool:
        return any(g.matches(verb, target) for g in self.grants)

    @classmethod
    def from_value(cls, value: Any) -> "RoleGrants":
        """Accept a list of grant-strings (and/or rebac dicts), or
        ``{"grants": [...], "rebac": [...]}``."""
        grants: List[Grant] = []
        rebac: List[RebacGrant] = []
        items = value if isinstance(value, list) else (value or {}).get("grants", [])
        for it in items:
            if isinstance(it, str):
                grants.append(Grant.parse(it))
            elif isinstance(it, dict) and "via" in it:
                rebac.append(RebacGrant.from_dict(it))
            elif isinstance(it, dict):
                grants.append(Grant(it["verb"], it.get("target", "*")))
        if isinstance(value, dict):
            for it in value.get("rebac", []):
                rebac.append(RebacGrant.from_dict(it))
        return cls(grants=grants, rebac=rebac)


@dataclass
class RbacPolicy:
    """A workspace's role → grants policy (the persisted, versioned unit)."""

    name: str = "rbac"
    version: int = 1
    default_deny: bool = True
    roles: Dict[str, RoleGrants] = field(default_factory=dict)

    def check(self, role: Optional[str], verb: str, target: str) -> Decision:
        """Static (non-ReBAC) decision. Deny-by-default within the policy."""
        if role is None:
            return Decision(False, "no authenticated role for an RBAC-enabled workspace")
        rg = self.roles.get(role)
        if rg is None:
            return Decision(False, f"role '{role}' has no grants")
        if rg.allows(verb, target):
            return Decision(True, "granted")
        return Decision(False, f"role '{role}' lacks {verb}:{target}")

    def lint(self) -> List[str]:
        problems: List[str] = []
        if not self.roles:
            problems.append("policy defines no roles")
        return problems

    def to_dict(self) -> Dict[str, Any]:
        roles: Dict[str, Any] = {}
        for name, rg in self.roles.items():
            if rg.rebac:
                roles[name] = {"grants": [g.to_str() for g in rg.grants],
                               "rebac": [r.to_dict() for r in rg.rebac]}
            else:
                roles[name] = [g.to_str() for g in rg.grants]
        return {"name": self.name, "version": self.version,
                "default_deny": self.default_deny, "roles": roles}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "RbacPolicy":
        pol = cls(name=d.get("name", "rbac"), version=int(d.get("version", 1)),
                  default_deny=bool(d.get("default_deny", True)))
        for name, value in (d.get("roles") or {}).items():
            pol.roles[name] = RoleGrants.from_value(value)
        return pol
