"""Regression: get_nodes_edges_batch must not KeyError on entity names with a
literal double-quote (e.g. '16" Model'). edges_norm is keyed by the Cypher-escaped
id but AGE returns the unescaped value, so the lookup must re-normalize."""
from __future__ import annotations

import asyncio
import pytest

from lightrag.kg.postgres_impl import PGGraphStorage


def _make(out_rows, in_rows):
    g = PGGraphStorage.__new__(PGGraphStorage)
    g.graph_name = "test_graph"

    async def _query(sql, *a, **k):
        return out_rows if "-[]->" in sql else in_rows

    g._query = _query
    return g


@pytest.mark.offline
def test_get_nodes_edges_batch_handles_quoted_name():
    # AGE returns the UNESCAPED value '16" Model' (not the escaped key).
    out_rows = [{"node_id": '16" Model', "connected_id": "AC Adapter"}]
    in_rows = [{"node_id": '16" Model', "connected_id": "Front View"}]
    g = _make(out_rows, in_rows)
    res = asyncio.run(g.get_nodes_edges_batch(['16" Model', "Battery"]))
    # no KeyError; edges associated under the ORIGINAL caller id
    assert set(res.keys()) == {'16" Model', "Battery"}
    assert ('16" Model', "AC Adapter") in res['16" Model']
    assert ("Front View", '16" Model') in res['16" Model']
    assert res["Battery"] == []


@pytest.mark.offline
def test_get_nodes_edges_batch_plain_name_unaffected():
    out_rows = [{"node_id": "Battery", "connected_id": "LED"}]
    g = _make(out_rows, [])
    res = asyncio.run(g.get_nodes_edges_batch(["Battery"]))
    assert res["Battery"] == [("Battery", "LED")]
