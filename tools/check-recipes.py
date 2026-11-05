#!/usr/bin/env python3
"""Golden check for the recipes in models/library.yaml.

A vLLM serve command is easy to break by "tidying" it; every flag can be load-bearing.
A recipe is data that a generic launcher turns into that command, so this script renders
each recipe the way SparkVault will and compares it with the command in your frozen launch
script, token for token. If they differ it prints the first differing token and exits 1.

    python3 tools/check-recipes.py                       # the shipped examples
    python3 tools/check-recipes.py --catalogue DIR       # your own library.yaml
    python3 tools/check-recipes.py --catalogue DIR --root DIR   # where `golden.script` paths start

`golden.script` paths in library.yaml are resolved against --root. With the shipped examples
that is the repository root; for your own catalogue it is wherever your launch scripts live.

Also checks: no duplicate flags, a recipe's served name matches `served_name`,
and every locked flag carries a reason.
"""
import re
import shlex
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from app.recipe import render  # the same function the app uses


def tokens(text):
    # shlex understands the single/double quoting both scripts use. "$VLLM_API_KEY" stays
    # literal because shlex does no expansion.
    return shlex.split(text)


def golden_text(root, script, var, subst):
    src = (root / script).read_text()
    if var == "SERVE_CMD":      # SERVE_CMD='...'  with '"'"' standing for a literal '
        m = re.search(r"^SERVE_CMD='(.*?)'\n\n", src, re.S | re.M)
        body = m.group(1).replace("'\"'\"'", "'") if m else None
    else:                        # COMMON="..."  (first assignment only)
        m = re.search(r'^\s*COMMON="(vllm serve .*?)"\n', src, re.S | re.M)
        body = m.group(1) if m else None
    if body is None:
        raise SystemExit(f"cannot find {var} in {script}")
    body = body.replace("\\\n", " ")
    for k, v in (subst or {}).items():
        body = body.replace(k, v)
    return body


def main():
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--catalogue", type=Path, default=REPO / "examples", help="folder holding library.yaml")
    ap.add_argument("--root", type=Path, help="base for golden.script paths (default: the repo root for the examples, else the current directory)")
    a = ap.parse_args()
    root = a.root or (REPO if a.catalogue.resolve() == (REPO / "examples") else Path.cwd())
    models = yaml.safe_load((a.catalogue / "library.yaml").read_text())["models"]
    bad = 0
    for m in models:
        r = m.get("recipe")
        if not r:
            continue
        flags = [f["flag"] for f in r["serve"]]
        dup = {f for f in flags if flags.count(f) > 1}
        g = r["golden"]
        want = tokens(golden_text(root, g["script"], g["var"], g.get("subst")))
        got = tokens(render(r, m["hf_repo"], g.get("include_managed", True)))
        problems = []
        if dup:
            problems.append(f"duplicate flags: {sorted(dup)}")
        for f in r["serve"]:
            if "lock" in f and not f["lock"]:
                problems.append(f"{f['flag']}: lock has no reason")
        sn = next((f.get("value") for f in r["serve"] if f["flag"] == "--served-model-name"), None)
        if sn and sn != m.get("served_name"):
            problems.append(f"served name {sn!r} != served_name {m.get('served_name')!r}")
        if want != got:
            i = next((i for i, (a, b) in enumerate(zip(want, got)) if a != b), min(len(want), len(got)))
            problems.append(f"rendered command differs from {g['script']} at token {i}: "
                            f"script {want[i:i+3]} vs recipe {got[i:i+3]} "
                            f"(lengths {len(want)} vs {len(got)})")
        if problems:
            bad += 1
            print(f"FAIL {m['id']}")
            for p in problems:
                print("   ", p)
        else:
            print(f"ok   {m['id']}: {len(got)} tokens identical to {g['script']} ({g['var']})")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
