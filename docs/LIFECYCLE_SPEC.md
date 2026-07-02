# Spec — Lifecycle layer (P3, Gap 2)

Implementation spec for declarative state machines over object types. Design and
the settled decisions live in
[`AGENTIC_PROJECT_GRAPH.html` § Closing the gaps · Gap 2](AGENTIC_PROJECT_GRAPH.html#gaps).

## 0. Goal & non-goals

**Goal.** A generic, thin state-machine layer: each object type may declare
legal states + transitions; a transition **action** is checked against it
(is `from → to` legal? is this role allowed?) and, on success, the object's
state property is updated on its graph node.

**In the first slice**
- Per-object-type state machines: `states`, `initial`, `transitions[{from, to, roles?}]`.
- A transition guard wired into `/actions/invoke` for actions that declare a
  `transition`. Reads current state from the graph node, applies the new state
  after a successful emit.
- `/lifecycle` API (CRUD + `/lifecycle/check`).

**Deferred** — cross-object entry conditions (`requires`, a graph query, e.g.
"Feature → in_progress needs an accepted Decision"): parsed + stored, not yet
enforced (like ReBAC). The declared-transition + role check ships now.

**Non-goals.** A general workflow/BPMN engine (timers, parallelism, compensation).
Keep it a thin FSM + guards.

## 1. Invariants

1. **No machine for a type → permissive** (nothing to enforce).
2. **Declared transitions only** — an undeclared `from → to` is rejected.
3. **Roles are authenticated** — a transition's `roles` restriction checks the
   principal role (from the token, via `get_principal`), never `actor`.
4. **Advisory by default, hard for invariants** — the illegal-transition check is
   a hard `409`; the harness already hard-gates git/branch, so CG leans to
   flag-and-explain elsewhere. (This slice enforces legal transitions hard;
   cross-object `requires` will be advisable/hard per rule.)

## 2. Package — `context_graph/lifecycle/`

Mirrors `context_graph/{rbac,rules,ontology,actions}`.

```
context_graph/lifecycle/
├── __init__.py
├── schema.py   # Transition, StateMachine, Lifecycle, Decision
├── store.py    # LifecycleStore (abc) + Json + InMemory (lifecycle_<ws>.json)
└── service.py  # LifecycleService (check, current_state, apply, get_summary, save, delete)
```

### 2.1 `schema.py`
```python
@dataclass
class Transition:
    from_state: str
    to: str
    roles: List[str] = field(default_factory=list)     # empty = any role
    requires: List[str] = field(default_factory=list)  # cross-object guards (phase 2)

@dataclass
class StateMachine:
    object_type: str
    prop: str = "state"          # the node property that holds the state
    states: List[str] = []
    initial: str = ""
    transitions: List[Transition] = []
    def is_state(self, s) -> bool
    def can(self, from_state, to, role) -> Decision:
        # to in states? transition declared? role allowed? -> Decision(allowed, reason)
    def lint(self) -> List[str]   # initial in states; every from/to in states

@dataclass
class Lifecycle:
    name: str = "lifecycle"
    version: int = 1
    machines: Dict[str, StateMachine] = {}      # keyed by object_type
    def machine_for(self, object_type) -> Optional[StateMachine]
    def check(self, object_type, from_state, to, role) -> Decision:
        m = self.machine_for(object_type)
        return Decision(True, "no state machine") if m is None else m.can(from_state, to, role)
    def lint(self) -> List[str]
    def to_dict/from_dict
```
`from_dict` accepts the ergonomic shape:
```json
{ "machines": { "Task": {
    "states": ["proposed","active","blocked","done"], "initial": "proposed",
    "transitions": [
      {"from":"proposed","to":"active"}, {"from":"active","to":"blocked"},
      {"from":"blocked","to":"active"},
      {"from":"active","to":"done","roles":["integrator","manager"]} ] } } }
```

### 2.2 `store.py`
Copy an existing store (`rbac/store.py`), rename `policy → lifecycle`, file
`lifecycle_<ws>.json`, `validate` runs `Lifecycle.lint()`.

### 2.3 `service.py`
```python
class LifecycleService:
    def get_summary(ws), save(ws, dict), delete(ws)
    def machine_for(ws, object_type) -> Optional[StateMachine]
    def check(ws, object_type, from_state, to, *, role=None) -> Decision   # permissive if no lifecycle/machine
    async def current_state(rag, object_ref, machine) -> str:
        node = await rag.chunk_entity_relation_graph.get_node(object_ref)
        return (node or {}).get(machine.prop) or machine.initial
    async def apply(rag, object_ref, machine, to) -> None:
        node = await rag.chunk_entity_relation_graph.get_node(object_ref) or {}
        node = {**node, machine.prop: to}
        await rag.chunk_entity_relation_graph.upsert_node(object_ref, node)
```

## 3. Integration — the transition guard in `ActionService.invoke`

An action opts in by declaring a transition (`context_graph/actions/schema.py`):
```python
@dataclass
class ActionTransition:
    to_param: str                 # which arg holds the target state
# ActionDefinition gains: transition: Optional[ActionTransition] = None
```

`ActionService.invoke(rag, ws, name, *, actor, object_ref, args,
principal_role=None, lifecycle=None)` — new params. When `action.transition` and
`lifecycle` are set and a machine exists for `action.object_type`:

```
target  = coerced[action.transition.to_param]
current = await lifecycle.current_state(rag, object_ref, machine)
d = machine.can(current, target, principal_role)
if not d.allowed:
    return {"ok": False, "error": "illegal_transition",
            "reason": d.reason, "from": current, "to": target}   # route → 409
# ... existing: emit_decision_trace (rules gate) ...
# after a PASS/FLAG emit:
await lifecycle.apply(rag, object_ref, machine, target)          # write new state
result["from"], result["to"] = current, target
```

Order: **RBAC (route) → lifecycle check (invoke) → rules gate (emit) → apply +
side effect.** RBAC = "may invoke at all"; lifecycle = "is this transition legal
for this role". Non-transition actions skip lifecycle entirely.

The route (`actions_routes.py`) resolves `principal_role` once (already does for
RBAC) and passes `principal_role` + `lifecycle_service` into `invoke`; maps
`illegal_transition → 409`.

## 4. API — `lifecycle_routes.py`

Mirror `rbac_routes.py`.

| Method · Path | Body | Returns |
|---|---|---|
| `GET /lifecycle` | — | summary: machines → states/initial/transitions |
| `POST /lifecycle` | `{lifecycle: {...}}` | summary (`400` on bad) |
| `DELETE /lifecycle` | — | `{deleted, workspace}` |
| `POST /lifecycle/check` | `{object_type, from, to, role?}` | `{allowed, reason}` |

## 5. Server wiring
Construct `LifecycleService(JsonLifecycleStore(working_dir/lifecycle))` in CG
mode; include `create_lifecycle_routes(...)`; pass `lifecycle_service` into
`create_actions_routes(..., lifecycle_service=…)`.

## 6. Tests — `tests/test_lifecycle.py` (asyncio.run pattern)
- schema: `StateMachine.can` (unknown state, undeclared transition, role gate, ok); lint.
- store: versions; load/delete.
- service: permissive with no machine; `current_state` default→initial; `apply` writes.
- HTTP: `/lifecycle` CRUD + `/lifecycle/check`; `/actions/invoke` with a transition action → **409** on an illegal jump, **200** + state applied on a legal one; permissive with no lifecycle.

## 7. Preset — `presets/agentic-dev/lifecycle.json`
```json
{ "name": "lifecycle", "machines": {
    "Task":    { "states": ["proposed","active","blocked","done"], "initial": "proposed",
                 "transitions": [ {"from":"proposed","to":"active"}, {"from":"active","to":"blocked"},
                                  {"from":"blocked","to":"active"},
                                  {"from":"active","to":"done","roles":["integrator","manager"]} ] },
    "Feature": { "states": ["proposed","accepted","in_progress","shipped","superseded"], "initial":"proposed",
                 "transitions": [ {"from":"proposed","to":"accepted"}, {"from":"accepted","to":"in_progress"},
                                  {"from":"in_progress","to":"shipped"} ] } } }
```
`actions.json`: give `AdvanceTask` `"transition": {"to_param": "to"}`. Installer
gains a `lifecycle.json` step (`POST /lifecycle {lifecycle: …}`), after rbac.

## 8. Effort
schema+store+service (~½d) · invoke integration + routes + wiring (~½d) · tests
(~½d) · preset + live verify (~¼d). Cross-object `requires` is a later add.
