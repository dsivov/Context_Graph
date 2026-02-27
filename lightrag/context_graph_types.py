"""Data types for Context Graph (CG) implementation.

Extends LightRAG's triple-based KG (h,r,t) with contextual quadruples (h,r,t,rc),
where rc (RelationContext) captures decision traces, temporal info, and provenance.

Based on: "Context Graph" paper (CGR3 paradigm) - IDEA Research.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from typing import List, Optional, Dict, Any


@dataclass
class RelationContext:
    """The 'rc' component of a contextual quadruple (h,r,t,rc).

    Captures the operational reality and decision lineage behind a relationship,
    transforming flat triples into rich contextual quadruples.
    """

    supporting_sentences: List[str] = field(default_factory=list)
    """Direct verbatim quotes from source documents that support this relationship."""

    temporal_info: Optional[str] = None
    """Validity periods, timestamps, or temporal qualifiers (e.g., 'Valid for Q4 2026')."""

    quantitative_data: Optional[str] = None
    """Budget figures, discount percentages, or other numerical metrics."""

    decision_trace: Optional[str] = None
    """The 'why': rationale, exceptions, approvals, or overrides behind the relationship."""

    provenance: Optional[str] = None
    """Source reference: Slack thread ID, call transcript timestamp, document section."""

    approved_by: Optional[str] = None
    """Entity name of the approver (e.g., 'VP_Smith', 'Finance_Team')."""

    approved_via: Optional[str] = None
    """Channel through which approval was given: 'slack', 'zoom', 'email', 'in_person', 'jira', 'system'."""

    valid_from: Optional[str] = None
    """ISO-8601 date when this decision became effective (e.g., '2024-08-14')."""

    valid_until: Optional[str] = None
    """ISO-8601 date when this decision expires (e.g., '2024-12-31')."""

    policy_ref: Optional[str] = None
    """Policy this decision follows or overrides (e.g., 'DiscountPolicy_Standard')."""

    confidence_score: float = 1.0
    """Extraction reliability signal (0.0–1.0)."""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "RelationContext":
        valid_fields = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in valid_fields})

    @classmethod
    def from_json(cls, s: str) -> "RelationContext":
        """Parse from JSON string; returns empty RelationContext on failure."""
        try:
            d = json.loads(s)
            if isinstance(d, dict):
                return cls.from_dict(d)
        except (json.JSONDecodeError, TypeError, AttributeError):
            pass
        return cls()

    def is_empty(self) -> bool:
        """Returns True if no meaningful content has been captured."""
        return (
            not self.supporting_sentences
            and self.temporal_info is None
            and self.quantitative_data is None
            and self.decision_trace is None
            and self.provenance is None
        )

    def is_active(self, as_of: Optional[str] = None) -> bool:
        """True if this decision is currently valid based on valid_from/valid_until.

        Returns True if neither date is set (validity unknown).
        Dates must be ISO-8601 strings (YYYY-MM-DD).
        """
        import datetime

        if not self.valid_from and not self.valid_until:
            return True
        today = as_of or datetime.date.today().isoformat()
        if self.valid_from and today < self.valid_from:
            return False
        if self.valid_until and today > self.valid_until:
            return False
        return True

    def to_text(self) -> str:
        """Render as human-readable text suitable for LLM prompts."""
        parts = []
        if self.supporting_sentences:
            parts.append("Evidence: " + " | ".join(self.supporting_sentences))
        if self.temporal_info:
            parts.append(f"Valid: {self.temporal_info}")
        if self.quantitative_data:
            parts.append(f"Data: {self.quantitative_data}")
        if self.decision_trace:
            parts.append(f"Rationale: {self.decision_trace}")
        if self.provenance:
            parts.append(f"Source: {self.provenance}")
        return "; ".join(parts) if parts else ""

    @classmethod
    def merge(cls, contexts: List["RelationContext"]) -> "RelationContext":
        """Merge multiple RelationContexts into one.

        Strategy:
        - supporting_sentences: union (deduplicated, preserving order)
        - all scalar string fields: first non-None wins
        - confidence_score: maximum across all contexts
        """
        all_sentences: List[str] = []
        seen_sentences: set = set()
        temporal_info = None
        quantitative_data = None
        decision_trace = None
        provenance = None
        approved_by = None
        approved_via = None
        valid_from = None
        valid_until = None
        policy_ref = None
        max_confidence = 0.0

        for rc in contexts:
            for s in rc.supporting_sentences:
                if s not in seen_sentences:
                    seen_sentences.add(s)
                    all_sentences.append(s)
            if temporal_info is None and rc.temporal_info:
                temporal_info = rc.temporal_info
            if quantitative_data is None and rc.quantitative_data:
                quantitative_data = rc.quantitative_data
            if decision_trace is None and rc.decision_trace:
                decision_trace = rc.decision_trace
            if provenance is None and rc.provenance:
                provenance = rc.provenance
            if approved_by is None and rc.approved_by:
                approved_by = rc.approved_by
            if approved_via is None and rc.approved_via:
                approved_via = rc.approved_via
            if valid_from is None and rc.valid_from:
                valid_from = rc.valid_from
            if valid_until is None and rc.valid_until:
                valid_until = rc.valid_until
            if policy_ref is None and rc.policy_ref:
                policy_ref = rc.policy_ref
            max_confidence = max(max_confidence, rc.confidence_score)

        return cls(
            supporting_sentences=all_sentences,
            temporal_info=temporal_info,
            quantitative_data=quantitative_data,
            decision_trace=decision_trace,
            provenance=provenance,
            approved_by=approved_by,
            approved_via=approved_via,
            valid_from=valid_from,
            valid_until=valid_until,
            policy_ref=policy_ref,
            confidence_score=max_confidence if max_confidence > 0 else 1.0,
        )


@dataclass
class ContextNode:
    """CRM entity with enriched Entity Context (ec).

    Extends LightRAG's entity representation with multi-source context,
    supporting the (e, ec) complete entity representation from the CG paper.
    """

    entity_name: str
    """Unique identifier (e.g., 'Lead_Alpha', 'Opportunity_X')."""

    entity_type: str
    """Classification: LEAD, OPPORTUNITY, STAKEHOLDER, COMPETITOR, etc."""

    description: str = ""
    """Multi-source summary via LLM Profiling P(·)."""

    attributes: Dict[str, str] = field(default_factory=dict)
    """Current state attributes (e.g., Lead Score, Stage, Industry)."""

    reference_links: List[str] = field(default_factory=list)
    """External references: LinkedIn, Wikidata, CRM system links."""


@dataclass
class ContextEdge:
    """CRM relationship as a contextual quadruple (h, r, t, rc).

    The core innovation: extends (h,r,t) triples with Relation Context (rc)
    that captures the operational reality and decision lineage behind the link.
    """

    source_id: str
    """Head entity (h)."""

    target_id: str
    """Tail entity (t)."""

    relation_type: str
    """Relationship keyword/type (r): QUALIFIES, OBJECTS_TO, APPROVES, etc."""

    weight: float = 1.0
    """Relationship strength/frequency."""

    context: RelationContext = field(default_factory=RelationContext)
    """The relation context (rc): 4th component of the quadruple."""
