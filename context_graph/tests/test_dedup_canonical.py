"""Tests for dedup Layer A — canonicalization. Offline, pure."""

from __future__ import annotations

import pytest

from context_graph.dedup import (
    canonicalize, prefer_canonical_name, representativeness, is_acronym_of,
)


@pytest.mark.offline
@pytest.mark.parametrize("raw,key", [
    ("Apple", "apple"),
    ("apple", "apple"),
    ("PostgreSQL", "postgresql"),
    ("Apple Inc.", "apple"),
    ("Acme Corporation", "acme"),
    ("Acme Co.", "acme"),
    ("I.B.M.", "ibm"),
    ("IBM", "ibm"),
    ("state-of-the-art", "state of the art"),
    ("  Sarah   Chen  ", "sarah chen"),
    ('"Quoted"', "quoted"),
    ("", ""),
])
def test_canonicalize(raw, key):
    assert canonicalize(raw) == key


@pytest.mark.offline
def test_canonicalize_never_empties_a_pure_suffix():
    # A name that is only a legal suffix must not collapse to "".
    assert canonicalize("Inc") == "inc"
    assert canonicalize("Group") == "group"


@pytest.mark.offline
def test_case_and_suffix_variants_share_a_key():
    assert canonicalize("Apple") == canonicalize("apple") == canonicalize("Apple Inc.")


@pytest.mark.offline
def test_prefer_canonical_name_without_counts_expands_acronym():
    # No frequency data → completeness + anti-acronym expand the acronym.
    assert prefer_canonical_name(["IBM", "International Business Machines"]) == \
        "International Business Machines"
    assert prefer_canonical_name(["apple", "Apple"]) == "Apple"   # proper-noun tiebreak
    assert prefer_canonical_name([]) == ""


@pytest.mark.offline
def test_frequency_dominates_when_a_form_is_much_more_common():
    # People overwhelmingly say "Postgres" → it wins despite being shorter.
    counts = {"Postgres": 47, "PostgreSQL Database Management System": 2}
    assert prefer_canonical_name(list(counts), counts=counts) == "Postgres"


@pytest.mark.offline
def test_acronym_penalty_breaks_a_frequency_tie():
    # Comparable frequency → expand the acronym rather than keep the bare form.
    counts = {"IBM": 10, "International Business Machines": 10}
    assert prefer_canonical_name(list(counts), counts=counts) == \
        "International Business Machines"


@pytest.mark.offline
def test_strong_acronym_frequency_still_wins():
    # If the acronym is vastly more common, frequency dominates the penalty.
    counts = {"IBM": 500, "International Business Machines": 3}
    assert prefer_canonical_name(list(counts), counts=counts) == "IBM"


@pytest.mark.offline
def test_representativeness_is_a_float():
    s = representativeness("Postgres", count=47, max_count=47, max_words=4)
    assert isinstance(s, float) and s > 0


@pytest.mark.offline
def test_is_acronym_of():
    assert is_acronym_of("IBM", "International Business Machines")
    assert is_acronym_of("i.b.m.", "International Business Machines")
    assert not is_acronym_of("IBM", "Apple Inc")
    assert not is_acronym_of("A", "Apple")          # too short
