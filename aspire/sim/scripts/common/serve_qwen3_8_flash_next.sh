#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# Manage a four- or eight-GPU OpenAI-compatible Qwen3.8-Flash-Next vLLM server.
#
# Use a vLLM 0.29.0+ runtime with Qwen3.8-Flash-Next support. The official
# dedicated image is vllm/vllm-openai:qwen38-flash-next.
#
# Optional overrides:
#   QWEN38_MODEL_PATH=/path/to/Qwen3.8-Flash-Next-FP8
#   QWEN38_VLLM_BIN=/path/to/vllm
#   QWEN38_GPUS=0,1,2,3 QWEN38_PORT=8121
#   QWEN38_MAX_MODEL_LEN=262144 QWEN38_GPU_MEMORY_UTILIZATION=0.90
#   QWEN38_MAX_NUM_SEQS=256 QWEN38_PLE_CPU_OFFLOAD=0
#   QWEN38_ENABLE_EXPERT_PARALLEL=0 QWEN38_WAIT_SECONDS=1800
#
# Eight-GPU mode always enables expert parallelism with the Triton MoE backend;
# plain TP8 is incompatible with the official block-FP8 checkpoint.
#
# Usage:
#   bash scripts/common/serve_qwen3_8_flash_next.sh start
#   bash scripts/common/serve_qwen3_8_flash_next.sh status
#   bash scripts/common/serve_qwen3_8_flash_next.sh smoke
#   bash scripts/common/serve_qwen3_8_flash_next.sh stop

set -euo pipefail

ACTION="${1:-start}"
MODEL_PATH="${QWEN38_MODEL_PATH:-Qwen/Qwen3.8-Flash-Next-FP8}"
VLLM_BIN="${QWEN38_VLLM_BIN:-vllm}"
GPU_IDS="${QWEN38_GPUS:-0,1,2,3}"
HOST="${QWEN38_HOST:-127.0.0.1}"
PORT="${QWEN38_PORT:-8121}"
SERVED_MODEL_NAME="${QWEN38_SERVED_MODEL_NAME:-qwen3.8-flash-next}"
MAX_MODEL_LEN="${QWEN38_MAX_MODEL_LEN:-262144}"
GPU_MEMORY_UTILIZATION="${QWEN38_GPU_MEMORY_UTILIZATION:-0.90}"
MAX_NUM_SEQS="${QWEN38_MAX_NUM_SEQS:-256}"
PLE_CPU_OFFLOAD="${QWEN38_PLE_CPU_OFFLOAD:-0}"
ENABLE_EXPERT_PARALLEL="${QWEN38_ENABLE_EXPERT_PARALLEL:-0}"
WAIT_SECONDS="${QWEN38_WAIT_SECONDS:-1800}"
STATE_DIR="${QWEN38_STATE_DIR:-/tmp/aspire-qwen3.8-flash-next}"
PID_FILE="$STATE_DIR/server.pid"
LOG_FILE="$STATE_DIR/server.log"

IFS=',' read -r -a GPU_ARRAY <<< "$GPU_IDS"
GPU_COUNT="${#GPU_ARRAY[@]}"
if [[ "$GPU_COUNT" -ne 4 && "$GPU_COUNT" -ne 8 ]]; then
  echo "ERROR: QWEN38_GPUS must contain exactly four or eight comma-separated GPU IDs; got '$GPU_IDS'." >&2
  exit 2
fi
if [[ "$PLE_CPU_OFFLOAD" != "0" && "$PLE_CPU_OFFLOAD" != "1" ]]; then
  echo "ERROR: QWEN38_PLE_CPU_OFFLOAD must be 0 or 1." >&2
  exit 2
fi
if [[ "$ENABLE_EXPERT_PARALLEL" != "0" && "$ENABLE_EXPERT_PARALLEL" != "1" ]]; then
  echo "ERROR: QWEN38_ENABLE_EXPERT_PARALLEL must be 0 or 1." >&2
  exit 2
fi

server_is_ready() {
  curl --silent --fail --max-time 3 "http://$HOST:$PORT/health" >/dev/null 2>&1
}

server_pid() {
  if [[ -f "$PID_FILE" ]]; then
    tr -dc '0-9' < "$PID_FILE"
  fi
}

show_status() {
  local pid
  pid="$(server_pid)"
  if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
    echo "process: running (PID $pid)"
  else
    echo "process: stopped"
  fi
  if server_is_ready; then
    echo "endpoint: ready at http://$HOST:$PORT/v1/chat/completions"
    curl --silent --fail --max-time 5 "http://$HOST:$PORT/v1/models"
    echo
  else
    echo "endpoint: not ready"
  fi
  echo "log: $LOG_FILE"
}

start_server() {
  local pid elapsed
  local -a env_args parallel_args
  if server_is_ready; then
    echo "Qwen3.8 endpoint is already ready at http://$HOST:$PORT."
    show_status
    return 0
  fi

  pid="$(server_pid)"
  if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
    echo "ERROR: PID $pid is still running but the endpoint is not ready; inspect $LOG_FILE." >&2
    exit 1
  fi
  if [[ "$MODEL_PATH" == /* && ! -f "$MODEL_PATH/config.json" ]]; then
    echo "ERROR: model config is missing: $MODEL_PATH/config.json" >&2
    exit 1
  fi
  if [[ "$VLLM_BIN" == */* && ! -x "$VLLM_BIN" ]]; then
    echo "ERROR: vLLM executable is missing: $VLLM_BIN" >&2
    exit 1
  fi

  mkdir -p "$STATE_DIR"
  : > "$LOG_FILE"
  echo "Starting $SERVED_MODEL_NAME on GPUs $GPU_IDS; log: $LOG_FILE"

  env_args=("CUDA_VISIBLE_DEVICES=$GPU_IDS")
  if [[ "$PLE_CPU_OFFLOAD" == "1" ]]; then
    env_args+=("VLLM_PLE_CPU_OFFLOAD=1")
  fi

  parallel_args=(--tensor-parallel-size "$GPU_COUNT")
  if [[ "$GPU_COUNT" -eq 8 || "$ENABLE_EXPERT_PARALLEL" == "1" ]]; then
    parallel_args+=(--enable-expert-parallel --moe-backend triton)
  fi

  nohup env "${env_args[@]}" \
    "$VLLM_BIN" serve "$MODEL_PATH" \
      --host "$HOST" \
      --port "$PORT" \
      --served-model-name "$SERVED_MODEL_NAME" \
      --dtype auto \
      "${parallel_args[@]}" \
      --gpu-memory-utilization "$GPU_MEMORY_UTILIZATION" \
      --max-model-len "$MAX_MODEL_LEN" \
      --max-num-seqs "$MAX_NUM_SEQS" \
      --enable-prefix-caching \
      --no-enable-flashinfer-autotune \
      --reasoning-parser qwen3 \
      --tool-call-parser qwen3_xml \
      --enable-auto-tool-choice \
      > "$LOG_FILE" 2>&1 &
  pid=$!
  echo "$pid" > "$PID_FILE"

  elapsed=0
  while (( elapsed < WAIT_SECONDS )); do
    if server_is_ready; then
      echo "Qwen3.8 endpoint is ready after ${elapsed}s."
      show_status
      return 0
    fi
    if ! kill -0 "$pid" 2>/dev/null; then
      echo "ERROR: vLLM exited before becoming ready." >&2
      tail -n 80 "$LOG_FILE" >&2
      exit 1
    fi
    sleep 5
    elapsed=$((elapsed + 5))
  done

  echo "ERROR: vLLM was not ready after ${WAIT_SECONDS}s; it is still running as PID $pid." >&2
  tail -n 80 "$LOG_FILE" >&2
  exit 1
}

stop_server() {
  local pid waited
  pid="$(server_pid)"
  if [[ -z "$pid" ]] || ! kill -0 "$pid" 2>/dev/null; then
    echo "Qwen3.8 server is not running."
    return 0
  fi
  kill "$pid"
  waited=0
  while kill -0 "$pid" 2>/dev/null && (( waited < 60 )); do
    sleep 2
    waited=$((waited + 2))
  done
  if kill -0 "$pid" 2>/dev/null; then
    echo "ERROR: PID $pid did not stop within 60s; inspect it manually." >&2
    exit 1
  fi
  rm -f "$PID_FILE"
  echo "Stopped Qwen3.8 server PID $pid."
}

case "$ACTION" in
  start) start_server ;;
  status) show_status ;;
  smoke)
    python3 "$(dirname "$0")/smoke_qwen3_8_flash_next.py" \
      --base-url "http://$HOST:$PORT/v1" \
      --model "$SERVED_MODEL_NAME"
    ;;
  stop) stop_server ;;
  *)
    echo "Usage: $0 {start|status|smoke|stop}" >&2
    exit 2
    ;;
esac
