"""Context Graph (CG) implementation built on top of LightRAG.

Extends the standard triple-based knowledge graph (h,r,t) to contextual quadruples
(h,r,t,rc) where rc is a RelationContext capturing temporal validity, quantitative
data, decision traces, provenance, and supporting evidence from source documents.

Also implements the CGR3 reasoning paradigm (Retrieve → Rank → Reason) for
iterative, context-aware question answering over the enriched graph.

Usage::

    from lightrag.context_graph import ContextGraph
    from lightrag.llm.openai import gpt_4o_mini_complete, openai_embed
    from lightrag import QueryParam

    async def main():
        cg = ContextGraph(
            working_dir="./cg_storage",
            llm_model_func=gpt_4o_mini_complete,
            embedding_func=openai_embed,
        )
        await cg.initialize_storages()

        # Ingest documents — relation contexts are extracted automatically
        await cg.ainsert("Your text with decisions, approvals, and context...")

        # Standard LightRAG query (relation_context enriches retrieved edges)
        result = await cg.aquery("Your question", param=QueryParam(mode="hybrid"))

        # CGR3 iterative reasoning
        answer = await cg.cgr3_query("Complex multi-hop question?")

        await cg.finalize_storages()
"""
from __future__ import annotations

import asyncio
import json
import time
from collections import defaultdict
from dataclasses import asdict
from typing import Any, Optional

from lightrag.base import BaseKVStorage, BaseVectorStorage, TextChunkSchema
from lightrag.context_graph_types import RelationContext
from lightrag.exceptions import PipelineCancelledException
from lightrag.lightrag import LightRAG
from lightrag.namespace import NameSpace
from lightrag.operate import (
    _handle_single_entity_extraction,
    _truncate_entity_identifier,
    merge_nodes_and_edges,
)
from lightrag.prompt import PROMPTS
from lightrag.utils import (
    compute_mdhash_id,
    create_prefixed_exception,
    fix_tuple_delimiter_corruption,
    logger,
    pack_user_ass_to_openai_messages,
    remove_think_tags,
    sanitize_and_normalize_extracted_text,
    split_string_by_multi_markers,
    update_chunk_cache_list,
    use_llm_func_with_cache,
)
from lightrag.constants import DEFAULT_ENTITY_NAME_MAX_LENGTH, DEFAULT_SUMMARY_LANGUAGE


# ─────────────────────────────────────────────────────────────────────────────
# Low-level extraction helpers
# ─────────────────────────────────────────────────────────────────────────────


def _extract_json_object(text: str) -> dict | None:
    """Best-effort extraction of a JSON object from an LLM response.

    LLMs frequently wrap JSON in a ``` or ```json code fence, or surround it
    with prose. This recovers the object in those cases and returns it as a
    dict, or ``None`` if no JSON object can be parsed (callers treat ``None``
    as 'unparseable' and fall back). Returning ``None`` for a non-object
    (scalar/array) avoids ``AttributeError`` on a later ``.get(...)``.
    """
    if not text:
        return None
    s = text.strip()
    # Strip a leading code fence + optional language tag, then drop the
    # trailing fence (and anything after it).
    if s.startswith("```"):
        s = s[3:]
        if s[:4].lower() == "json":
            s = s[4:]
        s = s.split("```", 1)[0].strip()
    try:
        obj = json.loads(s)
    except (json.JSONDecodeError, TypeError):
        # Fall back to the outermost {...} span.
        start, end = s.find("{"), s.rfind("}")
        if start == -1 or end <= start:
            return None
        try:
            obj = json.loads(s[start : end + 1])
        except (json.JSONDecodeError, TypeError):
            return None
    return obj if isinstance(obj, dict) else None


async def _handle_single_cg_relationship_extraction(
    record_attributes: list[str],
    chunk_key: str,
    timestamp: int,
    file_path: str = "unknown_source",
) -> dict | None:
    """Parse a single relation record, accepting 5 fields (standard) or 6 (with rc).

    Field layout for Context Graph:
        relation<|#|>src<|#|>tgt<|#|>keywords<|#|>description<|#|>RELATION_CONTEXT_JSON
    """
    n = len(record_attributes)
    if n not in (5, 6) or "relation" not in record_attributes[0]:
        if n > 1 and "relation" in record_attributes[0]:
            logger.warning(
                f"{chunk_key}: CG relation format error; got {n} fields on "
                f"`{record_attributes[1]}`~`{record_attributes[2] if n > 2 else 'N/A'}`"
            )
        return None

    try:
        from lightrag.utils import is_float_regex  # local to avoid circular import

        source = sanitize_and_normalize_extracted_text(
            record_attributes[1], remove_inner_quotes=True
        )
        target = sanitize_and_normalize_extracted_text(
            record_attributes[2], remove_inner_quotes=True
        )

        if not source:
            logger.info(
                f"Empty source entity after sanitization. Original: '{record_attributes[1]}'"
            )
            return None
        if not target:
            logger.info(
                f"Empty target entity after sanitization. Original: '{record_attributes[2]}'"
            )
            return None
        if source == target:
            return None

        edge_keywords = sanitize_and_normalize_extracted_text(
            record_attributes[3], remove_inner_quotes=True
        ).replace("，", ",")

        edge_description = sanitize_and_normalize_extracted_text(record_attributes[4])

        weight = (
            float(record_attributes[-1].strip('"').strip("'"))
            if is_float_regex(record_attributes[-1].strip('"').strip("'"))
            else 1.0
        )

        # Parse optional 6th field: relation context JSON
        relation_context_json: str | None = None
        if n == 6:
            raw_rc = record_attributes[5].strip()
            if raw_rc:
                # Validate / normalise the JSON
                try:
                    rc_dict = json.loads(raw_rc)
                    if isinstance(rc_dict, dict):
                        # Ensure confidence_score is a float between 0 and 1
                        cs = rc_dict.get("confidence_score", 1.0)
                        try:
                            cs = float(cs)
                        except (TypeError, ValueError):
                            cs = 1.0
                        rc_dict["confidence_score"] = min(max(cs, 0.0), 1.0)
                        relation_context_json = json.dumps(rc_dict, ensure_ascii=False)
                    else:
                        logger.warning(
                            f"{chunk_key}: Relation context is not a JSON object; ignored"
                        )
                except json.JSONDecodeError:
                    logger.warning(
                        f"{chunk_key}: Malformed relation context JSON for "
                        f"`{source}`→`{target}`; ignored"
                    )

        edge = dict(
            src_id=source,
            tgt_id=target,
            weight=weight,
            description=edge_description,
            keywords=edge_keywords,
            source_id=chunk_key,
            file_path=file_path,
            timestamp=timestamp,
        )
        if relation_context_json is not None:
            edge["relation_context"] = relation_context_json
        return edge

    except (ValueError, Exception) as e:
        logger.warning(
            f"CG relationship extraction failed in chunk {chunk_key}: {e}"
        )
        return None


async def _process_cg_extraction_result(
    result: str,
    chunk_key: str,
    timestamp: int,
    file_path: str = "unknown_source",
    tuple_delimiter: str = "<|#|>",
    completion_delimiter: str = "<|COMPLETE|>",
) -> tuple[dict, dict]:
    """Process a single CG extraction result (handles 5- and 6-field relations)."""
    maybe_nodes: dict = defaultdict(list)
    maybe_edges: dict = defaultdict(list)

    if completion_delimiter not in result:
        logger.warning(
            f"{chunk_key}: Completion delimiter not found in CG extraction result"
        )

    records = split_string_by_multi_markers(
        result,
        ["\n", completion_delimiter, completion_delimiter.lower()],
    )

    # Fix LLM output where tuple_delimiter is used as record separator
    fixed_records: list[str] = []
    for record in records:
        record = record.strip()
        if not record:
            continue
        entity_records = split_string_by_multi_markers(
            record, [f"{tuple_delimiter}entity{tuple_delimiter}"]
        )
        for er in entity_records:
            if not er.startswith("entity") and not er.startswith("relation"):
                er = f"entity<|{er}"
            parts = split_string_by_multi_markers(
                er,
                [
                    f"{tuple_delimiter}relationship{tuple_delimiter}",
                    f"{tuple_delimiter}relation{tuple_delimiter}",
                ],
            )
            for p in parts:
                if not p.startswith("entity") and not p.startswith("relation"):
                    p = f"relation{tuple_delimiter}{p}"
                fixed_records.append(p)

    for record in fixed_records:
        record = record.strip()
        if not record:
            continue

        delimiter_core = tuple_delimiter[2:-2]
        record = fix_tuple_delimiter_corruption(record, delimiter_core, tuple_delimiter)
        if delimiter_core != delimiter_core.lower():
            record = fix_tuple_delimiter_corruption(
                record, delimiter_core.lower(), tuple_delimiter
            )

        record_attributes = split_string_by_multi_markers(record, [tuple_delimiter])

        # Try entity first
        entity_data = await _handle_single_entity_extraction(
            record_attributes, chunk_key, timestamp, file_path
        )
        if entity_data is not None:
            truncated_name = _truncate_entity_identifier(
                entity_data["entity_name"],
                DEFAULT_ENTITY_NAME_MAX_LENGTH,
                chunk_key,
                "Entity name",
            )
            entity_data["entity_name"] = truncated_name
            maybe_nodes[truncated_name].append(entity_data)
            continue

        # Try CG relation (5 or 6 fields)
        rel_data = await _handle_single_cg_relationship_extraction(
            record_attributes, chunk_key, timestamp, file_path
        )
        if rel_data is not None:
            ts = _truncate_entity_identifier(
                rel_data["src_id"],
                DEFAULT_ENTITY_NAME_MAX_LENGTH,
                chunk_key,
                "Relation entity",
            )
            tt = _truncate_entity_identifier(
                rel_data["tgt_id"],
                DEFAULT_ENTITY_NAME_MAX_LENGTH,
                chunk_key,
                "Relation entity",
            )
            rel_data["src_id"] = ts
            rel_data["tgt_id"] = tt
            maybe_edges[(ts, tt)].append(rel_data)

    return dict(maybe_nodes), dict(maybe_edges)


# ─────────────────────────────────────────────────────────────────────────────
# CG entity extraction pipeline (mirrors operate.extract_entities)
# ─────────────────────────────────────────────────────────────────────────────


async def extract_entities_with_context(
    chunks: dict[str, TextChunkSchema],
    global_config: dict[str, str],
    pipeline_status: dict = None,
    pipeline_status_lock=None,
    llm_response_cache: BaseKVStorage | None = None,
    text_chunks_storage: BaseKVStorage | None = None,
) -> list:
    """CG variant of operate.extract_entities using contextual-quadruple prompts.

    Extracts entities and relationships from text chunks, enriching each
    relationship with a RelationContext (rc) JSON field that captures supporting
    evidence, temporal validity, quantitative data, decision traces, and provenance.

    The returned chunk_results list can be passed directly to
    ``operate.merge_nodes_and_edges`` — the standard merge pipeline transparently
    preserves the ``relation_context`` field via the updated
    ``_collect_relation_context`` helper in operate.py.
    """
    if pipeline_status is not None and pipeline_status_lock is not None:
        async with pipeline_status_lock:
            if pipeline_status.get("cancellation_requested", False):
                raise PipelineCancelledException(
                    "User cancelled during CG entity extraction"
                )

    use_llm_func: callable = global_config["llm_model_func"]
    entity_extract_max_gleaning = global_config["entity_extract_max_gleaning"]

    ordered_chunks = list(chunks.items())
    language = global_config["addon_params"].get("language", DEFAULT_SUMMARY_LANGUAGE)
    entity_types = global_config["addon_params"].get(
        "entity_types",
        ["Person", "Organization", "Location", "Event", "Concept", "Artifact"],
    )

    # Build CG example string
    cg_examples_list = PROMPTS.get("cg_entity_extraction_examples", [])
    example_context_base = dict(
        tuple_delimiter=PROMPTS["DEFAULT_TUPLE_DELIMITER"],
        completion_delimiter=PROMPTS["DEFAULT_COMPLETION_DELIMITER"],
        entity_types=", ".join(entity_types),
        language=language,
    )
    cg_examples = "\n".join(
        ex.format(**example_context_base) for ex in cg_examples_list
    )

    context_base = dict(
        tuple_delimiter=PROMPTS["DEFAULT_TUPLE_DELIMITER"],
        completion_delimiter=PROMPTS["DEFAULT_COMPLETION_DELIMITER"],
        entity_types=",".join(entity_types),
        examples=cg_examples,
        language=language,
    )

    processed_chunks = 0
    total_chunks = len(ordered_chunks)

    async def _process_single_content(
        chunk_key_dp: tuple[str, TextChunkSchema],
    ) -> tuple[dict, dict]:
        nonlocal processed_chunks
        chunk_key, chunk_dp = chunk_key_dp
        content = chunk_dp["content"]
        file_path = chunk_dp.get("file_path", "unknown_source")
        cache_keys_collector: list = []

        # Build prompts
        cg_system_prompt = PROMPTS["cg_entity_extraction_system_prompt"].format(
            **context_base
        )
        cg_user_prompt = PROMPTS["entity_extraction_user_prompt"].format(
            **{**context_base, "input_text": content}
        )
        cg_continue_prompt = PROMPTS["cg_entity_continue_extraction_user_prompt"].format(
            **{**context_base, "input_text": content}
        )

        final_result, timestamp = await use_llm_func_with_cache(
            cg_user_prompt,
            use_llm_func,
            system_prompt=cg_system_prompt,
            llm_response_cache=llm_response_cache,
            cache_type="extract",
            chunk_id=chunk_key,
            cache_keys_collector=cache_keys_collector,
        )

        history = pack_user_ass_to_openai_messages(cg_user_prompt, final_result)

        maybe_nodes, maybe_edges = await _process_cg_extraction_result(
            final_result,
            chunk_key,
            timestamp,
            file_path,
            tuple_delimiter=context_base["tuple_delimiter"],
            completion_delimiter=context_base["completion_delimiter"],
        )

        # Optional gleaning pass
        if entity_extract_max_gleaning > 0:
            tokenizer = global_config["tokenizer"]
            max_input_tokens = global_config["max_extract_input_tokens"]
            full_ctx = cg_system_prompt + json.dumps(history) + cg_continue_prompt
            if len(tokenizer.encode(full_ctx)) > max_input_tokens:
                logger.warning(
                    f"CG gleaning stopped for {chunk_key}: token limit exceeded"
                )
            else:
                glean_result, timestamp = await use_llm_func_with_cache(
                    cg_continue_prompt,
                    use_llm_func,
                    system_prompt=cg_system_prompt,
                    llm_response_cache=llm_response_cache,
                    history_messages=history,
                    cache_type="extract",
                    chunk_id=chunk_key,
                    cache_keys_collector=cache_keys_collector,
                )
                glean_nodes, glean_edges = await _process_cg_extraction_result(
                    glean_result,
                    chunk_key,
                    timestamp,
                    file_path,
                    tuple_delimiter=context_base["tuple_delimiter"],
                    completion_delimiter=context_base["completion_delimiter"],
                )
                for name, entities in glean_nodes.items():
                    if name in maybe_nodes:
                        orig_len = len(
                            maybe_nodes[name][0].get("description", "") or ""
                        )
                        glean_len = len(entities[0].get("description", "") or "")
                        if glean_len > orig_len:
                            maybe_nodes[name] = list(entities)
                    else:
                        maybe_nodes[name] = list(entities)
                for edge_key, edge_list in glean_edges.items():
                    if edge_key in maybe_edges:
                        orig_len = len(
                            maybe_edges[edge_key][0].get("description", "") or ""
                        )
                        glean_len = len(edge_list[0].get("description", "") or "")
                        if glean_len > orig_len:
                            maybe_edges[edge_key] = list(edge_list)
                    else:
                        maybe_edges[edge_key] = list(edge_list)

        if cache_keys_collector and text_chunks_storage:
            await update_chunk_cache_list(
                chunk_key,
                text_chunks_storage,
                cache_keys_collector,
                "cg_entity_extraction",
            )

        processed_chunks += 1
        logger.info(
            f"CG chunk {processed_chunks}/{total_chunks}: "
            f"{len(maybe_nodes)} entities, {len(maybe_edges)} relations — {chunk_key}"
        )
        if pipeline_status is not None:
            async with pipeline_status_lock:
                msg = (
                    f"CG extraction {processed_chunks}/{total_chunks}: "
                    f"{len(maybe_nodes)} ent + {len(maybe_edges)} rel"
                )
                pipeline_status["latest_message"] = msg
                pipeline_status["history_messages"].append(msg)

        return maybe_nodes, maybe_edges

    chunk_max_async = global_config.get("llm_model_max_async", 4)
    semaphore = asyncio.Semaphore(chunk_max_async)

    async def _process_with_semaphore(chunk):
        async with semaphore:
            if pipeline_status is not None and pipeline_status_lock is not None:
                async with pipeline_status_lock:
                    if pipeline_status.get("cancellation_requested", False):
                        raise PipelineCancelledException(
                            "User cancelled during CG chunk processing"
                        )
            try:
                return await _process_single_content(chunk)
            except Exception as e:
                chunk_id = chunk[0]
                raise create_prefixed_exception(e, chunk_id) from e

    tasks = [asyncio.create_task(_process_with_semaphore(c)) for c in ordered_chunks]
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)

    first_exception = None
    chunk_results: list = []
    for task in done:
        try:
            exc = task.exception()
            if exc is not None:
                first_exception = first_exception or exc
            else:
                chunk_results.append(task.result())
        except Exception as e:
            first_exception = first_exception or e

    if first_exception is not None:
        for t in pending:
            t.cancel()
        if pending:
            await asyncio.wait(pending)
        progress_prefix = f"C[{processed_chunks + 1}/{total_chunks}]"
        raise create_prefixed_exception(first_exception, progress_prefix) from first_exception

    return chunk_results


# ─────────────────────────────────────────────────────────────────────────────
# ContextGraph — main class
# ─────────────────────────────────────────────────────────────────────────────


class ContextGraph(LightRAG):
    """LightRAG extended with Context Graph (CG) capabilities.

    Key differences from standard LightRAG:

    1. **Contextual quadruples**: every extracted relationship is augmented with a
       ``RelationContext`` (rc) JSON field stored directly in the graph edge,
       capturing supporting evidence, temporal validity, quantitative data,
       decision traces, and provenance.

    2. **CG-aware extraction prompts**: the LLM is instructed to produce a compact
       JSON Relation Context object as the 6th field of each ``relation`` record.

    3. **CGR3 query method**: ``cgr3_query()`` implements the iterative
       Retrieve → Rank → Reason paradigm from the paper, looping until the LLM
       judges that sufficient context has been gathered (or max_iterations reached).

    All standard LightRAG functionality (ainsert, aquery, storage backends, etc.)
    is preserved and fully compatible.
    """

    # ------------------------------------------------------------------
    # Storage lifecycle overrides
    # ------------------------------------------------------------------

    def __post_init__(self) -> None:
        super().__post_init__()
        # decisions_vdb: vector index over decision_trace text for precedent search.
        # vector_db_storage_cls is already wrapped with partial(global_config=...)
        # by super().__post_init__(), so this call mirrors other VDB initialisations.
        self.decisions_vdb: BaseVectorStorage = self.vector_db_storage_cls(
            namespace=NameSpace.VECTOR_STORE_DECISIONS,
            workspace=self.workspace,
            embedding_func=self.embedding_func,
            meta_fields={"src_id", "tgt_id"},
        )

    async def initialize_storages(self) -> None:
        await super().initialize_storages()
        await self.decisions_vdb.initialize()

    async def finalize_storages(self) -> None:
        await super().finalize_storages()
        try:
            await self.decisions_vdb.finalize()
        except Exception as e:
            logger.error(f"Failed to finalize decisions_vdb: {e}")

    # ------------------------------------------------------------------
    # Extraction override
    # ------------------------------------------------------------------

    async def _process_extract_entities(
        self,
        chunk: dict[str, Any],
        pipeline_status=None,
        pipeline_status_lock=None,
    ) -> list:
        """Override: use CG extraction (contextual quadruples) instead of triples."""
        try:
            chunk_results = await extract_entities_with_context(
                chunk,
                global_config=asdict(self),
                pipeline_status=pipeline_status,
                pipeline_status_lock=pipeline_status_lock,
                llm_response_cache=self.llm_response_cache,
                text_chunks_storage=self.text_chunks,
            )
            return chunk_results
        except Exception as e:
            error_msg = f"CG extraction failed: {e}"
            logger.error(error_msg)
            if pipeline_status is not None and pipeline_status_lock is not None:
                async with pipeline_status_lock:
                    pipeline_status["latest_message"] = error_msg
                    pipeline_status["history_messages"].append(error_msg)
            raise

    # ------------------------------------------------------------------
    # Context Graph helpers
    # ------------------------------------------------------------------

    async def get_edge_context(
        self, src: str, tgt: str
    ) -> Optional[RelationContext]:
        """Retrieve the RelationContext for an edge, if present.

        Returns None if the edge does not exist or has no relation context.
        """
        if not await self.chunk_entity_relation_graph.has_edge(src, tgt):
            return None
        edge = await self.chunk_entity_relation_graph.get_edge(src, tgt)
        if edge is None:
            return None
        rc_json = edge.get("relation_context")
        if not rc_json:
            return None
        return RelationContext.from_json(rc_json)

    async def get_edges_with_context(
        self, entity: str
    ) -> list[dict[str, Any]]:
        """Return all edges connected to *entity* that carry a RelationContext.

        Each dict in the returned list contains all standard edge fields plus a
        parsed ``relation_context`` key holding a :class:`RelationContext` object.
        """
        if not await self.chunk_entity_relation_graph.has_node(entity):
            return []
        raw_edges = await self.chunk_entity_relation_graph.get_node_edges(entity)
        results = []
        for src, tgt in (raw_edges or []):
            edge = await self.chunk_entity_relation_graph.get_edge(src, tgt)
            if edge is None:
                continue
            rc_json = edge.get("relation_context")
            if rc_json:
                edge = dict(edge)
                edge["relation_context"] = RelationContext.from_json(rc_json)
                results.append(edge)
        return results

    # ------------------------------------------------------------------
    # Real-time decision capture
    # ------------------------------------------------------------------

    async def emit_decision_trace(
        self,
        src: str,
        tgt: str,
        relation_type: str,
        rc: RelationContext,
        *,
        upsert: bool = True,
    ) -> None:
        """Record a decision trace directly into the graph at execution time.

        Unlike ``ainsert()`` which extracts context from prose, this writes a
        structured :class:`RelationContext` to an edge — for agent orchestration
        code to call at the moment a decision is made.

        Args:
            src: Head entity name.
            tgt: Tail entity name.
            relation_type: Relationship keyword (stored as ``keywords``).
            rc: The :class:`RelationContext` capturing the decision.
            upsert: If True and the edge already exists, merge the new RC with
                the existing one rather than overwriting it.
        """
        # Ensure nodes exist in the graph with all fields the merge pipeline expects
        provenance = rc.provenance or "agent_runtime"
        for name in (src, tgt):
            await self.chunk_entity_relation_graph.upsert_node(
                name,
                {
                    "entity_id": name,
                    "entity_type": "ENTITY",
                    "source_id": "emit_decision_trace",
                    "description": name,
                    "file_path": provenance,
                },
            )

        # Merge with existing RC when upsert=True
        if upsert and await self.chunk_entity_relation_graph.has_edge(src, tgt):
            existing = await self.chunk_entity_relation_graph.get_edge(src, tgt)
            if existing and existing.get("relation_context"):
                existing_rc = RelationContext.from_json(existing["relation_context"])
                rc = RelationContext.merge([existing_rc, rc])

        edge_data = dict(
            keywords=relation_type,
            description=rc.decision_trace or relation_type,
            weight=rc.confidence_score,
            source_id="emit_decision_trace",
            file_path=rc.provenance or "agent_runtime",
            timestamp=int(time.time()),
            relation_context=rc.to_json(),
        )
        await self.chunk_entity_relation_graph.upsert_edge(src, tgt, edge_data=edge_data)

        # Index decision_trace text in decisions_vdb for later precedent search
        if rc.decision_trace:
            decision_id = compute_mdhash_id(f"{src}>{tgt}", prefix="dec-")
            await self.decisions_vdb.upsert(
                {
                    decision_id: {
                        "content": rc.decision_trace,
                        "src_id": src,
                        "tgt_id": tgt,
                    }
                }
            )

    # ------------------------------------------------------------------
    # Decision summary ingestion (makes decisions visible to /query)
    # ------------------------------------------------------------------

    async def ingest_decision_summary(
        self,
        text: str,
        *,
        category: str = "general",
        summary_id: str | None = None,
    ) -> str:
        """Ingest an aggregated decision summary as a standard document.

        Unlike :meth:`emit_decision_trace` which writes individual edges to
        ``decisions_vdb`` (only reachable via precedent search), this method
        pipes the text through the full ``ainsert`` pipeline — producing
        chunks, entity/relation embeddings, and graph nodes — so that the
        summary is discoverable by standard ``/query`` and CGR3 queries.

        The **caller** (domain-specific loader) is responsible for aggregating
        raw decisions into meaningful natural-language summaries.  This method
        is intentionally domain-agnostic.

        Args:
            text: Natural-language summary of a group of decisions.
            category: Grouping label (e.g. ``"by_vehicle"``, ``"by_outcome"``).
                Used in the ``file_path`` tag for later identification/cleanup.
            summary_id: Stable document ID.  When provided, an existing summary
                with the same ID is deleted before re-ingestion, giving upsert
                semantics.  If ``None``, an ID is derived from the text hash.

        Returns:
            The ``track_id`` from ``ainsert`` (for status polling).
        """
        from lightrag.utils import compute_mdhash_id

        file_path = f"decision_summary/{category}"
        doc_id = summary_id or compute_mdhash_id(text, prefix="dsum-")

        # Upsert: remove stale version if re-ingesting with the same ID
        if summary_id:
            existing = await self.full_docs.get_by_id(summary_id)
            if existing is not None:
                await self.full_docs.delete([summary_id])

        return await self.ainsert(
            text,
            ids=[doc_id],
            file_paths=[file_path],
        )

    # ------------------------------------------------------------------
    # Precedent search and decision enumeration
    # ------------------------------------------------------------------

    async def find_precedents(
        self,
        query_text: str,
        top_k: int = 10,
        min_confidence: float = 0.0,
    ) -> list[dict]:
        """Find past decisions semantically similar to *query_text*.

        Returns a ranked list of edges whose ``decision_trace`` is most similar,
        enabling queries like "find precedents for this type of exception."

        Args:
            query_text: Natural language description of the decision scenario.
            top_k: Maximum number of results to return.
            min_confidence: Only include edges with ``confidence_score`` ≥ this value.

        Returns:
            List of dicts, each with keys ``src_id``, ``tgt_id``, and
            ``relation_context`` (:class:`RelationContext` instance).
        """
        hits = await self.decisions_vdb.query(query_text, top_k=top_k * 2)
        results: list[dict] = []
        for hit in hits:
            src = hit.get("src_id")
            tgt = hit.get("tgt_id")
            if not src or not tgt:
                continue
            edge = await self.chunk_entity_relation_graph.get_edge(src, tgt)
            if edge is None:
                continue
            rc = RelationContext.from_json(edge.get("relation_context", "{}"))
            if rc.confidence_score < min_confidence:
                continue
            results.append(
                {
                    "src_id": src,
                    "tgt_id": tgt,
                    "relation_context": rc,
                }
            )
            if len(results) >= top_k:
                break
        return results

    async def get_all_decisions(
        self,
        approved_by: Optional[str] = None,
        approved_via: Optional[str] = None,
        policy_ref: Optional[str] = None,
        min_confidence: float = 0.0,
        active_as_of: Optional[str] = None,
    ) -> list[dict]:
        """Return all edges that carry a RelationContext with a ``decision_trace``.

        Optionally filter by structured approval-chain fields.

        Args:
            approved_by: Only include decisions approved by this entity name.
            approved_via: Only include decisions approved via this channel
                (``'slack'``, ``'zoom'``, ``'email'``, ``'in_person'``,
                ``'jira'``, ``'system'``).
            policy_ref: Only include decisions referencing this policy.
            min_confidence: Exclude edges below this confidence threshold.
            active_as_of: ISO-8601 date string; only include decisions that are
                valid on this date (uses :meth:`RelationContext.is_active`).

        Returns:
            List of dicts with keys ``src_id``, ``tgt_id``, and
            ``relation_context`` (:class:`RelationContext` instance).
        """
        all_edges = await self.chunk_entity_relation_graph.get_all_edges()
        # get_all_edges() returns dicts with "source" and "target" keys
        results: list[dict] = []
        for edge in all_edges:
            rc_json = edge.get("relation_context")
            if not rc_json:
                continue
            rc = RelationContext.from_json(rc_json)
            if not rc.decision_trace:
                continue
            if rc.confidence_score < min_confidence:
                continue
            if approved_by and rc.approved_by != approved_by:
                continue
            if approved_via and rc.approved_via != approved_via:
                continue
            if policy_ref and rc.policy_ref != policy_ref:
                continue
            if active_as_of and not rc.is_active(active_as_of):
                continue
            results.append(
                {
                    "src_id": edge.get("source"),
                    "tgt_id": edge.get("target"),
                    "relation_context": rc,
                }
            )
        return results

    # ------------------------------------------------------------------
    # CGR3 query
    # ------------------------------------------------------------------

    async def cgr3_query(
        self,
        query: str,
        mode: str = "hybrid",
        max_iterations: int = 3,
        top_k: int = 60,
    ) -> str:
        """CGR3 iterative reasoning: Retrieve → Rank → Reason.

        Implements an improved three-step paradigm:

        1. **Retrieve**: Use multiple retrieval strategies (the specified mode
           plus a vector/naive fallback) to gather entities, relations, their
           RelationContext, **and** raw text chunks.
        2. **Rank**: Ask the LLM to assess relevance and identify gaps in the
           accumulated context — no wasted ID-ranking step.
        3. **Reason**: Ask the LLM to synthesize an answer or identify what's
           missing for the next iteration.

        Args:
            query: Natural language question.
            mode: Primary retrieval mode (default ``"hybrid"``).
            max_iterations: Maximum Retrieve-Rank-Reason loops (default 3).
            top_k: Number of entities/relations retrieved per iteration.

        Returns:
            The final answer string generated by the LLM.
        """
        from lightrag.base import QueryParam

        llm_func = self.llm_model_func
        all_contexts: list[str] = []
        seen_context_hashes: set[int] = set()
        current_query = query

        for iteration in range(max_iterations):
            logger.info(
                f"CGR3 iteration {iteration + 1}/{max_iterations}: "
                f"'{current_query[:100]}'"
            )

            # ── Step 1: Retrieve (multi-strategy) ─────────────────────
            # Primary retrieval with the user's chosen mode
            param = QueryParam(
                mode=mode,
                top_k=top_k,
                only_need_context=True,
            )
            context_result = await self.aquery(current_query, param=param)
            primary_context: str = (
                context_result.content
                if hasattr(context_result, "content")
                else str(context_result)
            )

            # On first iteration, also do a naive (vector) retrieval to
            # catch terms that may not be graph entities (e.g. brand names,
            # product lines).
            if iteration == 0:
                try:
                    naive_param = QueryParam(
                        mode="naive",
                        top_k=min(top_k, 30),
                        only_need_context=True,
                    )
                    naive_result = await self.aquery(query, param=naive_param)
                    naive_context: str = (
                        naive_result.content
                        if hasattr(naive_result, "content")
                        else str(naive_result)
                    )
                except Exception as e:
                    logger.warning(f"CGR3 naive fallback failed: {e}")
                    naive_context = ""
            else:
                naive_context = ""

            # Deduplicate context across iterations
            new_parts = []
            for ctx in [primary_context, naive_context]:
                if not ctx or not ctx.strip():
                    continue
                ctx_hash = hash(ctx.strip()[:500])
                if ctx_hash not in seen_context_hashes:
                    seen_context_hashes.add(ctx_hash)
                    new_parts.append(ctx)

            if not new_parts and not all_contexts:
                logger.warning("CGR3: no context retrieved; stopping early")
                break
            elif not new_parts:
                logger.info("CGR3: no new context found; proceeding to answer")
                break

            iter_context = "\n\n".join(new_parts)
            all_contexts.append(iter_context)

            # ── Step 2+3: Combined Rank & Reason ──────────────────────
            # Merge into one LLM call to reduce latency and improve coherence
            full_context = "\n\n---\n\n".join(all_contexts)
            # Truncate to ~12k chars to stay within context limits
            if len(full_context) > 12000:
                full_context = full_context[:12000] + "\n...[truncated]"

            reason_prompt = PROMPTS["cgr3_reason_prompt"].format(
                query=query,
                context=full_context,
            )
            try:
                reason_response = await llm_func(reason_prompt)
                reason_response = remove_think_tags(reason_response)
            except Exception as e:
                logger.warning(f"CGR3 reason step failed (iter {iteration + 1}): {e}")
                break

            # Robustly recover the JSON object (handles ```/```json fences and
            # prose). Returns None for unparseable or non-object output, which
            # we treat as "stop iterating and synthesize a final answer".
            parsed = _extract_json_object(reason_response)
            if parsed is None:
                logger.warning(
                    f"CGR3 reason parse failed (iter {iteration + 1}): could not "
                    f"extract a JSON object. Falling back to final answer generation."
                )
                break

            if parsed.get("is_sufficient", False):
                answer = parsed.get("answer")
                if answer:
                    logger.info(
                        f"CGR3 sufficient after {iteration + 1} iteration(s)"
                    )
                    return answer
                break

            # Not sufficient — refine the query for next iteration
            follow_up_entities = parsed.get("follow_up_entities") or []
            missing_info = parsed.get("missing_info", "")
            if follow_up_entities:
                # Use the missing entities as the next query to maximize
                # retrieval relevance for the gap
                current_query = (
                    f"{' '.join(follow_up_entities)} {missing_info or query}"
                )
            elif missing_info:
                current_query = f"{missing_info} {query}"
            else:
                logger.info("CGR3: no follow-up info; stopping iteration early")
                break

        # ── Final answer generation ────────────────────────────────────
        # Use a full LightRAG query (with LLM synthesis) using accumulated
        # context as supplementary information in the user prompt.
        logger.info("CGR3: generating final answer from accumulated context")
        final_context = "\n\n---\n\n".join(all_contexts)
        # Keep a generous amount of context for the final synthesis
        max_context_chars = 10000
        if len(final_context) > max_context_chars:
            final_context = final_context[:max_context_chars] + "\n...[truncated]"

        final_param = QueryParam(
            mode=mode,
            top_k=top_k,
            user_prompt=(
                f"You have the following additional retrieval context gathered "
                f"across multiple iterations. Use it together with any freshly "
                f"retrieved data to give a comprehensive answer.\n\n"
                f"---Accumulated Context---\n{final_context}"
            ),
        )
        final_result = await self.aquery(query, param=final_param)
        return (
            final_result.content
            if hasattr(final_result, "content")
            else str(final_result)
        )
