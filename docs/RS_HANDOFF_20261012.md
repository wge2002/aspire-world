# RS 项目交接：ASPIRE Streaming World（2026-10-12）

## 接手入口与当前授权

项目工程目录：`/root/github/aspire-world`；仿真工作目录：
`/root/github/aspire-world/aspire/sim`。SSH：`ssh rs`，对应
`ssh root@47.116.39.128 -p 1014`。GitHub：
<https://github.com/wge2002/aspire-world/tree/SZ-DEV>，分支必须保持大写 `SZ-DEV`。
以本文所在 Git commit 为本次发布版本，通过 `git rev-parse HEAD` 核对。

用户要求由 RS 上的 agent 直接接手，以减少学校网络到 RS 的断链影响。
先读根目录 `AGENTS.md`、`CC_USAGE.md`，再读本交接及共同协议。
本次同步不启动新实验；不要把“目录/配置准备完成”写成“正在训练”。
后续已有实验授权是：在 RS 上优先跑新 v3 的 drawer、stove、wine，
顺序为 drawer → stove → wine，串行、每任务独立 repeat1 的 pilot。
这是三-cell 子集，尚不是每方法五任务 × 两重复的完整十-cell 比较。
没有新 DLC 作业授权，不能恢复已经停止的旧作业。

## 代码实现与验证程度

当前显式方法 `sam2-articulation-qwen-v3`，原生实验 profile
`sam2-articulation-native-v3`，runtime mode `sam2-articulation-world-v3`。
v1/v2 仍保留，旧冻结 runtime 和结果没有改写。

| 文件 | 功能 |
| --- | --- |
| `aspire/sim/cap/world_model/World.py` | 观测、预测、关系分层；时间戳、证据、失效与 episode 生命周期 |
| `semantic_world.py` | 部件假设图、身份关联、支持分数与带证据的结构修订 |
| `rigid_tracking.py` | 公开 RGB-D 特征对应、双向光流检验、鲁棒刚体配准；缺证据时断开运动参考 epoch |
| `joint_models.py` | 固定／滑动／转动模板，拟合轴和参数、时序验证；小弧线不直接当作滑动 |
| `articulated_world.py` | 父部件相对运动、关节模型查询/预测、平移及角度交互反馈 |
| `qwen_world_review.py` | 事件触发的 loopback Qwen JSON 提案；记录实际参数、使用量和哈希；不执行生成代码 |
| `streaming_replay.py`、`streaming_bridge.py` | 感知、控制检查点、模型复查及程序公开接口的集成 |
| `scripts/libero/streaming_fixloop_{protocol,campaign,heldout}.py` | 原生 CC 开发、计费、选择、冻结与完整 50-seed 评分 |

Qwen 提出语义拆解、父子连接和模型族；轴、位移、角度由数值程序根据公开观测拟合。
SAM3 初始化/重定位，SAM2 streaming 跟踪，不能把 mask 中心当作完整刚体位姿。
部件与模型均是假设，分数不是校准概率。根部件只能显式表示 free/unknown，
缺少被跟踪的参考主体时不能得到受约束关节模型。

数值预测默认限于已观察范围；`extrapolate=True` 显式返回未验证外推，
不表示知道真实关节限位。`world_begin_joint_interaction` 配合
`world_finish_interaction` 检查相对平移及转角；中心不动不能证明旋转成功。
需要新鲜部件运动、实际到达报告和独立的公开接触关系证据。
接受结构/模型族改变后，正在执行的旧轨迹会在下一个 motor step 前中断。
不合格的模型提案保留原有效 World 并记录拒绝；服务故障或跟踪失效仍中断。

读取 `aspire/sim/docs/articulation-streaming-world.md` 获取完整接口与声明因素。
尚未证明通用自动部件发现、完整关节辨识或新版本任务成功率。
没有力传感器、质量/摩擦估计或隐藏关节真值。

已验证证据（均在 RS，日志不进 Git）：

- 相关旧/新回归曾执行 296 项：295 通过；一项 Git 完整性检查因临时副本无 `.git`
  失败，随后在实际 RS 工程 Git 仓库单独通过。保留原始失败记录。
- 后续修订的组合契约检查 64 项通过；最终 v3 契约 30 项通过，含转角反馈、
  表征变化中断、根节点规则及同一冻结规则覆盖 50 个**合成**测试 seed。
- 最小真实链路的第二次诊断执行成功：83.60 秒、`sandbox_rc=0`；实际
  SAM3/SAM2/Qwen，Qwen 接受 defer 耗时 56.11 秒；robot updates=1，object frames=2，
  无 tracking loss。测试代码只执行 home/刷新等接口，未完成 drawer 任务。
- 第一次诊断执行因不合法 Qwen 关节提案失败，耗时 435.49 秒。
  工程诊断账本总计**已消耗两次** seed51 的程序尝试，不能删除/退款。
- 更早的整场景 Qwen 生成请求曾到 900 秒超时；它没有开始程序执行。
  最小公开区域描述生成只耗时 8.72 秒。应保持复查针对当前事件，避免整场景推理。

验证目录：`/mnt/workspace/aspire-world-validation-20261012-articulation-v3`。
关键文件：`joint-contracts.log`、`final-contracts.log`、`regression.log`、
`legacy-git-guard.log`、`short-run-2.log`；账本
`engineering-cell/development_state.json`；逐次诊断产物在
`engineering-cell/development/diagnostic/seed_51/attempt_{1,2}/`。
不要把这些工程生成配置、反馈或代码交给新实验求解器。

## 运行位置与接手检查

| 用途 | RS 路径 |
| --- | --- |
| 可继续修改的工程 Git 仓库 | `/root/github/aspire-world` |
| v3 工程验证源码副本 | `/mnt/workspace/aspire-world-validation-20261012-articulation-v3/source` |
| 正式 RS runtime 准备目标 | `/mnt/workspace/aspire-world-runtimes/articulation-v3-SZ-DEV-20261012` |
| 正式 RS 配置/冻结清单准备目录 | `/mnt/workspace/aspire-world-deployment-20261012-rs-articulation-v3` |
| 正式 RS 新实验结果目录 | `/mnt/workspace/aspire-world-runs/rs-articulation-v3-20261012` |

交接检查时后台 `prepare_rs_campaign.py` 仍可能在复制/冻结源码。
这个操作只准备目录、用户、case 和 manifests，**不会运行任务**。
尚未实现/启动正式串行 supervisor；不得因为有 `queue.json` 就声称队列已运行。
若准备目录已存在，不要再次直接运行 helper 的 fresh 初始化，也不要删除后重来。
先检查 `prepared.json`、`queue.json`、正在运行的进程及已有 cell 账本。

```bash
cd /root/github/aspire-world
git branch --show-current
git rev-parse HEAD
pgrep -af '[p]repare_rs_campaign.py'
ls /mnt/workspace/aspire-world-deployment-20261012-rs-articulation-v3
ls /mnt/workspace/aspire-world-runs/rs-articulation-v3-20261012
```

已准备的 cell 命名为 `drawer_sam2articulation_r1`、`stove_sam2articulation_r1`、
`wine_sam2articulation_r1`。对应账号由 helper 申请为
`aspire-rs-articulation-{task}-r1`，UID 30101–30103；HOME 在
`/mnt/workspace/aspire-world-users/rs-articulation-{task}-r1`。
必须以实际 `/etc/passwd`、`queue.json` 为准，确认准备结束和 UID 无冲突。
每个 cell 独立 HOME/control/Claude config，源码对其只读。

当准备完成且再次通过预检，可启动 drawer：

```bash
runuser -u aspire-rs-articulation-drawer-r1 -- \
  /mnt/workspace/aspire-world-envs/libero/bin/python -B \
  /mnt/workspace/aspire-world-runtimes/articulation-v3-SZ-DEV-20261012/aspire/sim/scripts/libero/streaming_fixloop_campaign.py \
  --case /mnt/workspace/aspire-world-deployment-20261012-rs-articulation-v3/drawer.case.json
```

启动前切到对应 runtime 的 `aspire/sim`，设置公开 LIBERO config、CUDA compat
库路径等已核实环境；默认包含外层 heldout，不要使用 `--no-heldout` 偷改实验。
需要在 RS 的持久 tmux/进程 supervisor 中运行，SSH 断开也应继续。
先看 launcher 的 `campaign_state.json`、`sessions/`、`outer_init.json`、
`cell/development_state.json`；前置 native probe 成功后 `init` 才开始 14 小时钟。
已有账本的 cell 不能重新初始化；只有符合 launcher 状态的显式 `--resume`
才可以继续，且预算、期限均不重置。基础设施失败保留记录并单独汇报。
第一任务结束后同样检查，再启动 stove，最后 wine；不要共享生成代码/反馈/技能。

本次使用过的无凭据辅助代码也已发布在
`aspire/sim/scripts/ops/rs_articulation_v3_20261012/`。
它们是固定日期部署的记录/工具，包含 RS 绝对路径，非通用 quick start。

## 模型、服务与资源

RS 是共享主机，其他项目也在用 GPU；不要杀其他项目进程或整个 DSW 实例。
本项目 Qwen PID 记录在验证目录 `qwen.pid`，启动信息在 `qwen-start.json`，日志 `qwen.log`。
交接时模型为 `http://127.0.0.1:8127`，served name `qwen3.8-flash-next`。
核验：`curl -s http://127.0.0.1:8127/v1/models`；应返回 `max_model_len=1000000`。
PID 会变化，以 PID 文件、实际命令和端口归属核对，不盲目 kill 旧 PID。

Qwen3.8-Flash-Next-FP8：主模型 125B/激活约6B，另51B n-gram embedding、4B MTP；
权重约185.6GB，revision `236dfdf285828023ca3bcd3f37366c58a3469b13`。
TP4，当前 GPU `4,5,7,6`；用户授权选择当时占用最少的四张共享卡。
显存预留0.80（原专用 DLC 配置0.95），xhigh、配置1M context/64K output 保持不变。
共享卡观测到生成约10 tokens/s；不能将其吞吐当作专用 DLC 吞吐。
新 RS cells 使用 simulator/SAM2 GPU0，既有 SAM3/GraspNet 在GPU6，Pyroki在CPU。
实际卡占用会变；启动前重查，别静默改模型/感知/并发条件。

| 组件 | 路径或端口 |
| --- | --- |
| LIBERO Python | `/mnt/workspace/aspire-world-envs/libero/bin/python` |
| Qwen/vLLM Python | `/mnt/workspace/aspire-world-envs/qwen38-vllm/bin/python` |
| 实验 native CC | `/mnt/workspace/aspire-world-tools/claude/2.1.220/claude` |
| SAM3 / GraspNet / Pyroki | `127.0.0.1:8114 / 8115 / 8116` |
| SAM2 streaming | `/mnt/workspace/aspire-world-assets/SAM2_streaming` |
| SAM2 checkpoint | `/mnt/workspace/aspire-world-models/sam2.1_hiera_small.pt` |
| Qwen weights | `/mnt/workspace/aspire-world-models/Qwen3.8-Flash-Next-FP8` |
| 公开 LIBERO config | `/mnt/workspace/aspire-world-deployment-20261010/public-libero-config` |
| EGL vendor config | `/mnt/workspace/aspire-world-deployment-20261010/egl/10_nvidia.json` |

已有环境：LIBERO Torch2.12.1/cu126、NumPy1.26.4、Mujoco3.3.0、robosuite1.4；
Qwen vLLM0.29/Torch2.13/cu130。使用已有部署清单核对实际版本，不随意重装。
工程 CC 与实验 CC 分开：工程通常 Opus5.5/high，实验必须 native CC2.1.220/localQwen/xhigh。

## 必须保持的共同协议

权威入口：`aspire/sim/docs/libero-pro-common-protocol.{md,json}`。
套件 `libero_goal_swap`；配置 `env_configs/libero/franka_libero_traced.yaml`，
API `FrankaLiberoApiReducedSkillLibraryTraced`，`privileged=false`、`max_steps=4000`。
只用公开观测与实际感知，C条件无高层策略MD库、预整理物体清单、历史任务解法。
World/策略不能读取隐藏状态或成功判据；oracle仅在外层开发反馈与评分。

开发51–65，每seed最多三次**总计费程序执行**（含诊断、报错、超时），
不是每次修改重新获得三次；没有全局15版本上限，也无新增动作/恢复次数帽。
trial900s，开发14h，测试5h，继续/重启不重置账本或绝对期限。
只用开发证据选择一个泛化程序，冻结 policy/World/config/dependencies/更新规则，
同一个冻结程序测试1–50，测试期间不得修复或逐seed选程序。
成功率 success/50，分别列success/failure/crash/infrastructure_error，
程序报错不能因曾完成环境任务而改判成功；不完整cell独立报告。

共同环境修复版本 `libero-pro-common-env-v3-statepack-2026-10-10`：修正重复reset；
测试保留原50状态，开发新增固定15状态，无取模/跨分区复用。
manifest：`/mnt/workspace/aspire-world-assets/common-initial-states-v3/initial_state_manifest.json`，
SHA256 `1668dd6b6bebeb0261ed507ab15f6326ac73d7a586180ca2b30a45c8b5a106e4`。

## 已有实验结果与已停止作业

| 任务／版本 | 完整名称 | 结果 |
| --- | --- | --- |
| bowl／v1 | `put_the_bowl_on_the_plate` | 44/50，88%，完整测试 |
| bowldrawer／v2 | `open_the_top_drawer_and_put_the_bowl_inside` | 45/50，90%；45成功、4失败、1crash、0infra |
| drawer／v2 | `open_the_middle_drawer_of_the_cabinet` | 37次开发、0成功，用户已停止，无正式测试率 |
| wine／v1 | `put_the_wine_bottle_on_the_rack` | 12/50，24%，完整测试 |
| stove／v1 | `turn_on_the_stove` | 15次开发、0成功，已停止，无正式测试率 |

这是一组混合版本/重复的 pilot 结果，不是完整配对比较，不能计算成新v3总体成功率。
bowldrawer90%也不证明在线语义结构修订贡献：其柜体/抽屉部分仍是未跟踪语义节点，
当时没有观察到持续在线结构修订。v3尚无正式任务成绩。

旧 DLC `dlc14wy2vfv8hnal` 已按用户要求停止，平台/Pod分别于
2026-10-12 11:56:06/11:56:11（北京时间）确认Stopped，一节点八卡已释放。
停止回执：`/mnt/workspace/aspire-world-runs/semantic-v2-pilot-20261011/drawer_sam2semantic_r1/operator_cancellation.json`。
旧 `dlc1k10phnxcesaz`、`dlc1qjoy3jw2bk9g` 也已停止，不能因同步源码恢复它们。

原始结果根：`/mnt/workspace/aspire-world-runs/dlc-four-rs-validated-20261011`、
`/mnt/workspace/aspire-world-runs/semantic-v2-pilot-20261011`、
`/mnt/workspace/aspire-world-runs/streaming-stove-dlc-20261011`。
保留全部旧冻结文件、账本与耗时，绝不拿其他cell生成程序作为新cell输入。

## 接手剩余工作

1. 检查后台准备进程是否结束；核对冻结清单、case、账号/权限和仍在运行的模型。
2. 如准备中断，仅补齐缺项并保留已存在目录；不要 fresh 重置整个准备/实验。
3. 用原生launcher的预检与实际probe确认第一cell，再启动持久串行执行/监控。
4. 先drawer，再stove，再wine；各自独立开发、冻结、完整评估。
5. 汇报实际任务成功数/50与机制日志：复查/拒绝/结构修订/模型变化/角度反馈次数。
   不把单纯defer、接口跑通或合成数据契约当作识别正确或成功率提高。
6. 后续推送保持 `SZ-DEV`；代码/协议/交接文档进Git，日志、原始结果、媒体、权重、
   凭据和冻结runtime留在外部。工程仓库 Contact-GraspNet 子模块的环境兼容补丁
   可能是本地dirty，保留；不要对整个仓库 `reset --hard` 或 `clean -fd`。
