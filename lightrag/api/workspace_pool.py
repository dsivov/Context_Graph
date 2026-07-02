"""Workspace pool for multi-tenant LightRAG/ContextGraph instances.

Each workspace gets its own fully initialized ContextGraph/LightRAG instance
with isolated storage (Neo4j labels, KV namespaces, vector collections).
Instances are created lazily on first request and cached.

Uses a proxy pattern with contextvars so existing route code needs zero changes —
the ``rag`` object passed to routes transparently delegates to the correct
workspace instance based on the current request's ``LIGHTRAG-WORKSPACE`` header.
"""

import asyncio
import contextvars
import re
from typing import Type

from lightrag.utils import logger

# Valid workspace name: alphanumeric and underscores only
_WORKSPACE_RE = re.compile(r"^[a-zA-Z0-9_]+$")

# Context variable holding the current workspace name for the active request
_current_workspace: contextvars.ContextVar[str] = contextvars.ContextVar(
    "current_workspace", default="default"
)


class WorkspacePool:
    """Manages a pool of LightRAG/ContextGraph instances, one per workspace."""

    def __init__(self, rag_cls: Type, rag_kwargs: dict):
        self._rag_cls = rag_cls
        self._rag_kwargs = rag_kwargs
        self._instances: dict[str, object] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._global_lock = asyncio.Lock()
        self._needs_init: set[str] = set()

    def seed(self, workspace: str):
        """Synchronously create and register an instance (no storage init).

        Used during app setup so the proxy can resolve attributes before
        the async lifespan starts.  The lifespan should call
        ``await finalize_seed(workspace)`` to complete initialization.
        """
        if not workspace:
            workspace = "default"
        if workspace not in self._instances:
            rag = self._rag_cls(workspace=workspace, **self._rag_kwargs)
            self._instances[workspace] = rag
            self._needs_init.add(workspace)
            logger.info(f"Seeded workspace: {workspace} (pending async init)")
        return self._instances[workspace]

    async def finalize_seed(self, workspace: str):
        """Complete async initialization for a seeded workspace."""
        if workspace in self._needs_init:
            rag = self._instances[workspace]
            await rag.initialize_storages()
            await rag.check_and_migrate_data()
            self._needs_init.discard(workspace)
            logger.info(f"Workspace '{workspace}' fully initialized")

    async def get_rag(self, workspace: str):
        """Get or create a rag instance for the given workspace."""
        if not workspace:
            workspace = "default"

        if not _WORKSPACE_RE.match(workspace):
            raise ValueError(
                f"Invalid workspace name '{workspace}'. "
                "Only a-z, A-Z, 0-9, and _ are allowed."
            )

        # Fast path: instance already exists
        if workspace in self._instances:
            # May still need async init if seeded
            if workspace in self._needs_init:
                await self.finalize_seed(workspace)
            return self._instances[workspace]

        # Slow path with per-workspace lock
        async with self._global_lock:
            if workspace not in self._locks:
                self._locks[workspace] = asyncio.Lock()
            lock = self._locks[workspace]

        async with lock:
            if workspace in self._instances:
                return self._instances[workspace]

            logger.info(f"Initializing workspace: {workspace}")
            rag = self._rag_cls(workspace=workspace, **self._rag_kwargs)
            await rag.initialize_storages()
            await rag.check_and_migrate_data()
            self._instances[workspace] = rag
            logger.info(f"Workspace '{workspace}' ready")
            return rag

    @property
    def workspaces(self) -> list[str]:
        return list(self._instances.keys())

    async def shutdown(self):
        for workspace, rag in self._instances.items():
            logger.info(f"Shutting down workspace: {workspace}")
            try:
                await rag.finalize_storages()
            except Exception as e:
                logger.error(f"Error finalizing workspace '{workspace}': {e}")
        self._instances.clear()
        self._locks.clear()


class WorkspaceProxy:
    """Proxy that delegates attribute access to the workspace-specific rag instance.

    Routes receive this object as ``rag``.  On every attribute access it
    looks up the current workspace (set by middleware via contextvars) and
    forwards the call to the real ContextGraph/LightRAG instance from the pool.
    """

    def __init__(self, pool: WorkspacePool):
        # Use object.__setattr__ to avoid triggering __setattr__ proxy
        object.__setattr__(self, "_pool", pool)

    def _get_current_rag(self):
        """Synchronously return the cached rag for the current workspace.

        Raises RuntimeError if the workspace hasn't been initialized yet
        (middleware should have done this).
        """
        workspace = _current_workspace.get()
        pool: WorkspacePool = object.__getattribute__(self, "_pool")
        if workspace in pool._instances:
            return pool._instances[workspace]
        raise RuntimeError(
            f"Workspace '{workspace}' not initialized. "
            "The workspace middleware should have called pool.get_rag() first."
        )

    def __getattr__(self, name):
        return getattr(self._get_current_rag(), name)

    def __setattr__(self, name, value):
        setattr(self._get_current_rag(), name, value)

    # Support isinstance() checks used by context_graph_routes._require_context_graph()
    def __class_getitem__(cls, item):
        return cls

    @property
    def __class__(self):
        """Report as the class of the underlying rag instance.

        This makes ``isinstance(proxy, ContextGraph)`` work correctly.
        """
        return type(self._get_current_rag())


def get_workspace_middleware(pool: WorkspacePool, default_workspace: str = "default"):
    """Return a **pure ASGI** middleware that sets the workspace context per request.

    Deliberately not a ``BaseHTTPMiddleware``: that wraps the ASGI receive channel
    and corrupts request-body streaming for mounted sub-apps (notably the MCP
    Streamable-HTTP transport — large bodies would truncate at a chunk boundary).
    This one reads only the ``LIGHTRAG-WORKSPACE`` header from ``scope`` and never
    touches ``receive``/``send``, so the body streams through untouched.
    """

    from starlette.responses import JSONResponse

    class WorkspaceMiddleware:
        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            if scope.get("type") != "http":
                await self.app(scope, receive, send)
                return

            headers = dict(scope.get("headers") or [])
            workspace = headers.get(b"lightrag-workspace", b"").decode("latin-1").strip()
            if not workspace:
                workspace = default_workspace

            if not _WORKSPACE_RE.match(workspace):
                resp = JSONResponse(
                    status_code=400,
                    content={"detail": f"Invalid workspace name '{workspace}'. "
                             "Only a-z, A-Z, 0-9, and _ are allowed."})
                await resp(scope, receive, send)
                return

            try:
                await pool.get_rag(workspace)
            except Exception as e:
                logger.error(f"Failed to initialize workspace '{workspace}': {e}")
                resp = JSONResponse(
                    status_code=500,
                    content={"detail": f"Failed to initialize workspace: {e}"})
                await resp(scope, receive, send)
                return

            token = _current_workspace.set(workspace)
            try:
                await self.app(scope, receive, send)
            finally:
                _current_workspace.reset(token)

    return WorkspaceMiddleware
