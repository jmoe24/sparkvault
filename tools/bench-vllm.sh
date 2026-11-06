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
# BENCH_DATASET=random (default) | prompts. `random` sends token soup, which gpt-oss-120b answers briefly (about 170 of
# the 256 requested tokens), and `--ignore-eos` does not change that (it does not override harmony stop tokens).
# `prompts` sends the long-form questions in tools/bench-prompts.jsonl (the custom dataset) so requests run to the
# token limit like a real workload. Switching datasets changes the workload, so numbers are comparable only within
# one dataset (the record's `source` text says which). Output tok/s is taken from the server token counter
# (vllm:generation_tokens_total), because reasoning models return their analysis as reasoning_content and
# `vllm bench serve` counts only the visible text (it reported ~1450 of 5120 tokens on 2026-11-05).
BENCH_DATASET="${BENCH_DATASET:-random}"
BENCH_PROMPTS="${BENCH_PROMPTS:-$(dirname "$0")/bench-prompts.jsonl}"
OUT_LEN=256
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

# total tokens the server has generated so far (all engines); empty if /metrics is unreachable
gen_total() {
  curl -s --noproxy "*" "$BASE_URL/metrics" -H "Authorization: Bearer $VLLM_API_KEY" \
    | awk '/^vllm:generation_tokens_total[{ ]/ {s+=$NF; f=1} END{if(f) printf "%d", s}'
}

echo
echo "== Concurrent throughput benchmark (vllm bench serve) =="
BENCH_HELP=$(docker exec "$VLLM_CONTAINER" vllm bench serve --help 2>&1 || true)
if [ -n "$BENCH_HELP" ] && ! echo "$BENCH_HELP" | grep -q "^usage\|--backend"; then BENCH_HELP=""; fi
if [ -n "$BENCH_HELP" ]; then
  # the tool sends one warm-up request first; skip it when the flag exists so the counter delta is exact
  SKIP_WARM=""; echo "$BENCH_HELP" | grep -q -- "--skip-initial-test-run" && SKIP_WARM="--skip-initial-test-run"
  DATASET_ARGS="--dataset-name random --random-input-len 256 --random-output-len $OUT_LEN"
  if [ "$BENCH_DATASET" = prompts ]; then
    docker exec -i "$VLLM_CONTAINER" sh -c 'cat > /tmp/sv-bench-prompts.jsonl' < "$BENCH_PROMPTS"
    # vLLM's custom dataset reads the file with pandas, which the stock localai/vllm-main image does not have
    # (the gpt-oss image does). Put pandas and its pure-Python deps in a folder under /tmp of THIS container
    # only: --no-deps so pip cannot pull a second numpy next to the pinned one, and PYTHONPATH just for the
    # bench process. The image and site-packages are untouched; a recreated container starts clean.
    if ! docker exec "$VLLM_CONTAINER" python3 -c 'import pandas' >/dev/null 2>&1; then
      echo "pandas not in $VLLM_CONTAINER; installing it under /tmp/sv-bench-deps (container only)"
      docker exec "$VLLM_CONTAINER" pip install -q --no-deps --target /tmp/sv-bench-deps \
        "pandas<3" python-dateutil pytz tzdata six 2>&1 | redact | tail -5
      OLD_PP=$(docker exec "$VLLM_CONTAINER" printenv PYTHONPATH 2>/dev/null || true)
      EXTRA_ENV="-e PYTHONPATH=/tmp/sv-bench-deps${OLD_PP:+:$OLD_PP}"
    fi
    # the chat endpoint applies the chat template itself, so the client must not
    DATASET_ARGS="--dataset-name custom --dataset-path /tmp/sv-bench-prompts.jsonl --custom-output-len $OUT_LEN --skip-chat-template --disable-shuffle"
  fi
  G0=$(gen_total || true)
  docker exec -e HF_HUB_OFFLINE=1 ${EXTRA_ENV:-} "$VLLM_CONTAINER" vllm bench serve \
    --backend openai-chat \
    --base-url "$BASE_URL" \
    --endpoint /v1/chat/completions \
    --model "$MODEL" \
    --tokenizer "$TOKENIZER" \
    $DATASET_ARGS \
    --num-prompts "$NUM_PROMPTS" $SKIP_WARM \
    --header "Authorization=Bearer $VLLM_API_KEY" 2>&1 | redact | tee "$BENCH_TMP/bench.txt"
  G1=$(gen_total || true)
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
  # Tokens the server really generated during the run: counter delta (minus the warm-up request when the tool
  # could not skip it, estimated as one average request). Falls back to the tool's own count if no counter.
  DUR=$(num 'Benchmark duration (s):'); IN=$(num 'Total input tokens:'); CNT=$(num 'Total generated tokens:')
  SRV=""
  if [ -n "${G0:-}" ] && [ -n "${G1:-}" ] && [ "$G1" -ge "$G0" ]; then
    if [ -n "${SKIP_WARM:-}" ]; then SRV=$((G1 - G0)); else SRV=$(( (G1 - G0) * NUM_PROMPTS / (NUM_PROMPTS + 1) )); fi
  fi
  GEN="${SRV:-$CNT}"
  if [ -n "$GEN" ]; then
    echo "Generated tokens: $GEN of $((NUM_PROMPTS * OUT_LEN)) requested (server counter: ${SRV:-n/a}, bench count: ${CNT:-n/a})"
  fi
  jq -n \
    --arg gen "${GEN:-}" --arg srv "${SRV:-}" --arg cnt "${CNT:-}" --arg dur "${DUR:-}" --arg inp "${IN:-}" --argjson want "$((NUM_PROMPTS * OUT_LEN))" \
    --arg single "${SINGLE_TOK_S:-}" --arg n "$NUM_PROMPTS" \
    --arg ok "$(num 'Successful requests:')" --arg failed "$(num 'Failed requests:')" \
    --arg out "$(num 'Output token throughput (tok/s):')" --arg tot "$(num 'Total token throughput (tok/s):')" \
    --arg ttft "$(num 'Mean TTFT (ms):')" --arg tpot "$(num 'Mean TPOT (ms):')" --arg itl "$(num 'P99 ITL (ms):')" \
    --arg clock "$CLOCK" --argjson min "$MIN_MHZ" '
    def n: if . == "" then null else tonumber end;
    # server-side figures when the counter was readable; the tool own count otherwise
    ($srv | n) as $s | ($dur | n) as $d | ($inp | n) as $i | ($cnt | n) as $c
    | (if $s != null and $d != null and $d > 0 then ($s / $d * 100 | round / 100) else ($out | n) end) as $outtps
    | (if $s != null and $d != null and $d > 0 and $i != null then (($i + $s) / $d * 100 | round / 100) else ($tot | n) end) as $tottps
    # TPOT is derived from the tool own token count, so it is wrong whenever that count is short
    | (if $s != null and $c != null and ($c < $s * 0.95) then null else ($tpot | n) end) as $tpotv |
    { bench: { single_stream_tok_s: ($single | n | if . then (.*100|round/100) else . end),
               concurrent: { num_prompts: ($n | tonumber), output_tok_s: $outtps, total_tok_s: $tottps,
                             mean_ttft_ms: ($ttft | n), mean_tpot_ms: $tpotv, p99_itl_ms: ($itl | n),
                             failed_requests: ($failed | n) } },
      gpu_sm_clock_mhz: ($clock | n),
      clock_ok: (if ($clock | n) == null then null else (($clock | tonumber) >= $min) end),
      output_from_server: ($s != null),
      complete: ($outtps != null and ($single | n) != null and ($failed | n) == 0
                 and (($gen | n) == null or ($gen | tonumber) >= ($want * 0.95))) }
    | walk(if type == "object" then with_entries(select(.value != null)) else . end)' > "$BENCH_JSON"
  echo; echo "== Wrote $BENCH_JSON =="
fi
