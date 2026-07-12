"""App bundle — the Stage-2 composition artifact (P0).

An :class:`AppBundle` names the signed blocks (rules / flows / actions) an app
composes, pins the meta-DSL and ontology versions it was built against
(decision 4/6), and carries connector bindings + views. It is the unit that
export/import and migration operate on. See docs/PLATFORM_ARCHITECTURE.html.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class AppBundle:
    domain: str                          # workspace
    project: str
    app_id: str
    meta_dsl_version: str = "0"          # pins L0
    ontology_version: int = 0            # pins L1 ontology
    rule_ids: List[str] = field(default_factory=list)
    flow_ids: List[str] = field(default_factory=list)
    action_ids: List[str] = field(default_factory=list)
    connector_bindings: List[Dict[str, Any]] = field(default_factory=list)
    views: List[Dict[str, Any]] = field(default_factory=list)
    signed_by: Optional[str] = None
    signed_at: Optional[str] = None

    @property
    def signed(self) -> bool:
        return bool(self.signed_by and self.signed_at)

    def lint(self) -> List[str]:
        problems: List[str] = []
        if not self.app_id:
            problems.append("app_id is required")
        if not self.domain:
            problems.append("domain is required")
        if not self.project:
            problems.append("project is required")
        if not self.flow_ids:
            problems.append("an app needs at least one flow")
        return problems

    def to_dict(self) -> Dict[str, Any]:
        return {
            "domain": self.domain,
            "project": self.project,
            "app_id": self.app_id,
            "meta_dsl_version": self.meta_dsl_version,
            "ontology_version": self.ontology_version,
            "rule_ids": self.rule_ids,
            "flow_ids": self.flow_ids,
            "action_ids": self.action_ids,
            "connector_bindings": self.connector_bindings,
            "views": self.views,
            "signed_by": self.signed_by,
            "signed_at": self.signed_at,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "AppBundle":
        return cls(
            domain=d.get("domain", ""),
            project=d.get("project", ""),
            app_id=d.get("app_id", ""),
            meta_dsl_version=str(d.get("meta_dsl_version", "0")),
            ontology_version=int(d.get("ontology_version", 0)),
            rule_ids=list(d.get("rule_ids", [])),
            flow_ids=list(d.get("flow_ids", [])),
            action_ids=list(d.get("action_ids", [])),
            connector_bindings=list(d.get("connector_bindings", [])),
            views=list(d.get("views", [])),
            signed_by=d.get("signed_by"),
            signed_at=d.get("signed_at"),
        )
