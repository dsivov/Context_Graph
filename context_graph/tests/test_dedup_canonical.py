"""Tests for dedup Layer A — canonicalization. Offline, pure."""

from __future__ import annotations

import pytest

from context_graph.dedup import canonicalize, prefer_canonical_name, is_acronym_of


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
def test_prefer_canonical_name_picks_fuller_form():
    assert prefer_canonical_name(["IBM", "International Business Machines"]) == \
        "International Business Machines"
    assert prefer_canonical_name(["apple", "Apple"]) == "Apple"   # capitalised wins tie
    assert prefer_canonical_name([]) == ""


@pytest.mark.offline
def test_is_acronym_of():
    assert is_acronym_of("IBM", "International Business Machines")
    assert is_acronym_of("i.b.m.", "International Business Machines")
    assert not is_acronym_of("IBM", "Apple Inc")
    assert not is_acronym_of("A", "Apple")          # too short
