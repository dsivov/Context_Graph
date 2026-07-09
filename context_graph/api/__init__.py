"""Context Graph HTTP API layer.

FastAPI routers for the CG-exclusive endpoints (CGR3 reasoning, decision emit,
relation-context inspection, dedup/quality/connectivity/community management).
Mounted into the LightRAG server when USE_CONTEXT_GRAPH is enabled.
"""
