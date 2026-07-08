"""NL → DSL rule author (wiring step 6).

Turns a natural-language policy into a validated rule set + concept catalog. The
agent is *constrained, not trusted*: every draft must parse (with non-empty
conditions), reference only defined concepts, and is dry-run on fixtures before
it is offered back. See ``docs/CLOSING_THE_GAPS.html`` §05.

The LLM is the workspace's configured ``llm_model_func`` (provider-agnostic:
``await llm(prompt, system_prompt=...) -> str``). Nothing here is provider-specific.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

from lightrag.context_graph import _extract_json_object
from context_graph.types import RelationContext
from lightrag.utils import logger

from context_graph.rules.engine import ACTION_VERBS, RulesEngine
from context_graph.rules.projection import PARAM_FIELDS
from context_graph.rules.similarity import ConceptCatalog, Model2VecBackend
from context_graph.rules.gate import RulesGate
from context_graph.rules.store import validate_policy

LLMFunc = Callable[..., Awaitable[str]]


@dataclass
class GenerationResult:
    """The outcome of an NL→DSL generation attempt."""

    valid: bool
    dsl: str = ""
    concepts: Dict[str, List[str]] = field(default_factory=dict)
    fixtures: List[Dict[str, Any]] = field(default_factory=list)
    dry_run: List[Dict[str, Any]] = field(default_factory=list)
    explanation: str = ""
    errors: List[str] = field(default_factory=list)
    attempts: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "valid": self.valid,
            "dsl": self.dsl,
            "concepts": self.concepts,
            "fixtures": self.fixtures,
            "dry_run": self.dry_run,
            "explanation": self.explanation,
            "errors": self.errors,
            "attempts": self.attempts,
        }


class RuleAuthor:
    """Drafts, validates, repairs, and dry-runs DSL from a natural-language policy."""

    def __init__(self, llm: LLMFunc, *, gate_backend: Any = None) -> None:
        self._llm = llm
        self._backend = gate_backend  # None → real model for the dry-run

    # -- prompts -----------------------------------------------------------

    def _system_prompt(self) -> str:
        params = "\n".join(f"  - {k}: {v}" for k, v in PARAM_FIELDS.items())
        verbs = "\n".join(f"  - {k}(\"reason\"): {v}" for k, v in ACTION_VERBS.items())
        return f"""You convert a natural-language policy into a business-rule DSL for a \
decision gate. Output STRICT JSON only.

GRAMMAR (formatting is strict — `when` and `then` MUST each be alone on their \
own line; conditions and actions are indented on the lines beneath them):

rule "<short name>"  priority <int>
when
    <condition>
    and <condition>
then
    <action>("<reason>")
end

CONDITIONS are Python boolean expressions over these params:
{params}

MATCHING RULES (important):
- For FREE-TEXT params (relation_type, decision_trace, provenance, etc.) match by \
MEANING, never by exact string: use  sim(field, "CONCEPT") > 0.4 . Do NOT use == \
on free text. Define every CONCEPT you reference in the "concepts" object with \
3-6 short example phrases.
- For NUMBERS / FLAGS / channels (amount, percent, confidence, is_active, \
approved_via) use exact operators (>, <, ==).
- Do not rely on similarity for polarity (approved vs rejected) — use structured \
fields.

ACTIONS available in `then`:
{verbs}

Return JSON with exactly these keys:
{{
  "dsl": "<the full rule set>",
  "concepts": {{ "CONCEPT_NAME": ["phrase", "..."] }},
  "fixtures": [
    {{"name": "...", "expect": "FLAG|PASS|REJECT",
      "decision": {{"src": "...", "tgt": "...", "relation_type": "...",
                    "quantitative_data": "...", "approved_via": "..."}}}}
  ],
  "explanation": "<one sentence in plain English>"
}}"""

    def _user_prompt(self, policy: str, concepts: Dict[str, List[str]],
                     repair: Optional[str], prev_dsl: Optional[str]) -> str:
        if concepts:
            known = "\n".join(f"  - {k}: {', '.join(v)}" for k, v in concepts.items())
        else:
            known = "  (none yet — define any you need)"
        base = (
            f"Policy to encode:\n{policy.strip()}\n\n"
            f"Concepts already defined (reuse these when relevant):\n{known}\n\n"
            f"Produce the JSON."
        )
        if repair:
            base += (
                f"\n\nYOUR PREVIOUS ATTEMPT FAILED VALIDATION:\n{repair}\n"
                f"Previous dsl was:\n{prev_dsl or ''}\nFix it and return corrected JSON."
            )
        return base

    # -- generation loop ---------------------------------------------------

    async def generate(
        self,
        policy: str,
        *,
        concepts: Optional[Dict[str, List[str]]] = None,
        max_repairs: int = 1,
    ) -> GenerationResult:
        seed = {k: list(v) for k, v in (concepts or {}).items()}
        errors: List[str] = []
        prev_dsl: Optional[str] = None

        for attempt in range(max_repairs + 1):
            raw = await self._llm(
                self._user_prompt(policy, seed, errors[-1] if errors else None, prev_dsl),
                system_prompt=self._system_prompt(),
            )
            payload = _extract_json_object(raw)
            if payload is None:
                errors.append("the model did not return a JSON object")
                continue

            dsl = (payload.get("dsl") or "").strip()
            proposed = payload.get("concepts") or {}
            merged = {**seed, **{k: list(v) for k, v in proposed.items()}}
            prev_dsl = dsl

            try:
                validate_policy(dsl, merged)
            except ValueError as e:
                errors.append(str(e))
                continue

            fixtures = payload.get("fixtures") or []
            dry = self._dry_run(dsl, merged, fixtures)
            logger.info(f"RuleAuthor produced a valid rule set after {attempt + 1} attempt(s)")
            return GenerationResult(
                valid=True, dsl=dsl, concepts=merged, fixtures=fixtures,
                dry_run=dry, explanation=payload.get("explanation", ""),
                errors=errors, attempts=attempt + 1,
            )

        return GenerationResult(
            valid=False, dsl=prev_dsl or "", concepts=seed, errors=errors,
            attempts=max_repairs + 1,
        )

    # -- dry-run -----------------------------------------------------------

    def _dry_run(self, dsl: str, concepts: Dict[str, List[str]],
                 fixtures: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Evaluate each fixture against the freshly generated rule set."""
        if not fixtures:
            return []
        backend = self._backend if self._backend is not None else Model2VecBackend()
        catalog = ConceptCatalog(backend=backend).define_many(concepts)
        gate = RulesGate(RulesEngine(catalog).load(dsl))

        out: List[Dict[str, Any]] = []
        for fx in fixtures:
            decision = fx.get("decision") or {}
            src = decision.get("src", "src")
            tgt = decision.get("tgt", "tgt")
            relation_type = decision.get("relation_type", "")
            rc = RelationContext.from_dict(
                {k: v for k, v in decision.items() if k not in ("src", "tgt", "relation_type")}
            )
            try:
                d = gate.check(src, tgt, relation_type, rc)
                outcome = d.outcome
            except Exception as e:  # pragma: no cover - defensive
                outcome = f"error: {e}"
            expect = (fx.get("expect") or "").upper()
            out.append({
                "name": fx.get("name", ""),
                "expect": expect or None,
                "outcome": outcome,
                "ok": (not expect) or (outcome == expect),
            })
        return out
