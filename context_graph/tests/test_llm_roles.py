"""Per-task LLM roles (upstream 1.5.x alignment) — the routing contract.

ContextGraph exposes _llm_extract / _llm_query that fall back to the single
llm_model_func unless a role is attached, so the feature is fully optional and
backward-compatible. Fully offline.
"""

from __future__ import annotations

from types import SimpleNamespace

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
    cg._llm_role_keyword = None
    assert cg._llm_extract == "DEFAULT" and cg._llm_query == "DEFAULT"
    assert cg._llm_keyword == "DEFAULT"


# --- KEYWORD role -----------------------------------------------------------

@pytest.mark.offline
def test_keyword_role_falls_back_to_default():
    cg = _cg()
    assert cg._llm_keyword == "DEFAULT"


@pytest.mark.offline
def test_attach_routes_keyword_independently_of_query():
    cg = _cg()
    cg.attach_llm_roles(query="QUERY", keyword="KEYWORD")
    assert cg._llm_query == "QUERY"
    assert cg._llm_keyword == "KEYWORD"
    assert cg._llm_extract == "DEFAULT"


@pytest.mark.offline
def test_partial_attach_keeps_default_for_unset_keyword():
    cg = _cg()
    cg.attach_llm_roles(extract="EXTRACT")       # keyword left unset
    assert cg._llm_keyword == "DEFAULT"


class _Param:
    """Minimal stand-in for QueryParam (avoids importing the full dataclass)."""
    def __init__(self, keyword_model_func=None):
        self.keyword_model_func = keyword_model_func


@pytest.mark.offline
def test_apply_keyword_role_injects_when_set():
    cg = _cg()
    cg._llm_role_keyword = "KEYWORD"
    p = _Param()
    cg._apply_keyword_role(p)
    assert p.keyword_model_func == "KEYWORD"


@pytest.mark.offline
def test_apply_keyword_role_noop_when_unset():
    cg = _cg()
    cg._llm_role_keyword = None
    p = _Param()
    cg._apply_keyword_role(p)
    assert p.keyword_model_func is None


@pytest.mark.offline
def test_apply_keyword_role_respects_caller_override():
    cg = _cg()
    cg._llm_role_keyword = "KEYWORD"
    p = _Param(keyword_model_func="CALLER")
    cg._apply_keyword_role(p)
    assert p.keyword_model_func == "CALLER"   # not clobbered


@pytest.mark.offline
def test_queryparam_has_keyword_model_func_field():
    from lightrag.base import QueryParam
    assert QueryParam().keyword_model_func is None
    assert QueryParam(keyword_model_func="K").keyword_model_func == "K"


@pytest.mark.offline
def test_extract_keywords_prefers_keyword_model_func(monkeypatch):
    """Runtime routing proof: extract_keywords_only calls keyword_model_func,
    not model_func or the global func, when the KEYWORD role is set."""
    import asyncio
    from lightrag import operate as _operate
    from lightrag.base import QueryParam

    async def _cache_miss(*a, **k):
        return None
    monkeypatch.setattr(_operate, "handle_cache", _cache_miss)

    called = {"which": None}

    def _make(tag):
        async def _f(prompt, keyword_extraction=False, **kwargs):
            called["which"] = tag
            return '{"high_level_keywords": ["h"], "low_level_keywords": ["l"]}'
        return _f

    param = QueryParam(model_func=_make("MODEL"), keyword_model_func=_make("KEYWORD"))
    global_config = {
        "addon_params": {},
        "tokenizer": SimpleNamespace(encode=lambda s: s.split()),
        "llm_model_func": _make("GLOBAL"),
    }
    # enable_llm_cache False → skip the cache-save branch (no real KV needed).
    hashing_kv = SimpleNamespace(global_config={"enable_llm_cache": False})
    hl, ll = asyncio.run(
        _operate.extract_keywords_only("q", param, global_config, hashing_kv=hashing_kv)
    )
    assert called["which"] == "KEYWORD"
    assert hl == ["h"] and ll == ["l"]
