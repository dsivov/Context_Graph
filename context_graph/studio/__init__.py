"""Context Graph studio — the diff-and-approve authoring layer (P3).

Authoring (Stage 1), migration, and re-approval share one gesture: propose →
assess → (gate + sign) → apply, over a typed, versioned ArtifactDiff. The
:class:`DiffEngine` composes the ontology / rule / flow / action services; the
:class:`StudioStore` keeps the append-only version ledger for history + revert.
See docs/PLATFORM_ARCHITECTURE.html (decisions 4/5/7).
"""

from context_graph.studio.schema import DIFF_KINDS, DIFF_ORIGINS, ArtifactDiff
from context_graph.studio.service import DiffEngine
from context_graph.studio.store import (
    ArtifactVersion,
    InMemoryStudioStore,
    JsonStudioStore,
    SignOff,
    StudioStore,
)

__all__ = [
    "ArtifactDiff",
    "DIFF_KINDS",
    "DIFF_ORIGINS",
    "DiffEngine",
    "StudioStore",
    "InMemoryStudioStore",
    "JsonStudioStore",
    "ArtifactVersion",
    "SignOff",
]
