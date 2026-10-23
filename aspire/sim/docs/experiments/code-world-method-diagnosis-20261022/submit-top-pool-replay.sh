#!/bin/sh
# CC_TASK_13 method-diagnosis replays: 23 jobs / 345 trials of frozen and non-frozen development versions
# (objtaskmilk_p6b_r1, spatialtaskcookie_p6b_r1, controls drawer_p6b_r1 / bowldrawer_p6b_r1) on dev 51-65 and unseen 66-80,
# through the production held-out functions; no Qwen, no solver. TOP-pool single-worker 4-GPU submission.
# Exclusive output is a submission-once guard. An error/ambiguous result is inspected, never blindly retried.
set -euC
task_root=/mnt/home/gewang/experiments/code-world-method-diagnosis-20261022-replay
mkdir -p "$task_root/submission"
receipt="$task_root/submission/replay-submit.log"
if [ -e "$receipt" ]; then
  echo "Submission record already exists: $receipt; inspect it before any further action" >&2
  exit 2
fi
exec /mnt/home/gewang/.local/bin/dlc submit pytorchjob \
  --name=aspire-method-diagnosis-replay-1022 \
  '--command=bash /mnt/home/gewang/experiments/code-world-method-diagnosis-20261022-replay/launch/replay-entry.sh' \
  --data_source_uris=bmcpfs://cpfs-010029yj6czm6fnidqh0l-vpc-qtrdw8.cn-shanghai.cpfs.aliyuncs.com/media/home::/mnt/home \
  --resource_id=quotal96jnu33mnl \
  --workspace_id=1493863 \
  --priority=9 \
  --job_max_running_time_minutes=600 \
  --workers=1 \
  --worker_image=dsw-registry-vpc.cn-shanghai.cr.aliyuncs.com/pai/modelscope:1.28.0-pytorch2.3.1tensorflow2.16.1-gpu-py311-cu121-ubuntu22.04 \
  --worker_cpu=48 \
  --worker_memory=256Gi \
  --worker_shared_memory=16Gi \
  --worker_gpu=4 > "$receipt" 2>&1
