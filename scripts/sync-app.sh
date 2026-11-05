#!/usr/bin/env bash
# Copy this repo's COMMITTED files to the server folder you build from. You choose the destination;
# there is no default, so nothing is ever copied by accident.
#
#   scripts/sync-app.sh --dest DEST            # dry run: shows what would change
#   scripts/sync-app.sh --dest DEST --apply    # copy
#   scripts/sync-app.sh --dest DEST --apply --init    # first copy into an empty folder
#
# DEST is anything rsync accepts: a mounted share (/mnt/nas/docker/sparkvault/app) or user@host:/path.
# Environment fallback: SPARKVAULT_APP_DEST.
#
# Only what is committed (git archive HEAD) is copied, so no .git, no .env, nothing from your working tree.
# Files in DEST that are no longer in the repo are removed, which is why a DEST that does not already hold a
# SparkVault copy (Dockerfile and app/) is refused unless you pass --init. Keep your edited compose file, secrets
# file, catalogue and inbox OUTSIDE DEST (next to it), or they would be deleted. Rebuild the container afterwards.
set -euo pipefail

dest=${SPARKVAULT_APP_DEST:-}; apply=false; init=false
while [ $# -gt 0 ]; do
  case "$1" in
    --dest) dest=${2:?--dest needs a destination}; shift 2 ;;
    --apply) apply=true; shift ;;
    --init) init=true; shift ;;
    -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1 (see --help)" >&2; exit 2 ;;
  esac
done
[ -n "$dest" ] || { echo "--dest is required (or SPARKVAULT_APP_DEST)." >&2; exit 2; }
command -v rsync >/dev/null || { echo "rsync not found" >&2; exit 1; }
cd "$(dirname "$0")/.."
git rev-parse --git-dir >/dev/null 2>&1 || { echo "run this from a git clone of the repo" >&2; exit 1; }
[ -z "$(git status --porcelain)" ] || echo "NOTE: uncommitted changes are NOT copied (only HEAD $(git rev-parse --short HEAD))." >&2

if ! $init; then
  case "$dest" in
    *:*) rsync -q --list-only "${dest%/}/Dockerfile" >/dev/null 2>&1 || { echo "$dest does not hold a SparkVault copy yet; use --init for a first copy." >&2; exit 1; } ;;
    *)   { [ -f "$dest/Dockerfile" ] && [ -d "$dest/app" ]; } || { echo "$dest does not hold a SparkVault copy yet; use --init for a first copy." >&2; exit 1; } ;;
  esac
fi

tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
git archive HEAD | tar -x -C "$tmp"
flags=(-a --itemize-changes --delete); $apply || flags+=(--dry-run)
echo "== $($apply && echo COPYING || echo DRY RUN, would copy) HEAD $(git rev-parse --short HEAD) -> $dest =="
rsync "${flags[@]}" "$tmp/" "${dest%/}/"
$apply && echo "Done. Rebuild the project (a plain restart reuses the old image)." || echo "Nothing was changed. Add --apply to copy."
