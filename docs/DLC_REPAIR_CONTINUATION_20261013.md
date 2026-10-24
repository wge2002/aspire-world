# 默认空间 v3 修复续跑（2026-10-13）

## 用户暂停：2026-10-14 12:01（北京时间）

用户要求“不要跑了，暂停任务，分析原因”。StopJob 在 12:01 被接受；
12:03 实时核对 DLC `dlc6eu0wbbqxatvx` 状态为 Stopped / StoppedByUser，
master 与 worker 两 Pod 都为 Stopped。CPU 管理汇总进程也已退出。
不恢复这轮或开启新实验，直到用户之后明确授权。

| 实验 | 暂停时结果 | 未测试 | 完整测试成功率 |
| --- | --- | --- | --- |
| drawer r1 | 43 成功、6 失败、1 crash | 0 | 86% |
| stove r1 | 0 成功、14 失败、1 暂停中断 | 35 | 未完成 |
| wine r1 | 0 成功、1 失败、1 暂停中断 | 48 | 未完成 |
| drawer r2 | 开发 terminal blocker，未冻结 | 50 | 未完成 |

两次在途测试已由运行器记录为 infrastructure_error，无 running 行；
不将中断冒充任务失败，不补跑或重置任何预算。四个开发账本仍是
本轮/累计 28/45、37/45、29/45、1/22；原时钟和冻结版本保留。
停止前快照、API 回执、终态确认与暂停汇总在：
`/mnt/workspace/aspire-world-deployment-20261013-dlc-default-v3-repair/operator-pause-20261014-120120/`。
停止后调用原协议 `report` 函数生成两份 incomplete held-out 结果，只读取
已有状态，不执行 seed；检查四份开发账本和 drawer r1 完整报告字节未变。
节点 pair_state / campaign_state 部分仍保留旧状态，停止回执和实时平台终态
是本次停机证据，不能由这些旧文件推断仍在运行。

暂停后的表征/控制原因分析见[实验明细](EXPERIMENT_RESULTS_DETAILS.md)。

## 最新状态：2026-10-14 11:25（北京时间）

11:24 GetJob 实时复核任务仍为 Running，默认空间 `1000309`，两节点健康
记录更新正常。stove / wine 的累计开发次数均已耗尽，15 个开发 seed
均无本轮任务成功；不能继续追加开发尝试。

| 实验 | 实际状态 | 本轮 / 累计开发尝试 | 本轮成功开发 seed 数 | 冻结测试 |
| --- | --- | --- | --- | --- |
| drawer r1 | 已完成 | 28 / 45 | 13 | 43 / 50，86% |
| stove r1 | 冻结测试中 | 37 / 45 | 0 | 已完成 7，成功 0；第 8 个正在执行 |
| wine r1 | 开发结束，原生会话收尾中 | 29 / 45 | 0 | 尚未开始 |
| drawer r2 | 开发阻塞，已退出 | 1 / 22 | 0 | 尚未开始 |

stove 已完成开发处理、选定候选并产生 stage1_result；同一个 bundle
`d4ed1082eb6bae96d61f7fad92c9529c7f0dcb568cc62c3c856b87ac98ade36a`
用于冻结测试。seeds 1–7 都是正常执行但未完成目标的 failure，seed 8
正在运行，42 个未开始。测试阶段从 10:45:25 开始，原五小时 evaluation
watchdog 截止 15:45:25；这与已取消的整个 DLC 五小时上限是两个不同预算。
当前不发布完整测试成功率，简表保持“—”。

wine 最后一个开发执行 seed 51 / attempt 3 已正常结束、reward 0。
本轮 28 条 complete、1 条 infrastructure_error，累计预算全用完；
选择了本轮测试过的候选，但尚无 stage1_result、held-out 账本或最终测试率。
目前本轮新增开发执行 95 次，加父轮共 157 次；开发和冻结测试分开统计。
旧 wine seed 65 的历史成功不算本轮成功。失败日志已产出，不是缺少反馈。

成功率简表、明细和导航已更新；冻结源码、已评分结果、账本和时钟未修改。

## 最新状态：2026-10-14 10:37（北京时间）

10:34 GetJob 实时确认 `dlc6eu0wbbqxatvx` 仍为 Running，默认空间
`1000309`，整体时长上限 `0`；两节点健康记录更新至本分钟。
10:37 账本复核如下，完整冻结成绩尚无新增。

| 实验 | 实际状态 | 本轮 / 累计开发尝试 | 本轮成功开发 seed 数 | 冻结测试 |
| --- | --- | --- | --- | --- |
| drawer r1 | 已完成 | 28 / 45 | 13 | 43 / 50，86% |
| stove r1 | 开发次数耗尽，原生会话收尾中 | 37 / 45 | 0 | 尚未开始 |
| wine r1 | 开发运行中 | 25 / 41 | 0 | 尚未开始 |
| drawer r2 | 开发阻塞，已退出 | 1 / 22 | 0 | 尚未开始 |

stove 所有 15 个开发 seed 均累计执行三次，剩余执行预算为零；已选择一个
本轮测试过的候选并保存 findings，worker 仍在向 coordinator 完成汇报。
尚无 coordinator check / stage1_result 或 held-out 结果，不越过正常收尾。
wine 当前执行 seed 55 的累计第 3 次，尚未评分；剩余 seeds 51–54
各一次，共四次，不能跨 seed 挪用。计入在途执行的本轮总尝试为 91 次，
加父轮累计 153 次；冻结测试另外有 drawer r1 的 50 次。
开发时钟、累计预算、runtime identity 和冻结结果均未修改。
成功率简表及逐任务明细已同步更新，未测试单元仍用“—”表示。

2026-10-14 09:03（北京时间），用户确认继续现有实验。实时复核作业仍为
Running、默认空间 `1000309`、两个八 GPU worker、整体时长上限 `0`；
两节点健康记录均已更新至本分钟。stove 正在执行 seed 52 的累计第 3 次
尝试（本轮 / 累计为 27 / 35），wine 正在执行 seed 64 的累计第 3 次尝试
（16 / 32）。两次均处于 running，尚无可报告结果。现有会话自主推进，
管理汇总进程仍在运行；不重复启动、不重置账本或开发时钟。
drawer r1 已完成的冻结结果保留，drawer r2 保持 terminal 停止。

## 结果快照：2026-10-14 08:53（北京时间）

实时 GetJob 确认作业 `dlc6eu0wbbqxatvx` 仍为 Running，默认工作空间
`1000309`、quota `quotaabjvnuk8san`，两个八 GPU worker，整体时长上限为
`0`。以下是未结束修复轮的中途结果，不代表四个实验均已完成。

| 实验 | 实际状态 | 本轮 / 累计开发尝试 | 本轮出现成功的开发 seed 数 | 冻结测试 |
| --- | --- | --- | --- | --- |
| drawer r1 | 完成，2026-10-14 07:42 | 28 / 45 | 13 | 43 / 50，86% |
| stove r1 | 原生开发会话仍运行 | 26 / 34 | 0 | 尚未开始 |
| wine r1 | 原生开发会话仍运行 | 15 / 31 | 0 | 尚未开始 |
| drawer r2 | 开发未通过，2026-10-14 05:06 已退出 | 1 / 22 | 0 | 尚未开始 |

本轮新增 70 次开发执行，连同父轮共 132 次；冻结测试另有 50 次。
开发阶段跨版本的成功 seed 数不等于最终冻结程序的开发成功率。
drawer r1 最终冻结版本自身在本轮开发的 8 个 seed 上得到 8 次成功，
对应 bundle SHA256 为
`56ff803cc5e00d66d17ceb7bccbaed6bf51254e08aac60e93f2652615ecd4990`。
核对 held-out seeds 恰为 1–50，全部使用该 bundle，没有未运行或未决试验。
正式结果为 success 43、failure 6、crash 1、infrastructure_error 0。
失败 seed 为 14、24、25、32、47、48；崩溃 seed 36 的记录是启动感知
`StreamingSeedError: rank_unavailable`，柜体提示词未提供所需候选。
不能利用这些 held-out 记录回头修改本次冻结版本。

stove 的 26 次新执行均有终结记录，其中 25 次程序正常结束但未完成目标，
1 次程序非零退出；wine 的 15 次新执行为 14 次正常结束但未完成目标、
1 次基础设施错误。最新 stove seed 55 / attempt 3 仍未形成任务成功，
旋钮观测位置有明显跳变；最新 wine seed 65 / attempt 3 已完成搬运和
放置动作，但最后观测的瓶体位于架外近桌面高度，未得到任务成功。
这是最新日志证据，不是对全部失败的完整归因。

drawer r2 的 stage1 check 为 terminal：父轮 seed 52 已累计用完三次
尝试且没有有效评分，新修复程序无法获准执行；本轮仅补齐了 seed 56
最后一次初始执行，其程序非零退出。不得重置、退还次数或将父轮
seed 61 的历史成功算作修复后验证。已保存未执行的修复代码供后续研究。

stove 尚有 11 次、wine 尚有 14 次累计尝试余量，分属各自未耗尽的 seed，
不能跨 seed 挪用。它们的开发 watchdog 分别截止北京时间 13:13:37 和
13:14:14；整体五小时上限仍然取消。节点健康记录已更新至 08:51，
两个节点分别仍有 stove / wine 会话运行。

原始结果与状态：

- `/mnt/workspace/aspire-world-deployment-20261013-dlc-default-v3-repair/progress-summary.md`
- `/mnt/workspace/aspire-world-runs/dlc-default-v3-repair-20261013/drawer_sam2articulationrepair20261013_r1/cell/stage1_result.json`
- `/mnt/workspace/aspire-world-runs/dlc-default-v3-repair-20261013/drawer_sam2articulationrepair20261013_r1/heldout/heldout_result.json`
- 四个 control 目录各自的 `campaign_state.json`、`cell/development_state.json`；
  `entry/rank0/pair_state.json`、`entry/rank1/pair_state.json`。

## 运行配置与启动记录

用户授权将上一轮 drawer r1、stove r1、wine r1、drawer r2 的各自代码和
开发记录用于后续修复轮，迁往默认工作空间 `1000309`，quota
`quotaabjvnuk8san`。明确不使用 `1493863`。仍为两个八 GPU 节点，总计
16 张 L20Z；本轮的 DLC 整体五小时上限已由用户取消。
协议中的单次试验 900 秒、开发阶段 14 小时、冻结测试阶段 5 小时 watchdog
保留。不得保留旧平台 300 分钟上限或五小时停止进程。

作业 `dlc6eu0wbbqxatvx` 已于 2026-10-13 22:57:40（北京时间）进入
Running，实际首个节点入口为 22:57:42。GetJob 确认 WorkspaceId `1000309`、
ResourceId `quotaabjvnuk8san`、两个八 GPU worker，JobMaxRunningTimeMinutes
为 `0`。23:24 复核两节点本地 Qwen / SAM3 / GraspNet / Pyroki 已就绪，
四个原生开发会话均 running、原生模型预检均确认 context 1,000,000；
新账本正确导入历史尝试 17 / 8 / 16 / 21，开始分析和修复，尚无新增尝试。
新开发时钟在各自 init 时一次性开始（约 23:13），后续恢复不刷新。
后续真实阶段以实时状态和账本为准。

这是一轮带历史输入的修复续跑研究，不是新鲜独立重复，也不是原身份的
`--resume`。旧开发截止时间已过，且工程修复改变了 runtime 哈希；原账本、
结果和时钟保留，新阶段必须有单独身份、冻结清单和一次性记录的时钟。
后续恢复新阶段仍不重置其时钟。保留 C 输入、实际感知、nonprivileged API、
外层 oracle、Qwen3.8-Flash-Next/native CC 2.1.220/xhigh/1M/64K。

每个开发 seed 51–65 的三次 TOTAL 尝试限制跨父实验和本轮累计：

| 父实验 | 原已用尝试 | 剩余尝试 | 原成功 seed |
| --- | --- | --- | --- |
| drawer r1 | 17 | 28 | 无 |
| stove r1 | 8 | 37 | 无 |
| wine r1 | 16 | 29 | 65 |
| drawer r2 | 21 | 24 | 61 |

drawer r2 的 seed 52 原三次尝试均没有有效评分，已经用尽预算。
原 Fix Loop 的 exhausted-without-graded-evidence 门槛会阻断该 cell 的修复/
冻结，不能靠重新计时恢复它或退款。保留这项 inherited terminal blocker；
其余三个 cell 没有此项阻塞。两个节点保存各自结果后等待双方收尾完成，避免
master 提前退出时平台中断另一节点仍在执行的 cell。

仅 `spends_retry=true` 的实际尝试消耗次数，别名不重复计数；诊断、启动失败、
崩溃、超时及上轮整体停止中断的尝试不退还。父实验的成功是历史证据，不能
替代修复后 runtime 的测试或选出未经新 runtime 验证的候选。旧版没有冻结
或测试 held-out seeds 1–50。本轮完成开发、证据检查和选择后才可冻结测试。

每个 cell 使用新的独立用户、HOME、control、私有父记录只读副本和原生
agent 会话。禁止在四个 cell 之间复制新生成代码、反馈、技能或成功轨迹。
副本只包含本 cell 的开发信息；不读 held-out。旧目录不修改。

工程修复已通过 482 项回归测试（11 skipped）和协调端 78 项独立重点检查：
坐标系契约、动作前失去跟踪的门控、真实 RGB-D 身份关联及精简的事件审查。
这些检查不代表新的实验成功率，须在本轮测量。

部署准备目录：
`/mnt/workspace/aspire-world-deployment-20261013-dlc-default-v3-repair/`。
运行输出：`/mnt/workspace/aspire-world-runs/dlc-default-v3-repair-20261013/`。
新 runtime：
`/mnt/workspace/aspire-world-runtimes/articulation-v3-repaired-SZ-DEV-20261013/`。
提交与实时云状态：部署目录的 `submission.json` / `job-status-latest.json`。
实际模型和 cell 状态：运行目录 `entry/rank{0,1}/pair_state.json`。
新的冻结清单 SHA256：
`ae768364c4586fb8845fa5c3e5f6b331ba357fabbefeee1d670add69e5c9d91e`。
续跑专项协调端复查 33 项、相关流程验收 277 项通过；节点收尾等待也通过验证。
更广的整仓检查有 37 failures / 12 errors，涉及外部历史资产和隔离执行限制，
未在旧版重跑基线；不能声称整仓测试全部通过。详细工程交接在
`/tmp/aspire-v3-default-continuation-20261013/implementation-handoff.json`。
跨 cell 汇总 `progress-summary.md` / `progress-summary.json` 仅 root 可读；
一个不使用 GPU/模型的被动管理进程负责汇总，作业终态后保存报告并退出。
报告器不停止作业，也不重置时钟或预算。
本机 DSW 的 ASPIRE 推理、感知及实验进程保持停止；其他项目不受影响。
