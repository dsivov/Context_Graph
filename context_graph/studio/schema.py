"""Artifact diff — the one authoring gesture (P0).

Authoring, migration, and re-approval all produce an :class:`ArtifactDiff` and
run it through the same ``DiffEngine`` (decisions 4/5/7); only ``origin`` differs.
``behaviour_changed`` (set by ``DiffEngine.assess`` via dry-run / test_cases)
decides whether full sign-off is required or a lightweight approval suffices. See
docs/PLATFORM_ARCHITECTURE.html.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

DIFF_KINDS = ("ontology", "rule", "flow", "action", "app")
DIFF_ORIGINS = ("authoring", "migration", "reapproval")


@dataclass
class ArtifactDiff:
    kind: str                            # one of DIFF_KINDS
    artifact_id: str
    to_version: int
    from_version: Optional[int] = None
    delta: Dict[str, Any] = field(default_factory=dict)
    behaviour_changed: bool = False
    origin: str = "authoring"            # one of DIFF_ORIGINS

    def lint(self) -> list[str]:
        problems: list[str] = []
        if self.kind not in DIFF_KINDS:
            problems.append(f"unknown kind '{self.kind}'")
        if self.origin not in DIFF_ORIGINS:
            problems.append(f"unknown origin '{self.origin}'")
        if not self.artifact_id:
            problems.append("artifact_id is required")
        return problems

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "artifact_id": self.artifact_id,
            "to_version": self.to_version,
            "from_version": self.from_version,
            "delta": self.delta,
            "behaviour_changed": self.behaviour_changed,
            "origin": self.origin,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ArtifactDiff":
        return cls(
            kind=d.get("kind", ""),
            artifact_id=d.get("artifact_id", ""),
            to_version=int(d.get("to_version", 1)),
            from_version=(None if d.get("from_version") is None else int(d["from_version"])),
            delta=dict(d.get("delta") or {}),
            behaviour_changed=bool(d.get("behaviour_changed", False)),
            origin=d.get("origin", "authoring"),
        )
