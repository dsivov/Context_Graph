"""Context Graph lifecycle — declarative state machines over object types (P3, Gap 2).

Each object type may declare legal states + transitions (optionally
role-restricted). A transition action is checked against the machine (is
``from → to`` legal for this role?) and, on success, the object's state property
is updated on its graph node. No machine for a type → permissive. See
``docs/LIFECYCLE_SPEC.md``.

    from context_graph.lifecycle import Lifecycle, LifecycleService

    lc = Lifecycle.from_dict({"machines": {"Task": {
        "states": ["proposed","active","done"], "initial": "proposed",
        "transitions": [{"from":"proposed","to":"active"},
                        {"from":"active","to":"done","roles":["integrator"]}]}}})
    lc.check("Task", "proposed", "done").allowed          # False (undeclared)
    lc.check("Task", "active", "done", role="developer")  # False (role)
"""

from context_graph.lifecycle.schema import (
    Transition,
    StateMachine,
    Lifecycle,
    Decision,
)
from context_graph.lifecycle.store import (
    LifecycleStore,
    JsonLifecycleStore,
    InMemoryLifecycleStore,
    validate_lifecycle,
)
from context_graph.lifecycle.service import LifecycleService

__all__ = [
    "Transition",
    "StateMachine",
    "Lifecycle",
    "Decision",
    "LifecycleStore",
    "JsonLifecycleStore",
    "InMemoryLifecycleStore",
    "validate_lifecycle",
    "LifecycleService",
]
