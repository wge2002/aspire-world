# Code as World 开发交接

交接日期：2026-10-12，北京时间。依据“code as world 开发”对话
2026-10-10 至 2026-10-12 的 47 个回合整理；代码基线是
[`d88ca05172ebac8422dd266bc4ad707c36e772e4`](https://github.com/wge2002/aspire-world/commit/d88ca05172ebac8422dd266bc4ad707c36e772e4)，
发布分支固定为大写 `SZ-DEV`。

本线已经从任务生成的代码 World，推进到持续跟踪部件、用交互检验假设、
由事件触发 Qwen 复查的 Articulated Streaming World v3。
v3 已通过数值与接口验证，但尚无正式任务成绩。接手者应先补齐 RS 工程仓库，
核验已准备的冻结 runtime，再执行已授权的 drawer → stove → wine 串行 pilot。

先读 [AGENTS.md](../AGENTS.md)、[CC_USAGE.md](../CC_USAGE.md)、
[共同协议](../aspire/sim/docs/libero-pro-common-protocol.md)及其
[机器可读约束](../aspire/sim/docs/libero-pro-common-protocol.json)。
[RS 操作交接](RS_HANDOFF_20261012.md)保留详细部署路径、启动条件和历史产物地址；
下述接手核对更新了其中的准备状态与源码同步状态。

## 接手时的实际状态

2026-10-12 13:29 北京时间只读核对 RS：

| 位置或状态 | 已确认内容 |
| --- | --- |
| 本地及 GitHub | `SZ-DEV` 已发布 `d88ca05`，包含 v3 源码、协议、测试与操作交接 |
| RS 工程仓库 | `/root/github/aspire-world`，分支 `SZ-DEV`，HEAD 仍为 `c94a4876106c12c3a89fc5d884d316c0a4109490` |
| RS 源码同步 | 本次发布的 59 个文件中，38 个与工程仓库字节一致；21 个不同或缺失 |
| RS 验证副本 | `/mnt/workspace/aspire-world-validation-20261012-articulation-v3/source`；核对的 22 个 v3 运行时、协议与契约测试文件全部与发布源码一致 |
| 已准备的 runtime | `/mnt/workspace/aspire-world-runtimes/articulation-v3-SZ-DEV-20261012`；同一组 22 个关键文件全部一致 |
| 准备操作 | 准备进程已结束；`prepared.json` 为 `prepared`，`not_launched_yet=true` |
| 三个正式 cell | 目录已存在，队列均为 `pending`；未发现 `campaign_state.json`、开发账本或 heldout 结果 |
| 冻结清单 | `runtime-freeze.json` 已存在，SHA256 与三个 case 的绑定均一致 |
| Qwen 服务 | `127.0.0.1:8127` 返回 `qwen3.8-flash-next`，`max_model_len=1000000` |

冻结清单 SHA256 为
`dd0a5a5651855fab8602b3bbf3e9374e3af5b4259c2786dba98392ac81fe6ba8`。
清单文件哈希一致与关键源码一致，不替代启动前的完整依赖校验。

**RS 工程仓库尚未完成本轮同步。** 其中缺少 `articulated_world.py`、
`joint_models.py`、`rigid_tracking.py`、`qwen_world_review.py`、对应说明和测试；
`streaming_bridge.py`、`streaming_replay.py` 及部分协议脚本仍有版本差异。
已经验证的新实现位于上述验证副本和准备 runtime。
继续开发时先保留工程仓库现有改动，再与 GitHub 发布版本逐文件对齐。
不要把工程仓库旧 HEAD 当作 v3 runtime 的身份，也不要覆盖已冻结运行树。

## 项目目标与各部分分工

ASPIRE 提供仿真环境、公开感知和动作 API、程序执行、失败反馈、Fix Loop、
预算账本、冻结评估及技能管理。Qwen 通过原生 Claude Code 工具流程编写、
分析和修复程序；本线改进的是程序与方法，不进行 Qwen 权重训练。

Policy 决定观察、抓取、移动和恢复；World 保存状态、证据和假设，并提供
更新、查询、预测及检验。一次执行内部按固定规则更新状态；程序源码的修复
发生在开发尝试之间。测试允许当前 episode 内更新状态和拟合参数，
但使用同一个冻结程序，并在下一 episode 清空动态记忆。

对话讨论过 Python 与 JSON 的区别：状态、证据和部件图可以用 JSON 兼容数据保存；
代码负责计算、更新与判断。当前采用结构化数据加可执行规则的组合。
“Python World 优于 JSON 规则加解释器”仍是待研究的问题，尚未开展相应对照；
历史 p1 的 `off/p1` 比较也不能回答这个问题。

## 方法演进与当前生成产物

| 阶段 | World 的主要内容 | 求解器生成什么 |
| --- | --- | --- |
| 历史代码 World 与 p1 | `observed/predicted/rehearsal` 分层、事实证据、七函数接口及预测与实测核验 | 配套 policy 和任务 world 程序；一起选择和冻结 |
| Streaming v1 | 通用工程 `World.py`、SAM3 初始化、SAM2 时序跟踪、多节奏更新及控制 checkpoint | 当前 cell 的 policy、公开观测驱动的对象提示与 streaming 配置 |
| Semantic v2 | 部件假设图、身份关联、执行报告、交互检验及带证据的结构修订 | 当前场景的部件拆解、关系与策略；结构修改由程序显式提出 |
| Articulation v3 | 公开 RGB-D 刚体运动估计、父部件相对运动、固定模型拟合、事件触发 Qwen 复查 | 独立 policy/config；执行中的 Qwen 提出结构或模型族修订，数值程序检验参数 |

v3 的显式身份为：方法 `sam2-articulation-qwen-v3`、原生 profile
`sam2-articulation-native-v3`、runtime mode `sam2-articulation-world-v3`。
v1/v2 与历史生成 world/p1 保留各自接口和冻结身份。
当前通用 World 是工程输入，不能称为每个 cell 的 Qwen 从头生成的世界程序。

机器人本体状态在控制检查点更新；对象几何默认每 0.2 秒采集时钟检查更新；
关系默认每 0.5 秒检查，或按策略请求主动核验；语义与关节模型由事件触发复查。
这是一套同步协作执行路径，使用仿真采集时间，不保证墙钟实时帧率。
跟踪、复查和动作仍由同一个模拟器线程协调，在线 Qwen 的延迟计入 trial。

## v3 表征与修订规则

World 分开保存实测几何、预测、关系和语义假设。状态携带部件身份、帧时间、
证据编号及有效性；未知与可靠测得的 `False` 分开处理。
SAM3 用于初始化和重新定位，SAM2 连续跟踪掩码。
掩码的可见中心与范围只描述可见表面，不能代表完整物体位姿、接触或抓牢。

部件之间可以提出固定、滑动、转动、自由运动或未知模型。
滑动采用 `p(q)=p0+d*q`，转动采用 `p(q)=c+R(a,q)*(p0-c)`；
Qwen 提出候选模型族，数值程序用公开 RGB-D 特征对应、双向光流和鲁棒配准
估计父子相对运动，再拟合轴与参数，并用后续样本检验。
小范围圆弧不能直接判为滑动，静止观测不能单独证明固定连接；
缺少被跟踪的参考父部件、运动激励或可靠对应时保留未知。
跟踪失效会断开运动参考 epoch，不能跨缺测间隙拼接轨迹。

根部件的关节只能显式为 `parent:null` 加 `free/unknown`。
默认预测限于已观察的位移或转角范围；`extrapolate=True` 返回未验证外推。
已观察范围不是物理限位。当前也没有质量、摩擦、力反馈或完整隐藏关节真值。

初始化假设、身份冲突、可信交互效果矛盾和模型预测矛盾会排入复查事件。
Qwen 只收到当前公开画面、World 快照、事件证据与执行报告，返回受校验的 JSON；
它不能写入可执行代码或权威几何值。结构改变须取得当前 SAM3 掩码并成功
重新初始化 SAM2，之后才提交；无效提案记录拒绝，保留仍有效的旧 World。
服务故障、跟踪失效或关键未知状态仍会中断动作。

交互检验先确认机器人实际到达、公开接触关系和新鲜部件运动，再判断模型矛盾。
`world_begin_joint_interaction` 与 `world_finish_interaction` 同时检查相对平移及转角；
旋钮中心不动不能证明旋转成功。接受结构或模型族变化后，旧轨迹在下一个
motor step 前中断，由 Policy 重新观察和规划。

当前并未证明通用自动部件发现、完整关节辨识或任务性能提升。
验收应同时记录复查、拒绝、有效修订、预测误差及策略响应，
不能把 `defer`、图像跟踪次数或接口执行成功算作模型识别正确。

## 必须保持的实验条件

以下当前共同协议优先于对话早期的历史默认值；详细字段以协议及 case 为准。

| 条件 | 固定要求 |
| --- | --- |
| 环境 | LIBERO-Pro `libero_goal_swap`；`franka_libero_traced.yaml`；`FrankaLiberoApiReducedSkillLibraryTraced`；`privileged=false`；`max_steps=4000` |
| 输入 | C 条件；保留公开 API 文档、任务语言和当前观测；不提供高层策略 MD、预整理物体清单或历史解法 |
| 求解器 | local Qwen3.8-Flash-Next，native CC 2.1.220，xhigh，配置 1M context/64K output；记录实际请求参数 |
| 开发 | seeds 51–65，每 seed 最多 3 次累计计费程序执行，最多 45 次/cell；oracle 仅在外层作开发 gate |
| 测试 | 同一个冻结 bundle 完整评估 seeds 1–50；反馈不返回求解器，期间不修复或逐 seed 选程序 |
| watchdog | trial 900 秒，开发 14 小时，测试 5 小时；resume 保留原 deadline 与已用预算 |
| 独立重复 | 完整设计为每方法 5 任务 × 2 次独立开发重复，共 10 cell；当前 RS 仅 3-cell pilot |
| 隔离 | 每 cell 从头生成，独立 HOME/CC config/World/账本；不共享生成代码、反馈或新学技能 |
| 评分 | success/50；分别报告 success、failure、crash、infrastructure_error；未执行 seed 单列，不完整 cell 保留可见 |

程序 smoke、诊断、报错、超时和中断都保留计费账本；纯初始公开快照不执行程序，
可以不计费。不设全局 15 版本上限，不要求用满预算，不新增动作、恢复或查询次数帽。
程序报错即使环境曾达成任务也不能改判成功。

用户已批准共同 reset 修复及开发状态包：测试 1–50 保留原始 50 个状态，
开发 51–65 使用每任务新增的固定 15 个状态，不能取模复用测试场景。
状态包版本为 `libero-pro-common-env-v3-statepack-2026-10-10`，manifest SHA256
`1668dd6b6bebeb0261ed507ab15f6326ac73d7a586180ca2b30a45c8b5a106e4`。
状态内容只供外层加载，不向求解器或 World 暴露。

## 历史任务结果与失败线索

下表是对话最终记录的混合版本 pilot，不能汇总成 v3 成绩或完整配对比较。

| 任务 | 完整名称 | 已有结果 |
| --- | --- | --- |
| bowl | `put_the_bowl_on_the_plate` | v1 第二次独立重复：44/50，88%；6 failure，0 crash，0 infra |
| bowldrawer | `open_the_top_drawer_and_put_the_bowl_inside` | v2：45/50，90%；4 failure，1 crash，0 infra；10月12日10:50确认完成 |
| drawer | `open_the_middle_drawer_of_the_cabinet` | v2 已消耗37次开发尝试，0成功；用户停止，无正式测试率 |
| wine | `put_the_wine_bottle_on_the_rack` | v1：12/50，24%；完整测试 |
| stove | `turn_on_the_stove` | v1 已消耗15次开发尝试，0成功；已停止，无正式测试率 |

早期 drawer 的主要问题是重新分割后切换到另一层把手、把“不再移动”当作到位、
姿态参数形状错误，以及没有确认夹持就把目标跳变当作拉开。
早期 bowldrawer 的把手位移很小仍进入抓碗阶段，重复无效动作最终耗尽4000步。
当时 drawer 只跟踪柜体，bowldrawer 只跟踪碗，关系和 checkpoint 没有形成有效验收。
这些开发诊断支持增加身份、执行和交互检查，但不证明 v3 已解决任务。

v2 bowldrawer 最终仍主要跟踪碗，柜体和抽屉是未跟踪语义节点；
未观察到持续在线结构修订。因此90%不能归因于语义拆解自动纠错。
历史成绩可由外层汇报，但旧生成程序、任务反馈和轨迹不得作为新 cell 求解器输入。

旧 DLC `dlc14wy2vfv8hnal` 已于10月12日11:56确认停止并释放一个8卡节点；
`dlc1k10phnxcesaz`、`dlc1qjoy3jw2bk9g` 也已停止。
不要根据对话较早的 Running 状态恢复旧作业。

## 已完成的验证及其范围

RS v3 验证保存在
`/mnt/workspace/aspire-world-validation-20261012-articulation-v3`：

- 一组296项回归有295项通过；临时副本缺少 `.git` 导致一项完整性检查失败，
  后来在真实工程 Git 仓库单独通过，原失败记录保留。
- 后续组合契约64项通过；最终v3契约30项通过，包括角度反馈、表征变化中断、
  根节点规则和同一冻结规则覆盖50个合成 seed。这些不是50次真实任务评估。
- 第二次最小真实链路83.60秒，`sandbox_rc=0`；使用实际SAM3/SAM2/Qwen，
  Qwen 接受 `defer` 用时56.11秒，robot updates=1、object frames=2、无跟踪丢失。
  程序只做 home/刷新等接口操作，没有完成drawer。
- 第一次诊断435.49秒，因不合法Qwen关节提案失败；该工程cell的seed51累计
  已消耗两次程序尝试。整场景生成曾900秒超时且没有开始程序执行，
  后来的最小公开区域描述生成8.72秒。保持复查聚焦当前事件。

工程诊断账本是 `engineering-cell/development_state.json`，与三个正式新cell分开。
不清空两次工程尝试，也不把诊断生成配置、反馈或代码交给正式求解器。
关键日志名与逐次产物位置见 [RS操作交接](RS_HANDOFF_20261012.md)。

本地发布前39个Python文件AST、JSON解析和上传范围检查通过。
本地回归61项通过、6项跳过；完整收集缺少gymnasium/torch，
另2项失败和37项setup错误来自非root环境无法执行所有权变更。
这些环境限制未被写成源码回归通过；RS验证与本机检查分别保留。

## 源码入口与部署资源

| 阅读入口 | 作用 |
| --- | --- |
| [World.py](../aspire/sim/cap/world_model/World.py)、[streaming-world.md](../aspire/sim/docs/streaming-world.md) | 分层事实、生命周期、多节奏更新与控制桥接 |
| [semantic_world.py](../aspire/sim/cap/world_model/semantic_world.py)、[semantic-streaming-world.md](../aspire/sim/docs/semantic-streaming-world.md) | 部件图、身份与交互假设修订 |
| [rigid_tracking.py](../aspire/sim/cap/world_model/rigid_tracking.py)、[joint_models.py](../aspire/sim/cap/world_model/joint_models.py) | 公开几何运动估计与模型拟合 |
| [articulated_world.py](../aspire/sim/cap/world_model/articulated_world.py)、[qwen_world_review.py](../aspire/sim/cap/world_model/qwen_world_review.py) | 关节状态、事件复查与修订准入 |
| [articulation-streaming-world.md](../aspire/sim/docs/articulation-streaming-world.md) | v3公开接口、阈值、模型库与冻结因素 |
| [streaming-fixloop.md](../aspire/sim/docs/streaming-fixloop.md) | 原生开发、记账、选择、冻结与heldout流程 |
| [streaming_fixloop_campaign.py](../aspire/sim/scripts/libero/streaming_fixloop_campaign.py) | 单cell原生launcher与preflight |
| [RS辅助工具](../aspire/sim/scripts/ops/rs_articulation_v3_20261012/README.md) | 固定日期的准备和诊断记录；不作为通用quick start |

RS通过 `ssh rs` 接入；工程目录 `/root/github/aspire-world`，仿真根目录
`/root/github/aspire-world/aspire/sim`。实验使用既有
`/mnt/workspace/aspire-world-envs/libero/bin/python`，原生CC在
`/mnt/workspace/aspire-world-tools/claude/2.1.220/claude`。
SAM3/GraspNet/PyRoKi端口为8114/8115/8116，Qwen此次为8127。

Qwen是FP8，主模型125B、每token激活约6B，另51B n-gram embedding及4B MTP；
权重约185.6GB，使用TP4，revision
`236dfdf285828023ca3bcd3f37366c58a3469b13`。
此前共享卡部署记录为GPU4,5,7,6、显存比例0.80；模拟器/SAM2用GPU0，
SAM3/GraspNet用GPU6、PyRoKi用CPU。共享卡约10 tokens/s是当时观察值。
启动前按实际占用和service manifest复核；共享卡配置与专用DLC配置分开报告。
工程CC通常Opus5.5/high，实验CC必须保留localQwen/xhigh，二者路由不能混淆。

## 后续执行顺序与完成标准

用户最初要求RS调试通过后再授权DLC；随后明确改为DLC资源不足，先在RS跑
首轮失败或低成功率任务。当前授权是新的drawer → stove → wine，串行，
各一个独立repeat1。没有新DLC allocation，也没有完整十cell比较授权。
三个cell按14小时开发加5小时测试上限合计57小时，启动准备耗时另计。

1. 在RS工程仓库保留现有dirty修改和子模块补丁，先获取GitHub `SZ-DEV`，
   逐文件对齐发布源码与文档。RS上大量工程改动按CC_USAGE交给项目CC；
   本地实现与验证由Codex直接处理。不要整体 `reset --hard` 或 `clean -fd`。
2. 核对准备目录
   `/mnt/workspace/aspire-world-deployment-20261012-rs-articulation-v3` 的
   `prepared.json`、`queue.json`、`runtime-freeze.json`及三个case。
   准备已完成，不能重跑helper的fresh初始化或删除后重来。
3. 核对账号 `aspire-rs-articulation-{drawer,stove,wine}-r1`、UID30101–30103、
   各自HOME/control权限、只读runtime、公共LIBERO配置、依赖与服务归属。
   不杀其它项目进程，不盲目关闭整个DSW；Qwen PID以实际端口和启动记录核对。
4. 用原生launcher的preflight和真实probe验收第一cell；在持久tmux或supervisor中
   执行串行流程。`queue.json`只是数据文件，不能据此声称supervisor已经启动。
   第一任务终态确认后再启动第二、第三；默认包括外层heldout，不使用
   `--no-heldout`改变实验。
5. 每次继续先检查实际 `campaign_state.json`、sessions、开发账本和deadline。
   native probe通过后init才开始开发时钟；已有账本不能fresh重置。
   resume只延续原身份、预算和期限，基础设施失败保留独立记录。
6. 每cell按同一冻结bundle报告50个seed的四类计数、未执行seed、完整性、
   开发消耗和阶段耗时。同时报告复查/拒绝/有效结构修订/模型族改变/
   角度反馈及策略响应，区分接口验证、机制证据与实际任务成功。

正式结果根目录为 `/mnt/workspace/aspire-world-runs/rs-articulation-v3-20261012`，
cell为 `drawer_sam2articulation_r1`、`stove_sam2articulation_r1`、
`wine_sam2articulation_r1`。源码、协议与交接文档进Git；日志、原始结果、
媒体、权重、凭据及冻结runtime保留在外部。后续发布继续使用 `SZ-DEV`。
