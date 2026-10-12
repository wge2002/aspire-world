# 最新源码与实验协议入口

当前 RS 接手入口：[2026-10-12 交接文档](RS_HANDOFF_20261012.md)。
工程目录 `rs:/root/github/aspire-world`，GitHub 分支 `SZ-DEV`；
实验准备与真实启动状态分别核对，不能因已有配置目录宣称任务已运行。

## Articulated Streaming World v3（2026-10-12）

- [关节模型与在线 Qwen 复查](../aspire/sim/docs/articulation-streaming-world.md)：显式方法 `sam2-articulation-qwen-v3`，用公开 RGB-D 对应点估计部件相对运动，拟合固定／滑动／转动模型，并处理身份冲突、可信交互矛盾和模型预测矛盾。
- [运动模型](../aspire/sim/cap/world_model/joint_models.py)、[World 状态](../aspire/sim/cap/world_model/articulated_world.py)及[复查接口](../aspire/sim/cap/world_model/qwen_world_review.py)：Qwen 提出结构和模型族，数值验证决定参数是否可用；证据不足保持未知。
- RS 最小真实链路已通过；用户授权优先在 RS 跑 drawer → stove → wine，
  当前正式启动情况见交接与各cell账本。尚无v3正式成功率；旧v1/v2冻结实验保持原身份。

## Semantic Streaming World v2（2026-10-11）

- [部件语义与交互更新接口](../aspire/sim/docs/semantic-streaming-world.md)：显式方法 `sam2-semantic-v2`，包括场景中生成的部件假设、SAM2 部件跟踪与身份关联、公开动作反馈及带证据的结构修订。
- [实现](../aspire/sim/cap/world_model/semantic_world.py)与[动作反馈](../aspire/sim/cap/world_model/motion_feedback.py)：沿用共同任务、模型、seed、预算和评分条件；新增机制单独声明为方法因素。
- 初始验证范围为 drawer、bowldrawer 各一个独立的新方法 cell；不是完整十-cell 比较。旧冻结代码、账本与结果保持原实验身份。

## RS Streaming World 共同协议（2026-10-10）

- [共同开发与评估协议](../aspire/sim/docs/libero-pro-common-protocol.md)及[机器可读约束](../aspire/sim/docs/libero-pro-common-protocol.json)：固定五个 Goal-Swap 任务、C 条件、local Qwen/native CC 2.1.220、开发 51–65 每 seed 三次总尝试、冻结测试 1–50，以及 900 秒/14 小时/5 小时 watchdog。
- [多节奏 Streaming World 实现](../aspire/sim/docs/streaming-world.md)：通用工程 world、SAM2_streaming 与公开感知链路是需明确声明的方法因素。

本条实验线按每方法 10 个独立 cell 设计；协议和实现入口不表示服务已就绪、实验已完成或全部 cell 已启动。历史研究保持原身份与冻结输出。

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
