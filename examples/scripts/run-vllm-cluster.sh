#!/usr/bin/env bash
# Example (incomplete on purpose) two-node launch script for the `gpt-oss-120b` recipe.
# Only the serve command is kept, because that is the part tools/check-recipes.py compares with
# the recipe. A real script also starts Ray (or a multi-node vLLM) on both nodes.
set -euo pipefail

MODEL_ID="openai/gpt-oss-120b"
MXFP4_LAYERS="moe,qkv,o"

COMMON="vllm serve $MODEL_ID \
    --tool-call-parser openai \
    --reasoning-parser openai_gptoss \
    --enable-auto-tool-choice \
    --tensor-parallel-size 2 \
    --gpu-memory-utilization 0.7 \
    --enable-prefix-caching \
    --load-format fastsafetensors \
    --quantization mxfp4 \
    --mxfp4-backend CUTLASS \
    --mxfp4-layers $MXFP4_LAYERS \
    --attention-backend FLASHINFER \
    --kv-cache-dtype fp8 \
    --max-num-batched-tokens 8192"

COMMON="$COMMON --distributed-executor-backend ray"
echo "$COMMON"
