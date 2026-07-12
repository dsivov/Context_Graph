"""Context Graph (CG) implementation built on top of LightRAG.

Extends the standard triple-based knowledge graph (h,r,t) to contextual quadruples
(h,r,t,rc) where rc is a RelationContext capturing temporal validity, quantitative
data, decision traces, provenance, and supporting evidence from source documents.

Also implements the CGR3 reasoning paradigm (Retrieve → Rank → Reason) for
iterative, context-aware question answering over the enriched graph.

Usage::

    from context_graph.core import ContextGraph
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
import contextlib
import json
import time
from collections import defaultdict
from dataclasses import asdict
from typing import Any, Optional

from lightrag.base import BaseKVStorage, BaseVectorStorage, TextChunkSchema
from context_graph.types import RelationContext
from context_graph.jsonio import _extract_json_object
from lightrag.exceptions import PipelineCancelledException
from lightrag.lightrag import LightRAG
from lightrag.namespace import NameSpace
from lightrag.operate import (
    _handle_single_entity_extraction,
    _truncate_entity_identifier,
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
from lightrag.constants import (
    DEFAULT_ENTITY_NAME_MAX_LENGTH,
    DEFAULT_SUMMARY_LANGUAGE,
    GRAPH_FIELD_SEP,
)
from lightrag.kg.shared_storage import get_storage_keyed_lock


# ─────────────────────────────────────────────────────────────────────────────
# Low-level extraction helpers
# ─────────────────────────────────────────────────────────────────────────────


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
# JSON extraction (Step 4 prototype — upstream 1.5.x alignment)
# Behind CG_JSON_EXTRACTION; produces the SAME (maybe_nodes, maybe_edges) shape as
# the delimiter parser above, so everything downstream is unchanged. rc becomes a
# first-class JSON key instead of a delimited 6th field.
# ─────────────────────────────────────────────────────────────────────────────


def _rc_json_from_obj(raw_rc, chunk_key: str, src: str, tgt: str) -> str | None:
    """Normalise a relation_context (dict or JSON string) into a stored JSON string,
    clamping confidence_score to [0,1]. Returns None if absent or malformed."""
    if raw_rc is None or raw_rc == "":
        return None
    try:
        rc_dict = raw_rc if isinstance(raw_rc, dict) else json.loads(str(raw_rc))
    except (json.JSONDecodeError, TypeError):
        logger.warning(f"{chunk_key}: Malformed relation context for `{src}`→`{tgt}`; ignored")
        return None
    if not isinstance(rc_dict, dict):
        logger.warning(f"{chunk_key}: relation_context is not a JSON object; ignored")
        return None
    cs = rc_dict.get("confidence_score", 1.0)
    try:
        cs = float(cs)
    except (TypeError, ValueError):
        cs = 1.0
    rc_dict["confidence_score"] = min(max(cs, 0.0), 1.0)
    return json.dumps(rc_dict, ensure_ascii=False)


async def _process_cg_json_result(
    parsed, chunk_key: str, timestamp: int, file_path: str = "unknown_source"
) -> tuple[dict, dict]:
    """Convert a parsed ``{"entities": [...], "relationships": [...]}`` dict into the
    same ``(maybe_nodes, maybe_edges)`` structure the delimiter parser produces.
    Field names are accepted flexibly (entity_name/name, src_id/source, etc.)."""
    maybe_nodes: dict = defaultdict(list)
    maybe_edges: dict = defaultdict(list)
    if not isinstance(parsed, dict):
        logger.warning(f"{chunk_key}: JSON extraction result is not an object")
        return dict(maybe_nodes), dict(maybe_edges)

    for ent in parsed.get("entities") or []:
        if not isinstance(ent, dict):
            continue
        name = sanitize_and_normalize_extracted_text(
            str(ent.get("entity_name") or ent.get("name") or ""), remove_inner_quotes=True
        )
        if not name:
            continue
        name = _truncate_entity_identifier(
            name, DEFAULT_ENTITY_NAME_MAX_LENGTH, chunk_key, "Entity name"
        )
        etype = sanitize_and_normalize_extracted_text(
            str(ent.get("entity_type") or ent.get("type") or "UNKNOWN")
        )
        desc = sanitize_and_normalize_extracted_text(str(ent.get("description") or ""))
        maybe_nodes[name].append({
            "entity_name": name,
            "entity_type": etype or "UNKNOWN",
            "description": desc,
            "source_id": chunk_key,
            "file_path": file_path,
            "timestamp": timestamp,
        })

    for rel in parsed.get("relationships") or parsed.get("relations") or []:
        if not isinstance(rel, dict):
            continue
        src = sanitize_and_normalize_extracted_text(
            str(rel.get("src_id") or rel.get("source") or rel.get("source_entity") or ""),
            remove_inner_quotes=True,
        )
        tgt = sanitize_and_normalize_extracted_text(
            str(rel.get("tgt_id") or rel.get("target") or rel.get("target_entity") or ""),
            remove_inner_quotes=True,
        )
        if not src or not tgt or src == tgt:
            continue
        src = _truncate_entity_identifier(src, DEFAULT_ENTITY_NAME_MAX_LENGTH, chunk_key, "Relation entity")
        tgt = _truncate_entity_identifier(tgt, DEFAULT_ENTITY_NAME_MAX_LENGTH, chunk_key, "Relation entity")
        kw = sanitize_and_normalize_extracted_text(
            str(rel.get("keywords") or rel.get("relationship_keywords") or ""),
            remove_inner_quotes=True,
        ).replace("，", ",")
        desc = sanitize_and_normalize_extracted_text(
            str(rel.get("description") or rel.get("relationship_description") or "")
        )
        try:
            weight = float(rel.get("weight", 1.0))
        except (TypeError, ValueError):
            weight = 1.0
        edge = {
            "src_id": src, "tgt_id": tgt, "weight": weight,
            "description": desc, "keywords": kw,
            "source_id": chunk_key, "file_path": file_path, "timestamp": timestamp,
        }
        rc = _rc_json_from_obj(rel.get("relation_context"), chunk_key, src, tgt)
        if rc is not None:
            edge["relation_context"] = rc
        maybe_edges[(src, tgt)].append(edge)

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
                            # Prefer the longer glean description, but do not drop a
                            # relation_context the first pass captured if this glean
                            # pass re-emitted the edge without a 6th field — otherwise
                            # decision lineage silently disappears on the glean pass.
                            new_list = list(edge_list)
                            orig_rc = maybe_edges[edge_key][0].get("relation_context")
                            if orig_rc and not new_list[0].get("relation_context"):
                                new_list[0] = dict(new_list[0])
                                new_list[0]["relation_context"] = orig_rc
                            maybe_edges[edge_key] = new_list
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
        # communities_vdb: vectors over community summaries for the thematic
        # "global" mode (Topic 3, Layer 4). Retrieval index; the full listing lives
        # in the JSON community store.
        self.communities_vdb: BaseVectorStorage = self.vector_db_storage_cls(
            namespace=NameSpace.VECTOR_STORE_COMMUNITIES,
            workspace=self.workspace,
            embedding_func=self.embedding_func,
            meta_fields={"community_id", "title", "size"},
        )
        self._community_store = None  # lazily created
        # Optional pre-emit rules gate (context_graph.rules.RulesGate). When set,
        # emit_decision_trace evaluates it before persisting. None → no-op.
        self.rules_gate = None
        # Entity deduplication (Graph-Quality v-next). The store is cheap and
        # inert until a scan/sweep is invoked, so this touches no ingest path.
        self._dedup_store = None  # lazily created (needs working_dir)
        # Garbage filtering (Topic 2): quarantine store + cached node filter.
        self._quarantine_store = None
        self._node_filter_cache = None
        # Per-task LLM roles (upstream 1.5.x alignment). None → fall back to the
        # single llm_model_func. The server attaches role-bound callables built
        # from EXTRACT_/QUERY_ env config; see attach_llm_roles.
        self._llm_role_extract = None
        self._llm_role_query = None

    # ------------------------------------------------------------------
    # Per-task LLM roles
    # ------------------------------------------------------------------

    def attach_llm_roles(self, *, extract=None, query=None) -> None:
        """Wire per-task LLM callables. Each is an async ``(prompt, system_prompt=…,
        **kwargs) -> str`` with the same contract as ``llm_model_func``. Unset roles
        keep falling back to ``llm_model_func``, so this is fully backward‑compatible."""
        if extract is not None:
            self._llm_role_extract = extract
        if query is not None:
            self._llm_role_query = query

    @property
    def _llm_extract(self):
        """LLM for high‑volume extraction / verification (cheap, fast role).
        getattr keeps this safe when built via __new__ (tests) — no attr → default."""
        return getattr(self, "_llm_role_extract", None) or self.llm_model_func

    @property
    def _llm_query(self):
        """LLM for reasoning / synthesis (strong role): CGR3, community & decision
        synthesis, blended query."""
        return getattr(self, "_llm_role_query", None) or self.llm_model_func

    async def initialize_storages(self) -> None:
        await super().initialize_storages()
        await self.decisions_vdb.initialize()
        await self.communities_vdb.initialize()

    async def finalize_storages(self) -> None:
        await super().finalize_storages()
        for name, store in (("decisions_vdb", self.decisions_vdb),
                            ("communities_vdb", self.communities_vdb)):
            try:
                await store.finalize()
            except Exception as e:
                logger.error(f"Failed to finalize {name}: {e}")

    async def _insert_done(
        self, pipeline_status=None, pipeline_status_lock=None
    ) -> None:
        # The base persists a hard-coded storage list; decisions_vdb is ours, so
        # flush it alongside so pipeline-driven writes (e.g. ingest_decision_summary)
        # reach disk. Runtime emits flush via _persist_decision_indices() directly.
        await super()._insert_done(pipeline_status, pipeline_status_lock)
        try:
            await self.decisions_vdb.index_done_callback()
        except Exception as e:
            logger.error(f"Failed to persist decisions_vdb: {e}")

    async def _persist_decision_indices(self) -> None:
        """Flush the decision edge and its derived indices to disk.

        emit_decision_trace / reindex_decisions write outside the ingestion
        pipeline, so nothing else calls index_done_callback() on these stores.
        Without this, a file-based graph backend (NetworkX) keeps the new edge in
        memory only and NanoVectorDB keeps the vectors in memory only — both lost
        on restart. The graph is flushed first because it is the source of truth;
        the two vector indices are derived and rebuildable via reindex_decisions().
        """
        stores = (
            self.chunk_entity_relation_graph,
            self.decisions_vdb,
            self.relationships_vdb,
        )
        for store in stores:
            try:
                await store.index_done_callback()
            except Exception as e:  # pragma: no cover - persistence must not break emit
                name = getattr(store, "namespace", type(store).__name__)
                logger.warning(f"Failed to persist decision store {name}: {e}")

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
            # Route extraction through the EXTRACT role (cheap/fast) when configured;
            # extract_entities_with_context reads global_config["llm_model_func"].
            gc = asdict(self)
            gc["llm_model_func"] = self._llm_extract
            chunk_results = await extract_entities_with_context(
                chunk,
                global_config=gc,
                pipeline_status=pipeline_status,
                pipeline_status_lock=pipeline_status_lock,
                llm_response_cache=self.llm_response_cache,
                text_chunks_storage=self.text_chunks,
            )
            # Garbage filter (Topic 2): quarantine low-quality / off-schema nodes
            # before they reach the merge. Covers the gleaning pass for free, since
            # its nodes are already folded into chunk_results here.
            return self._filter_extracted(chunk_results)
        except Exception as e:
            error_msg = f"CG extraction failed: {e}"
            logger.error(error_msg)
            if pipeline_status is not None and pipeline_status_lock is not None:
                async with pipeline_status_lock:
                    pipeline_status["latest_message"] = error_msg
                    pipeline_status["history_messages"].append(error_msg)
            raise

    # ------------------------------------------------------------------
    # Garbage filtering (Graph-Quality v-next, Topic 2)
    # ------------------------------------------------------------------

    @property
    def quarantine_store(self):
        """Lazily-created per-workspace store of rejected nodes (restorable, D12)."""
        if self._quarantine_store is None:
            import os
            from context_graph.quality import JsonQuarantineStore
            self._quarantine_store = JsonQuarantineStore(
                os.path.join(self.working_dir, "quarantine")
            )
        return self._quarantine_store

    def _garbage_filter_enabled(self) -> bool:
        import os
        return os.getenv("GARBAGE_FILTER_ENABLED", "true").strip().lower() not in (
            "false", "0", "no", "off", "")

    def _node_filter(self):
        """Build (and cache) the workspace NodeFilter: its saved ontology if any,
        else DEFAULT_ENTITY_TYPES (D11); open-world unless GARBAGE_CLOSED_WORLD=true (D10)."""
        if self._node_filter_cache is not None:
            return self._node_filter_cache
        import os
        from context_graph.quality import NodeFilter
        try:
            from context_graph.ontology.store import JsonOntologyStore
            onto = JsonOntologyStore(
                os.path.join(self.working_dir, "ontology")
            ).load(self.workspace)
        except Exception:
            onto = None
        closed = os.getenv("GARBAGE_CLOSED_WORLD", "false").strip().lower() in (
            "true", "1", "yes", "on")
        self._node_filter_cache = NodeFilter(onto, closed_world=closed)
        return self._node_filter_cache

    def _filter_extracted(self, chunk_results: list) -> list:
        """Quarantine low-quality / off-schema nodes across chunk results, dropping
        them and any edges that touch them. Rejects go to the quarantine store
        (restorable). No-op when GARBAGE_FILTER_ENABLED=false."""
        if not self._garbage_filter_enabled():
            return chunk_results
        nf = self._node_filter()
        rejected: list[dict] = []
        for maybe_nodes, maybe_edges in chunk_results:
            for name in list(maybe_nodes.keys()):
                recs = maybe_nodes.get(name) or []
                rep = recs[0] if recs else {}
                reason = nf.check(
                    name, rep.get("description", ""), rep.get("entity_type", "")
                )
                if not reason:
                    continue
                rejected.append({
                    "entity_name": name,
                    "entity_type": rep.get("entity_type", ""),
                    "description": (rep.get("description") or "")[:300],
                    "reason": reason,
                })
                del maybe_nodes[name]
                for ek in list(maybe_edges.keys()):
                    if isinstance(ek, (tuple, list)) and name in (ek[0], ek[1]):
                        del maybe_edges[ek]
        if rejected:
            try:
                self.quarantine_store.add(self.workspace, rejected)
            except Exception as e:  # pragma: no cover - never break ingest
                logger.warning(f"quarantine store add failed: {e}")
            logger.info(f"CG garbage filter: quarantined {len(rejected)} node(s)")
        return chunk_results

    async def _remove_entity(self, name: str) -> None:
        """Delete a node and its incident edges from the graph + entity vector index."""
        await self.chunk_entity_relation_graph.delete_node(name)
        try:
            await self.entities_vdb.delete([compute_mdhash_id(name, prefix="ent-")])
        except Exception:  # pragma: no cover - best-effort vdb cleanup
            pass

    async def scan_garbage(self, *, apply: bool = True, limit: int = 100000) -> dict:
        """Retroactively clean an existing graph of garbage nodes (Topic 2).

        Runs the same node-quality filter over every existing entity and, for each
        reject, quarantines it (restorable) and removes it from the graph. ``apply``
        false is a preview (reports what would go, changes nothing). Returns a summary
        with counts by reason and a small sample.
        """
        if not self._garbage_filter_enabled():
            return {"disabled": True}
        # Retroactive DELETION uses only the deterministic gate (name-based garbage +
        # empty description) — NOT the ontology validator. A node that merely misses a
        # required property is real-but-incomplete; deleting it would lose signal.
        from context_graph.quality import quality_check
        graph = self.chunk_entity_relation_graph
        labels = list(await graph.get_all_labels() or [])[:limit]
        summary = {"scanned": 0, "quarantined": 0, "removed": 0,
                   "by_reason": {}, "sample": []}
        rejected: list[dict] = []
        for name in labels:
            summary["scanned"] += 1
            node = await graph.get_node(name) or {}
            verdict = quality_check(name, node.get("description", ""),
                                    node.get("entity_type", ""))
            reason = None if verdict.ok else verdict.reason
            if not reason:
                continue
            summary["quarantined"] += 1
            summary["by_reason"][reason] = summary["by_reason"].get(reason, 0) + 1
            if len(summary["sample"]) < 15:
                summary["sample"].append({"name": name, "reason": reason})
            rejected.append({
                "entity_name": name, "entity_type": node.get("entity_type", ""),
                "description": (node.get("description") or "")[:300], "reason": reason,
            })
            if apply:
                try:
                    await self._remove_entity(name)
                    summary["removed"] += 1
                except Exception as e:  # pragma: no cover
                    logger.warning(f"garbage remove {name} failed: {e}")
        # Preview (apply=false) must not mutate: only quarantine what we actually removed.
        if apply and rejected:
            try:
                self.quarantine_store.add(self.workspace, rejected)
            except Exception as e:  # pragma: no cover
                logger.warning(f"quarantine add failed: {e}")
        if apply and summary["removed"]:
            try:
                await self.chunk_entity_relation_graph.index_done_callback()
                await self.entities_vdb.index_done_callback()
            except Exception as e:  # pragma: no cover
                logger.warning(f"garbage scan persist failed: {e}")
        logger.info(f"scan_garbage: {{'scanned': {summary['scanned']}, "
                    f"'quarantined': {summary['quarantined']}, 'removed': {summary['removed']}}}")
        return summary

    # ------------------------------------------------------------------
    # Connectivity — isolate rescue (Graph-Quality v-next, Topic 3)
    # ------------------------------------------------------------------

    async def _isolated_nodes(self, limit: int) -> list[dict]:
        """Return up to *limit* degree-0 nodes as ``{name, description}``."""
        graph = self.chunk_entity_relation_graph
        labels = list(await graph.get_all_labels() or [])
        degree = {n: 0 for n in labels}
        for e in await graph.get_all_edges() or []:
            a, b = e.get("source"), e.get("target")
            if a in degree:
                degree[a] += 1
            if b in degree:
                degree[b] += 1
        out = []
        for name in labels:
            if degree.get(name, 0) != 0:
                continue
            node = await graph.get_node(name) or {}
            out.append({
                "name": name,
                "description": (node.get("description") or "").split(GRAPH_FIELD_SEP)[0].strip(),
            })
            if len(out) >= limit:
                break
        return out

    async def rescue_isolates(self, *, apply: bool = True, limit: int = 50,
                              max_candidates: int = 8) -> dict:
        """Layer 3 — reconnect isolated nodes with LLM-verified real edges (D14).

        For each degree-0 node, embedding proposes near existing nodes and the LLM
        adds only relationships their descriptions support. ``apply=false`` previews
        the isolates without calling the LLM or adding edges. Off the ingest path.
        """
        from context_graph.connectivity import IsolateRescue

        isolates = await self._isolated_nodes(limit)
        if not apply:
            return {"isolates": len(isolates), "preview": True,
                    "sample": [i["name"] for i in isolates[:15]]}

        graph = self.chunk_entity_relation_graph

        async def find_candidates(name: str, desc: str):
            q = f"{name}\n{desc}" if desc else name
            try:
                hits = await self.entities_vdb.query(q, top_k=max_candidates + 4) or []
            except Exception:
                hits = []
            cands = []
            for h in hits:
                cn = h.get("entity_name") or h.get("id")
                if not cn or cn == name:
                    continue
                cnode = await graph.get_node(cn) or {}
                cands.append({
                    "name": cn,
                    "description": (cnode.get("description") or "").split(GRAPH_FIELD_SEP)[0].strip(),
                })
            return cands

        async def add_edge(src: str, tgt: str, keywords: str, description: str):
            await self.acreate_relation(src, tgt, {
                "keywords": keywords, "weight": 1.0,
                "description": description or keywords, "source_id": "isolate_rescue",
            })

        rescue = IsolateRescue(
            self._llm_extract, find_candidates=find_candidates,
            add_edge=add_edge, max_candidates=max_candidates,
        )
        result = await rescue.rescue(isolates)
        result["isolates_scanned"] = len(isolates)
        try:
            await graph.index_done_callback()
            await self.relationships_vdb.index_done_callback()
        except Exception as e:  # pragma: no cover
            logger.warning(f"isolate rescue persist failed: {e}")
        return result

    async def prune_isolates(self, *, apply: bool = False, limit: int = 100000) -> dict:
        """Propose-only pruning of degree-0 isolates (D13). Run *after* connectivity
        repair, so candidates are the isolates rescue couldn't connect.

        Default is a preview — ``apply=false`` lists candidates and changes nothing.
        Only degree-1 leaves are never touched (they are real single-relationship
        entities). When applied, nodes are moved to the restorable quarantine, never
        hard-deleted, in keeping with the never-lose-data ethos.
        """
        graph = self.chunk_entity_relation_graph
        isolates = await self._isolated_nodes(limit)
        summary = {"isolates": len(isolates), "removed": 0, "preview": not apply,
                   "sample": [i["name"] for i in isolates[:20]]}
        if not apply:
            return summary
        rejected: list[dict] = []
        for iso in isolates:
            name = iso["name"]
            node = await graph.get_node(name) or {}
            rejected.append({
                "entity_name": name, "entity_type": node.get("entity_type", ""),
                "description": (node.get("description") or "")[:300],
                "reason": "low-degree isolate (pruned)",
            })
            try:
                await self._remove_entity(name)
                summary["removed"] += 1
            except Exception as e:  # pragma: no cover
                logger.warning(f"prune isolate {name} failed: {e}")
        if rejected:
            try:
                self.quarantine_store.add(self.workspace, rejected)
            except Exception as e:  # pragma: no cover
                logger.warning(f"quarantine add failed: {e}")
        if summary["removed"]:
            try:
                await graph.index_done_callback()
                await self.entities_vdb.index_done_callback()
            except Exception as e:  # pragma: no cover
                logger.warning(f"prune persist failed: {e}")
        logger.info(f"prune_isolates: {{'isolates': {summary['isolates']}, "
                    f"'removed': {summary['removed']}}}")
        return summary

    # ------------------------------------------------------------------
    # Community "global" mode (Graph-Quality v-next, Topic 3, Layer 4)
    # ------------------------------------------------------------------

    @property
    def community_store(self):
        """Lazily-created per-workspace store of community records (enumerable)."""
        if self._community_store is None:
            import os
            from context_graph.community import JsonCommunityStore
            self._community_store = JsonCommunityStore(
                os.path.join(self.working_dir, "community")
            )
        return self._community_store

    async def build_communities(self, *, min_size: int = 3,
                                max_communities: int = 300) -> dict:
        """Detect communities (Louvain), summarise each with the LLM, and index the
        summaries for the thematic "global" mode. Authoritative rebuild."""
        from context_graph.community import detect_communities, CommunitySummarizer

        graph = self.chunk_entity_relation_graph
        labels = list(await graph.get_all_labels() or [])
        edges = list(await graph.get_all_edges() or [])
        comms = detect_communities(labels, edges, min_size=min_size)[:max_communities]
        summarizer = CommunitySummarizer(self._llm_query)
        try:
            await self.communities_vdb.drop()
        except Exception:
            pass
        records: list[dict] = []
        vdb_payload: dict = {}
        for i, members in enumerate(comms):
            cid = f"comm-{i}"
            member_dicts = []
            for name in members:
                node = await graph.get_node(name) or {}
                member_dicts.append({
                    "name": name, "type": node.get("entity_type"),
                    "description": (node.get("description") or "").split(GRAPH_FIELD_SEP)[0].strip(),
                })
            s = await summarizer.summarize(member_dicts)
            records.append({"id": cid, "title": s["title"], "summary": s["summary"],
                            "members": members, "size": len(members)})
            vdb_payload[cid] = {
                "content": f"{s['title']}\n{s['summary']}",
                "community_id": cid, "title": s["title"], "size": len(members),
            }
        if vdb_payload:
            await self.communities_vdb.upsert(vdb_payload)
            try:
                await self.communities_vdb.index_done_callback()
            except Exception as e:  # pragma: no cover
                logger.warning(f"communities_vdb persist failed: {e}")
        self.community_store.replace(self.workspace, records)
        summary = {"communities": len(records),
                   "members_covered": sum(r["size"] for r in records)}
        logger.info(f"build_communities: {summary}")
        return summary

    async def community_query(self, query: str, *, top_k: int = 5) -> dict:
        """Thematic "global" answer: retrieve the most relevant community summaries and
        synthesise a holistic answer over them (a real community-summary global mode)."""
        hits = await self.communities_vdb.query(query, top_k=top_k) or []
        used, blocks = [], []
        for h in hits:
            cid = h.get("community_id")
            rec = self.community_store.get(self.workspace, cid) if cid else None
            title = (rec or {}).get("title") or h.get("title") or cid
            summary = (rec or {}).get("summary") or ""
            used.append({"community_id": cid, "title": title})
            blocks.append(f"## {title}\n{summary}")
        if not blocks:
            return {"response": "No communities built yet — run build_communities first.",
                    "communities": []}
        prompt = (
            "Answer the question using these knowledge-graph community summaries as "
            "context. Be holistic — draw on the relevant themes and name them.\n\n"
            f"COMMUNITIES:\n{chr(10).join(blocks)}\n\n---\nQuestion: {query}"
        )
        try:
            answer = await self._llm_query(prompt)
        except Exception as e:  # pragma: no cover
            answer = f"(LLM error: {e})"
        return {"response": answer, "communities": used}

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
    ) -> Optional[Any]:
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

        Returns:
            The :class:`~context_graph.rules.gate.GateDecision` if a
            ``rules_gate`` is attached (outcome PASS/FLAG and its audit record),
            else ``None``. On FLAG the edge is persisted with ``needs_review``.

        Raises:
            RuleViolation: if the attached gate REJECTs the decision (nothing is
                persisted). Callers/endpoints should map this to HTTP 422.
        """
        from context_graph.rules.gate import RulesGate, RuleViolation

        # Serialize the whole read-merge-write on this edge under the same per-edge
        # lock the extraction pipeline uses (get_storage_keyed_lock, GraphDB
        # namespace). Without it two concurrent emits on the same (src,tgt) both read
        # the old rc, both merge, and the last upsert wins — silently dropping one
        # decision's lineage. Degrades to a no-op lock when shared storage is not
        # initialized (library use / unit tests).
        async with self._graph_edge_lock(src, tgt):
            # Merge with existing RC when upsert=True. Done first (read) so the gate
            # below evaluates the FINAL context the edge will carry.
            if upsert and await self.chunk_entity_relation_graph.has_edge(src, tgt):
                existing = await self.chunk_entity_relation_graph.get_edge(src, tgt)
                if existing and existing.get("relation_context"):
                    existing_rc = RelationContext.from_json(existing["relation_context"])
                    # New decision first so it wins on scalar fields (merge is
                    # first-non-None-wins) — mirrors the extraction pipeline's
                    # new-over-existing convention in _collect_relation_context. The
                    # newest approval/validity/trace supersedes the prior one, while
                    # supporting_sentences still union and confidence takes the max.
                    rc = RelationContext.merge([rc, existing_rc])

            # ── Pre-emit rules gate (wiring step 5) ──────────────────────────
            # If a RulesGate is attached, evaluate before any write. REJECT raises
            # here (nothing persisted); FLAG annotates the edge with needs_review.
            # Guarded by isinstance so a default/mocked attribute is a no-op.
            gate_decision = None
            gate = getattr(self, "rules_gate", None)
            if isinstance(gate, RulesGate):
                gate_decision = gate.check(src, tgt, relation_type, rc)
                if gate_decision.blocked:
                    raise RuleViolation(gate_decision)

            # Ensure the endpoint nodes exist — but never clobber a richer, already
            # extracted node. upsert_node does SET n += props (Neo4j) / add_node
            # overwrite (NetworkX), so writing description=name/entity_type=ENTITY onto
            # an existing entity would erase its extracted profile. Only create when absent.
            provenance = rc.provenance or "agent_runtime"
            for name in (src, tgt):
                if await self.chunk_entity_relation_graph.get_node(name) is not None:
                    continue
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

            edge_data = dict(
                keywords=relation_type,
                description=rc.decision_trace or relation_type,
                weight=rc.confidence_score,
                source_id="emit_decision_trace",
                file_path=rc.provenance or "agent_runtime",
                timestamp=int(time.time()),
                relation_context=rc.to_json(),
            )
            if gate_decision is not None and gate_decision.flagged:
                edge_data["needs_review"] = True
                edge_data["rules_audit"] = json.dumps(
                    gate_decision.audit, ensure_ascii=False
                )
            await self.chunk_entity_relation_graph.upsert_edge(
                src, tgt, edge_data=edge_data
            )

            # Project the decision into the search fabrics. Both are DERIVED from the
            # edge written above (the single source of truth) and are rebuildable via
            # reindex_decisions(): relationships_vdb so ordinary /query retrieval
            # surfaces the decision, and decisions_vdb for semantic precedent search.
            if rc.decision_trace:
                await self._index_decision(src, tgt, relation_type, rc)
                await self._persist_decision_indices()

        return gate_decision

    def _graph_edge_lock(self, src: str, tgt: str):
        """Per-edge lock matching the extraction pipeline (operate.py merge path).

        Returns the shared keyed lock for this edge so concurrent writers serialize.
        Falls back to a no-op context when shared storage is not initialized (library
        use without the server, or unit tests with mocked instances).
        """
        workspace = getattr(self, "workspace", "") or ""
        namespace = f"{workspace}:GraphDB" if workspace else "GraphDB"
        try:
            return get_storage_keyed_lock(sorted([src, tgt]), namespace=namespace)
        except RuntimeError:
            return contextlib.nullcontext()

    # ------------------------------------------------------------------
    # Connectivity report (Graph-Quality v-next, Phase 0)
    # ------------------------------------------------------------------

    async def connectivity_report(self, *, sample_isolates: int = 20) -> dict:
        """Measure how connected the knowledge graph is (backend-agnostic).

        The baseline metric for the graph-quality work: a fragmented or isolate-heavy
        graph is what deduplication, garbage filtering and the connectivity pass are
        meant to move. Computed from ``get_all_labels`` + ``get_all_edges`` (which
        every backend implements) via union-find, so it needs no backend changes.

        Returns node/edge counts, isolate count and %, connected-component count,
        the largest component's share, a degree summary, and a small isolate sample.
        """
        graph = self.chunk_entity_relation_graph
        labels: list[str] = list(await graph.get_all_labels() or [])
        edges: list[dict] = list(await graph.get_all_edges() or [])
        n = len(labels)
        if n == 0:
            return {
                "total_nodes": 0, "total_edges": 0, "isolated_nodes": 0,
                "isolated_pct": 0.0, "connected_components": 0,
                "largest_component_size": 0, "largest_component_pct": 0.0,
                "degree": {"mean": 0.0, "median": 0.0, "max": 0, "degree0": 0, "degree1": 0},
                "isolate_sample": [],
            }

        # Union-find over the node set; self-loops / dangling endpoints ignored.
        parent = {name: name for name in labels}

        def find(x):
            root = x
            while parent[root] != root:
                root = parent[root]
            while parent[x] != root:  # path compression
                parent[x], x = root, parent[x]
            return root

        degree: dict[str, int] = {name: 0 for name in labels}
        edge_count = 0
        for e in edges:
            a, b = e.get("source"), e.get("target")
            if a not in parent or b not in parent or a == b:
                continue
            edge_count += 1
            degree[a] += 1
            degree[b] += 1
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

        # Component sizes.
        comp_size: dict[str, int] = {}
        for name in labels:
            r = find(name)
            comp_size[r] = comp_size.get(r, 0) + 1
        num_components = len(comp_size)
        largest = max(comp_size.values()) if comp_size else 0

        degs = sorted(degree.values())
        deg0 = sum(1 for d in degs if d == 0)
        deg1 = sum(1 for d in degs if d == 1)
        mean_deg = sum(degs) / n
        median_deg = float(degs[n // 2] if n % 2 else (degs[n // 2 - 1] + degs[n // 2]) / 2)
        isolate_sample = [name for name in labels if degree[name] == 0][:sample_isolates]

        return {
            "total_nodes": n,
            "total_edges": edge_count,
            "isolated_nodes": deg0,
            "isolated_pct": round(100.0 * deg0 / n, 2),
            "connected_components": num_components,
            "largest_component_size": largest,
            "largest_component_pct": round(100.0 * largest / n, 2),
            "degree": {
                "mean": round(mean_deg, 3), "median": median_deg,
                "max": degs[-1], "degree0": deg0, "degree1": deg1,
            },
            "isolate_sample": isolate_sample,
        }

    # ------------------------------------------------------------------
    # Entity deduplication (Graph-Quality v-next, Topic 1)
    # ------------------------------------------------------------------

    @property
    def dedup_store(self):
        """Lazily-created per-workspace alias/audit store (reversible merges, D3)."""
        if self._dedup_store is None:
            import os
            from context_graph.dedup import JsonDedupStore
            self._dedup_store = JsonDedupStore(os.path.join(self.working_dir, "dedup"))
        return self._dedup_store

    def _dedup_thresholds(self) -> tuple:
        import os
        from context_graph.dedup import DEFAULT_HARD, DEFAULT_GRAY
        hard = float(os.getenv("DEDUP_HARD", DEFAULT_HARD))
        gray = float(os.getenv("DEDUP_GRAY", DEFAULT_GRAY))
        return hard, gray

    @property
    def dedup_enabled(self) -> bool:
        """Master switch (DEDUP_ENABLED, default on). Gates the scan/sweep endpoints
        and the periodic background sweep."""
        import os
        return os.getenv("DEDUP_ENABLED", "true").strip().lower() not in (
            "false", "0", "no", "off", "")

    def _dedup_sweep_batch(self) -> int:
        import os
        try:
            return max(1, int(os.getenv("DEDUP_SWEEP_BATCH", "10")))
        except ValueError:
            return 10

    async def _apply_entity_merge(self, alias: str, into: str, canonical: str) -> None:
        """Graph-level merge: fold node *alias* into *into*, remember the canonical
        display name. Reuses the vetted amerge_entities (rewires edges, updates vdb)."""
        if alias == into:
            return
        await self.amerge_entities(
            [alias], into,
            merge_strategy={"description": "concatenate", "entity_type": "keep_first"},
        )
        if canonical:
            self.dedup_store.set_canonical_name(self.workspace, into, canonical)

    async def deduplicate_entities(self, *, apply: bool = True, limit: int = 5000) -> dict:
        """Layer E — scan existing entities for duplicates (conservative, type-aware).

        For each entity, find its nearest neighbour in ``entities_vdb``; auto-merge
        only above the HARD cosine threshold with compatible types and a name backstop
        (recorded reversibly), and queue the gray band for :meth:`run_dedup_sweep`.
        Backend-agnostic; runs off the ingest path. Returns a summary.
        """
        from context_graph.dedup import canonicalize, prefer_canonical_name, type_ok, name_ok

        hard, gray = self._dedup_thresholds()
        graph = self.chunk_entity_relation_graph
        store = self.dedup_store
        labels: list[str] = list(await graph.get_all_labels() or [])[:limit]
        merged_away: set[str] = set()
        summary = {"scanned": 0, "merged": 0, "queued": 0, "skipped": 0}

        async def node_type(name: str):
            node = await graph.get_node(name)
            return (node or {}).get("entity_type")

        async def mention_count(name: str, node: dict = None) -> int:
            # Frequency proxy: how many source chunks the entity appears in.
            node = node if node is not None else (await graph.get_node(name) or {})
            sid = node.get("source_id") or ""
            return len([c for c in sid.split(GRAPH_FIELD_SEP) if c]) or 1

        for name in labels:
            summary["scanned"] += 1
            if name in merged_away:
                summary["skipped"] += 1
                continue
            my_node = await graph.get_node(name) or {}
            my_type = my_node.get("entity_type")
            # Query with the SAME representation entities_vdb stored (name + first
            # description line), not the bare name — else a short name is diluted
            # against the long stored name+description content and true dupes miss.
            desc = (my_node.get("description") or "").split(GRAPH_FIELD_SEP)[0].strip()
            query_text = f"{name}\n{desc}" if desc else name
            try:
                hits = await self.entities_vdb.query(query_text, top_k=5) or []
            except Exception:
                hits = []
            top = next(
                (h for h in hits
                 if (h.get("entity_name") or h.get("id")) not in (None, "", name)
                 and (h.get("entity_name") or h.get("id")) not in merged_away),
                None,
            )
            if top is None:
                continue
            cand = top.get("entity_name") or top.get("id")
            score = float(top.get("distance") or 0.0)
            ctype = top.get("entity_type")
            if ctype is None:
                ctype = await node_type(cand)
            if score >= hard and type_ok(my_type, ctype) and name_ok(name, cand):
                # apply=False is a pure preview: count, but touch neither graph nor store.
                if apply:
                    # Representative canonical name — frequency-weighted (the form used
                    # most often usually wins); expands bare acronyms only near a tie.
                    counts = {name: await mention_count(name, my_node),
                              cand: await mention_count(cand)}
                    canonical = prefer_canonical_name([name, cand], counts=counts)
                    # The representative form SURVIVES as the node; the other folds in
                    # — so the graph/UI shows "Kubernetes", not "kubernetes".
                    survivor, alias = (name, cand) if canonical.strip() == name.strip() else (cand, name)
                    try:
                        await self._apply_entity_merge(alias, survivor, canonical)
                    except Exception as e:  # pragma: no cover
                        logger.warning(f"dedup merge {alias}->{survivor} failed: {e}")
                        continue
                    store.record_merge(
                        self.workspace, alias=alias, alias_key=canonicalize(alias),
                        into=survivor, method="embedding", score=score, canonical_name=canonical,
                    )
                    merged_away.add(alias)
                summary["merged"] += 1
            elif score >= gray and type_ok(my_type, ctype):
                if apply:
                    store.enqueue_review(self.workspace, name=name, candidate=cand, score=score)
                summary["queued"] += 1

        logger.info(f"deduplicate_entities: {summary}")
        return summary

    async def run_dedup_sweep(self) -> dict:
        """Layer C — LLM adjudicates *same-or-not* for the gray-zone queue; the
        canonical name is chosen by frequency-weighted score. Confirmed merges are
        applied to the graph. Off the ingest path."""
        from context_graph.dedup import DedupSweep

        graph = self.chunk_entity_relation_graph

        async def apply(alias: str, into: str, canonical: str):
            await self._apply_entity_merge(alias, into, canonical)

        async def get_count(name: str) -> int:
            node = await graph.get_node(name) or {}
            sid = node.get("source_id") or ""
            return len([c for c in sid.split(GRAPH_FIELD_SEP) if c]) or 1

        sweep = DedupSweep(
            self.dedup_store, self.workspace, self._llm_extract,
            apply_merge=apply, get_count=get_count,
            batch_size=self._dedup_sweep_batch(),
        )
        return await sweep.run()

    async def unmerge_entity(self, merge_id: str) -> bool:
        """Reverse a recorded merge's *resolution* (D3). Note: this restores the alias
        mapping/audit; re-splitting already-folded graph edges is not automatic."""
        return self.dedup_store.unmerge(self.workspace, merge_id)

    async def _index_decision(
        self, src: str, tgt: str, relation_type: str, rc: RelationContext
    ) -> None:
        """Project a decision edge into the derived search indices.

        Writes the same relationships_vdb record shape the extraction pipeline uses
        for any relation (so the decision is retrievable by a normal /query), plus the
        decisions_vdb precedent-search entry. Never raises — a failed index write must
        not undo the graph edge, which is the source of truth (repair via
        :meth:`reindex_decisions`).
        """
        trace = (rc.decision_trace or "").strip()
        if not trace:
            return
        # Canonicalize the pair (smaller id first) so the derived record ids match
        # the extraction pipeline's (operate.py) and there is exactly ONE record per
        # undirected edge regardless of which orientation emit/reindex sees. Graph
        # get_edge is undirected on both backends, so ordering src/tgt is safe.
        a, b = (src, tgt) if src <= tgt else (tgt, src)
        # 1) main retrieval fabric — decisions become first-class relations
        try:
            rel_id = compute_mdhash_id(a + b, prefix="rel-")
            rel_id_reverse = compute_mdhash_id(b + a, prefix="rel-")
            # Drop any stale reverse-orientation record left by a prior write path.
            try:
                await self.relationships_vdb.delete([rel_id_reverse])
            except Exception:  # pragma: no cover - best-effort cleanup
                pass
            await self.relationships_vdb.upsert(
                {
                    rel_id: {
                        "src_id": a,
                        "tgt_id": b,
                        "source_id": "emit_decision_trace",
                        "content": f"{relation_type}\t{a}\n{b}\n{trace}",
                        "keywords": relation_type,
                        "description": trace,
                        "weight": rc.confidence_score,
                        "file_path": rc.provenance or "agent_runtime",
                    }
                }
            )
        except Exception as e:  # pragma: no cover - index write must not break emit
            logger.warning(f"decision relationships_vdb index failed for {a}->{b}: {e}")
        # 2) precedent-search index
        try:
            dec_id = compute_mdhash_id(f"{a}>{b}", prefix="dec-")
            dec_id_reverse = compute_mdhash_id(f"{b}>{a}", prefix="dec-")
            try:
                await self.decisions_vdb.delete([dec_id_reverse])
            except Exception:  # pragma: no cover - best-effort cleanup
                pass
            await self.decisions_vdb.upsert(
                {dec_id: {"content": trace, "src_id": a, "tgt_id": b}}
            )
        except Exception as e:  # pragma: no cover
            logger.warning(f"decision decisions_vdb index failed for {a}->{b}: {e}")

    async def reindex_decisions(self) -> dict:
        """Rebuild the decision search indices from the graph (the source of truth).

        Scans every rc-bearing edge and re-projects it into relationships_vdb and
        decisions_vdb via :meth:`_index_decision`. This is what makes those indices
        *derived*: drop them and this repopulates from the graph, and it repairs drift
        (edges recorded through other write paths, or partial failures, that never
        reached the vector stores). Idempotent — safe to run any time.

        Authoritative rebuild: ``decisions_vdb`` holds only decision records, so it is
        dropped first and repopulated — this removes orphans for edges deleted from the
        graph (upsert alone would leave them, crowding out real precedents). The shared
        ``relationships_vdb`` cannot be dropped (it also holds pipeline relations); its
        per-edge reverse-orientation duplicates are cleaned inside :meth:`_index_decision`.
        """
        graph = self.chunk_entity_relation_graph
        # Drop the exclusively-decision index so deleted-edge orphans don't survive.
        try:
            await self.decisions_vdb.drop()
        except Exception as e:
            logger.warning(f"reindex_decisions: could not drop decisions_vdb, "
                           f"orphans may persist: {e}")
        n = 0
        if hasattr(graph, "get_edges_with_relation_context"):
            # Fast path: a filtered lookup returns only decision edges.
            for e in await graph.get_edges_with_relation_context():
                rc_json = e.get("relation_context")
                if not rc_json:
                    continue
                rc = RelationContext.from_json(rc_json)
                if not rc.decision_trace:
                    continue
                rtype = e.get("keywords") or "decision"
                await self._index_decision(e["source"], e["target"], rtype, rc)
                n += 1
        else:
            # Fallback: scan all edges (backends without a filtered query).
            for d in await self.get_all_decisions():
                src, tgt = d["src_id"], d["tgt_id"]
                edge = await graph.get_edge(src, tgt)
                rtype = (edge or {}).get("keywords") or "decision"
                await self._index_decision(src, tgt, rtype, d["relation_context"])
                n += 1
        await self._persist_decision_indices()
        logger.info(f"reindex_decisions: re-projected {n} decision edges from the graph")
        return {"reindexed": n}

    async def reindex_graph_vectors(self) -> dict:
        """Rebuild ``entities_vdb`` and ``relationships_vdb`` from the graph — the
        source of truth. Recovers from vector drift (embedding-model or storage
        changes, partial write failures) using the EXACT record shape ingestion
        writes (mirrors ``ainsert_custom_kg``), so semantic search and dedup keep
        working. Off the ingest path.

        Composition: entities_vdb is dropped + rebuilt (it holds only entities);
        relationships_vdb is dropped + rebuilt from every edge with the standard
        relation shape, then :meth:`reindex_decisions` overlays the richer decision
        projections back on top (it upserts, never drops, relationships_vdb).
        ``decisions_vdb`` is rebuilt by that same call.
        """
        graph = self.chunk_entity_relation_graph

        # --- entities: name + first-nothing; content mirrors ainsert_custom_kg ---
        labels = list(await graph.get_all_labels() or [])
        ent_records: dict = {}
        for name in labels:
            node = await graph.get_node(name) or {}
            desc = node.get("description", "") or ""
            ent_records[compute_mdhash_id(name, prefix="ent-")] = {
                "content": name + "\n" + desc,
                "entity_name": name,
                "source_id": node.get("source_id", ""),
                "description": desc,
                "entity_type": node.get("entity_type", "UNKNOWN"),
                "file_path": node.get("file_path", "reindex"),
            }

        # --- relationships: every edge, standard shape (decisions overlaid after) ---
        edges = list(await graph.get_all_edges() or [])
        rel_records: dict = {}
        for e in edges:
            src, tgt = e.get("source"), e.get("target")
            if not src or not tgt:
                continue
            kw = e.get("keywords", "") or ""
            desc = e.get("description", "") or ""
            rel_records[compute_mdhash_id(src + tgt, prefix="rel-")] = {
                "src_id": src,
                "tgt_id": tgt,
                "source_id": e.get("source_id", ""),
                "content": f"{kw}\t{src}\n{tgt}\n{desc}",
                "keywords": kw,
                "description": desc,
                "weight": e.get("weight", 1.0),
                "file_path": e.get("file_path", "reindex"),
            }

        for vdb in (self.entities_vdb, self.relationships_vdb):
            try:
                await vdb.drop()
            except Exception as ex:  # pragma: no cover - best-effort
                logger.warning(f"reindex_graph_vectors: could not drop "
                               f"{getattr(vdb, 'namespace', 'vdb')}: {ex}")
        if ent_records:
            await self.entities_vdb.upsert(ent_records)
        if rel_records:
            await self.relationships_vdb.upsert(rel_records)
        for vdb in (self.entities_vdb, self.relationships_vdb):
            try:
                await vdb.index_done_callback()
            except Exception as ex:  # pragma: no cover
                logger.warning(f"reindex_graph_vectors persist failed: {ex}")

        # Overlay the decision projections (also rebuilds decisions_vdb).
        dec = await self.reindex_decisions()
        summary = {"entities": len(ent_records), "relationships": len(rel_records),
                   "decisions_reprojected": dec.get("reindexed", 0)}
        logger.info(f"reindex_graph_vectors: {summary}")
        return summary

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

        # Upsert: fully remove any stale version before re-ingesting. Deleting only
        # full_docs left the doc_status entry behind, so the pipeline deduped the
        # re-add as a "[DUPLICATE]" and the update was silently dropped (while the
        # original doc + chunks/entities were already gone). adelete_by_doc_id clears
        # doc_status, chunks, graph elements and vectors so the re-ingest proceeds.
        if summary_id:
            existing = await self.full_docs.get_by_id(summary_id)
            if existing is not None:
                await self.adelete_by_doc_id(summary_id)

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

    def _format_decisions_context(self, precedents: list[dict]) -> str:
        """Render recorded decisions as an LLM context block (raw text)."""
        lines = [
            "## Recorded decisions (the project's own decision log — ADRs, change "
            "requests, approvals; captured at the moment they were made)"
        ]
        for p in precedents:
            rc = p.get("relation_context")
            trace = (getattr(rc, "decision_trace", "") or "").strip()
            if not trace:
                continue
            who = f" — recorded by {rc.approved_by}" if getattr(rc, "approved_by", None) else ""
            lines.append(f"- [{p.get('src_id')} → {p.get('tgt_id')}]{who}: {trace}")
        return "\n".join(lines)

    def _format_named_nodes_context(self, nodes: list[dict]) -> str:
        """Render nodes the query named by exact id (with their neighbourhood)."""
        lines = ["## Entities named in the question (exact matches from the graph)"]
        for n in nodes:
            typ = f" ({n['type']})" if n.get("type") else ""
            desc = n.get("description") or n["name"]
            lines.append(f"- {n['name']}{typ}: {desc}")
            if n.get("neighbours"):
                lines.append(f"  related: {', '.join(n['neighbours'][:8])}")
        return "\n".join(lines)

    # Bounds for the query-time blend (keep the injected context small and cheap).
    _BLEND_MAX_PRECEDENTS = 8
    _BLEND_MAX_NAMED_NODES = 6
    _BLEND_MAX_SCAN_TOKENS = 40  # cap graph probes per query (C6)
    _BLEND_CHAR_BUDGET = 4000  # ~1k tokens ceiling on injected text (C5)

    async def aquery_llm(self, query, param=None, system_prompt=None):
        """Blend recorded decisions into the normal retrieval context.

        Decisions recorded via :meth:`emit_decision_trace` live in
        ``decisions_vdb`` and are otherwise reachable only through precedent
        search — invisible to ``/query`` and the WebUI Retrieval tab. This
        override fetches the decisions most relevant to *query* and injects them
        into the LLM context, so a single query returns code, docs, **and** the
        project's own decisions together.

        The injected block is appended to ``param.user_prompt`` rather than spliced
        into the system-prompt template. That makes it (a) mode-agnostic — both the
        kg and naive templates render ``{user_prompt}``, so ``naive`` no longer
        crashes on a ``context_data`` placeholder; (b) cache-correct — ``user_prompt``
        is part of the query cache key, so a new decision actually invalidates a stale
        cached answer; and (c) non-destructive — a caller-supplied ``system_prompt`` is
        passed through untouched. Skipped for ``bypass`` and ``only_need_context``.

        Overrides ``aquery_llm`` (not ``aquery``) because the REST query routes
        call ``aquery_llm`` directly; ``aquery`` wraps it, so this covers both.
        """
        from lightrag.base import QueryParam

        if param is None:
            param = QueryParam()

        precedents: list[dict] = []
        named_nodes: list[dict] = []
        should_blend = (
            getattr(self, "decisions_vdb", None) is not None
            and getattr(param, "mode", "mix") != "bypass"
            and not getattr(param, "only_need_context", False)
        )
        if should_blend:
            precedents, named_nodes = await self._collect_blend_context(query)
            block = self._build_blend_block(precedents, named_nodes)
            if block:
                # Inject as an additional-context instruction on user_prompt. This is
                # cache-keyed and rendered by every retrieval mode's template.
                instruction = (
                    "Additional authoritative context — the project's own recorded "
                    "decisions and the entities named in the question. Use these and "
                    "cite them when they apply:\n\n" + block
                )
                existing = (getattr(param, "user_prompt", "") or "").strip()
                param.user_prompt = (
                    f"{existing}\n\n{instruction}" if existing else instruction
                )

        result = await super().aquery_llm(query, param, system_prompt=system_prompt)

        # Fallback: retrieval short-circuits with the fail_response marker
        # ("[no-context]") when it finds no entities/chunks — a pure by-id lookup.
        # In that case the LLM was never called, so our user_prompt injection never
        # reached it; answer directly from the structural context we located. Keyed
        # strictly on the marker (not on any answer that mentions "enough
        # information"), and never for only_need_prompt/only_need_context/streaming.
        if (
            (precedents or named_nodes)
            and not getattr(param, "only_need_prompt", False)
            and not getattr(param, "only_need_context", False)
        ):
            lr = result.get("llm_response") or {}
            content = lr.get("content") or ""
            if lr.get("response_iterator") is None and "[no-context]" in content:
                parts = []
                if named_nodes:
                    parts.append(self._format_named_nodes_context(named_nodes))
                if precedents:
                    parts.append(self._format_decisions_context(precedents))
                try:
                    ans = await self._llm_query(
                        "Answer the question using ONLY this project context (quote the "
                        "relevant entity or decision). If it doesn't apply, say so briefly.\n\n"
                        + "\n\n".join(parts)
                        + f"\n\n---\nQuestion: {query}"
                    )
                    if isinstance(ans, str) and ans.strip():
                        result.setdefault("llm_response", {})["content"] = ans
                        result["status"] = "success"
                except Exception as e:  # pragma: no cover - never break a query
                    logger.warning(f"aquery by-name fallback failed: {e}")
        return result

    async def _collect_blend_context(self, query: str) -> tuple[list[dict], list[dict]]:
        """Gather precedent decisions + exact-named graph nodes for the blend.

        Bounded work: at most ``_BLEND_MAX_SCAN_TOKENS`` graph probes, stopping once
        the precedent/named-node caps are hit — so a paragraph-length query or a query
        that happens to name a high-degree hub node can't trigger an unbounded fan-out
        of graph roundtrips.
        """
        precedents: list[dict] = []
        named_nodes: list[dict] = []
        try:
            precedents = await self.find_precedents(query, top_k=5)
        except Exception as e:  # pragma: no cover - never break a query
            logger.warning(f"aquery decision-blend skipped: {e}")
            precedents = []
        try:
            import re

            seen = {(p.get("src_id"), p.get("tgt_id")) for p in precedents}
            named_seen: set[str] = set()
            graph = self.chunk_entity_relation_graph
            candidates = list(
                dict.fromkeys(re.findall(r"[A-Za-z][A-Za-z0-9_.-]{3,}", query))
            )[: self._BLEND_MAX_SCAN_TOKENS]
            for cand in candidates:
                if (
                    len(named_nodes) >= self._BLEND_MAX_NAMED_NODES
                    and len(precedents) >= self._BLEND_MAX_PRECEDENTS
                ):
                    break
                if not await graph.has_node(cand):
                    continue
                edges = await graph.get_node_edges(cand) or []
                # (a) the node itself — its description + a few neighbours
                if cand not in named_seen and len(named_nodes) < self._BLEND_MAX_NAMED_NODES:
                    node = await graph.get_node(cand)
                    if node:
                        nbrs = []
                        for s, t in edges[:8]:
                            other = t if s == cand else s
                            e = await graph.get_edge(s, t)
                            nbrs.append(f"{(e or {}).get('keywords', 'related')} → {other}")
                        named_nodes.append({
                            "name": cand,
                            "type": node.get("entity_type", ""),
                            "description": (node.get("description") or "").strip(),
                            "neighbours": nbrs,
                        })
                        named_seen.add(cand)
                # (b) decisions attached to it
                for s, t in edges:
                    if len(precedents) >= self._BLEND_MAX_PRECEDENTS or (s, t) in seen:
                        continue
                    edge = await graph.get_edge(s, t)
                    if edge and edge.get("relation_context"):
                        rc = RelationContext.from_json(edge["relation_context"])
                        if rc.decision_trace:
                            precedents.append(
                                {"src_id": s, "tgt_id": t, "relation_context": rc}
                            )
                            seen.add((s, t))
        except Exception as e:  # pragma: no cover
            logger.warning(f"aquery by-name include skipped: {e}")
        return precedents, named_nodes

    def _build_blend_block(self, precedents: list[dict], named_nodes: list[dict]) -> str:
        """Render the blend context, capped at ``_BLEND_CHAR_BUDGET`` chars.

        Injected text bypasses the retrieval context's token-fitting, so it is bounded
        here to keep the final prompt from overflowing the model window.
        """
        blocks = []
        if named_nodes:
            blocks.append(self._format_named_nodes_context(named_nodes))
        if precedents:
            blocks.append(self._format_decisions_context(precedents))
        text = "\n\n".join(b for b in blocks if b.strip())
        if len(text) > self._BLEND_CHAR_BUDGET:
            text = text[: self._BLEND_CHAR_BUDGET].rstrip() + "\n… (truncated)"
        return text

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

        llm_func = self._llm_query   # CGR3 reasoning → strong QUERY role
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
