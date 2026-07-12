# Governed-Workflow Platform — Work Plan

The build sequence for the platform designed in
[`GOVERNED_WORKFLOW_PLATFORM.html`](GOVERNED_WORKFLOW_PLATFORM.html) and architected in
[`PLATFORM_ARCHITECTURE.html`](PLATFORM_ARCHITECTURE.html). This is the full roadmap — from the first
event round-trip to the full platform — organized as **thin vertical slices**, each independently demonstrable
and each gated by a concrete test.

**Principles that gate every phase** (from the resolved decisions):
- **Ports first.** Land the interface + a lean in-process adapter before any consumer. Durability/broker adapters come later behind the same port.
- **No unbounded LLM at runtime.** The model enters only as a declared `agent` Action.
- **One diff gesture.** Authoring, migration, and re-approval share `DiffEngine`.
- **Record every hop.** Nothing runs without writing a quad `(h,r,t,rc)`.
- **Tests are the exit criteria**, not an afterthought — each phase names its gate.

**Conventions:** new code follows `context_graph/<pkg>/{schema,service,store}.py` (+ `agent.py` where an LLM authors).
Tests live in `context_graph/tests/`. Env in the `lightgraph_custom` conda env:
`… -m pytest context_graph/tests`.

---

## Phase overview

| Phase | Theme | Ships | Milestone |
|------|-------|-------|-----------|
| **P0** | Foundations & contracts | schemas, ports, in-process adapters | — |
| **P1** | The loop (thin vertical) | webhook → map → rule → decision trace | **M1 · loop proven** |
| **P2** | Flow engine MVP | executor over rules/lifecycle/actions + replay | **M2 · flows run** |
| **P3** | Stage-1 Studio | diff-and-approve authoring + flow canvas | **M3 · authoring** |
| **P4** | Stage-2 packaging | App bundle, Domain/Project/App, views | **M4 · demo-complete** |
| **P5** | Agent layer & integration breadth | agent Action, agent mapper, connectors, timers | **M5 · full runtime** |
| **P6** | Durability, migration, scale, v-next | migration tool, broker/durable adapters, diagrams, marketplace | **M6 · platform** |

The **demo that proves the thesis** (design doc §build path) is complete at **M4**: an event arrives from a webhook,
maps to ontology terms, hits a rule-gateway, routes to a human sign-off, lands in a terminal state, and the decision
trace shows who/why/when for every hop — no LLM on that path. P5–P6 thicken it.

---

## P0 · Foundations & contracts

**Goal:** every schema and port exists with a lean in-process adapter and unit tests — nothing wired end-to-end yet, but the seams are set.

**Deliverables**
- [ ] `events/schema.py` — `Event` (+ `mapping_meta`).
- [ ] `events/service.py` — `EventBus` Protocol + `InProcessBus`; `subscribe`/`publish`.
- [ ] `events/store.py` — `IngressLog` Protocol + durable default (JSON KV / table); **idempotency dedupe on `idempotency_key`**.
- [ ] `flows/schema.py` — `FlowNode`, `FlowEdge`, `FlowDefinition`, `Run`.
- [ ] `flows/store.py` — `RunStore` Protocol + in-memory/JSON default (incl. `due_timers`).
- [ ] `apps/schema.py` — `AppBundle`; `studio/schema.py` — `ArtifactDiff`.
- [ ] `actions/` — `AgentSpec` dataclass (handler not implemented yet).

**Test gate**
- [ ] `test_event_bus.py` — publish/subscribe round-trip; multiple subscribers.
- [ ] `test_ingress_log.py` — append is durable; **re-appending a seen `idempotency_key` returns `False` (dedupe)**; `replay(since)` yields in order.
- [ ] `test_run_store.py` — save/get round-trip; `due_timers` selects only past-due.

**Dependencies:** none. **Exit:** all ports importable, adapters green, no consumer yet.

---

## P1 · The loop (thin vertical) → **M1**

**Goal:** prove *events-in → deterministic-decision-out* with the smallest possible path. No flows yet.

**Deliverables**
- [ ] `integration/connectors/webhook.py` — inbound webhook connector (extends webingest base).
- [ ] `integration/mapper.py` — `DeterministicMapper` (field-map + `Property.coerce` against the domain ontology).
- [ ] `integration/service.py` — ingress service: connector → mapper → `IngressLog.append` → `EventBus.publish`.
- [ ] `api/routers/ingress_routes.py` — `POST /ingress/webhook/{connector}`, `GET /ingress/log`; register in the CG routes factory.
- [ ] A demo subscriber: on `Event`, call `RulesEngine.evaluate()` and `emit_decision_trace(...)`.

**Test gate**
- [ ] `test_loop_e2e.py` — POST a webhook payload → assert an event is logged, deduped on retry, a rule evaluates, and **a decision quad is written to the graph**.
- [ ] Determinism: same payload twice (same idempotency key) → one decision, not two.

**Dependencies:** P0. **Exit:** M1 — a curl to the webhook produces an auditable decision on the graph.

---

## P2 · Flow engine MVP → **M2**

**Goal:** the executor walks a real flow — event → task → gateway → state — with a persisted run and replay.

**Deliverables**
- [ ] `flows/service.py` — `FlowExecutor` (`start`, `advance`): node dispatch for `event`/`task`/`gateway`/`state` (timers stubbed).
  - [ ] gateway node → `RulesEngine.evaluate()`, branch on result via `FlowEdge.when`.
  - [ ] task node → `ActionDefinition` invoke (webhook/compute), record execution.
  - [ ] state node → `lifecycle` transition (guarded).
  - [ ] persist `Run` to `RunStore` at every wait/terminal; append to `Run.history`.
- [ ] Wire the bus subscriber from P1 to `FlowExecutor.start` (event → run).
- [ ] `api/routers/flow_routes.py` — `GET|POST /flows`, `POST /flows/{id}/dry-run`, `GET /runs`, `GET /runs/{id}`, `GET /runs/{id}/replay`.

**Test gate**
- [ ] `test_flow_executor.py` — the design-doc "governed request" flow: gateway routes >20% to VP-approval, ≤20% to auto; both reach `booked`.
- [ ] **Replay**: re-walking `Run.history` reproduces the identical terminal state + trace.
- [ ] Every hop wrote a quad; the run is reconstructable from the graph alone.

**Dependencies:** P0, P1. **Exit:** M2 — a golden flow runs deterministically and replays byte-identically.

---

## P3 · Stage-1 Studio (diff-and-approve) → **M3**

**Goal:** an expert + LLM author ontology / rules / flows through the one diff gesture, with sign-off and versioning.

**Deliverables**
- [ ] `studio/service.py` — `DiffEngine` (`propose`, `assess`, `apply`).
  - [ ] `propose` → route to `OntologyAuthor` / `RuleAuthor` / (new) flow-drafter by `kind`; return `ArtifactDiff`.
  - [ ] `assess` → re-run rule dry-runs / flow `test_cases`; set `behaviour_changed`.
  - [ ] `apply` → `RulesGate` check + human sign-off → new version; record approval as a decision.
- [ ] `studio/store.py` — versioned artifact history per `(kind, id)`; revert.
- [ ] `api/routers/studio_routes.py` — `/studio/propose|assess|apply`, `/studio/history/{kind}/{id}`.
- [ ] **WebUI:** extend Ontology & Rules surfaces to the diff-review loop; **new flow canvas** (BPMN-lite editor) — the one net-new screen; Studio shell with Domain/Project/App nav + history/revert.

**Test gate**
- [ ] `test_diff_engine.py` — propose→assess→apply produces a new version; a cosmetic diff has `behaviour_changed=False` (lightweight), a threshold change has `True` (full sign-off).
- [ ] Sign-off is recorded (who/why/when) as a decision.
- [ ] WebUI smoke: author a rule via chat → see the diff → dry-run → sign → it versions.

**Dependencies:** P0 (schemas), P2 (flows to author). **Exit:** M3 — a domain's blocks can be authored, tested, and signed in the Studio.

---

## P4 · Stage-2 packaging & namespaces → **M4 (demo-complete)**

**Goal:** compose signed blocks into an App inside the Domain/Project/App namespace; ship the end-user views.

**Deliverables**
- [ ] `apps/service.py` — compose `AppBundle` from signed block ids; validate all refs resolve & are signed; pin `meta_dsl_version` + `ontology_version`.
- [ ] `apps/store.py` — Project/App registry within a Domain (workspace); `sign`, `export`, `import` (bundle JSON).
- [ ] `api/routers/app_routes.py` — `/projects`, `/apps`, `/apps/{id}/sign|export`, `/apps/import`.
- [ ] **WebUI end-user views:** work queue, task/sign-off form, **decision-trace timeline** (who/why/when per hop).
- [ ] Connector bindings on the App route inbound events to the right app_id.

**Test gate**
- [ ] `test_app_bundle.py` — an App composed only of signed blocks validates; an unsigned ref is rejected; export→import round-trips.
- [ ] **Demo E2E**: webhook → mapped event → app-routed run → rule-gateway → human sign-off task → terminal state → trace timeline renders every hop, **no LLM in the path**.

**Dependencies:** P2, P3. **Exit:** **M4 — the demo that proves the thesis runs end to end.**

---

## P5 · Agent layer & integration breadth → **M5**

**Goal:** the LLM re-enters — but only as a boxed actor — and ingress grows past a single webhook.

**Deliverables**
- [ ] `actions/` — `agent` handler kind: run an `AgentSpec` (typed inputs from run vars, validate output against `output_schema`), confidence-route (`on_low_confidence` → human task), **record as a decision**.
- [ ] `integration/mapper.py` — `AgentMapper` (unstructured tail) **reusing the `AgentSpec` contract** + `ExtractionValidator`; confidence-route low results to a human review task.
- [ ] `flows/` — `timer` node + scheduler loop over `RunStore.due_timers`; parallel gateways (fan-out/join).
- [ ] `integration/connectors/` — poller + one messaging connector (email or Slack) on the connector base.

**Test gate**
- [ ] `test_agent_action.py` — an agent Action with a mocked LLM: valid output passes schema; invalid output is rejected; low confidence routes to a human task; execution recorded.
- [ ] `test_agent_mapper.py` — unstructured record → ontology-typed via mocked LLM; low-confidence → review task.
- [ ] `test_timers.py` — a timer node fires via `due_timers`; a parallel gateway joins correctly.

**Dependencies:** P2 (executor), P4 (apps/views for human-task routing). **Exit:** M5 — full runtime: agents, timers, parallel flows, multiple connectors.

---

## P6 · Durability, migration, scale, v-next → **M6 (platform)**

**Goal:** production-hardening and the deferred capabilities — all behind the ports already in place.

**Deliverables**
- [ ] **Migration tool** — `migration/service.py`: on a meta-DSL/ontology version bump, produce a migration `ArtifactDiff` per affected block/app; approve through `DiffEngine` (reuses P3). Ontology: additive auto, breaking → reviewed old→new mapping. In-flight runs pin-to-start (already true).
- [ ] `api/routers/migration_routes.py` — `/migration/diff`, `/migration/apply`.
- [ ] **Durable adapters** (swap behind ports): `RedisStreamsBus` (`EventBus`), durable `RunStore` (Postgres or a Temporal-style engine) — *only if a domain's revisit trigger fires* (long-running/high-concurrency).
- [ ] **v-next UX:** visual diagram representations for **flows and states** (beyond the editor canvas).
- [ ] **Marketplace / shared ontology:** cross-domain bundle sharing on the export format; shared-ontology independent versioning.

**Test gate**
- [ ] `test_migration.py` — additive ontology change auto-migrates; a breaking rename requires a reviewed mapping; a pinned in-flight run is unaffected; historical objects remain interpretable under their block-version.
- [ ] Adapter parity: the port contract tests (P0) pass against the durable adapters unchanged.

**Dependencies:** P3 (DiffEngine), P4 (bundles). **Exit:** M6 — versioned, migratable, swappable-durability platform.

---

## Cross-cutting (every phase)

- **Testing:** each new module ships unit tests; each phase ships its E2E gate above. Keep the full suite green (`pytest context_graph/tests`). Mock the LLM in all agent/author tests — no live calls in CI.
- **RBAC:** reuse object-level `rbac/` — sign-off, app publish, and connector config are role-gated; assignees on human tasks are roles.
- **Audit/observability:** every hop is already a decision quad; add a run/authoring activity view. Log via `lightrag.utils.logger`, never `print`.
- **Security:** webhook ingress authenticated (X-API-Key, reuse MCP pattern); `agent`/webhook egress respects the existing `allow_internal` guard; mapping-LLM output validated before it touches the graph.
- **Docs:** update `CLAUDE.md` module map and the design/architecture docs as packages land; a short per-package README.

## Dependency graph

```
P0 ──▶ P1 ──▶ P2 ──▶ P3 ──▶ P4 ──▶ P5 ──▶ P6
        │      │      ▲       ▲
        └──────┴──────┘       │
        (P3 authors the flows P2 runs; P4 composes P3's signed blocks)
```
Critical path to the demo: **P0 → P1 → P2 → P3 → P4 (M4)**. P5/P6 are additive.

## Risk register (top few)

| Risk | Mitigation |
|------|-----------|
| Flow executor scope-creep toward full BPMN | five node kinds only; the DSL stays small (decision 1) |
| Agent nodes erode the determinism story | contract + schema-validation + confidence-routing + audit; keep off certifiable paths (decision 3) |
| Studio UX is the long pole | reuse Ontology/Rules UIs; only the flow canvas is net-new (decision 7) |
| Migration rot returns | one diff engine, pin-the-version-above, behaviour-gated re-approval (decisions 4/5) |
| In-process bus loses events | durable ingress log from P0; connector re-delivery as backstop (decision 2) |

---

*Companion docs:* [design discussion](GOVERNED_WORKFLOW_PLATFORM.html) · [architecture](PLATFORM_ARCHITECTURE.html).
