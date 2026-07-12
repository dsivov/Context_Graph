"""Context Graph studio — the diff-and-approve authoring layer (P0 schema).

Authoring (Stage 1), migration, and re-approval share one gesture: propose →
assess → (gate + sign) → apply, over a typed, versioned ArtifactDiff. See
docs/PLATFORM_ARCHITECTURE.html (decisions 4/5/7).
"""

from context_graph.studio.schema import DIFF_KINDS, DIFF_ORIGINS, ArtifactDiff

__all__ = ["ArtifactDiff", "DIFF_KINDS", "DIFF_ORIGINS"]
