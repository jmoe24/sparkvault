#!/usr/bin/env python3
"""Check the result records SparkVault has parked in its inbox and copy the good ones into your
catalogue's results/ folder. The inbox is the folder you mounted at /inbox (SPARKVAULT_INBOX_HOST_DIR);
any other folder of .json records works too.

    python3 tools/import-results.py --catalogue DIR INBOX           # dry run: shows what it would do
    python3 tools/import-results.py --catalogue DIR INBOX --apply   # copy the valid ones into DIR/results/

A record is imported only if it validates against app/results.schema.json, its `model` is an id in
DIR/library.yaml, and its `id` equals its file name. An id that already exists in results/ is never
overwritten (use --replace to allow it). Nothing is committed; review and commit yourself.
"""
import argparse
import json
import shutil
import sys
from pathlib import Path

import jsonschema
import yaml

REPO = Path(__file__).resolve().parents[1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("inbox", type=Path, help="folder of uploaded .json records (no default: it depends on your setup)")
    ap.add_argument("--catalogue", type=Path, required=True, help="your catalogue folder (library.yaml + results/)")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--replace", action="store_true", help="overwrite a record whose id already exists in results/")
    a = ap.parse_args()

    schema = json.loads((REPO / "app" / "results.schema.json").read_text())
    models = {m["id"] for m in yaml.safe_load((a.catalogue / "library.yaml").read_text())["models"]}
    files = sorted(a.inbox.glob("*.json"))
    if not files:
        sys.exit(f"no .json files in {a.inbox}")

    ok = bad = 0
    for f in files:
        try:
            d = json.loads(f.read_text())
            jsonschema.validate(d, schema)
            problems = []
            if d["model"] not in models:
                problems.append(f"model {d['model']!r} is not in library.yaml")
            if d["id"] != f.stem:
                problems.append(f"id {d['id']!r} does not match the file name")
            dest = a.catalogue / "results" / f.name
            if dest.exists() and not a.replace:
                problems.append("results/ already has this id (use --replace to overwrite)")
        except Exception as e:
            problems = [str(e).splitlines()[0]]
            dest = None
        if problems:
            bad += 1
            print(f"SKIP {f.name}: " + "; ".join(problems))
            continue
        ok += 1
        b = d.get("bench", {})
        print(f"{'COPY' if a.apply else 'would copy'} {f.name}: {d['model']} on {d['node']}, validity={d['validity']}, "
              f"single={b.get('single_stream_tok_s', '-')} tok/s, concurrent={b.get('concurrent', {}).get('output_tok_s', '-')} tok/s")
        if a.apply:
            shutil.copyfile(f, dest)
    print(f"{ok} valid, {bad} skipped" + ("" if a.apply else " (dry run; add --apply)"))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
