# Stove / wine：分轮复盘修复研究（2026-10-14）

状态：2026-10-15 15:48（北京时间）核对：DLC 作业 `dlcxa3n63xgkmj21` 与 Pod
均已退出，云端为 Failed。两项开发于今天 04:29:58 触发原 14 小时时限，
节点 04:30:14 完成模型和感知服务清理。没有执行冻结 50 seed 测评。

本轮各实际执行 15 次，均仅第一轮：stove 为 10 次正常失败、5 次程序报错；
wine 为 14 次正常失败、1 次超时。stove seed 57 有 task_completed=1，但随后
数组排序报错，sandbox_rc=1，按协议不计合格成功。两项都没有冻结资格。

第二、三轮未执行：stove 从昨晚 18:45、wine 从 21:41 等待外层复盘，
没有复盘审批文件，直至开发截止。这是轮间协调未接续，不能记成跑满 45 次后
失败。stove 已生成的姿态/排序修正仍未验证。账本、时钟与输出全部保留；
不补写历史审批，不重置时限。统计与证据见部署目录 `operator-results-20261015.json`
及[实验明细](EXPERIMENT_RESULTS_DETAILS.md#r07-终止核对2026-10-15)。

以下保留启动和早期检查记录：
14:13 节点入口启动；实际节点完成权重哈希、设备与 UID 检查，Qwen 和两套
感知服务已就绪，加载全部模型后的两次 800×512 GraspNet 合成请求均为 200。
两项 campaign 于 14:24 建立独立会话，原生模型链路探测均通过；14:29:58
初始化各自账本与开发时钟。10 月 14 日 16:25 核对时：stove 完成 1/15，seed 51
正常失败；修改后的 seed 52 initial 提交被同批版本一致规则拒绝，未扣次数，
求解器当时正在调整提交方式。wine 的 seed 51 当时执行中，已完成 0/15。
该次核对尚无新成功信号，未进入复盘点或冻结测试，服务日志未见新 OOM/500。

云端确认 workspace `1000309`、quota `quotaabjvnuk8san`，一个 8 GPU 节点，
`JobMaxRunningTimeMinutes=0`。首次提交工具在导入本地 SDK 前失败，未发出
云端创建请求；对账确认无同名作业后，改用既有 `dlc-operator` 环境成功提交。
首次失败和唯一成功创建的回执均保留在本轮部署目录 `submission/`。

用户在停止上一轮后明确授权修复 stove 和 wine，并重新放到默认空间 DLC
运行；开发使用 3×15 的上限，开发没有合适结果就不执行 50 seed 测评；
每 15 seed 的轮次之间可由外层 Codex 分析开发证据并提出下一轮改进方向。

## 本轮身份与预算

这是新的、有协调端分析参与的修复研究，不是旧作业的普通 resume，也不是
从零开始的独立方法重复。旧作业 `dlc6eu0wbbqxatvx` 及四个 cell 保持停止，
旧账本、时钟、冻结源码、部分测试与完整结果全部保留。

- 任务：`turn_on_the_stove`、`put_the_wine_bottle_on_the_rack`，各一个修复 cell。
- 开发：seeds 51–65，每轮每 seed 最多一次计入预算的执行，最多三轮，最多 45 次/任务。
- 新研究的开发次数单独记账；旧研究已耗用的次数继续保留为历史，不退款。
- smoke、诊断、崩溃、超时和基础设施中断同样占用对应 seed 的本轮机会。
  不额外安排免费任务预跑；仅不执行策略的初始公开场景快照例外。
- 每轮边界保留结果、版本与账本摘要，外层复盘后才放行下一轮。修改代码
  不刷新次数或时钟。协调建议与它引用的开发证据、版本、时间一并留档。
- 新研究仍保留 trial 900 秒、开发 14 小时、冻结测试 5 小时 watchdog。
  用户已取消整个 DLC 作业的五小时上限；不重新增加这个上限。
- “合格”的最低要求：所选**同一个 bundle** 在本轮新 runtime 开发中至少有
  一次正常结束且外层 oracle 成功，并通过开发处理完整性和冻结检查。
  历史成功、其他版本成功、程序报错、模型自述均不能代替。
- 三轮结束仍无合格程序：终结为 `development_failed` / `heldout_skipped`，
  不创建 held-out 执行。中断或协议材料不足则明确记为 incomplete。
- 合格后冻结一个 bundle，外层测试 seeds 1–50；测试反馈不返回求解器、
  不驱动本次开发修复。正式成功率仍以完整 50 次为分母。

## 方法修复与复盘边界

已实现 `checked-motion-v1`：记录请求目标、IK 修改与实际到达状态；目标被
裁剪或姿态被替换时默认不执行，动作未确认到位时拒绝依赖它的抓放动作。
同时加入公开 RGB-D 驱动的可见表面/支撑面候选几何、TCP 到持物特征的关系、
滑移与观测失效检查。遮挡后当前验证变为 unknown，历史验证和预测单独标记。
这些接口不声称知道接触力、完整隐藏几何或任务成功。

已实现三轮执行、不可提前借用的次数限制、逐轮开发证据摘要、只读外层复盘
批准与新原生求解会话。`select`、`check`、`finalize` 和 held-out freeze 均检查
所选 bundle 自身在本研究中的正常 oracle 成功。没有成功的最后一轮自动跳过
测试；没有成功的中间轮正常结束并等待复盘，不反复尝试被拒绝的选择操作。

工程执行器最终 CPU 回归 407 项通过；主协调器独立验证 262 项全部通过。
其中 40 项需要宿主的真实 UID/GID 或进程树，受限沙箱首次执行失败，转到
宿主后全部通过；没有因此修改生产权限规则。另核验两个实际任务的提示、
开发输入清单、实际 UID 访问隔离和 800×512 合成请求的格式。
以上均不是任务成功率。实际 DLC 已验证 GPU 分配、Qwen 1M 服务、两套感知
服务，以及全部模型加载后的两次 800×512 GraspNet 请求。两个原生模型探测
均 exit 0、实际模型 Qwen3.8-Flash-Next、context 1,000,000；正式会话已启动。

补充确认的部署原因：上一轮 wine 专属 GraspNet 服务首次正式开发请求就报
CUDA out of memory（需要额外 1.22 GiB，卡上仅约 759 MiB 空闲）。旧布局
将该感知服务与 Qwen 放在同一物理 GPU6，Qwen 进程占约 66.53 GiB；最近
三个 wine 开发日志也均有 `/plan` 500 并转备用抓取。证据是该 cell 的
`entry/cells/wine_sam2articulation_r1/logs/01/A-graspnet.log` 与最后三个
开发 replay.log，不依赖测试轨迹。本轮已在部署清单中将 Qwen 与两个感知
GPU 分开；DLC 全部模型加载后的实请求预检已通过。实际任务中的抓取效果
仍须依据新开发记录判断。

stove 与 wine 各自只能获得自己的旧开发材料和本轮开发记录。准备阶段逐项
绑定复制的材料清单和哈希；不复制 held-out、跨任务摘要、实验结果总表或
其他 cell 的代码/反馈。旧开发材料及协调端分析参与均作为方法差异声明。
没有共享学习技能或预先整理物体表。几何参数只能来自公开 RGB-D 和机器人
本体观测；缺证据明确 unknown，不读取模拟器资产、真值或 oracle。

外层 Codex 只从当前 cell 的开发日志、公开观测和程序分析问题，建议下一轮
如何改进。每轮由本地 Qwen 的原生 Claude Code 工作流生成/修改策略；
工程 Claude Code 的 Opus 模型与实验 Qwen 路由分别记录。

## 部署计划

默认 workspace `1000309`，quota `quotaabjvnuk8san`。两个任务合并在一个
8 GPU L20Z 节点，独立用户、HOME、控制目录、感知服务与模拟器。
共用一套 Qwen TP4。节点 GPU 2–5 为 Qwen，stove 使用 GPU0 感知/GPU1
模拟器，wine 使用 GPU6 感知/GPU7 模拟器。以正式启动清单和节点探测为准。

保留 Qwen3.8-Flash-Next、本地推理、native CC 2.1.220、xhigh、1M context、
64K output 和现有权重版本。复用已核验的环境与权重；凭据只在外层受保护
文件中，DLC 禁用云凭据注入，求解器无提供商密钥。

部署：`/mnt/workspace/aspire-world-deployment-20261014-supervised-repair/`

输出：`/mnt/workspace/aspire-world-runs/dlc-supervised-repair-20261014/`

新 runtime：`/mnt/workspace/aspire-world-runtimes/supervised-repair-SZ-DEV-20261014/`

工程记录：`/tmp/aspire-supervised-repair-20261014/`。

最多 90 次开发执行和 100 次有条件的冻结测试；两任务并行。每任务开发
最多 14 小时，进入测试后最多 5 小时，另有服务加载/预检。轮间复盘占用
原开发时钟，不承诺所有预算一定用满。启动后按真实节点与账本状态汇报。

## 记录维护

工程基准 Git `63a7fc7382fb258b962372490daaef586fc67fe8`，分支 `SZ-DEV`，
包含未提交修复；仅此 Git 不能完整重建运行时，实际源修改和冻结清单 SHA256
须共同记录。冻结清单 SHA256：
`82291384b0519e08dd9fcbe4afcdcf7da1fed80a0fabba55f1c3cb9532dcf699`。
冻结 6,989 个 runtime 源文件和 79 个外部依赖文件；30 份覆盖源码及 Git diff
保存在本轮部署目录的 `source-overlay/`、`source-changes.patch` 和
`source-change-manifest.json`。没有提交或推送。

两项 campaign 实际启动后，已在私有[成功率矩阵](EXPERIMENT_RESULTS.md)与
[逐次明细](EXPERIMENT_RESULTS_DETAILS.md)新增 R07 / V06 记录。没有完整测试率时
矩阵保持“—”，开发失败不伪装成 50 次测试的 0%。两份记录不进入 solver
可读 runtime，启动前使用真实实验 UID 核验拒绝访问。

## 轮间复盘入口

外层使用冻结 runtime 的 `scripts/libero/streaming_fixloop_review.py`，
对本轮部署目录中的两个 `*.case.json` 执行 `queue` 查询。仅当某 cell
为 `awaiting_review` 时，导出它自己的 review request，读取该 cell 的
`supervised/round_<r>/summary.json`、对应开发程序与日志，写出原因分析和
下一轮指导，再通过 `approve-review` 绑定该轮证据哈希。

决策为 `continue`、`freeze` 或 `stop`；没有复盘文件不会自动放行，
不得预先批准、伪造分析或用另一个任务/held-out 结果填入建议。前两轮
完成 15 个 seed 后等待复盘；第三轮直接按新成功门槛收尾。复盘期间开发
时钟继续走，截止时间不会成为默认批准。当前会话没有可用的自动唤醒工具，
因此检查点会可靠等待外层实际处理，不能声称已设置自动 Codex 定时复盘。
