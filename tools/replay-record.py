#!/usr/bin/env python3
"""Turn the output of replay-dump.py into a SparkVault result record.

replay-dump.py (localai repo, scripts/candidates/) replays a captured agent request at a vLLM server and
ends with a line `REPLAY_RESULT {"tool_call_pass": N, "tool_call_total": M}`. This reads that output
(a file, or stdin), checks the record against the app's schema, and writes <date>-<node>-<label>.json.

    sudo -u hermes cat <request_dump.json> | docker exec -i vllm-test \\
        env REPLAY_MODEL=llama-3.3-70b python3 -u /tmp/replay-dump.py 8001 10 | tee replay.out
    python3 tools/replay-record.py replay.out --model llama-3.3-70b --node gx101 \\
        --source "netops request dump of 2026-11-02, 10 per variant" --out ~/sparkvault-inbox

then `tools/submit-result.sh <file>` to send it. Never put prompt text or a key in --source.
"""
import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCHEMA = HERE.parent / "app" / "results.schema.json"


def parse(text: str) -> dict:
    found = re.findall(r"^REPLAY_RESULT (\{.*\})\s*$", text, re.M)
    if not found:
        sys.exit("no REPLAY_RESULT line in the input (did replay-dump.py finish?)")
    block = json.loads(found[-1])
    if set(block) != {"tool_call_pass", "tool_call_total"}:
        sys.exit(f"unexpected replay block: {block}")
    return block


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", nargs="?", default="-", help="replay-dump.py output (default stdin)")
    ap.add_argument("--model", required=True, help="library.yaml model id")
    ap.add_argument("--node", required=True, help="gx101, gx102, or gx101+gx102")
    ap.add_argument("--topology", default="single", choices=["single", "tp2"])
    ap.add_argument("--label", default="replay")
    ap.add_argument("--date", default=date.today().isoformat())
    ap.add_argument("--source", required=True, help="which dump and how many requests per variant")
    ap.add_argument("--launch-script", help="e.g. scripts/candidates/llama-3.3-70b.sh")
    ap.add_argument("--out", default=".", help="directory for the record")
    a = ap.parse_args()

    text = sys.stdin.read() if a.input == "-" else Path(a.input).read_text()
    block = parse(text)
    if block["tool_call_pass"] > block["tool_call_total"]:
        sys.exit("pass exceeds total")

    rec = {
        "schema_version": 1,
        "id": f"{a.date}-{a.node}-{a.model}-{a.label}",
        "date": a.date,
        "model": a.model,
        "node": a.node,
        "topology": a.topology,
        "label": a.label,
        "validity": "ok",
        "replay": block,
        "source": a.source,
    }
    if a.launch_script:
        rec["config"] = {"launch_script": a.launch_script}

    try:
        import jsonschema  # optional; the server validates again on upload
        jsonschema.validate(rec, json.loads(SCHEMA.read_text()))
    except ImportError:
        print("jsonschema not installed; skipping local validation", file=sys.stderr)

    out = Path(a.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{rec['id']}.json"
    if path.exists():
        sys.exit(f"{path} exists; pick another --label")
    path.write_text(json.dumps(rec, indent=2) + "\n")
    print(f"{path}: {block['tool_call_pass']}/{block['tool_call_total']}")


if __name__ == "__main__":
    main()
