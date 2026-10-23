# 最新源码与实验协议入口

源码对齐日期：2026-10-23。当前入口是 **p6d 方法修复、p6c pipeline 修复、p6b worker 恢复与 p4–p6 协议**，以及 Qwen 4-cell unit 启动层。本页区分实现、历史验证和设计提案；原始运行结果不入 Git。

## 最新方法与运行链（2026-10-23）

| 入口 | 发布内容与边界 |
| --- | --- |
| [p6d 方法修复](../aspire/sim/docs/experiments/code-world-method-repair-p6d-20261022/PLAN.md) | `method_switches` 显式启用 M2/M3/M4：sweep-only 版本评分、冻结前重扫、unseen 66–80、子目标验证、judgment calibration、record-only screening、m4-r1 动作接口及 LIBERO success latch。含 prepare、host gate、启动和验收工具；代码收录不等于新 smoke / trial 已完成。 |
| [p6c pipeline 修复](../aspire/sim/docs/experiments/code-world-pipeline-repair-p6c-20261022/README.md) | worker API failure 恢复、watchdog 终态归因、完成后持久化宽限与外层 close-infra；保留支持源码。 |
| [p6b worker 恢复](../aspire/sim/docs/experiments/code-world-worker-recovery-p6b-20261021/PLAN.md) | 原 worker 无法恢复时的新 generation、失败轮次与基础设施收尾；保留 [l10](../aspire/sim/docs/experiments/code-world-worker-recovery-p6b-20261021-l10/PLAN.md) / [ns](../aspire/sim/docs/experiments/code-world-worker-recovery-p6b-20261021-ns/PLAN.md) 协议。 |
| [p6 并行与收尾](../aspire/sim/docs/experiments/code-world-repair-parallel-p6-20261020/PLAN.md) | held-out 与免费 phase-B sweep 并行、进程树清理、累计开发截止和冻结收尾。N 是 cell 内 replay 并发，区别于节点的 4 个 cell 槽位。 |
| [p5 开发预算](../aspire/sim/docs/experiments/code-world-budget-p5-20261019/PLAN.md) | `aspire-v1` opt-in：phase A seed 51 至首次成功，phase B 至多 8 版本 / 每 mode 至多 3 版本；开发累计期限 24h。 |
| [p4 覆盖门控](../aspire/sim/docs/experiments/code-world-coverage-p4-20261016/PLAN.md) | 冻结版本的开发集覆盖、terminal guard、held-out supplement；另保留 p4n / p4fix 准备脚本。PLAN 的初始 design 状态属于当时记录，现行实现以代码与后续协议为准。 |
| [方法诊断](../aspire/sim/docs/experiments/code-world-method-diagnosis-20261022/REPORT.md) | 方法问题与修复依据的整理文档；原始证据仍在计算存储。 |
| [跨任务技能库设计](../aspire/sim/docs/experiments/code-world-skill-library-design-20261022/DESIGN.md) | 设计及候选整理，尚不是已接入的技能库；排期和提案不构成启动授权。 |

共享实现入口：[method_switches.py](../aspire/sim/scripts/libero/method_switches.py)、[judgment_calibration.py](../aspire/sim/cap/world_model/judgment_calibration.py)、[子目标验证契约](../aspire/sim/cap/world_model/skills/subgoal-verification.md)、[m4-r1 接口说明](../aspire/sim/cap/world_model/skills/api-revision-m4.md)、[p6d 回归](../aspire/sim/tests/test_p6d_method.py)。

各阶段通过 case 的显式字段选择行为；旧冻结运行不会因同步源码而更新。p5 及后续预算与历史每 seed 三次的 profile 不同，以对应 PLAN 和 case 为准。p6d 的 unseen 测量在冻结后由外层执行，仍与 worker 开发材料及 held-out 1–50 分离。

节点启动层新增 session/后代进程清理、失败 unit teardown、基础设施中断归档后有限重新排队、节点 cell 分配与按 case 并发推导 deadline；见 [清理回归](../aspire/sim/tests/test_node_layout_teardown.py)。这些属于现行源码；下文 10-16 smoke 是旧快照的历史验收，不能替代最新版本的真实运行验收。

BEHAVIOR 仿真同步了 runner / r1pro 的抓取状态检查和启动辅助脚本，入口为 [grasp_checks.py](../aspire/sim/cap/integrations/r1pro/grasp_checks.py)、[回归](../aspire/sim/tests/test_r1pro_grasp_checks.py)及 [b1k_env.sh](../aspire/sim/scripts/behavior/b1k_env.sh)；不包含真实机器人 workspace。

## 节点布局（2026-10-15 起）

今后所有实验只用这一种启动逻辑（用户 2026-10-15 决定；契约 [DESIGN](../aspire/sim/docs/engineering/node-layout-20261015/DESIGN.md)）：

- **每 4 卡一个 unit**：1 个 Qwen TP4 服务（CUDA graph 配置，util 0.82，见 [QWEN](QWEN.md#实验服务配置2026-10-15-起)）+ SAM3 + GraspNet + PyRoKi(CPU) + **2 个 cell 槽位**；感知与模拟器放在本 unit 的四张 Qwen 卡上（unit 内相对卡号：SAM3 0、GraspNet 1、槽位 a 模拟器 2、槽位 b 模拟器 3）。
- 8 卡节点 = 2 个 unit（GPU 0–3、4–7）= 同时 4 个 cell；4 卡 DLC 作业 = 1 个 unit = 2 个 cell。其他卡数被入口拒绝。
- 端口：unit 0 为模型 8121、SAM3 8114、GraspNet 8115、PyRoKi 8116；unit u 每项加 10u。
- 每个槽位从共享队列原子认领 cell（`mkdir` 认领），一个 cell 全程绑定一个 unit；槽位绑定（模型 endpoint、各服务 URL、模拟器 GPU/EGL）写入该 cell 的 `slot-case.json`、认领记录与 `queue-result.json`。unit 之间独立健康检查与 ServiceWatch，一个 unit 故障只把它的 cell 记为 infrastructure。
- 实现：[`scripts/common/node_layout/`](../aspire/sim/scripts/common/node_layout/)（`unit_layout.py`、`unit_supervisor.py`、`unit-entry.sh`、`reference/model-server.json`）；新研究的 prepare 用 `unit_layout.freeze_launch()` 把它冻结进结果根的 `launch/`，不再继承 p3 的 `support/dlc-supervisor.py`。测试：[test_node_layout.py](../aspire/sim/tests/test_node_layout.py)。
- 已删除的旧逻辑：eager 模型配置、`expandable_segments`、“一节点一 cell、Qwen 0–3 / SAM3 4 / GraspNet 5 / sim 6 / 卡 7 空闲”的布局。历史研究目录及其冻结的 `support/`、`launch/` 保持原样，只代表当时的运行。

## 历史 4-cell 验收与停止逻辑（2026-10-16）

- `native_smoke` 由 supervisor 观察各 cell 自己的账本：第一条 charged trial 到终态后清理该 cell 的进程树并记录 `supervisor_smoke_stop`，不再依赖模型遵守暂停提示。正在运行的首条 trial 不会为此中断；`full` 模式保持原来的完成条件。
- `--preflight` 完成检查后退出，不向结果根写入；节点时间窗不足以容纳启动、一个 cell 和清理时直接拒绝启动。
- 已独立核实真实 8 卡 / 两 unit / 四 cell smoke：每个 cell 只有 snapshot 与一次 charged seed-51，四个队列终态均 `native_smoke_complete`，没有停止后的额外 trial，进程树清理通过。此前 10-15 的暂停越界记录保留在原存储，已由本次修复及新 smoke 验证覆盖。
- smoke 验收的是编排机制；四个首次 bowl trial 的 reward 均为 0，其中一个 sandbox_rc 为 1。完整开发与 50-seed held-out 流程未在这次 smoke 中运行。GraspNet 功能请求成功，但没有逐卡峰值采样，显存余量仍未定量验证。
- [smoke 准备示例](../aspire/sim/docs/engineering/node-layout-20261015/smoke/prepare-smoke-20261016.py)、[采样与收集源码](../aspire/sim/docs/engineering/node-layout-20261015/smoke/)保留原轮次路径；复用前必须为新运行设定独立输出，不能重用已有结果根。新研究还需自己的 prepare / study.json。槽位绑定后 worker prompt 会重渲染，后续 prepare 应固定绑定后的 prompt 身份；本次实际绑定已核验。

## Search control p3（2026-10-13）

- [PLAN](../aspire/sim/docs/experiments/code-world-search-control-p3-20261013/PLAN.md)：历史轮次的设计与后续决策记录；原研究的冻结启动链保留，未来运行使用上面的 unit 层。
- [search_control.py](../aspire/sim/cap/world_model/search_control.py)：初始批次签名提前停止、primitive class 饱和与切换、显式 replicate 记账。
- progress 缺测返回 `None`；未重新观测目标、没有已判定对象预测的尝试不作为饱和证据。重命名 class 而不改变策略有独立审计记录。
- [回归测试](../aspire/sim/tests/test_search_control.py)与[prepare 源码](../aspire/sim/docs/experiments/code-world-search-control-p3-20261013/prepare-search-study.py)。历史研究的运行路径与输出不迁移到新布局。

## Runtime world p2（2026-10-11）

- [接口契约](../aspire/sim/cap/world_model/RUNTIME_INTERFACE.md)与[PLAN](../aspire/sim/docs/experiments/code-world-runtime-p2-20261011/PLAN.md)。
- [状态模板](../aspire/sim/cap/world_model/runtime_state.py)、[预测生命周期](../aspire/sim/cap/world_model/runtime_predictions.py)、[使用审计](../aspire/sim/cap/world_model/runtime_mechanism_audit.py)：以实际公开感知维护对象、坐标系、单位、新鲜度和 unknown；预测不填充实测状态。
- [机制测试](../aspire/sim/tests/test_runtime_world.py)、[编排测试](../aspire/sim/tests/test_runtime_study_support.py)。

## Prediction contract（2026-10-08）

- [PLAN](../aspire/sim/docs/experiments/code-world-prediction-base-20261008/PLAN.md)：比较 `off` 与 `p1`；5 个任务 × 2 arms × 2 repeats。
- [NATIVE_WORLD_INTERFACE](../aspire/sim/docs/experiments/code-world-prediction-base-20261008/NATIVE_WORLD_INTERFACE.md)：预测事实、实测证据、match / mismatch / unknown / unresolved 与 policy 查询契约。
- [prediction_contract.py](../aspire/sim/cap/world_model/prediction_contract.py)：将每次调用前的预测与后续真实 observed layer 对齐；policy 自行决定如何使用，框架不自动执行恢复动作。
- [prediction_study_analysis.py](../aspire/sim/scripts/libero/prediction_study_analysis.py)：开发集预测/使用统计与冻结评估分析入口。
- [prepare-prediction-study.py](../aspire/sim/docs/experiments/code-world-prediction-base-20261008/prepare-prediction-study.py)、[support](../aspire/sim/docs/experiments/code-world-prediction-base-20261008/support/)：准备、隔离、队列、预算与原生 agent 支持。
- [契约回归](../aspire/sim/tests/test_prediction_contract.py)、[准备流程回归](../aspire/sim/tests/test_prediction_study_support.py)：CPU 测试及合成 fixture。

上述 p1–p3 历史求解器记录保持 local Qwen3.8-Flash-Next、native CC 2.1.220、xhigh、1M context、64K 配置输出。开发 seeds 51–65，原 per-seed 尝试预算，冻结后外层评估 1–50；真实感知和 nonprivileged API 不变。任务规模、窗口与资源配置以各轮 PLAN 和代码为准，不把历史授权视为新任务授权。

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
