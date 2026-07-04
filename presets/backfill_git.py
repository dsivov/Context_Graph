#!/usr/bin/env python3
"""Backfill an existing git project's development reality into a CG workspace.

For projects that predate the Context Graph flow: deterministic (no LLM) import
of the structural + historical facts a fresh workspace should already know —

  * source directories        → Module entities (Developer -owns-> Module)
  * top-level module deps      → Module -depends_on-> Module (heuristic: all → the
                                 largest module)
  * the git author             → Developer entity
  * recent commits             → Commit entities, Commit -touches-> Module (from
                                 the files each commit changed)

Assumes a workspace already has a dev-shaped ontology (e.g. via POST /onboard or
the agentic-dev preset). Uses the graph CRUD endpoints; relations are open-world,
so keywords that aren't in the ontology are warnings, not errors.

    python presets/backfill_git.py --repo /path/to/project --workspace myproj
    python presets/backfill_git.py --repo . --workspace myproj --commits 30
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import subprocess
import urllib.error
import urllib.request


def git(repo, *args):
    return subprocess.check_output(["git", "-C", repo, *args], text=True).strip()


def gather_docs(repo, limit):
    """Markdown docs worth ingesting for the semantic layer (README + docs/)."""
    patterns = ["README.md", "*.md", "docs/*.md", "docs/**/*.md"]
    out = {}
    for pat in patterns:
        for p in sorted(glob.glob(os.path.join(repo, pat), recursive=True)):
            if os.path.isfile(p):
                out[os.path.relpath(p, repo)] = p
    return dict(list(out.items())[:limit])


_CODE_EXTS = (".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".rb", ".kt", ".swift")
_CODE_SKIP = {"__pycache__", "node_modules", ".venv", "venv", "migrations", "dist", "build", ".git"}


def gather_code(repo, modules, limit, include_tests=False):
    """Source files to ingest for the code semantic layer (shallow paths first)."""
    out = {}
    for m in modules:
        if not include_tests and m in ("tests", "test"):
            continue
        base = os.path.join(repo, m)
        if not os.path.isdir(base):
            continue
        for root, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if d not in _CODE_SKIP and not d.startswith(".")
                       and (include_tests or d not in ("tests", "test"))]
            for f in files:
                if f.endswith(_CODE_EXTS):
                    p = os.path.join(root, f)
                    out[os.path.relpath(p, repo)] = p
    items = sorted(out.items(), key=lambda kv: (kv[0].count(os.sep), kv[0]))  # shallow first
    return dict(items[:limit])


def detect_modules(repo):
    """Top-level directories that contain source files."""
    exts = (".py", ".js", ".ts", ".tsx", ".go", ".rs", ".java", ".rb")
    mods = []
    for name in sorted(os.listdir(repo)):
        d = os.path.join(repo, name)
        if not os.path.isdir(d) or name.startswith(".") or name in ("node_modules", "__pycache__"):
            continue
        for _root, _dirs, files in os.walk(d):
            if any(f.endswith(exts) for f in files):
                mods.append(name)
                break
    return mods


def main() -> int:
    ap = argparse.ArgumentParser(description="Backfill a git project into a CG workspace.")
    ap.add_argument("--repo", required=True)
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--url", default=os.environ.get("LIGHTRAG_URL", "http://localhost:9621"))
    ap.add_argument("--api-key", default=os.environ.get("LIGHTRAG_API_KEY"))
    ap.add_argument("--commits", type=int, default=15)
    ap.add_argument("--modules", nargs="*", help="Override auto-detected module dirs.")
    ap.add_argument("--no-docs", action="store_true",
                    help="Skip ingesting README/docs (skip the semantic layer).")
    ap.add_argument("--max-docs", type=int, default=25)
    ap.add_argument("--code", action="store_true",
                    help="Also ingest source files so code-level facts are query-able.")
    ap.add_argument("--max-code-files", type=int, default=60)
    ap.add_argument("--include-tests", action="store_true",
                    help="Include tests/ when ingesting code (default: skip).")
    ap.add_argument("--no-reindex", action="store_true",
                    help="Skip reindexing decisions after extraction drains (default: reindex).")
    args = ap.parse_args()

    repo = os.path.abspath(args.repo)
    modules = args.modules or detect_modules(repo)
    if not modules:
        print("no source module directories detected", flush=True)
        return 1

    def post(path, body):
        data = json.dumps(body).encode()
        headers = {"Content-Type": "application/json", "LIGHTRAG-WORKSPACE": args.workspace}
        if args.api_key:
            headers["X-API-Key"] = args.api_key
        req = urllib.request.Request(args.url.rstrip("/") + path, data=data, method="POST", headers=headers)
        try:
            with urllib.request.urlopen(req):
                return True
        except urllib.error.HTTPError:
            return False

    def get(path):
        headers = {"LIGHTRAG-WORKSPACE": args.workspace}
        if args.api_key:
            headers["X-API-Key"] = args.api_key
        req = urllib.request.Request(args.url.rstrip("/") + path, method="GET", headers=headers)
        try:
            with urllib.request.urlopen(req) as r:
                return json.loads(r.read())
        except Exception:
            return None

    def entity(name, etype, **props):
        return post("/graph/entity/create", {"entity_name": name,
                    "entity_data": {"entity_type": etype, **props}})

    def relation(s, t, kw):
        return post("/graph/relation/create", {"source_entity": s, "target_entity": t,
                    "relation_data": {"keywords": kw, "weight": 1.0}})

    author = git(repo, "log", "-1", "--pretty=format:%an")
    entity(author, "Developer", description="Author on this repository")

    # largest module (by py/js file count) is the likely core others depend on
    def count(m):
        return sum(len(fs) for _r, _d, fs in os.walk(os.path.join(repo, m)))
    core = max(modules, key=count)
    for m in modules:
        entity(m, "Module", description=f"{m}/ source module", maturity="active")
        relation(author, m, "owns")
        if m != core:
            relation(m, core, "depends_on")

    log = git(repo, "log", f"-{args.commits}", "--pretty=format:%h\x1f%s").split("\n")
    modset = set(modules)
    ncommits = 0
    for line in log:
        if "\x1f" not in line:
            continue
        sha, subj = line.split("\x1f", 1)
        entity(sha, "Commit", description=subj)
        ncommits += 1
        files = git(repo, "show", "--name-only", "--pretty=format:", sha).split("\n")
        for m in {f.split("/", 1)[0] for f in files if f} & modset:
            relation(sha, m, "touches")

    print(f"backfilled '{args.workspace}': {author} (developer), "
          f"{len(modules)} modules {modules}, {ncommits} commits", flush=True)

    # --- semantic layer: ingest README/docs so the graph is query-able and the
    #     "why" (architecture / decisions in prose) is extracted with provenance.
    if not args.no_docs:
        docs = gather_docs(repo, args.max_docs)
        texts, sources = [], []
        for rel, path in docs.items():
            try:
                txt = open(path, encoding="utf-8", errors="ignore").read()
            except OSError:
                continue
            if txt.strip() and len(txt) <= 400_000:
                texts.append(txt)
                sources.append(rel)
        if texts and post("/documents/texts", {"texts": texts, "file_sources": sources}):
            print(f"queued {len(texts)} docs for extraction (async — poll "
                  f"/documents/pipeline_status): {sources}", flush=True)

    # --- code layer: ingest source files (full content) so code-level facts —
    #     symbol locations, paths, logic — become query-able with provenance.
    if args.code:
        code = gather_code(repo, modules, args.max_code_files, include_tests=args.include_tests)
        texts, sources = [], []
        for rel, path in code.items():
            try:
                txt = open(path, encoding="utf-8", errors="ignore").read()
            except OSError:
                continue
            if txt.strip() and len(txt) <= 200_000:
                texts.append(txt)
                sources.append(rel)
        # ingest in batches so a large repo doesn't overflow one request
        BATCH = 25
        total = 0
        for i in range(0, len(texts), BATCH):
            if post("/documents/texts", {"texts": texts[i:i + BATCH], "file_sources": sources[i:i + BATCH]}):
                total += len(texts[i:i + BATCH])
        if total:
            print(f"queued {total} source files for extraction (async — poll "
                  f"/documents/pipeline_status)", flush=True)

    # --- decisions reindex: extraction can produce RelationContext-bearing edges
    #     (decisions in prose). Those bypass the emit path, so once the pipeline
    #     drains, reindex projects them into the search fabric. Bounded + best-effort.
    if not args.no_docs and not args.no_reindex:
        import time
        print("waiting for extraction to drain before reindexing decisions…", flush=True)
        time.sleep(5)  # let the pipeline pick up the queued jobs
        for _ in range(240):  # ~20 min cap
            st = get("/documents/pipeline_status") or {}
            if not st.get("busy") and not st.get("request_pending"):
                break
            time.sleep(5)
        if post("/graph/decisions/reindex?wait=true", {}):
            print("reindexed decisions from the graph", flush=True)
        else:
            print("decision reindex skipped (endpoint unavailable — run it later)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
