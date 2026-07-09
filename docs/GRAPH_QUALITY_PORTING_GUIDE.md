# Porting Context Graph's Graph‑Quality features into `knowledge_rag`

A step‑by‑step guide for lifting **entity deduplication**, **garbage‑node filtering**,
**graph connectivity repair**, and the **ontology** layer (their shared dependency) out of the
[Context Graph](https://github.com/dsivov/Context_Graph) fork and into the `knowledge_rag`
service stack — **without taking the rest of Context Graph**.

> **Audience:** the `knowledge_rag` team (PolarTie/pt‑mcp‑servers). You run a mostly‑vanilla
> LightRAG fork, all‑Postgres storage, a `make_lightrag` factory, FastAPI services, and a
> NiceGUI operator UI. This guide is tailored to that layout — file paths and seams are yours.
>
> **Source of truth for the code you'll copy:** the `context_graph/` package in
> `dsivov/Context_Graph` (branch `main`). Line references below (`core.py:NNN`) point there.

---

## 0. TL;DR

You are taking four self‑contained Python packages plus a thin "glue" class. The good news from
auditing both codebases:

* **None of the four packages import the `ContextGraph` core class.** They reach the graph, the
  vector DB, and the LLM **only through injected callables/arguments** — so they attach to a
  plain `LightRAG` instance with no engine changes.
* **The only cross‑package coupling** is that garbage filtering (`quality/filter.py`) and the
  ontology validator share the `ontology` package, and `ontology` reaches sideways into two
  pure‑regex helpers in `rules/projection.py`. That single edge is severed by copying ~45 lines
  (Step 2). After that the bundle is closed.
* **Your Postgres backend already implements every graph method these features call**
  (`get_all_labels`, `get_all_edges`, `get_node`, `delete_node`, `index_done_callback`), and
  LightRAG exposes `amerge_entities` / `acreate_entity` / `acreate_relation`. The features are
  **backend‑agnostic over Postgres** — verified against your `PGGraphStorage`.
* **The stores are trivially portable to Postgres.** Each store is a template‑method base class
  whose only abstract surface is `_read_raw` / `_write_raw` (a JSONB blob per workspace). One
  ~20‑line mixin + one migration and they live in PG like the rest of your state.

**Effort shape:** copy 5 packages → 1 tiny edit to sever the rules edge → 1 subclass (`QualityLightRAG`) →
1 store mixin + 1 migration → config + endpoints in `ingestion_server` → a NiceGUI page. No
changes to your LightRAG fork itself.

---

## 1. Scope — what to take, what to leave

### Take

| Package (in `context_graph/`) | What it does | Files |
|---|---|---|
| `dedup/` | Embedding‑blocked entity resolver + canonical‑name scorer + async LLM sweep for the gray band | `canonical.py`, `store.py`, `resolver.py`, `sweep.py` |
| `quality/` | Deterministic garbage‑name gate + ontology‑aware node filter + quarantine | `gate.py`, `filter.py`, `store.py` |
| `connectivity/` | LLM‑verified isolate rescue (proposes + confirms edges) | `rescue.py` |
| `ontology/` | Typed schema, coercing extraction validator, (optional) NL→schema author | `schema.py`, `store.py`, `validate.py`, `service.py`, `agent.py` |
| `jsonio.py` | Dependency‑free LLM‑JSON parser used by the above | `jsonio.py` |
| `community/` *(optional)* | Louvain community detection + LLM summaries + thematic "global" query | `detect.py`, `summarize.py`, `store.py` |

### Leave (the rest of Context Graph — not needed)

RelationContext quadruples & CGR3 reasoning, the decision‑trace / precedent subsystem, the
Business Rules Engine gate, RBAC, lifecycle state machines, the action layer, and web ingestion.
None of the four target packages depend on any of these.

> **`community/` is optional and independent.** `connectivity` does **not** depend on it. Skip it
> for a first cut; add later for the thematic global‑query feature.

---

## 2. How it maps onto *your* architecture

| Context Graph (monolith) | `knowledge_rag` (yours) | Where |
|---|---|---|
| `ContextGraph(LightRAG)` subclass carries the orchestration methods | A new `QualityLightRAG(LightRAG)` subclass, injected via the factory | `pt-knowledge-rag-core/knowledge_rag/lightrag_init.py:106` |
| Features auto‑run inside the monolith's `ainsert` | Same subclass override runs inside your `worker.py` `ainsert` | `ingestion_server/worker.py:225` |
| JSON side‑stores under `working_dir` | **Postgres** tables (JSONB blob per workspace) | new `migrations/007-*.sql` + `ingestion_server/db.py` |
| Routes in `context_graph/api/routes.py` | `@app.*` handlers in your FastAPI app | `ingestion_server/main.py` |
| React "Graph Quality" panel | A NiceGUI page + `api_client` methods | `debug_webui/pages/graph_quality.py`, `app.py`, `api_client.py` |
| Per‑request workspace header | **One fixed workspace** (`kdefault_v1`), resolved server‑side | endpoints read your `_workspace` global — no param |
| `DEDUP_*` / `GARBAGE_*` env | Frozen `Config` dataclass + compose env + `app_settings` live override | `ingestion_server/config.py` |

Two facts that make this clean and that you should keep in mind throughout:

1. **Your LightRAG fork is effectively vanilla** (`__all__ = ["LightRAG", "QueryParam"]`; the only
   "dedup" in it is upstream's query‑result merge). So there is no extraction‑format mismatch —
   you keep standard 5‑field extraction; these features operate on the resulting graph.
2. **You are single‑tenant** (`kdefault_v1`). Everywhere Context Graph threads a `workspace`
   argument, you pass your one resolved `_workspace`. Do **not** add workspace path/header/query
   params — mirror `delete_document_cascade(_pool, uid, _workspace)`.

---

## 3. Dependency tiers (the copy manifest)

Copy these into a new package inside your shared vendor core so **every service** (ingestion, mcp)
can import them. Suggested home: `pt-knowledge-rag-core/knowledge_rag/graph_quality/`.

```
knowledge_rag/graph_quality/            ← new package (in pt-knowledge-rag-core)
├── jsonio.py                 ← copy context_graph/jsonio.py            (stdlib only)
├── dedup/                    ← copy context_graph/dedup/*  (4 files)
├── quality/                  ← copy context_graph/quality/*  (3 files)
├── connectivity/             ← copy context_graph/connectivity/rescue.py
├── ontology/                 ← copy context_graph/ontology/*  (5 files)
├── community/                ← (optional) copy context_graph/community/*  (3 files)
├── projection_min.py         ← NEW: the 2 regex parsers (Step 2)
├── stores_pg.py              ← NEW: Postgres store backends (Step 3)
└── quality_lightrag.py       ← NEW: the QualityLightRAG subclass (Step 4)
```

**Import rewrites after copying** (mechanical, two kinds):

1. `from lightrag.utils import logger` → keep as‑is (your LightRAG fork has it) **or** swap for
   `logging.getLogger("graph_quality")`. Appears in: `dedup/store.py`, `dedup/sweep.py`,
   `quality/store.py`, `ontology/store.py`, `ontology/agent.py`, `connectivity/rescue.py`,
   `community/store.py`, `community/summarize.py`.
2. `from context_graph.jsonio import _extract_json_object` →
   `from knowledge_rag.graph_quality.jsonio import _extract_json_object`. Appears in:
   `dedup/sweep.py`, `connectivity/rescue.py`, `ontology/agent.py`, `community/summarize.py`.
3. `from context_graph.X import ...` (intra‑package, e.g. `context_graph.dedup.canonical`) →
   `from knowledge_rag.graph_quality.X import ...`. A single find‑replace of
   `context_graph.` → `knowledge_rag.graph_quality.` across the copied tree handles 1‑for‑1.
4. `quality/filter.py` imports `from lightrag.constants import DEFAULT_ENTITY_TYPES` — keep
   (your fork has it) or inline the list.

That's the whole rewrite. There is **no** `import context_graph.core` anywhere to fix.

---

## 4. Step 1 — Vendor the packages

```bash
# from the pt-mcp-servers checkout, with Context_Graph cloned alongside
DST=knowledge_rag/vendor/pt-knowledge-rag-core/knowledge_rag/graph_quality
mkdir -p "$DST"
cp    ../Context_Graph/context_graph/jsonio.py                 "$DST/"
cp -r ../Context_Graph/context_graph/dedup                     "$DST/"
cp -r ../Context_Graph/context_graph/quality                   "$DST/"
cp -r ../Context_Graph/context_graph/connectivity              "$DST/"
cp -r ../Context_Graph/context_graph/ontology                  "$DST/"
cp -r ../Context_Graph/context_graph/community                 "$DST/"   # optional

# rewrite intra-fork imports in one pass
grep -rl 'context_graph\.' "$DST" | xargs sed -i 's/context_graph\./knowledge_rag.graph_quality./g'
touch "$DST/__init__.py"
```

Then delete `community/` if you're deferring it, and drop the ontology `agent.py`/`service.py`
only if you don't want NL‑authored schemas (they're optional; keep `schema.py`, `store.py`,
`validate.py` — those are what garbage filtering needs).

---

## 5. Step 2 — Sever the ontology → rules edge (one small file)

`ontology/schema.py` imports two coercion helpers from the rules package:

```python
# ontology/schema.py (as shipped)
from context_graph.rules.projection import parse_amount, parse_percent
```

`context_graph.rules.projection` in turn imports `RelationContext` and, because importing the
submodule executes `rules/__init__.py`, drags in the **entire** rules engine. But
`parse_amount` / `parse_percent` are **pure regex functions with zero dependencies**. Copy just
those two functions plus their module constants (`_PERCENT_RE`, `_AMOUNT_RE`, `_MAGNITUDE`) from
`context_graph/rules/projection.py` into a new leaf module:

```python
# knowledge_rag/graph_quality/projection_min.py
# Lifted verbatim from context_graph/rules/projection.py (the two parsers + their
# regexes). Pure stdlib — no RelationContext, no rules engine.
import re
from typing import Optional

_PERCENT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:%|percent\b)", re.IGNORECASE)

# A money-ish number, optionally with a currency symbol, thousands separators,
# a decimal, and a magnitude suffix (k/m/b).
_AMOUNT_RE = re.compile(
    r"""
    (?P<ccy>[$€£¥])?\s*
    (?P<num>
        \d{1,3}(?:,\d{3})+(?:\.\d+)?   # 25,000  or  1,234.56
      | \d+\.\d+                        # 1234.56
      | \d+                             # 1234
    )
    \s*(?P<suf>[kKmMbB])?
    """,
    re.VERBOSE,
)

_MAGNITUDE = {"k": 1_000.0, "m": 1_000_000.0, "b": 1_000_000_000.0}


def parse_percent(text: Optional[str]) -> Optional[float]:
    """'20% discount' → 0.20; '15 percent' → 0.15; '$25,000' / '0.2' → None."""
    if not text:
        return None
    m = _PERCENT_RE.search(text)
    if not m:
        return None
    try:
        return float(m.group(1)) / 100.0
    except ValueError:
        return None


def parse_amount(text: Optional[str]) -> Optional[float]:
    """'$25,000' → 25000.0; '€8k' → 8000.0; '1.2M' → 1200000.0; '20% discount' → None.
    Only money-like numbers (symbol / separators / decimal / suffix) count, so a bare
    small integer ('valid for 5 days') is not read as an amount."""
    if not text:
        return None
    cleaned = _PERCENT_RE.sub(" ", text)          # strip % so its digits aren't read as amount
    for m in _AMOUNT_RE.finditer(cleaned):
        num, ccy, suf = m.group("num"), m.group("ccy"), m.group("suf")
        money_like = bool(ccy) or ("," in num) or ("." in num) or bool(suf)
        if not money_like:
            continue
        try:
            value = float(num.replace(",", ""))
        except ValueError:
            continue
        if suf:
            value *= _MAGNITUDE[suf.lower()]
        return value
    return None
```

Then change the one import in the copied `ontology/schema.py`:

```python
from knowledge_rag.graph_quality.projection_min import parse_amount, parse_percent
```

Now `import knowledge_rag.graph_quality.ontology` pulls in **nothing** outside the new package.
(If you'd rather not, the alternative is to also copy `context_graph/types.py` — it's stdlib‑only —
and the `rules/projection.py` file wholesale. The 45‑line inline is cleaner.)

---

## 6. Step 3 — Postgres‑backed stores (the template‑method trick)

Each store (`DedupStore`, `QuarantineStore`, `OntologyStore`, `CommunityStore`) is an abstract
base class that implements **all** its logic on top of a tiny raw‑blob interface:

```python
@abstractmethod
def _read_raw(self, workspace: str) -> Optional[Dict[str, Any]]: ...
@abstractmethod
def _write_raw(self, workspace: str, data: Dict[str, Any]) -> None: ...
# dedup + ontology additionally: _delete_raw; ontology also: _list_workspaces
```

So a Postgres backend is just those primitives against **one JSONB table**. All the interesting
logic (`record_merge`, `enqueue_review`, `summary`, `partition`, …) is inherited unchanged.

**Migration** — add `migrations/007-graph-quality.sql` (follows your `CREATE TABLE IF NOT EXISTS`
+ `*_kdefault_v1` convention; here one generic table keyed by store kind):

```sql
-- migrations/007-graph-quality.sql
CREATE TABLE IF NOT EXISTS graph_quality_store (
    kind        TEXT        NOT NULL,          -- 'dedup' | 'quarantine' | 'ontology' | 'community'
    workspace   TEXT        NOT NULL,          -- 'kdefault_v1'
    data        JSONB       NOT NULL DEFAULT '{}'::jsonb,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (kind, workspace)
);
```

**The mixin + four backends** — `knowledge_rag/graph_quality/stores_pg.py`:

```python
# knowledge_rag/graph_quality/stores_pg.py
import json
from typing import Any, Dict, List, Optional

from psycopg_pool import ConnectionPool          # add psycopg[binary,pool] to requirements
from psycopg.types.json import Jsonb

from .dedup.store import DedupStore
from .quality.store import QuarantineStore
from .ontology.store import OntologyStore
from .community.store import CommunityStore       # optional


class _PgBlob:
    """Backs the stores' raw-blob primitives with one JSONB row per (kind, workspace)
    in graph_quality_store. Synchronous (psycopg) — the base stores are sync, so call
    their public methods from async endpoints via
    ``await asyncio.to_thread(store.method, ...)``. All four store types share this
    mixin; quarantine/community simply never call _delete_raw / _list_workspaces."""

    KIND: str = ""

    def __init__(self, pool: ConnectionPool, **kw):
        super().__init__(**kw)                    # DedupStore/… accept now=... via **kw
        self._pool = pool

    def _read_raw(self, workspace: str) -> Optional[Dict[str, Any]]:
        with self._pool.connection() as con:
            row = con.execute(
                "SELECT data FROM graph_quality_store WHERE kind=%s AND workspace=%s",
                (self.KIND, workspace)).fetchone()
        return row[0] if row else None            # psycopg3 loads JSONB → dict

    def _write_raw(self, workspace: str, data: Dict[str, Any]) -> None:
        with self._pool.connection() as con:
            con.execute(
                """INSERT INTO graph_quality_store (kind, workspace, data, updated_at)
                   VALUES (%s, %s, %s, now())
                   ON CONFLICT (kind, workspace)
                   DO UPDATE SET data = EXCLUDED.data, updated_at = now()""",
                (self.KIND, workspace, Jsonb(data)))

    def _delete_raw(self, workspace: str) -> bool:            # DedupStore + OntologyStore
        with self._pool.connection() as con:
            cur = con.execute(
                "DELETE FROM graph_quality_store WHERE kind=%s AND workspace=%s",
                (self.KIND, workspace))
            return cur.rowcount > 0

    def _list_workspaces(self) -> List[str]:                  # OntologyStore only
        with self._pool.connection() as con:
            return [r[0] for r in con.execute(
                "SELECT workspace FROM graph_quality_store WHERE kind=%s",
                (self.KIND,)).fetchall()]


class PgDedupStore(_PgBlob, DedupStore):            KIND = "dedup"
class PgQuarantineStore(_PgBlob, QuarantineStore):  KIND = "quarantine"
class PgOntologyStore(_PgBlob, OntologyStore):      KIND = "ontology"
class PgCommunityStore(_PgBlob, CommunityStore):    KIND = "community"      # optional
```

> **Why sync psycopg (not your asyncpg pool)?** The base stores are synchronous (they were
> file‑backed) and their logic is inherited untouched — you only implement blob I/O. A sync call
> can't use your async `_pool`, so the mixin uses a small `psycopg` `ConnectionPool` and you invoke
> store methods from async endpoints via `await asyncio.to_thread(store.record_merge, ...)`. These
> ops are infrequent (operator‑triggered maintenance), so the thread hop is free. Build the pool
> once at startup: `ConnectionPool(conninfo=config.pg_dsn, min_size=1, max_size=4)`.
>
> *(Alternative, no new driver: make the ~8 public methods you actually call —`record_merge`,
> `enqueue_review`, `add`, `list`, `pop`, `summary`, `save`, `load`— async and back them with your
> existing `asyncpg` pool. More edits to the copied stores; skip unless you want to avoid psycopg.)*

`app_settings` alternative for ontology: if you'd rather not add a table for the ontology, its
blob is config‑shaped and can live in your existing `app_settings` JSONB KV under
`ontology.kdefault_v1`. The `PgOntologyStore` primitives just read/write that key.

---

## 7. Step 4 — The glue: `QualityLightRAG(LightRAG)`

Context Graph's orchestration methods live on the `ContextGraph` class. Re‑home the four features'
methods on a small subclass. **They use only public LightRAG API + the injected stores.** Below is
the **complete** file — every method body lifted from `context_graph/core.py`, with just two
adaptations from the original: (a) store `@property` blocks return the **attached PG stores**
instead of constructing JSON stores, and (b) `_node_filter` loads the ontology from the attached
PG store. Deferred `from context_graph.…` imports become `from knowledge_rag.graph_quality.…`.

```python
# knowledge_rag/graph_quality/quality_lightrag.py
"""Vanilla LightRAG + graph-quality ops (dedup, garbage, connectivity, community)
+ an on-ingest garbage filter. Method bodies are lifted from context_graph/core.py
(dsivov/Context_Graph) and adapted so the side-stores are Postgres-backed and
attached after construction. No changes to the LightRAG base class."""
from __future__ import annotations

import os
from typing import Any

from lightrag import LightRAG
from lightrag.utils import logger, compute_mdhash_id
from lightrag.constants import GRAPH_FIELD_SEP


class QualityLightRAG(LightRAG):

    def __post_init__(self) -> None:
        super().__post_init__()
        # Guards so the on-demand methods don't AttributeError if attach_quality()
        # was never called (e.g. the query-only mcp path constructs plain LightRAG).
        self._dedup_store = None
        self._quarantine_store = None
        self._ontology_store = None
        self._community_store = None
        self._node_filter_cache = None

    # ---- store wiring: attached by the service that owns the asyncpg pool ----
    def attach_quality(self, *, dedup_store, quarantine_store,
                       ontology_store=None, community_store=None) -> None:
        self._dedup_store = dedup_store
        self._quarantine_store = quarantine_store
        self._ontology_store = ontology_store
        self._community_store = community_store
        self._node_filter_cache = None

    @property
    def dedup_store(self):
        return self._dedup_store

    @property
    def quarantine_store(self):
        return self._quarantine_store

    @property
    def community_store(self):
        return self._community_store

    # ---- env-driven config ----
    def _garbage_filter_enabled(self) -> bool:
        return os.getenv("GARBAGE_FILTER_ENABLED", "true").strip().lower() not in (
            "false", "0", "no", "off", "")

    def _dedup_thresholds(self) -> tuple:
        from knowledge_rag.graph_quality.dedup import DEFAULT_HARD, DEFAULT_GRAY
        hard = float(os.getenv("DEDUP_HARD", DEFAULT_HARD))
        gray = float(os.getenv("DEDUP_GRAY", DEFAULT_GRAY))
        return hard, gray

    @property
    def dedup_enabled(self) -> bool:
        return os.getenv("DEDUP_ENABLED", "true").strip().lower() not in (
            "false", "0", "no", "off", "")

    def _dedup_sweep_batch(self) -> int:
        try:
            return max(1, int(os.getenv("DEDUP_SWEEP_BATCH", "10")))
        except ValueError:
            return 10

    # ================= GARBAGE FILTERING =================

    def _node_filter(self):
        if self._node_filter_cache is not None:
            return self._node_filter_cache
        from knowledge_rag.graph_quality.quality import NodeFilter
        onto = None
        if self._ontology_store is not None:                 # ← was JsonOntologyStore(working_dir)
            try:
                onto = self._ontology_store.load(self.workspace)
            except Exception:
                onto = None
        closed = os.getenv("GARBAGE_CLOSED_WORLD", "false").strip().lower() in (
            "true", "1", "yes", "on")
        self._node_filter_cache = NodeFilter(onto, closed_world=closed)
        return self._node_filter_cache

    async def _process_extract_entities(self, chunk, pipeline_status=None,
                                        pipeline_status_lock=None) -> list:
        """Override: keep vanilla extraction, then quarantine low-quality/off-schema
        nodes before they reach the merge. (Context Graph swaps in its contextual
        extractor here; you keep the base extractor and only add the filter.)"""
        results = await super()._process_extract_entities(
            chunk, pipeline_status, pipeline_status_lock)
        return self._filter_extracted(results)

    def _filter_extracted(self, chunk_results: list) -> list:
        if not self._garbage_filter_enabled():
            return chunk_results
        nf = self._node_filter()
        rejected: list[dict] = []
        for maybe_nodes, maybe_edges in chunk_results:
            for name in list(maybe_nodes.keys()):
                recs = maybe_nodes.get(name) or []
                rep = recs[0] if recs else {}
                reason = nf.check(name, rep.get("description", ""), rep.get("entity_type", ""))
                if not reason:
                    continue
                rejected.append({
                    "entity_name": name,
                    "entity_type": rep.get("entity_type", ""),
                    "description": (rep.get("description") or "")[:300],
                    "reason": reason,
                })
                del maybe_nodes[name]
                for ek in list(maybe_edges.keys()):
                    if isinstance(ek, (tuple, list)) and name in (ek[0], ek[1]):
                        del maybe_edges[ek]
        if rejected:
            try:
                self.quarantine_store.add(self.workspace, rejected)
            except Exception as e:  # never break ingest
                logger.warning(f"quarantine store add failed: {e}")
            logger.info(f"garbage filter: quarantined {len(rejected)} node(s)")
        return chunk_results

    async def _remove_entity(self, name: str) -> None:
        await self.chunk_entity_relation_graph.delete_node(name)
        try:
            await self.entities_vdb.delete([compute_mdhash_id(name, prefix="ent-")])
        except Exception:  # best-effort vdb cleanup
            pass

    async def scan_garbage(self, *, apply: bool = True, limit: int = 100000) -> dict:
        if not self._garbage_filter_enabled():
            return {"disabled": True}
        # Retroactive DELETION uses only the deterministic gate (name-based garbage +
        # empty description) — NOT the ontology validator: a node merely missing a
        # required property is real-but-incomplete; deleting it would lose signal.
        from knowledge_rag.graph_quality.quality import quality_check
        graph = self.chunk_entity_relation_graph
        labels = list(await graph.get_all_labels() or [])[:limit]
        summary = {"scanned": 0, "quarantined": 0, "removed": 0, "by_reason": {}, "sample": []}
        rejected: list[dict] = []
        for name in labels:
            summary["scanned"] += 1
            node = await graph.get_node(name) or {}
            verdict = quality_check(name, node.get("description", ""), node.get("entity_type", ""))
            reason = None if verdict.ok else verdict.reason
            if not reason:
                continue
            summary["quarantined"] += 1
            summary["by_reason"][reason] = summary["by_reason"].get(reason, 0) + 1
            if len(summary["sample"]) < 15:
                summary["sample"].append({"name": name, "reason": reason})
            rejected.append({
                "entity_name": name, "entity_type": node.get("entity_type", ""),
                "description": (node.get("description") or "")[:300], "reason": reason,
            })
            if apply:
                try:
                    await self._remove_entity(name)
                    summary["removed"] += 1
                except Exception as e:
                    logger.warning(f"garbage remove {name} failed: {e}")
        if apply and rejected:                               # preview must not mutate
            try:
                self.quarantine_store.add(self.workspace, rejected)
            except Exception as e:
                logger.warning(f"quarantine add failed: {e}")
        if apply and summary["removed"]:
            try:
                await self.chunk_entity_relation_graph.index_done_callback()
                await self.entities_vdb.index_done_callback()
            except Exception as e:
                logger.warning(f"garbage scan persist failed: {e}")
        logger.info(f"scan_garbage: {summary}")
        return summary

    # ================= CONNECTIVITY =================

    async def _isolated_nodes(self, limit: int) -> list[dict]:
        graph = self.chunk_entity_relation_graph
        labels = list(await graph.get_all_labels() or [])
        degree = {n: 0 for n in labels}
        for e in await graph.get_all_edges() or []:
            a, b = e.get("source"), e.get("target")
            if a in degree:
                degree[a] += 1
            if b in degree:
                degree[b] += 1
        out = []
        for name in labels:
            if degree.get(name, 0) != 0:
                continue
            node = await graph.get_node(name) or {}
            out.append({"name": name,
                        "description": (node.get("description") or "").split(GRAPH_FIELD_SEP)[0].strip()})
            if len(out) >= limit:
                break
        return out

    async def connectivity_report(self, *, sample_isolates: int = 20) -> dict:
        graph = self.chunk_entity_relation_graph
        labels = list(await graph.get_all_labels() or [])
        edges = list(await graph.get_all_edges() or [])
        n = len(labels)
        if n == 0:
            return {"total_nodes": 0, "total_edges": 0, "isolated_nodes": 0,
                    "isolated_pct": 0.0, "connected_components": 0,
                    "largest_component_size": 0, "largest_component_pct": 0.0,
                    "degree": {"mean": 0.0, "median": 0.0, "max": 0, "degree0": 0, "degree1": 0},
                    "isolate_sample": []}
        parent = {name: name for name in labels}             # union-find

        def find(x):
            root = x
            while parent[root] != root:
                root = parent[root]
            while parent[x] != root:                         # path compression
                parent[x], x = root, parent[x]
            return root

        degree = {name: 0 for name in labels}
        edge_count = 0
        for e in edges:
            a, b = e.get("source"), e.get("target")
            if a not in parent or b not in parent or a == b:
                continue
            edge_count += 1
            degree[a] += 1
            degree[b] += 1
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb
        comp_size: dict = {}
        for name in labels:
            r = find(name)
            comp_size[r] = comp_size.get(r, 0) + 1
        num_components = len(comp_size)
        largest = max(comp_size.values()) if comp_size else 0
        degs = sorted(degree.values())
        deg0 = sum(1 for d in degs if d == 0)
        deg1 = sum(1 for d in degs if d == 1)
        mean_deg = sum(degs) / n
        median_deg = float(degs[n // 2] if n % 2 else (degs[n // 2 - 1] + degs[n // 2]) / 2)
        isolate_sample = [name for name in labels if degree[name] == 0][:sample_isolates]
        return {"total_nodes": n, "total_edges": edge_count, "isolated_nodes": deg0,
                "isolated_pct": round(100.0 * deg0 / n, 2),
                "connected_components": num_components,
                "largest_component_size": largest,
                "largest_component_pct": round(100.0 * largest / n, 2),
                "degree": {"mean": round(mean_deg, 3), "median": median_deg,
                           "max": degs[-1], "degree0": deg0, "degree1": deg1},
                "isolate_sample": isolate_sample}

    async def rescue_isolates(self, *, apply: bool = True, limit: int = 50,
                              max_candidates: int = 8) -> dict:
        from knowledge_rag.graph_quality.connectivity import IsolateRescue
        isolates = await self._isolated_nodes(limit)
        if not apply:
            return {"isolates": len(isolates), "preview": True,
                    "sample": [i["name"] for i in isolates[:15]]}
        graph = self.chunk_entity_relation_graph

        async def find_candidates(name: str, desc: str):
            q = f"{name}\n{desc}" if desc else name
            try:
                hits = await self.entities_vdb.query(q, top_k=max_candidates + 4) or []
            except Exception:
                hits = []
            cands = []
            for h in hits:
                cn = h.get("entity_name") or h.get("id")
                if not cn or cn == name:
                    continue
                cnode = await graph.get_node(cn) or {}
                cands.append({"name": cn,
                              "description": (cnode.get("description") or "").split(GRAPH_FIELD_SEP)[0].strip()})
            return cands

        async def add_edge(src: str, tgt: str, keywords: str, description: str):
            await self.acreate_relation(src, tgt, {
                "keywords": keywords, "weight": 1.0,
                "description": description or keywords, "source_id": "isolate_rescue"})

        rescue = IsolateRescue(self.llm_model_func, find_candidates=find_candidates,
                               add_edge=add_edge, max_candidates=max_candidates)
        result = await rescue.rescue(isolates)
        result["isolates_scanned"] = len(isolates)
        try:
            await graph.index_done_callback()
            await self.relationships_vdb.index_done_callback()
        except Exception as e:
            logger.warning(f"isolate rescue persist failed: {e}")
        return result

    async def prune_isolates(self, *, apply: bool = False, limit: int = 100000) -> dict:
        graph = self.chunk_entity_relation_graph
        isolates = await self._isolated_nodes(limit)
        summary = {"isolates": len(isolates), "removed": 0, "preview": not apply,
                   "sample": [i["name"] for i in isolates[:20]]}
        if not apply:
            return summary
        rejected: list[dict] = []
        for iso in isolates:
            name = iso["name"]
            node = await graph.get_node(name) or {}
            rejected.append({"entity_name": name, "entity_type": node.get("entity_type", ""),
                             "description": (node.get("description") or "")[:300],
                             "reason": "low-degree isolate (pruned)"})
            try:
                await self._remove_entity(name)
                summary["removed"] += 1
            except Exception as e:
                logger.warning(f"prune isolate {name} failed: {e}")
        if rejected:
            try:
                self.quarantine_store.add(self.workspace, rejected)
            except Exception as e:
                logger.warning(f"quarantine add failed: {e}")
        if summary["removed"]:
            try:
                await graph.index_done_callback()
                await self.entities_vdb.index_done_callback()
            except Exception as e:
                logger.warning(f"prune persist failed: {e}")
        logger.info(f"prune_isolates: {summary}")
        return summary

    # ================= DEDUP =================

    async def _apply_entity_merge(self, alias: str, into: str, canonical: str) -> None:
        if alias == into:
            return
        await self.amerge_entities(
            [alias], into,
            merge_strategy={"description": "concatenate", "entity_type": "keep_first"})
        if canonical:
            self.dedup_store.set_canonical_name(self.workspace, into, canonical)

    async def deduplicate_entities(self, *, apply: bool = True, limit: int = 5000) -> dict:
        from knowledge_rag.graph_quality.dedup import (
            canonicalize, prefer_canonical_name, type_ok, name_ok)
        hard, gray = self._dedup_thresholds()
        graph = self.chunk_entity_relation_graph
        store = self.dedup_store
        labels = list(await graph.get_all_labels() or [])[:limit]
        merged_away: set[str] = set()
        summary = {"scanned": 0, "merged": 0, "queued": 0, "skipped": 0}

        async def node_type(name: str):
            node = await graph.get_node(name)
            return (node or {}).get("entity_type")

        async def mention_count(name: str, node: dict = None) -> int:
            node = node if node is not None else (await graph.get_node(name) or {})
            sid = node.get("source_id") or ""
            return len([c for c in sid.split(GRAPH_FIELD_SEP) if c]) or 1

        for name in labels:
            summary["scanned"] += 1
            if name in merged_away:
                summary["skipped"] += 1
                continue
            my_node = await graph.get_node(name) or {}
            my_type = my_node.get("entity_type")
            # Query with the SAME representation entities_vdb stored (name + first
            # description line), not the bare name — else short names dilute and miss.
            desc = (my_node.get("description") or "").split(GRAPH_FIELD_SEP)[0].strip()
            query_text = f"{name}\n{desc}" if desc else name
            try:
                hits = await self.entities_vdb.query(query_text, top_k=5) or []
            except Exception:
                hits = []
            top = next((h for h in hits
                        if (h.get("entity_name") or h.get("id")) not in (None, "", name)
                        and (h.get("entity_name") or h.get("id")) not in merged_away), None)
            if top is None:
                continue
            cand = top.get("entity_name") or top.get("id")
            score = float(top.get("distance") or 0.0)
            ctype = top.get("entity_type")
            if ctype is None:
                ctype = await node_type(cand)
            if score >= hard and type_ok(my_type, ctype) and name_ok(name, cand):
                if apply:                                    # apply=False is pure preview
                    counts = {name: await mention_count(name, my_node),
                              cand: await mention_count(cand)}
                    canonical = prefer_canonical_name([name, cand], counts=counts)
                    # Representative form SURVIVES; the other folds in.
                    survivor, alias = (name, cand) if canonical.strip() == name.strip() else (cand, name)
                    try:
                        await self._apply_entity_merge(alias, survivor, canonical)
                    except Exception as e:
                        logger.warning(f"dedup merge {alias}->{survivor} failed: {e}")
                        continue
                    store.record_merge(self.workspace, alias=alias, alias_key=canonicalize(alias),
                                       into=survivor, method="embedding", score=score,
                                       canonical_name=canonical)
                    merged_away.add(alias)
                summary["merged"] += 1
            elif score >= gray and type_ok(my_type, ctype):
                if apply:
                    store.enqueue_review(self.workspace, name=name, candidate=cand, score=score)
                summary["queued"] += 1
        logger.info(f"deduplicate_entities: {summary}")
        return summary

    async def run_dedup_sweep(self) -> dict:
        from knowledge_rag.graph_quality.dedup import DedupSweep
        graph = self.chunk_entity_relation_graph

        async def apply(alias: str, into: str, canonical: str):
            await self._apply_entity_merge(alias, into, canonical)

        async def get_count(name: str) -> int:
            node = await graph.get_node(name) or {}
            sid = node.get("source_id") or ""
            return len([c for c in sid.split(GRAPH_FIELD_SEP) if c]) or 1

        sweep = DedupSweep(self.dedup_store, self.workspace, self.llm_model_func,
                           apply_merge=apply, get_count=get_count,
                           batch_size=self._dedup_sweep_batch())
        return await sweep.run()

    async def unmerge_entity(self, merge_id: str) -> bool:
        # Restores the alias mapping/audit; does NOT re-split already-folded edges.
        return self.dedup_store.unmerge(self.workspace, merge_id)
```

> **This file is complete and self‑contained** — it depends only on the copied
> `knowledge_rag.graph_quality.*` packages plus the public LightRAG surface listed in Appendix C.
> Adaptations from `core.py` are marked with `←`/comments: the store properties return attached PG
> stores, `_node_filter` reads the PG ontology store, and `_process_extract_entities` calls
> `super()` (vanilla extraction) rather than Context Graph's contextual extractor.

<details>
<summary><b>Optional: the community "global" mode methods</b> (skip for v1)</summary>

The community methods need a `communities_vdb` vector index. Add it in `__post_init__` and
initialize/finalize it, then add the two methods:

```python
    # in __post_init__, after the guards above (only if you want community mode):
    self.communities_vdb = self.vector_db_storage_cls(
        namespace="communities", workspace=self.workspace,
        embedding_func=self.embedding_func,
        meta_fields={"community_id", "title", "size"})

    async def initialize_storages(self) -> None:
        await super().initialize_storages()
        await self.communities_vdb.initialize()

    async def finalize_storages(self) -> None:
        await super().finalize_storages()
        try:
            await self.communities_vdb.finalize()
        except Exception:
            pass

    async def build_communities(self, *, min_size: int = 3, max_communities: int = 300) -> dict:
        from knowledge_rag.graph_quality.community import detect_communities, CommunitySummarizer
        graph = self.chunk_entity_relation_graph
        labels = list(await graph.get_all_labels() or [])
        edges = list(await graph.get_all_edges() or [])
        comms = detect_communities(labels, edges, min_size=min_size)[:max_communities]
        summarizer = CommunitySummarizer(self.llm_model_func)
        try:
            await self.communities_vdb.drop()
        except Exception:
            pass
        records: list[dict] = []
        vdb_payload: dict = {}
        for i, members in enumerate(comms):
            cid = f"comm-{i}"
            member_dicts = []
            for name in members:
                node = await graph.get_node(name) or {}
                member_dicts.append({"name": name, "type": node.get("entity_type"),
                                     "description": (node.get("description") or "").split(GRAPH_FIELD_SEP)[0].strip()})
            s = await summarizer.summarize(member_dicts)
            records.append({"id": cid, "title": s["title"], "summary": s["summary"],
                            "members": members, "size": len(members)})
            vdb_payload[cid] = {"content": f"{s['title']}\n{s['summary']}",
                                "community_id": cid, "title": s["title"], "size": len(members)}
        if vdb_payload:
            await self.communities_vdb.upsert(vdb_payload)
            try:
                await self.communities_vdb.index_done_callback()
            except Exception as e:
                logger.warning(f"communities_vdb persist failed: {e}")
        self.community_store.replace(self.workspace, records)
        summary = {"communities": len(records), "members_covered": sum(r["size"] for r in records)}
        logger.info(f"build_communities: {summary}")
        return summary

    async def community_query(self, query: str, *, top_k: int = 5) -> dict:
        hits = await self.communities_vdb.query(query, top_k=top_k) or []
        used, blocks = [], []
        for h in hits:
            cid = h.get("community_id")
            rec = self.community_store.get(self.workspace, cid) if cid else None
            title = (rec or {}).get("title") or h.get("title") or cid
            summary = (rec or {}).get("summary") or ""
            used.append({"community_id": cid, "title": title})
            blocks.append(f"## {title}\n{summary}")
        if not blocks:
            return {"response": "No communities built yet — run build_communities first.",
                    "communities": []}
        prompt = ("Answer the question using these knowledge-graph community summaries as "
                  "context. Be holistic — draw on the relevant themes and name them.\n\n"
                  f"COMMUNITIES:\n{chr(10).join(blocks)}\n\n---\nQuestion: {query}")
        try:
            answer = await self.llm_model_func(prompt)
        except Exception as e:
            answer = f"(LLM error: {e})"
        return {"response": answer, "communities": used}
```

`namespace="communities"` creates a separate PG vector collection; no extra migration needed
(LightRAG's `PGVectorStorage` provisions it). Omit this whole block if you skip community mode.
</details>

**Inject the subclass via the factory.** One backward‑compatible change in
`pt-knowledge-rag-core/knowledge_rag/lightrag_init.py`:

```python
async def make_lightrag(*, working_dir, workspace, llm_model_func, embedding_func,
                        llm_model_name=None, lightrag_cls=LightRAG, **lightrag_kwargs):   # + lightrag_cls
    ...
    rag = lightrag_cls(                 # was: LightRAG(
        working_dir=working_dir, workspace=workspace,
        llm_model_func=llm_model_func, embedding_func=embedding_func,
        kv_storage="PGKVStorage", vector_storage="PGVectorStorage",
        graph_storage="PGGraphStorage", doc_status_storage="PGDocStatusStorage",
        **lightrag_kwargs,
    )
    await rag.initialize_storages()
    return rag
```

Then in `ingestion_server/main.py` `lifespan` (where `_pool` exists), build the stores and attach:

```python
from knowledge_rag.graph_quality.quality_lightrag import QualityLightRAG
from knowledge_rag.graph_quality.stores_pg import (
    PgDedupStore, PgQuarantineStore, PgOntologyStore, PgCommunityStore)

_rag = await make_lightrag(working_dir=working_dir, workspace=workspace,
                           llm_model_func=llm_model_func, embedding_func=embedding_func,
                           lightrag_cls=QualityLightRAG)   # the one added kwarg
_rag.attach_quality(
    dedup_store      = PgDedupStore(_pool),
    quarantine_store = PgQuarantineStore(_pool),
    ontology_store   = PgOntologyStore(_pool),
    community_store  = PgCommunityStore(_pool),   # optional
)
```

The `mcp_server` (query‑only) can keep constructing plain `LightRAG` — it doesn't need the
maintenance methods.

---

## 8. Step 5 — On‑ingest garbage filter (automatic)

The only feature that runs **automatically during ingest** is the garbage filter. It's already
wired in the subclass above via the `_process_extract_entities` override, which runs the base
extractor and then `_filter_extracted` (both fully inlined in Step 4). That filter:

* is a **no‑op unless `GARBAGE_FILTER_ENABLED`** (env, default true);
* drops obviously‑garbage nodes (git hashes, pure numbers, env‑var names, bare paths, pronoun/empty
  names) via the deterministic `quality_check` gate, plus ontology type checks in **closed‑world**
  mode; and
* records what it dropped to the quarantine store — nothing is hard‑deleted.

Because your `worker.py` calls `self.rag.ainsert(...)` (`worker.py:225`) and `ainsert` invokes
`_process_extract_entities` internally, **no change to `worker.py` is required** — the subclass
does it. (If you prefer not to subclass the extraction path, the alternative is a post‑`ainsert`
`scan_garbage(apply=True)` call right after `worker.py:229`; the on‑ingest override is cheaper and
catches nodes before they're indexed.)

---

## 9. Step 6 — Config knobs

Follow your two‑tier pattern (frozen `Config` dataclass + compose env, with optional
`app_settings` live override). Add to `ingestion_server/config.py`:

```python
# ingestion_server/config.py — Config dataclass
garbage_filter_enabled: bool = field(default_factory=lambda: _env("GARBAGE_FILTER_ENABLED", "true") == "true")
garbage_closed_world:   bool = field(default_factory=lambda: _env("GARBAGE_CLOSED_WORLD", "false") == "true")
dedup_enabled:          bool = field(default_factory=lambda: _env("DEDUP_ENABLED", "true") == "true")
dedup_hard:            float = field(default_factory=lambda: float(_env("DEDUP_HARD", "0.93")))
dedup_gray:            float = field(default_factory=lambda: float(_env("DEDUP_GRAY", "0.85")))
dedup_sweep_batch:       int = field(default_factory=lambda: int(_env("DEDUP_SWEEP_BATCH", "10")))
dedup_sweep_interval:    int = field(default_factory=lambda: int(_env("DEDUP_SWEEP_INTERVAL", "0")))   # sec; 0 = off
```

Declare the same keys in the `ingestion-api` `environment:` block of `docker-compose.yml`
(alongside `RECYCLE_*` / `VISION_*`). The core reads `GARBAGE_*` / `DEDUP_HARD` / `DEDUP_GRAY`
via `os.getenv`, so setting them in the container env is sufficient. For admin‑tunable thresholds
without a restart, mirror your `retention.recycle_days` precedent:
`float(await get_app_setting(_pool, "dedup.hard", config.dedup_hard))`.

**Threshold meaning:** cosine over the entity embedding (your `text-embedding-3-large`).
`≥ DEDUP_HARD` auto‑merges inline; `[DEDUP_GRAY, DEDUP_HARD)` is queued for the LLM sweep.

---

## 10. Step 7 — HTTP endpoints in `ingestion_server`

Add plain `@app.*` handlers (no `APIRouter` — match your file), reading the `_rag`/`_pool`/`_workspace`
globals, mirroring `_delete_one_with_lightrag`. **No in‑service auth** (internal‑only container);
gate them behind the admin role in the web UI, per your roadmap principle A3.

```python
# ingestion_server/main.py — globals _rag, _pool, _workspace already exist.
import asyncio
from fastapi import HTTPException

def _rag_or_503():
    if _rag is None:
        raise HTTPException(503, "LightRAG not initialized")
    return _rag

# ---- connectivity ----
@app.get("/api/graph/connectivity")
async def graph_connectivity():
    return await _rag_or_503().connectivity_report(sample_isolates=8)

@app.post("/api/graph/connectivity/rescue")
async def graph_rescue(apply: bool = False, limit: int = 20, max_candidates: int = 8):
    return await _rag_or_503().rescue_isolates(apply=apply, limit=limit, max_candidates=max_candidates)

@app.post("/api/graph/prune/isolates")
async def graph_prune(apply: bool = False):
    return await _rag_or_503().prune_isolates(apply=apply)

# ---- dedup ----  (async _rag methods → await directly)
@app.post("/api/graph/dedup/scan")
async def graph_dedup_scan(apply: bool = False):
    rag = _rag_or_503()
    if not rag.dedup_enabled:
        raise HTTPException(403, "dedup disabled")
    return await rag.deduplicate_entities(apply=apply)

@app.post("/api/graph/dedup/sweep")
async def graph_dedup_sweep():
    rag = _rag_or_503()
    if not rag.dedup_enabled:
        raise HTTPException(403, "dedup disabled")
    return await rag.run_dedup_sweep()

# ---- dedup reads (sync store → offload to a thread, per the store design) ----
@app.get("/api/graph/dedup/review")
async def graph_dedup_review():
    rag, ws = _rag_or_503(), _workspace
    pending, summary = await asyncio.to_thread(
        lambda: (rag.dedup_store.list_pending(ws), rag.dedup_store.summary(ws)))
    return {"pending": pending, "summary": summary}

@app.get("/api/graph/entities/merges")
async def graph_entity_merges(include_undone: bool = False):
    rag, ws = _rag_or_503(), _workspace
    merges = await asyncio.to_thread(rag.dedup_store.list_merges, ws, include_undone=include_undone)
    return {"merges": [m.to_dict() for m in merges]}

@app.post("/api/graph/entities/unmerge")
async def graph_entity_unmerge(merge_id: str):
    ok = await _rag_or_503().unmerge_entity(merge_id)
    if not ok:
        raise HTTPException(404, "merge not found")
    return {"ok": True}

# ---- garbage / quarantine ----
@app.post("/api/graph/garbage/scan")
async def graph_garbage_scan(apply: bool = False):
    return await _rag_or_503().scan_garbage(apply=apply)

@app.get("/api/graph/quarantine")
async def graph_quarantine():
    rag, ws = _rag_or_503(), _workspace
    items, summary = await asyncio.to_thread(
        lambda: (rag.quarantine_store.list(ws), rag.quarantine_store.summary(ws)))
    return {"items": items, "summary": summary}

@app.post("/api/graph/quarantine/restore")
async def graph_quarantine_restore(name: str):
    rag, ws = _rag_or_503(), _workspace
    item = await asyncio.to_thread(rag.quarantine_store.pop, ws, name)
    if item is None:
        raise HTTPException(404, "not in quarantine")
    await rag.acreate_entity(name, {"entity_type": item.get("entity_type") or "UNKNOWN",
                                    "description": item.get("description") or name})
    return {"restored": name}

@app.post("/api/graph/quarantine/discard")
async def graph_quarantine_discard(name: str):
    rag, ws = _rag_or_503(), _workspace
    item = await asyncio.to_thread(rag.quarantine_store.pop, ws, name)
    if item is None:
        raise HTTPException(404, "not in quarantine")
    return {"discarded": name}

# ---- community (optional) ----
@app.post("/api/graph/community/build")
async def graph_community_build(min_size: int = 3, max_communities: int = 300):
    return await _rag_or_503().build_communities(min_size=min_size, max_communities=max_communities)

@app.get("/api/graph/communities")
async def graph_communities():
    rag, ws = _rag_or_503(), _workspace
    lst, summary = await asyncio.to_thread(
        lambda: (rag.community_store.list(ws), rag.community_store.summary(ws)))
    return {"communities": lst, "summary": summary}

@app.post("/api/graph/community/query")
async def graph_community_query(body: dict):
    return await _rag_or_503().community_query(body["query"], top_k=int(body.get("top_k", 5)))
```

**Ontology endpoints** — build the service once at startup
(`_onto = OntologyService(PgOntologyStore(sync_pool))`), then:

```python
from knowledge_rag.graph_quality.ontology import OntologyService
from knowledge_rag.graph_quality.ontology.agent import OntologyAuthor

@app.get("/api/ontology")
async def ontology_get():
    return await asyncio.to_thread(_onto.get_summary, _workspace)

@app.post("/api/ontology")
async def ontology_save(body: dict):
    try:
        return await asyncio.to_thread(_onto.save, _workspace, body["ontology"])
    except (ValueError, KeyError, TypeError) as e:
        raise HTTPException(400, str(e))

@app.delete("/api/ontology")
async def ontology_delete():
    return {"deleted": await asyncio.to_thread(_onto.delete, _workspace)}

@app.post("/api/ontology/generate")
async def ontology_generate(body: dict):
    rag = _rag_or_503()
    if rag.llm_model_func is None:
        raise HTTPException(503, "LLM not configured")
    base = (await asyncio.to_thread(_onto.store.load, _workspace)) if body.get("extend", True) else None
    result = await OntologyAuthor(rag.llm_model_func).generate(
        body["description"], base=base, max_repairs=int(body.get("max_repairs", 1)))
    if body.get("save") and result.valid:
        await asyncio.to_thread(_onto.save, _workspace, result.ontology.to_dict())
    return {"valid": result.valid, "ontology": result.ontology.to_dict(),
            "explanation": result.explanation, "attempts": result.attempts, "saved": bool(body.get("save") and result.valid)}

@app.post("/api/ontology/validate")
async def ontology_validate(body: dict):
    return await asyncio.to_thread(
        _onto.validate_extraction, _workspace,
        body.get("entities", []), body.get("relations", []),
        closed_world=bool(body.get("closed_world", False)))
```

That's the complete set — **15 graph routes + 5 ontology routes**. Appendix B has the same list as a
route ↔ method ↔ response‑fields table for quick reference.

> **Consistency for mutating ops:** `deduplicate_entities` / `scan_garbage` / `prune_isolates`
> call `index_done_callback()` on the graph + vdbs after applying, because these run **outside**
> the ingest pipeline that would otherwise flush. The copied method bodies already do this — keep it.

---

## 11. Step 8 — (optional) background dedup sweep

The gray band (`[GRAY, HARD)`) is queued, not merged inline. To drain it automatically, add a
background task in your `ingestion_server` lifespan mirroring your recycle‑bin sweep:

```python
if config.dedup_enabled and config.dedup_sweep_interval > 0:
    async def _sweep_loop():
        while True:
            await asyncio.sleep(config.dedup_sweep_interval)
            try:
                if _rag and _rag.dedup_store.list_pending(_workspace):
                    await _rag.run_dedup_sweep()      # LLM adjudicates the queue
            except asyncio.CancelledError: raise
            except Exception: logger.exception("dedup sweep failed")
    app.state.sweep = asyncio.create_task(_sweep_loop())
```

Default `DEDUP_SWEEP_INTERVAL=0` (off) — operators run `/api/graph/dedup/sweep` manually until
they trust it. Cancel the task on shutdown.

---

## 12. Step 9 — The NiceGUI UI

A single page + a few client methods, following your existing page contract
(`def build(container)`, `api_client` singleton returning `(dict, DebugExchange)`, `ui.notify`,
`_json_block`). Because you're single‑tenant, **no workspace selector is needed** — the endpoints
resolve `kdefault_v1` server‑side.

**1. `debug_webui/api_client.py`** — add methods hitting the ingestion service (they live on
`INGESTION_URL`, not `LIGHTRAG_URL`):

```python
async def graph_connectivity(self):
    return await _do_request("GET", f"{cfg.INGESTION_URL}/api/graph/connectivity", timeout=60.0)
async def dedup_scan(self, apply: bool = False):
    return await _do_request("POST", f"{cfg.INGESTION_URL}/api/graph/dedup/scan?apply={str(apply).lower()}", timeout=180.0)
async def garbage_scan(self, apply: bool = False):
    return await _do_request("POST", f"{cfg.INGESTION_URL}/api/graph/garbage/scan?apply={str(apply).lower()}", timeout=180.0)
async def quarantine_list(self):
    return await _do_request("GET", f"{cfg.INGESTION_URL}/api/graph/quarantine", timeout=60.0)
# ... rescue, prune, dedup_sweep, dedup_review, entity_merges, entity_unmerge, community_* — one per endpoint
```

**2. `debug_webui/pages/graph_quality.py`** (new) — one `build()` with an action toolbar + a
result area, following your `query.py` Execute/spinner/`_json_block` idiom:

```python
from nicegui import ui
from api_client import api

def build(container):
    with container:
        ui.label("Graph Quality").classes("text-2xl font-bold mb-2")
        result = ui.column().classes("w-full")

        async def run(coro_fn, label):
            result.clear()
            with result: ui.spinner("dots", size="lg")
            res, ex = await coro_fn()
            result.clear()
            with result:
                if ex.error or (ex.response and ex.response.status >= 400):
                    ui.label(f"Error: {ex.error or ex.response.status}").classes("text-red-500")
                    ui.notify(f"{label} failed", type="negative"); return
                ui.code(__import__("json").dumps(res, indent=2, default=str), language="json").classes("w-full")
                ui.notify(f"{label} ok", type="positive")

        with ui.row().classes("gap-2 mb-4"):
            ui.button("Connectivity",   on_click=lambda: run(api.graph_connectivity, "connectivity"))
            ui.button("Dedup preview",  on_click=lambda: run(lambda: api.dedup_scan(False), "dedup preview"))
            ui.button("Dedup + merge",  on_click=lambda: run(lambda: api.dedup_scan(True),  "dedup merge")).props("color=warning")
            ui.button("Garbage preview",on_click=lambda: run(lambda: api.garbage_scan(False),"garbage preview"))
            ui.button("Quarantine",     on_click=lambda: run(api.quarantine_list, "quarantine"))
        ui.timer(0.1, lambda: run(api.graph_connectivity, "connectivity"), once=True)  # first paint
```

**3. `debug_webui/app.py`** — three edits: add `graph_quality` to the `from pages import ...`
line; add `("Graph Quality", "/graph-quality")` to `NAV_ITEMS`; add the route block:

```python
@ui.page("/graph-quality")
def graph_quality_page():
    _header("Graph Quality")
    with ui.column().classes("w-full max-w-7xl mx-auto p-4"):
        graph_quality.build(ui.column().classes("w-full"))
```

For the full 5‑card UX (connectivity dashboard tiles, dedup review queue, merge audit with
per‑row Unmerge, quarantine list with Restore/Discard, communities + thematic Q&A), see the
reference panel `lightrag_webui/src/features/GraphQuality.tsx` in Context Graph and the
response‑field list in Appendix B. An **Ontology** manager page (`pages/ontology.py`) follows the
same contract against the `/api/ontology*` endpoints.

> **Admin‑gating:** since these endpoints are unauthenticated at the ingestion layer, show the
> Graph Quality nav entry only to admin operators in the web UI (your existing role check).

---

## 13. Step 10 — (optional) MCP read‑only diagnostics

Do **not** expose destructive maintenance as agent MCP tools — it's an operator action. If agents
need read‑only visibility, add only `connectivity_report` / `quarantine` as `@app.tool(...)` in
`mcp_server/tools_query.py` (bearer‑token authed), obtaining the rag via the existing
`_get_lightrag` helper.

---

## 14. Tests

Context Graph's tests for these packages are **offline** (no external services) and port directly —
they exercise the packages through their injected‑callable contracts, so they don't need your PG
wiring:

* `context_graph/tests/` — ontology, dedup canonical/resolver/sweep, quality gate/filter.
* `tests/test_context_graph.py` — the dedup/garbage/connectivity method behaviors (mock graph/vdb).

Copy the ones covering the four packages, rewrite the `context_graph.` imports to
`knowledge_rag.graph_quality.`, and they run under your pytest. Add one integration test that
round‑trips a `PgDedupStore` against a test database to cover the new blob primitives.

---

## 15. Phased rollout checklist

1. **[ ] Vendor packages** (Step 1) + sever the rules edge (Step 2). *Verify:* `python -c "import knowledge_rag.graph_quality.ontology, ...dedup, ...quality, ...connectivity"` imports clean.
2. **[ ] Postgres stores** (Step 3): migration `007`, `stores_pg.py`, ensure‑schema in `db.py`. *Verify:* a store round‑trips a blob.
3. **[ ] `QualityLightRAG`** (Step 4) + `lightrag_cls` in `make_lightrag` + `attach_quality` in ingestion lifespan. *Verify:* service boots, `_rag.connectivity_report()` returns numbers.
4. **[ ] Read‑only endpoints first** (connectivity, quarantine list, dedup/garbage **preview** with `apply=false`) (Step 7). *Verify:* curl returns sane counts; nothing mutates.
5. **[ ] NiceGUI page** with the preview buttons (Step 9). *Verify:* operator sees the dashboard.
6. **[ ] Enable mutations**: dedup merge, garbage remove, isolate rescue/prune, quarantine restore/discard. Test on a **copy** of the graph first; all are quarantine‑backed/reversible except merge (see caveat).
7. **[ ] On‑ingest garbage filter** (Step 5): flip `GARBAGE_FILTER_ENABLED=true`, ingest a doc, confirm garbage lands in quarantine, real nodes don't.
8. **[ ] Background sweep** (Step 8) and/or **community** + **ontology** (optional).

**Caveats to communicate to operators:**

* `unmerge_entity` restores the alias mapping/audit but does **not** re‑split already‑folded graph
  edges (same limitation as in Context Graph). Merge is the one op to preview carefully.
* Garbage removal and isolate prune move nodes to **quarantine** (restorable), not hard delete.
* Only **degree‑0** isolates are pruned; degree‑1 leaves are intentionally left alone.

---

## Appendix A — File inventory (what you copy vs. what you write)

| Copied from Context Graph | New (you write) |
|---|---|
| `jsonio.py` | `projection_min.py` (2 regex parsers) |
| `dedup/` (4), `quality/` (3), `connectivity/` (1), `ontology/` (5), `community/` (3, optional) | `stores_pg.py` (mixin + 4 backends) |
| tests for the above | `quality_lightrag.py` (the subclass) |
|  | `migrations/007-graph-quality.sql` |
|  | endpoints in `ingestion_server/main.py`; config in `config.py`; `pages/graph_quality.py` + `api_client` methods + `app.py` edits |

**Total copied ≈ 16 files + tests; total new ≈ 5 files + edits to 4 existing files.** No changes to
your LightRAG fork.

---

## Appendix B — Endpoint reference (Context Graph route → your route → method → response fields)

All new routes on `ingestion_server` under `/api/graph/*` and `/api/ontology*`. Method column is
the `QualityLightRAG` method. Response fields are what the UI reads.

| Your route | Method (`_rag`) | Params | Response fields (UI reads) |
|---|---|---|---|
| `GET  /api/graph/connectivity` | `connectivity_report(sample_isolates=8)` | — | `total_nodes, total_edges, isolated_nodes, isolated_pct, connected_components, largest_component_pct, degree.{mean,median,max,degree0,degree1}, isolate_sample[]` |
| `POST /api/graph/dedup/scan` | `deduplicate_entities(apply)` | `apply: bool` | `scanned, merged, queued, skipped` |
| `POST /api/graph/dedup/sweep` | `run_dedup_sweep()` | — | `merged, rejected` |
| `GET  /api/graph/dedup/review` | `dedup_store.list_pending` + `.summary` | — | `pending[], summary.{pending_review, merges_live}` |
| `GET  /api/graph/entities/merges` | `dedup_store.list_merges(include_undone)` | `include_undone: bool` | `merges[].{id, alias, into, method}` |
| `POST /api/graph/entities/unmerge` | `unmerge_entity(merge_id)` | `merge_id: str` | `{ok}` (404 if unknown) |
| `POST /api/graph/garbage/scan` | `scan_garbage(apply)` | `apply: bool` | `scanned, quarantined, removed, by_reason{}, sample[]` |
| `GET  /api/graph/quarantine` | `quarantine_store.list` + `.summary` | — | `items[].{name, reason}, summary.{count, by_reason}` |
| `POST /api/graph/quarantine/restore` | pop + `acreate_entity(name, …)` | `name: str` | `{restored}` (404 if absent) |
| `POST /api/graph/quarantine/discard` | `quarantine_store.pop` | `name: str` | `{discarded}` |
| `POST /api/graph/connectivity/rescue` | `rescue_isolates(apply, limit, max_candidates)` | `apply, limit, max_candidates` | `isolates_scanned, connected, edges_added` |
| `POST /api/graph/prune/isolates` | `prune_isolates(apply, limit)` | `apply, limit` | `isolates, removed, preview, sample[]` |
| `POST /api/graph/community/build` | `build_communities(min_size, max_communities)` | `min_size, max_communities` | `communities` (count) |
| `GET  /api/graph/communities` | `community_store.list` + `.summary` | — | `communities[].{id, size, title}, summary.communities` |
| `POST /api/graph/community/query` | `community_query(query, top_k)` | body `{query, top_k}` | `response, communities[].title` |
| `GET/POST/DELETE /api/ontology` | `OntologyService.get_summary / save / delete` | body `{ontology}` on POST | `workspace, exists, name, version, object_types[], link_types[], lint[]` |
| `POST /api/ontology/generate` | `OntologyAuthor(llm).generate(...)` | body `{description, extend, save, max_repairs}` | `valid, ontology, explanation, attempts, dry_run.{conforming,total}` |
| `POST /api/ontology/validate` | `OntologyService.validate_extraction(...)` | body `{entities, relations, closed_world}` | `ok, total, conforming, violations[]` |

---

## Appendix C — LightRAG surface used (why it's backend‑agnostic over Postgres)

Every graph mutation/read the features perform goes through these public LightRAG members, all
present and PG‑backed in your fork:

| Symbol | Used by | Confirmed in your fork |
|---|---|---|
| `chunk_entity_relation_graph.get_all_labels` | dedup, garbage, connectivity | `postgres_impl.py:5731` |
| `chunk_entity_relation_graph.get_all_edges` | connectivity, dedup mention‑count | `postgres_impl.py:6125` |
| `chunk_entity_relation_graph.get_node` | all | `postgres_impl.py:5014` |
| `chunk_entity_relation_graph.delete_node` | garbage, prune | `postgres_impl.py:5283` |
| `chunk_entity_relation_graph.index_done_callback` | all mutating ops | `postgres_impl.py:4737` |
| `amerge_entities` | dedup merge | `lightrag.py:4397` |
| `acreate_relation` | isolate rescue | `lightrag.py:4363` |
| `acreate_entity` | quarantine restore | `lightrag.py:4333` |
| `entities_vdb.query` | dedup candidates, rescue candidates | `BaseVectorStorage` (PGVectorStorage) |
| `entities_vdb.delete` | `_remove_entity` | `PGVectorStorage` |

Because these satisfy the `BaseGraphStorage` / `BaseVectorStorage` contracts, the features never
touch Postgres directly — they'd work identically on NetworkX or Neo4j. Your all‑PG deployment
needs no special handling beyond the store migration in Step 3.

---

*Questions on any step, or want the fully‑expanded method bodies inlined rather than referenced by
`core.py` line — ask and we'll extend this guide.*
