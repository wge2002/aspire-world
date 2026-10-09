# Qwen Code World r3：修复后重跑计划（2026-10-07）

用户授权：使用 CC_USAGE 完成管线修复，重新运行原来的三个任务。最多提交三个 DLC，每项只提交一次；不自动重投。旧轮次、旧账本和旧运行目录保留。

| Cell | 任务 | DLC 名称 |
|---|---|---|
| bowl_C | put_the_bowl_on_the_plate | aspire-closedloop-r3-1007-bowl-c |
| bowldrawer_C | open_the_top_drawer_and_put_the_bowl_inside | aspire-closedloop-r3-1007-bowldrawer-c |
| drawer_C | open_the_middle_drawer_of_the_cabinet | aspire-closedloop-r3-1007-drawer-c |

每项为独立 C/full fresh-generation 实验，保持 executable_world / foundation / world_use / closed_loop revision r1。无高层策略 MD、旧解、旧轨迹或 held-out 输入。

- 实验：本地 vLLM Qwen3.8-Flash-Next-FP8，served alias `qwen3.8-flash-next`；native CC 2.1.220，xhigh，1M context，64K output。工程 CC 为 Opus 5 / high / default-manual。
- 开发 seeds 51–65，每 seed 最多三次实际执行。每项本轮 seed51 的基础设施诊断已扣第一次，剩两次；其他 seed 各三次。保留别名去重。冻结后评测 seeds 1–50，结果不返回给求解器。
- 只使用真实 SAM3 / GraspNet / IK 的非特权接口。
- 每 DLC：1 worker × 8 L20Z，160 CPU / 800Gi RAM / 64Gi shm；优先级 9。GPU0–3 模型，4 SAM3，5 GraspNet，6 仿真/IK，7 备用。
- workspace `1000309`，共享付费 quota `quotaabjvnuk8san`。当前作业列表已只读查看，其他任务仍在运行；CLI 未提供精确空闲节点数，是否立即运行以平台调度为准，不释放其他任务资源。
- 镜像：`dsw-registry-vpc.cn-shanghai.cr.aliyuncs.com/pai/modelscope:1.28.0-pytorch2.3.1tensorflow2.16.1-gpu-py311-cu121-ubuntu22.04`。
- CPFS：`bmcpfs://cpfs-010029yj6czm6fnidqh0l-vpc-qtrdw8.cn-shanghai.cpfs.aliyuncs.com/media/home::/mnt/home`；本任务写入新的运行与结果目录。入口先 source `dlc_entry_prelude.sh`，切换 UID/GID 10011:10011，再访问项目文件；清除多机 rendezvous 环境变量。
- 开发工作窗口 22h，独立报告收尾 1h，held-out 上限 13.5h；加启动与清理余量后 DLC 上限 2292min（38.2h）。预计启动后 12–24h，完成即退出。增加的是墙钟保护时间，没有增加尝试预算。

结果根：`/mnt/home/gewang/experiments/code-world-qwen-closed-loop-r3-20261007`。三项分别使用 `{cell}/outputs`；冻结运行根：`/mnt/home/gewang/code/ASPIRE-code-world-qwen-closed-loop-r3-20261007/cells/{cell}/aspire/sim`。

每项实际命令（将 `{cell}` 替换为表内 Cell）：

```sh
bash /mnt/home/gewang/experiments/code-world-qwen-closed-loop-r3-20261007/launch-20261007/dlc-entry.sh --case /mnt/home/gewang/experiments/code-world-qwen-closed-loop-r3-20261007/{cell}/case.json
```

提交使用已审阅的 `submit-foundation.py`，其命令与三份 `dlc-run plan` 一致。`--skip-preflight` 只避免重复把整场长实验当 DSW 预检运行；两道独立预检均已完成：三项真实感知工作诊断通过，三项 `dlc-preflight` 环境验证通过（各 rc=0）。冻结副本的 closeout、driver、deadline 哈希与已测试版本一致。

修复验收：33 个新增收尾用例；连同既有传输、驱动、计次和启动回归，共 121 passed / 2 subtests passed。真实 r2 开发记录的分类及原 worker lineage 只读复核通过。真实 /proc 检查能发现本任务进程、排除无关进程，退出后恢复静默。旧 r2 的 78 个文件哈希不变。

边界：完整 Qwen 长运行及新 report-only guard 的原生模型往返仍由这轮运行检验；离线通过不等于任务成功率提高。基础设施错误或开发证据不完整时仍明确阻止 held-out，不伪造完成。
