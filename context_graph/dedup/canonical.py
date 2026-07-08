"""Layer A — deterministic canonicalization for entity deduplication.

Turns a raw (already sanitized) entity name into a canonical **key** used to match
duplicates, and helps pick a canonical **display name** for a merged cluster. Pure
and deterministic — the cheap, auditable first line of dedup. The raw name is always
preserved by the caller; this only derives the matching key.

What the key collapses: case, surrounding/─inner punctuation, acronym dots
(``I.B.M.`` → ``ibm``), and trailing legal/org suffixes (``Acme Inc.`` → ``acme``).
What it deliberately does **not** do: acronym↔expansion (``IBM`` vs ``International
Business Machines``) or synonyms — those need embeddings (Layer B) or the LLM
(Layer C). Singular/plural folding is intentionally omitted (too aggressive for a
conservative policy).
"""

from __future__ import annotations

import re
import unicodedata
from typing import Iterable, Optional

# Trailing organisation / legal suffix tokens dropped from the key. Only stripped
# when they are the LAST token(s) and never when they are the whole name.
_LEGAL_SUFFIXES = frozenset({
    "inc", "incorporated", "llc", "ltd", "limited", "corp", "corporation",
    "co", "company", "gmbh", "plc", "sa", "ag", "nv", "bv", "srl", "spa",
    "pty", "llp", "lp", "group", "holdings", "holding", "co ltd",
})

_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)   # anything not word-char/space
_WS_RE = re.compile(r"\s+")
_ALPHA_WORD_RE = re.compile(r"[A-Za-z]+")


def canonicalize(name: str) -> str:
    """Return the canonical dedup key for *name* (may be empty for empty input).

    ``"Apple Inc."`` → ``"apple"`` · ``"I.B.M."`` → ``"ibm"`` ·
    ``"state-of-the-art"`` → ``"state of the art"`` · ``"PostgreSQL"`` → ``"postgresql"``.
    """
    if not name:
        return ""
    s = unicodedata.normalize("NFKC", name).casefold()
    # Collapse acronym dots first so "i.b.m." → "ibm" (not "i b m").
    s = s.replace(".", "")
    # Remaining punctuation (hyphens, slashes, quotes…) → spaces.
    s = _PUNCT_RE.sub(" ", s)
    s = _WS_RE.sub(" ", s).strip()
    if not s:
        return ""
    tokens = s.split(" ")
    # Drop trailing legal-suffix tokens, but never reduce the name to nothing.
    while len(tokens) > 1 and tokens[-1] in _LEGAL_SUFFIXES:
        tokens.pop()
    return " ".join(tokens)


# Representativeness weights (D5/D6): frequency dominates — the form people actually
# use is usually the canonical one — with completeness and proper-noun-ness as
# tie-breakers and a penalty that expands bare acronyms only when frequency is close.
_W_FREQ = 1.0
_W_PROPER = 0.25
_W_COMPLETE = 0.25
_W_ACRONYM = 0.25


def _proper_noun_fraction(name: str) -> float:
    """Fraction of alphabetic words that start with a capital (proper-noun-ness)."""
    words = _ALPHA_WORD_RE.findall(name)
    if not words:
        return 0.0
    return sum(1 for w in words if w[:1].isupper()) / len(words)


def _is_bare_acronym(name: str) -> bool:
    """True for a single all-uppercase token like ``IBM`` / ``NASA`` (2–6 letters)."""
    s = name.strip()
    if " " in s:
        return False
    letters = re.sub(r"[^A-Za-z]", "", s)
    return 2 <= len(letters) <= 6 and letters.isupper()


def representativeness(
    name: str, *, count: int = 0, max_count: int = 1, max_words: int = 1,
) -> float:
    """Score how representative a surface form is as the canonical display name.

    ``w1·frequency + w2·proper_noun + w3·completeness − w4·bare_acronym``, each term
    normalised to [0,1] within the candidate set. Frequency (mentions across the
    corpus) dominates, so the commonly-used form wins; the acronym penalty only
    breaks near-ties, expanding e.g. ``IBM`` → ``International Business Machines``
    when neither is clearly more frequent.
    """
    freq = (count / max_count) if max_count > 0 else 0.0
    completeness = (len(name.split()) / max_words) if max_words > 0 else 0.0
    acronym = 1.0 if _is_bare_acronym(name) else 0.0
    return (_W_FREQ * freq + _W_PROPER * _proper_noun_fraction(name)
            + _W_COMPLETE * completeness - _W_ACRONYM * acronym)


def prefer_canonical_name(
    names: Iterable[str], *, counts: Optional[dict] = None,
) -> str:
    """Canonical **display** name — the most *representative* variant.

    Chooses by :func:`representativeness` (frequency-weighted), not "longest wins".
    Pass ``counts`` (variant name → mention count, e.g. number of source chunks) to
    let frequency drive the choice; without counts it falls back to completeness /
    proper-noun-ness / anti-acronym. The raw surface form is preserved.
    """
    cands = [n.strip() for n in names if n and n.strip()]
    if not cands:
        return ""
    counts = counts or {}
    max_count = max((int(counts.get(n, 0)) for n in cands), default=0)
    max_words = max((len(n.split()) for n in cands), default=1)
    return max(
        cands,
        key=lambda n: (
            representativeness(n, count=int(counts.get(n, 0)),
                               max_count=max_count, max_words=max_words),
            len(n),  # stable tiebreak
        ),
    )


def is_acronym_of(short: str, long: str) -> bool:
    """True if *short* is the initialism of *long* (e.g. ``IBM`` of ``International
    Business Machines``). A cheap high-precision signal for the resolver's name check
    and for adjudication hints. Requires ≥ 2 letters to avoid trivial matches.
    """
    s = re.sub(r"[^a-z]", "", (short or "").casefold())
    if len(s) < 2:
        return False
    initials = "".join(w[0] for w in _ALPHA_WORD_RE.findall(long or "")).casefold()
    return s == initials
