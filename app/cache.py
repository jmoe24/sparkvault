"""Read-only scan of the Hugging Face cache on the cache host (hub/models--<org>--<name>).

Detects the failure signatures seen in practice:
  * dangling symlinks in snapshots/ (a plain `rsync -a` of the newer shared-blob layout copies
    links, not files, and `du -shL` then prints "cannot access"),
  * *.incomplete files left by an interrupted download.
Nothing here ever writes.
"""
import os
import stat
import time
from dataclasses import dataclass, field
from pathlib import Path


def dirname(repo: str) -> str:
    return "models--" + repo.replace("/", "--")


def repo_of(dirname_: str) -> str:
    return dirname_.removeprefix("models--").replace("--", "/", 1)


@dataclass
class CacheInfo:
    repo: str
    present: bool = False
    sha: str | None = None            # refs/main
    size: int = 0                     # bytes, shared blobs counted once
    snapshots: list = field(default_factory=list)
    dangling: int = 0
    incomplete: int = 0
    error: str | None = None          # set when the cache cannot be read at all (e.g. permission denied)
    layout: str | None = None         # where the largest file (the weights) lives: "shared" (hub/blobs/<xx>/<hash>) or "per-model"

    @property
    def problems(self) -> list:
        out = []
        if self.dangling:
            out.append(f"{self.dangling} dangling link(s) in snapshots/ (a failed copy; see model-library.md step 4)")
        if self.incomplete:
            out.append(f"{self.incomplete} .incomplete file(s) (interrupted download)")
        return out


def scan_model(hf_dir: Path, repo: str) -> CacheInfo:
    info = CacheInfo(repo=repo)
    root = hf_dir / "hub" / dirname(repo)
    # Explicit stat/listdir, not Path.is_dir(): on this Python is_dir() turns "permission denied"
    # into False, which would show every model as "absent" when the real problem is the mount.
    hub = hf_dir / "hub"
    try:
        os.stat(hub)
    except FileNotFoundError:
        info.error = f"{hub} not found (is the Hugging Face folder mounted into the container?)"
        return info
    except OSError as e:
        info.error = f"{e.strerror.lower()} reading {hub}"
        return info
    try:
        st = os.stat(root)
        if not stat.S_ISDIR(st.st_mode):
            return info
        os.listdir(root)
    except FileNotFoundError:
        return info                                 # genuinely not in the cache
    except OSError as e:
        info.error = f"{e.strerror.lower()} reading {root}"
        return info
    info.present = True
    try:
        info.sha = (root / "refs" / "main").read_text().strip() or None
    except OSError:
        pass
    snaps = root / "snapshots"
    try:
        info.snapshots = sorted(p.name for p in snaps.iterdir() if p.is_dir())
    except OSError:
        pass
    # size and dangling links, from the snapshot refs/main points at (else all of them)
    wanted = [info.sha] if info.sha in info.snapshots else info.snapshots
    seen = set()
    biggest = (-1, None)
    shared_store = Path(os.path.realpath(hf_dir / "hub" / "blobs"))
    for sha in wanted:
        for dirpath, _, files in os.walk(snaps / sha):
            for name in files:
                f = os.path.join(dirpath, name)
                try:
                    st = os.stat(f)                       # follows the link
                except OSError:
                    info.dangling += 1
                    continue
                if st.st_size >= biggest[0]:       # where the weights live decides the layout
                    real = Path(os.path.realpath(f))
                    biggest = (st.st_size, "shared" if real.is_relative_to(shared_store) else "per-model")
                if (st.st_dev, st.st_ino) not in seen:
                    seen.add((st.st_dev, st.st_ino))
                    info.size += st.st_size
    info.layout = biggest[1]
    try:
        info.incomplete = sum(1 for p in (root / "blobs").iterdir() if p.name.endswith(".incomplete"))
    except OSError:
        pass
    return info


def list_repos(hf_dir: Path) -> list:
    try:
        return sorted(repo_of(p.name) for p in (hf_dir / "hub").iterdir()
                      if p.name.startswith("models--") and p.is_dir())
    except OSError:
        return []


def shared_incomplete(hf_dir: Path) -> int:
    """Stale *.incomplete in the shared blob store (hub/blobs/<xx>/...)."""
    n = 0
    try:
        for sub in (hf_dir / "hub" / "blobs").iterdir():
            if sub.name.endswith(".incomplete"):
                n += 1
            elif sub.is_dir():
                n += sum(1 for p in sub.iterdir() if p.name.endswith(".incomplete"))
    except OSError:
        pass
    return n


def drift(model: dict, info: CacheInfo) -> tuple:
    """(state, message) comparing the catalogue with what is in the cache."""
    pinned, on_nas = model.get("revision"), model.get("on_nas")
    if info.error:
        return "unreadable", info.error + " (a mount or permission problem, not a missing model)"
    if not info.present:
        if on_nas:
            return "missing", "Catalogue says it is in the cache, but it is not"
        return "absent", "Not in the model cache" + ("" if on_nas is False else " (catalogue does not say)")
    if info.problems:
        return "broken", "; ".join(info.problems)
    if info.sha is None:
        return "norev", "In the cache but refs/main is missing, so the revision is unknown"
    if pinned is None:
        return "unpinned", f"Cache has {info.sha[:12]}; the catalogue pins no revision"
    if pinned != info.sha:
        return "drift", f"Cache refs/main is {info.sha[:12]}, catalogue pins {pinned[:12]}"
    if on_nas is not True:
        return "stale", f"Present and matches, but the catalogue says on_nas: {on_nas}"
    return "ok", f"Matches the pinned revision {pinned[:12]}"


class Cached:
    """Tiny TTL cache so page loads do not re-stat NFS every time."""
    def __init__(self, ttl=30, clock=time.monotonic):
        self.ttl, self.clock, self.store = ttl, clock, {}

    def get(self, key, fn):
        hit = self.store.get(key)
        if hit and self.clock() - hit[0] < self.ttl:
            return hit[1]
        val = fn()
        self.store[key] = (self.clock(), val)
        return val
