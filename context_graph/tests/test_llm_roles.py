"""Per-task LLM roles (upstream 1.5.x alignment) — the routing contract.

ContextGraph exposes _llm_extract / _llm_query that fall back to the single
llm_model_func unless a role is attached, so the feature is fully optional and
backward-compatible. Fully offline.
"""

from __future__ import annotations

import pytest

from context_graph.core import ContextGraph


def _cg():
    # __new__ bypasses __post_init__ (no role slots) — the property must still be safe.
    cg = ContextGraph.__new__(ContextGraph)
    cg.llm_model_func = "DEFAULT"
    return cg


@pytest.mark.offline
def test_roles_fall_back_to_default():
    cg = _cg()
    assert cg._llm_extract == "DEFAULT"
    assert cg._llm_query == "DEFAULT"


@pytest.mark.offline
def test_attach_routes_each_role():
    cg = _cg()
    cg.attach_llm_roles(extract="EXTRACT", query="QUERY")
    assert cg._llm_extract == "EXTRACT"
    assert cg._llm_query == "QUERY"


@pytest.mark.offline
def test_partial_attach_keeps_default_for_unset_role():
    cg = _cg()
    cg.attach_llm_roles(query="QUERY")          # extract left unset
    assert cg._llm_extract == "DEFAULT"
    assert cg._llm_query == "QUERY"


@pytest.mark.offline
def test_attach_after_post_init_slots_exist():
    # When constructed normally the slots are None → still fall back cleanly.
    cg = ContextGraph.__new__(ContextGraph)
    cg.llm_model_func = "DEFAULT"
    cg._llm_role_extract = None
    cg._llm_role_query = None
    assert cg._llm_extract == "DEFAULT" and cg._llm_query == "DEFAULT"
