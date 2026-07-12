# JSON Extraction Path (Step 4 — upstream LightRAG 1.5.x alignment)

**Status:** prototype, shipped **behind a flag that defaults OFF** (`CG_JSON_EXTRACTION=false`).
The proven delimiter extractor remains the default and is byte-for-byte unchanged when the
flag is off.

**Date:** 2026-07 · **Branch:** `main` · **Model used for validation:** `gpt-4o`

---

## 1. Why

Upstream LightRAG 1.5.x moved entity/relation extraction from a delimiter-tokenized text
format (`entity<|>name<|>type<|>desc`) toward a **single JSON object** per chunk. JSON is
more robust to parse (no positional-field counting), composes with `json_repair`, and maps
cleanly onto structured outputs.

Context Graph's extractor is a *customized* fork of the delimiter path — it appends a sixth
field, the `relation_context` (rc) quadruple, to every relationship. Adopting the JSON shape
is therefore not a drop-in cherry-pick: the rc must survive as a first-class key. Step 4
prototypes that adoption **without disturbing the shipped path** — everything lives behind
`CG_JSON_EXTRACTION`, default off.

## 2. What changed

| File | Change |
|------|--------|
| `context_graph/core.py` | `_process_cg_json_result` / `_rc_json_from_obj` (JSON→graph parser + rc normalizer); `json_mode` param on `extract_entities_with_context` with a prompt branch and a `_parse()` closure; `_json_extraction_enabled()` flag helper wired into `_process_extract_entities` |
| `lightrag/prompt.py` | `cg_entity_extraction_json_{system,user,continue}_prompt` + `cg_entity_extraction_json_examples` (two rc-populated few-shot demonstrations) |
| `lightrag/api/config.py` | `args.cg_json_extraction` from `CG_JSON_EXTRACTION` |
| `env.example` | documented `CG_JSON_EXTRACTION=false` block |
| `context_graph/tests/test_json_extraction.py` | 5 tests — parser unit tests + a mocked-LLM end-to-end orchestration test |

**How the flag routes:** with `json_mode=True`, `extract_entities_with_context` builds the
JSON-schema prompt, calls the same cached-LLM + gleaning machinery, then parses via
`_extract_json_object` (falling back to `json_repair.loads`) into the **identical**
`(maybe_nodes, maybe_edges)` structure the delimiter parser produces — so the downstream
merge pipeline is untouched. Few-shot examples carry literal JSON braces, so they are injected
into the system prompt via an `__EXAMPLES__` sentinel **after** `str.format()` to avoid brace
collisions.

## 3. Validation — A/B methodology

Harness: `scratchpad/ab_extract.py` (re-runnable). It runs **both** extractors on the **same
real `investigator` chunks** through the live LLM, then diffs entities, relations, and rc. It
**writes to no store** — pure read + compare. Gleaning is disabled (`entity_extract_max_gleaning=0`)
so each mode is exactly one LLM call per chunk. Metrics:

- **rc coverage** — % of relations that carry any `relation_context`.
- **rc field density** — average count of *populated* (non-null, non-empty) rc sub-fields per
  rc-bearing edge. This is the proxy for *how much decision lineage* each relation captures —
  the thing Context Graph exists to record.
- **field-accuracy on shared relations** — for `(src,tgt)` pairs found by *both* modes,
  compare the two rc objects side by side (apples-to-apples, removes naming/granularity noise).

### First run (4 chunks) — caught a disqualifying regression

| metric | Delimiter | JSON (initial prompt) |
|--------|-----------|-----------------------|
| entities | 22 | 40 (over-extraction) |
| relations | 12 | 30 (over-extraction) |
| **rc coverage** | 92% | **3%** ❌ |

**Root cause:** the initial JSON prompt had *zero* few-shot examples and a rule instructing
the model to *omit* rc for "ordinary" relationships — but the delimiter path (via its 3
worked examples) populates rc on nearly every relation. So the model emitted many bare
relations with no rc.

**Fix** (commit `fix(extraction): JSON prompt few-shot examples restore rc coverage`):
flip the rule to an **always-include baseline** — every relationship carries rc with at least
`supporting_sentences` + `confidence_score`, plus the decision/policy sub-fields whenever the
text supports them — and add **two JSON-shaped few-shot examples** mirroring the delimiter
demonstrations.

### Larger run (18 chunks) — post-fix, for confidence

| metric | Delimiter | JSON (fixed prompt) |
|--------|-----------|---------------------|
| entities | 109 | 98 |
| relations | 89 | 83 |
| **rc coverage** | 93% (85/91) | **100% (86/86)** |
| **rc field density** | **3.16** | 2.23 |

Field-accuracy on shared relations (same `(src,tgt)` in both modes) — representative:

```
[Attorney-General's Office -> Netanyahu]
  DELIM rc: supporting_sentences + quantitative_data "21 theme nodes resolved" + decision_trace + provenance
  JSON  rc: supporting_sentences + confidence_score

[Arnon Milchan -> Shaul Elovitch]
  DELIM rc: supporting_sentences + decision_trace + provenance
  JSON  rc: supporting_sentences + confidence_score
```

The 18-chunk run showed JSON attaching rc *reliably* (100%) but *thinly* (density 2.23) — it
was settling for `supporting_sentences` + `confidence_score` and leaving `decision_trace`,
`quantitative_data`, and `provenance` empty where the text supported them.

### Depth pass — explicit field triggers

To close the density gap, the JSON prompt rule was strengthened from a generic "add sub-fields
when the text supports them" into an explicit **text-signal → field** trigger list
(any number → `quantitative_data`; any stated reason/rationale/hypothesis → `decision_trace`;
identifiable source → `provenance`; etc.), with a "populate 3+ sub-fields when the text allows"
target. Two fresh 18-chunk runs:

| metric | Delimiter (run 1 / run 2) | JSON (run 1 / run 2) |
|--------|---------------------------|----------------------|
| rc coverage | 100% / 100% | 100% / 100% |
| **rc field density** | 3.50 / 3.31 | **2.78 / 2.72** |

JSON rc field density rose from **2.23 → ~2.75** (stable across both runs, **+23%**) with
coverage held at 100%. The gap to the delimiter path (~3.4) narrowed from ~0.9 to **~0.6** —
meaningfully closer, but not eliminated.

## 4. Findings

1. **rc coverage: JSON wins.** After the prompt fix JSON attaches rc to **100%** of relations
   vs the delimiter path's 93% — it never forgets the rc.
2. **rc depth: delimiter still leads, but the gap is now small.** Out of the box JSON
   populated only **~2.23** rc sub-fields per relation vs delimiter's ~3.16. After the depth
   pass (explicit field triggers) JSON rose to a stable **~2.75** vs delimiter's **~3.4** — the
   gap narrowed from ~0.9 to **~0.6**. JSON now fills `quantitative_data` / `decision_trace` /
   `provenance` far more often, but the delimiter path still captures marginally richer lineage.
   Since decision lineage *is* the product, that residual ~0.6-field gap is the last thing
   standing between the two paths.
3. **Entity/relation counts: comparable.** 109 vs 98 and 89 vs 83 — the initial 2–3×
   over-extraction is gone. Cross-mode *overlap* is modest (47/109 shared entities), but that
   reflects gpt-4o sampling variance on naming/granularity — the delimiter path varied just as
   much between runs (22→16 entities on the 4-chunk set).
4. **Parse robustness: JSON is structurally safer, delimiter is intermittently fragile.** One
   dense, code-heavy chunk produced ~23 delimiter "found 3/4 fields" / "found 5/4 fields"
   format-error warnings in one run and **0** in the next — non-deterministic mangling of the
   positional format that JSON is immune to by construction. This is exactly the upstream
   motivation for the JSON shape.

## 5. Recommendation

**Keep `CG_JSON_EXTRACTION` OFF as the default — but the two paths are now near parity.**

After the depth pass the JSON path is: at parity on rc *coverage* (100% vs 93–100%), more
robust to parse (structurally immune to the delimiter's intermittent field-count errors), and
within **~0.6 sub-fields** on rc *depth* (~2.75 vs ~3.4). The residual depth gap is the only
remaining reason not to flip — and it is now small enough that the trade (marginally thinner
lineage for guaranteed-valid parsing + guaranteed rc attachment) is a reasonable judgment call
rather than a clear regression.

**Options from here:**
- **Hold** — keep the flag off; the delimiter path's richer lineage is the safe default. (current)
- **One more prompt iteration** — push JSON density to ≥3.0 (e.g. require `provenance` on every
  relationship; add a third few-shot example dense in `quantitative_data`), then re-A/B.
- **Flip with monitoring** — accept the ~0.6-field trade for parse robustness, set the default
  on, and watch rc density on real ingests. Reversible via the flag.

A multi-run A/B (already done: two 18-chunk runs post-depth-pass) confirms the JSON density
gain (~2.75) is stable, not sampling noise.

## 6. How to run / enable

```bash
# Re-run the A/B (reads investigator chunks, writes nothing; needs .env LLM creds)
AB_N=18 python scratchpad/ab_extract.py

# Enable the JSON extractor for a real ingest (opt-in; default is the delimiter path)
export CG_JSON_EXTRACTION=true
lightrag-server --host 0.0.0.0 --port 9621
```

Offline unit + orchestration tests: `pytest context_graph/tests/test_json_extraction.py`.
