from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest

from lightrag.lightrag import LightRAG
from lightrag.prepared_chunks import build_prepared_chunks, prepared_chunk_id

pytestmark = pytest.mark.offline


def engine():
    return SimpleNamespace(tokenizer=SimpleNamespace(encode=lambda s: list(s)),
                           chunk_token_size=100, apipeline_enqueue_documents=AsyncMock(),
                           apipeline_process_enqueue_documents=AsyncMock())


def test_revision_scoped_ids_and_exact_text():
    chunks = build_prepared_chunks(['Rule\nException', 'Rule\nException'], 'rev1', 'https://a.test/',
                                   engine().tokenizer, 100)
    assert len(chunks) == 1
    assert next(iter(chunks.values()))['content'] == 'Rule\nException'
    assert prepared_chunk_id('rev1', 'same') != prepared_chunk_id('rev2', 'same')


@pytest.mark.asyncio
async def test_explicit_chunks_only_apply_to_named_document():
    rag = engine()
    await LightRAG.ainsert(rag, 'Full source', ids=['rev1'], prepared_chunks=['Rule', 'Exception'])
    rag.apipeline_process_enqueue_documents.assert_awaited_once_with(
        None, False, prepared_chunks={'rev1': ['Rule', 'Exception']})


@pytest.mark.asyncio
async def test_legacy_api_does_not_change_chunking():
    rag = engine()
    await LightRAG.ainsert(rag, 'Full source')
    rag.apipeline_process_enqueue_documents.assert_awaited_once_with(None, False)


@pytest.mark.asyncio
async def test_source_vector_lane_requires_and_forwards_named_prepared_plan():
    rag = engine()
    with pytest.raises(ValueError):
        await LightRAG.ainsert(rag, 'Full source', prepared_index_graph=False)
    rag.apipeline_enqueue_documents.assert_not_awaited()
    await LightRAG.ainsert(rag, 'Full source', ids=['rev1'],
                          prepared_chunks=['Rule'], prepared_index_graph=False)
    rag.apipeline_process_enqueue_documents.assert_awaited_once_with(
        None, False, prepared_chunks={'rev1': ['Rule']}, prepared_index_graph=False)


@pytest.mark.asyncio
@pytest.mark.parametrize('ids,chunks', [(None, ['a']), (['a','b'],['a']), ([''],['a']),
                                       (['a'],[]), (['a'],[' ']), (['a'],['x'*101])])
async def test_invalid_plan_fails_before_enqueue(ids, chunks):
    rag = engine()
    with pytest.raises(ValueError):
        await LightRAG.ainsert(rag, 'Full source', ids=ids, prepared_chunks=chunks)
    rag.apipeline_enqueue_documents.assert_not_awaited()
