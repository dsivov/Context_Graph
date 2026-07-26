"""FlowExecutor — the flow engine that walks a :class:`FlowDefinition` (P2).

The executor is the *conductor* over the layers that already exist: it does not
re-implement rules, actions, or lifecycle — it dispatches to them and records a
replayable :class:`Run`. Node kinds:

* ``event``   — the entry; seeds the run and steps to its single successor.
* ``task``    — dispatch to :meth:`ActionService.invoke` (which authorizes via
                the rules gate, guards+applies any lifecycle transition, writes
                the decision quad, then runs the handler). We never double-emit.
* ``gateway`` — evaluate the workspace rules against ``run.vars`` and pick the
                out-edge whose ``when`` matches, in order: a triggered rule's
                action *reason* → rule *name* → the overall *outcome* → ``else``.
* ``state``   — a guarded lifecycle transition onto the object's node.
* ``timer``   — stubbed: park the run as ``waiting`` with ``wake_at`` (P5 wakes it).

Every hop emits one audit quad ``run:{run_id} -[flow_step:{kind}]-> {node}`` via
``emit_decision_trace`` (task hops instead carry the action's own quad, so we
don't double-emit), and appends to ``run.history``. A run is therefore
reconstructable from the graph alone, and :meth:`replay` re-walks the recorded
history against the pinned flow version to reproduce the terminal state + trace.

See docs/PLATFORM_WORK_PLAN.md (P2) and docs/GOVERNED_WORKFLOW_PLATFORM.html.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

from lightrag.utils import logger

from context_graph.flows.schema import FlowDefinition, FlowEdge, FlowNode, Run
from context_graph.types import RelationContext


@dataclass
class ReplayResult:
    """The outcome of re-walking a run's history against its pinned flow."""

    ok: bool
    status: str
    state: Optional[str]
    cursor: str
    path: List[str]                     # node ids visited, in order
    mismatches: List[str]               # empty when the replay reproduced the run


class FlowExecutor:
    """Walks flows, composing the rules / actions / lifecycle services.

    The services are optional so the executor degrades gracefully in tests and
    in non-CG deployments: a missing rules service makes every gateway fall to
    its ``else`` branch; a missing action service turns tasks into no-ops.
    """

    def __init__(
        self,
        flow_store,
        run_store,
        *,
        rag_resolver: Callable[[str], Any],
        rules_service: Any = None,
        action_service: Any = None,
        lifecycle_service: Any = None,
        now: Callable[[], float] = time.time,
        max_hops: int = 100,
    ) -> None:
        self._flows = flow_store
        self._runs = run_store
        self._resolve_rag = rag_resolver
        self._rules = rules_service
        self._actions = action_service
        self._lifecycle = lifecycle_service
        self._now = now
        self._max_hops = max_hops

    @property
    def run_store(self):
        return self._runs

    @property
    def flow_store(self):
        return self._flows

    # -- lifecycle of a run --------------------------------------------------

    async def start(
        self,
        workspace: str,
        flow: FlowDefinition,
        *,
        event: Any = None,
        vars: Optional[Dict[str, Any]] = None,
        app_id: str = "",
    ) -> Run:
        """Start (or resume) a run for *flow*. Idempotent on ``run_id``.

        The run id pins the flow version and folds in the triggering event's
        dedupe key, so a re-delivered event never starts a second run — the
        existing run is returned untouched.
        """
        entry = flow.entry()
        if entry is None:
            raise ValueError(f"flow '{flow.id}' has no event node")

        seed: Dict[str, Any] = {}
        if event is not None and getattr(event, "payload", None):
            seed.update(event.payload)
        if vars:
            seed.update(vars)

        dedupe = event.dedupe_key() if event is not None else self._vars_key(seed)
        run_id = f"{flow.id}@v{flow.version}:{dedupe}"

        existing = await self._runs.get(workspace, run_id)
        if existing is not None:
            logger.debug(f"flow start: run '{run_id}' already exists — idempotent no-op")
            return existing

        run = Run(
            run_id=run_id,
            app_id=app_id or (getattr(event, "app_id", None) or ""),
            flow_id=flow.id,
            flow_version=flow.version,
            cursor=entry.id,
            vars=seed,
        )
        await self._runs.save(workspace, run)
        return await self.advance(workspace, run, flow)

    async def advance(
        self,
        workspace: str,
        run: Run,
        flow: Optional[FlowDefinition] = None,
    ) -> Run:
        """Walk from the run's cursor until it waits or terminates, persisting
        at the end (and at every wait/terminal, which is where the walk stops)."""
        if flow is None:
            flow = self._flows.get(workspace, run.flow_id, run.flow_version)
        if flow is None:
            run.status = "failed"
            run.record(run.cursor, "error", {"error": "flow definition not found"})
            await self._runs.save(workspace, run)
            return run

        rag = self._resolve_rag(workspace) if self._resolve_rag else None
        hops = 0
        while run.status == "running":
            if hops >= self._max_hops:
                run.status = "failed"
                run.record(run.cursor, "error", {"error": "max hops exceeded"})
                break
            hops += 1
            node = flow.node(run.cursor)
            if node is None:
                run.status = "failed"
                run.record(run.cursor, "error", {"error": "cursor node missing"})
                break
            nxt = await self._dispatch(workspace, run, flow, node, rag)
            if nxt is None:
                break                       # terminal, waiting, or halted
            run.cursor = nxt

        await self._runs.save(workspace, run)
        return run

    # -- node dispatch -------------------------------------------------------

    async def _dispatch(
        self, workspace: str, run: Run, flow: FlowDefinition, node: FlowNode, rag: Any
    ) -> Optional[str]:
        if node.kind == "event":
            await self._emit_hop(rag, workspace, run, node, detail="run started")
            run.record(node.id, "event", {})
            return self._single_next(flow, node, run)
        if node.kind == "task":
            return await self._run_task(workspace, run, flow, node, rag)
        if node.kind == "gateway":
            return await self._run_gateway(workspace, run, flow, node, rag)
        if node.kind == "state":
            return await self._run_state(workspace, run, flow, node, rag)
        if node.kind == "timer":
            return self._run_timer(run, node)
        run.status = "failed"
        run.record(node.id, "error", {"error": f"unknown node kind '{node.kind}'"})
        return None

    async def _run_task(
        self, workspace: str, run: Run, flow: FlowDefinition, node: FlowNode, rag: Any
    ) -> Optional[str]:
        if not node.ref or self._actions is None:
            run.record(node.id, "task", {"skipped": True, "reason": "no action service/ref"})
            return self._single_next(flow, node, run)

        actor = str(self._resolve(node.config.get("actor", "system"), run.vars) or "system")
        obj = self._resolve(node.config.get("object"), run.vars)
        object_ref = str(obj) if obj is not None else node.ref
        args = self._resolve(node.config.get("args", {}), run.vars)
        role = self._resolve(node.config.get("role"), run.vars)

        result = await self._actions.invoke(
            rag, workspace, node.ref,
            actor=actor,
            object_ref=object_ref,
            args=args if isinstance(args, dict) else {},
            principal_role=str(role) if role is not None else None,
            lifecycle=self._lifecycle,
        )

        # The action already wrote its decision quad — do NOT double-emit a hop.
        run.record(node.id, "task", {
            "action": node.ref, "object": object_ref,
            "ok": result.get("ok"), "outcome": result.get("outcome"),
        })

        if result.get("ok"):
            coerced = result.get("coerced")
            if isinstance(coerced, dict):
                run.vars.update(coerced)      # expose results to downstream bindings
            if result.get("to"):
                run.state = result["to"]      # a transitioning action moved the object
            return self._single_next(flow, node, run)

        # A REJECT / invalid args / illegal transition halts the run.
        run.status = "failed"
        run.record(node.id, "halt", {"error": result.get("error"), "audit": result.get("audit")})
        return None

    async def _run_gateway(
        self, workspace: str, run: Run, flow: FlowDefinition, node: FlowNode, rag: Any
    ) -> Optional[str]:
        gate = self._rules.gate_for(workspace) if self._rules is not None else None
        result = gate.engine.evaluate(run.vars) if gate is not None else None
        edge = self._pick_branch(flow, node, result)
        branch = edge.when if edge is not None else None

        await self._emit_hop(
            rag, workspace, run, node,
            detail=f"branch={branch} outcome={result.outcome if result else 'no-gate'}",
        )
        run.record(node.id, "gateway", {
            "branch": branch,
            "outcome": result.outcome if result else None,
            "rules": [m.rule for m in result.triggered] if result else [],
        })

        if edge is None:
            run.status = "failed"
            run.record(node.id, "error", {
                "error": "no matching gateway branch",
                "outcome": result.outcome if result else None,
            })
            return None
        return edge.dst

    async def _run_state(
        self, workspace: str, run: Run, flow: FlowDefinition, node: FlowNode, rag: Any
    ) -> Optional[str]:
        target = node.ref
        obj = self._resolve(node.config.get("object"), run.vars)
        object_type = node.config.get("object_type")
        role = self._resolve(node.config.get("role"), run.vars)
        applied = False

        if (self._lifecycle is not None and obj is not None and object_type
                and target is not None):
            machine = self._lifecycle.machine_for(workspace, object_type)
            if machine is not None:
                current = await self._lifecycle.current_state(rag, str(obj), machine)
                decision = machine.can(current, target, str(role) if role is not None else None)
                if not decision.allowed:
                    run.status = "failed"
                    run.record(node.id, "state", {
                        "target": target, "allowed": False,
                        "reason": decision.reason, "from": current,
                    })
                    return None
                await self._lifecycle.apply(rag, str(obj), machine, target)
                applied = True

        run.state = target
        await self._emit_hop(rag, workspace, run, node, detail=f"state={target}")
        run.record(node.id, "state", {"target": target, "applied": applied})
        return self._single_next(flow, node, run)

    def _run_timer(self, run: Run, node: FlowNode) -> Optional[str]:
        """Park the run until ``wake_at``; the P5 scheduler advances it later.

        The cursor is left ON the timer node so the scheduler re-enters here;
        the executor must skip a timer whose wake has already passed to avoid a
        re-park loop — that is the scheduler's job (out of P2 scope).
        """
        seconds = self._duration_seconds(node.ref) or self._resolve(
            node.config.get("seconds"), run.vars
        )
        wake_at = None
        if seconds:
            wake_at = (
                datetime.fromtimestamp(self._now(), tz=timezone.utc)
                + timedelta(seconds=float(seconds))
            ).isoformat()
        run.status = "waiting"
        run.wake_at = wake_at
        run.record(node.id, "timer", {"wake_at": wake_at})
        return None

    # -- branch selection ----------------------------------------------------

    @staticmethod
    def _pick_branch(
        flow: FlowDefinition, node: FlowNode, result: Any
    ) -> Optional[FlowEdge]:
        """Pick a gateway's out-edge from a rules evaluation.

        Match priority (first hit wins): a triggered rule's action *reason*, then
        its *name*, then the overall *outcome* (PASS/FLAG/REJECT), then ``else``.
        ``node.ref``, if set, restricts the triggered rules considered to that
        single rule name.
        """
        by_when: Dict[Optional[str], FlowEdge] = {e.when: e for e in flow.out_edges(node.id)}
        triggered = list(getattr(result, "triggered", []) or [])
        if node.ref:
            triggered = [m for m in triggered if m.rule == node.ref]

        for m in triggered:
            if m.reason and m.reason in by_when:
                return by_when[m.reason]
        for m in triggered:
            if m.rule in by_when:
                return by_when[m.rule]
        if result is not None and result.outcome in by_when:
            return by_when[result.outcome]
        return by_when.get("else")

    # -- replay --------------------------------------------------------------

    async def replay(self, workspace: str, run: Run) -> ReplayResult:
        """Re-walk ``run.history`` against the pinned flow version, following the
        recorded gateway branches, and confirm it reproduces the run.

        Replay is deterministic and side-effect-free: it never calls the rules,
        action, or lifecycle services — it trusts the recorded branch at each
        gateway. A divergence (missing node, unrecorded branch, different
        terminal state/status) is reported in ``mismatches``.
        """
        flow = self._flows.get(workspace, run.flow_id, run.flow_version)
        mismatches: List[str] = []
        if flow is None:
            return ReplayResult(False, "failed", None, run.cursor, [],
                                ["flow version not found"])

        entry = flow.entry()
        if entry is None:
            return ReplayResult(False, "failed", None, run.cursor, [], ["no event node"])

        # Recorded decisions, consumed in order as replay reaches each node kind:
        # a gateway follows its recorded branch; a task honours its recorded
        # outcome (a halted task stops replay exactly where the run stopped).
        branches = [h for h in run.history if h.get("kind") == "gateway"]
        tasks = [h for h in run.history if h.get("kind") == "task"]
        bi = ti = 0
        path: List[str] = []
        state: Optional[str] = None
        status = "running"
        cursor: Optional[str] = entry.id
        hops = 0

        while cursor is not None and status == "running" and hops < self._max_hops:
            hops += 1
            node = flow.node(cursor)
            if node is None:
                mismatches.append(f"cursor '{cursor}' has no node")
                status = "failed"
                break
            path.append(node.id)

            if node.kind == "gateway":
                if bi >= len(branches):
                    mismatches.append(f"gateway '{node.id}' has no recorded branch")
                    status = "failed"
                    break
                want = branches[bi].get("detail", {}).get("branch")
                bi += 1
                edge = next((e for e in flow.out_edges(node.id) if e.when == want), None)
                if edge is None:
                    mismatches.append(f"recorded branch '{want}' not in flow at '{node.id}'")
                    status = "failed"
                    break
                cursor = edge.dst
                continue

            if node.kind == "task":
                # A task that halted the run (ok=False) stops replay here too.
                detail = tasks[ti].get("detail", {}) if ti < len(tasks) else {}
                ti += 1
                if detail.get("ok") is False:
                    status = "failed"
                    break

            if node.kind == "state":
                state = node.ref

            if node.kind == "timer":
                status = "waiting"
                break

            outs = flow.out_edges(node.id)
            if not outs:
                status = "done"
                cursor = None
                break
            cursor = outs[0].dst

        if status != run.status:
            mismatches.append(f"status {status!r} != recorded {run.status!r}")
        if state != run.state:
            mismatches.append(f"state {state!r} != recorded {run.state!r}")

        return ReplayResult(
            ok=not mismatches, status=status, state=state,
            cursor=cursor or "", path=path, mismatches=mismatches,
        )

    # -- helpers -------------------------------------------------------------

    @staticmethod
    def _single_next(flow: FlowDefinition, node: FlowNode, run: Run) -> Optional[str]:
        """Follow a non-branching node's single out-edge; terminate if none."""
        outs = flow.out_edges(node.id)
        if not outs:
            run.status = "done"
            return None
        return outs[0].dst

    def _resolve(self, spec: Any, run_vars: Dict[str, Any]) -> Any:
        """Resolve a node.config binding value against the run vars.

        ``"$var"`` → the raw typed var; a string containing ``{`` → ``str.format``
        over the vars; a dict/list → resolved element-wise; anything else → the
        literal. Consistent with the P1 DecisionBinding templates.
        """
        if isinstance(spec, str):
            if spec.startswith("$"):
                return run_vars.get(spec[1:])
            if "{" in spec:
                try:
                    return spec.format(**run_vars)
                except (KeyError, IndexError, ValueError):
                    return spec
            return spec
        if isinstance(spec, dict):
            return {k: self._resolve(v, run_vars) for k, v in spec.items()}
        if isinstance(spec, list):
            return [self._resolve(v, run_vars) for v in spec]
        return spec

    async def _emit_hop(
        self, rag: Any, workspace: str, run: Run, node: FlowNode, *, detail: str
    ) -> None:
        """Write the audit quad ``run:{run_id} -[flow_step:{kind}]-> {node}``.

        Audit-only: a rules-gate reaction or a graph error here must never take
        down the run, so failures are logged, not raised.
        """
        if rag is None or not hasattr(rag, "emit_decision_trace"):
            return
        rc = RelationContext(
            decision_trace=f"flow '{run.flow_id}' step '{node.id}' ({node.kind}): {detail}",
            approved_by="flow-engine",
            approved_via="system",
            provenance=f"run:{run.run_id}",
            confidence_score=1.0,
        )
        try:
            await rag.emit_decision_trace(
                f"run:{run.run_id}", node.id, f"flow_step:{node.kind}", rc
            )
        except Exception as e:  # audit is best-effort; never block the walk
            logger.warning(f"flow hop quad failed for {run.run_id}/{node.id}: {e}")

    @staticmethod
    def _vars_key(vars: Dict[str, Any]) -> str:
        blob = json.dumps(vars, sort_keys=True, ensure_ascii=False, default=str)
        return "vars:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _duration_seconds(ref: Optional[str]) -> Optional[float]:
        """Parse a compact duration on a timer node's ``ref`` (``"30s"``,
        ``"5m"``, ``"2h"``, ``"1d"``). Returns None if it isn't one."""
        if not ref or not isinstance(ref, str):
            return None
        units = {"s": 1, "m": 60, "h": 3600, "d": 86400}
        unit = ref[-1]
        if unit in units and ref[:-1].replace(".", "", 1).isdigit():
            return float(ref[:-1]) * units[unit]
        return None
