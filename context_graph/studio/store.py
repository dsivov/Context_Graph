"""Studio history — the versioned artifact ledger behind diff-and-approve (P3).

The Studio is the one authoring gesture (propose → assess → apply) over four
artifact kinds (ontology, rule, flow, action). The underlying stores mostly keep
only the *current* version (they bump a number and overwrite), so the Studio
keeps its own append-only ledger: for every applied diff it records an
:class:`ArtifactVersion` holding the full applied snapshot plus the sign-off
(who / why / when) and the decision audit. That ledger is what powers history and
``revert`` uniformly across every kind — independent of whether the underlying
store retained the old bytes. See docs/PLATFORM_ARCHITECTURE.html (decisions 4/5/7).
"""

from __future__ import annotations

import json
import os
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from lightrag.utils import logger

_WS_SANITIZE_RE = re.compile(r"[^A-Za-z0-9_]")


@dataclass
class SignOff:
    """Who approved a change, why, and when (an ISO-8601 timestamp supplied by
    the caller — the store takes no clock)."""

    approver: str
    reason: str
    at: str
    role: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {"approver": self.approver, "reason": self.reason,
                "at": self.at, "role": self.role}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "SignOff":
        return cls(approver=d.get("approver", ""), reason=d.get("reason", ""),
                   at=d.get("at", ""), role=d.get("role"))


@dataclass
class ArtifactVersion:
    """One applied revision of an artifact, as recorded in the Studio ledger."""

    kind: str
    artifact_id: str
    version: int
    snapshot: Dict[str, Any]                 # the full applied artifact ("after")
    from_version: Optional[int] = None
    behaviour_changed: bool = False
    origin: str = "authoring"
    sign_off: Optional[SignOff] = None
    decision_audit: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "artifact_id": self.artifact_id,
            "version": self.version,
            "snapshot": self.snapshot,
            "from_version": self.from_version,
            "behaviour_changed": self.behaviour_changed,
            "origin": self.origin,
            "sign_off": self.sign_off.to_dict() if self.sign_off else None,
            "decision_audit": self.decision_audit,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ArtifactVersion":
        so = d.get("sign_off")
        return cls(
            kind=d.get("kind", ""),
            artifact_id=d.get("artifact_id", ""),
            version=int(d.get("version", 1)),
            snapshot=dict(d.get("snapshot") or {}),
            from_version=(None if d.get("from_version") is None else int(d["from_version"])),
            behaviour_changed=bool(d.get("behaviour_changed", False)),
            origin=d.get("origin", "authoring"),
            sign_off=SignOff.from_dict(so) if so else None,
            decision_audit=d.get("decision_audit"),
        )


def _key(kind: str, artifact_id: str) -> str:
    return f"{kind}:{artifact_id}"


class StudioStore(ABC):
    """Append-only per-``(workspace, kind, artifact_id)`` version history."""

    def record(self, workspace: str, version: ArtifactVersion) -> None:
        data = self._read(workspace)
        data.setdefault(_key(version.kind, version.artifact_id), []).append(version.to_dict())
        self._write(workspace, data)

    def history(self, workspace: str, kind: str, artifact_id: str) -> List[ArtifactVersion]:
        raw = self._read(workspace).get(_key(kind, artifact_id), [])
        return [ArtifactVersion.from_dict(d) for d in raw]

    def latest(self, workspace: str, kind: str, artifact_id: str) -> Optional[ArtifactVersion]:
        hist = self.history(workspace, kind, artifact_id)
        return hist[-1] if hist else None

    def get(self, workspace: str, kind: str, artifact_id: str,
            version: int) -> Optional[ArtifactVersion]:
        for v in self.history(workspace, kind, artifact_id):
            if v.version == version:
                return v
        return None

    def artifacts(self, workspace: str) -> List[Dict[str, Any]]:
        """One row per tracked artifact: kind, id, latest version, count."""
        out: List[Dict[str, Any]] = []
        for versions in self._read(workspace).values():
            if not versions:
                continue
            last = versions[-1]
            out.append({"kind": last.get("kind"), "artifact_id": last.get("artifact_id"),
                        "version": last.get("version"), "revisions": len(versions)})
        return sorted(out, key=lambda r: (r["kind"] or "", r["artifact_id"] or ""))

    @abstractmethod
    def _read(self, workspace: str) -> Dict[str, List[Dict[str, Any]]]: ...
    @abstractmethod
    def _write(self, workspace: str, data: Dict[str, List[Dict[str, Any]]]) -> None: ...


class InMemoryStudioStore(StudioStore):
    def __init__(self) -> None:
        self._data: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}

    def _read(self, workspace: str) -> Dict[str, List[Dict[str, Any]]]:
        return {k: [dict(v) for v in vs]
                for k, vs in self._data.get(workspace, {}).items()}

    def _write(self, workspace: str, data: Dict[str, List[Dict[str, Any]]]) -> None:
        self._data[workspace] = {k: [dict(v) for v in vs] for k, vs in data.items()}


class JsonStudioStore(StudioStore):
    def __init__(self, base_dir: str) -> None:
        self._base_dir = base_dir

    def _path(self, workspace: str) -> str:
        name = _WS_SANITIZE_RE.sub("_", workspace) or "default"
        return os.path.join(self._base_dir, f"studio_{name}.json")

    def _read(self, workspace: str) -> Dict[str, List[Dict[str, Any]]]:
        p = self._path(workspace)
        if not os.path.exists(p):
            return {}
        try:
            with open(p, encoding="utf-8") as fh:
                return json.load(fh)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"StudioStore could not read {p}: {e}")
            return {}

    def _write(self, workspace: str, data: Dict[str, List[Dict[str, Any]]]) -> None:
        os.makedirs(self._base_dir, exist_ok=True)
        p = self._path(workspace)
        tmp = f"{p}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, p)
