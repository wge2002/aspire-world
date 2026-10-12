# ASPIRE LIBERO-Pro 共同开发与评估协议

版本 `libero-pro-common-v1`，2026-10-10 用户指定。适用于本条 ASPIRE 开发实验线与同事之间的方法比较。方法设计可以独立推进，以下共同条件不得隐式改变。本协议取代本实验线的历史默认值，历史实验及冻结产物保留原身份。

机器可读约束见 [libero-pro-common-protocol.json](libero-pro-common-protocol.json)。从 `aspire/sim` 执行，开发分支保持 `SZ-DEV`；推送仍需用户明确要求。协议落盘不代表启动全部 cell 或提交 DLC 作业。

## 1. 环境与任务

固定使用 LIBERO-Pro `libero_goal_swap`：

| 简称 | 任务标识 |
| --- | --- |
| bowl | `put_the_bowl_on_the_plate` |
| bowldrawer | `open_the_top_drawer_and_put_the_bowl_inside` |
| drawer | `open_the_middle_drawer_of_the_cabinet` |
| wine | `put_the_wine_bottle_on_the_rack` |
| stove | `turn_on_the_stove` |

沿用 [franka_libero_traced.yaml](../env_configs/libero/franka_libero_traced.yaml)，API 为 `FrankaLiberoApiReducedSkillLibraryTraced`，环境/API 均为 `privileged=false`，`max_steps=4000`。配置中的默认 task_id、trials、num_workers 不代替本协议的实际任务解析、种子与预算。

**共同环境版本 `libero-reset-v2-2026-10-10`（2026-10-10 用户批准）。** 旧 reset 先 `env.reset()` 再载入 `init_states[0]` 并返回旧观测，`FrankaLiberoEnv.reset` 随后载入 `init_states[(seed-1)%N]` 又调用 `env.reset()`，把选定初始状态覆盖掉。修复后（`cap/integrations/libero/__init__.py`、`cap/envs/simulators/libero.py`）：`env.seed(seed)` → 单次 `env.reset()` → 按 `(seed-1)%N`（seed 为空取 0）单次 `set_init_state` 并使用其返回的观测 → 不再 reset → 原有 10 步 settling；保持 streaming reset 回调作用域与 nonprivileged API。这是所有新比较共有的环境修复，不是 SAM2/streaming 方法因素；旧冻结运行树和旧输出不改，与修复前结果不作严格同条件比较。源码 SHA256 由外层在冻结时生成。

**共同环境版本 `libero-pro-common-env-v3-statepack-2026-10-10`（2026-10-10 用户授权，取代上段 v2 的 seed 映射）。** 五个任务的原始 init 文件各只有 50 个状态，v2 的 `(seed-1)%50` 会让开发 seeds 51–65 复用测试 seeds 1–15 的初始场景。v3 固定两个分区，不取模、不跨分区复用：测试 seeds 1–50 → 原始 init 文件 slot 0–49（文件字节/SHA256 不变，不重写不合并）；开发 seeds 51–65 → 每任务 15 个新增独立开发状态（每 seed 一个文件，slot 0–14）。开发状态由外层用 `scripts/libero/prepare_common_initial_states.py` 一次性生成：按 suite 原注册的任务定义/init 文件（与外层资产 preflight 核对 SHA256），使用与 `FrankaLiberoEnv` 相同的环境参数，声明的采样 seed，单次 `env.reset()` 后立即 `get_sim_state()`（采样时 0 步 settling；试验时测试/开发状态同样走单次 reset + `set_init_state` + 10 步 settling）。生成不执行任何策略/world、不步进动作、不查询 oracle/成功判据、不产生测试轨迹、不生成物体清单；重复状态写入 manifest 并失败，不换 seed 重采样；resume 只接受完全相同身份，已记录状态不重生成不覆盖。状态包放在源码之外的外层 runtime-assets 目录，作为预先声明的共同环境输入共享给本线所有方法和 cell，不属于任何 cell 学到的代码或反馈。正式 case 必须提供 `initial_state_manifest` 与其 SHA256；协议把它们仅传给试验进程（`ASPIRE_LIBERO_INITIAL_STATE_MANIFEST`/`_SHA256`，`ASPIRE_LIBERO_INITIAL_STATE_REQUIRED=1`），每次 reset 都核验 manifest、任务/suite/seed 精确映射和所选文件哈希，正式运行不回退取模。只有 `cpu-test-fake` CPU fixture 可缺省，并在身份中标明。求解器不得读取状态包、manifest 或生成脚本；生成代码不得调用 `get_sim_state`/`set_init_state`/`init_states`、加载 npy/npz/pt/pkl 或引用 bddl/xml/urdf/init 文件；状态内容不进入 policy/world/solver 命名空间，data-only World 仍只接收公开帧与本体感知。SAM2_streaming 身份对 `sam2/` 树中的 py/yaml、cu/cpp/头文件及外层编译的 `_C*.so` 取哈希，正式运行必须有编译扩展；方法依赖 `hydra-core==1.3.2`、`iopath==0.1.10` 列于 `env_configs/libero/sam2_streaming_requirements.txt`，不改基础锁文件。

外层协调器执行前核对并记录**实际加载**的 suite 注册项、任务索引/标识、任务定义文件、初始状态文件、seed 到初始状态的映射、模拟器与 API 实现。记录仓库和子模块 commit、未提交源码 SHA256、MuJoCo/robosuite/LIBERO-Pro 等依赖版本、配置及有效覆盖参数和相关文件 SHA256。确保比较中相同 seed 对应相同任务与初始场景；记录随机数、reset 实现和设备设置。

这些核验只由外层完成，不向求解器或生成程序提供资产内容、隐藏状态或成功判据。执行程序的复现检查按 ledger 计费；纯公开场景快照不执行策略，可不计费。

## 2. 输入与观测条件

固定当前 **C 条件**：不提供高层策略 MD 库、预先整理的物体清单或历史任务解法；保留公共 API 文档、当前任务语言和当前场景公开观测。可以从当前观测自行识别物体和编写任务程序，不得导入其他 cell 或历史解法的物体表、生成代码、反馈或新学到的技能。

策略和 world 使用 nonprivileged API 与实际感知服务，不得读取模拟器真值、隐藏物体状态、内部资产推断出的状态或环境成功判据，不得静默回退到 privileged API。环境 oracle 仅用于外层开发反馈和最终评分；**开发 gate 固定为 oracle**。World 自评、预测及关系核验可以辅助策略，不能替代开发 gate 或评分。测试逐 seed 反馈不得返回求解器。

## 3. 模型与执行方式

共同求解器固定为本地部署 **Qwen3.8-Flash-Next**，**native Claude Code 2.1.220**，`effort=xhigh`，context 配置 **1,000,000**，max output 配置 **64,000**。保留原生工具和上下文执行方式，不隐式改为自写 Python LLM agent、其他模型或 CC 版本。

记录权重仓库/revision/哈希、量化、served model name、推理引擎版本、完整服务参数、RoPE/context 配置、工具/reasoning parser、图像限制、CC 二进制版本及哈希。记录实际请求的 model、effort/reasoning、输出上限和上下文设置，区分配置上限与实际请求/输出。仅在启动文件写入目标值不足以证明其生效。

修改模型、感知服务或 agent 执行方式须明确标注为独立实验因素；其他共同条件变化也须声明，不能混为严格同条件比较。工程实现 CC 的模型与路由单独记录，不作为实验求解器。

## 4. 开发与测试划分

开发 seeds：**51–65**；测试 seeds：**1–50**。开发过程中不得查看或使用测试轨迹、反馈及结果，包括历史测试结果；外层隔离输出和求解器可读材料，不只依赖提示词声明。

原生 Fix Loop：观察初始公开场景 → 从头生成程序 → 在开发 seeds 执行、诊断和修复 → 仅依据开发证据选一个可泛化程序 → 冻结 → 外层完整测试 1–50。

每个开发 seed **最多 3 次计费尝试，总计最多 45 次/cell**，沿用现有 ledger 规则。修改、重放、重启或诊断不重置预算；执行程序的 smoke、诊断和失败尝试也统一记账，不额外叠加免费任务尝试。纯初始公开观测快照不执行策略，可不计费。超时、基础设施错误和中断保留账本及已消耗预算。

不设全局 15 版本上限，不要求每 seed 生成不同程序，也不要求用满尝试。不新增任意 motor-call、恢复或 world-query 上限；保留环境 max_steps、trial 超时和进程 watchdog。

## 5. 预算与独立重复

独立实验单位为 **方法 × 任务 × 独立开发重复**。每方法 × 五任务 × 两次独立开发重复 = **10 个 cell**；两方法比较 = **20 个 cell**。建议 ID 为 `<task>_<method>_r<1|2>`。

每个 cell 从头生成，不共享其他 cell 的生成 policy/world、反馈、修订、选择依据或新学到的技能。方法的通用工程运行时可作为预先声明和冻结的方法输入，须记录来源/哈希，不得夹带任务解法、预整理物体表或其他 cell 新学到的技能。任务内生成内容只在本 cell 内迭代。

| 限制 | 固定值 |
| --- | --- |
| 单 trial 超时 | 900 秒 |
| 每 cell 开发阶段 watchdog | 14 小时（50,400 秒） |
| 每 cell 测试阶段 watchdog | 5 小时（18,000 秒） |

记录阶段首次开始时间、deadline 和已消耗预算。恢复沿用同一 cell 身份、账本和原阶段 deadline，不获得新窗口。超时或基础设施错误单独报告；不得清空记录后作为新实验重跑，不覆盖旧输出。

## 6. 冻结与评分

仅按开发证据选择程序并记录理由和尝试引用。冻结 policy、该方法使用的任务 world、通用 world 运行时、配置与运行依赖，记录 SHA256；依赖包括解释器/锁文件、实际环境配置、感知服务实现和权重、SAM2 等后端。没有 world 的方法记为不适用。工程 world 与模型生成的任务 world 分别标明作者和来源。

同一个冻结 bundle 测试全部 50 个 seed，每 seed 执行前后核验身份；测试期间不得修复、改配置或逐 seed 选程序。

沿用 [native_world_heldout.py](../scripts/libero/native_world_heldout.py) 的评分语义，分别报告 `success`、`failure`、`crash`、`infrastructure_error`。正常执行结束且外层 oracle 成功才计 success；生成程序报错计 crash，即使环境曾完成任务也不能改判成功。启动、运行器、超时、缺失结果或冻结身份错误按现有评分路径记录基础设施问题和具体原因。

**成功率 = success 数 / 50**，完整 cell 四类计数之和为 50。未执行 seed 单列 `not_run`，不伪造为已执行结果。少于 50 个 seed 的 cell 标记 `incomplete`，报告已执行数、缺失 seed 和原因；可显示当前 success/50 计数，但不得称为完整测试成功率、不改用已执行数作分母，也不静默排除不完整 cell。

## 7. 产物与汇报

使用独立的新实验目录，日志、原始结果、媒体、权重和冻结运行树放在源码同步范围外。每 cell 保留：

- 配置和版本清单、有效任务/seed/模型/服务参数、实际请求脱敏记录及文件哈希。
- 开发尝试 ledger，逐次 seed/序号/bundle/时间/耗时/退出信息/oracle 反馈和阶段 deadline。
- 程序修订、开发证据、选择依据、冻结文件和依赖哈希。
- 逐 seed 测试结果、失败原因、耗时、四类状态、冻结身份、未完成 seed 及完整性标记。
- 方法需要的公开感知、world 更新/失效/预测、控制 checkpoint 和策略使用证据；不含隐藏状态或凭据。

按“任务 × 方法 × 独立重复”逐 cell 报告成功数/50、四类计数、已执行数、完整性、开发预算消耗和阶段耗时。开发/测试统计分开；两次独立开发重复保留各自结果，不把 100 个测试 seed 当作 100 次独立开发重复。

方法运行前声明相对共同协议的变化：world 形式和作者、更新节奏、感知链路、预测/自评/控制机制，以及模型/感知/agent 差异。设计差异作为方法因素；共同条件变化的 cell 标记偏离并单独报告。第二种方法未指定时，不自行补成历史 A/B 或其他 baseline。

## 当前多节奏 Streaming World 方法声明

当前候选方法使用通用工程 `World.py`、SAM2_streaming 时序掩码、公开 RGB-D 几何状态、多节奏机器人/物体/关系更新和策略 checkpoint 响应。通用 world 运行时由工程实现；Qwen 在本 cell 从头生成策略、观测驱动的物体 prompt 和任务谓词。不得把工程 world 宣称为 Qwen 从头学到的任务 world。

SAM2 是新增的方法感知因素，固定其源码、权重、初始化/重获/失效规则。当前节奏按模拟时间，协作式推理占用实际执行时间，不承诺墙钟实时频率。SAM3、GraspNet、IK 等共享服务须声明实际版本；缓存或镜像权重记录实际来源与哈希，未经核实不宣称与官方权重逐字节一致。

部署、合成帧验证和仅移动回家的示例不算任务成功或正式 cell。正式程序执行必须记账，不使用无记账示例预跑任务获取额外开发证据。RS 先验收基础服务和协议接口，再从 bowl 的新独立 cell 开始；后续 DLC 仍按本协议与独立 cell 调度。
