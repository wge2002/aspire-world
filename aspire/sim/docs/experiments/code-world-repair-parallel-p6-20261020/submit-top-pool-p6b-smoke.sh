#!/bin/sh
# P6b worker-recovery smoke; TOP-pool single-worker 4-GPU submission (coordinator-submitted).
# Exclusive output is a submission-once guard. An error/ambiguous result is inspected, never blindly retried.
set -euC
task_root=/mnt/home/gewang/experiments/code-world-worker-recovery-p6b-20261021-smoke
mkdir -p "$task_root/submission"
receipt="$task_root/submission/smoke-submit.log"
if [ -e "$receipt" ]; then
  echo "Submission record already exists: $receipt; inspect it before any further action" >&2
  exit 2
fi
exec /mnt/home/gewang/.local/bin/dlc submit pytorchjob \
  --name=aspire-p6b-recovery-smoke-1021 \
  '--command=bash /mnt/home/gewang/experiments/code-world-worker-recovery-p6b-20261021-smoke/launch/unit-entry.sh --node-id 0 --node-count 1 --node-window-minutes 1750' \
  --data_source_uris=bmcpfs://cpfs-010029yj6czm6fnidqh0l-vpc-qtrdw8.cn-shanghai.cpfs.aliyuncs.com/media/home::/mnt/home \
  --resource_id=quotal96jnu33mnl \
  --workspace_id=1493863 \
  --priority=9 \
  --job_max_running_time_minutes=600 \
  --workers=1 \
  --worker_image=dsw-registry-vpc.cn-shanghai.cr.aliyuncs.com/pai/modelscope:1.28.0-pytorch2.3.1tensorflow2.16.1-gpu-py311-cu121-ubuntu22.04 \
  --worker_cpu=24 \
  --worker_memory=224Gi \
  --worker_shared_memory=16Gi \
  --worker_gpu=4 > "$receipt" 2>&1
