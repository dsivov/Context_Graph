"""Context Graph action layer — executable operations bound to object types (P3).

Actions turn a decision graph you *reason from* into one you *operate from*: a
named, typed operation (``ApproveOrder``, ``CancelShipment``) is invoked against
an ontology object type, authorized and recorded through the business-rules gate
(``emit_decision_trace``), and — on PASS/FLAG — runs an optional side effect.
The audit edge on the graph is the record of the executed action.

See ``docs/CLOSING_THE_GAPS.html`` §08 (P3 action / kinetic layer).

    from context_graph.actions import ActionCatalog, ActionDefinition, ActionParam, ActionService

    cat = ActionCatalog(name="sales").define(
        ActionDefinition("ApproveOrder", object_type="Order", relation_type="APPROVED",
                         effect="approval")
            .add(ActionParam("discount", kind="percent", required=True))
    )
"""

from context_graph.actions.schema import (
    ActionParam,
    ActionHandler,
    ActionDefinition,
    ActionCatalog,
)
from context_graph.actions.store import (
    ActionStore,
    JsonActionStore,
    InMemoryActionStore,
    validate_catalog,
)
from context_graph.actions.handler import (
    run_handler,
    HandlerError,
)
from context_graph.actions.service import ActionService

__all__ = [
    "ActionParam",
    "ActionHandler",
    "ActionDefinition",
    "ActionCatalog",
    "ActionStore",
    "JsonActionStore",
    "InMemoryActionStore",
    "validate_catalog",
    "run_handler",
    "HandlerError",
    "ActionService",
]
