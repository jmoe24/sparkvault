#!/usr/bin/env bash
# Create .env for docker-compose.yml: asks for the host-specific paths and generates the secrets.
# Run from a clone of this repo (it uses app/hashpw.py). Refuses to overwrite an existing .env.
#
#   scripts/init-env.sh [output-file]        default: ./.env
#
# Every question can be pre-answered with the environment variable named in brackets, for
# unattended use. The password is read without echo and only its scrypt hash is written.
set -euo pipefail
cd "$(dirname "$0")/.."
out=${1:-.env}
[ ! -e "$out" ] || { echo "$out already exists; refusing to overwrite it." >&2; exit 1; }
command -v openssl >/dev/null || { echo "openssl not found" >&2; exit 1; }
command -v python3 >/dev/null || { echo "python3 not found" >&2; exit 1; }

ask() {   # ask VAR "question" [default]
  local var=$1 q=$2 def=${3:-} val
  if [ -n "${!var:-}" ]; then return; fi
  read -r -p "$q${def:+ [$def]}: " val
  printf -v "$var" '%s' "${val:-$def}"
  [ -n "${!var}" ] || { echo "$var is required." >&2; exit 1; }
}

ask SPARKVAULT_CATALOGUE_HOST_DIR "Folder with your library.yaml and results/ (absolute path)  [SPARKVAULT_CATALOGUE_HOST_DIR]"
ask SPARKVAULT_HF_HOST_DIR "Hugging Face cache root, the folder that contains hub/ (absolute path)  [SPARKVAULT_HF_HOST_DIR]"
ask SPARKVAULT_NODES "Node names, comma separated  [SPARKVAULT_NODES]" "node-a,node-b"
ask SPARKVAULT_PORT "Host port  [SPARKVAULT_PORT]" "8080"
for d in "$SPARKVAULT_CATALOGUE_HOST_DIR" "$SPARKVAULT_HF_HOST_DIR"; do
  case "$d" in /*) ;; *) echo "$d is not an absolute path." >&2; exit 1 ;; esac
done

echo "Choose the admin password (12+ characters) for the web UI:"
hash=$(python3 -m app.hashpw)
secret=$(openssl rand -hex 32)
token=""
if [ "${SPARKVAULT_ENABLE_UPLOAD:-}" = 1 ] || { [ -z "${SPARKVAULT_ENABLE_UPLOAD:-}" ] && read -r -p "Enable result upload (POST /api/results)? [y/N]: " yn && [[ $yn =~ ^[Yy] ]]; }; then
  token=$(openssl rand -hex 32)
fi

umask 077
cat > "$out" <<ENV
SPARKVAULT_SECRET_KEY=$secret
SPARKVAULT_ADMIN_HASH=$hash
SPARKVAULT_CATALOGUE_HOST_DIR=$SPARKVAULT_CATALOGUE_HOST_DIR
SPARKVAULT_HF_HOST_DIR=$SPARKVAULT_HF_HOST_DIR
SPARKVAULT_NODES=$SPARKVAULT_NODES
SPARKVAULT_PORT=$SPARKVAULT_PORT
SPARKVAULT_INGEST_TOKEN=$token
SPARKVAULT_COOKIE_SECURE=0
ENV
echo "Wrote $out (mode 600)."
[ -z "$token" ] || echo "Upload is on. The token is in $out as SPARKVAULT_INGEST_TOKEN; give it to whatever submits results, and create the inbox folder (docs: README, 'Result upload')."
echo "Next: docker compose up -d --build"
