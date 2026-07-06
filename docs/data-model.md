# Data model

## The contextual quadruple `(h, r, t, rc)`

Every relationship in a Context Graph is four components:

| Component | Meaning |
|-----------|---------|
| `h` | Head entity (source) |
| `r` | Relation type / keyword |
| `t` | Tail entity (target) |
| `rc` | **RelationContext** — the decision record |

A standard 5-field relation (no `rc`) is still accepted and stored with
`relation_context = null` — full backward compatibility with LightRAG.

## RelationContext

`lightrag/context_graph_types.py` — a dataclass with **11 fields**, grouped by what they answer.

| Field | Type | Meaning |
|-------|------|---------|
| `supporting_sentences` | `List[str]` | Verbatim quotes from source documents |
| `provenance` | `str \| None` | Source reference — thread ID, doc section, call timestamp |
| `confidence_score` | `float` | Extraction reliability 0.0–1.0 (default `1.0`) |
| `approved_by` | `str \| None` | Approver entity name (`"VP_Smith"`, `"Finance_Team"`) |
| `approved_via` | `str \| None` | Channel: `slack` · `zoom` · `email` · `in_person` · `jira` · `system` |
| `policy_ref` | `str \| None` | Policy name/ID followed or overridden |
| `valid_from` | `str \| None` | ISO-8601 effective date (`YYYY-MM-DD`) |
| `valid_until` | `str \| None` | ISO-8601 expiry date (`YYYY-MM-DD`) |
| `temporal_info` | `str \| None` | Free-form validity (`"Q4 2026"`, `"since 2020"`) |
| `decision_trace` | `str \| None` | The **why** — rationale / exception / override / approval |
| `quantitative_data` | `str \| None` | Numbers — discount %, budget, counts |

### Example

```
Standard KG:    Sarah Chen --APPROVES--> MegaCorp

Context Graph:  Sarah Chen --APPROVES--> MegaCorp
   rc = {
     decision_trace:    "Approved 20% discount citing 5-year relationship and competitive pressure",
     approved_by:       "Sarah Chen",
     approved_via:      "in_person",
     valid_from:        "2024-08-14",
     valid_until:       "2024-12-31",
     policy_ref:        "DiscountPolicy_Standard",
     quantitative_data: "20% discount",
     provenance:        "Slack #deals-review, Aug 14 2024",
     confidence_score:  0.97
   }
```

### Methods

| Method | Purpose |
|--------|---------|
| `to_dict()` / `from_dict(d)` | Serialize / deserialize (all 11 fields) |
| `to_json()` / `from_json(s)` | JSON form stored on the edge (`from_json` returns an empty rc on bad input) |
| `to_text()` | Human-readable rendering for prompts |
| `is_empty()` | True when no meaningful content is present |
| `is_active(as_of=None)` | True if currently valid per `valid_from`/`valid_until` |
| `merge(others)` | Combine rc from multiple chunks for the same edge |

> Note: `is_empty()` now inspects **all** content fields (fixed in commit 058a26e3).
> `to_text()` still renders only the 5 primary narrative fields for prompt brevity — this is
> intentional, not a bug. All 11 fields remain content and are preserved through merge/rebuild.

## ContextNode / ContextEdge

`ContextNode` and `ContextEdge` wrap an entity and an rc-bearing edge for the typed CG APIs.
`ContextEdge` carries the `RelationContext` (`context` field, defaulting via
`field(default_factory=RelationContext)`). On the graph, the context is persisted as a JSON string
on the edge under `relation_context` and re-parsed with `RelationContext.from_json` on read.

## On-the-wire extraction format (6-field relation)

```
# standard (5 fields)
relation<|#|>source<|#|>target<|#|>keywords<|#|>description
# Context Graph (6th field = compact RelationContext JSON)
relation<|#|>source<|#|>target<|#|>keywords<|#|>description<|#|>{"decision_trace":"…","approved_by":"…","confidence_score":0.95}
```

Keys may be omitted; the parser fills missing keys with defaults.
