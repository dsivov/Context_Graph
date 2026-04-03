"""
MCP (Model Context Protocol) server — Context Graph tools for conversation agents.

Exposes 10 MCP tools that map to existing Context Graph API calls.
Embedded in the FastAPI server as a Starlette sub-app via Streamable HTTP transport.

Created for CR-018.
"""

from __future__ import annotations

import hmac
from typing import Optional

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse

from lightrag.base import QueryParam
from lightrag.utils import logger
from lightrag.api.routers.query_routes import (
    classify_query_mode,
    get_catalog_info,
    CATALOG_BYPASS_SYSTEM_PROMPT,
)


VALID_MODES = {"local", "global", "hybrid", "naive", "mix", "bypass", "cgr3"}
MAX_QUERY_LEN = 10_000
MAX_TEXT_LEN = 100_000
MAX_DECISION_TRACE_LEN = 5_000
MAX_ENTITY_NAME_LEN = 200
MAX_HISTORY_MESSAGES = 50
MAX_HISTORY_MESSAGE_LEN = 10_000


def _validate_conversation_history(history: list[dict] | None) -> list[dict]:
    """Validate and normalize conversation_history. Returns clean list."""
    if not history:
        return []
    if len(history) > MAX_HISTORY_MESSAGES:
        raise ToolError(f"conversation_history exceeds {MAX_HISTORY_MESSAGES} messages")
    clean = []
    for msg in history:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if not role or not isinstance(role, str):
            continue  # skip malformed entries silently
        if len(content) > MAX_HISTORY_MESSAGE_LEN:
            content = content[:MAX_HISTORY_MESSAGE_LEN]  # truncate, don't error
        clean.append({"role": role, "content": content})
    return clean


def _validate_mode(mode: str) -> None:
    if mode not in VALID_MODES:
        raise ToolError(f"Invalid mode '{mode}'. Must be one of: {', '.join(sorted(VALID_MODES))}")


def _validate_text(text: str, field: str, max_len: int) -> None:
    if not text or not text.strip():
        raise ToolError(f"'{field}' must be non-empty")
    if len(text) > max_len:
        raise ToolError(f"'{field}' exceeds max length ({len(text)} > {max_len})")


def _validate_entity_name(name: str, field: str) -> None:
    if not name or not name.strip():
        raise ToolError(f"'{field}' must be non-empty")
    if len(name) > MAX_ENTITY_NAME_LEN:
        raise ToolError(f"'{field}' exceeds max length ({len(name)} > {MAX_ENTITY_NAME_LEN})")


def _require_context_graph(rag) -> None:
    """Raise ToolError if the underlying rag is not a ContextGraph instance."""
    from lightrag.context_graph import ContextGraph

    actual = rag._get_current_rag() if hasattr(rag, "_get_current_rag") else rag
    if not isinstance(actual, ContextGraph):
        raise ToolError(
            "This tool requires Context Graph mode (USE_CONTEXT_GRAPH=true)"
        )


def _rc_to_dict(rc) -> dict:
    """Convert a RelationContext dataclass to a plain dict for MCP response."""
    return {
        "supporting_sentences": rc.supporting_sentences or [],
        "temporal_info": rc.temporal_info,
        "quantitative_data": rc.quantitative_data,
        "decision_trace": rc.decision_trace,
        "provenance": rc.provenance,
        "approved_by": rc.approved_by,
        "approved_via": rc.approved_via,
        "valid_from": rc.valid_from,
        "valid_until": rc.valid_until,
        "policy_ref": rc.policy_ref,
        "confidence_score": rc.confidence_score,
    }


def create_mcp_server(
    rag,
    api_key: str | None = None,
    top_k: int = 60,
    cgr3_max_iterations: int = 3,
) -> tuple[FastMCP, Starlette]:
    """Create and return (mcp_server, mcp_starlette_app).

    The MCP server is stateless (each request is independent) and wraps
    existing ContextGraph / LightRAG methods via the WorkspaceProxy.

    Args:
        rag: WorkspaceProxy instance (delegates to per-workspace rag).
        api_key: Optional API key for X-API-Key auth on MCP requests.
        top_k: Default top_k for retrieval tools.
        cgr3_max_iterations: Default max iterations for CGR3 queries.

    Returns:
        Tuple of (FastMCP instance, Starlette ASGI app to mount).
    """
    from mcp.server.transport_security import TransportSecuritySettings

    mcp = FastMCP(
        name="ContextGraph",
        stateless_http=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=False,
        ),
    )

    # ── Tool 1: query_knowledge_graph ─────────────────────────────────

    @mcp.tool()
    async def query_knowledge_graph(
        query: str,
        mode: str = "mix",
        top_k: int = top_k,
        only_need_context: bool = False,
        conversation_history: list[dict] | None = None,
    ) -> dict:
        """Search the knowledge graph and return a synthesized natural-language answer. Use ONLY when you need a specific retrieval mode (local/global/hybrid/mix/naive) and a pre-written answer. For most questions, prefer query_auto instead — it picks the best mode automatically. For raw structured data (entity lists, relationship details, chunks) without synthesis, use query_data. For multi-hop reasoning ("trace", "walk through", "chain of approvals"), use query_cgr3. Optionally pass conversation_history (list of {role, content} dicts) for multi-turn context."""
        _validate_text(query, "query", MAX_QUERY_LEN)
        _validate_mode(mode)
        history = _validate_conversation_history(conversation_history)
        try:
            param = QueryParam(
                mode=mode,
                top_k=top_k,
                only_need_context=only_need_context,
                conversation_history=history,
            )
            result = await rag.aquery(query, param=param)
            response_text = (
                result.content if hasattr(result, "content") else str(result)
            )
            return {"response": response_text}
        except Exception as e:
            logger.error(f"MCP query_knowledge_graph error: {e}", exc_info=True)
            raise ToolError(str(e))

    # ── Tool 2: query_cgr3 ────────────────────────────────────────────

    @mcp.tool()
    async def query_cgr3(
        query: str,
        mode: str = "hybrid",
        max_iterations: int = cgr3_max_iterations,
        top_k: int = top_k,
    ) -> dict:
        """Answer complex multi-hop questions using iterative Retrieve-Rank-Reason (3 rounds). Use INSTEAD of query_knowledge_graph when the question asks to "trace", "walk through", "step by step", "chain of approvals", or requires connecting multiple decisions/people/events to build a narrative. Examples: "Trace why X got a discount and who approved it", "Walk me through the chain of approvals for Y". Slower (30-60s) but gathers evidence across multiple hops that a single query would miss."""
        _validate_text(query, "query", MAX_QUERY_LEN)
        _validate_mode(mode)
        _require_context_graph(rag)
        try:
            answer = await rag.cgr3_query(
                query=query,
                mode=mode,
                max_iterations=max_iterations,
                top_k=top_k,
            )
            return {"response": answer}
        except ToolError:
            raise
        except Exception as e:
            logger.error(f"MCP query_cgr3 error: {e}", exc_info=True)
            raise ToolError(str(e))

    # ── Tool 3: search_precedents ─────────────────────────────────────

    @mcp.tool()
    async def search_precedents(
        query: str,
        top_k: int = 10,
        min_confidence: float = 0.0,
    ) -> dict:
        """Find past decisions semantically similar to a scenario description. Use when the agent needs to check "have we done something like this before?" - e.g., discount approvals, policy exceptions, deal structures. Returns ranked list with full decision context (who approved, why, via which channel)."""
        _require_context_graph(rag)
        try:
            raw = await rag.find_precedents(
                query_text=query,
                top_k=top_k,
                min_confidence=min_confidence,
            )
            results = [
                {
                    "src_id": item["src_id"],
                    "tgt_id": item["tgt_id"],
                    "relation_context": _rc_to_dict(item["relation_context"]),
                }
                for item in raw
            ]
            return {"total_count": len(results), "results": results}
        except ToolError:
            raise
        except Exception as e:
            logger.error(f"MCP search_precedents error: {e}", exc_info=True)
            raise ToolError(str(e))

    # ── Tool 4: list_decisions ────────────────────────────────────────

    @mcp.tool()
    async def list_decisions(
        approved_by: Optional[str] = None,
        approved_via: Optional[str] = None,
        policy_ref: Optional[str] = None,
        min_confidence: float = 0.0,
        active_as_of: Optional[str] = None,
    ) -> dict:
        """List all decisions with structured filters: approved_by, approved_via (slack/email/zoom), policy_ref, min_confidence, active_as_of date. Use ONLY when the question asks to FILTER by a specific field value (e.g., "decisions approved via Slack", "all decisions by approver X", "decisions above 90% confidence"). NOT for exploring a single entity's relationships (use get_entity_context) or semantic search (use search_precedents)."""
        _require_context_graph(rag)
        try:
            raw = await rag.get_all_decisions(
                approved_by=approved_by,
                approved_via=approved_via,
                policy_ref=policy_ref,
                min_confidence=min_confidence,
                active_as_of=active_as_of,
            )
            decisions = [
                {
                    "src_id": item["src_id"],
                    "tgt_id": item["tgt_id"],
                    "relation_context": _rc_to_dict(item["relation_context"]),
                }
                for item in raw
            ]
            return {"total_count": len(decisions), "decisions": decisions}
        except ToolError:
            raise
        except Exception as e:
            logger.error(f"MCP list_decisions error: {e}", exc_info=True)
            raise ToolError(str(e))

    # ── Tool 5: get_edge_context ──────────────────────────────────────

    @mcp.tool()
    async def get_edge_context(src: str, tgt: str) -> dict:
        """Get the full RelationContext (decision trace, temporal validity, approval chain, evidence) for a specific relationship between two named entities. Use when the question names BOTH entities (e.g., "approval context between X and Y", "decision details for the X discount on Y"). If only ONE entity is named, use get_entity_context instead."""
        _validate_entity_name(src, "src")
        _validate_entity_name(tgt, "tgt")
        _require_context_graph(rag)
        try:
            rc = await rag.get_edge_context(src, tgt)
            if rc is None:
                # Try reverse direction
                rc = await rag.get_edge_context(tgt, src)
            has_context = rc is not None and not rc.is_empty()
            return {
                "src_id": src,
                "tgt_id": tgt,
                "has_context": has_context,
                "relation_context": _rc_to_dict(rc) if has_context else None,
            }
        except ToolError:
            raise
        except Exception as e:
            logger.error(f"MCP get_edge_context error: {e}", exc_info=True)
            raise ToolError(str(e))

    # ── Tool 6: get_entity_context ────────────────────────────────────

    @mcp.tool()
    async def get_entity_context(entity_name: str) -> dict:
        """Get all decision-bearing relationships connected to a specific entity. Use when the question names ONE entity and asks what decisions, approvals, or relationships involve them (e.g., "What decisions involve VP of Sales?", "Show everything connected to X", "What has entity Y done?"). Returns all edges with approval/decision metadata. If the question asks to FILTER decisions by channel/policy/confidence, use list_decisions instead."""
        _require_context_graph(rag)
        try:
            from lightrag.context_graph_types import RelationContext

            raw_edges = await rag.get_edges_with_context(entity_name)
            edges = []
            for edge in raw_edges:
                rc = edge.get("relation_context")
                if rc is None:
                    continue
                if isinstance(rc, str):
                    rc = RelationContext.from_json(rc)
                edges.append(
                    {
                        "src_id": edge.get("src_id", ""),
                        "tgt_id": edge.get("tgt_id", ""),
                        "keywords": edge.get("keywords"),
                        "description": edge.get("description"),
                        "weight": float(edge.get("weight", 1.0)),
                        "relation_context": _rc_to_dict(rc),
                    }
                )
            return {
                "entity_name": entity_name,
                "total_count": len(edges),
                "edges": edges,
            }
        except ToolError:
            raise
        except Exception as e:
            logger.error(f"MCP get_entity_context error: {e}", exc_info=True)
            raise ToolError(str(e))

    # ── Tool 7: record_decision ───────────────────────────────────────

    @mcp.tool()
    async def record_decision(
        src: str,
        tgt: str,
        relation_type: str,
        decision_trace: str,
        approved_by: Optional[str] = None,
        approved_via: Optional[str] = None,
        valid_from: Optional[str] = None,
        valid_until: Optional[str] = None,
        policy_ref: Optional[str] = None,
        confidence_score: float = 0.95,
    ) -> dict:
        """Record a decision trace into the knowledge graph in real-time. Call this when the conversation agent facilitates or witnesses a decision (discount approval, policy exception, deal terms). Captures: who approved, why, via which channel, validity period, policy reference."""
        _validate_entity_name(src, "src")
        _validate_entity_name(tgt, "tgt")
        _validate_text(decision_trace, "decision_trace", MAX_DECISION_TRACE_LEN)
        confidence_score = max(0.0, min(1.0, confidence_score))
        _require_context_graph(rag)
        try:
            from lightrag.context_graph_types import RelationContext

            rc = RelationContext(
                decision_trace=decision_trace,
                approved_by=approved_by,
                approved_via=approved_via,
                valid_from=valid_from,
                valid_until=valid_until,
                policy_ref=policy_ref,
                confidence_score=confidence_score,
            )
            await rag.emit_decision_trace(
                src=src,
                tgt=tgt,
                relation_type=relation_type,
                rc=rc,
            )
            return {"status": "ok", "edge": f"{src} -> {tgt}"}
        except ToolError:
            raise
        except Exception as e:
            logger.error(f"MCP record_decision error: {e}", exc_info=True)
            raise ToolError(str(e))

    # ── Tool 8: ingest_decision_summary ───────────────────────────────

    @mcp.tool()
    async def ingest_decision_summary(
        text: str,
        category: str = "general",
        summary_id: Optional[str] = None,
    ) -> dict:
        """Ingest a natural-language SUMMARY of aggregated decisions into the knowledge graph. Use when the user provides a pre-written summary or analysis text to store (e.g., "Summarize: Q3 had 80% approval rate on deals over $50k", "Store this analysis: monthly deal patterns show..."). Unlike record_decision (which records a single specific decision), this processes a block of text through the full extraction pipeline to create multiple entities and relations. The text parameter should contain the summary content."""
        _validate_text(text, "text", MAX_TEXT_LEN)
        _require_context_graph(rag)
        try:
            from lightrag.utils import compute_mdhash_id

            resolved_id = summary_id or compute_mdhash_id(text, prefix="dsum-")
            track_id = await rag.ingest_decision_summary(
                text=text,
                category=category,
                summary_id=resolved_id,
            )
            return {
                "status": "ok",
                "track_id": track_id,
                "summary_id": resolved_id,
            }
        except ToolError:
            raise
        except Exception as e:
            logger.error(f"MCP ingest_decision_summary error: {e}", exc_info=True)
            raise ToolError(str(e))

    # ── Tool 9: query_data ──────────────────────────────────────────

    @mcp.tool()
    async def query_data(
        query: str,
        mode: str = "mix",
        top_k: int = top_k,
        conversation_history: list[dict] | None = None,
    ) -> dict:
        """Retrieve structured data from the knowledge graph WITHOUT LLM synthesis. Returns raw entities, relationships, and text chunks as separate JSON arrays. Use this INSTEAD OF query_knowledge_graph when you need raw structured data for filtering, counting, listing, or programmatic processing — e.g., "list all entities related to X", "get product data for Y", "show me the raw knowledge graph data". Returns a dict with status, data (entities[], relationships[], chunks[], references[]), and metadata. Supports modes: local, global, hybrid, mix, naive. Optionally pass conversation_history for multi-turn context."""
        _validate_text(query, "query", MAX_QUERY_LEN)
        _validate_mode(mode)
        history = _validate_conversation_history(conversation_history)
        try:
            param = QueryParam(mode=mode, top_k=top_k, conversation_history=history)
            result = await rag.aquery_data(query, param=param)
            return result
        except ToolError:
            raise
        except Exception as e:
            logger.error(f"MCP query_data error: {e}", exc_info=True)
            raise ToolError(str(e))

    # ── Tool 10: query_auto ─────────────────────────────────────────

    @mcp.tool()
    async def query_auto(
        query: str,
        data_only: bool = False,
        conversation_history: list[dict] | None = None,
    ) -> dict:
        """RECOMMENDED DEFAULT — Smart query that automatically selects the optimal retrieval mode (local/global/hybrid/mix/cgr3) based on query intent. Use this for ALL general customer questions — it picks the best mode and handles 80%+ of queries optimally. Prefer this over query_knowledge_graph unless you specifically need a fixed mode. Set data_only=True to get structured JSON (entities, relations, chunks) without LLM synthesis. Returns response text plus the selected mode and reasoning. Optionally pass conversation_history for multi-turn context."""
        _validate_text(query, "query", MAX_QUERY_LEN)
        history = _validate_conversation_history(conversation_history)
        try:
            from functools import partial as _partial

            selected_mode, mode_reason = await classify_query_mode(query=query, rag=rag)

            # Handle catalog bypass — LLM call with full catalog text
            if selected_mode == "catalog_bypass":
                if data_only:
                    return {
                        "data": {},
                        "mode": "catalog_bypass",
                        "mode_reason": mode_reason,
                        "metadata": {"note": "catalog_bypass does not support data_only mode"},
                    }
                global_config = rag.global_config if hasattr(rag, "global_config") else {}
                llm_func = global_config.get("llm_model_func")
                if not llm_func:
                    llm_func = rag.llm_model_func
                _, catalog_text = get_catalog_info(rag)
                system_prompt = CATALOG_BYPASS_SYSTEM_PROMPT.format(catalog=catalog_text)
                bypass_func = _partial(llm_func, _priority=3)
                response_content = await bypass_func(
                    query, system_prompt=system_prompt,
                    history_messages=history if history else [],
                )
                return {
                    "response": response_content,
                    "mode": "catalog_bypass",
                    "mode_reason": mode_reason,
                }

            # CGR3 mode
            if selected_mode == "cgr3":
                if data_only:
                    # cgr3 doesn't support data-only; fall back to hybrid data retrieval
                    param = QueryParam(mode="hybrid", top_k=top_k, conversation_history=history)
                    result = await rag.aquery_data(query, param=param)
                    result["mode"] = "cgr3"
                    result["mode_reason"] = mode_reason
                    return result
                answer = await rag.cgr3_query(
                    query=query, mode="hybrid", max_iterations=cgr3_max_iterations, top_k=top_k,
                )
                return {"response": answer, "mode": "cgr3", "mode_reason": mode_reason}

            # Standard modes
            if data_only:
                param = QueryParam(mode=selected_mode, top_k=top_k, conversation_history=history)
                result = await rag.aquery_data(query, param=param)
                result["mode"] = selected_mode
                result["mode_reason"] = mode_reason
                return result
            else:
                param = QueryParam(mode=selected_mode, stream=False, top_k=top_k, conversation_history=history)
                result = await rag.aquery(query, param=param)
                response_text = result.content if hasattr(result, "content") else str(result)
                return {
                    "response": response_text,
                    "mode": selected_mode,
                    "mode_reason": mode_reason,
                }

        except ToolError:
            raise
        except Exception as e:
            logger.error(f"MCP query_auto error: {e}", exc_info=True)
            raise ToolError(str(e))

    # ── Build Starlette app with auth middleware ──────────────────────

    mcp_app = mcp.streamable_http_app()

    if api_key:

        @mcp_app.middleware("http")
        async def mcp_auth_middleware(request: Request, call_next):
            """Validate X-API-Key header — same auth as REST endpoints."""
            key = request.headers.get("X-API-Key", "")
            if not hmac.compare_digest(key, api_key):
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Invalid API key"},
                )
            return await call_next(request)

    return mcp, mcp_app
