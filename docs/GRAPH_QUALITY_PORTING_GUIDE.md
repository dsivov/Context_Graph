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
# Copied verbatim from context_graph/rules/projection.py (the two parsers + their regexes).
# Pure stdlib — no RelationContext, no rules engine.
import re
_PERCENT_RE = ...      # copy
_AMOUNT_RE  = ...      # copy
_MAGNITUDE  = ...      # copy
def parse_percent(text): ...   # copy
def parse_amount(text):  ...   # copy
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
import asyncpg, json
from .dedup.store import DedupStore
from .quality.store import QuarantineStore
from .ontology.store import OntologyStore
from .community.store import CommunityStore   # optional

class _PgBlob:
    """Implements the raw-blob primitives against graph_quality_store.
    NOTE: the base stores are synchronous. Run each store call inside a thread
    that owns a short-lived synchronous psycopg connection, OR (recommended)
    give this mixin an asyncpg pool and expose async wrappers — see note below.
    """
    KIND: str = ""
    def __init__(self, pool: asyncpg.Pool, **kw):
        super().__init__(**kw)
        self._pool = pool
    # ... _read_raw / _write_raw implemented via SELECT/UPSERT on (KIND, workspace)

class PgDedupStore(_PgBlob, DedupStore):        KIND = "dedup"
class PgQuarantineStore(_PgBlob, QuarantineStore): KIND = "quarantine"
class PgOntologyStore(_PgBlob, OntologyStore):  KIND = "ontology"
class PgCommunityStore(_PgBlob, CommunityStore): KIND = "community"
```

> **Sync/async note.** The Context Graph stores are **synchronous** (they were file‑backed). You
> have two clean choices:
> 1. **Simplest:** back `_read_raw`/`_write_raw` with a **synchronous** psycopg call (a small
>    dedicated pool). The store methods are cheap (one row read + one write per op) and run from
>    async endpoints via `await asyncio.to_thread(store.record_merge, ...)`.
> 2. **Async‑native:** add `async _aread_raw/_awrite_raw` using your `asyncpg` pool and make the
>    handful of public methods you actually call (`record_merge`, `enqueue_review`, `add`, `list`,
>    `pop`, `summary`, `save`, `load`) `async`. More work, no thread hop.
>
> Option 1 needs the least code and matches how infrequently these run (operator‑triggered
> maintenance). Either way, **the inherited logic is untouched** — you only implement blob I/O.

`app_settings` alternative for ontology: if you'd rather not add a table for the ontology, its
blob is config‑shaped and can live in your existing `app_settings` JSONB KV under
`ontology.kdefault_v1`. The `PgOntologyStore` primitives just read/write that key.

---

## 7. Step 4 — The glue: `QualityLightRAG(LightRAG)`

Context Graph's orchestration methods live on the `ContextGraph` class. Re‑home the four features'
methods on a small subclass. **They use only public LightRAG API + the injected stores**, so you
can copy the method bodies almost verbatim from `context_graph/core.py`.

```python
# knowledge_rag/graph_quality/quality_lightrag.py
import os
from lightrag import LightRAG
from lightrag.utils import compute_mdhash_id
from lightrag.constants import GRAPH_FIELD_SEP
from .dedup import canonicalize, prefer_canonical_name, type_ok, name_ok, DedupSweep, DEFAULT_HARD, DEFAULT_GRAY
from .quality import NodeFilter, quality_check
from .connectivity import IsolateRescue

class QualityLightRAG(LightRAG):
    """Vanilla LightRAG + on-demand graph-quality ops + an on-ingest garbage filter.
    Stores are attached after construction (they need the asyncpg pool)."""

    # --- store wiring (attached by the service that owns the pool) ---
    def attach_quality(self, *, dedup_store, quarantine_store,
                       ontology_store, community_store=None) -> None:
        self._dedup_store = dedup_store
        self._quarantine_store = quarantine_store
        self._ontology_store = ontology_store
        self._community_store = community_store
        self._node_filter_cache = None

    @property
    def dedup_store(self): return self._dedup_store
    @property
    def quarantine_store(self): return self._quarantine_store
    @property
    def community_store(self): return self._community_store

    # --- shared low-level primitives (copy from core.py) ---
    async def _apply_entity_merge(self, alias, into, canonical):
        ...   # core.py:1297  (uses self.amerge_entities)
    async def _remove_entity(self, name):
        ...   # core.py:729   (uses graph.delete_node + entities_vdb.delete)
    async def _isolated_nodes(self, limit):
        ...   # core.py:798   (uses get_all_labels/get_all_edges/get_node)

    def _node_filter(self):
        # core.py:675 — but load the ontology from the PG store, not a JSON dir:
        if self._node_filter_cache is None:
            onto = self._ontology_store.load(self.workspace) if self._ontology_store else None
            closed = os.getenv("GARBAGE_CLOSED_WORLD", "false").lower() in ("1","true","yes","on")
            self._node_filter_cache = NodeFilter(onto, closed_world=closed)
        return self._node_filter_cache

    # --- on-demand ops (copy bodies verbatim from core.py) ---
    async def deduplicate_entities(self, *, apply=True, limit=5000): ...   # core.py:1309
    async def run_dedup_sweep(self):                                  ...   # core.py:1395
    async def unmerge_entity(self, merge_id):                         ...   # core.py:1418
    async def scan_garbage(self, *, apply=True, limit=100000):        ...   # core.py:737
    async def connectivity_report(self, *, sample_isolates=20):      ...   # core.py:1184
    async def rescue_isolates(self, *, apply=True, limit=50, max_candidates=8): ...  # core.py:822
    async def prune_isolates(self, *, apply=False, limit=100000):    ...   # core.py:876
    # optional (community):
    async def build_communities(self, *, min_size=3, max_communities=300): ...  # core.py:939
    async def community_query(self, query, *, top_k=5):              ...   # core.py (community_query)

    # --- on-ingest garbage filter (Step 5) ---
    async def _process_extract_entities(self, chunk, pipeline_status=None, pipeline_status_lock=None):
        results = await super()._process_extract_entities(chunk, pipeline_status, pipeline_status_lock)
        return self._filter_extracted(results)      # core.py:694 — copy _filter_extracted too
```

> **Copying the bodies.** Each listed method in `context_graph/core.py` uses only:
> `self.chunk_entity_relation_graph.{get_all_labels,get_all_edges,get_node,delete_node,index_done_callback}`,
> `self.entities_vdb.{query,delete,index_done_callback}`, `self.relationships_vdb.index_done_callback`,
> `self.amerge_entities`, `self.acreate_relation`, `self.acreate_entity`, `self.llm_model_func`,
> `self.embedding_func`, `self.workspace`, and the injected stores. **All confirmed present on
> your PG‑backed LightRAG.** The only edits when copying: (a) `_node_filter` loads ontology from
> the PG store (shown above), and (b) the store‑construction `@property` blocks are replaced by
> the attached stores (shown above). Everything else pastes unchanged.

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
                           lightrag_cls=QualityLightRAG, ...)
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
wired above via the `_process_extract_entities` override, which runs the base extractor and then
`_filter_extracted` (copy from `core.py:694`). That method:

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
# ingestion_server/main.py
@app.get("/api/graph/connectivity")
async def graph_connectivity():
    if _rag is None: raise HTTPException(503, "LightRAG not initialized")
    return await _rag.connectivity_report(sample_isolates=8)

@app.post("/api/graph/dedup/scan")
async def graph_dedup_scan(apply: bool = False):
    return await _rag.deduplicate_entities(apply=apply)

@app.post("/api/graph/garbage/scan")
async def graph_garbage_scan(apply: bool = False):
    return await _rag.scan_garbage(apply=apply)

@app.post("/api/graph/connectivity/rescue")
async def graph_rescue(apply: bool = False, limit: int = 20):
    return await _rag.rescue_isolates(apply=apply, limit=limit)

@app.post("/api/graph/prune/isolates")
async def graph_prune(apply: bool = False):
    return await _rag.prune_isolates(apply=apply)

@app.get("/api/graph/quarantine")
async def graph_quarantine():
    ws = _workspace
    return {"items": _rag.quarantine_store.list(ws),
            "summary": _rag.quarantine_store.summary(ws)}
# ... restore/discard, dedup/sweep, dedup/review, entities/merges, entities/unmerge,
#     community/build, communities, community/query — full list in Appendix B.
```

The full route ↔ method mapping (15 graph‑quality routes + 5 ontology routes) is in **Appendix B**.
Response shapes are already what the UI consumes — see Appendix B for the fields.

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
