"""Project a finalized Context Graph edge into a flat ``params`` dict.

This is the contract between CG's data model and the Business Rules Engine
(``docs/CLOSING_THE_GAPS.html`` §06). A decision reaches the rules gate as a
quadruple ``(src, tgt, relation_type, rc)``; ``project_decision()`` flattens it
into the ``params`` mapping a rule evaluates — exposing both the raw
``RelationContext`` fields and a few *derived* fields (notably ``amount`` and
``percent`` parsed from the free-text ``quantitative_data``).

Both write paths converge here (see "How a decision finds its edge"):

* **emit** has the tuple in hand → call :func:`project_decision` directly.
* **ingestion / the upsert gate** has a stored edge dict → call
  :func:`project_edge`, which reads ``keywords`` → ``relation_type`` and
  ``relation_context`` → ``rc`` and delegates to :func:`project_decision`.

Either way the same inputs yield the same ``params`` — that identity is the
point, and is covered by a test.

Note on missing numerics: unparseable ``amount`` / ``percent`` are returned as
``None`` (honest "no value"), not ``0``. The engine layer is responsible for
coercing ``None`` before a numeric comparison so a rule like ``amount > 10000``
cannot raise on a missing value. Typed numbers arrive properly with the P2
ontology; until then this parser is best-effort over prose.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional, Union

from lightrag.context_graph_types import RelationContext

RCLike = Union[RelationContext, dict, str, None]

# ─────────────────────────────────────────────────────────────────────────────
# Free-text numeric parsing
# ─────────────────────────────────────────────────────────────────────────────

# A percentage: "20%", "20 %", "15 percent".
_PERCENT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:%|percent\b)", re.IGNORECASE)

# A money-ish number, optionally with a currency symbol, thousands separators,
# a decimal, and a magnitude suffix (k/m/b).
_AMOUNT_RE = re.compile(
    r"""
    (?P<ccy>[$€£¥])?\s*
    (?P<num>
        \d{1,3}(?:,\d{3})+(?:\.\d+)?   # 25,000  or  1,234.56
      | \d+\.\d+                        # 1234.56
      | \d+                             # 1234
    )
    \s*(?P<suf>[kKmMbB])?
    """,
    re.VERBOSE,
)

_MAGNITUDE = {"k": 1_000.0, "m": 1_000_000.0, "b": 1_000_000_000.0}


def parse_percent(text: Optional[str]) -> Optional[float]:
    """Extract a fractional percentage from free text, or ``None``.

    ``"20% discount"`` → ``0.20``; ``"15 percent"`` → ``0.15``;
    ``"$25,000"`` / ``"0.2"`` (no % sign) → ``None``.
    """
    if not text:
        return None
    m = _PERCENT_RE.search(text)
    if not m:
        return None
    try:
        return float(m.group(1)) / 100.0
    except ValueError:
        return None


def parse_amount(text: Optional[str]) -> Optional[float]:
    """Extract a monetary amount from free text, or ``None``.

    Returns a value only when the number looks money-like — a currency symbol,
    thousands separators, a decimal, or a magnitude suffix — so a bare small
    integer (e.g. "valid for 5 days") is *not* mistaken for an amount.
    Percentages are stripped first so ``"20% discount"`` yields ``None``.

    ``"$25,000"`` → ``25000.0``; ``"€8k"`` → ``8000.0``; ``"1.2M"`` →
    ``1200000.0``; ``"8,200"`` → ``8200.0``; ``"20% discount"`` → ``None``.
    """
    if not text:
        return None
    # Remove percentages so their digits are never read as an amount.
    cleaned = _PERCENT_RE.sub(" ", text)
    for m in _AMOUNT_RE.finditer(cleaned):
        num, ccy, suf = m.group("num"), m.group("ccy"), m.group("suf")
        money_like = bool(ccy) or ("," in num) or ("." in num) or bool(suf)
        if not money_like:
            continue
        try:
            value = float(num.replace(",", ""))
        except ValueError:
            continue
        if suf:
            value *= _MAGNITUDE[suf.lower()]
        return value
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Projection
# ─────────────────────────────────────────────────────────────────────────────


def _as_rc(rc: RCLike) -> RelationContext:
    """Coerce a RelationContext / dict / JSON string / None into a RelationContext."""
    if rc is None:
        return RelationContext()
    if isinstance(rc, RelationContext):
        return rc
    if isinstance(rc, str):
        return RelationContext.from_json(rc)
    if isinstance(rc, dict):
        return RelationContext.from_dict(rc)
    raise TypeError(f"Unsupported rc type: {type(rc).__name__}")


def project_decision(
    src: str,
    tgt: str,
    relation_type: Optional[str],
    rc: RCLike,
    *,
    as_of: Optional[str] = None,
) -> Dict[str, Any]:
    """Flatten a decision ``(src, tgt, relation_type, rc)`` into rule ``params``.

    Args:
        src: Head entity name.
        tgt: Tail entity name.
        relation_type: The relationship keyword (``edge["keywords"]`` on the
            ingestion path, or the explicit argument on the emit path).
        rc: The edge's :class:`RelationContext` (or dict / JSON string / None).
        as_of: ISO-8601 date for the ``is_active`` evaluation; defaults to today.
            Pass a fixed date for deterministic tests.

    Returns:
        A flat dict of fields a rule's ``when`` clause can reference. Raw
        ``RelationContext`` strings are passed through (may be ``None``);
        ``amount`` / ``percent`` are derived from ``quantitative_data`` and are
        ``None`` when nothing money/percentage-like is present.
    """
    rc = _as_rc(rc)
    return {
        # identity
        "src": src,
        "tgt": tgt,
        "relation_type": relation_type,
        # raw rc text fields (soft predicates can sim() over these)
        "decision_trace": rc.decision_trace,
        "provenance": rc.provenance,
        "temporal_info": rc.temporal_info,
        "quantitative_data": rc.quantitative_data,
        # structured approval chain (hard predicates)
        "approved_by": rc.approved_by,
        "approved_via": rc.approved_via,
        "policy_ref": rc.policy_ref,
        # temporal
        "valid_from": rc.valid_from,
        "valid_until": rc.valid_until,
        "is_active": rc.is_active(as_of),
        # signals
        "confidence": rc.confidence_score,
        "evidence_count": len(rc.supporting_sentences or []),
        # derived numerics (None when unparseable — engine coerces before compare)
        "amount": parse_amount(rc.quantitative_data),
        "percent": parse_percent(rc.quantitative_data),
    }


# Canonical params a rule may reference, with one-line descriptions. Single
# source of truth for the NL→DSL agent's capability manifest (keys MUST match
# project_decision's output).
PARAM_FIELDS: Dict[str, str] = {
    "src": "head entity name (free text)",
    "tgt": "tail entity name (free text)",
    "relation_type": "the relationship keyword — free text, match with sim()",
    "decision_trace": "rationale/approval text — free text, match with sim()",
    "provenance": "source reference — free text",
    "temporal_info": "validity description — free text",
    "quantitative_data": "amounts/percentages as written text",
    "approved_by": "approver entity name — free text",
    "approved_via": "channel, one of: slack|zoom|email|in_person|jira|system (exact ==)",
    "policy_ref": "policy id/name — free text",
    "valid_from": "ISO-8601 date string",
    "valid_until": "ISO-8601 date string",
    "is_active": "bool — True if currently within the validity window",
    "confidence": "float 0..1 — extraction confidence",
    "evidence_count": "int — number of supporting sentences",
    "amount": "float or None — monetary amount parsed from quantitative_data",
    "percent": "float or None — fraction parsed from quantitative_data (0.20 = 20%)",
}


def project_edge(
    src: str,
    tgt: str,
    edge: Dict[str, Any],
    *,
    as_of: Optional[str] = None,
) -> Dict[str, Any]:
    """Project a *stored* edge dict (ingestion / upsert gate call site).

    Reads ``keywords`` → ``relation_type`` and ``relation_context`` → ``rc`` from
    the edge attributes, then delegates to :func:`project_decision`. This is the
    shape returned by the graph storage at the shared ``upsert_edge`` point, so
    the gate can project an edge it is about to finalize.
    """
    return project_decision(
        src,
        tgt,
        edge.get("keywords"),
        edge.get("relation_context"),
        as_of=as_of,
    )
