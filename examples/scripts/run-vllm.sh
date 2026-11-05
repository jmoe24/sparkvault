#!/usr/bin/env bash
# Example launch script for the `qwen-agent` recipe in examples/library.yaml.
# SparkVault's tools/check-recipes.py renders the recipe and compares it, token for token,
# with SERVE_CMD below, so the catalogue can never drift from the command you actually run.
# Adapt it to your own node: image name, mounts and proxy settings are yours to choose.
set -euo pipefail

: "${VLLM_API_KEY:?VLLM_API_KEY not set}"

IMAGE="${IMAGE:-vllm/vllm-openai:latest}"
NAME="vllm-main"
MODEL_CACHE="${MODEL_CACHE:?set MODEL_CACHE to the model directory on this node}"

SERVE_CMD='vllm serve nvidia/Qwen3.6-35B-A3B-NVFP4 \
    --tensor-parallel-size 1 --port 8000 --trust-remote-code \
    --max-model-len 262144 --gpu-memory-utilization 0.4 --dtype auto \
    --quantization modelopt --kv-cache-dtype fp8 \
    --attention-backend flashinfer --moe-backend marlin \
    --max-num-seqs 32 --max-num-batched-tokens 8192 \
    --enable-chunked-prefill --async-scheduling --enable-prefix-caching \
    --enable-auto-tool-choice --tool-call-parser qwen3_coder --reasoning-parser qwen3 \
    --speculative-config '"'"'{"method":"mtp","num_speculative_tokens":3,"moe_backend":"triton"}'"'"' \
    --load-format fastsafetensors \
    --served-model-name qwen-agent \
    --api-key "$VLLM_API_KEY"'

docker rm -f "$NAME" 2>/dev/null || true
exec docker run -d --name "$NAME" --gpus all --restart unless-stopped \
  -p 8000:8000 -e VLLM_API_KEY -e HF_HUB_OFFLINE=1 -e TRANSFORMERS_OFFLINE=1 \
  -v "$MODEL_CACHE:/root/.cache/huggingface:ro" \
  "$IMAGE" bash -c "$SERVE_CMD"
