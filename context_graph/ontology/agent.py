"""NL → ontology author (P2, AI-assisted authoring).

Turns a natural-language description of a domain into a validated
:class:`~context_graph.ontology.Ontology` — object types with typed properties
and directed link types with cardinality. Mirrors P1's ``RuleAuthor``: the LLM is
*constrained, not trusted*. Every draft must

* deserialize into a real ``Ontology`` (well-formed types/properties/kinds),
* pass ``Ontology.lint()`` (no link references an undefined object type), and
* be **dry-run** — the model also returns a few sample entities/relations, which
  are checked against the drafted ontology with :class:`ExtractionValidator`, so
  the schema is exercised before it's offered back.

Drafts that fail are fed back for a bounded number of auto-repair rounds.

The LLM is the workspace's ``llm_model_func`` (``await llm(prompt,
system_prompt=...) -> str``); nothing here is provider-specific.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

from lightrag.context_graph import _extract_json_object
from lightrag.utils import logger

from context_graph.ontology.schema import Cardinality, Ontology, PropertyKind
from context_graph.ontology.validate import ExtractionValidator

LLMFunc = Callable[..., Awaitable[str]]


@dataclass
class OntologyGenerationResult:
    """The outcome of an NL→ontology generation attempt."""

    valid: bool
    ontology: Dict[str, Any] = field(default_factory=dict)   # Ontology.to_dict()
    lint: List[str] = field(default_factory=list)            # structural problems (empty = clean)
    samples: Dict[str, Any] = field(default_factory=dict)    # {entities, relations}
    dry_run: Dict[str, Any] = field(default_factory=dict)    # ExtractionReport.summary()
    explanation: str = ""
    errors: List[str] = field(default_factory=list)
    attempts: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "valid": self.valid,
            "ontology": self.ontology,
            "lint": self.lint,
            "samples": self.samples,
            "dry_run": self.dry_run,
            "explanation": self.explanation,
            "errors": self.errors,
            "attempts": self.attempts,
        }


class OntologyAuthor:
    """Drafts, validates, repairs, and dry-runs an ontology from a description."""

    def __init__(self, llm: LLMFunc) -> None:
        self._llm = llm

    # -- prompts -----------------------------------------------------------

    def _system_prompt(self) -> str:
        kinds = "\n".join(f"  - {k.value}: {d}" for k, d in _KIND_HELP.items())
        cards = ", ".join(c.value for c in Cardinality)
        return f"""You convert a natural-language description of a business domain \
into a typed ontology for a knowledge graph. Output STRICT JSON only.

An ontology has OBJECT TYPES (entity kinds, e.g. Person, Order) each with typed \
PROPERTIES, and directed LINK TYPES (relations, e.g. approved: Person -> Order) \
with a cardinality. Model the nouns as object types and the verbs as link types.

PROPERTY KINDS (pick the most specific):
{kinds}

Rules:
- Every link's source_types / target_types MUST be object types you define.
- Use "money" for currency amounts and "percent" for rates/discounts (they parse \
"$25,000" and "20%"). Use "enum" with enum_values for closed value sets.
- cardinality is one of: {cards}  (source:target multiplicity).
- Keep it tight: only the types the description implies. Prefer few, well-typed \
properties over many vague ones.

Return JSON with exactly these keys:
{{
  "ontology": {{
    "name": "<domain>",
    "object_types": [
      {{"name": "Order", "description": "...", "properties": [
        {{"name": "value", "kind": "money", "required": true}},
        {{"name": "status", "kind": "enum", "enum_values": ["open","won","lost"]}}
      ]}}
    ],
    "link_types": [
      {{"name": "approved", "source_types": ["Person"], "target_types": ["Order"],
        "cardinality": "1:N", "description": "...", "properties": []}}
    ]
  }},
  "samples": {{
    "entities": [{{"name": "Q3 Deal", "type": "Order", "value": "$25,000", "status": "won"}}],
    "relations": [{{"type": "approved", "source": "Sarah Chen", "target": "Q3 Deal"}}]
  }},
  "explanation": "<one sentence describing the ontology>"
}}

The samples must be realistic instances that CONFORM to your ontology — they are \
used to check it."""

    def _user_prompt(self, description: str, base: Optional[Dict[str, Any]],
                     repair: Optional[str]) -> str:
        parts = [f"Domain to model:\n{description.strip()}"]
        if base and (base.get("object_types") or base.get("link_types")):
            existing_obj = ", ".join(o["name"] for o in base.get("object_types", [])) or "(none)"
            existing_lnk = ", ".join(lt["name"] for lt in base.get("link_types", [])) or "(none)"
            parts.append(
                f"Extend this existing ontology (keep its types, add what's missing):\n"
                f"  object types: {existing_obj}\n  link types: {existing_lnk}"
            )
        parts.append("Produce the JSON.")
        if repair:
            parts.append(f"YOUR PREVIOUS ATTEMPT WAS INVALID:\n{repair}\nFix it and return corrected JSON.")
        return "\n\n".join(parts)

    # -- generation loop ---------------------------------------------------

    async def generate(
        self,
        description: str,
        *,
        base: Optional[Ontology] = None,
        max_repairs: int = 1,
    ) -> OntologyGenerationResult:
        base_dict = base.to_dict() if base is not None else None
        errors: List[str] = []
        last_ontology: Dict[str, Any] = {}

        for attempt in range(max_repairs + 1):
            raw = await self._llm(
                self._user_prompt(description, base_dict, errors[-1] if errors else None),
                system_prompt=self._system_prompt(),
            )
            payload = _extract_json_object(raw)
            if payload is None:
                errors.append("the model did not return a JSON object")
                continue

            onto_dict = payload.get("ontology")
            if not isinstance(onto_dict, dict):
                errors.append("missing 'ontology' object")
                continue
            if base_dict is not None:
                onto_dict = _merge_ontology(base_dict, onto_dict)

            # 1) must deserialize into a real Ontology
            try:
                onto = Ontology.from_dict(onto_dict)
            except (KeyError, ValueError, TypeError) as e:
                errors.append(f"ontology did not build: {e}")
                last_ontology = onto_dict
                continue
            last_ontology = onto.to_dict()

            # 2) must be structurally consistent
            lint = onto.lint()
            if lint:
                errors.append("lint failed: " + "; ".join(lint))
                continue

            # 3) dry-run the model's own samples against the drafted ontology
            samples = payload.get("samples") or {}
            report = ExtractionValidator(onto).validate(
                entities=samples.get("entities") or [],
                relations=samples.get("relations") or [],
            )

            logger.info(f"OntologyAuthor produced a valid ontology after {attempt + 1} attempt(s) "
                        f"({len(onto.object_types)} types, {len(onto.link_types)} links)")
            return OntologyGenerationResult(
                valid=True, ontology=onto.to_dict(), lint=[], samples=samples,
                dry_run=report.summary(), explanation=payload.get("explanation", ""),
                errors=errors, attempts=attempt + 1,
            )

        return OntologyGenerationResult(
            valid=False, ontology=last_ontology, errors=errors, attempts=max_repairs + 1,
        )


_KIND_HELP: Dict[PropertyKind, str] = {
    PropertyKind.STRING: "short free text (names, labels)",
    PropertyKind.TEXT: "long free text (descriptions, notes)",
    PropertyKind.INTEGER: "whole number",
    PropertyKind.FLOAT: "decimal number",
    PropertyKind.BOOLEAN: "true/false flag",
    PropertyKind.DATE: "ISO date YYYY-MM-DD",
    PropertyKind.ENUM: "one of a fixed set (give enum_values)",
    PropertyKind.MONEY: "currency amount",
    PropertyKind.PERCENT: "rate/fraction 0..1",
}


def _merge_ontology(base: Dict[str, Any], draft: Dict[str, Any]) -> Dict[str, Any]:
    """Union base + draft by type name (draft wins on conflicts)."""
    def _by_name(items: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        return {i["name"]: i for i in items if isinstance(i, dict) and i.get("name")}

    objs = _by_name(base.get("object_types", []))
    objs.update(_by_name(draft.get("object_types", [])))
    links = _by_name(base.get("link_types", []))
    links.update(_by_name(draft.get("link_types", [])))
    return {
        "name": draft.get("name") or base.get("name", ""),
        "version": base.get("version", 1),
        "object_types": list(objs.values()),
        "link_types": list(links.values()),
    }
