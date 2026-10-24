# 最新源码与实验协议入口

2026-10-19 用户授权按 wine 复盘方案修复，并在朱子悦项目 `1493863` 新跑一项
wine 修复研究，开发成功且通过普通冻结检查后才测 held-out 1–50；旧实验保留。
2026-10-24 同步核对：该研究已实际启动，并于 10 月 19 日 14:42（北京时间）
被用户停止；开发已用 18/45 次，正常成功 0 次，held-out 未开始。
停止回执与保留账本哈希一致，遗留的 raw running 标记不表示仍在执行。
新研究以 wine r1 自己的开发材料为起点，另记最多三轮各 15 次的新预算，
旧 45 次保留；不引入其他 repeat 或测试反馈。
本轮使用一节点八卡、同一本地 Qwen、独立自动复盘/收尾，正常 900秒/14小时/5小时
watchdog 和成功/冻结门槛不变。见 [Wine 修复轮](DLC_WINE_REPAIR_20261019.md)。

2026-10-17 用户改为朱子悦项目 workspace `1493863` 的一个八卡节点，确认
stove/wine 各两个重复：r1 保留各 15 次，只跑剩余两轮；r2 独立从头生成，
各最多三轮。默认空间旧作业 `dlcg4a7t7ff5ox1g` 已确认 Stopped，零新增次数。
首趟 `dlc12rui2zglkj7r` 完成模型/感知预检后，11:32 因各 HOME 缓存目录
为 root 所有而在实验初始化前退出，零新账本、时钟或尝试。已修复各用户
缓存权限，四个真实 UID 导入通过，旧日志完整归档。
11:42:19 已重提同一四项接续作业 `dlc7fct8r6v95slf`，11:43:36 Running，
11:44:21节点attempt02开始预检，模型、感知及加载后容量检查通过；四项于
11:58:57实际初始化并登记 R08/V07。23:14最终核对：两个r2已自动完成第3轮，
通过当前候选成功门槛与正常冻结检查，且完整执行held-out seeds1–50。
wine r2于19:44:05完成，stove r2于20:12:21完成；20:12:40节点完成共享服务清理。
stove r1用尽剩余30次后开发失败、按门槛跳过测评。wine r1因缺11份逐seed失败说明，
15:01退出为stage1_incomplete，未测。平台整作业Failed对应两个r1退出码1；
两个r2均退出码0，成绩与未测项分别记录于协调器私有文档，查询不重启已结束作业。
本阶段开发截止为10月18日01:58:57，源码、cases、运行身份和累计预算保持。
137 项检查与四个真实用户隔离预检通过，见[本次记录](DLC_UNIT8_FOUR_20261017.md)。

此前 2026-10-16 用户在本机资源检查后，改为授权一个默认空间 DLC 四卡节点
继续 stove / wine 剩余两轮；各保留父已用 15 次、最多再用 30 次。
四卡共卡启动器、独立冻结版本和提交前预检已通过（116 项 CPU 回归）。
23:29 已提交 `dlcg4a7t7ff5ox1g`；2026-10-17 10:54（北京时间）复核仍在排队，
约 11 小时 25 分，未分配节点，无新账本、时钟或尝试，开发尚未开始；
状态与路径见 [四卡续跑记录](DLC_UNIT4_CONTINUATION_20261016.md)。本机不启动本项目服务。

2026-10-16 已从 main 的 [401a5ba](https://github.com/wge2002/aspire-world/commit/401a5ba23feabe1a6a46ea2a301f14c1f897d846)
单独接入 Qwen decode CUDA graph 速度修复；配置转换器和现有 DLC 启动器
的 CPU 验证通过，未启动模型或实验。配置与后续接入步骤见 [Qwen 部署](QWEN.md)。
已取消的旧 DLC 任务仍保持停止，冻结运行和已有尝试记录保持原身份。

开发历史、方法分层、验证结果与后续执行顺序见
[Code as World 开发交接](CODE_AS_WORLD_HANDOFF_20261012.md)。
最新运行授权见 [stove / wine 剩余两轮自动接续](DLC_AUTONOMOUS_CONTINUATION_20261015.md)：
用户于 2026-10-15 要求修复两项代码与自主复盘，保留各已用 15 次，仅继续
第 2、3 轮（各最多再 30 次），默认空间 DLC，另记修复阶段时钟。已
完成工程修复与部署预检；2026-10-15 20:31 提交的
`dlc5zw1sbv3vzijn`（默认空间 `1000309`，1×8 GPU）于 22:43 按用户要求取消，
确认 StoppedByUser；取消前一直排队、零节点，没有新尝试或阶段时钟。
准备版本保留，速度修复已接入工程源码，不恢复实验。以下为父研究的终止记录。
父研究见 [stove / wine 分轮复盘修复研究](DLC_SUPERVISED_REPAIR_20261014.md)。
用户在暂停后授权修复并新开这两个任务，每任务至多三轮各 15 个开发 seed，
轮间由协调端分析，仅当前所选程序有开发 oracle 成功且通过冻结检查才测评。
2026-10-14 14:11 已提交新 DLC `dlcxa3n63xgkmj21`，默认空间 `1000309`、
一个 8 GPU 节点；14:13 节点入口开始执行真实预检。14:24 两项 campaign
启动，Qwen / 感知服务与模型加载后的 800×512 GraspNet 请求已通过。
14:29:58 原生模型探测通过、账本初始化。10 月 15 日 15:48 已核对终态：
两项各完成第 1 轮 15 次，等外层复盘至 04:29 开发截止，04:30 节点退出；
第二、三轮未执行，50 seed 测评均跳过。stove 10 正常失败、5 报错（其中
seed 57 触发成功判定但因报错不合格）；wine 14 正常失败、1 超时。
复盘未接续是协调流程缺口，不能当作已跑满三轮。新旧研究分开记账。
旧运行记录见 [默认空间四-cell 修复续跑](DLC_REPAIR_CONTINUATION_20261013.md)。
各轮任务成绩、Git 基线与实际冻结版本见[实验结果记录表](EXPERIMENT_RESULTS.md)
（仅协调端/用户，不提供给实验求解器）。
上一轮 `1493863` 的 DLC 任务已停止，共 62 次开发尝试、2 次开发 oracle 成功，
尚未冻结测试。2026-10-13 用户授权在默认空间 `1000309` 接续各自历史，
累计尝试次数不重置，并取消新轮整体五小时上限。新作业 `dlc6eu0wbbqxatvx`
2026-10-14 用户要求暂停。12:01 StopJob 被接受，12:03 确认作业和两 Pod
均为 Stopped；不恢复或新开实验，直到用户之后明确授权。
drawer r1 保留 43/50（86%）。stove 为 14 失败、1 暂停中断、35 未测；
wine 为 1 失败、1 暂停中断、48 未测。两者均无完整最终测试成绩，主表保持“—”。
drawer r2 继承 seed52 三次耗尽却无有效评分的冻结阻塞，开发未通过并已退出。
原生 1M 路由、累计次数及各 cell 隔离保持。
真实状态以停止回执和账本为准。冻结结果、开发预算和时钟保留，本机对应
ASPIRE 服务保持停止。原因分析已记录在实验明细；新修复研究的真实启动
状态以新部署记录为准，不能从准备目录推断已经运行。

当前 RS 接手入口：[2026-10-12 交接文档](RS_HANDOFF_20261012.md)。
工程目录 `rs:/root/github/aspire-world`，GitHub 分支 `SZ-DEV`；
实验准备与真实启动状态分别核对，不能因已有配置目录宣称任务已运行。

## Articulated Streaming World v3（2026-10-12）

- [关节模型与在线 Qwen 复查](../aspire/sim/docs/articulation-streaming-world.md)：显式方法 `sam2-articulation-qwen-v3`，用公开 RGB-D 对应点估计部件相对运动，拟合固定／滑动／转动模型，并处理身份冲突、可信交互矛盾和模型预测矛盾。
- [运动模型](../aspire/sim/cap/world_model/joint_models.py)、[World 状态](../aspire/sim/cap/world_model/articulated_world.py)及[复查接口](../aspire/sim/cap/world_model/qwen_world_review.py)：Qwen 提出结构和模型族，数值验证决定参数是否可用；证据不足保持未知。
- RS 与 DLC 最小真实链路已通过；用户最新授权优先在 DLC 跑 drawer、stove、wine 及 drawer 独立重复，
  当前正式执行情况见交接与各 cell 账本；修复续跑 drawer r1 已得到完整的
  43/50 冻结成绩，其余未完成任务保留状态。旧 v1/v2 冻结实验保持原身份。

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
