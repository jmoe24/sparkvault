"""Reads library.yaml and results/*.json from the catalogue folder (read-only).

Git is the source of truth. A malformed catalogue or result file never takes the app down:
it is skipped and reported in `problems`, which the UI shows.
"""
import json
from dataclasses import dataclass, field
from pathlib import Path

import jsonschema
import yaml

SCHEMA_PATH = Path(__file__).parent / "results.schema.json"   # ships with the app, not with your data


@dataclass
class Library:
    models: list = field(default_factory=list)
    results: list = field(default_factory=list)      # newest first
    problems: list = field(default_factory=list)
    head: str | None = None                          # commit of the clone
    pending: int = 0                                 # uploaded records in the inbox, not yet committed


@dataclass
class Summary:
    model: dict
    runs: list
    single: float | None
    conc: float | None
    ttft: float | None
    judge: float | None
    replay: str | None
    suspect: int


def git_head(repo: Path) -> str | None:
    """Commit the data came from: REPO_COMMIT (bundle) or .git (clone); no git binary needed."""
    try:                                    # a bundled copy carries REPO_COMMIT instead of .git
        first = (repo / "REPO_COMMIT").read_text().split()
        if first:
            return first[0]
    except OSError:
        pass
    g = repo / ".git"
    try:
        head = (g / "HEAD").read_text().strip()
        if not head.startswith("ref: "):
            return head
        ref = head[5:]
        if (g / ref).is_file():
            return (g / ref).read_text().strip()
        for line in (g / "packed-refs").read_text().splitlines():
            if line.endswith(" " + ref):
                return line.split()[0]
    except OSError:
        pass
    return None


class Loader:
    """Reloads only when a source file changed (cheap on NFS-backed clones)."""

    def __init__(self, catalogue_dir: Path, inbox_dir: Path | None = None):
        self.repo = catalogue_dir
        self.inbox = inbox_dir
        self._sig = None
        self._lib = Library()

    def _signature(self):
        paths = [self.repo / "library.yaml",
                 self.repo / ".git" / "HEAD", self.repo / "REPO_COMMIT"]
        paths += sorted((self.repo / "results").glob("*.json"))
        if self.inbox:
            paths += sorted(self.inbox.glob("*.json"))
        sig = []
        for p in paths:
            try:
                sig.append((str(p), p.stat().st_mtime_ns))
            except OSError:
                sig.append((str(p), None))
        return tuple(sig)

    def get(self) -> Library:
        sig = self._signature()
        if sig != self._sig:
            self._lib, self._sig = self._load(), sig
        return self._lib

    def _load(self) -> Library:
        lib = Library(head=git_head(self.repo))
        try:
            data = yaml.safe_load((self.repo / "library.yaml").read_text())
            lib.models = list(data["models"])
        except (OSError, yaml.YAMLError, KeyError, TypeError) as e:
            lib.problems.append(f"library.yaml: {type(e).__name__}: {e}")
            return lib
        ids = [m.get("id") for m in lib.models]
        for dup in sorted({i for i in ids if ids.count(i) > 1}):
            lib.problems.append(f"library.yaml: duplicate id {dup}")
        schema = None
        try:
            schema = json.loads(SCHEMA_PATH.read_text())
            jsonschema.Draft202012Validator.check_schema(schema)
        except (OSError, ValueError, jsonschema.SchemaError) as e:
            lib.problems.append(f"results.schema.json unusable, results not shown: {e}")
            return lib
        known = set(ids)
        for p in sorted((self.repo / "results").glob("*.json")):
            try:
                r = json.loads(p.read_text())
                jsonschema.validate(r, schema)
                if r["id"] != p.stem:
                    raise ValueError(f"id {r['id']!r} does not match file name")
                if r["model"] not in known:
                    raise ValueError(f"unknown model {r['model']!r}")
            except (OSError, ValueError, jsonschema.ValidationError) as e:
                lib.problems.append(f"results/{p.name}: {str(e).splitlines()[0]}")
                continue
            lib.results.append(r)
        if self.inbox:                      # uploaded, not yet committed: shown, marked pending
            have = {r["id"] for r in lib.results}
            for p in sorted(self.inbox.glob("*.json")):
                try:
                    r = json.loads(p.read_text())
                    jsonschema.validate(r, schema)
                    if r["id"] != p.stem:
                        raise ValueError(f"id {r['id']!r} does not match file name")
                    if r["model"] not in known:
                        raise ValueError(f"unknown model {r['model']!r}")
                except (OSError, ValueError, jsonschema.ValidationError) as e:
                    lib.problems.append(f"inbox/{p.name}: {str(e).splitlines()[0]}")
                    continue
                if r["id"] in have:         # the committed copy wins
                    continue
                r["_pending"] = True
                lib.results.append(r)
                lib.pending += 1
        lib.results.sort(key=lambda r: (r["date"], r["id"]), reverse=True)
        return lib


def summarize(model: dict, results: list) -> Summary:
    """Headline numbers come from valid runs only; single-node runs are preferred."""
    runs = [r for r in results if r["model"] == model["id"]]
    ok = [r for r in runs if r["validity"] == "ok"]

    def pick(pred):
        for pool in ([r for r in ok if r["topology"] == "single"], ok):
            hit = next((r for r in pool if pred(r)), None)
            if hit:
                return hit

    b = pick(lambda r: r.get("bench", {}).get("single_stream_tok_s") is not None)
    c = pick(lambda r: r.get("bench", {}).get("concurrent", {}).get("output_tok_s") is not None)
    t = pick(lambda r: r.get("bench", {}).get("concurrent", {}).get("mean_ttft_ms") is not None)
    e = pick(lambda r: r.get("eval", {}).get("overall_score") is not None)
    p = pick(lambda r: "replay" in r)
    return Summary(
        model=model, runs=runs,
        single=b["bench"]["single_stream_tok_s"] if b else None,
        conc=c["bench"]["concurrent"]["output_tok_s"] if c else None,
        ttft=t["bench"]["concurrent"]["mean_ttft_ms"] if t else None,
        judge=e["eval"]["overall_score"] if e else None,
        replay=f"{p['replay']['tool_call_pass']}/{p['replay']['tool_call_total']}" if p else None,
        suspect=sum(1 for r in runs if r["validity"] != "ok"),
    )


def flat(r: dict) -> dict:
    """One result as a flat row for templates (missing blocks become None)."""
    b = r.get("bench", {})
    c = b.get("concurrent", {})
    rp = r.get("replay")
    return dict(
        date=r["date"], node=r["node"], label=r.get("label", ""), validity=r["validity"], note=r.get("validity_note", ""),
        topology=r["topology"], single=b.get("single_stream_tok_s"), conc=c.get("output_tok_s"), ttft=c.get("mean_ttft_ms"),
        tpot=c.get("mean_tpot_ms"), acc=b.get("spec_decode_acceptance_pct"), judge=r.get("eval", {}).get("overall_score"),
        replay=f"{rp['tool_call_pass']}/{rp['tool_call_total']}" if rp else None,
        overrides=r.get("config", {}).get("overrides", []), source=r["source"], pending=bool(r.get("_pending")))
