from .lightrag import LightRAG as LightRAG, QueryParam as QueryParam
from context_graph.types import (
    RelationContext as RelationContext,
    ContextNode as ContextNode,
    ContextEdge as ContextEdge,
)


def __getattr__(name: str):
    # ``ContextGraph`` lives in ``context_graph.core``, which imports from
    # ``lightrag`` — importing it eagerly here would create a circular import
    # (lightrag/__init__ → context_graph.core → lightrag.base → lightrag/__init__).
    # Resolve it lazily so ``from lightrag import ContextGraph`` still works,
    # but only after this package has finished initializing.
    if name == "ContextGraph":
        from context_graph.core import ContextGraph

        return ContextGraph
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__version__ = "1.4.10"
__author__ = "Zirui Guo"
__url__ = "https://github.com/HKUDS/LightRAG"
