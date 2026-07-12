"""Flow definition + run schemas (P0).

A :class:`FlowDefinition` is the authored BPMN-lite artifact (an L0 Concept
instance): a graph of five node kinds — event, task, gateway, timer, state — that
the executor walks. A :class:`Run` is a live instance with a **pinned**
``flow_version`` (decision 4: pin-to-start-and-finish) and an append-only
``history`` for replay. See docs/PLATFORM_ARCHITECTURE.html.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

NODE_KINDS = ("event", "task", "gateway", "timer", "state")
RUN_STATUSES = ("running", "waiting", "done", "failed")


@dataclass
class FlowNode:
    id: str
    kind: str
    ref: Optional[str] = None          # action_id | rule_id | state_name | duration
    config: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "kind": self.kind, "ref": self.ref, "config": self.config}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "FlowNode":
        return cls(
            id=d["id"],
            kind=d.get("kind", ""),
            ref=d.get("ref"),
            config=dict(d.get("config") or {}),
        )


@dataclass
class FlowEdge:
    src: str
    dst: str
    when: Optional[str] = None          # gateway branch label ("exceeds" / "else")

    def to_dict(self) -> Dict[str, Any]:
        return {"src": self.src, "dst": self.dst, "when": self.when}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "FlowEdge":
        return cls(src=d["src"], dst=d["dst"], when=d.get("when"))


@dataclass
class FlowDefinition:
    id: str
    version: int = 1
    on_event: str = ""
    nodes: List[FlowNode] = field(default_factory=list)
    edges: List[FlowEdge] = field(default_factory=list)
    test_cases: List[Dict[str, Any]] = field(default_factory=list)

    def node(self, node_id: str) -> Optional[FlowNode]:
        for n in self.nodes:
            if n.id == node_id:
                return n
        return None

    def entry(self) -> Optional[FlowNode]:
        for n in self.nodes:
            if n.kind == "event":
                return n
        return None

    def out_edges(self, node_id: str) -> List[FlowEdge]:
        return [e for e in self.edges if e.src == node_id]

    def lint(self) -> List[str]:
        problems: List[str] = []
        ids = [n.id for n in self.nodes]
        if len(ids) != len(set(ids)):
            problems.append("duplicate node ids")
        for n in self.nodes:
            if n.kind not in NODE_KINDS:
                problems.append(f"node '{n.id}': unknown kind '{n.kind}'")
        events = [n for n in self.nodes if n.kind == "event"]
        if len(events) != 1:
            problems.append(f"exactly one event node required (found {len(events)})")
        idset = set(ids)
        for e in self.edges:
            if e.src not in idset:
                problems.append(f"edge src '{e.src}' is not a node")
            if e.dst not in idset:
                problems.append(f"edge dst '{e.dst}' is not a node")
        for n in self.nodes:
            if n.kind == "gateway":
                outs = self.out_edges(n.id)
                if not outs:
                    problems.append(f"gateway '{n.id}': needs outgoing branches")
                if any(e.when is None for e in outs):
                    problems.append(
                        f"gateway '{n.id}': every outgoing edge needs a 'when' label"
                    )
        if not self.on_event:
            problems.append("on_event is required")
        return problems

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "version": self.version,
            "on_event": self.on_event,
            "nodes": [n.to_dict() for n in self.nodes],
            "edges": [e.to_dict() for e in self.edges],
            "test_cases": self.test_cases,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "FlowDefinition":
        return cls(
            id=d["id"],
            version=int(d.get("version", 1)),
            on_event=d.get("on_event", ""),
            nodes=[FlowNode.from_dict(x) for x in d.get("nodes", [])],
            edges=[FlowEdge.from_dict(x) for x in d.get("edges", [])],
            test_cases=list(d.get("test_cases", [])),
        )


@dataclass
class Run:
    run_id: str
    app_id: str
    flow_id: str
    flow_version: int
    cursor: str = ""
    status: str = "running"
    vars: Dict[str, Any] = field(default_factory=dict)
    state: Optional[str] = None
    wake_at: Optional[str] = None       # ISO-8601; when a waiting timer is due
    history: List[Dict[str, Any]] = field(default_factory=list)

    def record(self, node_id: str, kind: str, detail: Optional[Dict[str, Any]] = None) -> None:
        """Append a step to the replayable history."""
        self.history.append({"node": node_id, "kind": kind, "detail": detail or {}})

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "app_id": self.app_id,
            "flow_id": self.flow_id,
            "flow_version": self.flow_version,
            "cursor": self.cursor,
            "status": self.status,
            "vars": self.vars,
            "state": self.state,
            "wake_at": self.wake_at,
            "history": self.history,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Run":
        return cls(
            run_id=d["run_id"],
            app_id=d.get("app_id", ""),
            flow_id=d.get("flow_id", ""),
            flow_version=int(d.get("flow_version", 1)),
            cursor=d.get("cursor", ""),
            status=d.get("status", "running"),
            vars=dict(d.get("vars") or {}),
            state=d.get("state"),
            wake_at=d.get("wake_at"),
            history=list(d.get("history") or []),
        )
