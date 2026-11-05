#!/usr/bin/env bash
# tools/bench-vllm.sh: quick throughput check against a running vLLM container.
# Run it on the GPU node itself (it talks to localhost:8000 by default). Requires VLLM_API_KEY in
# the environment, plus curl, jq, bc and docker.
set -euo pipefail

: "${VLLM_API_KEY:?VLLM_API_KEY not set}"

# `vllm bench serve` prints its whole argument list at startup, including the
# --header "Authorization=Bearer <key>" below, which can leak the key into a pasted terminal or
# log. Everything the benchmark prints goes through
# redact() so the key value can never reach the terminal, a pasted log, or a tee.
# (The key still appears in this host's process list while the benchmark runs,
# because it is a docker exec argument; that is visible to local users only.)
redact() {
  local k
  k=$(printf '%s' "$VLLM_API_KEY" | sed 's/[][\\/.*^$]/\\&/g')
  sed -u "s/$k/<redacted>/g"
}

VLLM_CONTAINER="${VLLM_CONTAINER:-vllm-main}"
BASE_URL="${BASE_URL:-http://localhost:8000}"
NUM_PROMPTS="${NUM_PROMPTS:-20}"
# BENCH_JSON=<path>: also write the measurements as JSON (the `bench` block of
# app/results.schema.json, plus `gpu_sm_clock_mhz` and `clock_ok`). The text output is
# unchanged. Wrap it in a full result record (see examples/results/) to upload or commit it.
BENCH_JSON="${BENCH_JSON:-}"
MIN_MHZ="${MIN_MHZ:-1500}"   # median SM clock under load below this = clamped node (a clamped GPU runs at a fraction of its speed)
BENCH_TMP=$(mktemp -d); trap 'kill ${SMI_PID:-} 2>/dev/null; rm -rf "$BENCH_TMP"' EXIT

MODELS_JSON=$(curl -s --noproxy "*" "$BASE_URL/v1/models" -H "Authorization: Bearer $VLLM_API_KEY")
MODEL=$(echo "$MODELS_JSON" | jq -r '.data[0].id')
TOKENIZER=$(echo "$MODELS_JSON" | jq -r '.data[0].root')
echo "== Model: $MODEL (tokenizer: $TOKENIZER) =="

if [ -n "$BENCH_JSON" ] && command -v nvidia-smi >/dev/null 2>&1; then
  nvidia-smi --query-gpu=clocks.sm,utilization.gpu --format=csv,noheader,nounits -l 1 > "$BENCH_TMP/clock.csv" &
  SMI_PID=$!
fi

echo
echo "== Quick single-request tok/s =="
START=$(date +%s.%N)
RESPONSE=$(curl -s --noproxy "*" "$BASE_URL/v1/chat/completions" \
  -H "Authorization: Bearer $VLLM_API_KEY" -H "Content-Type: application/json" \
  -d "{\"model\":\"$MODEL\",\"messages\":[{\"role\":\"user\",\"content\":\"Write a detailed 500 word essay about the history of lighthouses.\"}],\"max_tokens\":500,\"stream\":false}")
END=$(date +%s.%N)

TOKENS=$(echo "$RESPONSE" | jq '.usage.completion_tokens')
ELAPSED=$(echo "$END - $START" | bc)
if [ "$TOKENS" = "null" ] || [ -z "$TOKENS" ]; then
  echo "Request failed or returned no usage data:"
  echo "$RESPONSE" | jq .
else
  SINGLE_TOK_S=$(echo "$TOKENS/$ELAPSED" | bc -l)
  echo "tokens=$TOKENS elapsed=${ELAPSED}s tok/s=$SINGLE_TOK_S"
fi

echo
echo "== Concurrent throughput benchmark (vllm bench serve) =="
if docker exec "$VLLM_CONTAINER" vllm bench serve --help >/dev/null 2>&1; then
  docker exec -e HF_HUB_OFFLINE=1 "$VLLM_CONTAINER" vllm bench serve \
    --backend openai-chat \
    --base-url "$BASE_URL" \
    --endpoint /v1/chat/completions \
    --model "$MODEL" \
    --tokenizer "$TOKENIZER" \
    --dataset-name random \
    --random-input-len 256 \
    --random-output-len 256 \
    --num-prompts "$NUM_PROMPTS" \
    --header "Authorization=Bearer $VLLM_API_KEY" 2>&1 | redact | tee "$BENCH_TMP/bench.txt"
else
  echo "vllm bench serve not available in this image — skipping. Rely on the single-request number above."
fi

echo
echo "== Prometheus counters (sanity check) =="
curl -s --noproxy "*" "$BASE_URL/metrics" -H "Authorization: Bearer $VLLM_API_KEY" \
  | grep -E 'vllm:generation_tokens_total|vllm:time_per_output_token_seconds' || true

if [ -n "$BENCH_JSON" ]; then
  kill "${SMI_PID:-}" 2>/dev/null || true
  # one number out of a "Label:   123.45" line of `vllm bench serve`; empty if absent
  num() { grep -m1 -F "$1" "$BENCH_TMP/bench.txt" 2>/dev/null | sed -E 's/^[^:]*:[[:space:]]*//' | awk '{print $1}'; }
  # median SM clock while the GPU was busy (>=50% util); empty if no samples
  CLOCK=$(awk -F', *' '$2>=50 {print $1}' "$BENCH_TMP/clock.csv" 2>/dev/null | sort -n | awk '{a[NR]=$1} END{if(NR) print a[int((NR+1)/2)]}')
  jq -n \
    --arg single "${SINGLE_TOK_S:-}" --arg n "$NUM_PROMPTS" \
    --arg ok "$(num 'Successful requests:')" --arg failed "$(num 'Failed requests:')" \
    --arg out "$(num 'Output token throughput (tok/s):')" --arg tot "$(num 'Total token throughput (tok/s):')" \
    --arg ttft "$(num 'Mean TTFT (ms):')" --arg tpot "$(num 'Mean TPOT (ms):')" --arg itl "$(num 'P99 ITL (ms):')" \
    --arg clock "$CLOCK" --argjson min "$MIN_MHZ" '
    def n: if . == "" then null else tonumber end;
    { bench: { single_stream_tok_s: ($single | n | if . then (.*100|round/100) else . end),
               concurrent: { num_prompts: ($n | tonumber), output_tok_s: ($out | n), total_tok_s: ($tot | n),
                             mean_ttft_ms: ($ttft | n), mean_tpot_ms: ($tpot | n), p99_itl_ms: ($itl | n),
                             failed_requests: ($failed | n) } },
      gpu_sm_clock_mhz: ($clock | n),
      clock_ok: (if ($clock | n) == null then null else (($clock | tonumber) >= $min) end),
      complete: (($out | n) != null and ($single | n) != null and ($failed | n) == 0) }
    | walk(if type == "object" then with_entries(select(.value != null)) else . end)' > "$BENCH_JSON"
  echo; echo "== Wrote $BENCH_JSON =="
fi
