#!/bin/sh
# Awaiting coordinator submit. Not run by CC_TASK_4.
# CC_TASK_4 p4fix re-run: drawer_p4_r1c + goaltaskbowldrawer_p4n_r1b, one 4-GPU unit (two slots), default pool (workspace 1000309 / quota quotaabjvnuk8san).
/mnt/home/gewang/.local/bin/dlc \
  submit \
  pytorchjob \
  --name=aspire-p4fix-1018 \
  '--command=bash /mnt/home/gewang/experiments/code-world-p4fix-20261018/launch/unit-entry.sh --node-id 0 --node-count 1 --node-window-minutes 1240' \
  --data_source_uris=bmcpfs://cpfs-010029yj6czm6fnidqh0l-vpc-qtrdw8.cn-shanghai.cpfs.aliyuncs.com/media/home::/mnt/home \
  --resource_id=quotaabjvnuk8san \
  --workspace_id=1000309 \
  --priority=9 \
  --job_max_running_time_minutes=1250 \
  --workers=1 \
  --worker_image=dsw-registry-vpc.cn-shanghai.cr.aliyuncs.com/pai/modelscope:1.28.0-pytorch2.3.1tensorflow2.16.1-gpu-py311-cu121-ubuntu22.04 \
  --worker_cpu=24 \
  --worker_memory=200Gi \
  --worker_shared_memory=8Gi \
  --worker_gpu=4
