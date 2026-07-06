"""Per-workspace persistence for rule sets + concept catalogs (wiring step 4).

A workspace's policy is a :class:`RuleBundle` — the DSL rule text, the concept
catalog (name → example phrases), the pinned ``model_id``, an ``enabled`` flag,
and a monotonically increasing ``version``. The store persists bundles and can
reconstruct a live :class:`~context_graph.rules.gate.RulesGate` from one, so the
gate that ``emit_decision_trace`` calls (step 5) can be populated from config.

Two backends share all logic via :class:`RuleStore`:

* :class:`JsonRuleStore` — one JSON file per workspace (the file-based default).
* :class:`InMemoryRuleStore` — ephemeral, for tests and the API layer.

Saving **validates** first (the DSL must parse with non-empty conditions, and
every concept a rule references must be defined), so a broken policy is rejected
at author time rather than at gate time.
"""

from __future__ import annotations

import json
import os
import re
import time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from lightrag.utils import logger

from context_graph.rules.similarity import DEFAULT_MODEL_ID

# Concept references inside a rule: sim(field, "CONCEPT") / similar(field, "CONCEPT", t)
_CONCEPT_REF_RE = re.compile(
    r"""(?:sim|similar)\s*\(\s*[^,]+,\s*["']([A-Za-z0-9_]+)["']""",
    re.VERBOSE,
)
_WS_SANITIZE_RE = re.compile(r"[^A-Za-z0-9_]")


# ─────────────────────────────────────────────────────────────────────────────
# Bundle
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class RuleBundle:
    """A workspace's persisted policy: rules + concepts + metadata."""

    workspace: str
    version: int
    enabled: bool
    model_id: str
    updated_at: float
    concepts: Dict[str, List[str]] = field(default_factory=dict)
    dsl: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "RuleBundle":
        valid = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in valid})


def referenced_concepts(dsl: str) -> set:
    """The set of concept names a DSL rule set references (upper-cased)."""
    return {m.group(1).strip().upper() for m in _CONCEPT_REF_RE.finditer(dsl or "")}


# String literals (concept names, channel values) and identifiers, for extracting
# the decision fields a rule's conditions reference.
_STRING_LIT_RE = re.compile(r'"[^"]*"|\'[^\']*\'')
_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
# DSL keywords / bound functions / literals that are NOT decision fields.
_DSL_NON_FIELDS = frozenset(
    {"and", "or", "not", "in", "is", "true", "false", "none",
     "sim", "similar", "reject", "flag", "notify"}
)


def referenced_fields(condition_texts: Sequence[str]) -> set:
    """The decision-field identifiers a set of rule conditions reference.

    String literals (concept names, channel values) are stripped first, then bare
    identifiers that are not DSL keywords / bound functions are treated as fields.
    """
    fields: set = set()
    for cond in condition_texts:
        stripped = _STRING_LIT_RE.sub(" ", cond or "")
        for ident in _IDENT_RE.findall(stripped):
            if ident.lower() in _DSL_NON_FIELDS:
                continue
            fields.add(ident)
    return fields


def validate_policy(dsl: str, concepts: Mapping[str, Sequence[str]]) -> None:
    """Raise ``ValueError`` if the policy is not safe to persist.

    Checks: (1) the DSL parses with non-empty conditions (reuses the engine's
    load-time lint), (2) every concept referenced by a rule is defined, and
    (3) every decision **field** referenced is a real projected field. The field
    check is what makes the gate fail *closed* on an authoring typo: without it a
    rule like ``amont > 10000`` (typo) saves clean, then raises ``NameError`` at
    eval and is silently skipped — a REJECT rule that never fires. Neither check
    loads the embedding model.
    """
    from context_graph.rules.engine import RulesEngine
    from context_graph.rules.projection import PARAM_FIELDS
    from context_graph.rules.similarity import ConceptCatalog

    defined = {k.strip().upper() for k in concepts}
    # A names-only catalog (placeholder phrases, never encoded during load()).
    catalog = ConceptCatalog(backend=_NullBackend()).define_many(
        {k: ["x"] for k in concepts} or {"_": ["x"]}
    )
    engine = RulesEngine(catalog).load(dsl)  # raises on empty condition / parse error

    missing = referenced_concepts(dsl) - defined
    if missing:
        raise ValueError(
            f"DSL references undefined concept(s): {sorted(missing)}. "
            f"Defined: {sorted(defined)}"
        )

    condition_texts = [engine._conditions_text(r) for r in engine._rules]
    unknown = referenced_fields(condition_texts) - set(PARAM_FIELDS)
    if unknown:
        raise ValueError(
            f"DSL references unknown decision field(s): {sorted(unknown)}. "
            f"A rule can only match projected fields: {sorted(PARAM_FIELDS)}"
        )


class _NullBackend:
    """A backend that must never be asked to encode (used only for validation)."""

    model_id = "null"

    def encode(self, texts):  # pragma: no cover - defensive
        raise RuntimeError("validation must not encode text")


# ─────────────────────────────────────────────────────────────────────────────
# Store base + backends
# ─────────────────────────────────────────────────────────────────────────────


class RuleStore(ABC):
    """Abstract per-workspace rule store. Subclasses implement raw persistence."""

    def __init__(
        self,
        *,
        default_model_id: str = DEFAULT_MODEL_ID,
        now: Callable[[], float] = time.time,
    ) -> None:
        self._default_model_id = default_model_id
        self._now = now

    # -- public API --------------------------------------------------------

    def save(
        self,
        workspace: str,
        dsl: str,
        concepts: Mapping[str, Sequence[str]],
        *,
        enabled: bool = True,
        model_id: Optional[str] = None,
    ) -> RuleBundle:
        """Validate and persist a policy; bumps ``version`` on each save."""
        validate_policy(dsl, concepts)
        prev = self.load(workspace)
        version = (prev.version + 1) if prev is not None else 1
        bundle = RuleBundle(
            workspace=workspace,
            version=version,
            enabled=enabled,
            model_id=model_id or self._default_model_id,
            updated_at=self._now(),
            concepts={k: list(v) for k, v in concepts.items()},
            dsl=dsl,
        )
        self._write_raw(workspace, bundle.to_dict())
        logger.info(f"RuleStore saved workspace '{workspace}' v{version} (enabled={enabled})")
        return bundle

    def load(self, workspace: str) -> Optional[RuleBundle]:
        raw = self._read_raw(workspace)
        return RuleBundle.from_dict(raw) if raw is not None else None

    def delete(self, workspace: str) -> bool:
        return self._delete_raw(workspace)

    def list_workspaces(self) -> List[str]:
        return sorted(self._list_workspaces())

    def set_enabled(self, workspace: str, enabled: bool) -> RuleBundle:
        """Toggle a workspace's gate on/off without re-validating or bumping version."""
        bundle = self.load(workspace)
        if bundle is None:
            raise KeyError(f"No rule bundle for workspace '{workspace}'")
        bundle.enabled = enabled
        bundle.updated_at = self._now()
        self._write_raw(workspace, bundle.to_dict())
        return bundle

    def build_gate(self, workspace: str, *, backend: Any = None):
        """Reconstruct a :class:`RulesGate` for *workspace*, or ``None``.

        Returns ``None`` if no bundle exists or the bundle is disabled — the
        decision path attaches a gate only when one is active. Pass *backend* to
        inject a similarity backend (tests); otherwise the pinned model is used.
        """
        from context_graph.rules.gate import RulesGate
        from context_graph.rules.similarity import ConceptCatalog, Model2VecBackend

        bundle = self.load(workspace)
        if bundle is None or not bundle.enabled:
            return None
        be = backend if backend is not None else Model2VecBackend(bundle.model_id)
        catalog = ConceptCatalog(backend=be).define_many(bundle.concepts)
        return RulesGate.from_dsl(catalog, bundle.dsl)

    # -- raw persistence (subclass) ----------------------------------------

    @abstractmethod
    def _read_raw(self, workspace: str) -> Optional[Dict[str, Any]]:
        ...

    @abstractmethod
    def _write_raw(self, workspace: str, data: Dict[str, Any]) -> None:
        ...

    @abstractmethod
    def _delete_raw(self, workspace: str) -> bool:
        ...

    @abstractmethod
    def _list_workspaces(self) -> List[str]:
        ...


class InMemoryRuleStore(RuleStore):
    """Ephemeral store — handy for tests and as an API-layer cache."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._data: Dict[str, Dict[str, Any]] = {}

    def _read_raw(self, workspace: str) -> Optional[Dict[str, Any]]:
        raw = self._data.get(workspace)
        return dict(raw) if raw is not None else None

    def _write_raw(self, workspace: str, data: Dict[str, Any]) -> None:
        self._data[workspace] = dict(data)

    def _delete_raw(self, workspace: str) -> bool:
        return self._data.pop(workspace, None) is not None

    def _list_workspaces(self) -> List[str]:
        return list(self._data)


class JsonRuleStore(RuleStore):
    """One JSON file per workspace under *base_dir* (the file-based default)."""

    def __init__(self, base_dir: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self._base_dir = base_dir

    def _path(self, workspace: str) -> str:
        name = _WS_SANITIZE_RE.sub("_", workspace) or "default"
        return os.path.join(self._base_dir, f"rules_{name}.json")

    def _read_raw(self, workspace: str) -> Optional[Dict[str, Any]]:
        path = self._path(workspace)
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"RuleStore could not read {path}: {e}")
            return None

    def _write_raw(self, workspace: str, data: Dict[str, Any]) -> None:
        os.makedirs(self._base_dir, exist_ok=True)
        path = self._path(workspace)
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)  # atomic write

    def _delete_raw(self, workspace: str) -> bool:
        path = self._path(workspace)
        if os.path.exists(path):
            os.remove(path)
            return True
        return False

    def _list_workspaces(self) -> List[str]:
        if not os.path.isdir(self._base_dir):
            return []
        out = []
        for fn in os.listdir(self._base_dir):
            if fn.startswith("rules_") and fn.endswith(".json"):
                raw = self._read_raw_path(os.path.join(self._base_dir, fn))
                if raw and "workspace" in raw:
                    out.append(raw["workspace"])
        return out

    @staticmethod
    def _read_raw_path(path: str) -> Optional[Dict[str, Any]]:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (json.JSONDecodeError, OSError):
            return None
