# 最新实验与代码入口

当前最新源码基线是 **2026-09-30 Qwen Code World foundation R2**（2026-10-01 同步）。此索引描述本地记录的实验设计和实现，不宣称当前远端任务已完成，也不复制运行产物。

## Foundation R2（2026-09-30）

R2 保持 foundation 1/2/3/8 方法、Qwen 设置和 seed 预算，修复两项基础流程问题：离线策略 `SystemExit(None/0)` 仍保存 unknown/unsupported 报告，非零退出按程序错误记账；worker 返回完整报告，由 coordinator 原样写入 `findings.md` 后再检查完成条件。干净退出不视为任务成功。

- [PLAN](../aspire/sim/docs/experiments/code-world-qwen-foundation-r2-20260930/PLAN.md) 与 [IMPLEMENTATION](../aspire/sim/docs/experiments/code-world-qwen-foundation-r2-20260930/IMPLEMENTATION.md)：本轮范围、修复及原实施阶段验证记录。
- [NATIVE_WORLD_INTERFACE](../aspire/sim/docs/experiments/code-world-qwen-foundation-r2-20260930/NATIVE_WORLD_INTERFACE.md)：沿用的 world 接口契约。
- [prepare-foundation.py](../aspire/sim/docs/experiments/code-world-qwen-foundation-r2-20260930/prepare-foundation.py) 与 [support/](../aspire/sim/docs/experiments/code-world-qwen-foundation-r2-20260930/support/)：本轮准备和执行支持。
- [qwen-native-compat-r2.py](../aspire/sim/docs/experiments/code-world-qwen-foundation-r2-20260930/support/qwen-native-compat-r2.py)：加入报告交接与落盘的 native 兼容检查。
- [test_foundation_r2_repairs.py](../aspire/sim/tests/test_foundation_r2_repairs.py)：12 个 CPU 回归用例，覆盖退出分类、模块恢复、中断、报告边界和预算耗尽后的完成流程。

以下保留 R1 的方法说明与支持来源。两轮原始运行记录、逐 seed 结果、日志和媒体都不纳入 Git。

## Foundation R1（2026-09-30）

- [PLAN](../aspire/sim/docs/experiments/code-world-qwen-foundation-20260930/PLAN.md)：三个独立 C/full 任务，优化项 1/2/3/8 与后续未实施项。
- [IMPLEMENTATION](../aspire/sim/docs/experiments/code-world-qwen-foundation-20260930/IMPLEMENTATION.md)：统一 observed state、缺测 unknown、开发集自评审计、world 使用证据。
- [NATIVE_WORLD_INTERFACE](../aspire/sim/docs/experiments/code-world-qwen-foundation-20260930/NATIVE_WORLD_INTERFACE.md)：模型生成 world 的接口契约。
- [ACCEPTANCE](../aspire/sim/docs/experiments/code-world-qwen-foundation-20260930/ACCEPTANCE.md)：验收约束。
- [prepare-foundation.py](../aspire/sim/docs/experiments/code-world-qwen-foundation-20260930/prepare-foundation.py) 与 [support/](../aspire/sim/docs/experiments/code-world-qwen-foundation-20260930/support/)：分阶段准备、驱动、隔离和记账。
- [test_world_foundation.py](../aspire/sim/tests/test_world_foundation.py)：状态一致性、缺测、证据身份与开发/held-out 边界的行为测试。

三个任务为 bowl on plate、bowl in top drawer、open middle drawer。记录中的 solver 是 native CC 2.1.220 + local Qwen3.8-Flash-Next，xhigh、1M context、64K 配置输出；development 51–65，最多每 seed 三次计费执行，冻结后由外层评估 held-out 1–50。真实感知与 nonprivileged API 保持不变。

## 保留的直接支持来源

- [2026-09-26 two-task](../aspire/sim/docs/experiments/code-world-qwen-two-task-20260926/PLAN.md)：多任务准备与恢复支持源码。
- [2026-09-24 full](../aspire/sim/docs/experiments/code-world-qwen-full-20260924/PLAN.md)：完整 native Fix Loop 驱动和容量修复。
- [2026-09-22 debug](../aspire/sim/docs/experiments/code-world-qwen-debug-20260922/PLAN.md)：Qwen 兼容层与服务配置。
- [2026-09-20 C optimization](../aspire/sim/docs/experiments/code-world-c-opt-ablation-20260920/PROTOCOL.md)：接口与兼容支持。

这些目录仅保留协议、配置、源码和测试。旧的日志、逐 seed 结果、launch receipts、对话记录、视频和冻结生成结果未纳入。运行状态请回到原实验存储核验。
