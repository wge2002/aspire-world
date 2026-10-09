# 最新源码与实验协议入口

源码对齐日期：2026-10-09。当前重点是 **Prediction-contract base study / p1**，以及它依赖的 gate study、闭环与 pipeline 修复。本页描述代码和协议，不宣称实验已完成，不包含运行结果。

## Prediction contract（2026-10-08）

- [PLAN](../aspire/sim/docs/experiments/code-world-prediction-base-20261008/PLAN.md)：比较 `off` 与 `p1`；5 个任务 × 2 arms × 2 repeats。
- [NATIVE_WORLD_INTERFACE](../aspire/sim/docs/experiments/code-world-prediction-base-20261008/NATIVE_WORLD_INTERFACE.md)：预测事实、实测证据、match / mismatch / unknown / unresolved 与 policy 查询契约。
- [prediction_contract.py](../aspire/sim/cap/world_model/prediction_contract.py)：将每次调用前的预测与后续真实 observed layer 对齐；policy 自行决定如何使用，框架不自动执行恢复动作。
- [prediction_study_analysis.py](../aspire/sim/scripts/libero/prediction_study_analysis.py)：开发集预测/使用统计与冻结评估分析入口。
- [prepare-prediction-study.py](../aspire/sim/docs/experiments/code-world-prediction-base-20261008/prepare-prediction-study.py)、[support](../aspire/sim/docs/experiments/code-world-prediction-base-20261008/support/)：准备、隔离、队列、预算与原生 agent 支持。
- [契约回归](../aspire/sim/tests/test_prediction_contract.py)、[准备流程回归](../aspire/sim/tests/test_prediction_study_support.py)：CPU 测试及合成 fixture。

求解器记录保持 local Qwen3.8-Flash-Next、native CC 2.1.220、xhigh、1M context、64K 配置输出。开发 seeds 51–65，原 per-seed 尝试预算，冻结后外层评估 1–50；真实感知和 nonprivileged API 不变。任务规模、窗口与资源配置以各轮 PLAN 和代码为准，不把历史授权视为新任务授权。

## 当前支持协议

| 目录 | 作用 |
| --- | --- |
| [Gate ablation 2026-10-05](../aspire/sim/docs/experiments/code-world-gate-ablation-20261005/PLAN.md) | oracle / selfeval / vlmjudge admission、独立 cell 与共享队列；p1 固定 gate 为 oracle。 |
| [Closed-loop R3 2026-10-07](../aspire/sim/docs/experiments/code-world-qwen-closed-loop-r3-20261007/RUN_PLAN.md) | 三任务协议、报告收尾、超时/终态与预算边界。 |
| [Pipeline repair 2026-10-05](../aspire/sim/docs/experiments/code-world-pipeline-repair-20261005/NEXT_RUN_PLAN.md) | 图像接口与动作契约修复；[motion contract](../aspire/sim/docs/experiments/code-world-pipeline-repair-20261005/MOTION_CONTRACT_REVIEW.md)。 |
| [Closed-loop repair 2026-10-04](../aspire/sim/docs/experiments/code-world-closed-loop-repair-20261004/IMPLEMENTATION.md) | 世界判断与策略决策链及其可观测证据。 |
| [Foundation R3 2026-10-02](../aspire/sim/docs/experiments/code-world-qwen-foundation-r3-20261002/IMPLEMENTATION.md) | foundation 与 pipeline 兼容修复。 |
| [Foundation R2 2026-09-30](../aspire/sim/docs/experiments/code-world-qwen-foundation-r2-20260930/IMPLEMENTATION.md) | 离线策略退出报告与 worker → coordinator 报告落盘。 |
| [Foundation R1 2026-09-30](../aspire/sim/docs/experiments/code-world-qwen-foundation-20260930/IMPLEMENTATION.md) | observed state、缺测 unknown、开发集自评审计及使用证据。 |

旧协议和支持源码保留原路径。日志、launch receipts、原始结果、对话记录、冻结生成轨迹与媒体仍在原存储，不进入 Git。历史文档中的状态、远端绝对路径和授权只属于原轮次。对齐范围与文件身份见 [SYNC_SCOPE](SYNC_SCOPE.md) 和 [source-manifest](source-manifest.json)。
