"""Context Graph RBAC — per-workspace object-level access control (P3, Gap 1).

Opt-in and deny-by-default within a policy; a workspace with no policy is
permissive (single-agent stays zero-config). The principal is the authenticated
identity's role, resolved server-side — never a client-supplied field. Enforced
as a pre-check on ``/actions/invoke`` (before the rules gate). See
``docs/RBAC_SPEC.md``.

    from context_graph.rbac import RbacPolicy, RbacService, JsonRbacStore

    pol = RbacPolicy.from_dict({"roles": {"manager": ["*"],
                                          "developer": ["invoke:ProposeAPI"]}})
    pol.check("developer", "invoke", "ProposeAPI").allowed   # True
    pol.check("developer", "invoke", "MergeToMain").allowed  # False
"""

from context_graph.rbac.schema import (
    Grant,
    RebacGrant,
    RoleGrants,
    RbacPolicy,
    Decision,
)
from context_graph.rbac.store import (
    RbacStore,
    JsonRbacStore,
    InMemoryRbacStore,
    validate_policy,
)
from context_graph.rbac.service import RbacService

__all__ = [
    "Grant",
    "RebacGrant",
    "RoleGrants",
    "RbacPolicy",
    "Decision",
    "RbacStore",
    "JsonRbacStore",
    "InMemoryRbacStore",
    "validate_policy",
    "RbacService",
]
