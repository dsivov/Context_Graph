# Spec — Object-level RBAC (P3, Gap 1)

Implementation spec for the per-workspace access-control layer. Design rationale
and the settled decisions live in
[`AGENTIC_PROJECT_GRAPH.html` § Closing the gaps · Gap 1](AGENTIC_PROJECT_GRAPH.html#gaps);
this document is the buildable version.

## 0. Goal & non-goals

**Goal.** A generic layer that answers *"may this authenticated principal attempt
this operation?"* — enforced at the action-invoke chokepoint, per workspace,
opt-in, deny-by-default within a policy.

**In the first slice**
- Static `role → grants` over **actions** (the `/actions/invoke` path).
- Principal = the **authenticated** identity's role (JWT claim), never a client field.
- `POST/GET/DELETE /rbac` + `POST /rbac/check`.

**Deferred (phase 2 / later)**
- **ReBAC** — relationship-derived grants ("may act on objects you `own`") via a graph query.
- Gating **reads** (query-result filtering) and **direct graph CRUD**.
- Manifest integration (filtering a role's advertised actions).

**Non-goals.** Replacing the agent harness's local tool/branch enforcement
(`role_enforcer.py`). CG governs project-*state* actions; the harness governs
local tools. Two complementary layers.

## 1. Invariants (must hold)

1. **No policy → permissive.** A workspace without an `rbac` policy allows
   everything (single-agent stays zero-config).
2. **Policy present → deny-by-default.** Only granted operations pass.
3. **Principal is authenticated, not asserted.** The role comes from the
   validated token (`auth.py`), never from `actor` in the body or a
   `LIGHTRAG-ROLE` header. `actor` remains descriptive edge data; RBAC ignores it.
4. **Wildcards keep it terse.** `manager: ["*"]` is one line; you enumerate the
   *restricted* roles.
5. **RBAC needs identity.** If a workspace has a policy but the request carries no
   authenticated role, **deny** with a clear error — real RBAC can't run anonymously.

## 2. Package — `context_graph/rbac/`

Mirrors `context_graph/{rules,ontology,actions}` exactly.

```
context_graph/rbac/
├── __init__.py       # exports
├── schema.py         # RbacPolicy, RoleGrants, Grant, check()
├── store.py          # RbacStore (abc) + JsonRbacStore + InMemoryRbacStore
└── service.py        # RbacService (check, get_summary, save, delete; caches)
```

### 2.1 `schema.py`

Grant grammar (a string, wildcard-friendly): `"<verb>:<target>"`, or `"*"`.
- `verb ∈ {invoke, create, update, delete, read, *}`
- `target` = an action name, an object-type name, or `*`
- `"*"` alone = superuser.

```python
_VERBS = ("invoke", "create", "update", "delete", "read")

@dataclass
class Grant:
    verb: str            # one of _VERBS or "*"
    target: str          # action / object-type name or "*"
    @classmethod
    def parse(cls, s: str) -> "Grant":
        if s.strip() == "*": return cls("*", "*")
        verb, _, target = s.partition(":")
        # validate verb; default target "*" if omitted
        ...
    def matches(self, verb: str, target: str) -> bool:
        return (self.verb in ("*", verb)) and (self.target in ("*", target))

@dataclass
class RoleGrants:
    grants: List[Grant] = field(default_factory=list)
    rebac:  List["RebacGrant"] = field(default_factory=list)   # phase 2, see §7
    def allows(self, verb, target) -> bool:
        return any(g.matches(verb, target) for g in self.grants)

@dataclass
class RbacPolicy:
    name: str = "rbac"
    version: int = 1
    default_deny: bool = True                 # informational; deny-default is enforced
    roles: Dict[str, RoleGrants] = field(default_factory=dict)

    def check(self, role: Optional[str], verb: str, target: str) -> "Decision":
        if role is None:
            return Decision(False, "no authenticated role for an RBAC-enabled workspace")
        rg = self.roles.get(role)
        if rg is None:
            return Decision(False, f"role '{role}' has no grants")
        if rg.allows(verb, target):
            return Decision(True, "granted")
        return Decision(False, f"role '{role}' lacks {verb}:{target}")

    def lint(self) -> List[str]: ...       # unknown verbs, empty roles
    def to_dict/from_dict ...

@dataclass
class Decision:
    allowed: bool
    reason: str
```

`from_dict` accepts the ergonomic shape (grants as strings):
```json
{ "name": "rbac", "roles": {
    "manager": ["*"],
    "developer": ["invoke:ProposeAPI", "invoke:AdvanceTask", "create:Task"],
    "production-engineer": ["invoke:MergeToMain"] } }
```

### 2.2 `store.py`
Copy `context_graph/actions/store.py` verbatim, renaming `catalog → policy`,
file `rbac_<workspace>.json`, `validate_policy = lambda p: raise if p.lint()`.
Same abstract base + `JsonRbacStore(base_dir)` + `InMemoryRbacStore`, versioned,
atomic write, `_WS_SANITIZE_RE`.

### 2.3 `service.py`
```python
class RbacService:
    def __init__(self, store): self._store = store
    def get_summary(self, ws) -> dict            # roles, grants, version, exists
    def save(self, ws, policy_dict) -> RbacPolicy # RbacPolicy.from_dict -> store.save
    def delete(self, ws) -> bool

    def check(self, ws, role, verb, target, *, object_ref=None, rag=None) -> Decision:
        pol = self._store.load(ws)
        if pol is None:
            return Decision(True, "no RBAC policy — permissive")   # invariant #1
        d = pol.check(role, verb, target)
        if d.allowed or not pol.roles.get(role, RoleGrants()).rebac:
            return d
        return self._check_rebac(pol, role, verb, target, object_ref, rag)  # §7
```
Cache one built policy per `(ws, version)` like `RulesService`.

## 3. Principal resolution (`auth.py` / `utils_api.py`)

The JWT already carries `role` (`TokenPayload.role`; `validate_token` returns it).
Two small additions:

1. **Surface the principal to routes.** In `get_combined_auth_dependency`, after a
   token validates, set `request.state.principal = {"username": …, "role": …}`
   (guest/whitelist → `role=None`). Add a tiny dependency:
   ```python
   def get_principal(request: Request) -> Optional[dict]:
       return getattr(request.state, "principal", None)
   ```
2. **Map agent accounts → CG roles.** Each agent logs in as its own account; its
   token's `role` is the CG role (`developer`, `architect`, …). This is an
   account-config concern (extend `AUTH_ACCOUNTS`/account metadata to set the CG
   role at token creation). No new transport — the role rides the existing token.

> The role is read **only** from `request.state.principal`. A `LIGHTRAG-ROLE`
> header, or `actor` in the body, is never trusted for authz.

## 4. Enforcement — the invoke pre-check

RBAC is a **route-level guard before** `ActionService.invoke` (which runs the
rules gate). This keeps `ActionService` unchanged and matches the chain
*resolve principal → RBAC → rules gate → side effect*.

In `actions_routes.py :: invoke_action`:
```python
principal = get_principal(request)            # {username, role} or None
role = principal.get("role") if principal else None
d = rbac_service.check(_ws(), role, "invoke", request.action,
                       object_ref=request.object_ref, rag=rag)
if not d.allowed:
    raise HTTPException(status_code=403, detail=d.reason)
# ... then the existing service.invoke(...)
```
- `rbac_service` is passed into `create_actions_routes(rag, action_service, rbac_service=None, …)`.
  When `rbac_service is None` (RBAC unavailable), the check is skipped — permissive.
- `403` is the deny status (distinct from the rules gate's `422`).

## 5. API — `context_graph`/`lightrag/api/routers/rbac_routes.py`

Mirror `ontology_routes.py`; workspace-scoped; `_require_cg` guard; `combined_auth`.

| Method · Path | Body | Returns |
|---|---|---|
| `GET /rbac` | — | summary: `{workspace, exists, version, roles:{name:[grants]}}` |
| `POST /rbac` | `{policy: {...}}` | summary (`400` on bad policy) |
| `DELETE /rbac` | — | `{deleted, workspace}` (→ reverts to permissive) |
| `POST /rbac/check` | `{role, verb, target, object_ref?}` | `{allowed, reason}` (dry-run; used by tests + the manifest) |

## 6. Server wiring (`lightrag_server.py`)

Alongside the rules/ontology/action services (CG mode only):
```python
from context_graph.rbac import RbacService, JsonRbacStore
rbac_service = RbacService(JsonRbacStore(os.path.join(working_dir, "rbac")))
...
app.include_router(create_rbac_routes(rag, rbac_service, api_key=api_key))
# pass rbac_service into the actions router:
app.include_router(create_actions_routes(rag, action_service, rbac_service=rbac_service, api_key=api_key))
```

## 7. ReBAC (phase 2)

A grant may be relationship-derived:
```json
{ "verb": "invoke", "target": "DeprecateAPI", "via": "owns", "of": "Module" }
```
Meaning: *may DeprecateAPI on an API reachable from the principal's Role node via
`owns → Module → exposes → API`*. `_check_rebac` runs a bounded graph query
(`rag.chunk_entity_relation_graph`) from the Role node named `role`, along the
`via` edge to `of`-typed nodes, then to `object_ref`; allow if reachable. Needs
`rag`; skipped when `rag is None`. Ship **after** static grants prove out.

## 8. Test plan — `tests/test_rbac.py` (asyncio.run pattern, no plugin)

- **schema**: grant parse (`"*"`, `"invoke:X"`, bad verb); `RoleGrants.allows` wildcards; `RbacPolicy.check` (None role, unknown role, granted, denied).
- **store**: save versions; load/delete; roundtrip.
- **service.check**: no policy → allowed; policy + no role → denied; wildcard role → allowed; specific grant hit/miss.
- **HTTP** (TestClient + fake rag): `GET/POST/DELETE /rbac`; `/rbac/check`; and an `/actions/invoke` **403** when the role lacks the grant, **200** when granted, **permissive** when no policy. `503` without CG mode.

## 9. Preset piece — `presets/agentic-dev/rbac.json`

```json
{ "name": "rbac", "roles": {
    "manager":             ["*"],
    "architect":           ["invoke:ApproveArchitecture", "invoke:ProposeAPI", "invoke:DeprecateAPI"],
    "developer":           ["invoke:ProposeAPI", "invoke:AdvanceTask", "invoke:CreateChangeRequest"],
    "integrator":          ["invoke:AdvanceTask"],
    "devop":               ["invoke:AdvanceTask"],
    "production-engineer": ["invoke:MergeToMain", "invoke:AdvanceTask"] } }
```
Installed by `install.py` once the layer exists (add an `rbac.json` step:
`POST /rbac {policy: …}`). Reverts to permissive on `DELETE /rbac`.

## 10. Phasing & effort

| Phase | Scope | Rough size |
|-------|-------|-----------|
| **A** | schema + store + service (static) + `/rbac` API + wiring | ~1 day — mostly mirrors existing packages |
| **B** | `/actions/invoke` pre-check + principal surfacing in `auth.py` | ~½ day — small, security-sensitive |
| **C** | tests | ~½ day |
| **D** | ReBAC (graph-derived grants) | ~1 day — after A–C prove out |
| **E** | manifest integration (filter role's actions by grants) | folds into the manifest work |

Phase A–C is the shippable slice: opt-in, deny-default, wildcard, action-scoped,
authenticated-principal — the layer the `agentic-dev` preset's `rbac.json` needs.
