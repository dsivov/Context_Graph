#!/usr/bin/env python3
"""Install a Context Graph solution-pack preset into a workspace.

Generic: applies whichever pieces a preset directory provides, in dependency
order — ontology -> rules -> actions -> seed (entities then relations). Knows
nothing about any specific domain; the vocabulary lives entirely in the JSON.

    python presets/install.py --workspace my_project
    python presets/install.py --workspace acme --preset agentic-dev --url http://localhost:9621
    python presets/install.py --workspace acme --dry-run

Auth: pass --api-key or set LIGHTRAG_API_KEY (omitted if the server has no key).
Idempotent-ish: ontology/rules/actions replace-and-version; seed entity/relation
creation tolerates "already exists" and keeps going.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))


def strip_comment(obj):
    """Drop top-level _comment keys (documentation only)."""
    if isinstance(obj, dict):
        return {k: v for k, v in obj.items() if k != "_comment"}
    return obj


class Client:
    def __init__(self, url: str, workspace: str, api_key: str | None, dry_run: bool):
        self.url = url.rstrip("/")
        self.workspace = workspace
        self.api_key = api_key
        self.dry_run = dry_run

    def post(self, path: str, body: dict):
        if self.dry_run:
            return {"_dry_run": True}
        data = json.dumps(body).encode()
        headers = {"Content-Type": "application/json", "LIGHTRAG-WORKSPACE": self.workspace}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        req = urllib.request.Request(self.url + path, data=data, method="POST", headers=headers)
        try:
            with urllib.request.urlopen(req) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            return {"_http": e.code, "_body": e.read().decode()[:300]}


def load(preset_dir: str, name: str):
    path = os.path.join(preset_dir, name)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main() -> int:
    ap = argparse.ArgumentParser(description="Install a CG preset into a workspace.")
    ap.add_argument("--workspace", required=True, help="Target workspace (tenant).")
    ap.add_argument("--preset", default="agentic-dev", help="Preset directory under presets/.")
    ap.add_argument("--url", default=os.environ.get("LIGHTRAG_URL", "http://localhost:9621"))
    ap.add_argument("--api-key", default=os.environ.get("LIGHTRAG_API_KEY"))
    ap.add_argument("--dry-run", action="store_true", help="Print steps without calling the server.")
    args = ap.parse_args()

    preset_dir = args.preset if os.path.isdir(args.preset) else os.path.join(HERE, args.preset)
    if not os.path.isdir(preset_dir):
        print(f"preset not found: {preset_dir}", file=sys.stderr)
        return 2

    c = Client(args.url, args.workspace, args.api_key, args.dry_run)
    print(f"Installing preset '{args.preset}' into workspace '{args.workspace}' "
          f"({'dry-run' if args.dry_run else args.url})\n")

    def fail(label, r):
        if isinstance(r, dict) and "_http" in r:
            print(f"  ✗ {label}: HTTP {r['_http']} {r['_body']}")
            return True
        return False

    # 1) ontology
    onto = load(preset_dir, "ontology.json")
    if onto is not None:
        r = c.post("/ontology", {"ontology": strip_comment(onto)})
        if not fail("ontology", r):
            print(f"  ✓ ontology  v{r.get('version','?')}  "
                  f"({len(onto.get('object_types', []))} types)" if not args.dry_run else "  · ontology (dry-run)")

    # 2) rules
    rules = load(preset_dir, "rules.json")
    if rules is not None:
        r = c.post("/rules", strip_comment(rules))
        if not fail("rules", r):
            print(f"  ✓ rules     v{r.get('version','?')}  "
                  f"({len(r.get('rules', []))} rules)" if not args.dry_run else "  · rules (dry-run)")

    # 3) actions
    actions = load(preset_dir, "actions.json")
    if actions is not None:
        r = c.post("/actions", {"catalog": strip_comment(actions)})
        if not fail("actions", r):
            print(f"  ✓ actions   v{r.get('version','?')}  "
                  f"({len(r.get('actions', []))} actions)" if not args.dry_run else "  · actions (dry-run)")

    # 4) rbac — opt-in access policy (absent = permissive)
    rbac = load(preset_dir, "rbac.json")
    if rbac is not None:
        r = c.post("/rbac", {"policy": strip_comment(rbac)})
        if not fail("rbac", r):
            print(f"  ✓ rbac      v{r.get('version','?')}  "
                  f"({len(r.get('roles', {}))} roles)" if not args.dry_run else "  · rbac (dry-run)")

    # 5) seed — entities first, then relations (relations need both endpoints to exist)
    seed = load(preset_dir, "seed.json")
    if seed is not None:
        ents = seed.get("entities", [])
        rels = seed.get("relations", [])
        ok = skipped = 0
        for e in ents:
            r = c.post("/graph/entity/create", {"entity_name": e["entity_name"], "entity_data": e["entity_data"]})
            if isinstance(r, dict) and "_http" in r:
                skipped += 1
            else:
                ok += 1
        rok = rskip = 0
        for rel in rels:
            r = c.post("/graph/relation/create", {"source_entity": rel["source_entity"],
                                                  "target_entity": rel["target_entity"],
                                                  "relation_data": rel["relation_data"]})
            if isinstance(r, dict) and "_http" in r:
                rskip += 1
            else:
                rok += 1
        if args.dry_run:
            print(f"  · seed (dry-run): {len(ents)} entities, {len(rels)} relations")
        else:
            print(f"  ✓ seed      {ok} entities created ({skipped} skipped), "
                  f"{rok} relations created ({rskip} skipped)")

    print(f"\nDone. Point agents at workspace '{args.workspace}'.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
