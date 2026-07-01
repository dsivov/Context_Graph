"""Context Graph rules engine — fuzzy fact matching for the Business Rules Engine.

This package provides the *field-level* ``sim()`` predicate (see
``docs/CLOSING_THE_GAPS.html`` §04): a fast, deterministic, static-vector
similarity matcher that lets a business rule match free-text, LLM-extracted
fields (relation types, decision traces, channels) by *meaning* rather than by
exact string equality.

Public surface::

    from context_graph.rules import ConceptCatalog, make_sim_predicate

    catalog = ConceptCatalog()
    catalog.define("APPROVAL", ["approved", "signed off on", "authorized"])
    sim = make_sim_predicate(catalog)
    sim("GRANTED_APPROVAL", "APPROVAL")   # -> ~0.99 (float)

The predicate is designed to be registered into
`business_rule_engine <https://github.com/manfred-kaiser/business-rule-engine>`_::

    parser.register_function(sim)
    # rule:  when  sim(relation_type, "APPROVAL") > 0.4  then  ...
"""

from context_graph.rules.similarity import (
    DEFAULT_MODEL_ID,
    DEFAULT_MATCH_THRESHOLD,
    SimilarityBackend,
    Model2VecBackend,
    Concept,
    ConceptCatalog,
    make_sim_predicate,
    make_similar_predicate,
)
from context_graph.rules.projection import (
    project_decision,
    project_edge,
    parse_amount,
    parse_percent,
)
from context_graph.rules.engine import (
    RulesEngine,
    EvaluationResult,
    RuleMatch,
    Action,
)
from context_graph.rules.gate import (
    RulesGate,
    GateDecision,
    RuleViolation,
)
from context_graph.rules.store import (
    RuleBundle,
    RuleStore,
    JsonRuleStore,
    InMemoryRuleStore,
    validate_policy,
    referenced_concepts,
)
from context_graph.rules.service import RulesService
from context_graph.rules.agent import RuleAuthor, GenerationResult

__all__ = [
    "DEFAULT_MODEL_ID",
    "DEFAULT_MATCH_THRESHOLD",
    "SimilarityBackend",
    "Model2VecBackend",
    "Concept",
    "ConceptCatalog",
    "make_sim_predicate",
    "make_similar_predicate",
    "project_decision",
    "project_edge",
    "parse_amount",
    "parse_percent",
    "RulesEngine",
    "EvaluationResult",
    "RuleMatch",
    "Action",
    "RulesGate",
    "GateDecision",
    "RuleViolation",
    "RuleBundle",
    "RuleStore",
    "JsonRuleStore",
    "InMemoryRuleStore",
    "validate_policy",
    "referenced_concepts",
    "RulesService",
    "RuleAuthor",
    "GenerationResult",
]
