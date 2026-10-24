# Wine 修复轮（2026-10-19）

用户授权修复 wine，随后在朱子悦项目 DLC workspace `1493863`、quota
`quotal96jnu33mnl` 新跑一项 wine；本轮开发验证和正常冻结检查通过后，才评估
held-out seeds 1–50。旧实验、账本、时钟、冻结文件和成绩全部保留。

当前记录（2026-10-24 同步核对）：工程已完成并实际启动，作业
`dlcghv217zqk880a` 于 10 月 19 日 14:42（北京时间）被用户停止，平台与 Pod
均 Stopped、节点 terminated。开发已用 18/45 次：第 1 轮 15 次、第 2 轮 3 次，
全部为正常失败，正常 oracle 成功 0 次；未选择或冻结候选，held-out 未开始，
最终成功率“—”。保留原预算和期限，不恢复或替换该停止作业。

以下准备过程和计划路径保留提交前的历史快照；实际验收与身份见文末，
不能以早期准备文件的“尚未启动”描述代替最终记录。

已建立独立部署/运行目录与 UID30801 的私有 HOME、control、cache/config；
旧目录没有改写。`shared-node8-single-v1` 的启动、容量拒绝、清理及旧八卡/四卡
布局共 41 项 CPU 回归通过。长轴接口的独立旋转反例验证通过（原行读法
`abs(z)=0.8932`，正确列轴 `abs(z)=0.08716`），近等轴返回 ambiguous。
这些是工程证据，不是新轮任务成功信号；持物/放置和自动收尾仍待完整验收。

## 起点、预算和门槛

- 这是一轮有明确工程干预的修复研究，以 Oct17 wine r1 自己的代码和开发
  seeds 51–65 的证据为起点；不接收其他 repeat 解法或任何 held-out 轨迹。
- 旧 wine r1 的 45 次尝试仍属于旧研究。本轮另记新研究身份与最多 45 次预算：
  三轮，每轮 seeds 51–65 各一次，每 seed 最多三次；不修改旧账本或冒充续跑额度。
- 每 trial 900 秒，实际初始化起开发最多 14 小时，冻结测评最多 5 小时。
  普通恢复不重置时钟或次数，没有新的整体五小时上限。
- 每轮复盘由 DLC 上独立的同一本地 Qwen 原生会话执行；轮末报告收尾与
  root 验证自动接续，不依赖聊天唤醒，不添加额外 trial。
- 当前 runtime、当前选定的同一 bundle 必须正常退出且取得外层开发 oracle
  成功，再通过普通 check/freeze。旧成功、报错时的成功信号、几何自评均不能替代。
  没有合格候选就跳过 50 seed 测评，保留失败或 incomplete 状态。

## 修复范围

1. 几何接口明确 OBB 旋转矩阵的列轴约定，提供带坐标系和歧义状态的长轴，
   防止把横卧误判为站立；可见点几何不冒充完整物体真值。
2. 改善 held feature 与 TCP 的相对几何和证据质量判断，区分可见表面变化、
   遮挡、身份中断与有证据的滑移；预测继续标为预测，恢复必须有真实感知证据。
3. 修复放置策略的支撑几何、瓶体相对变换、可达性和松爪前后检查。
   保留跟踪，运动失败或证据不足必须明确反馈，不能无条件宣布完成。
4. 修复开发试验结束后的原生会话收尾；在同一个开发期限内自动补齐自身
   报告、失败 seed 说明和合法候选选择，再进行正常复盘与冻结检查。

本节保留原授权范围；实际验收与冻结身份见文末，工程通过不代表任务成功。

## 计算、模型和隔离

计划一个八卡 worker，仅跑一个 wine cell。沿用已验证服务分配：Qwen TP4
在 GPU4–7（8121），SAM3 在 GPU0（8114），GraspNet 在 GPU1（8115），
Pyroki 在 CPU（8116），独立模拟器/SAM2/EGL 在 GPU2；GPU3 不安排其他实验。
Qwen3.8-Flash-Next、本地推理、native CC 2.1.220、xhigh、1M context、64K
输出，保留 decode CUDA graphs。最终 profile 与 case 由严格校验和冻结清单绑定。

Qwen 加载/内存分析先于感知服务；节点必须完成八卡/GPU/EGL、资产和权重哈希、
服务健康、完整 800×512 无任务 GraspNet 容量探测和真实非特权 UID 检查，才初始化。
用户 HOME 的 cache/config 目录先归属本 cell，避免历史 root cache 权限故障。

本机 DSW 不启动本项目模型或仿真进程。云提交凭据只留外层，不注入求解器。
协调器的结果矩阵、明细、其他 cell 和 source-overlay 副本均需真实 UID 拒绝访问。
运行源码排除这些私有记录。原始日志、模型和运行产物不进入源码同步。

## 路径和版本

- 工程 `/root/github/aspire-world`，分支 `SZ-DEV`，准备基线
  `63a7fc7382fb258b962372490daaef586fc67fe8`；已有未提交修改保留。
- 工程证据 `/tmp/aspire-wine-repair-20261019/`。
- 计划部署 `/mnt/workspace/aspire-world-deployment-20261019-wine-repair/`。
- 计划运行源码 `/mnt/workspace/aspire-world-runtimes/wine-repair-SZ-DEV-20261019/`。
- 计划输出 `/mnt/workspace/aspire-world-runs/dlc1493863-wine-repair-20261019/`。

实际启动后，在协调器私有实验明细记录完整 Git HEAD、分支、源码差异/哈希、
runtime manifest、job/workspace、父研究和独立预算/时钟。矩阵仅填写完整冻结测评
成功率，无最终测评结果时为破折号。本轮启动时未提交或推送 Git；
2026-10-24 用户另行授权同步到本地 `SZ-DEV` 并推送。

## 实际验收、运行身份与停止证据

来源为 RS `/mnt/data/aspire-world-deployment-20261019-wine-repair/`。
`source-overlay/` 的 77 个文件均与来源清单哈希一致；具体发布范围见
[同步说明](SYNC_SCOPE.md)。留存验收日志 `operator/final-core-tests.log`
为 311 passed、2 warnings，`operator/final-seed-tests.log` 为 17 passed。
实际修复覆盖 OBB 列轴、带不确定性的持物几何、支撑面放置预测、到位后才
允许松爪，以及自动复盘和有界收尾；预测或未验证 IK 不能当成实际到位。

运行登记 R09/V08，study `wine-repair-20261019`，cell
`wine_supportrepair20261019_r1`，UID30801；DLC workspace1493863，
job `dlcghv217zqk880a`，一个八卡 worker。启动 Git 基线为
`63a7fc7382fb258b962372490daaef586fc67fe8` / `SZ-DEV`，含封存的未提交改动。
实际 `runtime-freeze-v2.json` SHA256：
`71dee4901443f03f16be14c3fed7a3e2ee751b1e985bc89523137ff8494d4e3b`。

最终状态依据部署目录的 `operator-latest-status.json`（10 月 19 日 14:44:26
北京时间）及其引用的 `operator/stop-20261019T144249+0800/`。
SIGTERM 留存了原始 campaign/slot 的 running 标记；平台 Stopped、节点
terminated 和停止回执为停止依据。本次核对的开发账本、campaign 状态和
pair 状态哈希仍与停止时一致，未改写它们。18 次消耗、原 14 小时时钟、
父轮 45 次记录和冻结文件均保留；剩余额度不构成恢复授权或期限重置。
