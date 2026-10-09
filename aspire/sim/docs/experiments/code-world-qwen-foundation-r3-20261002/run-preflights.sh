#!/usr/bin/env bash
set -eu
cd /mnt/home/gewang/code/ASPIRE-world-revision-20260910-01a0894b/aspire/sim
study=docs/experiments/code-world-qwen-foundation-r3-20261002
for task in bowl drawer; do
  .venv-libero/bin/python3 "$study/run-dsw-preflight-$task-c.py" > "$study/real-preflight-$task.log" 2>&1
done
