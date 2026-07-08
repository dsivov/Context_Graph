"""Layer 3 — deterministic node-quality gate (conservative, ontology-free).

Blocks the garbage that has no place in a knowledge graph — pronouns and deictic
references (``it``, ``the system``), and empty descriptions — while leaving anything
ambiguous alone (D12: conservative). Pure and deterministic, so it runs cheaply on
the write path and in a scan, independent of any ontology.

Deliberately NOT aggressive: single generic common nouns (``performance``) and short
descriptions are *not* rejected here — that was the rejected "aggressive" option.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

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
