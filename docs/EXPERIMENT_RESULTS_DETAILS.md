# 实验结果与版本明细

快速查看各版本的任务成功率，请打开[成功率简表](EXPERIMENT_RESULTS.md)。
本页保留逐任务记录、运行身份、版本证据和维护规则，供需要追溯时查阅。

最新补录（2026-10-24）：R09/V08 wine 修复研究实际初始化后，于
10 月 19 日 14:42（北京时间）被用户停止；18/45 次开发均为正常失败，
没有当前候选、冻结或 held-out 测试，最终成功率“—”。启动 Git、冻结身份、
独立预算、停止依据与输出路径见[本轮记录](DLC_WINE_REPAIR_20261019.md)。
本次只同步源码，未恢复实验。

R08 核对：2026-10-17 23:14:28（北京时间）。本次四项已结束。
stove r2完成冻结测试50/50（100%），wine r2完成3/50（6%），两项均无崩溃或
基础设施错误、无未测seed。stove r1开发失败跳过测评，wine r1冻结检查未过未测。
模型/感知服务已于20:12:40回收；平台Failed来自两个r1的退出码1，r2均退出码0。
逐seed、冻结文件与原时钟的核验结果见
[最终核查快照](/mnt/workspace/aspire-world-deployment-20261017-unit8-four/operator/status-20261017T151428Z/status.json)。

最新授权：2026-10-17 改为朱子悦项目 `1493863`，一个八卡节点跑 stove/wine
各两个重复。默认四卡作业 `dlcg4a7t7ff5ox1g` 已确认 Stopped / StoppedByUser，
零节点、新账本、时钟或尝试；保留取消证据。八卡首趟 `dlc12rui2zglkj7r`
于11:32因root所有的HOME缓存目录导致Numba导入失败，在正式初始化前退出，
四项均无新账本、时钟或尝试。权限修复及四个真实用户导入通过后，11:42:19
重提同一运行身份的 `dlc7fct8r6v95slf`，11:43:36已Running，11:44:21节点attempt02开始预检。
这是同一阶段的基础设施重试，不新开实验行或重置预算。首趟日志在部署目录
`operator/failed-launch1/`，修复证据为 `operator/cache-permission-repair.json`，
两次独立提交回执分别在 `submission/`、`submission-retry1/`。r1 各保留已用15次、
最多新增30次；r2 独立从头开始、各最多45次。11:58:57实际 init 后已逐项登记 R08/V07，
见[八卡四实验](DLC_UNIT8_FOUR_20261017.md)。

迁移前核对：2026-10-17 10:54（北京时间）。当时默认空间四卡作业 `dlcg4a7t7ff5ox1g`
自 10 月 16 日 23:29 提交后一直排队，约 11 小时 25 分；无已分配节点、
节点入口、新账本或阶段时钟，新增尝试 0 次。平台返回 `JobEnqueued`，
调度事件为空，未给出更具体原因。父轮各 15 次及证据哈希均保持不变。
实际启动后登记 R08/V07；本表由外层协调器维护。
见[四卡接续记录](DLC_UNIT4_CONTINUATION_20261016.md)。
本次[核查证据](/mnt/workspace/aspire-world-deployment-20261016-unit4-continuation/operator/status-20261017T025438Z/status.json)
包含云端状态历史、空运行目录核查和父证据哈希复核。

历史取消核对：2026-10-15 22:43（北京时间）。
剩余两轮准备作业 `dlc5zw1sbv3vzijn` 已按用户要求取消，确认 StoppedByUser；
取消前一直排队、零节点，没有新 phase、尝试或测评，因此不新增 R08/V07。
各任务 R07 已用 15 次和过期时钟保留，见[取消记录](DLC_AUTONOMOUS_CONTINUATION_20261015.md)。

每个“轮次 × 任务 × 方法 × 重复”占一行；修复续跑另开轮次并保留父轮。
当前已回填 RS / DLC Streaming World 实验线的 **9 轮**（R09 已实际初始化后补录），
其中 **7 条完成了冻结后的 50-seed 测试**。更早研究的补录范围见文末。
状态是上述核对时间的快照；进行中的实验在后续状态汇报和收尾时更新。

## R08 八卡四实验已结束

启动身份见[逐项启动记录](/mnt/workspace/aspire-world-deployment-20261017-unit8-four/operator-experiment-start.json)。
源码V07：Git `63a7fc7382fb258b962372490daaef586fc67fe8`、分支`SZ-DEV`，
含启动时封存的未提交改动；实际runtime SHA256为
`e47624c88c4b6d19afd2f72db6ab76b4c2407d3a14b9faaa747db06b7e453662`。
DLC workspace1493863、job`dlc7fct8r6v95slf`，一个八卡节点。
r1继承各自R07已用15次，仅续第2/3轮各最多新增30次；r2独立从头、无历史
输入，各最多45次。起点差异分开报告，四项不共享代码或开发反馈。

四项开发时钟均于10月17日11:58:57实际初始化，截止10月18日01:58:57。
旧父时钟与15次消耗保持；每个开发seed累计三次的限额经实际账本复核，均未超额。
轮间独立本地Qwen复盘实际自动执行，各项都走到了第3轮；不依赖聊天唤醒。
最终共新增150次开发执行（全部结束），合格成功31次；加上r1父轮30次，
四项各累计45次。下表仅统计本阶段新增尝试，历史结果不混入。

| 任务 / 重复 | 新增尝试 / 上限 | 合格成功 | 正常失败 | 程序报错 | 其他错误 | 最终开发状态 |
| --- | --- | --- | --- | --- | --- | --- |
| stove r1 | 30 / 30 | 0 | 30 | 0 | 0 | 无成功候选，development_failed |
| wine r1 | 30 / 30 | 4 | 26 | 0 | 0 | 选定程序4/15，缺逐seed说明，stage1_incomplete |
| stove r2 | 45 / 45 | 26 | 19 | 0 | 0 | 选定程序第3轮15/15成功，正常冻结 |
| wine r2 | 45 / 45 | 1 | 28 | 16 | 0 | 第3轮seed62成功，正常冻结 |

### 冻结后的完整50-seed成绩

| 任务 / 重复 | 覆盖 | 成功 | 正常失败 | 程序崩溃 | 基础设施错误 | 最终成功率 | 原始结果 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| stove r2 | 1–50，50/50已测 | 50 | 0 | 0 | 0 | **100%** | [报告][r08-stove-r2-final] |
| wine r2 | 1–50，50/50已测 | 3 | 47 | 0 | 0 | **6%** | [报告][r08-wine-r2-final] |

两份报告均completed=true、incomplete=false，无超时、无未测或未解决seed。
已逐个复核100个seed的状态、正常退出、外层oracle、相同冻结bundle，以及两项
冻结policy/config的SHA256；选定程序自己的当前阶段成功门槛与正常冻结检查均通过。
这是最终测试成绩，不是开发成功率。

stove r2冻结bundle为
`a7b943f4c6d14c0c555fd94c5ef377eabe240ad49f58facf0250dc9fb7db5c2c`。
第3轮同一程序开发15/15；冻结测试16:48:16启动，20:12:20最后一个seed结束，
20:12:21 campaign完成，在原测试截止21:48:16之前。冻结清单见
[stove冻结文件](/mnt/workspace/aspire-world-runs/dlc1493863-unit8-four-20261017/stove_independent20261017_r2/heldout/frozen_manifest.json)。
此前第2轮的11次成功来自其他版本或当时的v6，不混进该固定程序的开发分母。

wine r2冻结bundle为
`26e1acb6bc0fa2b752687613f97073b11b7090a34d085813fe6301ee44ad7657`。
第3轮最终1/15开发成功，选定bundle自己的13次开发执行中1次成功（seed62）；
第3轮早先两次来自其他bundle。第1轮另有seeds58/65曾oracle成功但程序报错，
仍不计合格成功。冻结测试17:01:32启动，19:44:04最后一个seed结束，19:44:05
campaign完成，在原截止22:01:32之前。测试成功seeds8、19、36，其余47次正常失败。
冻结清单见[wine冻结文件](/mnt/workspace/aspire-world-runs/dlc1493863-unit8-four-20261017/wine_independent20261017_r2/heldout/frozen_manifest.json)。

### 两项未开展冻结测试的r1

stove r1用完新增30次，第2/3轮均0/15；累计45次后按成功门槛退出
`development_failed`，16:21:50结束。held-out跳过、实际执行0个，最终成绩“—”。

wine r1第3轮选定bundle
`dc321bc5b96db4c6b60ed75b92c217e008c9e41dcfdaa1a26f46e837f96a2668`
在seeds51、56、57、63上正常成功，共4/15（开发26.7%）。15:01:10闭合开发，
15:01:39外层check因缺少11份`attempts/seed_N_BLOCKED.md`退出为stage1_incomplete。
缺失seeds52、53、54、55、58、59、60、61、62、64、65；findings.md不能替代
逐seed说明。候选与全部成功证据保留，未冻结、未执行held-out，最终成绩“—”。
本次核查不补写求解器报告、不跳过检查、不启动新尝试或重提作业。

### 退出、服务清理与核查证据

20:12:40节点记录campaigns_exited，四项退出码依次A=1、B=1、C=0、D=0。
共享Qwen正常退出，SAM3/GraspNet/Pyroki收到SIGTERM并退出，teardown无强杀。
入口规定只有所有cell退出码均0才整作业成功，因此云端Failed符合A/B状态，
不否定C/D两份完整冻结结果。已结束作业不因状态查询而重启，冻结代码、账本、
时钟、结果和启动Git/源码身份均保留。

[最终核查][r08-current]封存了四项开发账本、campaign状态、两项held-out
结果/账本/冻结清单的副本及哈希；这些跨cell与测试记录仅供用户和外层协调器读取。
早期进度仍保留在各次只读快照：
[12:20](/mnt/workspace/aspire-world-deployment-20261017-unit8-four/operator/status-20261017T042047Z/status.json)、
[14:32](/mnt/workspace/aspire-world-deployment-20261017-unit8-four/operator/status-20261017T063229Z/status.json)、
[15:19](/mnt/workspace/aspire-world-deployment-20261017-unit8-four/operator/status-20261017T071934Z/status.json)、
[16:41](/mnt/workspace/aspire-world-deployment-20261017-unit8-four/operator/status-20261017T084156Z/status.json)。

[r08-current]: /mnt/workspace/aspire-world-deployment-20261017-unit8-four/operator/status-20261017T151428Z/status.json
[r08-stove-r2-final]: /mnt/workspace/aspire-world-runs/dlc1493863-unit8-four-20261017/stove_independent20261017_r2/heldout/heldout_result.json
[r08-wine-r2-final]: /mnt/workspace/aspire-world-runs/dlc1493863-unit8-four-20261017/wine_independent20261017_r2/heldout/heldout_result.json

## Wine失败复盘与拟议修复（2026-10-19）

本节为外层协调器分析，包含跨重复与测试诊断，禁止提供给实验求解器。
证据见[本次私有分析](/mnt/workspace/aspire-world-deployment-20261017-unit8-four/operator/wine-analysis-20261019/evidence.json)。
本次没有修改策略或运行时、启动模型/实验，所有既有成绩、冻结文件和消耗保持。

历次wine需区分：10月11日v1完整测试12/50；10月12日v3在五小时限额内未完成；
10月13日修复轮测试在第二个seed因用户暂停中断，不能当成完整0%；
10月14日分轮研究只完成第1轮0/15，等外层复盘直到开发过期；
10月17日r1开发选定程序4/15而收尾未过，r2最终测评3/50。
方法、输入和重复不同，24%降到6%不足以单独归因于World模块。

### 本次核实的主要问题

1. **持物状态被主动绕开。** 最新r2冻结代码`rack_swap`将wine_bottle设为
   tracked:false、只跟踪rack；`measure_offset`只测一次TCP到可见瓶底的偏移，
   失败时还有基于table_z+0.17的高度回退。该做法减少了identity_conflict死锁，
   也失去了搬运中持续验证瓶体相对手的位姿/滑移的能力。现有held_geometry API
   已存在，但这份策略没有使用其绑定闭环；问题同时涉及运行时可恢复性与策略选择。
2. **几何已算出但没有驱动放置。** r2的`panel_model`计算斜面法向、方向与
   高度函数，但`release_pass`主要使用rail_top减固定偏移和TOPDOWN_Q，依赖
   竖直松爪后瓶子自己倾倒。缺少瓶体完整包络、有效支撑区域与放置姿态的联合求解。
   早期v1的开发总结也反复记录释放后滑出、位置代理与oracle不一致；“物理不可达”
   或“只接受某个槽位”是智能体假说，不作为已证明事实。
3. **有确定的姿态计算错误。** r2冻结代码350行用`Rb[il][2]`判断最长轴与
   竖直方向关系。底层common.py原样返回Open3D obb.R，轴是列，正确取值为
   `Rb[2, il]`（世界坐标+z为up时）。纯CPU构造水平长盒验证：旧值0.893会判站立，
   正确值0.087应判躺卧；列向量投影的0.18m包络与extent一致。这个反例证明
   索引错误，尚未量化它单独导致多少真实失败。不能据现有rest标签硬分全部物理原因。
4. **动作到位与放置完成没有闭合。** r2中transport未验证仍继续placement，
   release姿态未到位时会抬高后再次尝试open；最后无条件打印DONE placement，
   并有catch-all清洁退出包装。正常退出不能说明瓶子已稳定放上架，外层oracle
   仍正确将47次记为失败，分数没有采用该自报DONE。恢复逻辑也依赖有缺陷的姿态分类。
5. **移动物体的身份/滑移判断不够稳健。** 多轮开发记录出现运动后可见中心
   跳变、遮挡后重识别失败、World disarm，以及小中心变化伴较大刚体旋转漂移。
   r1后来将绑定容差放宽至0.12m/1rad，但仍有持物证据中断。尚不能仅凭漂移
   判断真假滑移；应加入可观测性/对应质量与运动预测，遮挡为unknown，真实矛盾
   才失效，不能以删除瓶体跟踪或不断放宽阈值取代修复。
6. **r1收尾被调度器截断。** 最后一条开发执行14:55:04结束，14:55:09检测到
   轮末，15:00:10左右会话被固定300秒boundary grace强停，退出143；原生记录
   此时仍在等待worker总结。findings.md和task_analysis.md仍写第2轮，11份失败
   seed说明全部缺失，15:01:39 check拒绝冻结。这是收尾状态机缺口，现有证据
   不支持仅归咎于模型不写报告。补齐说明只修复流程，不改善4/15的任务性能。

### 可复核的失败定位

r2的47次测试失败均有运行日志记录post-lift held=True、transport ok=True、
open_gripper ok和DONE；3次测试成功也经过相同阶段。说明策略经常走到释放，
下一步应优先查持物连续性与释放/支撑，不能把它们统归为抓取失败。
这些是策略日志和可见几何估计，不是全程抓稳或物理接触的真值证明。
测试逐seed诊断只供本节协调器分析；后续求解器只能读取获准的开发材料。

r1第3轮按真实运行日志（排除summary回显的程序源码）分类：4次正常oracle成功；
11次失败中4次无验证抓取、2次持物证据/策略门控中断、1次运动中episode耗尽、
4次已到释放/撤退阶段但oracle失败。因此r1同时有抓取、持物、运动和放置问题。

### 拟议修改顺序与验证

1. **先修确定性错误及收尾。** 纠正OBB轴索引，统一API轴/坐标系说明并输出
   明确的世界坐标长轴；用旋转盒、横卧/竖直/斜放回归验证。轮末分成“执行已满”
   和“收尾已通过”两步，失败说明、当前轮报告、已测试程序选择检查齐全后再
   关闭/复盘。缺材料自动运行同cell的受限收尾会话，不允许新增trial、不重置
   14小时时钟，也不跳过冻结门槛。
2. **修通用持物表示与恢复。** 在held_geometry/identity关联与恢复流程中，
   分开测量、预测、unknown与真实滑移；保留瓶体到TCP的完整可观测变换、长轴/
   底端和估计质量、时间/坐标系。利用已有公开机器人姿态和RGB-D/腕视角校验，
   为遮挡提供可恢复路径，结构修订不得悄悄抹去持物证据。避免把可见表面中心
   的变化直接等同于刚体滑动，也不凭空补质量或摩擦系数。
3. **让放置由瓶体和支撑共同决定。** 用观测得到的瓶体包络、架子斜面/横杆/
   支撑区域生成候选瓶体姿态，再由持物变换求TCP姿态并检查可达性/碰撞；
   到位、仍持物、目标支撑可信才松爪，之后再观测稳定性。失败反馈进入有类型的
   恢复/重规划，不只改释放高度常数或靠随机倾倒。相关能力做成通用几何/执行接口，
   任务策略仍由各自求解器产生，新增方法因素明确记录。
4. **分层验收后再评估。** 先离线回放自己的开发RGB-D与动作日志，核对漂移、
   可达性和支撑估计；分别验证运行时/收尾与放置策略，减少同时修改造成的归因困难。
   新修复轮保留旧45次和旧时钟，另记源码/方法与预算身份；生成器不读取47条
   held-out失败轨迹或其他repeat解法。候选有自己的当前运行成功证据、通过完整
   冻结检查后再按既定协议测1–50，重测标明修复研究，不能当作未见数据盲测。

## 各任务结果总表

最终成功率只取完整冻结测试的 `success / 50`；开发成功不填入这一列。
“未开始”和“—”表示没有最终测试成绩，不能解释成 0%。
Git 一栏记录有证据的源码基线；`+ 改动` 表示还包含 Git commit 之外的源码，
实际运行版本须同时查 V01–V08 的冻结哈希。未保存的 Git 号标为“未归档”。

| 轮次 / 开始日期 | 任务 | 方法 / 重复 | Git 基线 / 源码版本 | 当前或最终状态 | 冻结测试成功 / 总数 | 最终成功率 | 原始证据 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| R09 / 2026-10-19 | wine | v3 工程辅助修复 / r1 | `63a7fc7` + 改动 / V08 | 用户停止；18/45 次开发，0 正常成功；父轮45次保留 | 未开始 | — | [修复及停止记录](DLC_WINE_REPAIR_20261019.md) |
| R08 / 2026-10-17 | stove | v3 修复接续 / r1 | `63a7fc7` + 改动 / V07 | 开发失败退出；新增30次完成、0成功；累计45次 | 跳过，0次执行 | — | [本次快照][r08-current] |
| R08 / 2026-10-17 | wine | v3 修复接续 / r1 | `63a7fc7` + 改动 / V07 | 开发完成；选定程序4/15成功；累计45次，缺逐seed说明，冻结检查未过退出 | 未开始 | — | [本次快照][r08-current] |
| R08 / 2026-10-17 | stove | v3 独立从头生成 / r2 | `63a7fc7` + 改动 / V07 | **冻结测试完成** | **50 / 50** | **100%** | [结果][r08-stove-r2-final] |
| R08 / 2026-10-17 | wine | v3 独立从头生成 / r2 | `63a7fc7` + 改动 / V07 | **冻结测试完成** | **3 / 50** | **6%** | [结果][r08-wine-r2-final] |
| R07 / 2026-10-14 | stove | v3 分轮复盘修复 / r1 | `63a7fc7` + 改动 / V06 | 轮间等待超时终止；仅第 1 轮 15 次，0 合格成功 | 跳过，0 次执行 | — | [终止核对][r07-results] |
| R07 / 2026-10-14 | wine | v3 分轮复盘修复 / r1 | `63a7fc7` + 改动 / V06 | 轮间等待超时终止；仅第 1 轮 15 次，0 合格成功 | 跳过，0 次执行 | — | [终止核对][r07-results] |
| R06 / 2026-10-13 | drawer | v3 修复 / r1 | `63a7fc7` + 改动 / V05 | **完成** | **43 / 50** | **86%** | [结果][r06-drawer-r1] |
| R06 / 2026-10-13 | stove | v3 修复 / r1 | `63a7fc7` + 改动 / V05 | 用户暂停，测试未完成 | 0 / 50（14 失败、1 中断、35 未测） | — | [暂停结果][r06-stove-result] |
| R06 / 2026-10-13 | wine | v3 修复 / r1 | `63a7fc7` + 改动 / V05 | 用户暂停，测试未完成 | 0 / 50（1 失败、1 中断、48 未测） | — | [暂停结果][r06-wine-result] |
| R06 / 2026-10-13 | drawer | v3 修复 / r2 | `63a7fc7` + 改动 / V05 | 开发阻塞，已退出 | 未开始 | — | [停止检查][r06-drawer-r2] |
| R05 / 2026-10-12 | drawer | v3 / r1 | 未归档 / V04 | 五小时上限停止，未完成 | 未开始 | — | [停止报告][r05-rank0] |
| R05 / 2026-10-12 | stove | v3 / r1 | 未归档 / V04 | 五小时上限停止，未完成 | 未开始 | — | [停止报告][r05-rank0] |
| R05 / 2026-10-12 | wine | v3 / r1 | 未归档 / V04 | 五小时上限停止，未完成 | 未开始 | — | [停止报告][r05-rank1] |
| R05 / 2026-10-12 | drawer | v3 / r2 | 未归档 / V04 | 五小时上限停止，未完成 | 未开始 | — | [停止报告][r05-rank1] |
| R04 / 2026-10-11 | bowldrawer | v2 / r1 | 未单独归档 / V03 | **完成** | **45 / 50** | **90%** | [结果][r04-bowldrawer-r1] |
| R04 / 2026-10-11 | drawer | v2 / r1 | 未单独归档 / V03 | 用户停止，开发未通过 | 未开始 | — | [停止回执][r04-drawer-r1] |
| R03 / 2026-10-11 | stove | v1 / r1 | `c94a487` 基线，含增量 / V02 | 用户停止，开发未通过 | 未开始 | — | [停止回执][r03-stove-r1] |
| R02 / 2026-10-11 | bowl | v1 / r2 | `c94a487` 基线，含增量 / V02 | **完成** | **44 / 50** | **88%** | [结果][r02-bowl-r2] |
| R02 / 2026-10-11 | bowldrawer | v1 / r1 | `c94a487` 基线，含增量 / V02 | 用户停止，开发未通过 | 未开始 | — | [停止回执][r02-bowldrawer-r1] |
| R02 / 2026-10-11 | drawer | v1 / r1 | `c94a487` 基线，含增量 / V02 | 用户停止，开发未通过 | 未开始 | — | [停止回执][r02-drawer-r1] |
| R02 / 2026-10-11 | wine | v1 / r1 | `c94a487` 基线，含增量 / V02 | **完成** | **12 / 50** | **24%** | [结果][r02-wine-r1] |
| R01 / 2026-10-10 | bowl | v1 / r1 | `c94a487` + 改动 / V01 | **冻结测试完成** | **22 / 50** | **44%** | [结果][r01-bowl-r1] |

任务简称对应 `libero_goal_swap`：bowl = `put_the_bowl_on_the_plate`；
bowldrawer = `open_the_top_drawer_and_put_the_bowl_inside`；
drawer = `open_the_middle_drawer_of_the_cabinet`；wine = `put_the_wine_bottle_on_the_rack`；
stove = `turn_on_the_stove`。v1 = `sam2-streaming`，v2 = `sam2-semantic-v2`，
v3 = `sam2-articulation-qwen-v3`；R06 是 R05 各自的修复续跑，不是新的独立重复。
R07 是用户另行授权、带协调端逐轮复盘的新修复研究，历史开发输入仅来自
各自的 R06；新预算独立登记，不把它称为从零开始的独立重复。

R06 已暂停。用户 2026-10-14 要求“不要跑了，暂停任务，分析原因”；
StopJob 在 12:01 被接受，12:03 实时确认作业 `dlc6eu0wbbqxatvx` 和两个
Pod 均为 Stopped。管理汇总进程也已退出。该旧作业保持停止；随后授权的
stove / wine 分轮修复另建 R07，14:24 两项 campaign 已启动。
drawer r1 保留完整 43/50 成绩；stove / wine 各用尽累计 45 次开发尝试，
本轮均无开发成功，并分别在冻结阶段暂停。stove seeds 1–14 正常失败，
seed 15 因暂停中断，35 个未测；wine seed 1 正常失败，seed 2 因暂停中断，
48 个未测。两次中断按原报告语义单列 infrastructure_error，不能算作普通
任务失败，不补跑或退款，当前两份报告均为 incomplete，不发布完整成功率。
drawer r2 保留继承的 seed 52 terminal blocker。
停止前保存了 18 份状态/冻结文件快照；停止后只用原协议报告函数保存
未完成汇总，没有执行新 seed。已核对四份开发账本和 drawer r1 最终报告
字节未变，阶段时钟、预算和冻结源码未修改。操作证据见[暂停快照][r06-pause]。

## 暂停后的原因分析（2026-10-14）

依据是开发代码、开发日志与公开机器人/视觉反馈。表征确有未建立的操作信息，
但还有已证实的执行逻辑问题，不能把这轮失败统称为缺质量、摩擦或力矩参数。
两份 agent findings 把失败标为 Physical；下列证据不足以证明所有姿态和路径
都物理不可达，因此不采纳这个结论作为已证实原因。

| 环节 | 已核实证据 | 应补的信息或约束 |
| --- | --- | --- |
| 目标可达与动作到位 | 本轮冻结 API 的 `solve_ik` 将 TCP x 裁剪至最多 0.75 m、z 至少 0.005 m，还可能换用 IK 姿态。stove 开发日志有 x=0.768/0.802 m 或 z=-0.004 m 的目标，且报告 target_modified / not_reached。 | 分开记录原目标、有效目标、实际到达姿态、软件边界和姿态回退；失败不能自动推进到接触/释放。软件裁剪不是物理不可达的证明。 |
| stove 关节与接触 | 配置只持续跟踪 burner；knob 为 tracked:false 的 rotating 假设。小旋钮掩码受反光及手遮挡影响，缺少稳定的部件角度实测。 | 接触点/接触法向、夹爪接触区、实测旋钮轴/角度及可信变化；World 的关节拟合能力需要真实部件观测才能用于控制。 |
| wine 持物状态 | 搬运前 `_retire_bottle()` 关闭瓶体跟踪，释放高度从初始瓶底和抓取高度估算；当前没有可靠更新的手到瓶体变换。 | 瓶体相对夹爪的位姿、滑移/掉落状态和置信度；遮挡时可有明确标注的预测，放置前要重新测量验证。 |
| wine 支撑与释放 | 最终程序以 rack 中心和局部 top 高度生成释放位姿。`goto` 重试后仍返回最后一份失败报告，下降后调用 open_gripper 没有检查 reached；暂停前开发日志有厘米级未到位和桌面附近的最终瓶体观测。 | 具体槽位/支撑面、瓶子长轴与底部相对支撑的几何、支撑已建立的证据及释放条件；不能用整体中心点代替稳定放置位形。 |

代码位置：API 的 `solve_ik` 在
`aspire/sim/cap/integrations/franka/libero_reduced.py:395`；已核对工程文件
与这轮冻结文件字节一致。wine 生成程序的 `goto` 在 208–228 行，
`_retire_bottle()` 调用在 511 行，下降和无到位检查的释放在 590–591 行。
stove 配置在自己的 `cell/fix_streaming_config.json`，knob 未持续跟踪。
不修改这些历史文件，不把测试轨迹或其他 cell 的解法交给求解器。

建议修复顺序：先让动作到位/目标截断成为强制执行条件；再建立持物变换与
稳定支撑模型，并补旋钮的可测关节状态和接触几何。每个新增量都要带坐标系、
单位、来源、时间和置信度，未知时触发重观测或重新规划。质量、摩擦和驱动力矩
暂没有被证明是首要缺口，不能凭任务失败填猜测数值。GraspNet HTTP 500 和
长时间同步 Qwen 复查也出现在开发记录中，需独立复查服务/时延及相关输入，
不能靠增加表征字段解决。以上为暂停时分析；旧实验保持暂停。

后续用户已授权新 stove / wine 修复研究，见
[2026-10-14 分轮复盘记录](DLC_SUPERVISED_REPAIR_20261014.md)。准备时进一步
从 wine 自己的感知服务错误日志确认，GraspNet 500 是 CUDA OOM：旧布局
感知服务和 Qwen 共用 GPU6，抓取请求还需 1.22 GiB 时仅余约 759 MiB。
最近三次开发 replay 都记录了该 500 后退回备用抓取。新配置已将 Qwen
与感知 GPU 分开；DLC 节点在全部模型加载后，两套 800×512 请求均已通过。
这验证了部署预检，不将它冒充任务成功。

## R07 启动记录（2026-10-14）

默认 workspace `1000309`，quota `quotaabjvnuk8san`，作业
`dlcxa3n63xgkmj21`，一个 8 GPU L20Z 节点。14:11 创建、14:13 节点执行，
两项 campaign 于 14:24:04（北京时间）分别建立独立会话，原生模型探测通过。
14:29:58 各自初始化开发账本并启动第 1 轮代码生成；14 小时开发截止为
10 月 15 日 04:29:58，等待复盘不重置这个时间。14:32 首次核对时 trials 均为 0。
没有启动旧作业，没有 held-out 执行。实际启动与时钟回执见部署目录
`actual-launch-acceptance.json`。

启动源码 Git HEAD `63a7fc7382fb258b962372490daaef586fc67fe8`，branch `SZ-DEV`，
含未提交/新增源码；已核对启动时 HEAD 及 30 份覆盖源码仍与冻结时一致。
部署根 `/mnt/workspace/aspire-world-deployment-20261014-supervised-repair/`
内的 `launch-source-identification.json`、`source-change-manifest.json`、
`source-overlay/`、`source-changes.patch` 保留完整版本证据；runtime 清单为
`runtime-freeze.json`，SHA256 见 V06。

新 study `supervised-repair-20261014`：stove 和 wine 各自有 3×15 次开发预算，
seeds 51–65 每轮最多一次。父轮各自已消耗的 45 次继续保留，仅作为只读
开发输入，不进入新候选或成功统计。每轮之间由外层读取本 cell 开发证据，
记录分析/建议并签入只读复盘文件后放行；开发时钟不因等复盘而重置。
所选 bundle 在新 runtime 至少一次正常 oracle 成功且通过完整性检查，
才进入冻结 1–50；三轮无合格结果就跳过测试。模型仍为本地 Qwen3.8-Flash-Next，
native CC 2.1.220 / xhigh / 1M / 64K；900 秒 / 14 小时 / 5 小时 watchdog 保留。

输出根 `/mnt/workspace/aspire-world-runs/dlc-supervised-repair-20261014/`，
两个 cell 分别为 `stove_supervisedrepair20261014_r1` 与
`wine_supervisedrepair20261014_r1`。各自 parent 为 R06 同任务 r1；先验清单
与哈希见[本轮部署记录](DLC_SUPERVISED_REPAIR_20261014.md)。目前没有测试
bundle、测试 seed 或四类测试终结计数，最终成功率为“—”。

16:25 进展核对：云端作业和 Pod 均 Running，两个 campaign 为 running / round 1
open，没有轮间待复盘记录，没有选择或冻结程序。stove 实际计入预算 1 次，
seed 51 于 16:06:50–16:13:51 执行 421 秒，sandbox_rc=0、task_completed=0，
是正常任务失败；snapshot 与 alias-smoke 不重复计数。失败日志有连续
not_reached，位置误差约 0.11–0.33 m，未验证旋钮转动。求解器随后修改姿态
函数，但 16:23 的 seed 52 initial 提交因“同批 initial bundle 必须一致”在
执行前被拒绝，未进入 ledger、未消耗 seed 52；求解器正在调整提交方式。
wine 实际计入预算 1 次，seed 51 于 16:22:44 启动，尚未结束，不能按失败计数。

截至核对时，两项正式开发均无成功信号，50 个 held-out seed 均未开始。
Qwen、两套 SAM3 / GraspNet / Pyroki 日志未见新 ERROR、OOM 或 500；最近
Qwen 每个活跃请求约 10 token/s，日志仍更新。当前耗时主要在读取旧证据、
生成/分析策略与处理协议调用；不是已完成了大量 seed 后在等待复盘。
本次只检查和更新外层记录，没有重启服务、修改冻结代码、额外试跑或放行轮次。
快照：本轮部署目录 `operator-status-20261014-162418.json`，另以 16:25
账本与原生工具日志复核后续动作。

### R07 终止核对（2026-10-15）

10 月 15 日 15:43 云端核对：作业 `dlcxa3n63xgkmj21` 和唯一 Pod 均 Failed。
两项 campaign 于当日 04:29:58 以 `development_failed` 退出，原因为
`development deadline passed with no new oracle success`。节点 04:30:14
完成清理；两个 campaign 返回 1，Qwen 返回 0，六个感知/IK 服务收到 TERM
退出。没有新 GPU 故障证据；云端 Failed 对应本次开发未通过的退出码。

**实际只完成第一轮，第二、三轮没有执行。** stove 在 10 月 14 日 18:14:22
完成第 15 次尝试、18:45:18 进入 awaiting_review；wine 分别为 21:10:13、
21:41:08。两个复盘目录均没有审批文件，没有启动后续会话；分别等待约
9 小时 45 分和 6 小时 49 分，直到 14 小时开发截止。轮间复盘未接续是本次
协调流程未完成，不能表述成“已经跑完 3×15，策略仍全部失败”。已用次数
各为 15，未使用的 30 次额度不等于能重置过期时钟后直接续跑。

以下为**开发**结果，均为 seeds 51–65 各一次，不是冻结测试成功率：

| 任务 | 合格成功 | 正常任务失败 | 程序报错 | 基础设施错误 | 已用 / 计划上限 |
| --- | --- | --- | --- | --- | --- |
| stove | 0 | 10 | 5 | 0 | 15 / 45 |
| wine | 0 | 14 | 0 | 1（seed 51 超时） | 15 / 45 |

stove 的 seed 57 是重要的原始信号：`reward=1`、`task_completed=1`，但
`sandbox_rc=1`，报错发生在后续 `measure_tab` 的候选排序。
seeds 55、57、58、63、65 均因 `sorted(cands)` 比较包含 NumPy 数组的元组
触发 ambiguous truth value。按本研究“正常结束且 oracle 成功”的规则，
seed 57 归为 crash，不计成功或作为冻结资格；也不能把它说成从未出现成功信号。
第一轮实际使用同一 bundle `758d5ce9c519271ab5491113cbb5852cfe7bec6e7cb2c86272c40208a427adbc`。
`fix_code.py` 已有排序 key 与夹爪俯仰方向修正，但没有执行记录，不能声称修复有效。

wine 的 seed 51 达到 900 秒 trial watchdog，计为 infrastructure_error；
seeds 52–65 正常结束但均失败。其中 12 次在无法建立水平姿态/持物偏移后
主动退出，seeds 62、65 在抓取失败后退出。部分记录有瓶体观测丢失、
结构重建失败和动作被中断；首个 seed 的 GraspNet 已返回 23 个候选，
此前的感知 OOM 不是这份日志的直接失败点，超时的更细原因仍需单独定位。

两个 cell 均 selected=null、stage1_complete=false，各自存在
`supervised/heldout_skipped.json`，没有 heldout 目录或正式测试 seed。
因此最终测试率仍为“—”，不能填 0/50。机械 ledger 仍保留 awaiting_review
以记录中断位置，campaign 终态和退出回执已确认停止，不表示当前仍在等待。
本次只归档核对，未改旧账本/时钟、未补写追溯审批、未重启作业。
完整统计、时间线及账本/摘要/退出文件 SHA256 见[终止核对][r07-results]。

## 完整测试的核对信息

下面七条均已核对：测试种子恰为 1–50、全部使用同一个冻结 bundle，四类
终结结果相加为 50，未执行和未决 seed 均为 0，记录的成功率等于成功数 / 50。
完成时间取最后一个测试 seed 的结束时间，精确到北京时间分钟。

| 轮次 / 任务 / 重复 | 测试完成时间 | 成功 | 任务失败 | 程序崩溃 | 基础设施错误 | 冻结 bundle SHA256 |
| --- | --- | --- | --- | --- | --- | --- |
| R08 / stove / r2 | 2026-10-17 20:12 | 50 | 0 | 0 | 0 | `a7b943f4c6d14c0c555fd94c5ef377eabe240ad49f58facf0250dc9fb7db5c2c` |
| R08 / wine / r2 | 2026-10-17 19:44 | 3 | 47 | 0 | 0 | `26e1acb6bc0fa2b752687613f97073b11b7090a34d085813fe6301ee44ad7657` |
| R06 / drawer / r1 | 2026-10-14 07:42 | 43 | 6 | 1 | 0 | `56ff803cc5e00d66d17ceb7bccbaed6bf51254e08aac60e93f2652615ecd4990` |
| R04 / bowldrawer / r1 | 2026-10-12 10:49 | 45 | 4 | 1 | 0 | `f936c9212ba21be03d29f9bc938b3b3dc6b7c4d505bff30c1ecaf8207c3f71a6` |
| R02 / bowl / r2 | 2026-10-11 18:37 | 44 | 6 | 0 | 0 | `4f9760da7942968fbc62ede71080359529d540f1267b3a4184b001ad23689fdc` |
| R02 / wine / r1 | 2026-10-11 22:02 | 12 | 38 | 0 | 0 | `a698c72d4d4088a88d64b667552812d395d4c834f98477a6ba5606a71bd6d00c` |
| R01 / bowl / r1 | 2026-10-11 14:22 | 22 | 28 | 0 | 0 | `28a208a4776fb58d7b608449df42fe6623e02f0996b8efc340b18d67280f96d3` |

这些结果属于不同方法、运行版本和独立重复；查看各行自己的成绩，不合并为
一个“项目总体成功率”。R01 的旧 `campaign_state.json` 停留在
`heldout_refused`，但其后续冻结测试已完成；本表依据完整的
`heldout_result.json` 和逐 seed 账本记载 22/50，不依据过期的 launcher 状态。

## 源码版本与 Git 对应

Git commit 指工程源码基线；runtime SHA256 指实际冻结运行树清单，bundle
SHA256 指该任务冻结的程序及 world/config。三者分别记录，不能互相代替。
当前几轮含未提交改动，不能只 checkout 表内 commit 就声称复现了实验。

| 版本 | Git 基线或记录缺口 | 实际 runtime manifest SHA256 | 版本证据 |
| --- | --- | --- | --- |
| V08：单 wine 工程辅助修复 | `63a7fc7382fb258b962372490daaef586fc67fe8`，`SZ-DEV`，含77文件快照；10月24日另行发布 | `71dee4901443f03f16be14c3fed7a3e2ee751b1e985bc89523137ff8494d4e3b` | [本轮记录](DLC_WINE_REPAIR_20261019.md)、[发布来源](SYNC_SCOPE.md) |
| V07：单节点八卡四实验 | `63a7fc7382fb258b962372490daaef586fc67fe8`，`SZ-DEV`，含四cell布局、独立起点与自动复盘 | `e47624c88c4b6d19afd2f72db6ab76b4c2407d3a14b9faaa747db06b7e453662` | [源码身份](/mnt/workspace/aspire-world-deployment-20261017-unit8-four/launch-source-identification.json)、[冻结清单](/mnt/workspace/aspire-world-deployment-20261017-unit8-four/runtime-freeze-v2.json) |
| V06：v3 分轮复盘修复 | `63a7fc7382fb258b962372490daaef586fc67fe8`，`SZ-DEV`，含 30 份覆盖源码及未提交修复 | `82291384b0519e08dd9fcbe4afcdcf7da1fed80a0fabba55f1c3cb9532dcf699` | [启动源码身份][v06-source]、[实际冻结清单][v06-freeze] |
| V05：v3 修复与续跑 | `63a7fc7382fb258b962372490daaef586fc67fe8`，另含未提交修复和续跑实现 | `ae768364c4586fb8845fa5c3e5f6b331ba357fabbefeee1d670add69e5c9d91e` | 工程修复 `coordinator-state.json` 的 `base_commit`；[runtime 准备清单][v05-prepared] |
| V04：v3 初轮 | 启动时 Git HEAD 未随运行清单归档。发布源码参考为 `d88ca05172ebac8422dd266bc4ad707c36e772e4`，不能当成完整实跑版本 | `dd0a5a5651855fab8602b3bbf3e9374e3af5b4259c2786dba98392ac81fe6ba8` | [开发交接](CODE_AS_WORLD_HANDOFF_20261012.md)、[runtime 准备清单][v04-prepared] |
| V03：Semantic v2 | 未单独归档 Git HEAD；保留增量源码和实际冻结哈希，待历史版本凭据补齐 | `66c2b0e09514e9e3bd0c1b7c7a8f3531258d382765252856b99b3db6a78ccaef` | [runtime 准备清单][v03-prepared] |
| V02：RS 验证后的 Streaming v1 | 沿用 V01 的 `c94a4876106c12c3a89fc5d884d316c0a4109490` 来源基线，另有 runtime 增量；未单独归档启动 HEAD | `051be6719b8a81f4ee0bd81765905f68e9e2be6499111fe7275b7e1a13de2a44` | [来源复制清单][v02-staging]、各 cell 的 identity / 冻结结果 |
| V01：最初 Streaming v1 | `c94a4876106c12c3a89fc5d884d316c0a4109490`，另含源码补丁和新增文件 | `6b6e6f3a45dc9de8d1354946215a56e49d1c037f177f5489d34113ac2c09628b` | [源码部署记录][v01-source]、[源码交接记录][v01-handoff]、[冻结清单][v01-freeze] |

V05 的基线原始记录：
`/tmp/aspire-world-v3-repair-20261013/coordinator-state.json`。
版本缺口保留在表中，不用今天的 Git HEAD 追填旧实验，也不为历史冻结树
创建一个事后 commit 冒充当时的运行版本。

## 轮次身份、停止原因与结果位置

以下路径均在 RS 或共享 `/mnt/workspace` 存储，原始结果不复制进 Git。
记录键是“轮次 + cell”，同名 cell 在修复阶段不能覆盖父轮行。

| 轮次 | 运行类型 / 作业 | 结果位置与必要说明 |
| --- | --- | --- |
| R09 | 空间 `1493863`；DLC `dlcghv217zqk880a`；单 wine 新修复研究，用户停止 | `dlc1493863-wine-repair-20261019/wine_supportrepair20261019_r1`；第1轮15次、第2轮3次，0成功、18正常失败、0崩溃、0基础设施错误，无未决计费尝试，未测。父输入仅R08 wine r1自己的开发材料，旧45次不导入新账本。 |
| R07 | 默认空间 `1000309`；DLC `dlcxa3n63xgkmj21`；新分轮复盘修复研究，已退出 | `dlc-supervised-repair-20261014/{stove,wine}_supervisedrepair20261014_r1`；各完成第 1 轮 15 次，等复盘至 10 月 15 日 04:29 开发截止；第 2、3 轮未执行，测试跳过。详见[本轮记录](DLC_SUPERVISED_REPAIR_20261014.md)。 |
| R06 | 默认空间 `1000309`；DLC `dlc6eu0wbbqxatvx`；R05 各自的修复续跑 | `dlc-default-v3-repair-20261013`；控制目录名含 `sam2articulationrepair20261013`。累计每 seed 三次尝试保留；整体五小时上限取消。drawer r2 继承 seed 52 无评分且三次耗尽的 terminal blocker，未冻结。详见[本轮记录](DLC_REPAIR_CONTINUATION_20261013.md)。 |
| R05 | 空间 `1493863`；DLC `dlc1wxknbfaya30g`；包含 drawer r1 从 DSW 向 DLC 的迁移 | drawer r1、stove r1、wine r1 在 `rs-articulation-v3-20261012`，drawer r2 在 `dlc1493863-v3-four-5h-20261012`。同一身份迁移不新增任务行；五小时截止后全部保留为未完成，62 次开发尝试，未冻结。详见[停止轮记录](DLC_RUN_20261012.md)。 |
| R04 | Semantic v2；DLC `dlc14wy2vfv8hnal` | `semantic-v2-pilot-20261011/{task}_sam2semantic_r1`。bowldrawer 完成；drawer 在 37 次开发尝试、0 成功后由用户停止，回执确认 Stopped。 |
| R03 | Streaming v1 stove；DLC `dlc1qjoy3jw2bk9g` | `streaming-stove-dlc-20261011/stove_sam2streaming_r1`。15 次开发尝试、0 成功后用户停止；没有正式测试成绩。 |
| R02 | Streaming v1 四任务；DLC `dlc1k10phnxcesaz` | `dlc-four-rs-validated-20261011/{task}_sam2streaming_r{n}`。bowl / wine 完成；bowldrawer / drawer 分别在 34 / 29 次开发尝试、0 成功后停止。 |
| R01 | Streaming v1 bowl 首次独立重复；DSW | `streaming-bowl-20261010/bowl_sam2streaming_r1`。39 次开发尝试，随后完成冻结测试；保留过期 campaign 状态与完整测试结果的差异。 |

上述结果相对路径均以 `/mnt/workspace/aspire-world-runs/` 为根。
工程合成测试、只移动回家的诊断和没有开发账本的空准备目录
`streaming-four-dlc-20261010` 不记为任务成功率实验。

## 后续维护规则

1. 新实验实际启动时添加任务行，记录轮次、任务、方法、重复、父实验（如有）、
   模型与协议、输出目录、作业 / workspace，以及**启动当时完整 Git HEAD**、
   branch、是否有未提交或未跟踪的运行源码、runtime 清单路径与 SHA256。
   含改动时保存源码差异 / 文件哈希证据，不能只写 `dirty`。
2. 每次用户请求状态汇报以及实验终止、取消、冻结测试结束时更新相应行。
   新修复阶段追加独立轮次；普通迁移或 resume 更新原行，保留原身份和时钟。
3. 最终成绩取实际冻结评估报告，并核对逐 seed 状态、完整性、分母和 bundle。
   单列 success / failure / crash / infrastructure_error 及未执行数；没有测满
   原协议种子时写“测试未完成”，不称为最终成功率，不把开发反馈当测试结果。
4. 旧状态文件和结果冲突时保留说明，按最终结果、停止回执和实际作业状态核对；
   Git 号缺失时写“未归档”，不从当前工程 HEAD 或目录日期推测。
5. 成功率简表和本页包含跨实验开发摘要和 held-out 成绩，**仅供用户及外层协调器**。
   两份文档都不提供给求解器，不复制进 solver 可读的冻结运行树；工程副本保持
   root 私有，未来部署预检须验证实验 UID 不能读取两份文档及其副本。
   汇总文档无需停止实验。

新任务行模板：

| 轮次 / 开始日期 | 任务 | 方法 / 重复 | Git 基线 / 源码版本 | 当前或最终状态 | 冻结测试成功 / 总数 | 最终成功率 | 原始证据 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 新轮次 / YYYY-MM-DD | 任务简称 | 方法 / rN | 启动 HEAD；runtime 版本 | 开发运行中 | 未开始 | — | 账本路径 |

## 早期研究补录范围

2026-09 至 2026-10-09 的 code-world rerun、C 修复消融、Qwen foundation、
closed-loop、gate / prediction 等研究的源码和计划在
`aspire/sim/docs/experiments/`，原始结果主要在旧服务器
`rbs-debug:/mnt/home/gewang/experiments/`。本次未取得这些轮次的原始结果和
启动版本凭据，不宣称 18 条已覆盖整个项目历史；计划中的 cell 也不等于已运行。
取得原存储后逐个补录，保留它们自己的协议、模型、运行身份及 seed 分区。

已有旧资料入口：

- [C 修复消融](../aspire/sim/docs/experiments/code-world-c-opt-ablation-20260920/PROTOCOL.md)
- [Qwen Foundation R2](../aspire/sim/docs/experiments/code-world-qwen-foundation-r2-20260930/PLAN.md)
- [Closed-loop R3](../aspire/sim/docs/experiments/code-world-qwen-closed-loop-r3-20261007/RUN_PLAN.md)
- [Gate ablation](../aspire/sim/docs/experiments/code-world-gate-ablation-20261005/PLAN.md)
- [Prediction study](../aspire/sim/docs/experiments/code-world-prediction-base-20261008/PLAN.md)

[r07-stove-r1]: /mnt/workspace/aspire-world-runs/dlc-supervised-repair-20261014/stove_supervisedrepair20261014_r1/campaign_state.json
[r07-wine-r1]: /mnt/workspace/aspire-world-runs/dlc-supervised-repair-20261014/wine_supervisedrepair20261014_r1/campaign_state.json
[r07-results]: /mnt/workspace/aspire-world-deployment-20261014-supervised-repair/operator-results-20261015.json
[v06-source]: /mnt/workspace/aspire-world-deployment-20261014-supervised-repair/launch-source-identification.json
[v06-freeze]: /mnt/workspace/aspire-world-deployment-20261014-supervised-repair/runtime-freeze.json
[r06-drawer-r1]: /mnt/workspace/aspire-world-runs/dlc-default-v3-repair-20261013/drawer_sam2articulationrepair20261013_r1/heldout/heldout_result.json
[r06-stove-r1]: /mnt/workspace/aspire-world-runs/dlc-default-v3-repair-20261013/stove_sam2articulationrepair20261013_r1/cell/development_state.json
[r06-stove-heldout]: /mnt/workspace/aspire-world-runs/dlc-default-v3-repair-20261013/stove_sam2articulationrepair20261013_r1/heldout/heldout_state.json
[r06-stove-result]: /mnt/workspace/aspire-world-runs/dlc-default-v3-repair-20261013/stove_sam2articulationrepair20261013_r1/heldout/heldout_result.json
[r06-wine-result]: /mnt/workspace/aspire-world-runs/dlc-default-v3-repair-20261013/wine_sam2articulationrepair20261013_r1/heldout/heldout_result.json
[r06-pause]: /mnt/workspace/aspire-world-deployment-20261013-dlc-default-v3-repair/operator-pause-20261014-120120/paused-results.json
[r06-wine-r1]: /mnt/workspace/aspire-world-runs/dlc-default-v3-repair-20261013/wine_sam2articulationrepair20261013_r1/cell/development_state.json
[r06-drawer-r2]: /mnt/workspace/aspire-world-runs/dlc-default-v3-repair-20261013/drawer_sam2articulationrepair20261013_r2/campaign_state.json
[r05-rank0]: /mnt/workspace/aspire-world-runs/dlc1493863-v3-four-5h-20261012/entry/rank0/progress-report.json
[r05-rank1]: /mnt/workspace/aspire-world-runs/dlc1493863-v3-four-5h-20261012/entry/rank1/progress-report.json
[r04-bowldrawer-r1]: /mnt/workspace/aspire-world-runs/semantic-v2-pilot-20261011/bowldrawer_sam2semantic_r1/heldout/heldout_result.json
[r04-drawer-r1]: /mnt/workspace/aspire-world-runs/semantic-v2-pilot-20261011/drawer_sam2semantic_r1/operator_cancellation.json
[r03-stove-r1]: /mnt/workspace/aspire-world-deployment-20261011-semantic-v2-ops/stove-stop-receipt.json
[r02-bowl-r2]: /mnt/workspace/aspire-world-runs/dlc-four-rs-validated-20261011/bowl_sam2streaming_r2/heldout/heldout_result.json
[r02-bowldrawer-r1]: /mnt/workspace/aspire-world-runs/dlc-four-rs-validated-20261011/bowldrawer_sam2streaming_r1/operator_cancellation.json
[r02-drawer-r1]: /mnt/workspace/aspire-world-runs/dlc-four-rs-validated-20261011/drawer_sam2streaming_r1/operator_cancellation.json
[r02-wine-r1]: /mnt/workspace/aspire-world-runs/dlc-four-rs-validated-20261011/wine_sam2streaming_r1/heldout/heldout_result.json
[r01-bowl-r1]: /mnt/workspace/aspire-world-runs/streaming-bowl-20261010/bowl_sam2streaming_r1/heldout/heldout_result.json
[v05-prepared]: /mnt/workspace/aspire-world-deployment-20261013-dlc-default-v3-repair/runtime-prepared.json
[v04-prepared]: /mnt/workspace/aspire-world-deployment-20261012-rs-articulation-v3/prepared.json
[v03-prepared]: /mnt/workspace/aspire-world-deployment-20261011-semantic-v2/prepared.json
[v02-staging]: /mnt/workspace/aspire-world-deployment-20261011-rs-e2e/runtime-staging.json
[v01-source]: /mnt/workspace/aspire-world-deployment-20261010/source-deployed.json
[v01-handoff]: /mnt/workspace/aspire-world-deployment-20261010/source-handoff.json
[v01-freeze]: /mnt/workspace/aspire-world-deployment-20261010/streaming-runtime-freeze.json
