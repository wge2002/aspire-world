# CC_TASK_15 跨任务技能库：准备与设计（2026-10-23）

只读设计。没有改 pipeline 代码，没有 stage、提交或启动作业，没有用 GPU。
候选清单在 `candidates.md`，抽取脚本在 `scripts/`，中间数据在 `data/`。

## 0. 结论与排期

1. **候选够用，但覆盖有限。** 33 个有开发通过的 cell 中盘点出 24 张候选卡，另有 1 张跨簇卡。
   - 强候选 14 个，都是 ≥3 个 cell 独立重写、有 12+/15 或 15/15 的程序。其中分段移动已被 m4-r1 取代，可用的是 13 个。
   - 可合并成约 12 个 L0 条目。
   - 对冻结程序的 58 次开发期失败：直接覆盖 10 次，部分覆盖 17 次（放置/释放），有条件覆盖 12 次（炉子，要先新做 A6 红度判定），
     覆盖不了 19 次（关抽屉 16、掉落 2、未知 1）。
   - mug 关门推出杯子、twomoka 第二个壶需要 M4 原语，不是库能解决的。
2. **最缺的是验证类技能，不是动作类。** 动作类（SAM3 门控、四元数、rim-pinch、偏移补偿释放、抽屉拉开、旋钮）都有多份 15/15 样本。
   验证类几乎是空白：
   - 没有一个通过的程序判断炉子是否点着；
   - 没有一个判断物体是否倾斜；
   - 高分的容器类程序也不确认"已经在里面"。
   这正是 M3 契约要求、而 worker 一直没写出来的部分。库每个技能必须内置子目标验证（`SkillResult`，判不了返回 `None`）。
3. **晋升要比上游多一道跨任务检验。** 上游 verifier 在同一任务的 51–55 上重解，只证明"能传授"，它自己也这么写。
   - 我们加上 V2：在另一个 trigger 适用的任务上用库重解，同样 51–55、≤2 版本。
   - 库在一轮内冻结，轮间更新。
   - 新技能先只对一半 cell 可见。
   - 以 `wrong`、多 cell 的 `misleading`、用与不用的 unseen 差作为降级信号。
4. **接入改动中等，约 1.5 天 CC 工作量加 1 天写 L0，必须排在 CC_TASK_14 落定之后。**
   - 库放在 `cap/world_model/library/`，用 vendoring 加标记行，bundle sha 不变。
   - read guard 与 native_cc_guard 都不用改。
   - 需要改的：`method_switches` 加 `skill_library`；新增 LIBRARY_CONTRACT；`verify_worker` 开例外；冻结时的用量扫描；verifier 角色；p6e stager。
5. **对照实验**：B0 = p6d 方法，B1 = B0 + L1（leave_task_out 视图），6 个未达标任务 × 2 臂 × K=2 = 24 个 cell。
   - 主比较是首次通过 51 的 trial 数，这组实验真正有功效的就是这一项。
   - 交付分的比较只能看出 ≥0.25 量级的效应。
   - 判定规则见 5.2，已经写定。

**排期（建议；截止 11-16，按一轮 1–2 天）**

| 日期 | 事项 | 算力 | 依赖 |
|---|---|---|---|
| 10-23 – 10-25 | p6d smoke 与 4-cell 试跑（CC_TASK_14，rbs-debug2）。并行：写 L0（库仓库，离线），A6 在开发关键帧上标定 | rbs-debug2 8 卡 | 协调端启动 p6d；L0 目录需批准创建 |
| 10-25 – 10-26 | CC_TASK_14 落定后实现 p6e（第 4.4 节）并补测试；L0 过 L 检查 | 无 GPU | p6d 试跑没有方法级问题 |
| 10-26 | L0 的 V1/V2：约 6 个 verifier cell，得到 L1 | 1 个节点，半天 | p6e smoke 通过 |
| 10-27 – 10-29 | **R1 对照**：24 个 cell（B0/B1 × 6 任务 × K=2） | rbs-debug2 + top 池 2 个 8 卡节点，约 2 天 | 用户批准 DLC |
| 10-29 – 10-30 | 分析 E0–E6，curator 汇总并跑 verifier，得到 L2 | 1 个节点，半天 | — |
| 10-31 – 11-02 | **R2 交付轮**：未达标任务 K=3（按 R1 结论决定开不开库，开库用 `view=all`）；另加 mug、twomoka 的 M4 臂（如果已经立项） | 2–3 个节点 | R1 决策 |
| 11-03 – 11-05 | **R3**：仍未达标的任务继续，库按轮间规则更新 | 2–3 个节点 | — |
| 11-06 – 11-08 | **R4 / rerun sweep**：用最终库重跑 held-out <0.95 且库已变的任务 | 2–3 个节点 | — |
| 11-09 – 11-16 | 缓冲（任一轮延误 1–2 天）、汇总、写作 | — | — |

## 1. 候选技能盘点（摘要；完整清单见 candidates.md）

- **范围与方法**：
  - p3、p4（含 rerun）、p4n、p5、r3、p6b、ns、l10 共 57 个 cell，都在同一 API 下跑过；43 个有可恢复的程序，33 个程序通过过 ≥1 个开发种子。
  - 程序字节取自开发 trial（sha 与 bundle 一致）。证据只用开发期材料，held-out 只作排序。
  - 33 份程序的禁用 API 都是 0 命中。
- **按簇**：

| 簇 | 卡片 | 强 | 中 | 弱或缺口 |
|---|---|---|---|---|
| 感知 | P1–P6 | P2 门控（32 个 cell）、P3 抗劫持（19）、P5 三维定位（≥24） | P4 身份关联、P6 遮挡 | P1 任务语言（真正解析的只有 3 个） |
| 运动与抓取 | M1–M6 | M3 四元数（14）、M4 rim-pinch（13）、M6 抓取验证与重试（12/10/8）；M1 glide（9，已被 m4-r1 取代） | M5 plan_grasp 门控 | M2 IK 守卫 |
| 放置与释放 | R1–R5 | R1 偏移补偿（12 个 cell，8 个任务）、R2 下降高度（10）、R3 容器投放（13） | R4 释放后确认、R5 停手（并入 X1） | 姿态判定是空白 |
| 铰接 | A1–A7 | A1 把手（12）、A2 前向夹持拉开（7）、A3 开度验证（9）、A5 旋钮（6） | — | A4 关抽屉（2 个 cell，最好 6/15）、A6 灶圈红度（0）、A7 微波炉门（0） |

- **重复发明的程度**：P2 在 32/33 个 cell 里各写了一遍，M3 有 14 个，M4、R3 各 13 个，R1、A1 各 12 个。
  这和诊断 M5"多数 cell 重新发明同样的 helper"一致，也是库能缩短阶段 A 的主要依据。
- **耦合**：几乎所有阈值都是场景常量，而且在不同 cell 之间差一个数量级。例如：
  - SAM3 分数下限 0.02–0.55；
  - 碗沿偏移 0.045 到 0.88·r；
  - 抽屉开度 0.05–0.135；
  - TCP 偏置 0.100–0.115。
  入库时一律作为参数或 precondition，不带默认常量。有 6 处互相矛盾的经验，写 L0 前要裁决（candidates.md "需要裁决的矛盾"）。
- **嫌疑来源**：以下只作旁证，不作证据：
  - objtaskmilk_p6b（噪声最大值）；
  - spatialtaskcookie_p6b（过拟合）；
  - goaltask_p6b 的 `GOODPAIR`（未设种子的随机重采样，禁止入库）；
  - spatialdrawer_p4n 的常数悬挂补偿；
  - drawer_p5_r2（9 个版本取最大）。
- **world 依赖**：
  - P4、A1 的序数身份、A3 的开度事实，多数经 `world` 读取，入库时改为直接读 `get_observation()`，或者由调用方传入。
  - 有 13 个 cell 的身份关联规则只出现在 world 程序里，policy 字节里没有，P4 因此只做 policy 侧版本。
- **用到但不在 brief 列表里的全局名**：`rotation_matrix_to_quaternion`、`interpolate_segment`、`goto_pose(z_approach=)`、`segment_sam3_point_prompt`。
  已经查过当前 worktree：
  - `rotation_matrix_to_quaternion`、`segment_sam3_point_prompt` 在 `api-reference.md` 里；
  - `interpolate_segment` 只在 `libero_reduced_skill_library.py` 里出现，`api-reference.md` 没有；
  - `goto_pose(z_approach=)` 只在 `libero_reduced.py` 的实现里，没有写进文档。
  后两个入库前要确认是有意公开的接口，否则库里不用。

## 2. 库的格式

### 2.1 放在哪里
- 冻结进每个 cell 的位置是 `cap/world_model/library/`。不放 `.claude/`，也不放 `docs/`，原因有三：
  - CC_TASK_14 往 `.claude/.../skills/` 写文件时被 auto-mode 分类器拦过。用户 10-22 18:40 已定技能文件放非 `.claude` 路径，
    p6d 的两个技能文件就在 `cap/world_model/skills/`。
  - `p3.check_runtime` 不允许 runtime 的 `docs/` 下出现未声明文件（`code-world-search-control-p3-20261013/prepare-search-study.py:568-572`）。
  - C 条件在 stage 时会删掉 `.claude/libero/skills/*.md`（同文件 :232-238、`check_strategy_md` :499-509）。
- 放在 `cap/` 下的好处：
  - 自动进 runtime manifest 的 `files`（`scripts/common/native_cc_freeze.py:13-14,39-57`），每次调用都由 `verify_runtime` 核对。
  - worker 的 Bash 不能改 `cap/`（`scripts/common/native_cc_guard.py:57-59`）。
  - 它在 `case["sim"]` 之内，cell read guard 原样放行（`.../support/cell_read_guard.py:40-42`）。
- 库的"仓库"在所有 cell 之外：`/mnt/home/gewang/experiments/code-world-skill-library/`。
  - 其中有 `L<k>/`（按版本冻结的完整目录）、`registry.json`（每个版本的目录 digest、条目、来源）、
    `proposals/`（各 cell 提交的候选）和 `verification/`（verifier 工作区）。
  - stager 只从某个 `L<k>/` 复制，不从工程 worktree 复制。

### 2.2 目录与单个技能
```
cap/world_model/library/
  LIBRARY.json            # 版本、digest、条目索引（id、version、status、触发、依赖、评级计数）
  README.md               # 怎么查、怎么用、怎么评级（≤80 行，worker 先读它）
  CHANGELOG.md            # 条目的历史与被替换的说法（条目正文只写"现在为真"的内容）
  skills/<id>.py          # 代码：纯函数 + 内置验证，只用公开 API
  skills/<id>.md          # 卡片：触发、接口、前置条件、验证、证据、局限
```
- 每个技能一份代码 + 一张卡片，id 形如 `perceive.sam3_candidates`、`grasp.rim_pinch`、`place.offset_release`。
- 代码约束：
  - 只用 `api-reference.md` 中的公开函数，加 numpy/scipy。
  - 不 `import world`。需要 world 状态时由调用方以参数传入，避免库与某一版 world 协议耦合。
  - 不读文件，不出现任务物体名或场景坐标常量，所有阈值都是带默认值的参数。
- 每个对外函数必须返回 `SkillResult`（dict）：
  ```python
  {"ok": bool | None,        # None = 观测不足、无法判定（不算成功）
   "subgoal": "held:bowl",    # 本次声称达成的子目标
   "evidence": {...},         # 判定所用的观测量（位移、宽度、颜色比例、深度差……）
   "used": {...},             # 实际执行的目标/姿态（与 m4-r1 的 solve_ik 打印对齐）
   "ticks": int}              # 本次消耗的仿真步数（上游 ASPIRE "Quote the tick cost" 规则）
  ```
- 子目标验证内置：
  - 动作类技能执行后必须重新观测，用卡片写明的信号判 `ok`。
  - 判不了时返回 `None`，不返回 True。这与 CC_TASK_14 的 `subgoal-verification.md`"Unknown is not confirmed"一致。
  - 感知类技能的验证是"选择器两端有界、空集返回 None"（上游 lint 的 seed-a-search-with-None 规则）。
- 前置条件写成代码：卡片里的 `precondition(obs, **params) -> (bool, reason)`，库函数开头先调用，不满足就返回 `ok=None`。
  上游的说法是 "A precondition is code or it is nothing"。

### 2.3 卡片字段（`skills/<id>.md` 头部 YAML + 正文）
| 字段 | 内容 |
|---|---|
| `id` / `version` | 语义版本：接口变动升主版本，阈值或修正升次版本 |
| `status` | `candidate` / `provisional` / `verified` / `quarantined` / `retired`（第 3 节） |
| `trigger` | 症状或场景条件，例如"开口直径 > 夹爪行程的碗/杯"，不写任务名 |
| `interface` | 签名、参数含义与默认值、返回的 `SkillResult` 字段 |
| `precondition` | 指向代码中的检查函数；文字只作说明 |
| `verification` | 用什么观测判 ok，阈值怎么来，什么情况下是 None |
| `evidence` | 每条来源：round/cell、bundle sha、开发种子通过数 x/15、翻转的开发种子数；verifier 结果；后续 `useful` 评级来自哪些 cell |
| `limits` | 已知反例与不适用范围，一句一条 |
| `api` | 依赖的公开 API；是否依赖 m4-r1 |
| `grades` | 累计 useful / misleading / wrong / not-applicable 计数（由 curator 汇总，不由 worker 写） |

- 证据只能来自开发种子 51–65 的账本、开发 trial 日志、findings.md，以及 verifier 在 51–55 上的结果。
  unseen 66–80 只以 cell 级汇总分数出现，用于选择和降级规则（第 3 节）。不写 held-out 的任何东西。
- 上游 ASPIRE 规则照搬三条：
  - 条目正文只写现在为真的内容，历史进 CHANGELOG。
  - 单条约 400 词以内。
  - 发起者在自己程序上做的消融只算 provenance，不算 evidence。

### 2.4 worker 如何查找与调用
- 查找：prompt 里加一段 LIBRARY_CONTRACT（第 4 节）。worker 先读 `library/README.md` 与 `LIBRARY.json`，
  按 `trigger` 选卡片读，并在 `task_analysis.md` 里写下预计用哪些技能、为什么。
- 调用方式比较：

| 方式 | 好处 | 坏处 |
|---|---|---|
| A 只读 import（`from aspire.sim.cap.world_model.library.skills import grasp_rim`） | 用量可精确统计；worker 不能悄悄改；PYTHONPATH 已指向 cell 根（`native_world_protocol.py:298`） | 被 import 的代码不进 bundle sha（`collect_bundle` 只哈希 policy/world 源，:352-408），bundle 不再自足，只能靠 runtime manifest；worker 无法按场景改参数以外的逻辑；与上游"把片段抄进程序"不同 |
| B 复制进 policy（vendoring）+ 标记行 | bundle 自足，held-out/回放只依赖 policy 字节；与上游一致；允许改 | 需要静态扫描才能知道用了、改了没有 |

- 建议 B，加两条约束：
  - 复制时保留标记行 `# lib:<id>@<version>`。
  - 冻结时由协议做静态扫描：对标记块做规范化 AST 哈希，与库中同版本函数比较，记 `verbatim` / `modified` / `absent`。
- 这样 bundle 仍只由 policy/world 字节决定，与 M2 的重扫、unseen、held-out 机制都不冲突。

### 2.5 使用与评级记录
- worker 在 `attempts/skill_report.json` 写评级，格式沿用上游：`consulted[]` 每条给 verdict 与开发种子证据，
  外加 `proposed_new[]` / `proposed_edits[]`。
  - 放 `attempts/` 下，是因为现有 guard 本来就允许写这里（`native_cc_guard.py:31-50`），不需要改 guard。
- 协议在 `check` 时校验其格式（未知 verdict、misleading/wrong 缺具体种子、proposal 缺代码），不合格只给警告，不阻断 cell。
  上游也是"技能验证与任务自己的 Stage 2 解耦"。
- 冻结时写进 `stage1_result.json` / campaign_state 的 `library_usage`：库版本与 digest、每个技能的扫描结果、worker 评级。
- curator 在两轮之间汇总各 cell 的 `library_usage`，得到每个技能的使用率、verbatim 率、评级计数，
  以及"用了该技能的 cell"与"没用的 cell"的开发分和 unseen 分。

## 3. 晋升与淘汰

### 3.1 上游 ASPIRE 的做法（已在本机副本 `code/ASPIRE-upstream-v1.0-fa00f0e` 核实）
- 状态机：`none → proposed → verified → in library`，或 `→ refuted → not promoted`（`main-agent-prompt.md:53-64`）。
  技能验证只决定能否晋升，不阻塞任务自己的 held-out（:62-64）。
- verifier（`verifier-prompt.md:88-97,181-185`）：
  - 在**同一任务**上重解，只用 51–55 五个种子，最多 2 个版本，≥4/5 判 `verified`。
  - 不许读提案者的程序和目录（:60-84），否则判 `void`。
  - 协调端交给 verifier 的提案保留通过数，删掉种子编号（`main-agent-prompt.md:440-450`）。
- verifier 自己写明它证明的是"写下来能传授"，不是"能泛化"（`verifier-prompt.md:7-11`）。
  **所以上游没有跨任务检验这一关，这是我们要补的。**
- 晋升后库 hash 立即改变，之后派发的任务都用新库（`main-agent-prompt.md:301-302`），即轮内同步更新。
  所有任务完成后做一次 rerun sweep：held-out <0.8 且库已变的任务用最终库重跑，写到新目录（:529-551；`gen_progress.py:48`）。
- 另外两条上游规则我们照搬：
  - 6e（:407）：只有一致证据的修正不晋升，要问"哪个测量能推翻它"。
  - 6f（:396）：提案者在自己程序上做的消融只算 provenance。

### 3.2 我们的状态与门槛
| 状态 | 进入条件 | worker 可见 |
|---|---|---|
| `candidate` | curator 从开发期证据抽出（本次盘点，或 cell 的 `proposed_new`），过离线检查 L | 否 |
| `provisional` | 通过 V1（传授） | 是，卡片标 provisional |
| `verified` | 通过 V2（跨任务），或在 ≥2 个**不同任务**的 cell 里被评 `useful` 且没有 `wrong` | 是 |
| `quarantined` | 触发第 3.4 节任一降级条件 | 否（文件留在仓库，索引里去掉） |
| `retired` | 被新版本或 m4-r1 原语取代，或连续 2 轮无人使用 | 否 |

**L 离线检查**（零 GPU，lint 脚本，每次改库都跑）：
- 不出现 `check_policy` 的禁用模式（`native_world_protocol.py:326-341`），不读文件。
- 不出现任务物体名或场景坐标常量：阈值都是参数，三维字面量只能出现在默认参数里。
- 对外函数返回 `SkillResult`，开头调用 precondition，搜索类代码有空集分支（上游 `lint_skills.py` 的三条规则）。
- 感知函数用合成数组做单测（空 mask、全图 mask、两个连通块），保证返回 `None` 而不是抛异常或误报。

**V1 传授检验**（照搬上游）：
- 一个 verifier cell 跑提案来源的任务，看得到库 `L_k` 和提案卡片，看不到来源 cell。
- 只跑 51–55，最多 2 个版本，≥4/5 通过。
- 成本：≤10 次计分 trial 加探索，约 1–2 h 占一个 slot。
- verifier 要在 `skill_report.json` 里给提案逐条评级。上游指出 verdict 与逐条评级常常不一致（`verifier-prompt.md:187-193`），
  晋升看逐条评级 `useful`。

**V2 跨任务检验**（我们新增，针对"同任务重解不证明泛化"和我们自己的噪声结论）：
- 选一个**不是提案来源**、且卡片 `trigger` 适用的任务，跑一个 verifier cell，设定与 V1 相同（51–55、≤2 版本）。
- 判定看两条：
  - ≥4/5 通过；
  - 该技能被 verbatim 或 modified 使用，且评级为 `useful`。
- 没有合适的第二个任务时，技能停在 `provisional`。下一轮里如果有 ≥2 个不同任务的 cell 评 `useful`、没有 `wrong`，
  就自然升为 `verified`（等价于用实验臂本身做 V2）。

**对噪声的处理**（诊断第 1、5 节）：
- 来源 cell 的证据必须用 M2 重扫后的合并通过数，不用账本里取最大值的单次分数。
- 来源 cell 若"冻结开发分 − unseen 通过数 ≥ 4/15"（上游 step 3 所说的过拟合程序），它的提案必须过 V2，不能只凭 V1 进入 provisional。
  spatialtaskcookie ns 就属于这一类（12/15 对 7/15）。
- 来源程序调用 `plan_grasp`、且又不在 m4-r1 下时，它的成败可能是 GraspNet 随机性（objtaskmilk 9,1,8,2,8,13…）。
  这类证据只记 provenance，不算 evidence。
- 5 个种子 ≥4/5 本身的噪声也大：真实成功率 0.6 的程序单次有约 34% 的概率 ≥4/5（两个版本时更高）。V1 只是低成本筛子，真正的门槛是 V2 加实际使用评级。

### 3.3 谁执行、花多少
- **curator**：每轮之间由协调端派一个独立 CC 会话，只读各 cell 的 `library_usage` 与 `skill_report.json`。工作包括：
  - 合并近似重复；
  - 改写成参数化代码；
  - 跑 L 检查；
  - 生成 verifier 包，按上游规则删种子编号、删指向来源目录的引用。
- curator 不读 held-out 的任何东西；unseen 只读 cell 级汇总分数。
- **verifier**：pipeline 里的一种 cell 角色（第 4 节）：`role=verifier`，dev 种子只用 51–55，不跑 unseen，不跑 held-out。
- **预算**：每轮之间约 4–8 个 verifier cell（V1 与 V2 合计），一个 8 卡节点并行 4 个，约半天。
  L0 建库那一次的规模类似（第 5 节）。

### 3.4 防扩散与降级
- **轮内冻结**：一轮内所有 cell 用同一个 `L_k`，digest 记进 case 与 manifest。上游是轮内同步更新，我们不这样做，原因有两个：
  - M1 要在同任务的 K 个 cell 之间用 unseen 选择，K 个 cell 看到不同的库就不可比；
  - 有库与无库的对照要求一轮内的处理固定。
- **新技能限量**：刚升为 `provisional` 的技能，在下一轮只对 trigger 匹配的任务的半数 cell 可见（K=3 时 1–2 个），
  另一半作为同轮对照。连续一轮没有出问题才全量可见。
- **降级为 quarantined**，满足任一条即可：
  - 任一 cell 评 `wrong`，且 curator 复核属实（API 错误、常量错、跑不起来）。上游 step 5 的要求是先修再派下一批。
  - 来自 ≥2 个不同 cell、带具体种子的 `misleading`。
  - 同一轮内用了该技能的 cell 与没用的 cell 相比，unseen 均值低 ≥0.15，且两边各 ≥3 个 cell。
    这只是触发人工复核的信号，不当作因果结论。
  - 有库重跑比原结果更差（上游 rerun 规则："a rerun that scores worse is a real signal"）。
- **退役**：
  - m4-r1 已提供同一功能（例如 glide 对应 `goto_pose(max_step=…)`），只保留验证部分并入其他技能；
  - 或新版本通过 V1/V2 替代旧版本；
  - 或连续 2 轮没有任何 cell 查阅或使用。
- 被降级或退役的条目写进 `CHANGELOG.md`，正文只保留现在为真的内容（上游规则）。

## 4. 接入 pipeline（只写设计）

所有事实来自 `data/integration_facts.md`（只读调查），文中行号以那份为准。CC_TASK_14 正在改同一批文件，实现前要重新核对。

### 4.1 cell 内：库是受控的例外
现在的隔离由四层组成：
- 原样冻结的 runtime，加 manifest 的逐文件 sha；
- cell read guard（只放行本 cell 的 sim 与 outputs，`cell_read_guard.py:25-50`）；
- native_cc_guard（Bash 不能改 `cap/`，Write 只能写 attempts/ 等，`native_cc_guard.py:31-59`）；
- prompt 里的"never read another cell's outputs"（`native_world_campaign.py:709`）。

库不是"其他 cell 的输出"，而是 curator 在两轮之间、从开发期证据整理出来，并冻结了版本的代码与文档。
它走和 `cap/world_model/skills/*.md` 同一条通道进入 runtime：

1. **冻结**：stager 新增 `LIBRARY_OVERLAY`。
   - 来源是库仓库的 `L<k>/` 目录，不是工程 worktree，复制到 `cap/world_model/library/`。
   - 复制后校验目录 digest 等于 `registry.json` 里 `L<k>` 的 digest。
   - 注意 p6d verify 要求每个 `C_OVERLAY` 文件与 worktree 逐字节相等（`prepare-p6d-study.py:341-343`），所以库不能放进 `C_OVERLAY`，要单独一张表单独核对。
   - `diff_vs_reference` 里给这些文件登记 `RUNTIME_DIFF` 原因 `skill_library L<k>`（:269-324）。
2. **guard**：
   - read guard 不用改：库在 `case["sim"]` 之内，原样放行。
   - native_cc_guard 不用改：worker 不能改 `cap/`，库对 worker 只读，正合需要。
   - `skill_report.json` 写在 `attempts/` 下，本来就允许写。
   - `p3.check_runtime` 只管 `docs/`（:568-572），库不在 `docs/` 下，不受影响。
   - 需要改的只有一处：`two_task_scope.verify_worker` 拒绝 C 的 render 提到共享库（`two_task_scope.py:158-163`）。
     这个文件是冻结的 support，要在新一轮 support 覆盖里加例外：开关打开时，要求 render 指向 `cap/world_model/library/README.md`；
     开关关闭时照旧拒绝。
3. **开关**：`method_switches` 新增 `skill_library`：
   ```json
   "skill_library": {"version": "L1", "digest": "<sha256>", "view": "all | leave_task_out | none",
                     "exclude_sources": ["<task id>", ...], "mode": "vendor"}
   ```
   - `errors()` 校验：version 在 registry 里存在，digest 一致；`view=leave_task_out` 时 `exclude_sources` 非空；`mode` 只允许 `vendor`。
   - 不设这个 key 时，渲染、身份、计分逐字节与 p6d 相同，沿用 `method_switches` 的约定（:65-68）。
   - `view=leave_task_out` 的意思是：stager 冻结时，从 `LIBRARY.json` 的索引和 `skills/` 里去掉所有证据来自本任务的条目
     （第 5 节主比较要用）。这样冻结出来的是一个确定的子集，digest 另记。
4. **prompt**：
   - `executable_world_profile.method_contract` 加 `LIBRARY_CONTRACT`，只在开关打开时渲染，位置与 `SUBGOAL_CONTRACT` 并列（:858-875）。内容：
     - 先读 README 与 LIBRARY.json；
     - 按 trigger 读卡片；
     - 复制函数时保留 `# lib:<id>@<version>` 标记；
     - 评级写进 `attempts/skill_report.json`；
     - 库之外的跨任务内容仍然禁止。
   - C 的 coordinator prompt 现在写着"no high-level strategy library … do not create one"（`native_world_campaign.py:723-725`）。
     开关打开时改为"cell 内不推广、不改库；库的更新只发生在轮次之间"。
5. **manifest 与身份**：
   - `manifest_entry` 增加 `skill_library: {version, digest, view, entries: [{id, version, status, sha256}]}`。
   - 库文件本身在 `cap/` 下，已经自动进 manifest 的 `files`（`native_cc_freeze.py:13-14`）。
   - 协议身份已经包含 `method_switches`（`native_world_protocol.py:222`），库 digest 因此进入账本身份；换库就换身份，不会和旧账本混用。
6. **用量扫描**：
   - 冻结时（写 `stage1_result.json` 前）新模块 `cap/world_model/library_usage.py` 解析 policy 中的 `# lib:` 标记块。
   - 每块与库里同版本函数做规范化 AST 比较，得出 `verbatim / modified / absent`；另外做函数名或调用名匹配，抓没加标记的复制。
   - 结果和 `skill_report.json` 一起写进 `library_usage`。
   - bundle sha 不变（仍只由 policy/world 源决定，`collect_bundle` :352-408），所以 M2 重扫、unseen、held-out 回放都不受影响。

### 4.2 verifier 角色
- 用同一个 driver 和 protocol，case 加 `role: "verifier"`：
  - dev 种子只用 51–55；
  - 版本上限 2；
  - 不做 prefreeze resweep、unseen 和 held-out。
- worker prompt 换成 verifier 模板：上游 `verifier-prompt.md` 的规则，加上我们的 world/子目标契约。
- `proposed_skills.md` 由 curator 放进该 cell 的 `control/`（read guard 已放行 control 下的两份 prompt，需要再加这一个文件名）。
- verifier 看不到来源 cell。read guard 本来就挡住了其他 cell，这一点不用新增机制，只要 curator 不把来源路径写进提案。
- 结果写 `verification_report.json`，格式照上游（seeds、attempts_used、verdict、`proposals[]` 逐条评级）。

### 4.3 campaign 层面
- **轮次**：一轮 = 冻结 `L_k`，跑完一批 cell（开发、重扫、unseen、held-out），然后进入轮间窗口。
  - 轮间窗口里 curator 汇总、做 L 检查、派 V1/V2 verifier、晋升或降级，得到 `L_{k+1}`。
  - 轮内不更新库（理由见 3.4）。
- **与 M1 配合**：
  - 同任务的 K 个 cell 用同一个 `L_k`、同一个 view，按 unseen 选交付版本，规则不变。
  - unseen 只用于选择，也作为降级信号的 cell 级汇总，不进入卡片正文。
- **课程**：上游是逐任务动态排序。我们按轮排序，每轮在 `curriculum.md` 写明"库现在覆盖什么、本轮挑哪些任务、为什么"（照上游格式）。
  - 先跑失败分类和库覆盖重合度最高的任务（铰接、放置类：mokastove、bowldrawerclose、bowldrawer、goaltask）；
  - 原语受限的任务（mug、twomoka）留给 M4 臂，不指望库。
- **rerun sweep**：最后一轮用最终库重跑 held-out <0.95（我们的目标，上游是 0.8）且库已变的任务，写进新的 cell，不覆盖原结果。

### 4.4 需要改的文件与工作量（估计）
| 改动 | 文件 | 规模 |
|---|---|---|
| 开关、校验、manifest 项 | `scripts/libero/method_switches.py` | ~50 行 |
| LIBRARY_CONTRACT 与渲染 | `scripts/libero/executable_world_profile.py` | ~50 行 |
| C 的 coordinator 文本分支 | `scripts/libero/native_world_campaign.py` | ~15 行 |
| 用量扫描（AST 规范化比较） | 新 `cap/world_model/library_usage.py`，挂进 `native_world_protocol.py` 的冻结步骤 | ~150 + 20 行 |
| `skill_report.json` 格式检查（只警告） | `native_world_protocol.py` 的 `check` | ~40 行 |
| verifier 角色（种子、版本上限、跳过 unseen/held-out） | `native_world_protocol.py`、`method_switches.py`、新 p6e driver 覆盖 | ~120 行 |
| verify_worker 例外、read guard 放行 `proposed_skills.md` | 新一轮 support 覆盖 | ~30 行 |
| stager（`LIBRARY_OVERLAY`、digest 校验、leave-task-out 子集、diff 原因） | 新 `prepare-p6e-study.py`（import p6d） | ~200 行 |
| 库仓库工具（registry、lint、curate 汇总、verifier 打包） | 新 `scripts/library/*.py` | ~400 行 |
| 测试 | 新 6 个左右测试文件 | ~450 行 |
| L0 内容（8–12 个技能的代码与卡片） | 库仓库 `L0/` | ~1200 行 |

- 合计：pipeline 约 1.5 天 CC 工作量，L0 内容约 1 天，可以并行。另外约半天 GPU 跑 L0 的 V1/V2。
- 前置条件：CC_TASK_14 的改动先落定（提交或冻结）。现在两边改的是同一批 `scripts/libero/*.py`，并行改会冲突；
  库的代码可以先写在库仓库里，那里不在 worktree 内。

## 5. 下一轮对照实验

### 5.1 臂与任务
| 臂 | 内容 |
|---|---|
| B0 无库 | CC_TASK_14 的 p6d 方法（`method_switches` 的 P6D 值：M2/M3/M4、m4-r1、libero_latch），不设 `skill_library` |
| B1 有库 | B0 + `skill_library: {version: L1, view: leave_task_out}` |

- **任务**：现在 held-out 未到 0.95、且有过首次通过的 6 个任务：
  - bowldrawer（最好 0.82）、bowldrawerclose（0.20）、goaltaskbowldrawer（0.40）；
  - objtaskmilk（0.44）、spatialtaskcookie（0.50）、mokastove（p5 0.94，p6b 0.28）。
- mugmicrowave、mugtaskmicrowave、twomoka 不进这组对照：
  - 它们卡在原语与感知（诊断 4.2：关门把杯子推出来、第二个壶），库覆盖不了（见 candidates.md 的覆盖表）。
  - 放进来只会稀释效应。
  - 它们放到 M4 臂或之后的交付轮。
- **为什么主比较用 leave_task_out**：L0 的候选大多来自这 6 个任务的同族任务，有几个就来自同一任务（例如 mokastove p5、bowldrawer r3）。
  - 用 `view=all` 时，B1 的收益可能只是"同任务的旧答案"，不能算跨任务复用。
  - leave_task_out 把证据来自本任务的条目从冻结的库里去掉，B1 − B0 才是跨任务效应。
  - 交付轮再用 `view=all`。
- **每臂每任务 K=2 个全新 cell**，6 × 2 × 2 = 24 个 cell。
  - K=3 要 36 个 cell，按下面的算力需要 3 天，超出一轮 1–2 天；所以本轮取 K=2，K=3 留给交付轮。
  - 同一任务的 B0 与 B1 分在同一节点的不同 slot，提交时间交错，避免服务、节点与时间段的混淆。
  - 不复用 p6d 试跑的 4 个 cell 作 B0：p6e 的 support 与 stager 会变，混用要逐字节证明等价，不值得。
    p6d 试跑只用来决定 B0 的方法本身是否成立。

### 5.2 比较量与预先写定的判定规则
记号：`A51` = 阶段 A 到首次通过 seed 51 的 trial 数（到上限仍未过的记为删失）；`D` = 交付分，即同任务 K 个 cell 里按 unseen 选出的那个的 held-out。

| 编号 | 比较量 | 规则（写定，不按结果改） |
|---|---|---|
| E0 处理落实 | B1 cell 中有 ≥1 个技能被 `verbatim/modified` 使用的比例 | <50% → 结论为"库没被用上"，E1/E2 不作结论，先修 LIBRARY_CONTRACT |
| E1 效率（主） | 各任务 `A51` 中位数之比 B1/B0；删失按上限计 | 6 个任务的几何平均比 ≤0.6，且 ≥4/6 个任务 B1 ≤ B0 → "库提效" |
| E2 结果（主） | 每任务 `D(B1) − D(B0)` | 6 任务平均 ≥ +0.05，且没有任务 ≤ −0.20 → "库提分"；任一任务 ≤ −0.20 → 查该任务用过的技能，按 3.4 复核 |
| E3 偏差 | 冻结开发分 − held-out 的平均差 | B1 不比 B0 大 0.05 以上，否则说明库技能把开发种子过拟合带到了别的任务 |
| E4 成本 | 每 cell 输入 token 中位数、开发小时 | 只报告；B1 高出 >20% 时在结论里写明 |
| E5 首过率 | 拿到首次通过的 cell 数 | 只报告 |
| E6 冻结开发分、unseen 分 | 两臂均值 | 只报告，不作判定 |

- **决策**：
  - E1、E2 都成立 → 后续轮次全部开库（`view=all`），轮间更新。
  - 只有 E1 成立 → 保留库（省时间、省 token），交付分仍靠 M1 的 K 个 cell。
  - E1、E2 都不成立且 E0 达标 → 后续轮关库，资源转给 M1 的 K=3 与 M4 臂。论文里如实报告"没有跨任务收益"。
- **功效**（写在这里，免得事后过度解读）：
  - 同 pipeline 独立重复的 held-out 差绝对值平均 0.36（诊断第 5 节）。K=2 时，每任务 `D` 的差噪声约 ±0.3，6 个任务平均的标准误约 0.12。
    所以 E2 只能看出 ≥0.25 量级的效应，+0.05 的门槛只用于"不变差"的判断。
  - `A51` 跨 cell 从 4 到 111 不等，对数尺度的标准差约 1。每臂 12 个 cell 时，可分辨的比值约 0.45。
    E1 是这组实验真正有功效的比较。
- held-out 只当分数；unseen 只做选择和 cell 级汇总；库的晋升、降级不使用任何 held-out 信息。

### 5.3 GPU 与时间预算
- 一个 cell 在 ASPIRE 预算下开发期 1–19 h（`cells.json` 的 `dev_hours`），再加重扫（≤3 候选 × 15 次）、unseen 15 次、held-out 50 次，
  约 2–4 h。按每 cell 墙钟 ≤24 h 计。
- 一个 8 卡节点 = 2 个 unit = 4 个 cell 并行（`unit_layout.py`；p6d 试跑也是这样）。
- 24 个 cell = 6 个节点批次：
  - rbs-debug2 加 DLC top 池 2 个 8 卡节点，共 12 个 slot，2 批，约 2 天；
  - top 池给 5 个节点时，1 批约 1 天。
  - DLC 提交要用户逐次批准，需写明 workspace、卡数和时长上限。
- L0 建库的 V1/V2：约 6 个 verifier cell，每个只跑 51–55、≤2 版本，1 个节点半天。
- 轮间 curator 加 verifier：半天到 1 天，其中 verifier 占 1 个节点。

## 6. 对论文叙事的影响（只给分析与选项）

**现状**：
- 诊断表明，code world 的执行前机制（screening、逐指令预测）基本没有作用：58 次失败 world 一次都没提前标出，AUC 0.48。
- 有用的是事后判定，以及 M3 的子目标验证。
- 跨任务技能库是上游 ASPIRE 的核心机制之一。
- 因此一旦加库，提分很容易被读成"ASPIRE 的库在起作用"，而不是 code world 的贡献。

**三种讲法**：

| 选项 | 库与 code world 的关系 | 需要的对照 | 好处 | 风险 |
|---|---|---|---|---|
| A 并列组件 | 库就是 ASPIRE 的库，code world 是我们在它之外加的东西 | world × 库 的 2×2，或者至少"ASPIRE 原样带库"作基线 | 归因最干净，审稿人最好接受 | 2×2 要多一倍 cell；如果 world 那一维没有效应，故事就只剩方法修复 |
| B 库是 code world 的一部分 | code world = 每任务的状态与判据模块 + 跨任务的、经验证的代码知识；技能必须带内置子目标验证（`SkillResult`），与 world 判据同一种接口 | 本文第 5 节的 B0/B1，加上"只带动作、去掉验证部分的库"作消融 | 与 M3 的子目标验证、判据校准一脉相承，有区别于上游的点（上游技能是 markdown 片段，不带可执行验证） | 审稿人可能认为这是重新包装；必须用消融证明"验证部分"本身有贡献 |
| C 库只作工程手段 | 库只用来省 token、省时间，主结论仍在无库条件下给出 | 主表用无库；有库只出现在成本表 | 最稳，不影响已有结论 | 浪费了可能的提分；与 ASPIRE 比较时我们少了一个组件，可能吃亏 |

**归因要点**（三种讲法都需要）：
- **库内容的来源**必须可审计：
  - 每个条目的证据只来自开发种子；
  - registry 记录来源 cell 与 bundle sha；
  - 主比较用 leave_task_out。
  这样"跨任务复用"这句话才站得住。
- **拆分两类条目**：动作类（glide、rim-pinch、抽屉开关）与验证类（炉子是否点着、物体是否在容器里、抽屉是否到位）。
  - 两类各自统计使用率与 E1/E2，就能回答"收益来自更好的动作，还是来自更好的状态判定"。
  - 后者和 code world 的关系更直接。
- **与 ASPIRE 的可比性**：
  - m4-r1（solve_ik 报告截断、分段移动、GraspNet 确定化）是 API 改动，已在 manifest 中标注；
  - 库里凡是重复 m4-r1 功能的条目都退役（3.4），避免把 API 改动的收益计到库上。

**需要用户决定的**：选 A、B 还是 C；如果选 A，要不要追加 2×2 里"有库无 world"那一格（约 12 个 cell）。

## 7. 依据、局限与未决项

**依据与可复现性**
- 候选盘点：`scripts/extract_candidates.py` 生成 `data/candidates_raw.json` 与 `data/cell_index.json`，命令为
  `python3 -I scripts/extract_candidates.py data/candidates_raw.json`，只读，耗时 2–15 分钟（上次运行超过 120 s，在 900 s 超时内完成）。
  - 覆盖 8 组结果根、57 个 cell；其中 43 个有可恢复的程序，33 个冻结（或最好）程序通过过 ≥1 个开发种子。
  - policy 字节取自开发 trial 的 `code.py`，其 sha256 与 bundle 的 policy sha 一致；不读 held-out 的轨迹、视频或逐种子结果。
- 卡片：`data/cards_{perception,motion_grasp,place_release,articulated}.md`。由 4 个只读子代理按 `data/CARD_BRIEF.md` 写成，
  汇总在 `candidates.md`。
- 接入点：`data/integration_facts.md`（只读调查，行号以 10-23 00:0x 的 worktree 为准）。
- 上游 ASPIRE：本机副本 `/mnt/home/gewang/code/ASPIRE-upstream-v1.0-fa00f0e`（fa00f0e，10-22 复制，不含 .git），只读。
- 噪声与失败分类：`../code-world-method-diagnosis-20261022/REPORT.md` 第 1、4、5 节，没有重新推导。

**局限**
- "独立重写次数"靠读代码和 grep 计数。函数级的 capability 标签与程序级的正则签名都是粗粒度的，卡片里写明了各自的计数方法。
  多数 worker 把逻辑写在顶层或 `main()` 里，函数标签会低估。
- 开发期证据里，单次 15 种子的通过数噪声很大（诊断第 1、5 节）。卡片引用的"x/15"都是账本数，不是重扫数；
  在 p6d 的重扫机制下要重新取。
- 候选代码还没有在合成观测或真实 trial 上跑过。L0 写成后必须先过第 3 节的 L 检查和 V1/V2。
- 第 5 节的功效估计借用了诊断的跨 cell 方差，没有单独为 leave_task_out 估计。
- 时间与算力按历史 cell 的开发小时估计；p6d 的重扫、unseen 与 libero_latch 会改变每 cell 的耗时，p6d 试跑后要更新。

**未决项（需要用户或协调端决定）**
1. 论文讲法选 A、B 还是 C（第 6 节）；选 A 时要不要加"有库无 world"格。
2. R1 的规模：K=2（24 cell，约 2 天）还是 K=3（36 cell，约 3 天）；DLC top 池能给几个节点、多长时间，需要逐次批准。
3. 库放在 `cap/world_model/library/` 并用 vendoring（第 2 节建议），还是 import。
4. p6e 的实现时机：要等 CC_TASK_14 的改动落定（提交或冻结）再动 `scripts/libero/*.py`，否则会冲突。
5. L0 由谁来写：新的 CC 会话写在库仓库 `/mnt/home/gewang/experiments/code-world-skill-library/L0/`；那是新目录，需要批准创建。
6. mug、twomoka 的 M4 臂（力或位移受限的推拉原语，诊断 M4(c)）不在 CC_TASK_14 的范围里，要不要单独开任务。

**权限拦截**：本任务至今没有遇到权限拦截。上一个会话 10-23 00:13 中断的原因是 API 403（余额不足），不是权限拦截。
那次 4 个写卡片的子代理都没有写出文件，本次已重新派发。
