#!/bin/sh
# AWAITING COORDINATOR SUBMIT. Do not run without per-job approval (coordinator submits once).
# CC_TASK_3 p4n hardest-first study, node 0 of 2 (shared claim queue with node 1).
/mnt/home/gewang/.local/bin/dlc \
  submit \
  pytorchjob \
  --name=aspire-hardest-p4n-1018-node0 \
  '--command=bash /mnt/home/gewang/experiments/code-world-hardest-p4n-20261018/launch/unit-entry.sh --node-id 0 --node-count 2 --node-window-minutes 1240' \
  --data_source_uris=bmcpfs://cpfs-010029yj6czm6fnidqh0l-vpc-qtrdw8.cn-shanghai.cpfs.aliyuncs.com/media/home::/mnt/home \
  --resource_id=quotal96jnu33mnl \
  --workspace_id=1493863 \
  --priority=9 \
  --job_max_running_time_minutes=1250 \
  --workers=1 \
  --worker_image=dsw-registry-vpc.cn-shanghai.cr.aliyuncs.com/pai/modelscope:1.28.0-pytorch2.3.1tensorflow2.16.1-gpu-py311-cu121-ubuntu22.04 \
  --worker_cpu=48 \
  --worker_memory=400Gi \
  --worker_shared_memory=16Gi \
  --worker_gpu=8
