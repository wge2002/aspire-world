# 候选技能清单（CC_TASK_15 第 1 项，2026-10-23）

## 来源与做法
- 范围：p3、p4（含 rerun-a/b/c）、p4n/p4fix、p5、r3、p6b、ns、l10 结果根，共 57 个 cell，都在当前 API 下跑过
  （runtime manifest 中 `libero_reduced.py` 的 sha 前缀为 fee6d84b7d）。
  - 43 个 cell 有可恢复的程序。
  - 33 个 cell 的冻结程序（没有冻结的取最好的扫描版本）通过过 ≥1 个开发种子，这 33 个是盘点对象（`data/cell_index.json`）。
- 程序字节取自开发 trial 的 `code.py`，其 sha256 与 bundle 的 policy sha 一致。
- 证据只用开发期材料：程序、`findings.md`、`task_analysis.md`、开发种子 51–65 的账本行。
  held-out 只在 `cell_index.json` 里以汇总分出现，用于排序；卡片没有引用任何 held-out 逐种子结果、轨迹或视频。
- 复现：
  - `python3 -I scripts/extract_candidates.py data/candidates_raw.json` 生成函数级能力标签、程序级签名和 findings 里的 "Generalizable patterns"。
  - 卡片由 4 个只读子代理按 `data/CARD_BRIEF.md` 读程序、grep 写成，每张卡写明了计数方法。
- 禁用 API：33 份程序对 `env.handle.env|sim.data|sim.model|sim.forward|body_xpos|get_site_xpos|set_joint_qpos|parsed_problem|_eval_predicate|obj_body_id|_step_once` 全部 0 命中。
  协调端另外抽查复核了几项：`task_language` 26 个 cell，`plan_grasp` 5 个 cell，`GRASP_X_MAX=0.745` 1 个 cell，通过的程序里没有灶圈红度计算。
- 记号：
  - "独立 cell"= 在不同 cell 中各自写出的实现，同一任务的不同 cell 也算独立重写，但不算跨任务证据。
  - "x/15"是账本里的开发分，不是重扫分（诊断第 1 节：多版本 cell 平均高估 +0.11）。

## 总表（24 张卡 + 1 张跨簇卡）
强 = ≥3 个独立 cell 且有 12+/15 或 15/15 的程序。"L0 建议"是本设计对第一版库的提议，不是结论。

| ID | 技能 | 独立 cell | 最好的样本（开发分） | 强度 | 与 m4-r1 | L0 建议 |
|---|---|---|---|---|---|---|
| P1 | 任务语言解析 | 3（23 个只打印） | — | 弱 | 无关 | 降格为"写死名词与 task_language 一致"的断言 |
| P2 | SAM3 候选收集与门控 | 32 | bowl_p5_r1 15/15 | 强 | 无关 | 入库；阈值全部是参数（各 cell 0.02–0.55） |
| P3 | 候选验证与抗劫持 | 19 | stove_p4_r2、bowldrawer_p6_r1 15/15 | 中强 | 无关 | 入库（z/xy 带、形状、重叠）；"夹爪邻近排除"子项不入 |
| P4 | 跨帧身份关联 | 9 policy + 13 world 自述 | spatialdrawer_p4n、wine_p4 15/15 | 概念强，字节证据少 | 无关 | provisional，只做 policy 侧记忆版 |
| P5 | mask+depth 三维定位 | ≥24 | bowl_p5_r1 15/15 | 中强 | 无关 | 入库；带显式状态参数（free/holding/merged） |
| P6 | 遮挡处理 | 每个子技术 2–5 | mokastove_p5 等 15/15 | 中 | 无关 | 并入 P5/M6，作为返回 `None` 的约定 |
| M1 | 分段移动 / glide | 9 | spatialdrawer_p4n 15/15 | 强 | **已被 `goto_pose(max_step)` 取代** | 只保留"实测到位校验 + 重发" |
| M2 | IK 可达性守卫 | 1–2 | spatialdrawer_p5_r1 15/15 | 弱 | 大部分被取代（打印截断与换姿态） | 不入 |
| M3 | 手姿四元数 helper | 14 | stove_p4_r2、mokastove_p5 等 15/15 | 强 | 无关 | 入库；统一符号约定 |
| M4 | 碗沿捏取 rim-pinch | 13 | bowl_p5_r1、spatialbetween_p5 15/15 | 次数强，耦合重 | 无关 | 入库，改为用点云极值取壁点（各 cell 的偏移系数互相矛盾） |
| M5 | `plan_grasp` 接受带 + 几何回退 | 5 | spatialdrawer_p5_r1 15/15 | 弱/中 | GraspNet 确定化以后才有可复现的证据 | 只入"门控 + 几何回退"，CGN 作为可选 |
| M6 | 抓取验证与换策略重试 | 宽度闸门 12 / 抬升 10 / 阶梯 8 | spatialdrawer_p5、bowldrawer_p6_r1 15/15 | 强 | 无关 | 入库；宽度带按物体传参 |
| R1 | 带携带偏移补偿的释放 | 12（8 个任务） | bowl_p5、spatialbetween_p5、spatialdrawer_p5 15/15 | 强 | 无关 | 入库（本组最扎实） |
| R2 | 释放前下降高度 | 10 | 6 个程序 15/15 | 强（作为标定表） | 无关 | 入库；高度由调用方给，或从目标面点云估计 |
| R3 | 放入容器、抽屉、篮子 | 13 | objswapsoup_p6、bowldrawer_p6 15/15 | 模式强，常数弱 | 无关 | 入库；开口中心必须实测 |
| R4 | 释放后确认与有界纠偏 | 7 / 3 / 3 | spatialbetween_p5、spatialdrawer_p4n 15/15 | 中 | 无关 | 入库作验证类；"是否倾斜"是缺口 |
| R5 | 达成即停手 | 9 | wine_p4 15/15 | 中 | 吞异常那一半被 `libero_latch` 取代 | 只保留"确认即停"和"不撤销已达成的状态" |
| A1 | 抽屉把手定位 | 12（纯 policy 版 2 个） | 4 个 15/15 | 强 | 无关 | 入库 |
| A2 | 前向竖爪夹持拉抽屉 | 7 | drawer_p6_r1、drawer_p6b、drawer_p4_r2b、bowldrawer_p6_r1 15/15 | 强 | 分段与两次 goto 被取代 | 入库，只保留接触与验证逻辑 |
| A3 | 抽屉开度验证 | 9 | 多个 15/15 | 强 | 无关 | 入库；阈值作为参数（0.05–0.135） |
| A4 | 抽屉关闭推送 | 2 | 最好 6/15 | 弱（缺口） | 需要 M4(c) | 不入；只留三条规则进 A3 的卡片 |
| A5 | 旋钮弧线旋转 | 6 | stove_p4_r1b、stove_p4_r2、mokastove_p5 15/15 | 强 | 分段部分被取代 | 入库 |
| A6 | 灶圈红度等设备状态检查 | 0（唯一实现在 0/12 的 mokastove_p4n） | — | 缺口 | 无关 | **需要新做**：在开发关键帧上标定后进 V1/V2（见下） |
| A7 | 铰链门 / 微波炉 | 0 | — | 缺口 | 需要 M4 原语 | 不入 |
| X1 | 终止安全包装与"越过阈值的一步放在最后" | ≥5（drawer_p5_r2、drawer_p6b、stove_p3_r2、bowldrawerclose_p6b、mugmicrowave_p4n） | drawer_p6b 15/15 | 中 | 吞异常那一半被 `libero_latch` 取代 | 与 R5 合并 |

**汇总**：
- 强候选 14 个：P2、P3、P5、M1、M3、M4、M6、R1、R2、R3、A1、A2、A3、A5。其中 M1 已被 m4-r1 取代，可用的是 13 个。
- 中等 5 个：P4、P6、R4、R5、M5。另有跨簇卡 X1，也是中等。
- 弱或缺口 5 个：P1、M2、A4、A6、A7。
- 建议的 L0 约 12 个条目：
  - P2+P6、P3、P5；
  - M3、M4、M6（并入 M1 的到位校验）；
  - R1+R2、R3、R4+R5+X1；
  - A1+A2、A3、A5。
  - 另有 A6，是一个需要新做的验证类条目。

## 对冻结程序 58 次开发期失败的覆盖（诊断 4.1）
| 失败类别（次数） | 发生在哪 | 库能做什么 | 覆盖判断 |
|---|---|---|---|
| 铰接 30：炉子没点着 12 | mokastove_p6b | A5 给出 15/15 的旋钮动作（mokastove_p5）；A6 给出"点着没有"的观测判定 | **有条件覆盖**：A6 要先在开发关键帧上标定红度阈值并过 V1/V2；M3 的子目标验证契约是通用的兜底 |
| 铰接：抽屉没关到位 16 | bowldrawerclose_p5/p6b | A3 能判"没关到位"，但没有任何通过的关门动作（A4 弱） | **覆盖不了**，需要 M4(c) 力或位移受限的推拉原语 |
| 铰接：rake 没勾住 2 | goaltask_p5 | A2 前向夹持 + A3 开度验证 | 覆盖 |
| 放置/释放：开环松手、盒子立着或斜着 10 | goaltask_p6b | R1 偏移补偿 + R2 下降高度 + R4 释放后确认 | 部分覆盖：没有冻结程序估计物体姿态，"是否倾斜"只能用 z 代理 |
| 放置：碗斜搭在抽屉壁或前沿 6 | bowldrawerclose_p6b 4、bowldrawer_p6b 2 | R3 实测开口中心 + R4 确认 | 部分覆盖（姿态判定同样是缺口） |
| 放置 1 | objtaskmilk | R1 | 部分覆盖；这个 cell 本身的证据是噪声 |
| 感知 4：SAM3 "handle" 悬空误检等 | bowldrawer_p6b 3、drawer_p5_r2 1 | P2 门控 + P3 抗劫持 + A1 | 覆盖 |
| 抓取 4：再抓时抓空等 | drawer_p5_r2 2、objtaskmilk 1、spatialtaskcookie 1 | M6 宽度闸门 + 抬升验证 + 阶梯重试；M4 | 覆盖 |
| 搬运掉落 2 | spatialtaskcookie | M6 的携带检查只能察觉，不能防止 | **覆盖不了**：摩擦临界的抓取没有有效对策（三个 cell 都 BLOCKED） |
| 未知 1 | bowldrawerclose_p5 | — | — |

- 直接覆盖 10 次：感知 4、抓取 4、rake 2。
- 部分覆盖 17 次：放置/释放。偏移补偿、下降高度、开口中心都有可用的样本，但"物体是否倾斜"没有任何冻结程序判定过。
- 有条件覆盖 12 次：炉子，取决于 A6。
- 覆盖不了 19 次：关抽屉 16、掉落 2、未知 1。
- 合计 10 + 17 + 12 + 19 = 58。
- 58 次之外，从未拿到首次通过的两个任务也不在库的能力范围内：
  - mugmicrowave：关门把杯子推出来，门的几何判断各运行互相矛盾，指尖可达也到了下限；
  - twomoka：第二个壶。盖子更高，抓取深度不对；`solve_ik` 截断在 x≤0.75。
  这两个都需要 M4 原语（推拉原语、可达性探针），不需要库。

## 过拟合与噪声嫌疑（不作证据）
- objtaskmilk_p6b：13/15 是噪声最大值，重跑只有 8/15（诊断）。它的 `goto`、`_topdown_yaw_quat`、`compute_grasp`、MILK_GATE 只算旁证。
- spatialtaskcookie_p6b：rim-pinch 在摩擦临界，后期修补把 15 个开发布局过拟合了。它的 `glide`、碗沿冠顶、`_mask_cloud` 不入库。
- goaltaskbowldrawer_p6b 的 `GOODPAIR`：把 dev-51 的四个好姿态加噪声重采样，`default_rng()` 没设种子。**禁止入库**。
- spatialdrawer_p4n 的 `hang_x, hang_y = 0.025, 0.085` 常数预补偿：不入库，R1 用实测偏移。
- drawer_p5_r2 的 12/15 是 9 个版本里的最大值；bowldrawer_p5_r1 是没被选中的扫描版本。引用时不能当作"选定版本"。
- mokastove_p6b（3/15）、bowldrawerclose_p6b（3/15）、goaltask_p6b（5/15）只用来说明"这种失败发生过"，不作为有效实现。

## 需要裁决的矛盾（写 L0 之前）
1. 碗沿偏移系数：0.75 在 bowl_p5_r1 上全败，而 0.88、0.052、0.045、0.35·hx 各在自己的 cell 里通过。
   建议不用系数，改为沿指轴取点云极值。
2. 前向抓取四元数 (0.7071,0.7071,0,0) 的指轴：drawer_p6_r1 的注释说沿 X，其余说竖直；旋转数学上是竖直。
   bowldrawer_p4_r1b 用 (0.707,−0.707,0,0)，符号相反，没有核实。
3. 同一个 bowldrawer 任务：p6b 认为"只有倾斜钩子可行"，p6_r1 用前向竖爪 15/15。
   同一个 KITCHEN_SCENE3：闭爪钩扫 0/12，夹穹顶加转腕 15/15。
4. TCP 到手的偏置各 cell 实测在 0.100–0.115 之间，不能写死。
5. 逐段重读实测位姿再解 IK：objtaskmilk 称会发散，spatialdrawer_p4n、bowldrawer_p5_r1 则是逐步闭环有效。m4-r1 的分段原语出来后以它为准。
6. 定位估计器：bowl_p3 说整圈拟合会被手臂像素带偏，要回退中位数；bowldrawer_p4_r1 靠环心翻转了 seed 56。
   "排除手附近的点"在 bowldrawer_p4_r1 和 p6/p6b 之间也相反。库里用显式状态参数，不固化。

## A6 的建议做法（只读、零 GPU，留给 L0 作者）
- 唯一的实现在 mokastove_p4n_r1：在炉面 mask 上计算 `(R−max(G,B)>40)&(R>110)` 的像素比例，ON 阈值 0.05。
  它只用一次点亮事件标定，仓库里另有 0.091 与约 0.2 两处不一致的记录。
- 做法：
  1. 在 stove_p4_r2、mokastove_p5_r1 的**开发** trial 关键帧上，对旋钮动作前后分别计算红像素比例；
  2. 取两类之间的间隔作为阈值，阈值作为参数，默认值留空；
  3. 再按第 3 节过 V1/V2。
- 诊断（4.1、M3）已经核实失败帧的线圈是灰的、通过帧发红光，所以这个信号确实存在。

---

以下是四组卡片原文（由 `data/cards_*.md` 拼接，内容未改）。


# 感知组候选技能卡 (perception cluster, CC_TASK_15)

证据范围：开发期 (seeds 51-65) 的 33 份 policy_path 程序字节、各 cell 的 findings.md（开发期 worker 自述）、cell_index.json 的 dev_passed/dev_tested/n_versions_swept。未打开 development_state.json（体积大，本组不需要），未打开任何 heldout/sealed/unseen 路径，未引用 held-out 逐 seed 结果；cell_index 里的 held-out 率只用于排序，文中不引用。

计数方法与口径：
- "独立重写" = 不同 cell（各自独立沙箱里的 worker）在自己的 policy 字节里各写了一份功能等价的实现。先用正则对 33 份 policy 做特征粗筛（分数/面积/点数门槛、median、percentile、OBB、task_language 等），再对命中文件逐个读函数确认；正则命中但读后不是该功能的不计。同一 cell 内多个函数只计 1。
- 两类证据必须分开：(a) policy 字节里可见的实现；(b) findings.md 自述"关联规则写在 world 程序里"的实现。world 程序本身不在 policy_path 内，本组没有读到其字节，所以 (b) 只能当作"worker 自述"，在卡里单独标注，不计入"独立重写"主数字。
- 所有 33 份 policy 都 `import world`；本组扫描到 0 处禁用 API（env.handle.env、sim.data、body_xpos、set_joint_qpos、parsed_problem、_eval_predicate、_step_once、.bddl/.xml/.urdf 均无命中）。`env.handle.task_language` 属允许 API，不算禁用。
- 行号均指对应 cell 的 policy_path 文件（路径见 cell_index.json）。

## 卡片

### P1 任务语言解析：task_language -> 目标名词 / 序数 / 关系短语（弱，建议降格为"输入一致性检查"）
- 功能与接口草案：把 `env.handle.task_language` 解析成结构化字段，并与 policy 里写死的目标名词做一致性断言；解析失败返回 None，不给默认值。
  ```python
  def parse_task_language(lang: str, *, ordinal_words: dict[str, tuple[str, ...]],
                          verb_pat: str, stop_pat: str) -> dict:
      """-> {"raw": str, "target": str|None, "goal": str|None,
              "ordinal": str|None, "relation": str|None}"""
  def assert_language_matches(lang: str, required_any: tuple[str, ...]) -> None: ...
  ```
- 出处：最佳 = drawer_p6_r1，bundle 8bc3d729，policy_sha12 66cb68a80f74，`_ordinal_from_language` L27-36（top/upper、bottom/lower、否则 middle）。次选 = objswapsoup_p6_r1（0825bb17 / c6d120596c70）`target_name` L31-41（动词正则取名词短语）；objswapsoup_p4n_r1（de34728c / b160ae46be4f）inline L29-35。
- 独立重写：3 个 cell 真正解析语言（drawer_p6_r1:_ordinal_from_language；objswapsoup_p6_r1:target_name；objswapsoup_p4n_r1:inline L29-35）。计数法：grep `task_language` 共 26/33 个 cell 引用，其余 7 个（stove_p3_r2、bowldrawer_p4_r1、stove_p4_r1b、mokastove_p5_r1、bowldrawer_p6b_r1、drawer_p6b_r1、mokastove_p6b_r1）完全不碰；26 个里 23 个只 print/log。spatialbetween_p4n_r1 L111-115 明确把关系短语 PHRASE="akita black bowl between the plate and the ramekin" 写死，并注释"parsing task_language is redundant here"。
- 开发期证据：drawer_p6_r1 15/15（1 版）——findings 称按 z-rank 选 top/middle/bottom"在每个 seed 上稳定"，未报告某 seed 被它翻转。objswapsoup_p6_r1 15/15（1 版）——findings 里翻转 seed 51 及其余 14 个 swept seed 的是"mask 平均 R-B 取最小"的颜色规则，不是语言解析。objswapsoup_p4n_r1 4/15（1 版）：NAME 只用作 L126 的一个 SAM3 提示词（给各检测打"soup"分），其 findings 反而结论为"名字不可辨认，改为全部装载"，解析没有被报告为翻转因素。
- 依赖 API：只用 `env.handle.task_language`（允许）；解析函数本身不需要 `world`。其中 drawer_p6_r1 把序数传给 `world.update(values={"ordinal":...})`，由 world 里的 z-rank 规则消费（world 侧规则不在 policy 字节内）。
- 子目标验证：exemplar 均无。必须内建的信号：解析出的名词/序数在首次感知里至少有一个 SAM3 候选分数 >= floor；否则切换到形状词提示（objswapsoup_p4n_r1 findings：名字提示分数 <=0.10 而形状提示 0.9+，用"一次廉价诊断"区分）。
- 任务耦合：drawer_p6_r1 的默认分支 `return "middle"` 是静默默认；objswapsoup_p6_r1 的 `target_name` 默认 `"alphabet soup"`、`TARGET_PROMPTS=["can","soup can"]`、`npix>=900`、颜色极性（蓝/黄罐 R-B≈-1，红罐≈+14）都是该资产对的经验；p4n 正则 `(?:pick up the|pick the|take the)\s+(.+?)\s+and`。
- 参数化：序数词表、动词/截止正则、默认值（应为 None）、required_any 都做参数。可运行前置检查：
  ```python
  lang = str(env.handle.task_language).lower()
  assert any(w in lang for w in ("milk",)), f"policy 写死 milk，但任务是 {lang!r}"  # 调用方传入自己写死的词
  ```
  findings 的动机：objtaskmilk_p6b_r1 的文件名叫 ketchup，实际语言是 "Pick the milk and place it in the basket"；goaltaskbowldrawer_p6b_r1 文件名叫 bowl，实际目标是 cream cheese。两处都是 worker 肉眼读语言后写死，没有程序化检查。
- 风险：(1) 静默默认 = 静默错配风险（"middle"/"alphabet soup"）；(2) 序数判断顺序敏感："top layer of the drawer and put ... inside" 含 top，但同句可能同时出现 bottom/lower 描述别的对象，需要限定在 drawer 名词附近；(3) 在 `_swap` 套件里名字提示分数近 0，解析出名词也无法直接当 SAM3 提示（objswapsoup_p4n_r1 的结论）；(4) 证据主要来自 3 个 cell，且 2 个是同一任务；不构成"强"证据。与 m4-r1 无冲突。
- 覆盖的失败类型：perception 4（SAM3 "handle" 误命中）中"选错序数"那一部分、以及任务身份写错（上述 milk/cream cheese 两例）；不覆盖 articulated/place/grasp/drop；解析本身不产生动作，需要 P2/P3/P4 配合。

### P2 SAM3 候选收集与门控：top-N 上限 + 分数下限 + 面积窗 + 最少点数 + 去重/NMS，返回"全部通过者"而非单个最高分（强）
- 功能与接口草案：对一个文本提示取 SAM3 提案，按分数排序、封顶 N，逐个按分数/面积/点数/框形状过滤，反投影为世界点云，返回候选列表；选择哪一个交给 P3/P4。
  ```python
  def collect_candidates(rgb, depth, K, T, prompt: str, *, score_floor: float,
                         area_px: tuple[int, int], min_points: int, max_candidates: int,
                         box_side_px: tuple[float, float] | None = None,
                         box_iou_nms: float | None = None, dedupe_xy_m: float | None = None,
                         max_backprojections: int = 8) -> list[dict]:
      """-> [{"score","area","n","box","center":[x,y,z],"top_z","mask","pts"}...]"""
  ```
- 出处：最佳 = bowl_p5_r1，bundle 6c227a41，policy_sha12 b8a1c77002ba，`localize` L49-82（按分数排序取前 6、面积 400-60000、点数>=100，输出 median 中心 / p95 顶 / OBB extent，全部候选交给 world 关联）。次选：spatialbetween_p4n_r1（a73646d0 / 4062e3444079）`_pick` L49-77（分数>=0.5、面积>=64、框 IoU>0.35 的 NMS、封顶 k）；wine_p4_r1（98e17580 / c725f1c003ee）`gather`+`_rough_xy` L49-110（先用框中心单点反投影粗排，只对前 4 个做整 mask 反投影）；spatialdrawer_p5_r1（911d5f26 / 2c760350b2da）`_cands` L44-61；bowldrawer_p4_r1b（5daaf83b / 565740b3deda）`_union_points` L132-148（合并碎片 mask）。
- 独立重写：32/33 个 cell（唯一例外 drawer_p6_r1：调用 SAM3 但丢弃返回值，感知全在 world 里）。计数法：逐文件找到 `segment_sam3_text_prompt` 调用点及其下游 mask 过滤函数，确认含"分数或面积/点数门槛"。列表：bowl_p3:select_candidate、bowl_p4_r1b:perceive、bowl_p5:localize、drawer_p3:cents、drawer_p4_r2b:_cam_handles、drawer_p5_r2:observe_handles、drawer_p6b:perceive_handle、stove_p3_r1:_top_masks、stove_p3_r2:perceive L158-186、stove_p4_r1b:_knob_mask、stove_p4_r2:perceive L165-199、bowldrawer_p4_r1:observe_targets、bowldrawer_p4_r1b:perceive、bowldrawer_p5:collect、bowldrawer_p6:_perceive、bowldrawer_p6b:_bowl_obs/_handle_obs、bowldrawerclose_p5:_bowl/_drawer_pts（无分数门槛，仅点数）、bowldrawerclose_p6b:_pick、goaltaskbowldrawer_p5:_pick_mask、goaltaskbowldrawer_p6b:_pick、mokastove_p5:_seg_best、mokastove_p6b:_pick（仅面积窗）、objswapsoup_p4n:top_masks、objswapsoup_p5:_backprojected、objswapsoup_p6:_mask_entries、spatialbetween_p4n:_pick、spatialbetween_p5:perceive L80-93、spatialdrawer_p4n:_masks、spatialdrawer_p5:_cands、spatialtaskcookie:_instances、wine_p4_r1:gather、objtaskmilk:_mask_cloud。
- 开发期证据（均为 dev seeds，来自 findings）：bowl_p5_r1 15/15（1 版）——"提交全部 top-6 候选，由 world 选目标"消除了 seed 51 的 bowl->plate 身份跳变。drawer_p4_r2b 15/15（1 版）——分数下限 0.35 + 每提示封顶 5/提前 break，把每试验 API 调用从 1679 降到约 48，并修正 51-53 的错误把手身份。stove_p3_r2 6/6——下限 0.08（真旋钮仅 0.14-0.25）"翻转了每个 seed 的检测"。spatialbetween_p4n_r1 10/15（2 版）——分数>=0.5 + 框 NMS 处理 200 个重叠提案（真实例>0.85、垃圾<0.25）。goaltaskbowldrawer_p5_r1 13/15（4 版）——"分数优先而非面积优先"修复 bowl->plate、cabinet->table。wine_p4_r1 15/15（0 版）——上下文预排序使真瓶子不被 200 个部件 mask 淹没（seed 57/58）。bowldrawer_p4_r1 4/7：面积 25-1400 + z 门控清除了 seeds 51-61 的把手锚点误报。
- 依赖 API：segment_sam3_text_prompt、mask_to_world_points、get_observation（rgb/depth/intrinsics/pose_mat）。过滤本身不需要 `world`；但 32 个实现里约 20 个把结果直接写进 `world.update(...)`，字段名各 cell 自定（候选 dict 的键不统一），库函数应返回纯 dict 由 policy 转发。
- 子目标验证：无独立验证。需要内建：(1) 返回 0 个候选时显式 `status="no_candidate"` 而不是 None 静默；(2) 最高分与次高分差距（bowldrawerclose_p6b_r1 L91-94 用 0.05 作 `drawer_ambiguous` 拒绝阈）；(3) 打印前 3 个原始分数供事后校准。
- 任务耦合（阈值在 cell 间差异巨大，必须是参数）：分数下限 0.02(spatialdrawer_p4n)/0.05(objtaskmilk)/0.08(stove_p3_r2)/0.15-0.2(goaltask_p5、bowldrawer_p6、mokastove_p5)/0.25(bowl_p4_r1b、stove_p4_r1b、spatialbetween_p5)/0.30-0.35(bowldrawer_p5、drawer_p4_r2b、drawer_p6b)/0.4-0.5(wine、bowldrawer_p6b、spatialbetween_p4n)/0.55(spatialdrawer_p5 的 bowl)。面积单位也不统一：像素（bowl_p5 400-60000；spatialdrawer_p5 bowl 1500-9000 / plate 800-12000）、图像占比（mokastove_p6b 0.0006-0.12）、百分比（spatialtaskcookie 0.02-6.0）。最少点数出现过 5/8/12/20/30/40/60/100。
- 参数化：score_floor / area / min_points / max_candidates 全部由调用方给；`max_backprojections` 对应 findings 里反复出现的"每个 mask_to_world_points 都消耗预测窗口"（bowl_p3_r1：每候选约 10-16 次公共调用；stove_p3_r1：约 600 次；bowldrawer_p4_r1：2211 次）。可运行前置检查：
  ```python
  ms = segment_sam3_text_prompt(rgb, prompt); top = sorted((float(m["score"]) for m in ms), reverse=True)[:3]
  assert top and top[0] >= score_floor, f"{prompt!r} 前三分数 {top} < {score_floor}：提示未落地（swap 套件？）"
  ```
- 风险：(1) 分数下限在 15 个 dev 布局上校准，换布局可能过严（stove_p3_r2 初版 0.30 直接把真旋钮全拒掉，"程序未动就退出"）；(2) 反过来过松会放进 gripper/背景（见 P3）；(3) objtaskmilk（ns，GraspNet 不确定性使其 13/15 是噪声极大值）和 spatialtaskcookie（ns，后期修补过拟合 15 个 dev 布局）的门槛不应当作独立证据，但本卡的模式在其余 30 个 cell 里都有，不依赖它们；(4) 预测窗口耗尽属 p2/p3 world 契约问题，若新 world 契约改变，`max_backprojections` 的数值要重估。与 m4-r1 无冲突（感知不受影响）。
- 覆盖的失败类型：perception（SAM3 误命中、候选洪水）；这是 findings 里"初始运行 seeds 51-53 的共同失败"，冻结程序已修复所以在 58 个冻结失败分类里只占 perception 4。不覆盖 articulated/place/grasp/drop。

### P3 候选验证与抗劫持：剔除机械臂/夹爪、背景同类物、被附着零件、同名干扰物（中强；"夹爪邻近排除"子项偏弱）
- 功能与接口草案：对 P2 的候选逐个做几何/外观/邻近检验并记录拒绝原因；被"劫持"的典型情形是提示词（handle/knob/bowl/cabinet）命中夹爪、手腕、背景柜、炉头、同色容器。
  ```python
  def reject_hijacked(cands: list[dict], *, hand_pos=None, hand_radius: float = 0.10,
                      z_band: tuple[float, float] | None = None,
                      xy_box: tuple[tuple[float, float], tuple[float, float]] | None = None,
                      anchor_mask=None, max_mask_overlap: float = 0.35,
                      min_z_extent: float | None = None, footprint: tuple[float, float] | None = None,
                      color_gate: dict | None = None,
                      exclude_near: tuple[tuple[tuple[float, float], float], ...] = ()
                      ) -> tuple[list[dict], list[dict]]:
      """-> (kept, rejections=[{"i","reason","value"}])"""
  ```
- 出处：最佳 = bowldrawer_p6_r1，bundle a53bcde9，policy_sha12 445b5d6dc115，`_perceive` L86-98（把手质心 z 必须在 0.06-0.26，注释"reject the robot hand as a handle"；距柜体中心水平 >0.35 拒绝）。次选：stove_p4_r2（05662bdd / 174d84b05b3c）`perceive` L144-199（逐条打印拒绝原因：点数<40、距手 <=0.10、杆/鳍特征门、尺寸 ext 0.04-0.18 且 hspan 0.02-0.10、距炉 0.03-0.30、z 上限 stove_zmed+0.11、与炉 mask 重叠>0.35）；bowl_p3_r1（e29f25c9 / 200401ef14cd）`select_candidate` L92-135（最大簇 + zspan/footprint/亮度形状验证）；goaltaskbowldrawer_p6b_r1（620d696b / ddcf786449ca）`perceive` L63-95（柜体包围盒 + 上一次接受把手的运动约束）。
- 独立重写：policy 字节内 19/33 个 cell 有"分数/面积之外"的有效性门（计数法：读 P2 列表里每个过滤函数，凡含 z/xy 带、工作区裁剪、形状/高度/亮度/颜色、与别的 mask 重叠或邻近排除者计入）：bowl_p3:select_candidate+clean_points、bowldrawer_p4_r1:anchor_from、bowldrawer_p6:_perceive、bowldrawer_p6b:_bowl_obs/_handle_obs、bowldrawerclose_p5:_near、bowldrawerclose_p6b:_drawer_in_workspace L70-75、drawer_p4_r2b:_cam_handles L117（框宽 8-120、高 15-260、高>=宽）、drawer_p6b:_clean L20-25、goaltask_p6b:perceive、stove_p3_r1:_tall_dark_components、stove_p3_r2:_geom、stove_p4_r1b:_knob_mask L69-71、stove_p4_r2:perceive、objtaskmilk:_mask_cloud L72-74（z 跨度>=0.075）、mokastove_p5:AREA_WINDOW L22、objswapsoup_p4n:full_perception L113-117（距篮子<0.14 的是篮子幻影）、objswapsoup_p5:_feat_candidates、objswapsoup_p6:_mask_entries、spatialdrawer_p4n:find_target。其中"距夹爪/手邻近排除"在 policy 字节内只有 2 个 cell（bowldrawer_p6b_r1 L87 阈 0.11 与 L119 阈 0.12；stove_p4_r2 L152/L173 阈 0.10）。另有 findings 自述写在 world 里的邻近/带门：bowldrawer_p6_r1（距 pad 点 0.10）、spatialdrawer_p5_r1（距手 0.20）、stove_p4_r1b（距上次可信旋钮 0.07/0.06）、goaltask_p5（|dz|<=0.03）、drawer_p3_r1（指尖 0.06），本组读不到其字节，不计入主数字。
- 开发期证据：stove_p4_r2 15/15（0 版）——v2 的"重叠>0.35 + 距手 0.10 + z 上限 + 提示词序"翻转 seed 51，v3 再翻转 56/58/59。bowl_p3_r1 4/5（0 版）——形状验证翻转 seed 53，并让 54-63 类运行得以执行。bowldrawer_p6_r1 15/15（4 版）——把手门控去除了 seed 51 的假 open_amount 0.13 和 0.52 m 的错误投放高度。bowldrawer_p6b_r1 10/12（6 版）：findings 称不做"最近已知质心 + 拒绝近夹爪斑块"，SAM3 会把金属夹爪当 "metal bowl"/"handle"。goaltask_p6b_r1 5/15（7 版）：dev-51 attempt 65 曾把夹爪手腕当把手。objswapsoup_p4n_r1 4/15：篮子幻影曾劫持 v6 的 "milk" 抓取。
- 依赖 API：segment_sam3_text_prompt、mask_to_world_points、get_observation（`robot_cartesian_pos[:3]` 取手位置）；纯几何过滤不需要 `world`。邻近门需要上一帧可信位置，那部分通常放在 world.update 里（见 P4）。
- 子目标验证：只有 stove_p4_r2 对每个拒绝打印原因（`[dbg] reject ...`），其余 cell 静默丢弃，事后无法区分"没看见"与"看见但被拒"。必须内建：返回 rejections 并在全部被拒时给出主因，供 P6 判定"遮挡 vs 误拒"。
- 任务耦合：z 带 0.06-0.26 是该柜子把手高度（findings：中层把手 z≈0.11，顶层约 0.19）；xy_range ((0.35,0.80),(-0.25,0.40)) 是该桌面布局；亮度 30-215 / 饱和度<=60 专为银色碗（拒白盘和蓝盒）；`CAB_DEV=[[0.54,-0.38,-0.01],[0.84,-0.12,0.25]]` 是写死的柜体包围盒兜底；bowldrawerclose_p6b 的 x∈(0.20,0.78) 来自 solve_ik 裁剪范围。这些全部应是参数。
- 参数化：hand_radius、z_band、xy_box 必须由调用方基于首帧干净观测标定，不得继承常量。可运行前置检查（防"把夹爪当目标"）：
  ```python
  hand = np.asarray(obs["robot_cartesian_pos"][:3], float)
  assert all(np.linalg.norm(np.asarray(c["center"]) - hand) > hand_radius for c in kept) or holding
  ```
  注意中心点定义：robot_cartesian_pos 是手掌体，指尖在其下约 0.107 m（spatialdrawer_p5_r1 L19 `TCP_UP=0.107`）；bowldrawer_p6_r1 的 world 门用的是 pad 点，stove_p4_r2 和 bowldrawer_p6b 用的是手掌体，二者口径不一。
- 风险：(1) 反例：bowldrawer_p4_r1 L193-194 明确"no TCP self-exclusion：手指扶着杆时杆就在指尖"，所以邻近排除必须随抓取状态切换，否则会把真目标拒掉；bowldrawer_p4_r1b 的做法是"夹持期间根本不感知把手，改感知碗"。(2) 形状/颜色门（亮度、R-B）强烈依赖资产和光照；objswapsoup_p5_r1 里手臂阴影把蓝标签压到 RGB≈[51,49,48]，颜色门随之失效（4/15）。(3) 14 个 cell 用宽泛 `except Exception` 包住 SAM3 调用，仅 drawer_p3_r1、stove_p3_r1 对 `world.Unsupported` 重新抛出；stove_p3_r1 findings 记录过因吞掉它导致的 `unsupported_contract` 拒收，新库函数必须保持该异常透传。(4) spatialtaskcookie/objtaskmilk 的门槛为噪声/过拟合嫌疑，未作为证据。
- 覆盖的失败类型：perception 4（"handle" 误命中）直接相关；对 articulated 30 间接有益（假 open_amount、假进度被消掉，如 bowldrawer_p6_r1 的 0.13）；不覆盖未点燃炉灶、抽屉未全开关这类需要 M4 原语的物理问题。

### P4 跨帧身份关联：锚点 / 最近已知记忆 / 高度与关系排序，宁可 unknown 不静默换身份（概念强；policy 字节内的实现偏少）
- 功能与接口草案：在多个同类候选里确定"哪个是任务目标"，并在后续帧里保持同一身份；首次获取靠几何规则（高度序、与参照物的关系），之后靠"离上一次可信位置最近且在半径内"；搭载/放置/自由三种状态用不同参照；不满足任何规则返回 None（unknown）。
  ```python
  def associate(cands: list[dict], *, mode: str,            # "free" | "carried" | "placed"
                track: np.ndarray | None, radius_m: float,
                hand_pos: np.ndarray | None = None, carry_offset: np.ndarray | None = None,
                first_acquisition=None,                    # callable(cands)->dict|None
                exclude: tuple = (), ambiguity_margin_m: float = 0.015
                ) -> tuple[dict | None, str]:               # (chosen, reason_tag)
  def rank_column(cands, *, ordinal: str, align_axes=(0, 2), align_tol_m=0.06,
                  min_count=3, min_gap_m=0.035) -> dict | None   # top/middle/bottom by z
  ```
- 出处：最佳 = spatialdrawer_p4n_r1，bundle bb321f66，policy_sha12 9fb314246c56，`find_target` L79-148（三种模式：placed 取靠近盘子的候选；carried 取距手 <0.16 且排除盘邻近，否则 `carried_unseen`；free 取 z>0.08 且不在盘上、图像行最靠下者，即"抽屉里的碗"，返回原因标签）。次选：wine_p4_r1（98e17580 / c725f1c003ee）`gather(context, ctx_radius=0.32)` L66-110 + `_ANCHOR`/`anchor_update` L144-158（只在 world 回答 known 时更新"最后一次真实观测"）；bowldrawer_p6b_r1（efc0c704 / 0ae62a9dfd88）`_assoc_point`/`_bowl_obs`/`_handle_obs` L60-126；高度序：drawer_p6b_r1（b4a2118c / d66f594bf123）L66-68（按 z 排序取中位，不足 3 个取最高）。
- 独立重写：policy 字节内 9/33 个 cell 有身份规则：最近已知/上下文关联 5 个（bowldrawer_p6b:_assoc_point；wine_p4_r1:gather+_ANCHOR；objtaskmilk:_mask_cloud 的 xy 门 L47-90；goaltask_p6b:LASTH 门 L51-95；spatialtaskcookie:fresh_rim_top 取 0.05 内最近 L127-138），几何/模式关联 1 个（spatialdrawer_p4n:find_target），按高度排序 5 个（drawer_p6b L66-68；bowldrawer_p6 L99-100"最高=顶层"；bowldrawer_p4_r1b L177 argmax z；goaltask_p6b L91"最高通过门者=顶层"；bowldrawer_p6b L125 兜底 max z；与上面有交叠，去重后合计 9）。另 13 个 cell 的 findings 自述关联规则"写在 world 的 update 适配器里"，policy 只把全部候选递给 world：bowl_p5、bowldrawer_p4_r1、drawer_p3、drawer_p4_r2b、drawer_p5_r2、drawer_p6_r1、goaltask_p5、objswapsoup_p5、objswapsoup_p6、spatialbetween_p4n、spatialbetween_p5、spatialdrawer_p5、stove_p4_r1b。其中 bowl_p5_r1 的头注释 L6-9 和 spatialdrawer_p5_r1 的头注释 L10-11 在 policy 字节里直接写明"association is authored in the world"。合计 22/33，但 13 个只有自述，没有字节。
- 开发期证据：spatialdrawer_p4n_r1 15/15（2 版 [13,15]）——findings：几何关联（z>0.08、距盘>0.06、最低图像行）"翻转了 seeds 51-55 的身份"；对照实验"举起另一只相同的碗"在 seed 55 得 reward 0；v21 把"是否搬运"改为夹爪宽度>=0.035 而非视觉重检，消除了关联漂移造成的假阳性。wine_p4_r1 15/15（0 版）——上下文关联在抓取和释放时保持瓶子身份（seeds 57/58）。bowl_p5_r1 15/15（1 版）——world 侧规则"top>0.028 的是碗、优先最近上次位置 0.30 m 内"终结 bowl->plate 跳变。objswapsoup_p6_r1 15/15（1 版）——最近预测关联，半径自由 0.12 / 夹持 0.18，邻罐相距>=0.2（0.35 m 外的错误检测被拒），15 个 seed 无身份翻转。objswapsoup_p5_r1 4/15（6 版）——半径 0.18 让身份跳到另一罐，收紧到 0.13 后身份恢复，但总体仍因物理掉罐 4/15。bowldrawer_p4_r1 4/7——"连续性优先，最近 <=0.18 m，首次获取才用 max-z"清除了碗<->炉圈振荡（seed 53 仍耗尽）。
- 依赖 API：get_observation、mask_to_world_points、segment_sam3_text_prompt、robot_cartesian_pos；上一帧可信位置通常来自 `world.query("state", ...)`（known 才更新），所以是否依赖 `world` 取决于记忆放哪：policy 内自带 `_TRACK`/`_ANCHOR`（bowldrawer_p6b、wine）则不依赖，但 13 个 cell 的做法是依赖 world 适配器。
- 子目标验证：exemplar 靠返回标签（`drawer_lowest`/`carried_near_hand`/`placed_not_found`）做日志，没有独立确认。应内建：(1) 候选与 track 的距离和第二名的间隔，间隔<margin 返回 None；(2) 身份确认用"效果"信号：举起后该实体 z 上升 >=0.06（spatialdrawer_p5_r1 L239-242），或夹爪宽度进入夹持带，而不是再跑一次 SAM3。
- 任务耦合：find_target 里 `z>0.08`、`near_plate<0.06`、`0.012<z`、`mean_row` 最大、`carried<0.16`、`freed<0.30` 全是该双碗场景；spatialbetween_p4n 的关系短语 "akita black bowl between the plate and the ramekin" 写死；wine 的 0.32/0.22；`MILK_GATE=0.20`（代码 L19）与 findings 所述 0.11 不一致，说明该值未被稳定校准。
- 参数化：radius_m 必须小于同类实例最小间距（可运行检查：`assert radius_m < min_pairwise_distance(all_instances)`，例如 soup 两罐相距 0.366 m、半径 0.13；半径超过间距就等于允许身份被盗）；排序轴 align_axes 要排除滑动轴（drawer_p6_r1 findings："只用 x 和 z，绝不用 y，y 是滑动轴"）；carried 模式的参照点 = 手 + 学到的搬运偏移。
- 风险：(1) bowldrawer_p6b_r1 L89-94 两个分支都 `chosen = pool[0]`，半径判断是空操作——它实际是"信任最近的远离夹爪的斑块"，不是已验证的半径门，不能当证据；(2) 半径过小则关联失忆：drawer_p3_r1 开抽屉后旋钮离开三旋钮列，一帧模糊后槽位永久 unknown；drawer_p4_r2b 的 0.09 m 窗口小于 0.2 m 的拉出量；半径要和已知运动（手位移、拉出量）联动；(3) 半径过大则身份被偷（objswapsoup_p5 的 0.18、spatialbetween_p4n 在搬运高度拒绝重定位）；(4) objtaskmilk/spatialtaskcookie 为噪声/过拟合嫌疑，仅作旁证；(5) 13 个 world 侧规则不在证据字节内，库是否带 world 关联模板是集成问题，需要另行确认。与 m4-r1 无冲突。
- 覆盖的失败类型：perception 4（身份误判）；对 articulated 30 只在"抽屉拉出后关联丢失使 done()/progress() 为 unknown"这一证据质量问题上有帮助（drawer_p6_r1、drawer_p4_r2b 的 reward 为 1.0 而评估为 unobserved），并不增加成功率；place/release、grasp、drop 需要 M4 原语。

### P5 mask+depth 的三维定位：稳健中心 / 顶面 / OBB / 最少点数（中强；估计器选择高度依赖物体与遮挡状态）
- 功能与接口草案：把一个 SAM3 mask 反投影成点云，经去背景/去远平面/最大簇修剪后，输出位置估计及其置信指标；中心估计器由调用方按物体类型选择，默认中位数；总是返回点数与离散度，不足时返回 None。
  ```python
  def localize_object(mask, depth, K, T, *, min_points: int = 30,
                      trim: str | None = None,            # None | "radius" | "z_far" | "percentile_box"
                      trim_param: float | None = None,    # m 或百分位
                      center: str = "median",             # "median" | "top_band" | "mid_extent" | "rim_ring"
                      top_band_frac: float = 0.4, top_pct: float = 95.0,
                      want_obb: bool = False) -> dict | None:
      """-> {"center":[x,y,z],"top_z","bottom_z","extent":[..],"n","spread","obb":{...}|None,"method"}"""
  ```
- 出处：最佳基线 = bowl_p5_r1，bundle 6c227a41，policy_sha12 b8a1c77002ba，`localize` L70-80（中位数中心 + p95 顶面 + OBB extent，点数>=100）。次选（特定估计器）：objswapsoup_p6_r1（0825bb17 / c6d120596c70）`_mask_entries` L83-92（取 z>=60 百分位的"罐盖椭圆"中位 xy，z 取 p92，抵消侧面遮挡）；mokastove_p5_r1（b27482c8 / fbd80c2692f0）`_summarize` L46-78（壶身轴 = 最高 1.2 cm 点的中位 xy，避开黑色把手）；spatialdrawer_p4n_r1（bb321f66 / 9fb314246c56）`_center_xy` L60-69（3/97 百分位中点）；stove_p4_r2（05662bdd / 174d84b05b3c）`_clean` L91-96（丢弃低于最高点 0.06 的远平面渗色）；stove_p3_r2（8aea273a / 09f9b95ed85a）`_geom` L62-122（分带 + Kasa 圆拟合求旋钮枢轴、p95 杆长）。
- 独立重写（按估计器计，正则筛后读函数确认）：中位数作中心 >=24 个 cell；p85-p98 顶面/高度 11 个；百分位盒中点或修剪盒 4 个（spatialdrawer_p4n、bowldrawerclose_p5、goaltask_p5:_robust_center L115-124 与 _bowl_center L152-165、goaltask_p6b）；OBB 5 个（bowldrawer_p4_r1b、bowl_p5、bowldrawer_p6、mokastove_p6b、objtaskmilk）；远平面/最大簇/半径修剪 5 个（bowl_p3:largest_cluster、bowldrawer_p4_r1:anchor_from refine 0.045、stove_p4_r2:_clean、objswapsoup_p4n:mask_stats 取离质心最近 80%、goaltask_p6b:_cluster eps=0.045）；顶面特征锚点 4 个（objswapsoup_p4n 顶 15%、objswapsoup_p6 罐盖、mokastove_p5 壶身轴、bowldrawer_p4_r1 `rim_top` 环）；圆/环拟合 3 个（bowl_p3:circle_center L50-69、bowldrawer_p4_r1、stove_p3_r2:_geom）。最少点数在 30/33 个 cell 里以字面量出现，取值 5/8/12/15/20/25/30/40/50/60/80/100/150，众数 20-40。
- 开发期证据（均 dev，findings）：objswapsoup_p6_r1 15/15（1 版）——侧面遮挡使整 mask 中位偏向相机，罐盖区域估计后 seed 51 起飞出；mokastove_p5_r1 15/15（1 版）——"壶身轴 + 偏航避让把手"在 15 个 seed 上保持，而质心/原始 plan_grasp 落在把手上抓空；bowldrawer_p4_r1 4/7——mask 中位偏离碗心 2-3 cm 使夹爪滑出薄边，"环心"修复翻转 seed 56；objswapsoup_p4n_r1 4/15——地板污染使中位偏 3-6 cm，改顶部 15% 锚点后 openness 回到 0.69-0.73；spatialbetween_p4n_r1 10/15（2 版）——均值被视角偏 2 cm，改 5/95 百分位轮廓中点；stove_p3_r2 6/6——远端像素抖动使 r_max 读成 0.068，改 p95 并拟合枢轴（seed 54/64）。反例见风险(1)。
- 依赖 API：mask_to_world_points、get_oriented_bounding_box_from_3d_points（返回 center/extent/R）；不需要 `world`。输出由 policy 写入 `world.update`。
- 子目标验证：无。exemplar 靠 world 预测反馈事后比对。应内建：返回 `spread` 与"两种估计器的差"（如 |median − top_band| 的 xy>0.03 标记 mask 被污染），点数不足明确返回 None。
- 任务耦合：z 参照 —— bowl_p4_r1b findings：pose_mat 世界系里桌面 z≈0（盘中位 −0.002、碗 0.018），**不是** API 文档里的 0.9；stove_p3_r2 里桌面 0.0105、盘面 0.0371-0.0398、杆顶 0.048 全是写死带；bowl_p5 里 `0.028` 区分碗沿与盘面；`RIM_OFFSET_FRAC=0.88`、`GRIP_BELOW_RIM=0.025` 属抓取侧，不属本卡。
- 参数化：min_points、trim、center、top_band_frac、table_z 作为参数；可运行前置检查：
  ```python
  pts = np.asarray(mask_to_world_points(mask, depth, K, T), float)
  assert len(pts) >= min_points and np.isfinite(pts).all()
  assert abs(np.percentile(pts[:, 2], 5) - table_z) < 0.05, "z 参照不是 pose_mat 世界系的桌面"
  ```
- 风险：(1) 估计器相互矛盾：bowl_p3_r1 findings 说"整圈拟合在 mask 与手臂像素合并时把中心甩偏几厘米"，最终只接受 <=0.02 的修正（L120），而 bowldrawer_p4_r1 的环心恰是翻转 seed 56 的修复——结论是估计器必须随状态（静止/夹持/合并）切换，不能固化一个；(2) 顶面/罐盖/壶身轴三种都只在一类物体上验证过，迁移到别的物体无证据；(3) 夹持中的碗被夹爪 mask 合并，中位数会"停滞"在偏高处（bowl_p4_r1b findings，seeds 61/62）；(4) objtaskmilk 的 OBB 朝向（ns，13/15 为噪声最大值）和 spatialtaskcookie 的 z_hi 不作证据；(5) plan_grasp 的确定化（m4-r1）与本卡无关，但抓取点由本卡的中心推出，位置偏差会被放大。
- 覆盖的失败类型：grasp 4 与 place/release 17 中由"位置估计偏 2-6 cm"导致的那部分（空抓 openness≈0.015、释放偏 3-4.5 cm）；不覆盖 open-loop 释放本身、碗靠抽屉壁、articulated 30（未点燃、抽屉未全开关）——这些需要 M4 原语。

### P6 遮挡处理：把"看不见"与"失败了"分开（中；子技术多、各自只有 2-5 个 cell）
- 功能与接口草案：当目标 mask 因夹爪/手臂/被搬运物遮挡而丢失或坍缩时，给出带状态的目标估计而不是 None 或垃圾值：fresh / stale(沿用最后一次可信观测) / occluded_holding(本体感觉显示正在夹持) / unknown；并提供"目标静止时冻结首次干净参考"。
  ```python
  def resolve_target_state(fresh: dict | None, *, last_fresh: dict | None, age_calls: int,
                           max_age_calls: int, holding: bool | None,          # 来自夹爪宽度，不来自视觉
                           plausible=None,                                    # callable(center)->bool，如 airborne 检查
                           ) -> dict:     # {"status","value","reason"}
  def frozen_reference(store: dict, key: str, observation: dict | None, *, clean: bool) -> dict | None:
      """静止目标(盘子/架子)只在 clean=True 的首次观测写入，之后读缓存。"""
  ```
- 出处：本组无单一最佳函数，按子技术各取一个：(a) 本体感觉兜底 = mokastove_p5_r1（b27482c8 / fbd80c2692f0）L245-246 `carried = (mask 点数>=30 且 zlo>plate_top+0.03) or (0.0<=frac<=0.85)`，15/15；(b) 冻结干净参考 = spatialbetween_p5_r1（df6cd13b / 19179c1ca0d4）`plate_ref` L58-63（读 world scene 的 `plate_reference`，缺失才退到实时盘子位置），15/15；(c) 未知不等于失败 = spatialdrawer_p5_r1（911d5f26 / 2c760350b2da）L230-250（三次重看；仅"确认低于 rest_z+0.02"才判失败，被遮挡但夹爪宽度在带内则继续并在盘子上方复核），15/15；(d) 双相机 = drawer_p4_r2b（7fa2f452 / 42f8166ea02f）`detect_values` L133-152，15/15；(e) 空中合理性 = bowl_p3_r1（e29f25c9 / 200401ef14cd）`airborne` L312-314/L331/L348-350，4/5。
- 独立重写（按子技术计，policy 字节内）：(a) 本体感觉替代丢失 mask：>=9 个 cell（mokastove_p5、spatialdrawer_p5、spatialdrawer_p4n、drawer_p6_r1 L84-86 宽度带 0.08-0.36、stove_p4_r1b、bowl_p4_r1b `pinch_open_min=0.04`、bowldrawer_p6 `HOLD_LO/HI`、drawer_p4_r2b `HOLD_OPEN=0.12`、objtaskmilk L380-383；这与抓取验证组重叠，本卡只负责"mask 丢失时不要判失败"这一半）；(b) 冻结参考 2 个字节内（spatialbetween_p5 L58-63、bowl_p5_r1 L116-125 用 `rest_position`）+ findings 自述 spatialbetween_p4n、bowl_p4_r1b；(c) 第二相机 3 个（drawer_p4_r2b、drawer_p5_r2 L79-91、objswapsoup_p5 `_can_candidates_wrist` L127-165）；(d) 先撤离再感知 3 个字节内（objtaskmilk L393-398、mokastove_p5 L278 之后感知、stove_p4_r2 findings 的"退到空桌面口袋"）+ findings 自述 bowldrawer_p6、goaltask_p6b（必须 `goto_home_joint_position()`，侧向撤离无效）；(e) 夹持期间跳过被遮挡实体的感知/沿用最后观测：bowldrawer_p4_r1b `perceive(handle=False)`、bowldrawer_p6b L83、wine_p4_r1 `_ANCHOR`、bowldrawer_p6 `need_bowl=False` 共 4 个；(f) 空中合理性守卫：bowl_p3_r1 一个。
- 开发期证据：bowl_p3_r1 4/5（0 版）——findings：手臂在盘子正上方时"bowl" mask 粘到盘子上、估计坍缩到盘面高度（z=0.0012，真值 0.19），airborne 守卫（需高于盘 0.06，否则重看一次，再不行用最后可信的手-碗偏移）翻转 seed 62，对健康 seed（59）无影响。spatialdrawer_p5_r1 15/15（2 版 [10,15]）——"遮挡但夹持"分支翻转 seed 63；放宽盘子下限到 800 px/0.50 使被碗压住的盘环碎片仍可解析。bowl_p5_r1 15/15——静止盘子以首次干净观测为准（压碗后的月牙 mask 中位偏 ~3 cm，实测 0.026）。spatialbetween_p5_r1 15/15（3 版 [5,5,15]）——plate_reference 使 foundation_calibration 在 15 个 v2 试验里全部 true_positive。drawer_p4_r2b 15/15——双相机使拉出把手被餐具架遮挡后仍 progress 0.97-1.0。mokastove_p5_r1：被举起的壶对 agentview 不可见，若仅凭 mask 会误判"未搬运"。
- 依赖 API：get_observation（`robot_cartesian_pos[7]` 夹爪开度、`robot0_eye_in_hand` 腕相机）、goto_home_joint_position / goto_pose（撤离）；"last_fresh"和冻结参考有的由 `world` 保存（spatialbetween_p5 读 `world.query("scene")["plate_reference"]`，bowl_p5 读 `rest_position`），有的在 policy 内用模块级字典（wine `_ANCHOR`、bowldrawer_p6b `_TRACK`）。
- 子目标验证：这是本卡的核心——exemplar 用夹爪宽度/开度等本体感觉信号，不用 mask。阈值各 cell 自定：开度 <=0.85 视为夹住（mokastove_p5）；宽度 0.08-0.36 视为夹把手（drawer_p6_r1）；空闭合 0.013-0.016 vs 夹到薄沿 0.096（bowl_p4_r1b）。
- 任务耦合：`plate_top+0.03`、`TABLE_Z=0.13`（objswapsoup_p5 罐子落到桌面判据）、`rest_z+0.06/0.02`、`airborne` 的 0.06 都是场景高度常量；撤离点 `(x-0.12, y, 0.42)` 写死在 objtaskmilk。
- 参数化：holding 带 (lo, hi)、max_age_calls、plausible 由调用方传入；可运行前置检查（防止把"遮挡"当"失败"）：
  ```python
  open_frac = float(np.asarray(get_observation()["robot_cartesian_pos"])[7])
  status = "occluded_holding" if (target is None and holding_lo <= open_frac <= holding_hi) else ("unknown" if target is None else "fresh")
  assert status != "failed" or confirmed_low  # 只有观测到低于静止高度才允许判失败
  ```
- 风险：(1) 沿用 stale 值有"位置过期"风险——bowl_p4_r1b 的中位数在夹爪与碗 mask 合并时会"停滞"在偏高处，使基于它的停止判据在 19 cm 高处提前释放（seeds 61/62 的剩余模式）；(2) 多数子技术的阈值来自单一场景，换物体需重标；(3) 遮挡处理放在 world 程序里的部分（如 bowldrawer_p6 的 pad 点邻近门、drawer_p6_r1 的 grasped 判定）本组看不到字节；(4) 撤离到 home 要消耗步数，bowldrawer_p6 有 step budget 超时前科（seed 57）；(5) objtaskmilk/spatialtaskcookie 的相关机制为噪声/过拟合嫌疑；(6) 与 m4-r1：撤离移动可用 goto_pose(max_step) 分段，不冲突，但本卡不重实现运动。
- 覆盖的失败类型：perception 4 中"遮挡致误判"，以及 place/release 17 里由"遮挡后估计坍缩"诱发的错误释放（bowl_p3 的 30 cm 高处释放）；不覆盖物理上的开环释放、碗靠抽屉壁。


## 本组小结
- 强（>=3 个独立 cell 且有 12+/15 或 15/15 程序）：**P2 候选收集与门控**（32/33 个 cell 各自重写，exemplar bowl_p5_r1 15/15；但阈值在 cell 间差一个数量级，必须全部参数化，不能带默认常量入库）；**P3 候选验证与抗劫持**中的"z/xy 带 + 形状/高度/颜色 + 与别的 mask 重叠"部分（19/33，stove_p4_r2 与 bowldrawer_p6_r1 均 15/15）；**P5 三维定位**的基线部分（median + 顶面百分位 + 最少点数，>=24 个 cell；bowl_p5_r1 15/15）。
- 概念强但字节证据薄：**P4 身份关联**（policy 字节内 9 个 cell，world 侧自述 13 个，合计 22/33；spatialdrawer_p4n 与 wine 15/15）。"首次获取用几何、之后用最近已知半径内、否则 unknown、绝不按分数最高"在 >=10 个 cell 的 findings 里独立收敛；但 13 个只有 worker 自述，world 程序字节本组没读到——若新库不携带 world 关联模板，P4 只能做 policy 侧记忆版。
- 偏弱/可疑：**P3 里的"夹爪邻近排除"子项**（policy 字节内仅 bowldrawer_p6b 10/12 与 stove_p4_r2 两个 cell，口径还不一致：手掌体 vs pad 点；且 bowldrawer_p4_r1 有反例"手指扶杆时杆就在指尖"）；**P6 遮挡处理**（5 个子技术各 2-5 个 cell，阈值全是场景常量，其中 (a) 与抓取验证组重叠）；**P1 任务语言解析**（只有 3 个 cell 真解析，23 个只打印；其中 2 个还是同一任务；建议降格成"写死名词与 task_language 一致性断言"）。
- 噪声/过拟合嫌疑来源：objtaskmilk_p6b_r1（13/15 为噪声最大值）和 spatialtaskcookie_p6b_r1（后期修补过拟合 15 个 dev 布局）的 `_mask_cloud` / `fresh_rim_top` / OBB 朝向 / MILK_GATE 只作旁证，未计入强弱判断；mokastove_p6b_r1（3/15，77 行压缩风格）、bowldrawerclose_p6b_r1（3/15）、goaltask_p6b_r1（5/15）的感知细节仅当"劫持发生过"的证据，不当"有效实现"。
- 估计器之间互相矛盾，必须保留切换而非固化：bowl_p3_r1 说整圈拟合在手臂像素并入时把中心甩偏、回退到中位数，bowldrawer_p4_r1 则靠环心翻转 seed 56；bowldrawer_p4_r1 要求"手指扶杆时不要排除指尖附近"，bowldrawer_p6/p6b 则要求"排除手附近"。库需要显式的状态参数（free / holding / merged），否则 silent-misfit。
- 缺口：(1) 无"提示词是否落地"的统一预检（`_swap` 套件里名字提示分数 <=0.10 而形状词 0.9+，仅 objswapsoup_p4n 做过诊断）；(2) segment_sam3_point_prompt 只在 stove_p3_r1（L189）与 spatialbetween_p4n（`_point_mask` L118-141，用建模的搬运位置回点找回被手遮住的碗）出现，未形成 >=3 的候选；(3) 预测窗口/API 调用预算（每个 mask_to_world_points 耗一个调用号，历史上 600-2211 次/试验）属 world 契约，不在本组；(4) 14 个 cell 用宽泛 `except Exception` 吞 SAM3 异常，仅 2 个对 `world.Unsupported` 透传，库函数需统一规则；(5) 颜色身份规则（R-B、blue_frac）只在 object_swap 罐子上验证，不可泛化。
- 与 m4-r1（goto_pose max_step 分段、solve_ik 回显、plan_grasp 确定化）的关系：本组 6 张卡都是感知层，没有一张只是在重新实现 m4-r1 的功能，无需标注 superseded；P6 的"撤离再感知"只调用 goto_pose，不重复实现分段。
- 对 58 个冻结失败的覆盖上限：本组只直接对应 perception 4（SAM3 "handle" 误命中）；对 articulated 30、place/release 17、grasp 4、drop 2 只在"位置估计偏 2-6 cm / 遮挡后估计坍缩"这一入口上间接有益，不能替代 M4 原语。感知层的主要历史价值体现在 findings 里"初始运行 seeds 51-53 的共同失败"，而这些在冻结程序里已被修掉，所以在冻结失败分类里看起来占比很小。


# 候选卡片：motion_grasp 组（M1...）

> 证据范围：仅 development（seed 51-65）的 policy 代码、findings.md、task_analysis.md、development_state.json。未读取任何 heldout/sealed 路径。
> 状态：完成（6 张卡）。模型：Sonnet 5.5；全部 33 份 policy_path 已读/grep；未发现任何禁用 API（grep `env.handle.env|sim.data|body_xpos|set_joint_qpos|parsed_problem|_eval_predicate|_step_once|get_site_xpos|obj_body_id|sim.model|sim.forward` 在 33 份代码中 0 命中）。

## 卡片目录
- M1 分段移动 / glide（带到位校验）
- M2 IK 可达性守卫（工作区 clip / z 地板 / 姿态替换 / 实测到达）
- M3 手姿四元数 helper（顶抓 + yaw / 正面水平接近）
- M4 边缘捏取 rim-pinch（碗/宽物）
- M5 GraspNet plan_grasp 接受带 + 几何回退
- M6 抓取验证（宽度闸门 + 抬升证据 + 遮挡容忍）与换策略重试

### M1 分段移动 / glide（带到位校验）
- 功能与接口草案：把一次长 `goto_pose` 拆成每段 <=步长的直线 hop，必要时从实测 TCP 起算并在末点补发一次，再用实测手位姿核对是否到位。**m4-r1 的 `goto_pose(..., max_step=)` 已实现"拆段 + 末点补发"，本卡只保留"到位校验 + 失败回退"部分。**
  ```python
  def goto_verified(pos, quat, max_step=0.05, tol_xy=0.02, tol_z=0.02, retries=1, tcp_offset=0.10) -> (bool, np.ndarray):
      """goto_pose(pos, quat, max_step=max_step); 然后 get_observation()['robot_cartesian_pos'][:3]
      与 pos - R(quat)@[0,0,-tcp_offset] 比较（手体 = 指尖 + 0.10*手 z 轴，姿态感知）；超差则重发 retries 次。
      返回 (arrived, 实测手位置)。"""
  ```
- 出处（best）：spatialdrawer_p4n_r1，bundle bb321f66，policy_sha12 9fb314246c56，`_staged_move` L198-206 + `move_staged` L209-214 + `goto_arrive` L217-236（STEP=0.09 在 L40；`_hand_expected` L72-77）。
  runner-up：bowldrawer_p6_r1（a53bcde9 / 445b5d6dc115）`_goto` L156-161（`interpolate_segment(cur,p,MAX_STEP=0.075)`，`_arrive` L169-173 以实测 pad 点验证）；wine_p4_r1（98e17580 / c725f1c003ee）`goto_waypoints` L195-212（step=0.10，用 `interpolate_segment`）。
- 独立重写：9 个 cell（统计法：对 33 份 policy_path 全文 grep `interpolate_segment|MAX_STEP|STEP_MAX|STEP *=|chunk|np.ceil(.*/ *step` 后逐个读函数体，只算各自从头写的拆段逻辑）：
  spatialdrawer_p4n:`_staged_move`；bowldrawer_p4_r1:`goto_chain`(chunk 0.09, 最多 5 段 + 末点 settle)；wine_p4_r1:`goto_waypoints`(0.10)；goaltaskbowldrawer_p5_r1:`_goto_path`(0.075)；bowldrawer_p5_r1:`reach`(闭环伺服 <=0.07/步)；bowldrawer_p6_r1:`_goto`(0.075)；objtaskmilk_p6b:`goto`(MAX_STEP 0.055，入口一次重读 TCP)；spatialtaskcookie_p6b:`glide`(STEP_MAX 0.07，每点 `settle` 重发 2 次)；drawer_p6_r1: inline L92-97 `PULL_STEP=0.045` + 双发 `goto_pose(gp,QH)` L74-75。
  边缘（算半个）：objswapsoup_p5_r1 inline L340-380 三段 transport hop。其余 24 个 cell 单次 `goto_pose` 直达。
  步长分布 0.045-0.10 m，**各 cell 独立得到的阈值一致落在 0.07-0.09 m 以下**，与 API 文档"pre-grasp 偏移 <=0.075"一致。
- 开发期证据（development seeds 51-65）：
  - spatialdrawer_p4n 15/15，2 个版本 [13,15]；findings："Staged 7-9 cm goto sub-moves + measured-pose verification with retry (v15/v21) eliminated all short-stop failures"，原先 seeds 52/54/56/57 空抓由此消除。
  - bowldrawer_p6_r1 15/15，4 个版本 [14,14,13,15]；findings：seed 51 手臂停在目标前 ~0.1 m，改"<=0.075 m 分段 + 实测姿态 pad 变换 + 到位检查"后 gripper 行 residual 从 0.10 降到 ~0.01。
  - wine_p4_r1 15/15（0 版本扫描）；bowldrawer_p5_r1 9/9（仅测 9 seed，9 个版本 [6,7,6,2,0,8,8,0,9]）；drawer_p6_r1 15/15（1 版本）："blocking gotos over ~0.1 m stop short ~0.11 m"，PULL_STEP 0.045 后 seeds 51/55 全开，15/15。
  - bowldrawer_p4_r1 4/7：findings L6 "single jumps 0.2-0.3 m stopped 3-7 cm short"，链 <=9 cm + settle 后"judged gripper rows matched 13/13"。
  - objtaskmilk_p6b 13/15（诊断：噪声最大值，复跑 8/15）、spatialtaskcookie_p6b 12/15（疑似过拟合）：同样含此 helper，仅作旁证，不作为强度依据。
- 依赖 API：`goto_pose`（可选 `max_step`）、`get_observation`、`interpolate_segment`；到位校验若用 `world.query("state", entity="gripper", ...)`（spatialdrawer_p4n 做法）则依赖 `world`，**纯版本改用 `get_observation()["robot_cartesian_pos"]` 即可，不需要 `world`**。
- 子目标验证：有。spatialdrawer_p4n.`goto_arrive` 用实测手位置对比 `_hand_expected(p,quat)`，xy_err<=ARRIVE_TOL(0.06) 且 z_err<=Z_TOL(0.09) 才返回 True（L35-36，容差很松，只能抓到严重停滞，抓不到 1-3 cm 的短停），否则 `record_decision("retry_move")` 重发；bowldrawer_p6_r1.`_arrive` 用 `|pad - _CMD| < 0.05`。objtaskmilk/spatialtaskcookie 的 `goto` **不**验证（只靠下一次感知）。
- 任务耦合：很弱。写死的只有数值（STEP 0.09 / MAX_STEP 0.075 / 0.055 / ARRIVE_TOL，TCP 偏移 0.10-0.107）。`goto_checked`（drawer_p3_r1 L134-160）里的 `0.107` 偏移与别处 0.10 不一致，需统一为参数。
- 参数化：`max_step`、`tol_xy/tol_z`、`tcp_offset` 作参数；前置检查：
  `assert np.isfinite(pos).all() and pos[0] <= 0.745` 之前先用 M2 的 `clip_report`；`d = np.linalg.norm(pos - tcp_now); n = ceil(d/max_step)`，并打印 n 供后续核对。
- 风险：(1) 已被 m4-r1 取代：自写 `interpolate_segment` 循环应删，只留校验；(2) objtaskmilk 注明"每 hop 重新读实测位置再解 IK 会发散"，而 spatialdrawer_p4n/bowldrawer_p5_r1 反而是逐步闭环，两种证据**互相矛盾**，库里默认开环拆段 + 末端校验，不要逐 hop 重解；(3) 偏移符号：spatialdrawer_p4n findings L7 记录模型曾把 hand 当 pos-0.1z（错），应为 pos+0.1*hand_z（姿态感知），非顶抓姿态时必须用实测四元数；(4) 4000 步/回合预算：每次 hop 约消耗 15-100 步（goaltaskbowldrawer_p5_r1 L39），过细步长会耗尽预算。
- 覆盖的失败类型：grasp(4)、drop(2) 中因"停在目标前"导致的空抓；articulated(30) 中拉抽屉/把手对位偏差的前置原因（bowldrawer_p4_r1、drawer_p6_r1）。**不能**解决：IK 姿态被替换（见 M2）、到达了但夹爪接触物理（M4/M6）。

### M2 IK 可达性守卫（工作区 clip / z 地板 / 姿态替换 / 实测到达）
- 功能与接口草案：在下发 `goto_pose` 之前把目标夹进 `solve_ik` 的真实工作区并报告被改动了什么；下发之后用实测手位姿判断"是否真的到了请求的位置和朝向"，不到则触发 M1 的重发或换策略。**m4-r1 已让 `solve_ik` 在 clip/姿态替换时打印 `[solve_ik] ...`，本卡里"发现 clip"的部分被取代，只保留"事前夹取 + 事后对比 + 失败分支"。**
  ```python
  WS_LO = (-0.1, -0.5, 0.005); WS_HI = (0.75, 0.5, 0.9)      # solve_ik 源码里的 IK_WORKSPACE
  def reach_guard(pos, margin_x=0.005, ws_lo=WS_LO, ws_hi=WS_HI) -> (np.ndarray, list[str]):
      """返回 (夹取后的目标, 被改动轴的说明)；x 上限取 ws_hi[0]-margin_x（=0.745），不抛异常。"""
  def reached(pos_cmd, quat_cmd, tol_pos=0.02, tol_ang_deg=15, tcp_offset=0.10) -> dict:
      """get_observation()['robot_cartesian_pos'] -> {pos_err, ang_err_deg, ok}，姿态对比用四元数点积 |q1·q2|。"""
  ```
- 出处（best）：spatialdrawer_p5_r1，bundle 911d5f26，policy_sha12 2c760350b2da，`GRASP_X_MAX = 0.745` L112、`_grasp_target` L115-121（对 CGN 点与几何回退点都 `min(x, 0.745)`）。
  runner-up：goaltaskbowldrawer_p6b_r1（620d696b / ddcf786449ca）`cgn_now` L238 `np.clip(pos, [-0.1,-0.5,0.004], [0.75,0.5,0.9])`；bowl_p3_r1（e29f25c9 / 200401ef14cd）`_ZMIN = 0.008` L27（z 地板，注释引用 libero_reduced.py）。
- 独立重写：**只有 2 个 cell 有显式 x/y/z 夹取**（spatialdrawer_p5_r1:`_grasp_target`；goaltaskbowldrawer_p6b_r1: inline L238），另有 1 个 z 地板常量（bowl_p3_r1:L27）。统计法：对 33 份 policy_path grep `(min|clip|max)\(.*0\.7[0-9]*|X_MAX|0\.745|0\.75|_ZMIN|IK_WORKSPACE`，其余命中均为感知滤波（bowl_p3_r1 L45 `0.15<x<0.78`、bowldrawerclose_p6b_r1 L70-75 `_drawer_in_workspace` `0.20<x<0.78`），不是对运动指令的夹取，不计入。
  相关但不同的"到达/姿态"类经验（非夹取，各自独立）：stove_p3_r1 inline L360-364 "stall rule"（实测手 z-0.1 与上次比 <4 mm 即判停滞）；drawer_p3_r1:`goto_checked` L134-160（有界修正 ±0.04，err<0.04 通过）；drawer_p6_r1 L74-75 双发同一 `goto_pose`；bowldrawer_p6_r1 L238 起 `goto_home` 重新播种 IK。
- 开发期证据：spatialdrawer_p5_r1 15/15（2 版本 [10,15]），findings L8：CGN 重规划给出 x+0.053 的角点、"IK silently clips x>0.75, so the arm landed somewhere else"，改用 `GRASP_X_MAX` 与"仅接受 +y 侧窄带"后 seed 51 翻转并保持 15/15（翻转功劳同时来自 M5 的接受带，**无法单独归因于 x 夹取**）。bowl_p3_r1 4/5：z 地板问题（"CGN z 0.000-0.008, solve_ik clip floor 0.005, fingers bury"）对应 seeds 52/53/54/56 的 burner 误抓，真正的修复是感知（SAM3 bowl 掩码）而非 z 夹取。drawer_p6_r1 15/15：双发 goto 使 seed 51 到位残差 -0.228→-0.009 m，宽度 0.001→0.179。goaltaskbowldrawer_p6b_r1 仅 5/15（7 版本），不作强度依据。
- 依赖 API：`solve_ik`/`goto_pose`/`get_observation`；`reach_guard` 为纯数值，**不需要 `world`**。
- 子目标验证：exemplar（spatialdrawer_p5_r1）只做事前夹取，**不**检查是否到达；到达靠主循环里的 gripper-level 预测反馈（依赖 `world`）。stove_p3_r1/drawer_p3_r1 的 stall/err 判据才是真正的"事后自检"。要建：`reached()` 在每次 contact 前调用，ok=False 时不得 `close_gripper()`。
- 任务耦合：`0.745` 来自 API 源码而非任务（可由 `WS_HI` 参数承载）；goaltaskbowldrawer_p6b 把 z 写死 `0.004`，stove_p3_r1 的 `0.1` TCP 偏移、`0.004` 停滞阈值、`0.03` 降幅是 stove 专用，需参数化。
- 参数化：`ws_lo/ws_hi`、`margin_x`、`tcp_offset`、`tol_*`；前置检查：
  `p, notes = reach_guard(target); assert not notes or print("[reach_guard]", notes) is None`（任何被夹取的轴都应当打印并交由调用方决定改目标还是改姿态）。
- 风险：(1) **证据薄**：只有 1 个 cell 真正落地，且它的翻转与 M5 混在一起；(2) m4-r1 打印已把"静默 clip"变成可见信息，库里再夹一次属于冗余；(3) 夹取只改位置，**不会**阻止 IK 把姿态换成 top-down/45-tilt/side（m4-r1 打印 "orientation substituted"），仍需调用方读日志；(4) 双发 goto/goto_home 重播种是 pyroki 暖启动的经验性绕法（drawer_p6_r1、bowldrawer_p6_r1、drawer_p6b_r1、bowldrawer_p6b_r1 各自独立发现，4 个 cell），换 IK 实现后可能无效，也可能掩盖姿态替换。
- 覆盖的失败类型：grasp(4)里"目标超出工作区/低于 z 地板"导致的静默偏移；articulated(30) 中把手位于远端（x≈0.69 以上）时的空夹。**不能**解决：姿态不可达（顶抓够不到低矮把手，见 drawer_p6b_r1 findings L4）——这要靠换姿态（M3）或 M4 原语。

### M3 手姿四元数 helper（顶抓 + yaw / 正面水平接近）
- 功能与接口草案：由"接近方向 + 指对闭合方向"构造 wxyz 手姿，替代各 cell 手写的矩阵/常量；覆盖顶抓+世界 z 偏航（碗沿、旋钮、纸盒短轴）与水平正面接近（抽屉把手竖向夹）。
  ```python
  TOPDOWN = np.array([0.0, 1.0, 0.0, 0.0])      # wxyz，手 z 朝 -world z，手指闭合轴 = world y
  def hand_quat(approach, close_axis) -> np.ndarray:        # 右手系正交化；det<0 时翻 x；返回 w>=0 的 wxyz
  def topdown_yaw_quat(close_dir_xy) -> np.ndarray:         # 近似 Rz(psi)·TOPDOWN，使手指闭合方向 = close_dir_xy
  def front_quat(normal_xy, up=(0,0,1)) -> np.ndarray:      # 手 z = -normal，手 y = up（垫片竖向开合）
  ```
- 出处（best）：objtaskmilk_p6b_r1（4da1e6f0 / b6812ed55560）`_topdown_yaw_quat` L251-286（OBB 最短水平轴 → psi=atan2(d0,-d1)，纯 yaw，无 roll）；注意该 cell 为噪声最大值嫌疑，**helper 本身的数学以 stove_p3_r2 `_q_down` L38-53 + `_palm_down` L56-59 为准（带符号教训）**。
  runner-up：bowldrawer_p6b_r1（efc0c704 / 0ae62a9dfd88）`_quat(approach, sep)` L17-25（通用 Gram-Schmidt 版，最接近目标接口）；bowldrawer_p6_r1（a53bcde9 / 445b5d6dc115）`_yaw_quat` L31-37 + `_quat_from_n` L40-48。
- 独立重写：**14 个 cell**（顶抓 yaw 9 + 水平正面 6，bowldrawer_p6_r1 两类都写了，去重后 14；统计法：grep `quat|Rot|rotation_matrix_to_quaternion|cos\(.*/ *2` 后逐个读定义，只算各自手写数学者，常量 `TOPDOWN=[0,1,0,0]` 不算）。
  顶抓+yaw（9）：stove_p3_r1:`_level_quat` L73-77；stove_p3_r2:`_q_down` L38-53；stove_p4_r2:`_rz_quat`/`_qmul` L34-48；stove_p4_r1b:`q_yaw` L50-51；mokastove_p5_r1:`_quat_rotz` L104-108；mokastove_p6b_r1:`_yaw_q` L37-38；objtaskmilk_p6b:`_topdown_yaw_quat` L251-286；bowldrawer_p6_r1:`_yaw_quat` L31-37；spatialbetween_p4n:`plan_top_down_grasp` 内 inline `quat=[0,cos(θ/2),sin(θ/2),0]`（findings L53，policy L212-300）。
  水平/正面（6）：drawer_p3_r1:`grasp_quat` L110-131；bowldrawer_p4_r1:`horizontal_grasp_quat` L55-67；bowldrawer_p6_r1:`_quat_from_n` L40-48；drawer_p6b_r1:`horiz_quat` L28-34；goaltaskbowldrawer_p6b_r1:`front_quat` L11-18；bowldrawer_p6b_r1:`_quat` L17-25。
- 开发期证据（dev passed/tested；版本数）：stove_p3_r1 10/10；stove_p3_r2 6/6；stove_p4_r2 15/15；stove_p4_r1b 15/15；mokastove_p5_r1 15/15（1 版本）；mokastove_p6b_r1 3/15（5 版本，不计）；objtaskmilk_p6b 13/15（9 版本，噪声最大值）；bowldrawer_p6_r1 15/15（4 版本）；drawer_p3_r1 2/2；bowldrawer_p4_r1 4/7；drawer_p6b_r1 15/15（1 版本）；goaltaskbowldrawer_p6b 5/15；bowldrawer_p6b_r1 10/12（6 版本）；spatialbetween_p4n 10/15（2 版本 [4,10]）。
  worker 明确指出"姿态/偏航写对才翻转"的证据：stove_p3_r2 `_q_down` docstring "Seed-52 attempt 3: pads closed edge-on... psi taken with the wrong sign"；spatialbetween_p4n findings L55 "yaw parity proven by seed 56 (radial yaw closed empty at qpos 0.016)"（通过 51/52/54/56/57/58/60/62/63/65）；spatialbetween_p5_r1 findings L5 "with top-down quaternion [0,1,0,0] the finger pads close along world ±y, so the rim wall was not centered"（v2 5/15→15/15）；objtaskmilk findings/代码 L257-262 "a rolled hand-matrix orientation made IK drift ~0.12 m in xy; rotating ONLY about world z keeps IK well conditioned"；bowldrawer_p6_r1 findings L7 "yawed top-down quats triggered IK orientation fallbacks"。
- 依赖 API：`rotation_matrix_to_quaternion`、`get_oriented_bounding_box_from_3d_points`（仅 objtaskmilk 版）、可选 `scipy.spatial.transform.Rotation`；**不需要 `world`**。
- 子目标验证：无。helper 只产出四元数；是否真的"手指闭合轴对了"只能靠事后夹爪宽度（见 M6）。要建：构造后自检 `R=quat→matrix; assert abs(R[:,2]·approach)>0.99 and abs(R[:,1]·close_axis)>0.99`，并在首次 contact 前用 `reached()`（M2）核对实测姿态没有被 IK 替换。
- 任务耦合：低——除了 stove 系的 `psi - pi/2` 相位（取决于是否以 TOPDOWN 为基）、drawer 系把"up=world z / 抽屉法线沿 ±y"写死、bowldrawer_p6b 的 `DT=_quat([0,-0.5,-1],[0,.87,-.5])` 斜向顶抓是该抽屉专用常量。各 cell 的 psi 符号约定互不相同（stove_p3_r2 用 `psi-π/2`，stove_p4_r1b 直接用 `psi`，bowldrawer_p6_r1 用 `atan2(ax0,-ax1)`），合并时必须统一一个约定并用 round-trip 单测固定。
- 参数化：`approach`、`close_axis` 作参数；前置检查：
  `q=topdown_yaw_quat((1,0)); R=Rot.from_quat([q[1],q[2],q[3],q[0]]).as_matrix(); assert np.allclose(R[:,2],[0,0,-1],atol=1e-6) and np.allclose(abs(R[:,1]@[1,0,0]),1,atol=1e-6)`。
- 风险：(1) 数学本身确定性，**这是本组少数无随机性的 helper**，但"符号约定"是反复出错点（上面三处翻转证据）；(2) 与 m4-r1 无冲突（API 未提供四元数工具）；(3) 斜向/滚转手姿会触发 IK 姿态替换或漂移（objtaskmilk、bowldrawer_p6_r1 各自观察到），库默认只给纯 yaw 顶抓和水平正面两类；(4) objtaskmilk_p6b 的使用方是噪声最大值（13/15 复跑 8/15），不要拿它的通过率为 helper 背书，数学正确性另有 stove_* 与 spatialbetween_* 的独立证据。
- 覆盖的失败类型：grasp(4)中"手指闭合轴与物体宽度方向不匹配"的空夹；articulated(30)里抽屉把手夹取（水平正面接近）。**不能**解决：夹取点本身错（M4/M5）、把手实际不可达。

### M4 边缘捏取 rim-pinch（碗/比夹爪行程宽的物体）
- 功能与接口草案：当物体开口/宽度 > 夹爪行程（~0.08 m）时，不去中心夹，而是顶抓下降使一指在壁内、一指在壁外，把壁夹在指垫之间；生成捏取点列表（按尝试顺序）并给出下降深度。
  ```python
  def rim_pinch_points(cloud_xyz, finger_axis_xy=(0.0, 1.0), wall_inset=0.005, bite=0.020,
                       sides=("+","-"), min_clear=0.018) -> list[dict]:
      """cloud_xyz: 目标掩码的世界点云(N,3)。沿手指闭合轴取点云两端极值 e±，壁点 = 极值向内收 wall_inset；
      TCP z = rim_top(点云 90-95 分位 z) - bite，且 >= min_clear（指尖不碰桌）。
      返回 [{side, tcp(3,), quat}]，quat 由 M3 保证闭合轴 = finger_axis_xy。"""
  ```
- 出处（best）：bowl_p5_r1，bundle 6c227a41，policy_sha12 b8a1c77002ba，`grasp_and_lift` L85-113（`RIM_OFFSET_FRAC=0.88`、`GRIP_BELOW_RIM=0.025`，常量 L30-36）。选它因为最简单且 dev 15/15、1 个版本即 15/15。
  runner-up：spatialbetween_p5_r1（df6cd13b / 19179c1ca0d4）`grasp_and_lift` L145-186，常量 `RIM_DIR=(0,-1)`、`RIM_OFFSET=0.052`、`GRIP_Z=-0.010`（L20-26）；bowldrawerclose_p5_r1（ad3d57bf / 5e85ddc31a2f）`_grasp_bowl` L172-191（按点云极值取 4 个基数壁点 + 抬起核对，**最接近可复用接口**）。
- 独立重写：**13 个 cell**（统计法：grep `rim|pinch|wall` 命中 >=3 次的 cell 逐个读抓取函数，剔除只提到 rim 但用中心抓取者）：
  bowl_p5_r1(`grasp_and_lift`)、spatialbetween_p5_r1(`grasp_and_lift`)、spatialdrawer_p5_r1(`_grasp_target`+`RIM_OFFSET`)、spatialdrawer_p4n_r1(`rim_pinch` L243-262)、bowldrawerclose_p5_r1(`_grasp_bowl`)、bowl_p4_r1b(`pinch_candidates` L158-175 + `try_grasp`)、bowl_p3_r1(`plan_rim_grasp` L138-173)、bowldrawer_p4_r1b(`grasp_bowl` L416-435，`0.7*ry`)、bowldrawer_p6b_r1(前沿 rim 中位数 L96-104 + 深度 z=0.012 L221)、bowldrawer_p6_r1(径向 yaw + 抓感知位置 L271-277，`BOWL_RIM_R` 只声明未使用)、spatialbetween_p4n_r1(`plan_top_down_grasp` 合成回退 L277-298)、goaltaskbowldrawer_p5_r1(`rim_of`/`bowl_grasp_lift` L432-455，仅作 cheese 的后备)、spatialtaskcookie_p6b_r1(`fresh_rim_top` L127-140 + rim 冠顶)。
- 开发期证据（development seeds 51-65；dev passed/tested；版本数；worker 报告的翻转）：
  bowl_p5_r1 15/15（1 版本）：seed 51 因偏移系数 0.75→0.88 翻转（findings L10/L59："0.75-factor failed on all seeds (no wall contact); 0.88 lifted the bowl on 15/15"）；
  spatialbetween_p5_r1 15/15（3 版本 [5,5,15]）：偏移改到手指闭合轴(y)上 + 取实测 `RIM_OFFSET` 后 5/15→15/15，翻转 52/53/54/56/58/59/61/62/63/65，加深到 mid-wall 翻转 seed 51；
  spatialdrawer_p5_r1 15/15（[10,15]）：CGN 仅在 +y 侧窄带内接受，几何回退 `center+(0,0.045,-0.01)`，夹持宽度 0.09-0.19 vs 空夹 0.013；
  spatialdrawer_p4n_r1 15/15（[13,15]）：probe 标定 `(centre_x+0.35*half_x, +0.02, rim_top-0.028)`，seeds 55/56/57/59/60/61 宽度 0.088-0.15 vs 空夹 0.014；
  bowldrawerclose_p5_r1 6/15（6 版本）：4 基数壁点循环 + 抬起核对，"made grasp+place reliable on all 15 dev seeds"，但程序整体败于抽屉关闭（不是抓取）；
  bowldrawer_p6b_r1 10/12（6 版本）：深前沿捏取 z=0.012 + 偏移重试 {0,±0.012}，v2 2/15→v3 10/15，残余 seeds 56/59/62/64/65 "intermittently fails to hold the wide metal bowl"；
  bowl_p4_r1b 13/15、bowl_p3_r1 4/5、spatialbetween_p4n_r1 10/15（[4,10]）、bowldrawer_p6_r1 15/15（[14,14,13,15]）：均含 rim-pinch 但翻转归因混合；
  spatialtaskcookie_p6b 12/15（6 版本 [9,10,11,8,12,7]）：诊断已指出摩擦阈值边缘、后续修补过拟合 15 个 dev 布局（早期 v0/v1 泛化更好），**视为噪声/过拟合嫌疑**，不作证据。
- 依赖 API：`get_observation`、`segment_sam3_text_prompt`、`mask_to_world_points`、`goto_pose`、`close_gripper/open_gripper`；多数 exemplar 经 `world.query("state", entity="bowl", fact="position"/"top"/"extent"/"rim_radius")` 读取（bowl_p5_r1、spatialbetween_p5_r1、bowl_p4_r1b 依赖 `world`）；bowldrawerclose_p5_r1/bowldrawer_p4_r1b/bowldrawer_p6b_r1 直接用点云，**可不依赖 `world`**。
- 子目标验证：有，但各不相同——bowl_p5_r1 靠 `world` 的 `attached` 事实；spatialbetween_p5_r1 靠 `decide("lift","lift_ok")` 的预测反馈；bowldrawerclose_p5_r1 靠"抬起后 bowl z>0.11 或 xy 位移>0.10"（独立于夹爪宽度）；bowl_p4_r1b 靠捏后 `gripper_open>=0.04`（rim 0.096 vs 空夹 0.013-0.016）+ 抬起 `bowl z >= pre_z+0.05`；spatialdrawer_p4n 靠宽度 `>=0.035`。统一做法见 M6。
- 任务耦合：**高**。偏移规则互不一致（见下表）；还写死了 y 为手指轴（`Q=[0,1,0,0]`）、`RIM_Z=0.033/RIM_R=0.048`（goaltaskbowldrawer_p5_r1）、`0.012` 绝对 z（bowldrawer_p6b_r1，按 +0.05 前沿）、`GRIP_Z=-0.010` 相对 median（spatialbetween_p5_r1）、`half_x` 的 `0.35` 系数（spatialdrawer_p4n，抽屉里的碗专用）。
  | cell | 壁点规则 | 深度规则 | 手指轴 | dev |
  |---|---|---|---|---|
  | bowl_p5_r1 | y+0.88*ext_y/2 | ztop-0.025 | y | 15/15 |
  | spatialbetween_p5_r1 | y-0.052 | median-0.010 | y | 15/15 |
  | spatialdrawer_p5_r1 | y+0.045(CGN带 0.015<dy<0.08) | center-0.010 | y | 15/15 |
  | spatialdrawer_p4n_r1 | x+0.35*hx, y+0.02 | rim_top-0.028 | y | 15/15 |
  | bowldrawerclose_p5_r1 | 点云 hi/lo 极值∓0.006 (4 向) | hi_z-0.012, >=0.018 | y/x | 6/15 |
  | bowl_p4_r1b | y±rim_radius | rim-0.015 | y | 13/15 |
  | bowldrawer_p4_r1b | y±max(0.7*ry,0.03) | ztop-0.020 | y | 7/15 |
  | spatialbetween_p4n_r1 | 径向 0.75*r(<=0.055), yaw 交替 | rim-0.018 | 径向 | 10/15 |
  共同点只有：沿手指闭合轴取壁、指尖下探到壁中高度（rim 下 1-3 cm），且系数 0.75 在 bowl_p5_r1 上全败而 0.75 在 spatialbetween_p4n 上通过，说明系数取决于"半径如何测"，不可移植。
- 参数化：壁点必须由**实测点云极值**沿 `finger_axis` 给出（bowldrawerclose_p5_r1 做法），不要用半径系数；`bite`、`min_clear`、`wall_inset`、侧顺序作参数；前置检查：
  `assert 0.08 < (cloud[:,fa].max()-cloud[:,fa].min()) and rim_top - bite >= min_clear`（物体宽于夹爪行程才用 rim 捏取，否则直接中心夹）。
- 风险：(1) 一半 exemplar 靠 seed-51 单点标定系数，换碗/换尺度大概率错位（silent misfit：夹爪闭合在碗内，宽度 0.012-0.016，被 M6 抓到）；(2) 摩擦边缘：spatialtaskcookie、mokastove_p6b（摩卡壶本体夹持，同类摩擦边缘）、bowldrawerclose_p6b、bowldrawer_p6b 都报告"夹持到了但运输中滑脱"，rim-pinch 本身是摩擦阈值边缘的策略；(3) 与 M3 耦合：指轴符号错会让壁落在指垫之外（spatialbetween_p5_r1 v0/v1）；(4) m4-r1 确定性 plan_grasp 不影响本卡（本卡不依赖 GraspNet），但 bowl_p3_r1/spatialdrawer_p5_r1/spatialbetween_p4n_r1 的 CGN 分支见 M5。
- 覆盖的失败类型：grasp(4)（空夹）、drop(2)（滑脱前的壁深度/侧选择）、articulated 里"碗放入抽屉"的前置抓取；place/release(17) 中碗悬挂偏移（如 bowl_p5_r1 的 hand-vs-bowl 偏移）只被部分覆盖，需要 M4-primitive（按悬挂偏移对位释放）而非本卡。

### M5 GraspNet `plan_grasp` 接受带 + 几何回退
- 功能与接口草案：对一个已验证、无遮挡的目标掩码调用一次 `plan_grasp`，用"近垂直 + 距新鲜物体中心的位置带 + 高度夹取"筛选；不合格或异常则退回确定性的几何抓取点。
  ```python
  def plan_grasp_gated(obs, mask, center_xyz, band_xy, band_z, vertical_thresholds=(0.8, 0.6),
                       z_clamp=(None, None), x_max=0.745, min_mask_px=300, fallback=None) -> dict:
      """返回 {pose:(pos,quat), source:'cgn'|'fallback'|'none', reason:str, score}。
      1) mask 像素 < min_mask_px 或 gripper 在视野内 -> 直接 fallback（不调用 plan_grasp，见风险）；
      2) try plan_grasp -> select_top_down_grasp(vt) 依次放宽；3) |xy-center|<=band_xy 且 |z-center_z|<=band_z；
      4) 位置 x 夹到 x_max（M2），z 夹到 z_clamp；5) 否则 fallback(center)。"""
  ```
- 出处（best）：spatialdrawer_p5_r1，bundle 911d5f26，policy_sha12 2c760350b2da，`_plan_grasp` L124-160 + `_grasp_target` L115-121（vt 0.75→0.55；接受带 `0.015<dy<0.08 且 |dx|<0.03 且 |dz|<0.05`；`GRASP_X_MAX=0.745`；失败回 `center+RIM_OFFSET`）。
  runner-up：spatialbetween_p4n_r1（a73646d0 / 4062e3444079）`plan_top_down_grasp` L212-298（vt 默认 0.8→0.5，再自检 `alignment=1-2w²>=0.6`、`dxy<=min(1.35*max(r,0.04),0.066)`、自由侧、z=max(min(rim-0.018,z+0.03),center+0.015)；合成 rim 回退）；bowl_p3_r1（e29f25c9 / 200401ef14cd）`plan_rim_grasp` L138-173（vt 0.9；`0.015<d<0.62*foot`；z 夹到 `[rim-0.012, rim+0.008]`；回退"离手最近的顶部 20% 点 rim_z-0.006"）。
- 独立重写：**5 个 cell**（`calls_plan_grasp=True` 的全部 cell；统计法：cell_index 的 calls_plan_grasp + grep `plan_grasp\(`，其余 28 个程序全部**不**调用 plan_grasp），5 套互不相同的接受规则：
  bowl_p3_r1:`plan_rim_grasp`；spatialbetween_p4n_r1:`plan_top_down_grasp`；spatialdrawer_p5_r1:`_plan_grasp`；objtaskmilk_p6b_r1:`compute_grasp` L204-243（vt 0.6；放宽到"得分最高且离中心<0.20"；拒绝 `xy>0.10` 或 z 越出 `[z_lo-0.06, z_hi+0.12]`；保留 CGN 的腕部 roll）；goaltaskbowldrawer_p6b_r1:`cgn_now` L223-244（仅 argmax 得分，无接受带，`np.clip` 到工作区）。
- 开发期证据：spatialdrawer_p5_r1 15/15（[10,15]），findings L8/L11：无约束重规划挑到远角点 (x+0.053) 失败，加窄带后 seed 51 翻转并保持 15/15，夹持宽度 0.09-0.19 vs 空夹 0.013；spatialbetween_p4n_r1 10/15（[4,10]），findings L5："loose 0.5 vertical threshold produced a ~146-deg-rolled wrist descending below the rim that shoved the bowl up to 12 cm; a grasp point accepted at 7.6 cm off center closed in empty air"，改后通过 51/52/54/56/57/58/60/62/63/65，仍败 53/55/59/61/64（findings L71 称为 scene-run variance）；bowl_p3_r1 4/5：vt 0.9，findings seed 52 "~50 deg tilted CGN candidate raked the bowl sideways on every lift attempt"；objtaskmilk_p6b 13/15（9 版本；诊断：非确定性 + 噪声最大值，复跑 8/15）：CGN 取向是唯一抬起纸盒的抓取，findings L47-48；goaltaskbowldrawer_p6b 5/15（7 版本）：新鲜 CGN 位姿才出现 >=0.30 的夹持，但被 ~15% 断言故障拖累。
  反向证据：mokastove_p5_r1（15/15）与 wine_p4_r1（15/15）、bowl_p5_r1（15/15）等高分程序**完全不调用 plan_grasp**；mokastove findings L5/L11 记录原版 CGN 因壶柄主导掩码而指向壶柄，最终改为"点云顶部 1.2 cm 的 XY 中位数 + 手腕 yaw 避开壶柄"，并写明"gate CGN's answer to <=3 cm of the axis estimate"。
- 依赖 API：`plan_grasp`、`select_top_down_grasp`、`decompose_transform`、`mask_to_world_points`、`get_observation`；接受带本身是纯数值，**不需要 `world`**（bowl_p3_r1/spatialbetween_p4n 的几何依赖 `world` 里的 silhouette 中心，可由调用方传入 `center_xyz`）。
- 子目标验证：无（只产出一个抓取位姿）。必须由 M6 在 `close_gripper` 之后核对夹爪宽度与物体抬升；`source='fallback'` 时应在日志标明，方便区分是 CGN 还是几何点失败。
- 任务耦合：高。接受带数字全部来自单物体标定：`0.015<dy<0.08`（碗 +y 侧）、`0.066`/`1.35*radius`（碗半径）、`0.62*foot`、纸盒 `0.10`/`0.20` 与 z 窗口；goaltaskbowldrawer_p6b 的 `GOODPAIR` 是 dev-51 四个"好夹持"四元数/偏移对加噪声重采样（`np.random.default_rng()` 未播种）——**明显过拟合且不确定，禁止入库**。
- 参数化：中心、`band_xy/band_z`、`vertical_thresholds`、`z_clamp`、`min_mask_px`、`fallback` 作参数；前置检查：
  `assert mask.sum() >= min_mask_px and not gripper_in_view(obs)`（gripper 在视野内的最简判据：`np.linalg.norm(tcp_xy - center_xy) > 0.25`，即 objtaskmilk 的"先撤到远处再重新感知"）。
- 风险：(1) **公共 API 异常会让整次 trial 记为 world fault，且 try/except 拦不住**（objtaskmilk findings L6/L42-44；goaltaskbowldrawer_p6b findings L7：`AssertionError: No grasp candidates found` 约 15% 出现，最终做法是 live 代码里完全去掉 plan_grasp）——所以本 helper 的价值主要是"不调用"的门控；(2) m4-r1 使同一请求返回同一结果，但**输入变化仍会变**（api-revision-m4.md），"确定性"只消除复跑噪声，不消除"该位姿是否正确"，objtaskmilk 的 13/15 噪声最大值教训仍适用；(3) 接受带是 15 个 dev 布局上拟合的，spatialdrawer_p5_r1 的 +y 侧带无法移植到别的碗朝向；(4) 与 M4 重叠：bowl_p3_r1/spatialdrawer_p5_r1 的 CGN 分支本质上是"CGN 当 rim 点候选"，M4 的点云极值法已在 15/15 程序里覆盖，CGN 不是必需。
- 覆盖的失败类型：grasp(4)（挑到错位姿/空夹）；无法覆盖 perception(4)（掩码选错物体 → CGN 也跟着错，如 bowl_p3_r1 的 burner 误抓）。**建议库里只放"门控 + 几何回退"，把 CGN 当可选候选**。

### M6 抓取验证（夹爪宽度闸门 + 抬升证据 + 遮挡容忍）与换策略重试
- 功能与接口草案：`close_gripper` 之后先用实测夹爪宽度判"空夹/夹到东西"，抬起后再用目标自身的 z 上升判"真的带起来了"，遮挡时区分"未知"与"确认没起来"；失败则撤到物体上方、张开、按候选阶梯换一个抓取点/朝向再试，次数有界。
  ```python
  def verify_grasp(rest_z, width_band=(0.025, 0.60), rise_min=0.06, low_tol=0.02, looks=3,
                   read_target_z=..., read_width=...) -> dict:
      """返回 {status:'held_confirmed'|'held_blind'|'air'|'no_rise', width, z}。
      air: width <= width_band[0]；held_confirmed: 目标 z > rest_z+rise_min；
      no_rise(确认): 目标可见且 z < rest_z+low_tol；held_blind: 宽度在带内但目标看不见(未知≠确认没起来)，
      调用方必须在下一个可见位置(如放置点上方)复核。"""
  def grasp_ladder(candidates, attempt_fn, verify_fn, max_attempts=3, retreat_z=0.12) -> dict:
      """按候选顺序(不同点/侧/朝向)尝试；每次失败：撤到 rim/物体上方 retreat_z -> open_gripper -> 重新感知 -> 下一个候选。"""
  ```
- 出处（best）：spatialdrawer_p5_r1，bundle 911d5f26，policy_sha12 2c760350b2da，`main` 内抓取循环 L179-262（`AIR_W=0.025`/`CLOSED_W=0.5`；`_held` L105-109；rise = `rest_z+0.06`，`confirmed_low = z < rest_z+0.02`，3 次重看；`carry_blind_proceed`）。
  runner-up：bowl_p4_r1b（eacd33639106 / bundle 023ae89c）`try_grasp` L178-209 + `verify_lift` L212-229 + 侧交替循环 L323-345（`sides=["-y","+y","-y"]`，`pinch_open_min=0.04`，`lift_rise_min=0.05`，失败后先 `release()` 再回 home）；bowldrawerclose_p5_r1（ad3d57bf / 5e85ddc31a2f）`_grasp_bowl` L172-191（4 个壁点阶梯，抬起后 `z>0.11 or xy 位移>0.10`）；drawer_p4_r2b（7fa2f452 / 42f8166ea02f）`attempt_grasp` L177-208（`REACH_TRIES` 7 档沿法线/横向/高度阶梯，`HOLD_OPEN=0.12`）。
- 独立重写（统计法：grep `gripper_width|openness|aperture|robot_cartesian_pos\)?\[7\]|_held|_holds|verify|lift` 后逐个读判据代码）：
  宽度/开度闸门 **12 个 cell**：spatialdrawer_p5_r1(0.025/0.5)、spatialdrawer_p4n(PINCH_MIN_W 0.035)、bowldrawer_p6_r1(HOLD 0.025-0.60)、objswapsoup_p6_r1(0.2-0.97)、bowl_p4_r1b(0.04)、drawer_p4_r2b(0.12)、drawer_p6_r1(0.08-0.36)、drawer_p6b_r1(0.05-0.70, L122)、stove_p4_r1b(0.05)、goaltaskbowldrawer_p6b(>=0.25)、mokastove_p5_r1(`_aperture`)、objtaskmilk_p6b(aperture<0.85)；
  抬升/上升验证 **10 个 cell**：spatialdrawer_p5_r1、bowl_p4_r1b、bowldrawerclose_p5_r1、objswapsoup_p6_r1(`>grasp_z+0.05`)、bowldrawer_p6b_r1(`bowl z>0.06` L232)、spatialbetween_p4n/p5(`airborne = z>rest+0.06`)、bowl_p3_r1(`airborne` L312)、spatialtaskcookie_p6b(`carry_check` L191-199)、goaltaskbowldrawer_p5_r1(`w2[2]>0.08` L447)、bowl_p5_r1(`world attached`)；
  候选阶梯式重试 **8 个 cell**：bowldrawerclose_p5_r1(4 壁点)、bowldrawer_p6b_r1(`dyy∈{0,-0.012,0.012}` L223)、spatialdrawer_p4n(3 个 spot L249)、drawer_p4_r2b(7 档)、bowl_p4_r1b(侧交替)、stove_p4_r1b(2 套指轴假设+更深 L147-148)、spatialbetween_p4n(yaw 交替+自由侧)、goaltaskbowldrawer_p6b(TD 夹持 3 个中心偏移，findings L38-40)。同策略重试（只重新锚定）：spatialdrawer_p5_r1、objswapsoup_p6_r1、mokastove_p5_r1（空夹后再降 2.5 cm 重夹）。
- 开发期证据（dev passed/tested；版本数；worker 报告的翻转）：
  spatialdrawer_p5_r1 15/15（[10,15]）：seed 63 "arm fully occludes the carried bowl at hover while jaws hold it (width 0.185)"，旧 z-rise 验证把"未知"当"没起来"，改容忍后保持 15/15（findings L6/L13/L49-55）；
  spatialdrawer_p4n 15/15（[13,15]）：改"宽度>=0.035 判携带"后"killed the false 'carried' positives from association drift"（findings L15）；
  bowldrawer_p6_r1 15/15（[14,14,13,15]）：命中带标定 bar 0.12-0.18 / 碗沿 0.04-0.09 / 空夹 <=0.01，findings L11："Using a 0.08 m 'width' band mis-classified good grasps as air closes"（单位混淆）；
  objswapsoup_p6_r1 15/15（[15]）：阈值 `w<0.7` 把真实 0.77-0.79 的罐子判成空夹，改 `[0.2,0.97]` + 抬升 >5 cm 后 15/15（findings L6/L11/L34/L41）；
  drawer_p4_r2b 15/15（[15]）：开度 >=0.12 + 7 档阶梯"first pass at seed 52 attempt 3"（空夹 0.014-0.019，夹到 0.20+）；
  bowl_p4_r1b 13/15；bowldrawerclose_p5_r1 6/15（抓取/放置在 15 个 dev seed 上稳定，失败全在抽屉关闭，findings L9/L39）；bowldrawer_p6b_r1 10/12；goaltaskbowldrawer_p6b 5/15。
  宽度语义表（同一通道 `robot_cartesian_pos[7]` 归一化 0-1，约 gap/8 cm）：空夹 <=0.01-0.02；碗沿 0.04-0.19（各碗不同）；抽屉把手 0.12-0.25（>0.6 = 撞到面板，drawer_p5_r2 findings L44）；纸盒 0.3-0.55；金属罐 0.77-0.79；摩卡壶 0.4-0.8。
- 依赖 API：`get_observation()["robot_cartesian_pos"][7]`（宽度）、`goto_pose`、`open_gripper/close_gripper`、`segment_sam3_text_prompt`+`mask_to_world_points`（目标 z）；多数 exemplar 通过 `world.query("state", entity="gripper", fact="width"/"gripper_width"/"openness", ...)` 读取，**因而依赖 `world`**；纯观测版（bowldrawerclose_p5_r1、mokastove_p5_r1 `_aperture`）不依赖。
- 子目标验证：这张卡**本身就是**子目标验证。exemplar（spatialdrawer_p5_r1）双信号：宽度闸门(是否夹到东西) + 目标 z 上升(是否带起)；bowldrawerclose_p5_r1 明确"do NOT trust gripper width alone"；objswapsoup_p6_r1 findings L41 "the lift test is object-level evidence — never trust the gripper width alone"。
- 任务耦合：宽度带与物体绑定（上表）；`rest_z` 取自首次感知；`HELD_Z=0.11`(bowldrawerclose_p5_r1)、`>0.08`(goaltaskbowldrawer_p5_r1)、`>0.06`(bowldrawer_p6b_r1) 是绝对高度，只在 kitchen 桌面 z≈0 的碗上成立；阶梯偏移数字(±0.012、REACH_TRIES、sides)全是单任务标定。
- 参数化：`width_band`、`rise_min`、`rest_z`、`retreat_z`、`max_attempts`、候选列表由调用方给；前置检查：
  `w = obs["robot_cartesian_pos"][7]; assert 0.0 <= w <= 1.05`（确认读的是归一化宽度而非米）；以及 `assert width_band[0] < width_band[1]`。
- 风险：(1) **宽度带无法跨物体**，必须由调用方在首次成功夹持时记录，或按物体类型给默认带；(2) 单位混淆是反复出错点（bowldrawer_p6_r1 L11、objswapsoup_p6_r1 L6，米 vs 归一化）；(3) **反例，勿照抄**：mokastove_p5_r1 `carried = mask_ok or (0.0 <= frac <= 0.85)`（L246）把 0.0-0.2 的空夹也判为"携带"，注释却写"0.0-0.2 closed-on-air"；该程序 15/15 是因为抓取点另有保证，不是因为验证正确；(4) `held_blind` 放行会让假阳性进入运输，必须有第二次复核（spatialdrawer_p5_r1 在放置点上方复核），否则会掉落；(5) 4000 步预算：bowldrawerclose_p6b findings L8 "retry loops on already-failing seeds convert stalls into crashes"，所以 `max_attempts<=3`；(6) 与 m4-r1 无冲突，但 `goto_pose(max_step=)` 会让每次尝试多耗步数。
- 覆盖的失败类型：grasp(4)（空夹被发现、换点重试）、drop(2)（带起后掉落的早期发现）、articulated(30) 中"把手没夹住就开始拉"。**不能**覆盖：release/place(17)（开环释放、碗倚抽屉壁）与 articulated 的"炉子没点亮/抽屉没完全开合"——需要 M4 原语（闭环释放、闭合到位检测），不是抓取验证。

## 本组小结
- 强（>=3 个独立 cell 且含 dev 15/15 程序）：**M3 手姿四元数**（14 个 cell，stove_p4_r2/stove_p4_r1b/mokastove_p5_r1/bowldrawer_p6_r1/drawer_p6b_r1 均 15/15；数学确定，风险只在符号约定要统一，三处翻转证据独立）、**M6 抓取验证 + 阶梯重试**（宽度闸门 12 cell、抬升验证 10 cell、阶梯 8 cell；spatialdrawer_p5/p4n、bowldrawer_p6_r1、objswapsoup_p6_r1、drawer_p4_r2b 均 15/15；宽度带必须按物体传参，勿用 mokastove_p5_r1 的 `0.0<=frac<=0.85`）。
- 强但需瘦身：**M1 分段移动**（9 个 cell，4 个 15/15 程序：spatialdrawer_p4n、bowldrawer_p6_r1、wine_p4_r1、drawer_p6_r1）——各自独立得到 0.045-0.10 m 的步长；**已被 m4-r1 `goto_pose(max_step=)` 取代，仅保留"实测到位校验 + 重发"**；**M4 rim-pinch**（13 个 cell，bowl_p5_r1/spatialbetween_p5_r1/spatialdrawer_p5_r1/spatialdrawer_p4n_r1 15/15）——共同点是"沿手指轴取壁、下探到壁中高度"，偏移系数互相矛盾（0.75 全败 vs 0.88/0.052/0.045/0.35·hx 各自通过），库里必须用点云极值而非系数。
- 弱/中：**M5 plan_grasp 接受带**（5 个 cell，仅 spatialdrawer_p5_r1 15/15）；28/33 的程序（含几乎所有高分程序）根本不调用 plan_grasp，且公共 API 异常会令整次 trial 记 world fault（try/except 拦不住），建议库只放"门控 + 几何回退"，CGN 作可选。**M2 IK 守卫**仅 1-2 个 cell 真正落地（spatialdrawer_p5_r1 的 `GRASP_X_MAX=0.745`），且 m4-r1 已打印 clip/姿态替换，弱。
- 噪声/过拟合嫌疑（不应作为证据）：objtaskmilk_p6b（13/15 为噪声最大值，复跑 8/15；其 `goto`、`_topdown_yaw_quat`、`compute_grasp` 只作旁证）、spatialtaskcookie_p6b（`glide`/rim 冠顶；摩擦阈值边缘，后修补过拟合 dev 布局）、goaltaskbowldrawer_p6b（`GOODPAIR` 把 dev-51 四个好位姿加噪声重采样，`default_rng()` 未播种，**禁止入库**）。
- 互相矛盾的证据（合并前必须裁决）：(a) objtaskmilk 称"逐 hop 重读实测位姿重解 IK 会发散"，spatialdrawer_p4n/bowldrawer_p5_r1 却是逐步闭环有效；(b) rim 偏移系数（见 M4 表）；(c) 宽度通道的"空夹"读数在 mokastove_p5_r1 里自相矛盾。
- 缺口：release/place(17) 与 articulated(30) 不在本组——本组所有卡都停在"抓住并带起"；缺少"抓住后沿悬挂偏移闭环对位释放"（bowl_p5_r1、spatialbetween_p5_r1 里各有一份，属 place 组）；缺少 friction-marginal 抓取（摩卡壶、碗沿）运输中滑脱的有效对策，三个 cell 都 BLOCKED；`select_top_down_grasp` 默认阈值 0.8 与各 cell 0.5-0.9 的取值缺乏统一依据。
- 无任何 cell 的代码命中禁用 API 正则（33 份全查）；`world` 依赖：M1 校验版、M3、M2、M4（点云版）、M5 门控均可脱离 `world`，M6 与多数 exemplar 的宽度/抬升读取经 `world.query`，库版需改为直接读 `get_observation()`。



# 候选技能卡：place_release 组（CC_TASK_15，只读盘点）

> 状态：5 张卡已完成。证据仅限开发期（seeds 51-65）；未打开任何 heldout/sealed 路径。
> 计数方法：对 33 个 policy_path 用正则（offset/carry/open_gripper/terminated/nudge/airborne 等）逐文件扫描，再逐处阅读命中块确认；"独立重写"= 不同 cell 各自写出的实现（同一 task 的不同 cell 也分别计，另注 task 数）。
> 路径简写：SIM=/mnt/home/gewang/experiments；行号取自各 cell 的 policy_path（development/repair/seed_X/attempt_Y/code.py）。

### R1 带携带偏移补偿的搬运与释放（carry-offset release）
- 功能与接口草案：抬起后用新鲜观测量出"物体中心 - 手"偏移 off，搬运到目标上方后再量一次（预释放），把手瞄在 `goal - off`，使物体（而非手）落在目标中心；偏移不可测时回退到上一次可信偏移并限幅。
  ```python
  def release_over_target(obj_prompt, goal_xy, goal_surface_z, *, read_obj, read_hand,
                          tcp_offset_z=0.10, obj_clearance=0.035, max_off_xy=0.09,
                          max_corr_xy=0.025, min_airborne=0.06, fallback_off=None,
                          quat=TOPDOWN, remeasure=True) -> dict   # {released, off, hand_xy, reason}
  ```
  纯算术部分（`tcp = goal - off + [0,0,clearance] - tcp_offset`）可抽成无 world 依赖的函数，读状态部分通过 `read_obj/read_hand` 注入。
- 出处：
  - 最佳：bowl_p5_r1，bundle 6c227a41，policy_sha12 b8a1c77002ba，`carry_and_place` L116-156（取偏移 L132-143，瞄准 L144-147，释放+退+复看 L149-156）。
  - 备选 1：spatialbetween_p5_r1，bundle df6cd13b，sha12 19179c1ca0d4，`grasp_and_lift` L145-186（抬起后量偏移，L175 / 遮挡时用 CARRY_EST L27-31）+ `place` L198-234（预释放重量偏移 L211-217，MAX_SHIFT=0.09 守卫 L222-229）。
  - 备选 2：bowl_p3_r1，bundle e29f25c9，sha12 200401ef14cd，main 内联 L293-385（闭环对齐 4 轮、`airborne` 守卫 L312-314、悬空释放 L380-385）。
- 独立重写：12 个 cell 实测偏移（13 个含一个常数版），覆盖 8 个 task。逐个列：
  bowl_p3_r1:inline L298-385；bowl_p4_r1b:`current_offset_or_default`+`carry_and_place` L149-155/L232-297；bowl_p5_r1:`carry_and_place` L116-156；spatialbetween_p4n_r1:inline L408-491；spatialbetween_p5_r1:`place` L198-234；spatialdrawer_p5_r1:inline L292-322；spatialtaskcookie_p6b_r1:inline L342-380；objtaskmilk_p6b_r1:inline L431-487；bowldrawerclose_p5_r1:inline L230-254；bowldrawerclose_p6b_r1:inline L214-229；goaltaskbowldrawer_p5_r1:inline L396-418（`off3 = cb - hn`，L363 默认 [0,0,-0.11]）；mokastove_p6b_r1:inline L75。
  常数版：spatialdrawer_p4n_r1 inline L347-348（`hang_x,hang_y = 0.025, 0.085` 写死，不测量）。仅 z 向：mokastove_p5_r1 L270-272（由抓取几何推 hang_z），wine_p4_r1 L228-249/L363（carry_len）。
  同一 task 的不同 cell：put_the_bowl_on_the_plate 3 个（bowl_p3/p4_r1b/p5），spatialbetween 2 个，bowldrawerclose 2 个。
- 开发期证据（dev passed/tested；versions_swept；该 helper 被 worker 记为翻转的 seeds）：
  - bowl_p5_r1 15/15；1 版；findings："Placement offset compensation ... seeds 51-65 all 1.000 with v0 bytes"，repair-1 的 seed 51 因手对准盘心而偏 7 cm（未补偿）。
  - spatialbetween_p5_r1 15/15；3 版 [5,5,15]；预释放重测偏移使 seed 62 由漂移 (-0.035,-0.02) 转为合格；10 个 seed 的翻转与"抓取轴修正"同版发生，无法单独归因。
  - spatialbetween_p4n_r1 10/15；2 版 [4,10]；放置标定（冻结目标+2.5 cm 限幅修正+推正）使 v18b→v19 翻转 51/57/58/60。
  - spatialdrawer_p5_r1 15/15；2 版 [10,15]；findings 未把偏移列为翻转原因（翻转来自身份关联/抓取点）。
  - spatialdrawer_p4n_r1 15/15；2 版 [13,15]；固定 +2.5 cm 预补偿使 63 翻转，v26 翻转 56/57（与推正合并）。
  - bowl_p3_r1 4/5；0 版；悬空释放+减偏移翻转 55/56，57 靠中位数居中，62 靠 airborne 守卫。
  - bowl_p4_r1b 13/15；0 版；逐步下放中重量偏移解决 seed 51 repair-1 被推 7 cm；61/62 仍因掩码合并失败。
  - spatialtaskcookie_p6b_r1 12/15；6 版 [9,10,11,8,12,7]；闭环放置 v0 9/15→v4 12/15，63(v1)、65(v4) 翻转。
  - objtaskmilk_p6b_r1 13/15；9 版 [9,1,8,2,8,13,6,7,4]；偏移补偿列入 findings 但无 seed 归因。
  - bowldrawerclose_p5_r1 6/15；6 版；bowldrawerclose_p6b_r1 3/15；7 版；goaltaskbowldrawer_p5_r1 13/15；4 版；mokastove_p6b_r1 3/15；5 版；这四个均未被 findings 记为翻转点。
- 依赖 API：get_observation、segment_sam3_text_prompt、mask_to_world_points、goto_pose、open_gripper；读 `bowl/hand` 位置全部经 `world.query("state")`（bowl_p5/p3/spatial*/taskcookie 等）。**依赖 `world`**：物体位置是 world 的关联结果，被遮挡时可能是"携带模型预测"（手+旧偏移），此时量出的偏移是循环的。
- 子目标验证：部分。bowl_p5 释放后退到 `pt+PLACE_ABOVE` 再 `localize()`，判 `hypot(b2-p) < 0.05`（L152-156），但返回值只打印，不触发重试；spatialbetween_p5 `placement_ok`（L189-195）用冻结盘心 + 夹爪张开 >0.5 + xy<0.035，失败时仅在"碗仍在桌面"才重抓（L261-271）。需要内建：偏移取值前检查物体观测是否"新鲜且在空中"（z > 目标面 + min_airborne），否则重观测一次，仍不行才回退。
- 任务耦合：
  - 物体名与掩码：`"bowl"`,`"plate"`（bowl_p5 L61）；`RIM_OFFSET_FRAC=0.88`、`BOWL_REST_CLEAR=0.024`、`PLACE_ABOVE=0.16`（bowl_p5 L33-36）。
  - 常数：`CARRY_EST=(0,0,-0.13)`（spatialbetween_p5 L31，来自该碗的实测）、`MAX_SHIFT=0.09`、`hang_x,hang_y=0.025,0.085`（spatialdrawer_p4n L347）、`_cap(...,0.065/0.025/0.020)`（spatialbetween_p4n L431/452/463）。
  - 手到指尖：bowl_p5 写死 `-0.1`（L147），spatialdrawer_p5 用 `TCP_UP=0.107`（L19）：同一量两个值，必须参数化。
- 参数化：目标点、目标面高度、物体放置间隙 `obj_clearance`（碗在盘上 0.024-0.05 均有 15/15 先例）、`tcp_offset_z`、偏移限幅 `max_off_xy`、单次修正上限、fallback 偏移都应为参数。必须成立的前置检查：
  ```python
  def offset_ok(obj_xyz, hand_xyz, goal_z, min_airborne=0.06, max_off_xy=0.09):
      off = np.asarray(obj_xyz) - np.asarray(hand_xyz)
      return obj_xyz[2] > goal_z + min_airborne and np.hypot(off[0], off[1]) <= max_off_xy
  ```
- 风险：
  - 观测偏差：spatialbetween_p4n findings 指出可见点均值沿相机倾角偏后约 2 cm，改用 5/95 分位中点；bowl_p4_r1b 指出掩码在手下降时与夹爪合并使中位数"平台化"。
  - 常数版（spatialdrawer_p4n 的 hang_y=0.085）是对该布局的过拟合式预补偿，不应入库。
  - objtaskmilk（诊断：GraspNet 不确定，13/15 为噪声最大值，复跑 8/15）与 spatialtaskcookie（rim-pinch 在摩擦阈值上，后期修补过拟合 15 个布局，早期 v0/v1 更泛化）的证据只作旁证。
  - 与 m4-r1：`goto_pose(max_step=...)` 与确定性 plan_grasp 不重叠；但 `solve_ik` 会把 x>0.75、z<0.005 的目标裁剪（api-revision-m4.md），补偿后的 `goal - off` 可能越界，需打印/检查裁剪行。
- 覆盖的失败类型：place/release（开环释放、偏移 3-7 cm 超出 3 cm On 容差）。覆盖不了：抓取点本身造成的大偏移或掉落（grasp/drop），夹爪下物体长期看不见时的估计（需 M4 手腕相机原语）。

### R2 释放前下降高度与逐步下放（lower-before-release）
- 功能与接口草案：在目标上方先在巡航高度做横向对齐（单次限幅），再垂直下放到"物体底部离支撑面 clearance"，然后张爪；高度以"被携带物体中心/底部相对观测支撑面"表达，而不是绝对 z；可选逐步下放并以"指令 TCP 高度"为停止条件。
  ```python
  def lower_and_release(goal_xy, support_z, off_xyz, *, obj_clearance, tcp_offset_z=0.10,
                        cruise_z=None, step=None, max_corr_xy=0.025, min_tcp_z=0.008,
                        quat=TOPDOWN, open_after=True) -> dict  # {tcp_z, released, reason}
  ```
- 出处：
  - 最佳：bowl_p5_r1（bundle 6c227a41，sha12 b8a1c77002ba）L143-150：`release_hand_z = pt + BOWL_REST_CLEAR - off_z`，先到 `pt+PLACE_ABOVE` 再下放再张爪再回升；15/15。
  - 备选 1：bowl_p4_r1b（023ae89c / eacd33639106）`carry_and_place` L251-278 逐步下放（`descend_step=0.035`、`place_bowl_low_stop=0.055`、`stall_eps=0.005`）。
  - 备选 2：spatialbetween_p4n_r1（a73646d0 / 4062e3444079）L448-472：巡航高度横向修正（≤2.5 cm）与下放分开，原因见 L449-450 注释（seed-60 把横移并入下降会拽动碗）。
- 独立重写：10 个 cell 各写了自己的"释放高度"。取值如下（本表即汇总，高度含义因 cell 而异）：

  | cell | 目标面 | 释放参考 | 值 | dev |
  |---|---|---|---|---|
  | bowl_p5_r1 L36,143 | 盘 | 碗中心高于盘面 | 0.024 | 15/15 |
  | bowl_p3_r1 L383 | 盘 | 碗中心高于盘面（悬空） | 0.10 | 4/5 |
  | bowl_p4_r1b L39,266 | 盘 | 逐步下放至碗-盘高差 ≤ | 0.055 | 13/15 |
  | spatialbetween_p5_r1 L26,216-217 | 盘 | 碗中心高于盘面；TCP ≥ 盘面+0.005 | 0.035 | 15/15 |
  | spatialbetween_p4n_r1 L467 | 盘 | rest_z + off_z + 0.006 | 0.006 | 10/15 |
  | spatialdrawer_p5_r1 L21,316 | 盘 | 碗中心高于盘面 | 0.05 | 15/15 |
  | spatialdrawer_p4n_r1 L349 | 盘 | 手指令 z = 盘 z + | 0.085 | 15/15 |
  | spatialtaskcookie_p6b_r1 L20,347 | 盘 | 指尖高于盘面 | 0.040 | 12/15 |
  | mokastove_p5_r1 L270-273 | 炉 | 炉面+半高+0.02+hang_z-0.10 | 0.02 | 15/15 |
  | wine_p4_r1 L35,345,363 | 酒架 | 瓶底沉入架顶线以下 | -0.03 | 15/15 |

  计数方法：对 33 个文件 grep `open_gripper` 周边的高度表达式并逐处阅读；容器/抽屉类归 R3，不在此表。
- 开发期证据（该 helper 的翻转 seeds）：
  - bowl_p3_r1：悬空释放 + 减偏移翻转 55/56；seed 54 证据"任何接触高度运动都会推盘 1.8 cm 并弹碗 4.5 cm"。
  - bowl_p4_r1b：开环下放把倾斜的碗低缘压进盘并推 6-8 cm（seed 51 repair-1）；逐步下放翻转 51；残留故障：掩码与夹爪合并使观测中位数"平台化"，停止判据在约 19 cm 高处误触发，碗从高处掉落（seeds 61/62）；findings 记录的后继方案是"以指令 TCP 高度对比遮挡前盘面基准停止"，在 seed 61 上验证通过但因 p4 覆盖规则未能冻结。
  - wine_p4_r1：沉入 3 cm 后张爪，翻转 seed 61，其余 seed 此后 15/15（同一字节）。
  - spatialbetween_p4n_r1：释放下降量缩到 +6 mm，与限幅修正、推正同批使 5→10。
  - versions_swept：bowl_p5 1，bowl_p3 0，bowl_p4_r1b 0，spatialbetween_p5 3，spatialbetween_p4n 2，spatialdrawer_p5 2，spatialdrawer_p4n 2，taskcookie 6，mokastove_p5 1，wine 0。
- 依赖 API：goto_pose（`z_approach` 参数 0.0 为主）、open_gripper、get_observation；支撑面高度来自 `world.query("state", ... "top"/"rest_position")`。**依赖 `world`**（支撑面与物体高度的读取）；下放算术本身不依赖。
- 子目标验证：弱。bowl_p4_r1b 在下放循环里用观测的碗高度判停（有掩码合并缺陷，见上）；其余 cell 不确认"已降到位"，只在张爪后重观测。需内建：以"指令 TCP 高度 vs 观测支撑面基准"作停止条件，另用 `robot_cartesian_pos[:3]` 核对实际到达（m4 文档建议）。
- 任务耦合：`BOWL_REST_CLEAR=0.024`、`PLACE_CLEAR=0.035/0.05/0.040`（碗-盘专用，浅碗）；`SINK_DEPTH=0.03`、`ESCAPE_Y=0.22`（酒架坡向 +y）；`DROP_FTIP_Z=0.07`；`TABLE_Z=-0.011`（bowldrawerclose_p6b L22）与 bowl_p4_r1b 的"世界系桌面 z≈0"并存，说明桌面 z 随场景有 1 cm 量级差别，不能写死。
- 参数化：`obj_clearance`（由物体类型决定：浅碗 0.024-0.10 均有先例，>0.19 有失败先例）、`support_z`（必须来自观测）、`off_xyz`（R1）、`min_tcp_z`。前置检查：
  ```python
  tcp_z = support_z + obj_clearance - off_z - tcp_offset_z
  assert tcp_z >= 0.008, "release height below the IK floor; goto_pose would clip it"
  ```
  （bowl_p3_r1 L27 `_ZMIN=0.008`；m4 文档 z∈[0.005,0.9]）。
- 风险：
  - 证据只有"碗放盘子"一类，且 0.024-0.10 都能过，说明容差宽；这不能推广到易倒/易弹物体（酒瓶需沉入、罐子在篮口弹出，见 R3）。
  - 逐步下放（bowl_p4_r1b）依赖易失真的观测中位数；m4-r1 的 `goto_pose(max_step=...)` 已把分段移动做成原语，"自己写插值循环"的部分标注为 superseded by m4-r1，只保留"以指令 TCP 高度为停止条件"的验证部分。
  - spatialtaskcookie 的 0.040 + 推正组合有过拟合嫌疑（诊断：其 rim-pinch 在摩擦阈值上，后期修补过拟合 15 个布局）。
- 覆盖的失败类型：place/release 中"释放过高弹跳/过低推动目标"。覆盖不了：碗挂在抽屉壁上倾斜（R3/R4 与 M4 原语）、需要接触力感知的下放。

### R3 放入容器/抽屉/篮子（drop into container）
- 功能与接口草案：在高处飞到容器开口中心上方（绕开抽屉前缘/篮沿），再下降到"口沿以上若干 cm"，按 R1 的偏移补偿对准开口中心后张爪，竖直撤回；开口中心由实测几何给出，不用"把手偏移"之类写死常数。
  ```python
  def drop_into_container(opening_xy, rim_z, *, off_xyz=None, transport_z, release_above_rim,
                          tcp_offset_z=0.10, retreat_z=None, quat=TOPDOWN,
                          read_obj=None) -> dict   # {released, tcp_z, reason}
  ```
- 出处：
  - 最佳（篮子）：objswapsoup_p6_r1，bundle 0825bb17，sha12 c6d120596c70，main 内联 L299-316（`transport_z = max(tpos[2]+0.20, bpos[2]+0.14)`，释放 `bpos[2]+0.10`，撤回 `bpos[2]+0.28`）；15/15，1 版。
  - 最佳（抽屉）：bowldrawer_p6_r1，bundle a53bcde9，sha12 445b5d6dc115，L293-304（`cav = hnd_now - nn*CAV_BEHIND(0.06)`，悬停 `+0.26`，释放 `handle_z+0.04`，抬 0.25 后回 home）；15/15，4 版 [14,14,13,15]。
  - 备选：objtaskmilk_p6b_r1（4da1e6f0 / b6812ed55560）L416-519（悬停→测偏移→`bc_z+0.12` 释放→稳定判定→`bc_z+0.34` 撤回）；bowldrawer_p5_r1（3ca56ea8 / 94a78c1f1b75，best_swept_unselected）L363-395。
- 独立重写：13 个 cell。篮子：objswapsoup_p4n_r1:`carry` L201-235；objswapsoup_p5_r1:inline L340-435；objswapsoup_p6_r1:L299-316；objtaskmilk_p6b_r1:L416-519。抽屉/托盘：bowldrawer_p4_r1:L411-427；bowldrawer_p4_r1b:`carry_and_place` L475-488；bowldrawer_p5_r1:L363-395；bowldrawer_p6_r1:L293-304；bowldrawer_p6b_r1:L235-245；goaltaskbowldrawer_p5_r1:L404-419；goaltaskbowldrawer_p6b_r1:L354-401；bowldrawerclose_p5_r1:L244-251；bowldrawerclose_p6b_r1:L214-235。覆盖 4 个 task 族（soup/ketchup→篮子，碗/奶酪盒→上抽屉，碗→下抽屉）。计数法：对 33 文件扫 `open_gripper` 前 15 行的"目标=容器/抽屉"上下文，逐个阅读确认。
- 开发期证据（dev；versions；翻转 seeds）：
  - objswapsoup_p6_r1 15/15；1 版；释放本身未被记为翻转点（翻转来自按颜色识别罐子）。
  - bowldrawer_p6_r1 15/15；4 版；"低释放 + 开口半径 0.11→0.075"翻转 seed 60（世界把"搁在沿上"判成进入）。
  - bowldrawer_p5_r1 9/9（只扫了 9 个 seed）；9 版 [6,7,6,2,0,8,8,0,9]；该 cell 目录无 findings.md，本卡只引用其代码注释（L376-383），未采信 task_analysis。
  - bowldrawer_p6b_r1 10/12；6 版；"高抛点 z 0.34 再竖直下到 0.14、再竖直上撤"使 seed 51 由 0 变 1（避免撞合抽屉）。
  - bowldrawer_p4_r1 4/7；bowldrawer_p4_r1b 7/15（1 版 [7]）；goaltaskbowldrawer_p5_r1 13/15（4 版）；goaltaskbowldrawer_p6b_r1 5/15（7 版）；bowldrawerclose_p5_r1 6/15；bowldrawerclose_p6b_r1 3/15。
  - objswapsoup_p4n_r1 4/15（1 版 [4]）：只有"全部装载 + 严格口沿以上释放"的 seeds 53/56/57/59 通过，findings 称"口沿深度释放会碰倒篮子"。objswapsoup_p5_r1 4/15（6 版）：从 0.26 以上释放会把罐子弹出（seed 55/62）。
  - objtaskmilk_p6b_r1 13/15（9 版，噪声最大值）。
- 依赖 API：goto_pose、open_gripper、get_observation、mask_to_world_points；容器中心来自 `world.query("state")`（soup/milk/bowldrawer_p5/p6）。**依赖 `world`**：bowldrawer_p5 还读 `world.query("goal_state")`（`drawer_open`,`bowl_in_drawer`），任务专用，不可入库。
- 子目标验证：多数不确认。objswapsoup_p6 在 `still_holding` 失败时 "release_anyway"，之后不核对"是否入篮"；objswapsoup_p5 退到篮后 `hypot(罐-篮) < 0.13` 判 inside（L426-431）；objtaskmilk `_branch_settle`（xy<0.10 且 z<篮 z+0.16，L496-503）；bowldrawerclose_p6b 只读 `in_bottom_drawer`/`seen_in_drawer` 并打印（L239-241），其 findings 称的"落点平放 top_z<=FLAT_TOP"判据**不在冻结代码里**。需内建：释放并撤回后，从无遮挡视角判"物体中心在开口内 + 高度低于口沿 + 不在沿上"。
- 任务耦合：
  - 抽屉开口常数互相矛盾：`CAV_BEHIND=0.06`、`z=handle+0.04`（p6）；`CAVITY_OFFSET=[0,-0.07,0]`、绝对 z 0.255（bowldrawer_p4_r1 L24,425）；`cavity=[h0-0.10,h1-0.09]`、绝对 z 0.14（p6b L237-243）；`site+0.02`（p4_r1b L483）。均为各自场景下"把手→托盘中心"的经验量，抽屉开度不同就失效。
  - 篮口：`rel_z = max(0.19, bz+0.05)`（soup p4n L227）；`CARRY_Z=0.26`、`RETREAT_Z=0.44`、`TABLE_Z=0.13`（soup p5 L38-41）；basket 名 `"basket"`、milk 名 `MILK_PROMPTS`。
  - 奶酪盒入抽屉：`goal_center = interior_center + (0, 0.03, 0.07)`（goaltask_p5 L410-411）。
- 参数化：容器开口中心（实测）、口沿高度、`release_above_rim`、`transport_z`（必须高于抽屉前缘/篮沿）、撤回高度、是否需要避开相机视线（soup p5 撤到篮后 `bx-0.30`）。前置检查：
  ```python
  assert tcp_z >= rim_z + min_above_rim, "hand would enter the rim volume"   # p4n: min_above_rim≈0.05
  assert abs(release_xy - opening_xy) <= opening_half_extent - object_half_extent
  ```
- 风险：
  - 一个强样本（soup p6 15/15、bowldrawer_p6 15/15）靠的是 SAM3 篮子/把手稳定可见 + 容差大（篮 ±12×9 cm 的 In 区域，见 soup p5 findings）；换物体/换抽屉不能外推。
  - 抽屉三类程序的释放几何常数互相不一致，库里若固化某一个会静默失配。
  - bowldrawer_p5 的"release 后不再调用任何 API"属于 R5。
  - 与 m4-r1：只有 `goto_pose(max_step)` 取代自写分段；容器几何仍需自测。
- 覆盖的失败类型：place/release（开环释放、碗斜靠抽屉壁——部分：靠"高处抛点+竖直下降+居中"降低概率）、drop（罐子弹出）。覆盖不了：抽屉未完全打开/被撞合（articulated，需 M4 抽屉原语）、碗已倾斜地搁在沿上后的恢复。

### R4 释放后确认、有界纠偏与不乱动（post-release confirm）
- 功能与接口草案：张爪后先退开到不遮挡的高度，再从无遮挡视角、对照"释放前冻结的目标基准"判断物体是否在目标上/内、高度是否合理（已坐稳而非搁在沿上）；偏差落在 (min_err, max_err) 区间才做一次有界推正；已落在目标高度附近的物体不再重抓。
  ```python
  def confirm_placed(read_obj, goal_ref_xy, support_z, *, xy_tol=0.03, z_band=(-0.01, 0.03),
                     retreat=None, n_obs=2, gripper_open=None, open_min=0.5) -> dict
      # {placed, dxy, dz, perched, obj_visible, reason}
  def bounded_nudge(obj_xyz, goal_xy, *, min_err=0.024, max_err=0.09, stroke_cap=0.04,
                    back=0.05, push_z=0.03, contact_sides=(0, 55)) -> bool
  ```
- 出处：
  - 最佳（确认+恢复策略）：spatialbetween_p5_r1，bundle df6cd13b，sha12 19179c1ca0d4：`plate_ref` L58-63（取冻结盘心）、`placement_ok` L189-195（夹爪张开>0.5 且 xy<0.035）、释放后回升 0.15 再观测 L231-234、恢复规则 L261-271（碗已在盘面高度但偏心则**不**重抓，只在碗仍在桌面时重抓）。
  - 备选 1（推正）：spatialdrawer_p4n_r1，bundle bb321f66，sha12 9fb314246c56：`push_onto_plate` L265-301 + 释放后 `dxy>0.025` 触发 L357-362；15/15。
  - 备选 2：spatialbetween_p4n_r1，bundle a73646d0，sha12 4062e3444079：`_retreat_see` L497-503 + "0.024<d<0.09 → 推 min(d,0.04)" L505-526；10/15。
- 独立重写：按子功能计（计数法同上，grep + 阅读）。
  - 退开后再判：spatialbetween_p4n L493-526；spatialbetween_p5 L231-234；bowl_p5 L150-156；bowl_p4_r1b L287-297；bowl_p3 L387-389；objswapsoup_p5 L416-435（退到篮后离开相机视线）；goaltaskbowldrawer_p6b L388-392（竖直上撤并检查盒子是否被指尖带起 `bj[2]>0.26`）— 7 个 cell。
  - 冻结目标基准：bowl_p5 L120-129、spatialbetween_p5 L58-63、spatialbetween_p4n L411 — 3 个 cell（bowl_p4_r1b 的 findings 提出同一做法，但其代码仍读实时 `read_state("plate")` L239,259；bowl_p3 L321-323 也读实时盘）。
  - 有界推正：spatialbetween_p4n、spatialdrawer_p4n、spatialtaskcookie_p6b_r1 L410-447（停在盘沿的碗用闭指推到盘心）— 3 个 cell。
  - 高度/搁置判据（仅 z 代理）：spatialtaskcookie L360（`tb_z < pl_z+0.012` 视为已落盘）与 L414（`> pl_z+0.015` 且 xy<0.11 视为搁沿）；spatialbetween_p5 L266（`z < pref_z+0.028` 视为仍在桌面）；spatialbetween_p4n L442（`< rest_z+0.03` 视为落下）— 3 个 cell。
  - 张爪后/回 home 前确认夹爪状态：bowl_p4_r1b L383-390（夹爪未张开先张开并上升再 home）。
- 开发期证据（dev；versions；翻转 seeds）：
  - spatialbetween_p5 15/15；3 版；"别重抓已落盘的碗"来自 seed 51 attempt 3 的灾难（重抓把近乎成功的碗推出盘）。
  - spatialbetween_p4n 10/15；2 版 [4,10]；退开后推正使 v18b→v19 翻转 51/57/58/60（5→10）。
  - spatialdrawer_p4n 15/15；2 版 [13,15]；自适应释放+推正翻转 63；v26 翻转 56/57。
  - spatialtaskcookie 12/15；6 版；闭环 nudge（带停滞守卫）v0 9→v4 12，翻转 63、65。
  - bowl_p5 15/15；1 版；bowl_p4_r1b 13/15：done() 误判（seed 61）源于放置后盘掩码中位数偏 2-3 cm，建议用放置前基准。
- 依赖 API：get_observation、segment_sam3_text_prompt、mask_to_world_points、goto_pose、close/open_gripper；基准与状态读取经 `world.query("scene"/"state")` 与 `world.done()`。**依赖 `world`**（plate_reference/rest_position 是 world 事实）。
- 子目标验证：这张卡本身就是验证；但已有实现各有缺口。没有任何冻结程序估计物体姿态，"没有倾斜"只能用 z 代理；bowldrawerclose_p6b findings 称"落点平放 top_z≈0.066 与 3 次通过相关、搁沿 top_z≥0.095 与 4 次释放失败相关"，该判据不在代码中。必须内建：`dz` 带宽（相对观测支撑面）+ `perched` 标志 + 连续 n_obs 次一致。
- 任务耦合：`xy<0.035/0.05/0.045`（各 cell 不一）；推正触发 `0.024<d<0.09`、行程 `≤0.04`、起点后退 `0.05`、推高 `pz∈[0.025,0.035]`、（以上见 spatialbetween_p4n L513-518）；spatialdrawer_p4n 另有 55° 备用接触侧（L281-283，抽屉前缘可能挡住正后方进入）；`hang`/盘名 `"plate"`、`dxy>0.025`（取自"On 容差 3 cm"）。
- 参数化：目标基准 xy、支撑面 z、`xy_tol`（= 任务成功容差 - 观测偏差余量）、`z_band`、`min_err/max_err/stroke_cap`、退开高度、接触侧列表。前置检查：
  ```python
  assert goal_ref_xy is not None and goal_ref_frozen_before_placement   # 不得使用放置后实时目标掩码
  nudge_ok = min_err < dxy < max_err and (obj_z - support_z) < perched_z   # 搁沿的物体不能推
  ```
- 风险：
  - 推正是对"碗在空桌/盘上"的特化，推错方向会把近乎成功的物体推出去（spatialbetween_p5 seed 51 a3 的教训）；taskcookie 的推正+nudge 属于诊断所称的"后期修补过拟合 15 个布局"一类，证据降权。
  - 冻结基准要求释放前盘子被完整看到一次；若第一帧就被遮挡，基准是错的（静默失配）。
  - 与 m4-r1：`libero_latch` 成功规则下成功只需任一步达成，"事后推正"可能把已达成的结果推坏却不扣分——但也可能多耗步数预算；`goto_pose(max_step)` 取代自写分段推行程。
- 覆盖的失败类型：place/release（开环释放、偏心 2-4 cm）。部分：碗斜靠抽屉壁（仅 z 代理可发现，无法纠正）。覆盖不了：真实倾斜/姿态估计与"轻拍坐稳"这类接触原语（需 M4 原语）。

### R5 目标达成即停手（stop acting once done）
- 功能与接口草案：(a) 把可能终止回合的原语（释放、最后一次拖拽/推）当作"最后一个公共调用"：先读证据、记决策、发出原语、立刻返回；(b) 对所有公共调用加终止感知包装：遇到 `ValueError("...terminated episode")` 置位并不再发任何动作；(c) 释放后不做会破坏已达成状态的运动（酒瓶不退出，直接停）。
  ```python
  class StopGuard:
      def __init__(self, marker="terminated episode"): self.over = False; self.marker = marker
      def call(self, fn, *args, tag="", **kw):          # None if the episode is over
          if self.over: return None
          try: return fn(*args, **kw)
          except ValueError as e:
              if self.marker in str(e): self.over = True; return None
              raise                                       # real errors must not be swallowed
  def finish_with(guard, terminal_call, *, evidence_before, record):  # read -> record -> call -> return
  ```
- 出处：
  - 最佳（释放即止，无退出运动）：wine_p4_r1，bundle 98e17580，sha12 c725f1c003ee，L379-397（释放后只再观测一次 `perceive("after-release")` 并 `world.done()`，不再有任何夹爪运动）；15/15。
  - 备选 1（释放前读证据、释放后零调用）：bowldrawer_p5_r1，bundle 3ca56ea8，sha12 94a78c1f1b75，L376-432：`g0 = world.query("goal_state")` → `open_gripper()` → 若证据成立 `SystemExit(0)`；其余 `get_observation` 全包 `_ended`。
  - 备选 2（通用包装）：stove_p3_r2（8aea273a / 09f9b95ed85a）L125-142 `_Terminated`/`_STOP`；objswapsoup_p4n_r1（de34728c / b160ae46be4f）L38-68 `EpisodeOver`/`M()`；drawer_p5_r2（a3f76506 / e3afc70c93f2）L40-66 `DEAD`/`safe()`。
- 独立重写：9 个 cell（8 种实现）：wine_p4_r1 L379-397；bowldrawer_p5_r1 L376-432；stove_p3_r2 L125-232；objswapsoup_p4n_r1 L38-68,L238-270；drawer_p5_r2 L40-66；drawer_p6b_r1 L77-93,L159-170（`except Exception` 全吞）；drawer_p6_r1 L98-121（末次观测后不再动）；bowldrawerclose_p6b_r1 L295-317,L346-349（每次推压后必须先感知；"连续两次前沿读数完全相同=冻结"）；mokastove_p6b_r1 L72-77（"release 是最后一个公共调用"；先转旋钮再放锅）。其中纯"放置"相关 5 个：wine、bowldrawer_p5、objswapsoup_p4n、bowldrawerclose_p6b、mokastove_p6b；其余为关节类 cell，作为同一模式的旁证。计数法：grep `terminated|EpisodeOver|_Terminated|DEAD|STOP\[|SystemExit` 后逐处阅读，并核对各 findings 中同名叙述。
- 开发期证据（dev；versions；翻转 seeds）：
  - wine_p4_r1 15/15；0 版；沉入后张爪并"就地结束"翻转 seed 61，此后 51-65 同字节全过；seed 59（横向撤出）与 60（竖直上提）都把已稳定站立的瓶子带倒。
  - drawer_p5_r2 12/15；9 版 [2,4,0,3,8,9,5,12,8]；终止感知包装 + "穿越拖拽放最后"（v4→v7）使 55/57/58/59/60/61/62/63/64/65 由"记 0 分/失败"变为干净通过。
  - stove_p3_r2 6/6；0 版；`_STOP` 守卫消除了 world_program_error，57/58 终局重放均记 1.0。
  - bowldrawerclose_p6b_r1 3/15；7 版 [3,0,0,0,3,0,3]；attempt_62 环境奖励 1.0 却因推压后多发一次 `push-retract` 被记 world_program_error；attempt_64 加感知闸 + 冻结读数中断后记为通过（seed 51）。
  - mokastove_p6b_r1 3/15；5 版；先 360° 转旋钮，使"放锅"成为终止性最后调用，避免"place-first 越过 done 后 self-fault"（attempts 65-67）。
  - bowldrawer_p5_r1 9/9（9 个 seed）；9 版；注释称 seeds 52/57/58/63/65 的完成轨迹曾因释放后调用被记 0。
  - objswapsoup_p4n_r1 4/15、drawer_p6b_r1 15/15、drawer_p6_r1 15/15：wrapper/不动属于防御，findings 未归因翻转。
- 依赖 API：只用 try/except、get_observation 与 `open_gripper`/`goto_pose` 本身；证据读取多用 `world.query("goal_state"/"state")`、`world.done()`。包装本身**不依赖 `world`**；"释放前证据"版本依赖 world 的目标事实。
- 子目标验证：wine 在释放前不验证（靠终止本身），释放后只读一次；bowldrawer_p5 在释放前用 `goal_state` 验证"碗已在打开的抽屉里"；bowldrawerclose_p6b 靠前沿读数是否继续变化。需内建：把"目标是否已成立"的观测判据作为参数传入，成立即返回；不成立则允许重试而不是直接结束。
- 任务耦合：终止标记串 `"terminated episode"`；goal_state 键 `drawer_open/bowl_in_drawer/place_ok`（bowldrawer_p5 L386-387）；`ESCAPE_Y=0.22`、`SINK_DEPTH=0.03`（wine）；`SEATED_Y=-0.20`、`-0.212`（drawer close）；步数预算数字不一致（soup p4n：约 4000 步、每次阻塞移动约 90-120 步、≤30 次；bowldrawer_p6 findings 提到 ~999 步回合）。
- 参数化：终止标记、哪个原语是"可能终止的"、释放前证据谓词、动作预算、冻结检测窗口。前置检查：
  ```python
  def may_issue(guard, confirmed): return (not guard.over) and not confirmed
  # in the except branch: swallow only isinstance(exc, ValueError) and marker in str(exc);
  # IK / perception errors must propagate (drawer_p6b's blanket `except Exception` hides them)
  ```
- 风险：
  - 与 m4-r1 部分重叠：api-revision-m4.md 说明在 `libero_latch` 成功规则下，预算耗尽后的 `terminated episode` 不再记为程序错误，任一步达成即成功；此时"包装吞异常"这半部分被取代（superseded by m4-r1，只保留"确认即停"与"不撤销已达成状态"）。subgoal-verification.md §4 已有同义文字，缺的是可复用的机械实现。
  - wine 的"就地结束、不撤出"只在终局状态计分时有益；若按"任一步成功"计分，后撤并无损失，但站立瓶仍可能触发后续误判，需实测。
  - `except Exception` 全吞（drawer_p6b）会掩盖真实 IK/感知故障，静默提前结束；"读数冻结=终止"（bowldrawerclose_p6b）是启发式，真实卡死会误触发。
  - bowldrawerclose_p6b、mokastove_p6b 只有 3/15，主要价值是机制说明而非可复用代码。
- 覆盖的失败类型：把"成功后被记为错误/被后续动作破坏"的 place/release 与 articulated（stove 未亮、抽屉未全开）中的一类；覆盖不了：抓取/感知失败（grasp、perception），也不解决"释放时刻物体未真正到位"。

## 本组小结
- 强（>=3 个独立 cell 且有 dev 15/15 或 12+/15 的程序）：
  - R1 携带偏移补偿释放：12 个 cell 实测（8 个 task），bowl_p5 / spatialbetween_p5 / spatialdrawer_p5 三个 15/15；是本组最扎实的一张。
  - R2 释放高度：10 个 cell、6 个 15/15，但内容是一张"按物体/目标面标定的高度表"，只有碗-盘一类有足够样本，不能当单一常数。
  - R3 容器/抽屉投放：13 个 cell，soup_p6 与 bowldrawer_p6 为 15/15；模式强，但"把手→托盘中心"的常数各 cell 互相矛盾（0.06/0.07/0.09-0.10），入库必须改为实测开口中心。
- 中等：
  - R4 释放后确认：退开后再判 7 个 cell、冻结目标基准 3 个、有界推正 3 个，15/15 的是 spatialbetween_p5、spatialdrawer_p4n；z 代理（搁沿/落下）只有 3 个 cell 各写一次，且无任何冻结程序估计姿态。
  - R5 停手：9 个 cell（其中纯放置 5 个）；wine 的"释放后就地结束"15/15，但包装吞异常这一半在 m4-r1 的 `libero_latch` 下已被取代（superseded by m4-r1，只保留确认即停与不撤销已达成状态）。
- 弱/嫌疑：证据主要来自 spatialtaskcookie（12/15，诊断称 rim-pinch 在摩擦阈值上、后期修补过拟合 15 个布局）、objtaskmilk（13/15 是噪声最大值，复跑 8/15）、bowldrawerclose_p5/p6b（6/15、3/15）、goaltaskbowldrawer_p6b（5/15）、objswapsoup_p4n/p5（各 4/15）、mokastove_p6b（3/15）的做法只作旁证；spatialdrawer_p4n 的 `hang_x,hang_y=0.025,0.085` 常数预补偿不应入库。
- 空白（本组没有任何可复用代码）：(1) 没有一个冻结程序估计放置后物体姿态，"没有倾斜/没有斜靠抽屉壁"只能靠 z 代理，且 bowldrawerclose_p6b findings 里的 `top_z<=FLAT_TOP` 判据并不在冻结代码里；(2) 篮子/抽屉"是否入内"在 soup_p6、bowldrawer_p6 等高分程序里没有观测确认；(3) 被夹物体长期被遮挡时的偏移估计（只有 CARRY_EST 常数、世界携带模型、夹爪开度三种替代）；(4) 易倒/易弹物体（酒瓶、罐）只有 wine、soup 各一个 task 的经验。
- 独立性说明：同一 task 的多个 cell（碗放盘 3 个、spatialbetween 2 个、bowldrawer 5 个）互为独立重写，但不是跨 task 证据；R1 的 12 个 cell 只覆盖 8 个 task。
- 禁用 API 检查：对 33 个 policy 文件 grep `env.handle.env|sim.data|sim.model|sim.forward|body_xpos|get_site_xpos|set_joint_qpos|parsed_problem|_eval_predicate|obj_body_id|_step_once`，命中 0 处；本组各卡的 helper 均未触及禁用 API。需要 `world` 的部分已在各卡"依赖 API"处标明（R1/R2/R4 读状态、R3 的 goal_state、R5 的证据读取）。


# 候选 skill 卡片：articulated 组（抽屉 / 铰链门 / 旋钮 / 设备状态）

> 范围与口径：只用开发集证据（seed 51-65 的 policy 代码、findings.md、task_analysis.md、development_state 行、开发 trial 日志）。
> 未打开任何 heldout/sealed/unseen 路径；cell_index 里的 heldout 率仅用于排序，不在卡片中引用。
> 行号均指 `cell_index.json` 里该 cell 的 `policy_path` 文件（精确字节）；cell 名后括号为 bundle(8 hex)/policy_sha12。
> 扫描结果：33 个 policy 文件全部没有触碰 forbidden API（只有 `env.handle.task_language`，属允许项）。
> 非 brief 列表内的全局名（库内使用时必须核实是否存在）：`rotation_matrix_to_quaternion`（drawer_p3_r1、stove_p3_r1、goaltaskbowldrawer_p6b_r1）、
> `interpolate_segment`（bowldrawer_p6_r1）、`goto_pose(..., z_approach=)`（stove_p3_r2、mokastove_p5_r1、bowldrawer_p6b_r1、goaltaskbowldrawer_p5_r1）、`segment_sam3_point_prompt`（stove_p3_r1）。
> 所有 cell 的 api_sha 同为 fee6d84b7d，所以各 cell 对"可达性"的相互矛盾说法不能用 API 版本差异来解释。

## 卡片一览
- A1 抽屉把手定位（SAM3 把手 -> 世界坐标、序数身份、外法线）
- A2 前向竖爪夹持并拉开抽屉（含夹持命中判据）
- A3 抽屉开度验证（从自由视角用把手位移判定）
- A4 抽屉关闭推送（弱证据，主要描述缺口）
- A5 旋钮弧线旋转（灶台旋钮）
- A6 设备状态检查（灶台红圈、旋钮响应；多数是缺口）
- A7 铰链门/微波炉（无正面证据，仅缺口）

### A1 抽屉把手定位

- 功能与接口草案：用 SAM3 文本提示取"把手"候选，按分数门槛+深度点云得到世界坐标，再按"同列(x,z 对齐)按 z 排序"选出第 N 层把手，并从把手两侧的抽屉前板拟合外法线。
```python
def locate_handles(obs, prompts=("drawer handle", "handle"), score_floor=0.35, max_masks=8,
                   cams=("agentview", "robot0_eye_in_hand"), dedupe_m=0.04,
                   z_band=None, exclude_near_xyz=None, exclude_radius=0.12): ...   # -> [{center, n_pts, score, cam}]
def pick_by_ordinal(cands, ordinal, column_tol_xz=0.06, gap_m=(0.04, 0.10), n_expected=3): ...  # top/middle/bottom -> 候选或 None
def front_normal(mask, box, depth, K, T, bar_depth, strip_px=22, depth_win=(-0.02, 0.06)): ...  # -> (n_xy_unit, plane_center)|None
```
- 出处：best = drawer_p4_r2b（7fa2f452 / 42f8166ea02f）`_cam_handles` L97-130、`detect_values` L133-152、`_strip_normal` L60-94（双相机+分数下限+前板平面拟合法线）。
  runner-up：drawer_p6b_r1（b4a2118c / d66f594bf123）`perceive_handle` L42-74 与 `_clean` L20-25（自包含，z 排序取中位）；
  drawer_p5_r2（a3f76506 / e3afc70c93f2）`observe_handles` L74-95（两相机、前 8 个 mask、点数 5..5000）。
  注意：p4_r2b、p5_r2、p6_r1 的"第几层"身份判定放在 `world` 里（代码文件里看不到），只有 p6b 与 bowldrawer_p6_r1 在 policy 里自己做。
- 独立重写：12 个 cell。计数方法：对 33 个 policy_path 文件 grep 字符串字面量含 handle/knob 的 SAM3 提示，再逐个读上下文确认是"把手定位给操作用"。
  drawer_p3_r1:perceive L46-94（提示 "knob"，取前 40 个 mask，面积20..40000、颜色），drawer_p4_r2b:_cam_handles L97-130，
  drawer_p5_r2:observe_handles L74-95，drawer_p6_r1:_perceive L39-45（inline "handle"），drawer_p6b_r1:perceive_handle L42-74，
  bowldrawer_p6_r1:_perceive L68-131（z∈[0.06,0.26]、距柜体中心<0.35、取最高），bowldrawer_p6b_r1:_handle_obs L107-126（score≥0.5、距手<0.12 排除、跟踪关联），
  bowldrawer_p5_r1:collect L80-110（"drawer handle" score≥0.30、前4个），bowldrawer_p4_r1b:perceive L151-215/top_handle L218-223，
  bowldrawer_p4_r1:observe_targets L123-204（"drawer handle" 后退到 "handle"），goaltaskbowldrawer_p5_r1:_cycle L168-213，goaltaskbowldrawer_p6b_r1:perceive L56-105（"handle" 0.05/120px）。
  排除：spatialdrawer_p4n/p5（把手只当身份锚点，不用于操作）。
- 开发期证据（passed/tested；版本数）：
  drawer_p4_r2b 15/15，1 版；分数下限 0.35 令 seeds 51-53 的"median-z 选错顶层把手"翻转；双相机令 52/53 的拉后观测恢复（done 变 true_positive）。
  drawer_p6b_r1 15/15，1 版；drawer_p6_r1 15/15，1 版（"handle" 提示 15/15 序数正确）。drawer_p5_r2 12/15，9 版 [2,4,0,3,8,9,5,12,8]；x/z 锚点(|dz|≤0.05,|dx|≤0.06)修复 seed 51 身份漂移(v4→v5a)。
  bowldrawer_p6_r1 15/15，4 版 [14,14,13,15]；z 带+距 pad<0.10 排除令 seed 51 的"假 open_amount 0.13/假高度0.52"消失。
  bowldrawer_p6b_r1 10/12，6 版；bowldrawer_p5_r1 9/9（best_swept_unselected），9 版；bowldrawer_p4_r1b 7/15；bowldrawer_p4_r1 4/7；
  goaltaskbowldrawer_p5_r1 13/15，4 版；goaltaskbowldrawer_p6b_r1 5/15（抽屉部分 findings 称 100% 打开，失败在抓奶酪）；drawer_p3_r1 2/2（整条谱系 10/15，竖向三联规则令 51-53 期"找不到"翻转）。
- 依赖 API：get_observation、segment_sam3_text_prompt、mask_to_world_points（可选 get_oriented_bounding_box_from_3d_points）。`world` 依赖：p4_r2b/p5_r2/p6_r1/p3_r1 的身份与"known"状态在 world 里；纯函数版本只有 p6b 的 perceive_handle 与 bowldrawer_p6_r1 可以直接搬。
- 子目标验证：exemplar 自己不独立确认"选对了层"，只是 `world.query("state", ..., status=="known")`；p6_r1 的 findings 说明 3 个候选缺 1 个时就"unknown"。
  必须内置：同列检查（见下）+ 位置跟踪（抽屉只沿滑轨轴移动，x/z 不变）。
- 任务耦合：SCORE_FLOOR 0.35（p4_r2b；p6b 0.35；bowldrawer_p6b 0.5；p5_r1 0.30；goaltask_p6b 0.05）；p4_r2b 的 box 形状过滤 `8≤bw≤120, 15≤bh≤260, bh≥bw`（像素框，依赖 agentview 视角）；
  p6b 的 `_clean` 世界框 x∈(0.2,0.7), y∈(-0.45,0.25), z∈(-0.15,0.5) 和硬编码回退 `[0.41,-0.16,0.11]`；bowldrawer_p6_r1 的 z∈[0.06,0.26]；柜体位置随任务变化（"开中层抽屉"约 x≈0.41，"开顶层+放碗"约 x≈0.70）。
- 参数化：score_floor、max_masks、z_band、workspace_box、序数词表(top/upper/first→0...)、列容差都要变参数；回退坐标必须删除，找不到就返回 None 并让调用方重观测。可运行前置检查：
```python
def handle_column_ok(cands, n_expected=3, tol_xz=0.06, gap=(0.04, 0.10)):
    zs = sorted(c["center"][2] for c in cands)
    return (len(cands) >= n_expected and np.ptp([c["center"][0] for c in cands]) < tol_xz
            and all(gap[0] <= b - a <= gap[1] for a, b in zip(zs, zs[1:])))
```
- 风险：SAM3 "handle/knob" 每次返回约 200 个 mask（p4_r2b 一次 1626 次 API 调用，需要上限）；夹爪被当作把手(bowldrawer_p6_r1 z 0.28/0.52、spatialdrawer 四个 seed、p5_r2)——必须按距手/当前 pad 点排除；
  拉开后目标把手离开三联列、其它把手掉到分数下限以下(p6_r1 findings、p3_r1 "association amnesia")；只有同一款柜子的证据，未见别的资产。与 m4-r1 无冲突（不涉及 goto/IK/抓取规划）。非 objtaskmilk/spatialtaskcookie 的噪声/过拟合嫌疑来源。
- 覆盖的失败类型：perception（SAM3 把手误命中）与 articulated 中"开错层"；不能覆盖：到位后的接触/可达性问题（需要 A2 或 M4 原语）。

- 备注（并入 A1）：把手几何在不同 worker 笔记里互相矛盾：多数 cell 说"沿世界 X 的水平圆柱杆(≈0.09-0.15 m 长，径 13-24 mm)"，bowldrawer_p5_r1 的 policy 说"竖直圆柱 z 0.150..0.217"。以 4 个 15/15 的前向竖爪程序(drawer_p4_r2b、drawer_p6_r1、drawer_p6b_r1、bowldrawer_p6_r1)为准(水平杆)，p5_r1 的说法视为孤例，未解决。
- 强度：12 个独立 cell，其中 4 个 15/15 → 强；但"序数身份"部分多数藏在 world 里，纯 policy 层可搬的只有 2 个 cell。
### A2 前向竖爪夹持并拉开抽屉

- 功能与接口草案：手朝柜体水平接近(approach=-外法线)，手 y 轴=世界竖直(指对竖向夹合)，两指从上下夹住水平圆杆，闭合后沿外法线分段拖拽；用闭合后的开度判断"夹到杆/夹空气/撞到面板"。
```python
def front_quat(outward_n):                 # R: x_h=cross(up,z_h), y_h=up, z_h=-n   (n 水平单位向量) -> wxyz
def front_pinch_pull(handle_xyz, outward_n, *, standoff=0.12, reach_tries=((0.03,0,0),(-0.015,0,0),(0,0,0),(0.03,0,0.012),(0.03,0,-0.012)),
                     air_open_max=0.05, hold_open=(0.12, 0.60), pull_total=0.20, pull_step=0.045, double_goto=True,
                     reclose_after_step=False, max_cycles=2): ...    # -> {"grasped": bool, "openness": float, "tries": int, "base": xyz}
```
- 出处：best = drawer_p4_r2b（7fa2f452 / 42f8166ea02f）`attempt_grasp` L177-208（搜索阶梯+开度判据）与拉动循环 L247-262；
  runner-up：bowldrawer_p6_r1（a53bcde9 / 445b5d6dc115）`_quat_from_n` L40-48 + `_grasp_and_pull` L192-213（法线由测量得到，分三段滑入 +0.14/+0.06/0，再拉到 +0.07/0.13/0.19）；
  drawer_p6_r1（8bc3d729 / 66cb68a80f74）L53-117（最紧凑：顶视下探→两次相同 goto 稳定 IK→两次 close→拉 5 步×0.045，QH=(0.7071,0.7071,0,0)）。
- 独立重写：7 个 cell 用同一旋转(绕 x 轴 +90°：wxyz≈(0.7071,0.7071,0,0)，或列向量 x=(1,0,0),y=(0,0,1),z=(0,-1,0))。计数方法：在 33 个文件里 grep 该四元数/旋转矩阵及其构造。
  drawer_p3_r1:grasp_quat L110-131+run L163-329；drawer_p4_r2b:attempt_grasp L177-208；drawer_p5_r2:one_ratchet L106-164(Q_FRONT L33)；drawer_p6_r1:inline L53-117；
  drawer_p6b_r1:horiz_quat L28-34+main L96-170；bowldrawer_p6_r1:_quat_from_n L40-48/_grasp_and_pull L192-213；goaltaskbowldrawer_p6b_r1:front_quat L11-18（矩阵 [[1,0,0],[0,0,-1],[0,1,0]]）。
  另有 5 种不同接触方式(不算独立重写)：bowldrawer_p4_r1 hook_under（4/7）、bowldrawer_p5_r1 hook_behind_bar（9/9 未被选）、bowldrawer_p6b_r1 tilted_hook（10/12）、goaltaskbowldrawer_p5_r1 45°倾斜"楔形耙"（13/15）、bowldrawer_p4_r1b Q_GRIP=(0.707,-0.707,0,0)（7/15，符号与其它相反，未核实）。
- 开发期证据：drawer_p6_r1 15/15（seed 51 靠"两次相同 goto"令到位残差 -0.228→-0.009、夹宽 0.001→0.179 翻转；其余 14 个 seed 同字节通过），1 版；
  drawer_p6b_r1 15/15（seed 51 靠"压到把手自身高度 dz=0"翻转——dz=+0.05 会拉开顶层抽屉），1 版；drawer_p4_r2b 15/15（seed 52 attempt3 首次通过，其余 13 个 seed 同字节通过），1 版；
  bowldrawer_p6_r1 15/15（seed 51 翻转；seed 57 靠"只在开度<0.06 时才二次拉"修复），4 版 [14,14,13,15]；drawer_p5_r2 12/15（拉一次只能走 ≈10-13 cm），9 版；
  drawer_p3_r1 2/2（4 个同摘要 dev 通过：64、65、56、62；"滑脱检测+重夹"令 seed 61 翻转，到位校验令 59/60 翻转）；goaltaskbowldrawer_p6b_r1 5/15（抽屉段 findings 称 100% 打开 +0.095..0.16 m）。
- 依赖 API：goto_pose、close_gripper/open_gripper、get_observation（`robot_cartesian_pos[7]` 为归一化开度）、scipy Rotation；`rotation_matrix_to_quaternion` 为非清单全局（drawer_p3_r1、goaltask_p6b）；
  `interpolate_segment` 为非清单全局（bowldrawer_p6_r1）。`world` 依赖：p4_r2b 用 world.plan_pull/plan_object_still 与 pull_direction 事实；p6_r1 用 world 的 `grasped` 事实；p6b 用 W.set_pending_object。核心运动与开度判据不需要 world。
- 子目标验证：有。夹持：闭合后读 `robot_cartesian_pos[7]`——空夹 0.014-0.019(p4_r2b)、夹到杆 0.17-0.25、撞面板 >0.6(p5_r2：>0.6 的"假抓"曾导致空拖把抽屉推回去)；拉动：释放+退开后重观测把手位移(见 A3)。
  缺口：p6_r1 的 `grasped` 由 world 用"开度+记忆中心"推出来，不是独立观测；p6b 接受 0.05..0.70，上限会放过撞面板。库里要用 (0.12, 0.60)，并在每个场景先自测一次空夹基线。
- 任务耦合：旋转(0.7071,0.7071,0,0) 只对"外法线=+Y"成立（p6_r1/p6b/p5_r2 全部硬编码 +Y：`y+0.012`、`base+[0,0.03*i,...]`）；p4_r2b/bowldrawer_p6_r1 才用测量法线；
  偏移 `+0.012,-0.002`、拉距 0.12/0.20/0.19、`PULL_STEP=0.045`(注释："blocking gotos over >~0.1 m stop short")、`max(0.28, z+0.16)`、p6b 的 `0.05<=w<=0.70`；
  TCP 与手腕的偏置在各 cell 测得不一致：0.100(bowldrawer_p6_r1)、0.104(stove_p3_r2)、0.107(drawer_p3_r1/p4_r2b)、0.111(stove_p4_r1b)、0.115(mugmicrowave_p6)——不能写死。
- 参数化：outward_n 必须来自 A1 的 front_normal（不是常量）；hold_open/air_open_max 必须作为参数并在运行前用一次空夹校准；staging 距离、pull 距离、step 均为参数。前置检查：
```python
def front_pinch_precondition(n, handle_xyz, pitch_z=0.05, min_gap=0.035):
    n = np.array([n[0], n[1], 0.0]); assert abs(np.linalg.norm(n) - 1.0) < 1e-6   # 水平外法线
    return pitch_z >= min_gap     # 相邻把手/面板的竖向间隙须容得下上下两指；否则改用钩/楔类原语
```
- 风险：(1) 同一任务里并存互相矛盾的说法：bowldrawer_p6b 称"只有倾斜钩子能动"，而同任务同套件的 bowldrawer_p6_r1 用前向竖爪 15/15——说明成败对 IK 状态敏感；drawer_p4_r2b 说"水平夹(指沿 X)不可能"，drawer_p6_r1 的注释却把同一四元数描述为"指沿 X"（旋转数学上是指沿竖直，p4_r2b/p5_r2/p6b 的说法才对）。
  (2) 到位残差：两次相同 goto、<=0.06-0.075 m 分段、`PULL_STEP` 这些都被 m4-r1 的 `goto_pose(max_step=)` 分段与 IK 替换提示"取代"——标注"superseded by m4-r1，只保留开度判据与重夹逻辑"。
  (3) p5_r2 的 51/52/64 在 12/15 里仍失败（落点偏 2-6 cm，且 +8 mm 复夹会蹭到杆把抽屉拖回去）；手段是概率窗口而非确定性。(4) 非 objtaskmilk/spatialtaskcookie 来源，不属于已知噪声/过拟合集合。
- 覆盖的失败类型：articulated"抽屉没开到位"(30 条里的开启部分)；不能覆盖：单次拖拽行程上限(≈10-13 cm，需棘轮式多次夹)和成功阈值(见 A3)、伸手边缘的 IK 方差——需要 M4 的沿轴顺应拉动原语。
- 强度：7 个独立 cell、4 个 15/15 → 强。
### A3 抽屉开度验证

- 功能与接口草案：释放夹爪、退出把手视线(退到把手前方/回 home)后重新定位原来那根把手，用沿滑轨轴的位移当"开度"；位移够就停，不够就重夹，位置缺失就换视角而不是当作未开。
```python
def drawer_travel(ref_xyz, now_xyz, slide_axis):          # -> (travel_along_axis, lateral_residual)
def confirm_drawer_open(observe_handle_fn, ref_xyz, slide_axis, need_travel, *, retreat=("high_front", "home"),
                        keep_dx=0.045, keep_dz=0.04, max_views=2): ...   # -> {"status": "open"|"not_open"|"unknown", "travel": float|None}
```
- 出处：best = drawer_p6_r1（8bc3d729 / 66cb68a80f74）L98-117：释放→退到 (x,y+0.10,z+0.03)(两次 goto)→重感知；位置缺失则再退到 (x,y+0.24,0.40)→再感知；`moved = p.y - y >= 0.12`；
  runner-up：drawer_p5_r2（a3f76506 / e3afc70c93f2）L186-229：每两次棘轮回 home 取"干净视角"再判位移（FINAL_AT=0.10 触发最后一次长拖 FINAL_TRAVEL=0.26）；
  bowldrawer_p6_r1 L243-262：回 home 后读 `cabinet.top_drawer_open_amount`，仅在 <0.06 才二次拉。
- 独立重写：9 个 cell。计数方法：grep 各文件里 释放+退开+重观测+阈值比较的块，逐个读确认。
  drawer_p6_r1 L98-117；drawer_p5_r2 L186-229；drawer_p4_r2b L257-277（`world.query("goal_status")["displacement"] >= REQ_DISP=0.10`，加手腕相机去重 0.04 m）；
  drawer_p3_r1 L256-291（`displacement >= OPEN_DISP_MIN=0.05`；0.12 反而退步）；drawer_p6b_r1 L159-170（只退+再观测，无阈值）；bowldrawer_p6_r1 L243-262；
  bowldrawer_p5_r1 `choose_opened` L277-313（`goal_state.drawer_open`=行程≥6 cm；未达 FULL_TRAVEL 则重拖）；goaltaskbowldrawer_p5_r1 L312-346（`drawer.open_distance >= PULL_OK=0.05`）；bowldrawer_p4_r1b `open_top_drawer` L245-413（以拉动行程作仲裁）。
- 开发期证据：drawer_p6_r1 15/15（3 个 cycle 上限，1 版）；drawer_p5_r2 12/15（"干净 home 视角+最后一次长拖"令 61/62 的 11 cm 平台翻转为通过；9 版）；drawer_p4_r2b 15/15（手腕相机令 52/53 的 done 变 true_positive）；
  drawer_p3_r1 2/2（深停阈值 0.12 令 64、60 退化为滑脱）；bowldrawer_p6_r1 15/15（开度<0.06 才二次拉令 seeds 53/57 脱离步数超限）；bowldrawer_p5_r1 9/9（未被选）；goaltaskbowldrawer_p5_r1 13/15；bowldrawer_p4_r1b 7/15；drawer_p6b_r1 15/15。
- 依赖 API：get_observation、segment_sam3_text_prompt、mask_to_world_points、goto_home_joint_position；位移的"已知/未知"状态与 `open_amount/open_distance/displacement` 事实全部来自 `world`（p4_r2b、p6_r1 的 world.checkpoint、p3_r1、bowldrawer_p6_r1、goaltask_p5）。纯 policy 版本只有 drawer_p5_r2 的 `moved = cur_y - start_y` 可直接搬。
- 子目标验证：这张卡本身就是验证；信号=把手 mask→深度→世界 y 的前后差。局限(均为开发观测)：夹爪遮挡/并入 mask（bowldrawer_p4_r1b 夹持期间读到 z=0.312 的夹爪体；p5_r2"in-place 视角含夹爪 blob"）→ 必须自由视角；
  拉开后被餐具架遮挡(p4_r2b 只剩顶层杆)→ 加手腕相机；其它把手掉到分数下限以下令三联规则失效(p6_r1 findings 建议改成"离记忆位置最近、|dx|<0.045,|dz|<0.04")。
- 任务耦合：滑轨轴硬编码 +Y（p6_r1、p5_r2 的 `[1]-y`）；阈值随用途不同且互相冲突：0.05/0.06(可放碗)、0.10/0.12(算"动了")、p5_r2 实测"行程 0.12-0.13 失败、0.157-0.16 通过"(任务成功需 >~0.135)；
  p6_r1 以 0.12 为 moved 阈值却 15/15，是因为拖拽实际行程更大。退让位置 `(x,y+0.10,z+0.03)`、`(x,y+0.24,0.40)`、`home`。bowldrawer_p4_r1b：滑轨符号若相对"机器人 home y"定义，把手拉到 y≈0 时会退化翻号（改为相对"操作前锚点"）。
- 参数化：need_travel、slide_axis(来自 A1 的法线)、retreat 序列、keep_dx/keep_dz 全为参数；前置检查(可运行)：
```python
def travel_ok(ref, now, n, need, lateral_max=0.045):
    d = np.asarray(now) - np.asarray(ref); t = float(d @ np.asarray(n)); lat = float(np.linalg.norm(d - t * np.asarray(n)))
    return t >= need and lat < lateral_max      # 位移必须沿滑轨轴，否则是身份被夹爪/邻抽屉劫持
```
- 风险：(1) 身份漂移造成假进度——p5_r2 seed 51 的"假 moved 0.167"，bowldrawer_p6_r1 seed 51 的"假 open_amount 0.13"，必须有 lateral 检查；(2) 任务成功即终止回合，之后任何动作会令 trial 记作 world_program_error(p5_r2 的 v1 六个近乎满分 seed 因此记 0)，
  所以"让越过阈值的那一拖成为最后一个动作"并包一层 `safe(fn)`(检测到 terminated episode 就停)——这属跨簇通用项，建议主汇总者另立卡；(3) 与 m4-r1 无冲突。
- 覆盖的失败类型：articulated"抽屉没开/没开够"中的判定部分，以及"假成功/假进度"；不能覆盖：真正把抽屉拉到位(A2)和关闭(A4)。
- 强度：9 个独立 cell，含多个 15/15 → 强，但阈值需任务级标定，且 world 依赖重。
### A4 抽屉关闭推送（弱证据：只描述缺口，不作正面证据）

- 功能与接口草案：先感知抽屉前板(最靠外的点云)，在碗之外的"裸边"车道上用夹爪朝柜体方向推，逐次推后重新测前板 y，到平齐阈值就停且不再发动作。
```python
def drawer_front(obs, prompts=("bottom drawer",), score_min=0.35, area_min=800, workspace=((0.20,0.78),(-0.45,0.45),(-0.06,0.30)), pct=95): ...  # -> {"front_xyz","span"}|None
def push_drawer_closed(front_xyz, close_dir, *, lane_dx=-0.09, z_push=0.045, stand_off=0.07, stroke=0.27, quat=None,
                       max_presses=4, flush_front_y=None): ...   # 每次推后重测；达到 flush 或观测冻结即停
```
- 出处：best（弱）= bowldrawerclose_p6b_r1（22ef841b / ef1b64b58765）`perceive_drawer` L78-103 + `_drawer_in_workspace` L70-75 + 模块级推送循环 L243-362（逐次推后感知、flush 或"观测完全重复=回合已终止"即 break）；
  runner-up = bowldrawerclose_p5_r1（ad3d57bf / 5e85ddc31a2f）`_drawer_pts` L70-80、`_drawer_pair` L103-140、推送块 L256-291（PUSH_QUAT=(0.707,0.707,0,0)，前板 z≈0.055，`[wx, wy+0.07]→[wy+0.02]→[wy-0.27]` 4 轮）。
- 独立重写：2 个 cell（上述两个），方法：grep `PUSH|close_gripper` 与"drawer"字面量后逐个读；两者的推法互相矛盾，因此不算同一方法。
- 开发期证据：bowldrawerclose_p5_r1 6/15（通过 51,54,55,58,62,65；6 版 [3,4,2,5,4,6]；"水平 −Y 车身推"令 3/15→6/15；失败 9 个 seed 全在关抽屉，模式 close_not_seated/close_not_engaged）；
  bowldrawerclose_p6b_r1 3/15（通过 51,53,64；7 版 [3,0,0,0,3,0,3]；"每次推后感知门+深推裸边"令 51/53/64 翻转；其余 12 个 seed 为 seating-stall 8 个+release-perched 4 个，findings 标为物理上堵死）。另有 bowldrawerclose_p4n_r1 0/15（无程序证据）。
- 依赖 API：get_observation、segment_sam3_text_prompt（"bottom drawer"）、mask_to_world_points、goto_pose、close_gripper；`world` 依赖：p6b 的 `bottom_drawer.closed`（语义事实）与 `front` 事实；p5 也往 world 写 drawer 中心。
- 子目标验证：p5 程序只把最终 `front y97` 打日志，不据此分支(无验证)；p6b 每推一次重测前板 y，用"实测的 flush 阈值 −0.212"而不是 world 的语义 closed(−0.195 就判 closed，过早停；环境真正闭合在实测 −0.20..−0.22)。
  必须内置：每次推完再感知；观测冻结检测；flush 阈值来自首次成功读数而不是语义标志。
- 任务耦合：推方向 −Y（柜体固定、抽屉沿世界 Y 滑；p5 中 `_drawer_pair` 还假设"柜体在 −X、抽屉朝 +X"）；`PUSH_X_OFF=-0.09`（避开碗）、`PUSH_Z=0.045/0.03`、`PUSH_FROM_Y=0.07`、行程 −0.16/−0.27、`RAIL_STOP≈-0.22`、`FLUSH=-0.212`；SAM3 "bottom drawer" 会被背景墙柜劫持(p6b 靠 `0.20<x<0.78, -0.06<z<0.30` 工作区门过滤)。
- 参数化：close_dir（来自 A1/A3 测得的滑轨轴取反）、flush_front_y（先在未放物体时测一次"闭合状态"前板位置，或用首次成功读数）、车道偏移(由放置物 AABB 推出而非常量)、quat 作为参数。前置检查：`workspace_gate(pts)` 即 p6b 的 `_drawer_in_workspace`。
- 风险：两个 cell 对"该用顶视还是水平夹爪推"给出相反结论(p5：顶视 −Y 只推动碗；p6b：顶视闭爪尖在 z=0.03-0.035 的深推通过 3 个 seed)；
  碗在后开口托盘里被推出(p6b)；关闭瞬间成功会终止回合，之后的 retract 动作被记 world_program_error（p6b attempt_62 本是 reward 1.0 却被判错）；
  通过率低且不稳（≤40%），目前没有可复用的稳定原语。与 m4-r1 无冲突，但很可能需要 M4 的"沿轴顺应推"原语。
- 覆盖的失败类型：articulated"抽屉没关严"(taxonomy 30 条里的关闭部分)——实际上不能可靠覆盖；本卡的价值主要是"flush 阈值实测+每推一次门控+终止安全"三条通用规则。
- 强度：2 个 cell、最高 6/15 → 弱，列入缺口。
### A5 旋钮弧线旋转（灶台旋钮）

- 功能与接口草案：定位旋钮中心/拨片尖/顶面高度；让手指跨在拨片上(或夹住旋钮穹顶)，按弧线或原地转腕转过 total_rad；重观察拨片角度，响应不足(<responsive_rad)就反向再扫。
```python
def knob_geometry(rgb, depth, K, T, prompts=("black knob", "knob"), score_min=0.25, box_side_px=(12, 140),
                  anchor_mask=None, overlap_max=0.35, exclude_near_xyz=None, exclude_radius=0.10): ...   # -> {cx,cy,ztop,angle,r_tip}|None
def rotate_knob(geom, *, mode="pinch_arc", sign=+1, total_rad=0.9, step_rad=0.30, descend_depth=0.018,
                r_inset=0.007, hover=0.10, aperture_ok=0.05, responsive_rad=0.20, flip=True): ...         # mode: pinch_arc|wrist_spin|hook_sweep
```
- 出处：best = stove_p4_r1b（6c9cbab6 / 45297374f402）`_knob_mask` L58-78、`perceive` L81-115(极坐标求拨片尖)、`main` L129-222（跨拨片下探、抬起后读开度、+0.9 rad 弧、<0.2 rad 则反向扫）；
  runner-up：stove_p4_r2（05662bdd / 174d84b05b3c）`_fin_ring_gate` L99-127(拨片方向"有向/无向"双模)、`perceive` L130-215、`main` 轨道 L300-315(55° 弧，6 步，失败则手腕 +90° 重试)；
  mokastove_p5_r1（b27482c8 / fbd80c2692f0）`turn_knob` L167-182(夹穹顶赤道 z=0.02 原地转腕 30°×5)、翻转重试 L184-202。
- 独立重写：6 个 cell，方法：对 33 个文件 grep knob/lever/dial 并逐个读（bowl/spatial 的命中是"径向"之类误报，已剔除）。
  | cell | 接触方式 | 转角 | 夹/扫高度 | 验证信号 |
  |---|---|---|---|---|
  | stove_p4_r1b 15/15 | 夹拨片尖+弧线 | +0.9 rad，无响应再 −0.9 | ztop−0.018 | 拨片角变化≥0.2 rad |
  | stove_p4_r2 15/15 | 夹拨片+绕轴轨道 | 55°/次，最多 3 次 | 拨片中高+0.012 | world cumulative_rotation≥0.75×90° |
  | mokastove_p5_r1 15/15 | 夹穹顶赤道、原地转腕 | +150°，失败再 −300° | knob 顶−0.02(夹在[0.012,0.035]) | 拨片方向二倍角位移≥0.7 |
  | stove_p3_r1 10/10 | 闭爪钩扫(拨片半径+8 mm) | ±1.5/+2.0 rad 扇扫 | 拨片尖−0.006 | knob.angle 旋转≥0.70 rad |
  | stove_p3_r2 6/6 | 夹拨片，空夹则闭爪钩扫 | ≥1.2 rad 累计 | 杆顶−0.012 | 累计带符号转角≥1.2 |
  | mokastove_p6b_r1 3/15 | 顶视夹后绕轴 360° | 360° | 0.045→0.04 | 无(靠"先转旋钮再放锅") |
- 开发期证据：stove_p4_r1b 15/15（初版 0/8，改为"下探到拨片下方"令 15 个 seed 全部 0→1；旋钮转 1-1.8 rad）；stove_p4_r2 15/15（51 靠 v2 的"远离灶面 mask+排除手点"、56/58/59 靠 v3 双模方向特征翻转）；
  mokastove_p5_r1 15/15（51 靠 z=0.02 赤道夹持从 0 变 1，其余 14 个 seed 同字节通过；1 版）；stove_p3_r1 10/10（翻转 55、57-65；"在拨片半径处而非 +2 cm 处扫"是关键，seed 56 +2 cm 即漏）；
  stove_p3_r2 6/6（整条谱系 12/15 通过：钩扫+累计 1.2 rad 终止规则令 54/55/57/58/62-65 翻转）；mokastove_p6b_r1 3/15（旋钮段 findings 称稳；失败的 12 个 seed 都在放锅，不能当旋钮证据）。
- 依赖 API：get_observation、segment_sam3_text_prompt（"black knob" 0.36-0.85 最可靠；p3_r2 的真实旋钮分数只有 0.14-0.25，所以用了 0.08 的下限）、mask_to_world_points、goto_pose、open/close_gripper；
  `world` 依赖：p4_r2 的 lever_azimuth/cumulative_rotation、p3_r1/p3_r2 的 world.plan_motion 与 goal_plan(DIR,SPAN,STEPS)；p4_r1b 与 mokastove_p5_r1 的几何与响应判断都能在 policy 里完成，不依赖 world 做决策。`z_approach=` 与 `rotation_matrix_to_quaternion`、`segment_sam3_point_prompt` 为非清单全局。
- 子目标验证：有，但验证的是"拨片转了多少"，不是"灶开了"（见 A6）。信号：拨片角(或其二倍角)的前后差，来自抬起/退开后的重观测；夹持用 `robot_cartesian_pos[7]` 开度(>0.05 视为夹到，空夹≈0.015)。
  开发注意：夹爪贴着旋钮时 SAM3 常常 0 个 mask 甚至把夹爪认作 "black knob"(ztop=0.34)，所以必须抬起再测并做"离上次可信位置 <0.07 xy/0.06 z"的关联门。
- 任务耦合：转向 + 号(逆时针)；`r_tip−0.007`、`ztop−0.018/−0.024`、`HOVER_CLEAR 0.10`、`TWIST_STEP 0.30`、`BOX_SIDE_RANGE (12,140)`px、`MIN_SCORE 0.25`；p4_r2 的 `0.04≤ext≤0.18, 0.02≤hspan≤0.10` 与 `g_xy=0.4*base+0.6*tip`；
  mokastove_p6b 硬编码拨片相对旋钮中心的偏移 `(−0.05,+0.03)`；p3_r2 的 z 带 0.0105/0.039/0.048(桌面/盘顶/杆顶，只对该资产成立)；"开"所需转角在各 cell 不同：约 35-40°(p4_r2)、≥1.0-1.2 rad(p3_r2，seed 65 转 0.97 rad 仍冷)、150°(mokastove_p5)、360°(mokastove_p6b)。
- 参数化：sign、total_rad、响应阈值、几何高度必须来自测量或参数；"开"的判据不能用转角，应用 A6 的灶面红度。前置检查：
```python
def knob_precondition(geom, stove_c, d_stove=(0.03, 0.30), ztop_stove_gap=0.11):
    return d_stove[0] <= np.hypot(geom["cx"]-stove_c[0], geom["cy"]-stove_c[1]) <= d_stove[1]   # 旋钮必须在灶面附近，排除夹爪/管件误检
```
- 风险：(1) 同一 KITCHEN_SCENE3 里闭爪钩扫在 mokastove_p4n_r1 是 0/12(约 26 次真实 trial 灶面红度为 0.0，仅 seed 53 一次点亮(0.091)且无法复现，探针测得有效半径在轮毂附近 0.022 m、拨片尖处 0.05 m 会漏，推的方向随拨片相位翻转)，
  而"夹穹顶赤道+转腕"在 mokastove_p5_r1 是 15/15——同一原语类在不同场景上不可迁移，库里要按场景选模式；(2) p3 期 worker 说"夹取在物理上够不着"，但 p4/p5 的夹取 15/15，同一 api_sha，说明 p3 的可达性结论不可靠；
  (3) 转角够≠灶亮：p4_r2 findings 记录 world done() 对 15 个 seed 中 13 个假阴性(目标阈值 60° vs 实测开启角 ≈35-40°)；(4) 与 m4-r1 的关系：其中的小步 goto 与 TCP 偏置补偿被取代，保留几何与验证部分。非噪声/过拟合集合来源。
- 覆盖的失败类型：articulated"灶没点亮"(taxonomy 30 条里的灶)；不能覆盖：需要同时保持"先转旋钮再放锅"的复合任务排序(mokastove_p6b 的发现：先转后放，且放锅作为最后一次公共调用，才不会被 terminated-episode 误判)。
- 强度：6 个独立 cell，其中 3 个 15/15 → 强（但在 KITCHEN_SCENE3 上只有 mokastove_p5_r1 一个 15/15 样本）。
### A6 设备状态检查（灶圈红度；旋钮/抽屉响应另见 A5/A3）——只有一个弱样本，主要是缺口

- 功能与接口草案：用 RGB 判断灶圈是否发红(点亮)：灶面 mask 内"红通道显著高于绿/蓝"的像素占比，与操作前基线比较，给出 on/off/unknown。
```python
def burner_red_fraction(rgb, stove_mask, r_minus_gb=40, r_min=110): ...        # float in [0,1]
def stove_state(rgb, stove_mask, baseline_red=0.0, on_abs=0.05, on_delta=0.03, min_mask_px=2000, min_vis_frac=0.5): ...
#   -> {"status": "on"|"off"|"unknown", "red_frac": float}   # 手臂遮住灶面时返回 unknown，不是 off
```
- 出处：唯一代码实现 = mokastove_p4n_r1（不在 33 个 dev≥1 通过的 cell 内；该 cell 0/12）的 fix_code.py L196-210（`red=(r-max(g,b)>40)&(r>110)`，对 stove mask 求均值）与 fix_world_program.py L35-37、L240-253（`ON_RED_ABS=0.05`，`ON_RED_DELTA=0.03` 超基线）。
  路径 …/code-world-hardest-p4n-20261018/mokastove_p4n_r1/outputs/libero_fix_loop/libero_10_swap/KITCHEN_SCENE3_turn_on_the_stove_and_put_the_moka_pot_on_it/fix_code.py（sha256 前 12 位 572264a6fb44）。33 个通过 cell 的 policy 里没有任何一个计算灶面红度（grep 红/glow/burner/coil/hot：只有注释与 "burner" 当 SAM3 提示词）。
- 独立重写：0 个（通过的程序里）；弱实现 1 个（上面那个）。物理信号本身有三处独立的开发观察：stove_p3_r2 findings（seed 65 转 0.97 rad 灶仍冷，"成功状态点亮红圈"，据此把终止规则改为累计 ≥1.2 rad）、
  mokastove_p5_r1 findings（15 个 seed 关键帧里灶圈变红）、mokastove_p4n_r1 findings（红度 0.0→0.091，只有 seed 53 一次点亮）。三者都靠人看关键帧或该弱实现，不是通过程序的自检。
- 开发期证据：mokastove_p4n_r1 0/12（该阈值的标定来自 1 次点亮事件；fix_world_program.py 注释写"未点亮 0.0(51/52)、点亮约 0.2(53)"，而 findings 写 0.091——两处数值不一致，阈值 0.05 的依据薄弱）。
  相关的"state 判定被 world 判错"：stove_p4_r2 的 world done() 对 15 个 seed 里 13 个假阴性(目标阈值 60° vs 实际开启角 ≈35-40°)；stove_p3_r2 的 `goal_delta` 1.6 同样假阴性，后改 0.6。
- 依赖 API：get_observation（rgb）、segment_sam3_text_prompt（"stove"/"burner"，灶面 mask，分数 0.85-0.91 稳定）；纯函数，不需要 `world`（弱实现把 red_frac 写进 world.update 的 stove 实体，但计算本身在 policy 内）。
- 子目标验证：这张卡就是验证。目前缺口：没有任何 dev 通过程序用它；A5 的 6 个旋钮程序都只验证拨片角度，而 p4_r2 记录了角度阈值与任务判据不一致(假阴性)。必须内置：操作前取基线、操作后在手臂已退出视野的位姿取样、取样至少 2 帧。
- 任务耦合：红色判据 `R−max(G,B)>40 and R>110` 对渲染光照/材质敏感；灶面 mask 里若有红色物件(goal_swap 场景有"红边盘子"，锅/碗颜色因任务而异)会假阳性；阈值 0.05/0.03 来自单次事件。
- 参数化：r_minus_gb、r_min、on_abs、on_delta、基线都为参数；前置检查：mask 面积 ≥ min_mask_px 且被手臂遮挡比例低(p4_r2 的做法：撤到空白位姿(+0.16,−0.26,0.12)再感知)：
```python
def stove_view_ok(stove_mask, hand_xyz, stove_pts, min_px=2000, clear_r=0.10):
    return stove_mask.sum() >= min_px and np.min(np.linalg.norm(stove_pts - hand_xyz, axis=1)) > clear_r
```
- 风险：阈值在 12 个 seed 上没有被任何通过的程序检验，可能是"过拟合到 1 个事件"；红圈出现可能滞后于旋钮转动几步(未测)；最稳妥的做法是用 dev 通过 cell（stove_p4_r2、mokastove_p5_r1）的开发关键帧离线标定红度分布，这属只读操作，但本次未做。与 m4-r1 无冲突。
- 覆盖的失败类型：articulated"灶没点亮"的**判定**部分(taxonomy 30 条里的灶，让程序知道没亮就再转，而不是凭转角)；不能覆盖：真正让旋钮转到位(A5)和"转完立即终止回合"时的终止安全。
- 强度：弱（0 个通过程序实现，1 个 0/12 的单事件标定）→ 列入缺口。
### A7 铰链门/微波炉（无正面证据：只描述缺口）

- 功能与接口草案（设计目标，没有可搬的 exemplar）：测门的铰链/自由端，在门板平面外垂直按压到闭合线再滑过 4 cm，每次动作后用门的像素跨度/角点判断是否已闭合并立刻停止动作。
```python
def door_geometry(obs, prompts=("microwave",), body_cam_dist=(0.0, 1.0), door_cam_dist=(1.0, 1.35)): ...   # 按相机距离把点云分成机身/门/背景
def close_hinged_door(hinge_xy, free_edge_xy, panel_top_z, *, press_frac=0.7, overdrive=0.04, z_press=None, stop_after_closed=True): ...
def door_closed(px_span, open_ref_span, corner_to_flush_m, z_min=0.05, d_min=0.97): ...                      # 见下方验证信号
```
- 出处：无通过的程序。33 个 dev≥1 通过的 cell 里没有微波炉/铰链门代码（signature `door_hinge` 只命中 stove_p3_r1，是旋钮"hinge"一词的误报）。
  只有 0 通过的 cell 的 findings 可作参考：mugmicrowave_p4n_r1（0/15）、mugmicrowave_p5_r1、mugmicrowave_p6_r1（seed 51 经 56 次付费尝试仍堵死）、mugmicrowave_p6b_r1(l10)、mugtaskmicrowave_p6b_r1。
- 独立重写：0 个。
- 开发期证据（仅作缺口说明，不当正面证据）：任务判据=杯子在腔内 且 门已闭合，门初始为打开。
  闭门的三种互相矛盾的说法：p4n"垂直于门板平面在 0.7·L 处沿闭合切线按压，再滑过闭合线 +4 cm，门在 52/55/56/59/61/62/65 上闭合"；
  p6b-l10"尖端按压弧 a=(door−0.02,−0.02,0.125)→b=(door+0.26,+0.065,0.125)，门闭合在 27 次运行里 13 次；`z>0.05 且 d>0.97` 的门控滤除了底边细条的假闭合"；
  p5_r1"门板+铰链组件在约 15 种推法下姿态不变，自由端在 IK y 钳位(−0.5)之外，没有可用原语"，p6_r1"门板顶 0.20 m 而可达指尖下限 0.195-0.475，门无法操作"。
  塞杯子是另一个堵点：p4n/p6 都测得指尖可达下限高于入口上沿，p6b-l10 用 45° 倾斜闭爪鼻 (z 0.24→0.05) 把杯子座入，但门阶段把杯子"挤出"3/3。
- 依赖 API：get_observation（含手腕相机，p6 用手腕相机建腔体占据栅格）、segment_sam3_text_prompt("microwave"：mask 会并入背景柜子，需按相机距离切分)、mask_to_world_points、goto_pose；`world` 里的 "contained" 谓词曾是假阳性(p6b-l10：杯子钩在边缘就判在内，窗口应要求中心越过口平面 3.5 cm)。
- 子目标验证信号（来自 findings，未被通过程序验证）：门像素跨度跌破"滚动最大开启参照"或角点到闭合线、门掩膜用 |门中心−机身中心| 与 y≤−0.33 过滤机身角/柜子假命中(p5)、不要在杯子被遮挡后用"看不见杯子"作后续动作的前置条件(mugtask_p6b)。
- 任务耦合：全部几何都是 KITCHEN_SCENE6 专有，且各 worker 记录的铰链/口平面坐标互相不一致(p4n：铰链≈(0.50,−0.35)，口平面 x≈0.44；p6：口在南面 y=−0.40，铰链(0.29,−0.40)；l10：口 x∈[0.354,0.534])——不能作常量进库。
- 风险：成功即终止回合(p4n seed 58 把"放好+闭门"同时达成却被记 world_program_error)；步数上限约 180 次 API 调用；物理可达下限/IK 钳位(−0.5)是硬约束。
- 覆盖的失败类型：本组 taxonomy 里没有微波炉行，但属于"articulated 门未闭"；现有证据**不足以**入库，需要 M4 级原语(带可达性探针的受控按压)后再做。
- 强度：无 → 缺口。

## 本组小结
- 强（>=3 个独立 cell 且有 12+/15 或 15/15 程序）：A2 前向竖爪夹持拉抽屉（7 个独立 cell，4 个 15/15）、A1 把手定位（12 个独立 cell，4 个 15/15，但"序数身份"多藏在 world 里，纯 policy 层可搬的只有 2 个 cell）、
  A3 抽屉开度验证（9 个独立 cell，多个 15/15；阈值需任务级标定，且多半经 world 事实）、A5 旋钮弧线旋转（6 个独立 cell，3 个 15/15；在 KITCHEN_SCENE3 上只有 mokastove_p5_r1 一个 15/15 样本）。
- 弱/可疑：A4 抽屉关闭推送（2 个 cell，最高 6/15，两个 cell 对顶视/水平推给出相反结论，通过 seed 仅 3-6 个；只留"实测 flush 阈值+每推一次门控+终止安全"三条规则）；
  A6 灶圈红度（通过的程序里 0 个实现，唯一实现来自 0/12 的 mokastove_p4n_r1，阈值只靠 1 次点亮事件，且 0.091 与 ~0.2 两处记录不一致）。
- 缺口：A7 微波炉/铰链门——无任何通过程序；4 个 cell 对门几何与闭门可行性互相矛盾(门闭合 13/27 vs 完全不动)，且卡在指尖可达下限，需要 M4 级带可达性探针的原语。抽屉"关严"同样缺稳定原语。
- 互相矛盾、需主汇总者判定：(1) 前向抓取四元数的指轴描述（drawer_p6_r1 注释说沿 X，其余 3 个说竖直；旋转数学上是竖直）；(2) bowldrawer_p5_r1 说把手竖直，其余全说水平；
  (3) 同任务 bowldrawer 上 p6b"只有倾斜钩子可行"vs p6_r1 前向竖爪 15/15；(4) 同一 KITCHEN_SCENE3 的闭爪钩扫 0/12 vs 夹穹顶转腕 15/15；(5) TCP/手腕偏置在各 cell 测得 0.100-0.115 不等，不能写死。
- 与 m4-r1 的关系：A2/A5 中的"两次相同 goto 稳定 IK"、"<=0.06-0.075 m 分段"、"TCP 偏置补偿"、"reach() 闭环伺服"（bowldrawer_p5_r1）属 superseded by m4-r1，只保留开度/位移/拨片角的验证逻辑；A1/A3/A6 的感知与验证不受影响。
- 跨簇通用候选（本组频繁出现，建议另立卡）：终止安全包装 `safe(fn)`（drawer_p5_r2、drawer_p6b_r1、stove_p3_r2、bowldrawerclose_p6b_r1、mugmicrowave_p4n 各自独立写过）；"让可能越过成功阈值的那一步成为最后一个动作"。
- 噪声/过拟合嫌疑：本组没有 objtaskmilk / spatialtaskcookie 来源的 helper；但 drawer_p5_r2 的 12/15 只是 9 个版本里的最大值（且 51/52/64 在选定包上仍失败），bowldrawer_p5_r1 是 best_swept_unselected，引用时不要当"已选定包"。
