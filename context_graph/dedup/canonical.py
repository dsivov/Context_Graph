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
from typing import Iterable

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


def prefer_canonical_name(names: Iterable[str]) -> str:
    """Rule-provisional canonical **display** name chosen from raw variants.

    Heuristic (the LLM refines this later, per D5): prefer the fuller form — more
    words, then more characters, then more capitalised letters (proper nouns), with
    the original surface preserved. ``{"IBM", "International Business Machines"}`` →
    ``"International Business Machines"``.
    """
    cands = [n.strip() for n in names if n and n.strip()]
    if not cands:
        return ""
    return max(
        cands,
        key=lambda n: (len(n.split()), len(n), sum(c.isupper() for c in n)),
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
