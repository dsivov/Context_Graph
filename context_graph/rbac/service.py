"""RbacService — glue between the RBAC store and the ``/rbac`` API / enforcement.

The load-bearing invariant lives in :meth:`check`: **no policy → permissive**
(single-agent stays zero-config), **policy present → deny-by-default**.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from context_graph.rbac.schema import Decision, RbacPolicy
from context_graph.rbac.store import RbacStore


class RbacService:
    def __init__(self, store: RbacStore) -> None:
        self._store = store

    @property
    def store(self) -> RbacStore:
        return self._store

    # -- queries -----------------------------------------------------------

    def get_summary(self, workspace: str) -> Dict[str, Any]:
        pol = self._store.load(workspace)
        if pol is None:
            return {"workspace": workspace, "exists": False, "roles": {}}
        meta = self._store.meta(workspace) or {}
        return {
            "workspace": workspace,
            "exists": True,
            "name": pol.name,
            "version": pol.version,
            "updated_at": meta.get("updated_at"),
            "roles": {n: [g.to_str() for g in rg.grants] for n, rg in pol.roles.items()},
        }

    # -- mutations ---------------------------------------------------------

    def save(self, workspace: str, policy_dict: Dict[str, Any]) -> RbacPolicy:
        """Deserialize + persist. Raises ValueError on a malformed/invalid policy."""
        pol = RbacPolicy.from_dict(policy_dict)
        return self._store.save(workspace, pol)

    def delete(self, workspace: str) -> bool:
        return self._store.delete(workspace)

    # -- enforcement -------------------------------------------------------

    def check(self, workspace: str, role: Optional[str], verb: str, target: str,
              *, object_ref: Optional[str] = None, rag: Any = None) -> Decision:
        """Decide whether *role* may *verb* *target* in *workspace*.

        No policy → permissive. Otherwise deny-by-default via the static grants.
        (ReBAC / relationship-derived grants are phase 2 — parsed and stored, not
        yet enforced here.)
        """
        pol = self._store.load(workspace)
        if pol is None:
            return Decision(True, "no RBAC policy — permissive")
        return pol.check(role, verb, target)
