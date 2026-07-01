"""Tests for the field-level sim() similarity predicate (lightrag.rules).

Two layers:

* **offline unit tests** (default suite) — catalog logic with a deterministic
  fake backend; no model download, fast.
* **integration tests** (``--run-integration``) — the real ``potion-retrieval-32M``
  model and ``business_rule_engine`` wiring.
"""

from __future__ import annotations

import numpy as np
import pytest

from context_graph.rules import (
    Concept,
    ConceptCatalog,
    make_sim_predicate,
    make_similar_predicate,
    DEFAULT_MODEL_ID,
)
from context_graph.rules.similarity import _normalize_text


# ─────────────────────────────────────────────────────────────────────────────
# Fake backend: maps known phrases to fixed orthonormal-ish vectors so catalog
# logic is exercised deterministically without any model.
# ─────────────────────────────────────────────────────────────────────────────


class FakeBackend:
    """Deterministic backend. 'approve*' words → e0 axis, 'deny*' → e1, else e2."""

    model_id = "fake/deterministic"
    dim = 4

    def encode(self, texts):
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, t in enumerate(texts):
            t = t.lower()
            if "approv" in t or "authoriz" in t or "sign off" in t or "go ahead" in t:
                out[i, 0] = 1.0
            elif "den" in t or "reject" in t or "object" in t:
                out[i, 1] = 1.0
            else:
                out[i, 2] = 1.0
            out[i, 3] = 0.01  # tiny shared component
        return out


@pytest.fixture
def catalog():
    cat = ConceptCatalog(backend=FakeBackend())
    cat.define("APPROVAL", ["approved", "authorized", "sign off on", "go ahead"])
    cat.define("DENIAL", ["denied", "rejected", "objected"])
    return cat


# ── normalisation ────────────────────────────────────────────────────────────


@pytest.mark.offline
def test_normalize_splits_identifiers():
    assert _normalize_text("GRANTED_APPROVAL") == "GRANTED APPROVAL"
    assert _normalize_text("sign-off/approved") == "sign off approved"
    assert _normalize_text("  multiple   spaces ") == "multiple spaces"


# ── catalog scoring ──────────────────────────────────────────────────────────


@pytest.mark.offline
def test_score_matches_concept(catalog):
    # "GRANTED_APPROVAL" normalises to approval-axis → high score vs APPROVAL
    assert catalog.score("GRANTED_APPROVAL", "APPROVAL") > 0.9
    # an unrelated relation lands on the e2 axis → low score
    assert catalog.score("places order", "APPROVAL") < 0.2


@pytest.mark.offline
def test_polarity_is_not_resolved_by_similarity(catalog):
    # Antonyms live on different axes here, but the point of the test is that we
    # query the *right* concept; APPROVAL vs DENIAL are kept separate.
    assert catalog.score("rejected", "DENIAL") > 0.9
    assert catalog.score("rejected", "APPROVAL") < 0.2


@pytest.mark.offline
def test_empty_and_none_score_zero(catalog):
    assert catalog.score(None, "APPROVAL") == 0.0
    assert catalog.score("", "APPROVAL") == 0.0
    assert catalog.score("   ", "APPROVAL") == 0.0


@pytest.mark.offline
def test_unknown_concept_raises(catalog):
    with pytest.raises(KeyError):
        catalog.score("approved", "NOPE")


@pytest.mark.offline
def test_concept_name_is_case_insensitive(catalog):
    assert catalog.has("approval")
    assert catalog.score("approved", "approval") == catalog.score("approved", "APPROVAL")


@pytest.mark.offline
def test_query_vector_is_cached(catalog):
    # second call should hit the cache and return an identical score
    a = catalog.score("approved", "APPROVAL")
    b = catalog.score("approved", "APPROVAL")
    assert a == b
    assert "approved" in catalog._vec_cache


@pytest.mark.offline
def test_concept_requires_phrases():
    with pytest.raises(ValueError):
        Concept(name="EMPTY", phrases=[]).compile(FakeBackend())


@pytest.mark.offline
def test_fingerprint_records_model_and_concepts(catalog):
    fp = catalog.fingerprint()
    assert fp["model_id"] == "fake/deterministic"
    assert "APPROVAL" in fp["concepts"]
    assert "approved" in fp["concepts"]["APPROVAL"]


# ── predicate factories ──────────────────────────────────────────────────────


@pytest.mark.offline
def test_sim_predicate_returns_float(catalog):
    sim = make_sim_predicate(catalog)
    val = sim("authorized the deal", "APPROVAL")
    assert isinstance(val, float)
    assert val > 0.9


@pytest.mark.offline
def test_similar_predicate_returns_bool(catalog):
    similar = make_similar_predicate(catalog, default_threshold=0.4)
    assert similar("approved", "APPROVAL") is True
    assert similar("places order", "APPROVAL") is False
    # explicit threshold override
    assert similar("approved", "APPROVAL", 0.99) is True


# ─────────────────────────────────────────────────────────────────────────────
# Integration: real model + business_rule_engine
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.integration
def test_real_model_concept_catalog_separates_synonyms_from_antonyms():
    """The multi-phrase catalog must cleanly separate matches from non-matches."""
    cat = ConceptCatalog()  # real Model2VecBackend / potion-retrieval-32M
    assert cat.model_id == DEFAULT_MODEL_ID
    cat.define(
        "APPROVAL",
        ["approved", "approval granted", "signed off on", "authorized",
         "gave the go-ahead", "grants approval"],
    )
    should_match = ["APPROVES", "GRANTED_APPROVAL", "authorized the discount",
                    "VP signed off on the deal"]
    should_not = ["DENIES", "rejected the request", "places order",
                  "writes review", "cancelled shipment"]
    match_floor = min(cat.score(t, "APPROVAL") for t in should_match)
    nonmatch_ceil = max(cat.score(t, "APPROVAL") for t in should_not)
    assert match_floor > nonmatch_ceil, (match_floor, nonmatch_ceil)
    # a default threshold should sit cleanly between the two populations
    assert nonmatch_ceil < 0.4 < match_floor


@pytest.mark.integration
def test_sim_predicate_inside_business_rule_engine():
    """sim() works as a registered predicate in a real when/then rule."""
    from business_rule_engine import RuleParser

    cat = ConceptCatalog().define(
        "APPROVAL", ["approved", "signed off on", "authorized", "granted approval"]
    )
    sim = make_sim_predicate(cat)

    triggered = {"hit": False}

    def reject(reason):
        triggered["hit"] = True
        return reason

    rules = """
rule "large approval needs a manager"
when
    sim(relation_type, "APPROVAL") > 0.4
    and amount > 10000
then
    reject("Approvals over $10k need a manager")
end
"""
    parser = RuleParser()
    parser.register_function(sim)
    parser.register_function(reject)
    parser.parsestr(rules)

    # paraphrased relation + over threshold → fires
    parser.execute({"relation_type": "GRANTED_APPROVAL", "amount": 25000})
    assert triggered["hit"] is True

    # reset; unrelated relation → does not fire even though amount is large
    triggered["hit"] = False
    parser.execute({"relation_type": "places order", "amount": 25000})
    assert triggered["hit"] is False
