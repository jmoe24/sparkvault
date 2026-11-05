#!/usr/bin/env bash
# Create sparkvault.env for docker-compose.yml: generates the secrets and asks for your node names.
# Run it from a clone of this repo (it uses app/hashpw.py). Refuses to overwrite an existing file.
#
#   scripts/init-env.sh [output-file]        default: ./sparkvault.env
#
# Questions can be pre-answered with SPARKVAULT_NODES and SPARKVAULT_ENABLE_UPLOAD=1 for unattended use.
# The password is read without echo and only its scrypt hash is written. The file is created mode 600.
set -euo pipefail
cd "$(dirname "$0")/.."
out=${1:-sparkvault.env}
[ ! -e "$out" ] || { echo "$out already exists; refusing to overwrite it." >&2; exit 1; }
command -v openssl >/dev/null || { echo "openssl not found" >&2; exit 1; }
command -v python3 >/dev/null || { echo "python3 not found" >&2; exit 1; }

nodes=${SPARKVAULT_NODES:-}
if [ -z "$nodes" ]; then
  read -r -p "Node names, comma separated [node-a,node-b]: " nodes
  nodes=${nodes:-node-a,node-b}
fi

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
SPARKVAULT_INGEST_TOKEN=$token
SPARKVAULT_NODES=$nodes
SPARKVAULT_COOKIE_SECURE=0
ENV
echo "Wrote $out (mode 600)."
[ -z "$token" ] || echo "Upload is on: the token is in $out as SPARKVAULT_INGEST_TOKEN. Give it to whatever submits results, and create the inbox folder."
echo "Next: edit the EDIT lines in docker-compose.yml, then docker compose up -d --build"
