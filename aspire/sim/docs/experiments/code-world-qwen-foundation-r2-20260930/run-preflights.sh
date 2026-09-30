#!/usr/bin/env bash
set -eu
cd /mnt/home/gewang/code/ASPIRE-world-revision-20260910-01a0894b/aspire/sim
study=docs/experiments/code-world-qwen-foundation-r2-20260930
for task in bowl bowldrawer drawer; do
  .venv-libero/bin/python3 "$study/run-dsw-preflight-$task-c.py" > "$study/real-preflight-$task.log" 2>&1
done
