#!/usr/bin/env python3
"""Render a catalogue's results/*.json into one Markdown report: a per-model comparison table plus every run.

    python3 tools/results-report.py --catalogue DIR               # prints to stdout
    python3 tools/results-report.py --catalogue DIR --out FILE    # or writes a file (any folder, e.g. a notes vault)

Read-only on the catalogue. The JSON files stay the source of truth (schema: app/results.schema.json);
the report is a view you can regenerate, so do not edit it by hand. A run marked validity=suspect is
listed but never used as a model's headline number.
"""
import argparse
import datetime
import glob
import json
import os
import sys



def load(results_dir):
    runs, bad = [], []
    for f in sorted(glob.glob(os.path.join(results_dir, "*.json"))):
        try:
            d = json.load(open(f))
            d["_file"] = os.path.basename(f)
            runs.append(d)
        except Exception as e:  # a malformed file is listed, never fatal
            bad.append((os.path.basename(f), str(e)))
    return runs, bad


def g(d, *path):
    for k in path:
        if not isinstance(d, dict) or k not in d:
            return None
        d = d[k]
    return d


def fmt(v, nd=1):
    if v is None:
        return "-"
    return f"{v:.{nd}f}" if isinstance(v, float) else str(v)


def where(r):
    return f"{r['node']} {r['topology']}"


def headline(runs):
    """Best bench run per model: validity ok, with a single-stream number, highest concurrent output."""
    best = {}
    for r in runs:
        if r.get("validity") != "ok" or g(r, "bench", "single_stream_tok_s") is None:
            continue
        cur = best.get(r["model"])
        if cur is None or (g(r, "bench", "concurrent", "output_tok_s") or 0) > (g(cur, "bench", "concurrent", "output_tok_s") or 0):
            best[r["model"]] = r
    return best


def catalogue_ids(catalogue):
    """Model ids in models/library.yaml, so a model with no results still gets a row."""
    try:
        import yaml
        lib = yaml.safe_load(open(os.path.join(catalogue, "library.yaml")))
        return [m["id"] for m in lib.get("models", lib) if isinstance(m, dict) and "id" in m]
    except Exception:
        return []


def render(runs, bad, catalogue):
    models = sorted({r["model"] for r in runs} | set(catalogue_ids(catalogue)))
    best = headline(runs)
    out = ["# Model benchmarks", "",
           f"> Generated {datetime.date.today()} by `tools/results-report.py` from `results/*.json`. Do not edit; re-run it.", "",
           "## Headline per model", "",
           "Best valid bench run per model (highest concurrent output). A dash means that was never measured.", "",
           "| Model | Run | Single tok/s | Concurrent out tok/s | TTFT ms | TPOT ms | Tool calls | Eval |",
           "|---|---|---|---|---|---|---|---|"]
    for m in models:
        mr = [r for r in runs if r["model"] == m]
        ev = next((g(r, "eval", "overall_score") for r in mr if g(r, "eval", "overall_score") is not None), None)
        rp = next((r for r in mr if g(r, "replay", "tool_call_total")), None)
        tool = f"{rp['replay']['tool_call_pass']}/{rp['replay']['tool_call_total']}" if rp else "-"
        b = best.get(m)
        if b:
            out.append(f"| {m} | {where(b)}, {b.get('label', '')} | {fmt(g(b, 'bench', 'single_stream_tok_s'))} | "
                       f"{fmt(g(b, 'bench', 'concurrent', 'output_tok_s'))} | {fmt(g(b, 'bench', 'concurrent', 'mean_ttft_ms'), 0)} | "
                       f"{fmt(g(b, 'bench', 'concurrent', 'mean_tpot_ms'))} | {tool} | {fmt(ev, 2)} |")
        else:
            out.append(f"| {m} | no valid bench run | - | - | - | - | {tool} | {fmt(ev, 2)} |")
    out += ["", "## Every run", ""]
    for m in models:
        if not any(r["model"] == m for r in runs):
            out += [f"### {m}", "", "No results yet.", ""]
            continue
        out += [f"### {m}", "", "| Date | Where | Label | Validity | Single | Conc out | TTFT | Tools | Eval | Clock MHz | File |",
                "|---|---|---|---|---|---|---|---|---|---|---|"]
        for r in sorted((x for x in runs if x["model"] == m), key=lambda x: x["date"]):
            rp = r.get("replay")
            tool = f"{rp['tool_call_pass']}/{rp['tool_call_total']}" if rp else "-"
            val = r["validity"] + (f" ({r['validity_note']})" if r.get("validity_note") else "")
            out.append(f"| {r['date']} | {where(r)} | {r.get('label', '')} | {val} | {fmt(g(r, 'bench', 'single_stream_tok_s'))} | "
                       f"{fmt(g(r, 'bench', 'concurrent', 'output_tok_s'))} | {fmt(g(r, 'bench', 'concurrent', 'mean_ttft_ms'), 0)} | {tool} | "
                       f"{fmt(g(r, 'eval', 'overall_score'), 2)} | {fmt(g(r, 'config', 'gpu_sm_clock_mhz'))} | `{r['_file']}` |")
        out.append("")
    if bad:
        out += ["## Files that could not be read", ""] + [f"- `{f}`: {e}" for f, e in bad] + [""]
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--catalogue", required=True, help="folder holding library.yaml and results/")
    ap.add_argument("--out", help="write here instead of stdout")
    a = ap.parse_args()
    runs, bad = load(os.path.join(a.catalogue, "results"))
    if not runs:
        sys.exit(f"no readable results in {a.catalogue}/results")
    text = render(runs, bad, a.catalogue)
    if not a.out:
        print(text)
        return
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    open(a.out, "w").write(text + "\n")
    print(f"wrote {a.out} ({len(runs)} runs, {len({r['model'] for r in runs})} models)")


if __name__ == "__main__":
    main()
