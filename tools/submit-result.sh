#!/usr/bin/env bash
# Upload one result record to a running SparkVault (POST /api/results).
#
#   SPARKVAULT_URL=http://host:8080 SPARKVAULT_INGEST_TOKEN=... tools/submit-result.sh record.json
#
# The record must match app/results.schema.json (see examples/results/). The server answers
# 201 stored, 401 bad token, 404 upload disabled, 409 id exists, 413 too big, 422 invalid.
# The token is handed to curl on stdin, so it never appears in the process list or shell history.
set -euo pipefail
rec=${1:?usage: submit-result.sh record.json}
: "${SPARKVAULT_URL:?set SPARKVAULT_URL, e.g. http://host:8080}"
: "${SPARKVAULT_INGEST_TOKEN:?set SPARKVAULT_INGEST_TOKEN}"
[ -f "$rec" ] || { echo "no such file: $rec" >&2; exit 1; }
printf 'header = "Authorization: Bearer %s"\n' "$SPARKVAULT_INGEST_TOKEN" |
  curl --fail-with-body -sS -K - -X POST "${SPARKVAULT_URL%/}/api/results" \
    -H "Content-Type: application/json" --data-binary "@$rec"
echo
