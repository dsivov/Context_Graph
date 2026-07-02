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
import json
import os
import subprocess
import urllib.error
import urllib.request


def git(repo, *args):
    return subprocess.check_output(["git", "-C", repo, *args], text=True).strip()


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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
