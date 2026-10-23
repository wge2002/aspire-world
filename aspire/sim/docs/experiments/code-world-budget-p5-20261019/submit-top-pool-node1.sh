#!/bin/sh
# Awaiting coordinator submit. Not run by CC_TASK_6.
# CC_TASK_6 p5 study (p4 method + fixes + aspire-v1 development budget), node 1 of 3: spatialbetween_p5_r1, bowl_p5_r1, objswapsoup_p5_r1, goaltaskbowldrawer_p5_r1; cells pinned by study.json node_cells; two units x two slots; top pool (workspace 1493863 / quota quotal96jnu33mnl).
/mnt/home/gewang/.local/bin/dlc \
  submit \
  pytorchjob \
  --name=aspire-budget-p5-1019-node1 \
  '--command=bash /mnt/home/gewang/experiments/code-world-budget-p5-20261019/launch/unit-entry.sh --node-id 1 --node-count 3 --node-window-minutes 2420' \
  --data_source_uris=bmcpfs://cpfs-010029yj6czm6fnidqh0l-vpc-qtrdw8.cn-shanghai.cpfs.aliyuncs.com/media/home::/mnt/home \
  --resource_id=quotal96jnu33mnl \
  --workspace_id=1493863 \
  --priority=9 \
  --job_max_running_time_minutes=2430 \
  --workers=1 \
  --worker_image=dsw-registry-vpc.cn-shanghai.cr.aliyuncs.com/pai/modelscope:1.28.0-pytorch2.3.1tensorflow2.16.1-gpu-py311-cu121-ubuntu22.04 \
  --worker_cpu=48 \
  --worker_memory=400Gi \
  --worker_shared_memory=16Gi \
  --worker_gpu=8
