#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# Manage a four-GPU, OpenAI-compatible DeepSeek-V4-Flash-0731 vLLM server.
#
# Required overrides on hosts without the defaults below:
#   DSV4_MODEL_PATH=/path/to/DeepSeek-V4-Flash-0731
#   DSV4_VLLM_BIN=/path/to/vllm
#
# Optional overrides:
#   DSV4_GPUS=0,1,2,3 DSV4_PORT=8120 DSV4_MAX_MODEL_LEN=131072
#   DSV4_CUDA_COMPAT_DIR=/path/to/cuda-12.9/compat
#   DSV4_NVCC=/path/to/cuda-12.9/bin/nvcc
#   DSV4_STATE_DIR=/path/to/state DSV4_WAIT_SECONDS=1800
#
# Usage:
#   bash scripts/common/serve_deepseek_v4_flash_0731.sh start
#   bash scripts/common/serve_deepseek_v4_flash_0731.sh status
#   bash scripts/common/serve_deepseek_v4_flash_0731.sh smoke
#   bash scripts/common/serve_deepseek_v4_flash_0731.sh stop

set -euo pipefail

ACTION="${1:-start}"
MODEL_PATH="${DSV4_MODEL_PATH:-deepseek-ai/DeepSeek-V4-Flash-0731}"
VLLM_BIN="${DSV4_VLLM_BIN:-vllm}"
GPU_IDS="${DSV4_GPUS:-0,1,2,3}"
CUDA_COMPAT_DIR="${DSV4_CUDA_COMPAT_DIR:-}"
NVCC_BIN="${DSV4_NVCC:-}"
CUDA_HOME_DIR="${DSV4_CUDA_HOME:-}"
HOST="${DSV4_HOST:-127.0.0.1}"
PORT="${DSV4_PORT:-8120}"
SERVED_MODEL_NAME="${DSV4_SERVED_MODEL_NAME:-deepseek-v4-flash-0731}"
MAX_MODEL_LEN="${DSV4_MAX_MODEL_LEN:-131072}"
GPU_MEMORY_UTILIZATION="${DSV4_GPU_MEMORY_UTILIZATION:-0.92}"
MAX_NUM_SEQS="${DSV4_MAX_NUM_SEQS:-8}"
MAX_NUM_BATCHED_TOKENS="${DSV4_MAX_NUM_BATCHED_TOKENS:-8192}"
WAIT_SECONDS="${DSV4_WAIT_SECONDS:-1800}"
STATE_DIR="${DSV4_STATE_DIR:-/tmp/aspire-deepseek-v4-flash-0731}"
PID_FILE="$STATE_DIR/server.pid"
LOG_FILE="$STATE_DIR/server.log"

IFS=',' read -r -a GPU_ARRAY <<< "$GPU_IDS"
if [[ ${#GPU_ARRAY[@]} -ne 4 ]]; then
  echo "ERROR: DSV4_GPUS must contain exactly four comma-separated GPU IDs; got '$GPU_IDS'." >&2
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
  local -a env_args
  if server_is_ready; then
    echo "DeepSeek endpoint is already ready at http://$HOST:$PORT."
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
  if [[ -n "$CUDA_COMPAT_DIR" && ! -f "$CUDA_COMPAT_DIR/libcuda.so.1" ]]; then
    echo "ERROR: CUDA compatibility library is missing: $CUDA_COMPAT_DIR/libcuda.so.1" >&2
    exit 1
  fi
  if [[ -n "$NVCC_BIN" && ! -x "$NVCC_BIN" ]]; then
    echo "ERROR: CUDA compiler is missing: $NVCC_BIN" >&2
    exit 1
  fi

  mkdir -p "$STATE_DIR"
  : > "$LOG_FILE"
  echo "Starting $SERVED_MODEL_NAME on GPUs $GPU_IDS; log: $LOG_FILE"
  env_args=("CUDA_VISIBLE_DEVICES=$GPU_IDS")
  if [[ -n "$CUDA_COMPAT_DIR" ]]; then
    env_args+=("LD_LIBRARY_PATH=$CUDA_COMPAT_DIR${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}")
  fi
  if [[ -n "$NVCC_BIN" ]]; then
    if [[ -z "$CUDA_HOME_DIR" ]]; then
      CUDA_HOME_DIR="$(dirname "$(dirname "$NVCC_BIN")")"
    fi
    env_args+=(
      "DG_JIT_NVCC_COMPILER=$NVCC_BIN"
      "CUDA_HOME=$CUDA_HOME_DIR"
      "CUDA_PATH=$CUDA_HOME_DIR"
    )
  fi
  nohup env "${env_args[@]}" \
    "$VLLM_BIN" serve "$MODEL_PATH" \
      --host "$HOST" \
      --port "$PORT" \
      --served-model-name "$SERVED_MODEL_NAME" \
      --trust-remote-code \
      --dtype auto \
      --tensor-parallel-size 4 \
      --enable-expert-parallel \
      --kv-cache-dtype fp8 \
      --block-size 256 \
      --gpu-memory-utilization "$GPU_MEMORY_UTILIZATION" \
      --max-model-len "$MAX_MODEL_LEN" \
      --max-num-seqs "$MAX_NUM_SEQS" \
      --max-num-batched-tokens "$MAX_NUM_BATCHED_TOKENS" \
      --tokenizer-mode deepseek_v4 \
      --reasoning-parser deepseek_v4 \
      --tool-call-parser deepseek_v4 \
      --enable-auto-tool-choice \
      --moe-backend marlin \
      > "$LOG_FILE" 2>&1 &
  pid=$!
  echo "$pid" > "$PID_FILE"

  elapsed=0
  while (( elapsed < WAIT_SECONDS )); do
    if server_is_ready; then
      echo "DeepSeek endpoint is ready after ${elapsed}s."
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
    echo "DeepSeek server is not running."
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
  echo "Stopped DeepSeek server PID $pid."
}

case "$ACTION" in
  start) start_server ;;
  status) show_status ;;
  smoke)
    python3 "$(dirname "$0")/smoke_deepseek_v4_flash_0731.py" \
      --base-url "http://$HOST:$PORT/v1" \
      --model "$SERVED_MODEL_NAME"
    ;;
  stop) stop_server ;;
  *)
    echo "Usage: $0 {start|status|smoke|stop}" >&2
    exit 2
    ;;
esac
