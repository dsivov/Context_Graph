"""Neo4j full-text query sanitization (upstream 1.5.x cherry-pick).

Lucene reserved characters (e.g. '-' → NOT, ':' → field) are misread by the
full-text parser, so a raw 'tb-' silently matches nothing. _sanitize_fulltext_query
replaces them with spaces. Pure classmethod — no database needed.
"""

from __future__ import annotations

import pytest

from lightrag.kg.neo4j_impl import Neo4JStorage

san = Neo4JStorage._sanitize_fulltext_query


@pytest.mark.offline
@pytest.mark.parametrize("raw,expected", [
    ("tb-", "tb"),
    ("foo:bar", "foo bar"),
    ("(a)+b", "a b"),
    ("a-b/c", "a b c"),
    ("hello world", "hello world"),
    ("acme_corp", "acme_corp"),      # underscore is NOT reserved — preserved
    ("---", ""),                     # all reserved → empty (caller falls back)
    ("  spaced   out ", "spaced out"),
])
def test_sanitize(raw, expected):
    assert san(raw) == expected
