#!/bin/sh
# P6b-ns node 0: four fresh cells (libero_10_task mug-microwave N=2, mokastove N=4, libero_spatial_task N=4, libero_object_task N=4); TOP-pool single-worker 8-GPU submission.
# Exclusive output is a submission-once guard. An error/ambiguous result is inspected, never blindly retried.
set -euC
task_root=/mnt/home/gewang/experiments/code-world-worker-recovery-p6b-20261021-ns
mkdir -p "$task_root/submission"
receipt="$task_root/submission/node0-submit.log"
if [ -e "$receipt" ]; then
  echo "Submission record already exists: $receipt; inspect it before any further action" >&2
  exit 2
fi
exec /mnt/home/gewang/.local/bin/dlc submit pytorchjob \
  --name=aspire-worker-recovery-p6b-1021-ns-node0 \
  '--command=bash /mnt/home/gewang/experiments/code-world-worker-recovery-p6b-20261021-ns/launch/unit-entry.sh --node-id 0 --node-count 1 --node-window-minutes 2100' \
  --data_source_uris=bmcpfs://cpfs-010029yj6czm6fnidqh0l-vpc-qtrdw8.cn-shanghai.cpfs.aliyuncs.com/media/home::/mnt/home \
  --resource_id=quotal96jnu33mnl \
  --workspace_id=1493863 \
  --priority=9 \
  --job_max_running_time_minutes=2110 \
  --workers=1 \
  --worker_image=dsw-registry-vpc.cn-shanghai.cr.aliyuncs.com/pai/modelscope:1.28.0-pytorch2.3.1tensorflow2.16.1-gpu-py311-cu121-ubuntu22.04 \
  --worker_cpu=48 \
  --worker_memory=448Gi \
  --worker_shared_memory=16Gi \
  --worker_gpu=8 > "$receipt" 2>&1
