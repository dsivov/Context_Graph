"""Layer 3 — deterministic node-quality gate (conservative, ontology-free).

Blocks the garbage that has no place in a knowledge graph — pronouns and deictic
references (``it``, ``the system``), and empty descriptions — while leaving anything
ambiguous alone (D12: conservative). Pure and deterministic, so it runs cheaply on
the write path and in a scan, independent of any ontology.

Deliberately NOT aggressive: single generic common nouns (``performance``) and short
descriptions are *not* rejected here — that was the rejected "aggressive" option.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

# High-precision structural garbage — code artifacts that are almost never real
# knowledge-graph entities (common after a source-code backfill). Kept tight to stay
# conservative; false positives are recoverable (quarantined, not deleted).
_GIT_HASH_RE = re.compile(r"^[0-9a-f]{7,40}$")            # commit sha
_PURE_NUMBER_RE = re.compile(r"^[0-9]+$")                 # bare number
_ENV_VAR_RE = re.compile(r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+$")  # ALL_CAPS_WITH_UNDERSCORES
_PATH_RE = re.compile(r"^(?:/|[a-z]+://)")               # /api/v1 , http://…

# Pronouns / deictic tokens that are never real entities on their own.
_PRONOUNS = frozenset({
    "it", "its", "this", "that", "these", "those", "they", "them", "their",
    "theirs", "we", "us", "our", "ours", "you", "your", "yours", "i", "me", "my",
    "mine", "he", "him", "his", "she", "her", "hers", "who", "whom", "which",
    "what", "here", "there", "such",
})

# Generic filler references (article + generic noun) that carry no specific referent.
_GENERIC_PHRASES = frozenset({
    "the system", "the process", "the approach", "our approach", "the team",
    "the company", "the project", "the data", "the method", "the user",
    "the users", "the application", "the app", "the service", "the tool",
    "the platform", "the solution", "the framework", "the codebase", "the code",
    "the model", "the feature", "the function", "the component",
})

# Bare junk nouns.
_JUNK_NAMES = frozenset({
    "thing", "things", "stuff", "something", "anything", "everything", "nothing",
    "etc", "etc.", "misc", "n/a", "na", "none", "unknown", "other", "others",
})


@dataclass
class QualityVerdict:
    ok: bool
    reason: str = ""

    def __bool__(self) -> bool:
        return self.ok


def is_garbage_name(name: Optional[str]) -> Optional[str]:
    """Return a reason string if *name* is obviously not a real entity, else None."""
    n = (name or "").strip()
    if not n:
        return "empty name"
    low = n.lower().strip(" .\"'")
    if low in _PRONOUNS:
        return "pronoun / deictic reference"
    if low in _GENERIC_PHRASES:
        return "generic filler phrase"
    if low in _JUNK_NAMES:
        return "non-specific junk name"
    # Structural code artifacts (high precision). Pure-number first, since digits are
    # also valid hex (a 7-digit string would otherwise read as a commit hash).
    if _PURE_NUMBER_RE.match(n):
        return "bare number"
    if _GIT_HASH_RE.match(n):
        return "commit hash"
    if _ENV_VAR_RE.match(n):
        return "environment-variable / config name"
    if _PATH_RE.match(n):
        return "path / URL"
    return None


def quality_check(
    name: str, description: str = "", entity_type: Optional[str] = None,
) -> QualityVerdict:
    """Conservative quality gate for one extracted entity.

    Rejects only the clearly-worthless: a pronoun/deictic/junk name, or an empty
    description. Returns a :class:`QualityVerdict`; the caller quarantines rejects
    (D12) rather than dropping them.
    """
    reason = is_garbage_name(name)
    if reason:
        return QualityVerdict(False, reason)
    if not (description or "").strip():
        return QualityVerdict(False, "empty description")
    return QualityVerdict(True)
