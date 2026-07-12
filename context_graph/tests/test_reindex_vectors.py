"""reindex_graph_vectors — rebuilds entity/relation vectors from the graph.

The critical contract: the rebuilt records must match the shape ingestion writes
(ainsert_custom_kg) EXACTLY, or semantic search and dedup silently break. Offline.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from context_graph.core import ContextGraph
from lightrag.utils import compute_mdhash_id


def _cg():
    cg = ContextGraph.__new__(ContextGraph)
    cg.workspace = "ws"
    graph = AsyncMock()
    graph.get_all_labels = AsyncMock(return_value=["Alpha"])
    graph.get_node = AsyncMock(return_value={
        "entity_type": "Org", "description": "the alpha org",
        "source_id": "chunk-1", "file_path": "doc.md",
    })
    graph.get_all_edges = AsyncMock(return_value=[{
        "source": "Alpha", "target": "Beta", "keywords": "PARTNERS",
        "description": "a and b partner", "source_id": "chunk-1",
        "weight": 2.0, "file_path": "doc.md",
    }])
    cg.chunk_entity_relation_graph = graph
    cg.entities_vdb = AsyncMock()
    cg.relationships_vdb = AsyncMock()
    # decisions overlay is exercised elsewhere; stub it here
    cg.reindex_decisions = AsyncMock(return_value={"reindexed": 4})
    cg.reindex_graph_vectors = ContextGraph.reindex_graph_vectors.__get__(cg, type(cg))
    return cg


@pytest.mark.offline
@pytest.mark.asyncio
async def test_entity_record_matches_ingestion_shape():
    cg = _cg()
    await cg.reindex_graph_vectors()
    payload = cg.entities_vdb.upsert.call_args.args[0]
    eid = compute_mdhash_id("Alpha", prefix="ent-")
    assert eid in payload
    rec = payload[eid]
    assert rec["content"] == "Alpha\nthe alpha org"     # name + "\n" + description
    assert rec["entity_name"] == "Alpha"
    assert rec["source_id"] == "chunk-1"
    assert rec["entity_type"] == "Org"
    assert rec["file_path"] == "doc.md"


@pytest.mark.offline
@pytest.mark.asyncio
async def test_relation_record_matches_ingestion_shape():
    cg = _cg()
    await cg.reindex_graph_vectors()
    payload = cg.relationships_vdb.upsert.call_args.args[0]
    rid = compute_mdhash_id("Alpha" + "Beta", prefix="rel-")
    assert rid in payload
    rec = payload[rid]
    # content == f"{keywords}\t{src}\n{tgt}\n{description}"
    assert rec["content"] == "PARTNERS\tAlpha\nBeta\na and b partner"
    assert rec["src_id"] == "Alpha" and rec["tgt_id"] == "Beta"
    assert rec["weight"] == 2.0
    assert rec["source_id"] == "chunk-1"


@pytest.mark.offline
@pytest.mark.asyncio
async def test_drops_rebuilds_and_overlays_decisions():
    cg = _cg()
    summary = await cg.reindex_graph_vectors()
    cg.entities_vdb.drop.assert_awaited()
    cg.relationships_vdb.drop.assert_awaited()
    cg.reindex_decisions.assert_awaited()               # decision projections overlaid
    assert summary == {"entities": 1, "relationships": 1, "decisions_reprojected": 4}
