#!/usr/bin/env bash
# DLC entry for the Task 5 smoke WITH per-card GPU memory sampling: start a 5-s
# nvidia-smi sampler as 10011, run the frozen unit entry unchanged, stop the
# sampler, snapshot compute-apps. Samples land in <root>/dlc-gpu/.
#
#   dlc-sampled-entry.sh --node-id N --node-count M --node-window-minutes W [--preflight]
set -eo pipefail
# Same root-only step as unit-entry.sh, done before the identity switch below.
if [[ "$(id -u)" == 0 && ! -e /mnt/workspace/gewang ]]; then
  mkdir -p /mnt/workspace
  ln -s /mnt/home/gewang /mnt/workspace/gewang
fi
RBS_DLC_WORKDIR=/mnt/home/gewang
. /mnt/home/gewang/.config/rbs-dlc/dlc_entry_prelude.sh
set -u
root=/mnt/home/gewang/experiments/node-layout-smoke-20261016
here=/mnt/home/gewang/code/ASPIRE-world-revision-20260910-01a0894b/aspire/sim/docs/engineering/node-layout-20261015/smoke
out=$root/dlc-gpu
for argument in "$@"; do [[ $argument == --preflight ]] && out=$root/../node-layout-smoke-20261016-preflight-gpu; done
mkdir -p "$out"
stamp=$(date +%Y%m%dT%H%M%S)
nvidia-smi > "$out/nvidia-smi-before-$stamp.txt"
nvidia-smi --query-compute-apps=pid,gpu_uuid,used_memory --format=csv > "$out/compute-apps-before-$stamp.csv"
setsid /usr/bin/env python3 "$here/gpu_sampler.py" "$out/nvidia-smi-$stamp.csv" "$out/STOP-$stamp" 5 \
  > "$out/sampler-$stamp.log" 2>&1 < /dev/null &
set +e
bash "$root/launch/unit-entry.sh" "$@"
code=$?
set -e
touch "$out/STOP-$stamp"
sleep 8
nvidia-smi > "$out/nvidia-smi-after-$stamp.txt"
nvidia-smi --query-compute-apps=pid,gpu_uuid,used_memory --format=csv > "$out/compute-apps-after-$stamp.csv"
exit $code
