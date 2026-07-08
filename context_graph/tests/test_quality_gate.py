"""Tests for the deterministic node-quality gate (Topic 2, Layer 3). Offline, pure."""

from __future__ import annotations

import pytest

from context_graph.quality import quality_check, is_garbage_name


@pytest.mark.offline
@pytest.mark.parametrize("name", [
    "it", "This", "  they  ", "the system", "The Process", "our approach",
    "thing", "etc.", "unknown", '"it"', "N/A",
])
def test_rejects_garbage_names(name):
    v = quality_check(name, "some real description")
    assert not v.ok and v.reason


@pytest.mark.offline
@pytest.mark.parametrize("name", [
    "PostgreSQL", "Sarah Chen", "Kubernetes", "DISCOUNT-POLICY-2024",
    "Apple Inc.", "cr-m2-concurrency",
])
def test_accepts_real_names(name):
    assert quality_check(name, "a meaningful description of the entity").ok


@pytest.mark.offline
def test_empty_description_rejected():
    v = quality_check("PostgreSQL", "")
    assert not v.ok and "description" in v.reason


@pytest.mark.offline
def test_empty_name_rejected():
    assert not quality_check("   ", "desc").ok


@pytest.mark.offline
def test_conservative_keeps_single_generic_noun():
    # 'performance' is a single generic noun — NOT rejected (aggressive option declined)
    assert quality_check("performance", "the p95 latency of the API under load").ok


@pytest.mark.offline
def test_is_garbage_name_helper():
    assert is_garbage_name("it") == "pronoun / deictic reference"
    assert is_garbage_name("the codebase") == "generic filler phrase"
    assert is_garbage_name("PostgreSQL") is None


@pytest.mark.offline
def test_verdict_is_truthy():
    assert bool(quality_check("Kubernetes", "container orchestration"))
    assert not bool(quality_check("it", "x"))
