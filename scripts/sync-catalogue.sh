#!/usr/bin/env bash
# Copy your catalogue (library.yaml + results/) to the machine or share where SparkVault runs.
# You choose both ends; there is no default destination, so nothing is ever copied by accident.
#
#   scripts/sync-catalogue.sh --src DIR --dest DEST            # dry run: shows what would change
#   scripts/sync-catalogue.sh --src DIR --dest DEST --apply    # copy
#   scripts/sync-catalogue.sh --src DIR --dest DEST --apply --init   # first copy into an empty DEST
#
# DEST is anything rsync accepts: a mounted share (/mnt/share/sparkvault/catalogue) or user@host:/path.
# Environment fallbacks: SPARKVAULT_SRC, SPARKVAULT_DEST.
#
# Safety: only library.yaml and results/ are synced. Files in DEST/results/ that are not in SRC are
# removed (so a record you deleted disappears), which is why a DEST that does not already hold a
# library.yaml is refused unless you pass --init. A .env, an inbox/ or any other file in DEST is
# never touched. The container mounts the catalogue read-only, so no rebuild or restart is needed.
set -euo pipefail

src=${SPARKVAULT_SRC:-}; dest=${SPARKVAULT_DEST:-}; apply=false; init=false
while [ $# -gt 0 ]; do
  case "$1" in
    --src) src=${2:?--src needs a folder}; shift 2 ;;
    --dest) dest=${2:?--dest needs a destination}; shift 2 ;;
    --apply) apply=true; shift ;;
    --init) init=true; shift ;;
    -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1 (see --help)" >&2; exit 2 ;;
  esac
done
[ -n "$src" ] && [ -n "$dest" ] || { echo "Both --src and --dest are required (or SPARKVAULT_SRC / SPARKVAULT_DEST)." >&2; exit 2; }
command -v rsync >/dev/null || { echo "rsync not found" >&2; exit 1; }
[ -f "$src/library.yaml" ] || { echo "$src has no library.yaml; refusing." >&2; exit 1; }
# Validate before copying, if PyYAML and jsonschema are installed (skipped with a note otherwise).
here=$(cd "$(dirname "$0")/.." && pwd)
python3 - "$src" "$here/app/results.schema.json" <<'PY' || { echo "The catalogue failed validation; nothing was copied." >&2; exit 1; }
import sys, json, pathlib
try:
    import yaml, jsonschema
except ImportError:
    print("(PyYAML/jsonschema not installed: skipping validation)"); sys.exit(0)
src, schema = pathlib.Path(sys.argv[1]), json.load(open(sys.argv[2]))
ids = {m["id"] for m in yaml.safe_load((src / "library.yaml").read_text())["models"]}
bad = 0
for f in sorted((src / "results").glob("*.json")):
    try:
        r = json.loads(f.read_text()); jsonschema.validate(r, schema)
        assert r["id"] == f.stem, "id does not match file name"
        assert r["model"] in ids, f"unknown model {r['model']!r}"
    except Exception as e:
        bad += 1; print(f"INVALID {f.name}: {str(e).splitlines()[0]}")
sys.exit(1 if bad else 0)
PY
if ! $init; then
  case "$dest" in
    *:*) rsync -q --list-only "${dest%/}/library.yaml" >/dev/null 2>&1 || { echo "$dest does not hold a library.yaml yet; use --init for a first copy." >&2; exit 1; } ;;
    *)   [ -f "$dest/library.yaml" ] || { echo "$dest does not hold a library.yaml yet; use --init for a first copy." >&2; exit 1; } ;;
  esac
fi
flags=(-a --itemize-changes); $apply || flags+=(--dry-run)
echo "== $($apply && echo COPYING || echo DRY RUN, would copy) $src -> $dest =="
rsync "${flags[@]}" "$src/library.yaml" "${dest%/}/"
rsync "${flags[@]}" --delete "$src/results/" "${dest%/}/results/"
$apply && echo "Done." || echo "Nothing was changed. Add --apply to copy."
