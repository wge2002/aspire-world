#!/usr/bin/env bash
# Task 5 DSW smoke launcher: one node of the unit layout on the given GPUs, with
# a 5-s nvidia-smi sampler and before/after compute-apps snapshots.
#
#   run-smoke-20261016.sh GPUS     # GPUS = 0,1,2,3,4,5,6,7 (two units) or four cards (one unit)
set -euo pipefail
gpus=$1
root=/mnt/home/gewang/experiments/node-layout-smoke-20261016
out=$root/dsw-smoke
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
mkdir -p "$out"
nvidia-smi > "$out/nvidia-smi-before.txt"
nvidia-smi --query-compute-apps=pid,gpu_uuid,used_memory --format=csv > "$out/compute-apps-before.csv"
nvidia-smi --query-gpu=index,uuid --format=csv,noheader > "$out/gpu-uuids.csv"
setsid python3 "$here/gpu_sampler.py" "$out/nvidia-smi.csv" "$out/STOP_SAMPLER" 5 \
  > "$out/sampler.log" 2>&1 < /dev/null &
echo "sampler pid $!" > "$out/pids.txt"
cd /mnt/home/gewang/code/ASPIRE-world-revision-20260910-01a0894b/aspire/sim
set +e
bash "$root/launch/unit-entry.sh" --node-id 0 --node-count 1 --node-window-minutes 300 --gpus "$gpus" \
  > "$out/entry.log" 2>&1 < /dev/null
code=$?
set -e
echo "entry exit $code at $(date -Iseconds)" >> "$out/pids.txt"
touch "$out/STOP_SAMPLER"
sleep 8
nvidia-smi > "$out/nvidia-smi-after-cleanup.txt"
nvidia-smi --query-compute-apps=pid,gpu_uuid,used_memory --format=csv > "$out/compute-apps-after.csv"
exit $code
