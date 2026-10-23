# CC_TASK_13 方法层面诊断（2026-10-22）

范围：p5、r3、p6b、ns、l10 五个按 ASPIRE 预算跑的结果根（30 个 cell），只读。
GPU 回放走 top 池 DLC 作业 `dlcy9kjkbyp99mhb`：协调端 10-22 14:15 提交，14:16–17:08 运行，Succeeded，
23 个作业全部 rc=0，345 次 trial。结果在 `data/replay_analysis.json`，已补进第 3、6 节。
held-out 只用了汇总、逐种子成败和第 0 步观测（布局），没有看 held-out 的轨迹/视频。
数据与脚本都在本目录 `data/`、`scripts/`；逐 cell 表在 `q2_table.md`。

## 结论（三条主要瓶颈）

1. **开发期分数被"单次抽样 + 多版本取最大"系统性高估，这是 `_task` 子集落差的主因，而不是评测通路或种子分布。**
   - 证据：只跑出 1 个版本、首轮就 15/15 的 cell，held-out 与开发分差平均 +0.017；跑了 ≥3 个版本的 cell 平均差 +0.11。
     其中 objtaskmilk 差 +0.43（版本分 9,1,8,2,8,13,6,7,4，冻结 13/15，held-out 0.44 ≈ 版本均值 0.43），
     spatialtaskcookie 差 +0.30。
   - objtaskmilk 的 policy 调 `plan_grasp`，GraspNet 用未设种子的 `np.random.choice` 下采样点云
     （`contact_graspnet_pytorch/data.py:204`），在长驻服务进程里，同一代码、同一种子的结果不可复现。
     DSW 预演里冻结版本在 seed 51（开发期通过）上重跑失败，轨迹在 plan_grasp 之后分叉。
   - spatialtaskcookie 是确定性的，但它的 rim-pinch 抓取处于临界，代码小改就让结果在种子间来回翻转。
   - 种子分布没有差异：两个 `_task` cell 的布局置换检验 p 值分别为 0.65 和 0.77。
   - DLC 回放证实了上面三点：
     - objtaskmilk 冻结版本在开发种子上重跑两次都是 8/15（账本记 13/15），两次重跑逐种子只有 9/15 一致；
       unseen 7/15、8/15；held-out 0.44。
     - spatialtaskcookie 冻结版本在开发种子上两次都复现 12/15（15/15 一致），unseen 两次都是 7/15，held-out 0.50。
       它的 v0、v1 在 unseen 上是 9/15，反而好于冻结版本。
     - 对照组 drawer、bowldrawer 的开发分完全复现，所以评测通路没有问题。
2. **策略不验证子目标、world 判据不准，铰接物体操作和放置/释放是开发期失败的大头。code world 没有在执行前拦下任何一次失败。**
   - 证据：冻结版本在开发种子上的 58 次失败里，30 次是铰接物体操作（炉子没点着、抽屉没推到位），17 次是放置/释放。
     world 在执行前标出 0/58，policy 自己察觉 15/58。
   - 离线 screening 在 1240 次里从未给出"预测失败"。预测 goal=true 的候选首试通过率 5.5%，而基线是 3.3%。
     prediction mismatch 在同一 cell 内的 AUC 只有 0.48。
   - world 的 judgment 说"完成"时，repair 阶段的精度只有 32%。
3. **"每个任务只跑一个 worker"本身就是大的损失来源；另有一批损失来自 pipeline 而不是方法。**
   - 同一 pipeline 下同任务的独立重复共 26 对，held-out 差的绝对值平均 0.36，42% 的对差 ≥0.3，两次取好的平均增益 +0.18。
     例如 mokastove：p5 0.94，p6b 0.28。
   - 在 ASPIRE 预算的几轮里，冻结版本的开发分 4/4 次都正确排出了同任务独立运行的先后。
   - pipeline 损失来自三类问题：G5 上下文溢出、5 MiB 续接失败、G6 存关键帧时被看门狗截断。
     它们让 10 个 cell 没出分，另有 spatialtaskcookie 提前 14.3 h 结束开发、l10 mugmicrowave 空转 5.45 h，详见第 3 节。

## 1. 方法改动建议（按预期收益排序，均与具体任务无关）

### M1 每个任务 K 个独立 cell，用 unseen 66–80 选择交付版本（不改 solver）
- 改什么：未达标任务各开 K=3 个全新 cell（不同 worker、不共享账本），各自按现行规则冻结。
  然后在 unseen 66–80 上各跑一次冻结版本，取 unseen 通过数最高的去跑 held-out；平手时看开发分。
  unseen 只用于选择，不回传给 worker。
- 证据：
  - 回放中，冻结版本的 unseen 分（7–8/15、7/15）与 held-out 的差在 0.03–0.09（开发分则高出 0.30–0.43）。
    spatialtaskcookie 若按 unseen 选，会选到 9/15 的 v0/v1，而不是 7/15 的冻结版本。
  - 同 pipeline 的 26 对独立重复，held-out 差的绝对值平均 0.36，两次取好的平均增益 +0.18（`data/run_variance.json`）。
  - 在 ASPIRE 预算的几轮里，冻结开发分 4/4 正确排序了 mokastove、drawer、bowldrawer、bowldrawerclose 的独立运行。
- 影响：bowldrawer、mokastove、goaltask、objtaskmilk、spatialtaskcookie、bowldrawerclose。这些任务都有冻结版本，但分数在中段。
- 成本：K 倍的开发算力（每个 cell 约 10–15 h、150–350M 输入 token），另加每个 cell 15 次 unseen trial。
- 风险：15 个种子的 unseen 仍有约 ±0.12 的标准误；对一直拿不到首次成功的任务（mug、twomoka）无效。
- 检验：比较"按 unseen 选出的 cell 的 held-out"与"同批 K 个 cell 的 held-out 均值"，以及与目前最好分数。

### M2 选择与冻结规则去掉乐观偏差
- 改什么：
  - (a) 版本分只算 sweep 行，不把"修复到目标种子通过"那次 repair trial 计入。
  - (b) 版本分在统计噪声内（例如差 ≤2 个种子）时，不按最大值选，改选更早、更简单或已在更多种子上复测的版本。
  - (c) 冻结前把候选在 15 个开发种子上再跑一遍；随机性策略取两次的合并通过数。
  - (d) ASPIRE 的"交付前在 unseen 66–80 测一次"补进流程，并把开发分与 unseen 分之差写进结果。
- 证据：多版本 cell 的开发分比 held-out 平均高 +0.11，单版本 cell 只高 +0.017（`data/selection_bias.json`）。
  objtaskmilk 版本间每对相邻版本有 7–10/15 个种子翻转，61% 的翻转落在非修复目标种子上（`data/version_matrix_task_cells.json`）。
- 影响：主要让开发分变得可信，从而让 M1、M3 的判断不被噪声误导；单靠它不直接提高 held-out。
- 成本：每个冻结候选多 15–30 次 trial；改选择规则属于方法改动。
- 风险：会降低报出来的开发分，看起来"变差"。
- 检验：在下一轮所有 cell 上记录"冻结开发分 − held-out"，目标是平均差 ≤0.05（现在是 +0.11）。

### M3 子目标验证契约，以及 world 判据对照真实结果的校准
- 改什么：
  - (a) worker prompt 与 skill 要求每个子目标（抓起、开/关、放下、开关状态）用观测确认后才继续；未确认就换策略重试。
    达成后立即停止发动作，避免 terminated episode。
  - (b) 开发期（oracle gate）每条 trial 结束后，把 world 自写的 goal 判据与模拟器真实结果的混淆情况反馈给 worker。
    冻结前要求判据在开发 trial 上的精度达到阈值。
  - (c) 执行前的 screening 与逐指令 prediction 不再作为门槛，只保留事后判定。
- 证据：
  - 58 次开发期失败中，world 执行前标出 0 次，policy 察觉 15 次，而且多数察觉了仍继续执行（`data/failure_summary.json`）。
  - moka 的 12 次失败都是炉子没点着：失败帧里线圈是灰的，通过帧里发红光（`data/failures_l10.json`）。
    worker 却认定"ON 没有公开信号"，所以从不检查。
  - bowldrawerclose 与 goaltask 的 world 判据把"没推到位的抽屉""斜立在抽屉里的物体"判为成功。
  - world_program_error 中有 46 次其实是模拟器成功（reward 1.0），因为策略在任务完成后还在发动作（`data/world_use_stats.json`）。
  - screening 从未预测失败；mismatch 在同一 cell 内 AUC 0.48；60–70% 的预测被 superseded，从未判定。
- 影响：mokastove、bowldrawerclose、goaltask、bowldrawer（铰接与放置类失败占 47/58）；mug 系列的判据假阳性。
- 成本：改 prompt/skill 与开发期反馈，不改 API；开发期 token 可能下降（去掉无效的 prediction 负担）。
- 风险：Qwen 未必能稳定执行"验证后再继续"；需要给出可用观测的通用示例（颜色、位移、深度），不能写成某个任务的答案。
- 检验：bowldrawerclose、mokastove、goaltask 各 3 个 cell（用 M1 设计），比较冻结开发分和 held-out 与现有最好分数（0.20、0.94、0.40）。

### M4 底层原语与 API 修正（非特权）
- 改什么：
  - (a) `solve_ik` 把目标静默截到 x≤0.75（`cap/integrations/franka/libero_reduced.py:383`），还会静默换用其他姿态。
    改为截断或换姿态时返回或打印实际使用的目标与姿态。
  - (b) 提供分段 goto（几乎每个 worker 都重写了"glide"：单次 goto 超过 7–8 cm 就停在半路）。
  - (c) 提供力或位移受限的推/拉原语，用于抽屉、门的闭合。
  - (d) GraspNet 按请求内容设种子，让同输入同输出，开发期失败可以复现。
- 证据：
  - twomoka 三次运行都卡在第二个壶，worker 记录炉面 x 在 0.79–0.99，超出截断范围。炉面位置是 worker 的结论，未独立核实；截断本身已核实。
  - bowldrawerclose 约 16 种关门策略都推不动面板。
  - objtaskmilk 同代码 6–13/15 的波动。
- 影响：twomoka、mugmicrowave、bowldrawerclose 拿不到首次成功或卡在关门；objtaskmilk 一类调 plan_grasp 的任务。
- 成本：改 API 层与服务，要回归测试；改了 API 后与 ASPIRE 的可比性要标注。
- 风险：(d) 只消除方差、不提高真实成功率；(a) 会改变已有策略的行为。
- 检验：twomoka、mugmicrowave 各 3 个 cell，看能否拿到首次成功；objtaskmilk 冻结版本在 51–65 上重复两次应一致。

### M5 跨任务 skill 库（ASPIRE 式，在 M1–M3 之后）
- 改什么：从已解出的 cell 抽取通用 helper 进库（读 task_language、SAM3 过滤与身份关联、glide、rim-pinch、带偏移补偿的释放、子目标验证），
  worker 必须先查库。晋升前由 verifier 在 dev 51–55 上用库重解。
- 证据：同一任务两次运行，阶段 A 首过所需差异巨大，mokastove p5 只要 6 次 trial，p6b 要 97 次（9.8 h）。
  多数 cell 都重新发明了同样的 helper，每个 cell 花 150–680M 输入 token。
- 影响：缩短阶段 A，降低运行间方差；对 API 层面的限制无效。
- 成本：中等（库的管理与晋升流程）；有把错误 helper 扩散到多任务的风险。
- 检验：同一批任务，有库与无库各 K=3，比较首次通过 51 的 trial 数与 held-out。

## 2. 下一轮实验设计（建议）

前提：CC_TASK_12 的 G5/G6 修复（p6c driver）已就位，否则 pipeline 损失会盖过方法差异。

| 臂 | 内容 | cell |
|---|---|---|
| A（只改选择） | 现行方法 + M1（K=3，按 unseen 选）+ M2(a)(d) | 每个任务 3 个 |
| B（方法） | A + M3（子目标验证、判据校准反馈、screening 只作事后判定） | 每个任务 3 个 |
| C（原语，可选） | B + M4(a)(b)(c)，只用于从未出分的 twomoka、mugmicrowave | 每个任务 3 个 |

- 任务（未达 0.95 的）：bowldrawer（最好 0.82）、bowldrawerclose（0.20）、goaltaskbowldrawer（0.40）、objtaskmilk（0.44）、
  spatialtaskcookie（0.50）、mokastove（p5 0.94，p6b 0.28）；C 臂另加 twomoka、mugmicrowave（另有 mugtaskmicrowave 在跑）。
- 规模：6 个任务 × 2 臂 × 3 = 36 个 cell；按每个 8 卡节点 4 个 cell 算，需要 9 个节点次。
  可以先只跑 A 臂（18 个 cell）验证 M1/M2 的价值，再决定 B。
- 主要比较（预先写定）：
  - 每个任务"按 unseen 选出的 cell"的 held-out vs 目前最好分数。
  - 同臂 K 个 cell 的 held-out 均值与离散度，用来估计单 worker 的方差。
  - 冻结开发分 − held-out 的平均差：A 臂目标 ≤0.05，现状是 +0.11。
  - B vs A：同任务 held-out 均值差，以及首次通过 51 的 trial 数。
- held-out 依旧只当分数；unseen 只用于选择，不给 worker 看。

## 3. 损失来源：pipeline 还是方法

pipeline 部分引自 CC_TASK_12 普查（`../code-world-repair-parallel-p6-20261020/CC_TASK_12_HANDOFF.json` 的 census）与 Task 9。
"方法状态"一列取自停止时的账本与 `data/phaseA_never_passed.json`。

| cell | pipeline 原因 | 损失 | 停止时的方法状态 |
|---|---|---|---|
| p5 drawer_r1 | G5 上下文溢出（3.70 h） | 4.0 h 被 blocked，约 20 h 未用，无分 | 阶段 A 27 次未过 51；同任务 r3 1.00、p6b 0.98 |
| p5 twomoka_r1 | G5（5.49 h） | 5.8 h blocked，约 18 h 未用，无分 | 39 次未过 51；三次运行都卡在第二个壶 |
| p5 goaltask_r1 | G5 + 5 MiB 续接失败 | 10.5 h blocked，无分 | **已有 13/15 的选中候选**，是 pipeline 损失里最大的一项 |
| p5 objswapsoup_r1 | G6 存关键帧被截断 | 19.1 h blocked，无分 | 最好 4/15；同任务 r3 1.00 |
| r3 bowldrawerclose、drawer_r2、goaltask、mugmicrowave、twomoka | 5 MiB 续接失败，driver 空转到 24 h | 均无分 | 都未过 51（11–55 次） |
| ns spatialtaskcookie | G6（seed 55 attempt 7 在 882.9 s 被截断） | 9.67 h 结束开发，14.3 h 未用 | 冻结 12/15，held-out 0.50；v6 已写好但没能执行 |
| l10 twomoka | G6（attempt 19 在 891.1 s 被截断） | 5.1 h blocked，18.9 h 未用，无分 | 第一个壶已上炉，第二个从未成功 |
| l10 mugmicrowave | G5，续接被接受但立刻失败 | 空转 5.45 h | 仍在跑，未过 51 |
| p5 bowldrawer_r1、p5 mugmicrowave_r1 | 无（solver 跑满 24 h 截止） | 无分 | bowldrawer 最好 9/15 但没收尾；mug 99 次未过 51 |

判断：
- **纯 pipeline 损失**：p5 goaltask（13/15 候选没出分）与 p5 objswapsoup（同任务 r3 已 1.00）。
  修好 G5/G6 后，这类损失应当消失。
- **pipeline 放大了方法问题，但不是根因**：twomoka（p5、r3、l10）、mugmicrowave（r3、l10）、drawer_r1/r2、
  r3 的 bowldrawerclose 与 goaltask。停的时候都还没拿到首次成功；同族运行即使跑满 24 h 也没过（p5 mug 99 次），
  所以多给时间未必出分。
- **spatialtaskcookie 的 14.3 h 未用**：按第 1 节结论，多跑版本主要抬高开发期最大值。held-out 的提升取决于 v6 的
  真实成功率能否高于版本均值（约 0.63）。
  回放：6 个版本的 unseen 成绩为 9,9,7,5,7(冻结),7，没有一个超过 9/15（0.60）。
  所以多出的 14.3 h 在现行选择规则下多半只是再加几个版本，held-out 预计仍在 0.5–0.6，到不了 0.95。
  这条 pipeline 损失对最终分数的影响小，方法问题才是主因。
- **方法本身的损失**（pipeline 正常走完、仍不够高）：bowldrawerclose p5 0.20 / p6b 0.08、mokastove p6b 0.28、
  goaltask p6b 0.40、objtaskmilk 0.44、spatialtaskcookie 0.50、bowldrawer 0.80/0.82，以及 drawer p5_r2 0.66、
  mokastove p5→p6b 的运行间落差。

## 4. Q2 方法瓶颈

逐 cell 的完整表在 `q2_table.md`，由 `scripts/extract_cells.py` 生成 `data/cells.json` 后用 `scripts/q2_table.py` 渲染。
表中各列为：阶段 A 到首过 51 的 trial 数与小时、版本数、各版本开发通过数、B 阶段修复模式、被封模式、冻结开发分、
held-out、开发小时、token。

### 4.1 冻结版本在开发种子上的失败分类（58 次，`data/failure_summary.json`）

| cell | 失败 | 主因 |
|---|---|---|
| mokastove_p6b | 12 | 铰接 12：炉子没点着（失败帧线圈灰色，通过帧发红光），policy 从不检查 |
| bowldrawerclose_p5 | 9 | 铰接 8：抽屉差 1–3 cm 没关上，或反被拉开；未知 1 |
| bowldrawerclose_p6b | 12 | 铰接 8：碗沿卡住，面板停在差 1.5–6 cm 处；放置 4：碗斜搭在抽屉壁上 |
| goaltask_p6b | 10 | 放置/释放 10：开环松手，奶酪盒立着或斜着，有一次滑出 |
| goaltask_p5 | 2 | 铰接 2：rake 没勾住，policy 打了 WARN 仍继续 |
| bowldrawer_p6b | 5 | 感知 3：SAM3 "handle" 给出悬空误检，耗尽 4000 步；放置 2：碗斜搭在抽屉前沿 |
| drawer_p5_r2 | 3 | 抓取 2：再抓时抓空；感知 1 |
| objtaskmilk | 2 | 放置 1、抓取 1 |
| spatialtaskcookie | 3 | 搬运掉落 2、抓取 1 |
| 合计 | 58 | 铰接 30、放置/释放 17、感知 4、抓取 4、掉落 2、未知 1 |

- world 在执行前标出 0/58。
- policy 察觉 15/58，其中多数察觉后照样继续（"drawer not open enough" 后仍放物体）。
- world 判据把 4 个斜搭的碗、1 个未关到位的抽屉判为成功。

### 4.2 损失发生在哪一段

| 段 | cell | 根子 |
|---|---|---|
| 拿不到首次成功 | mugmicrowave（p5/r3/l10，99/49/117 次）、mugtaskmicrowave（62 次，在跑）、twomoka（p5/r3/l10） | 原语/API + 感知 + 搜索浪费 |
| 拿到了但推不开 | bowldrawerclose（6/15、3/15）、mokastove p6b（3/15）、goaltask p6b（5/15）、bowldrawer p6b（10/15） | 策略不验证、world 判据不准、铰接与放置原语 |
| 开发期到 held-out 掉分 | objtaskmilk、spatialtaskcookie、drawer p5_r2、bowldrawer r3、bowldrawerclose p5 | 选择规则 + 随机性/临界性（Q1） |

**拿不到首次成功**（`data/phaseA_never_passed.json`）：
- mugmicrowave：抓取与搬运很快就解决了，卡在"放进去并关门"。
  - p6b 里杯子进去过、门也关上过（17 次），但从未在同一次里两者都成立；关门会把杯子推出来，或 4000 步截断。
  - 各运行对微波炉开口朝向的判断互相矛盾，是感知/场景理解问题。
  - world 的 mug_inside 判据多次假阳性。
- twomoka：三次运行都能把第一个壶放上炉，第二个从未成功（盖子更高，抓取深度不对，常被碰倒）。
  `solve_ik` 静默截断 x≤0.75 并静默换姿态，worker 称之为"IK 抽奖"。
- 判断：同族运行失败一致，不是随机的。根子是原语/API 限制（IK 截断、无力控、步数上限），
  加上搜索早期 20–40 次花在错误的几何模型上。p6b mug 证明给足次数时两个子目标都能单独达到。

**拿到了但推不开**：
- 修复版本基本不改变通过数：goaltask 是 4,4,4,4,5,5,5；bowldrawerclose p6b 是 3,0,0,0,3,0,3；mokastove 是 1,3,3,3,3。
- worker 给失败模式起的名字常常不对。mokastove 叫 "delivery_slip"，实际是炉子没开；bowldrawer 说"碗留在桌上"，实际是斜搭在前沿。
  修复因此对不上真正的原因。
- 根子在 world 与预测契约：判据由 worker 自写，从未拿真实结果校准；执行前的 screening 只拦 Python 错误；
  逐指令预测 60–70% 被 superseded，同一 cell 内 mismatch 与成败无关（AUC 0.48）。

**开发期到 held-out 掉分**：见第 6 节 Q1。

### 4.3 code world 与预测契约（`data/world_use_stats.json`，30 个 cell、2928 行）

- screening：1240 次里拒掉 44 次，全是 authored Python 错误；从未预测物理失败。
  若只放行 goal=true 的候选，会挡掉 58% 实际通过的 trial。
- world judgment 对照模拟器（1163 次有判定，744 次 unknown）：判"假"时 99.2% 确实失败；
  repair 阶段判"真"时精度只有 32%。
- 13.6% 的计分行是 world_program_error，其中 306/317 是任务结束后仍发动作（"executing action in terminated episode"），
  46 次其实模拟器已成功。这类行占了 15.3% 的计费重试。held-out 受影响很小：只有 bowldrawer r3 有 1 个种子属于这种情况。
- 结论：执行前的那套机制基本是开销；有用的是事后判定，可以单独保留（M3(c)）。

### 4.4 预算与 token

- 已出分的 cell 开发 1–15 h，输入 token 23–360M。从未过 51 的 cell 输入 token 200–680M。
- 阶段 A 不限次，对 mug/twomoka 来说就是一直烧到 24 h 或卡在 pipeline。
  第 4.2 节的证据说明问题不在次数，而在原语与判据。建议阶段 A 加一个与任务无关的停损：
  例如连续 30 次没有新的子目标进展，就转去做原语诊断，而不是继续改策略。

## 5. Q3 运行间方差

数据：`data/run_variance.json`（`scripts/run_variance.py`），扫描所有 code-world 结果根的 held-out。
重复的定义：同一结果根（即同一 pipeline 版本）、同任务、名字只差 `_r<k>` 的 cell。

- 21 组、26 对独立重复：held-out 差的绝对值平均 0.36，中位数 0.24，42% 的对差 ≥0.3。
  例如 stove 0.0/1.0、bowldrawer 0.0/0.84、wine 0.56/0.02、drawer 0.66/0.0、stove 0.94/0.0。
- 两次取好的，比两次平均高 +0.18（平均）。
- 按 ASPIRE 预算的几轮（各轮之间只差 pipeline 修复，方法和预算相同）：

| 任务 | 各轮 held-out | 冻结开发分 |
|---|---|---|
| mokastove | p5 0.94，p6b 0.28 | 15/15，3/15 |
| drawer | p5 0.66，r3 1.00，p6b 0.98 | 12/15，15/15，15/15 |
| bowldrawer | r3 0.82，p6b 0.80 | 15/15，10/15 |
| bowldrawerclose | p5 0.20，p6b 0.08 | 6/15，3/15 |

- 冻结开发分在 4/4 个任务里正确排序了独立运行，虽然它对绝对水平偏乐观（第 6 节）。
- 在中等成功率的同一对之间，held-out 的逐种子相关很低（φ 0.05、0.20）：两次运行失败的不是同一批种子。
  这说明差异来自运行本身（找到哪种策略），而不是有固定的一批"难种子"。

判断：
- **"每个任务只跑一个 worker"是主要损失来源之一。** 单次运行的结果大致是在 0–1 之间的一次抽样，
  K 个独立运行加上可信的选择信号，期望收益在 +0.1 到 +0.2 量级。
- 开发分可以用来排序，但会偏高；unseen 66–80 是更干净的选择信号（它不参与修复，也不参与选版本）。
  所以 M1 采用"开发期冻结 + unseen 选择"。
- 代价是 K 倍开发算力。对已经 ≥0.95 的任务不必再跑。

## 6. Q1 `_task` 子集开发期到 held-out 的落差

objtaskmilk 开发 13/15 → held-out 0.44；spatialtaskcookie 开发 12/15 → held-out 0.50。

### (a) 评测通路
- 静态比对：held-out 与开发期用同一个 runtime 包装器（`scripts/libero/runtime/replay_trial.py`），
  同样的 policy 与 world 字节（sha 一致），`executable_world_config.json` 无差异。
  环境变量只差 `SNAPSHOT_DIR`，这个变量在 cap 与 replay_trial 里都没被读取。
  `ASPIRE_MAX_STEPS`=4000 两边相同；并行度与线程上限两边都走 `parallel_env`。
- 动态（DSW 预演，在协调端改为只用 DLC 之前跑过，见第 8 节）：用 held-out 的函数把冻结 v5 跑在 seed 51/52 上，
  结果是 51 失败（开发期通过）、52 通过。seed 51 的初始状态与开发期完全相同（牛奶位置一致到毫米），
  在 `plan_grasp` 返回之后 TCP 出现分叉（0.457 vs 0.375）。这是感知服务的随机性，不是通路不一致。
- DLC 回放（`data/replay_analysis.json` 的 frozen_dev），用 held-out 的函数在开发种子 51–65 上重跑冻结版本，与账本逐种子对比：

| cell | 账本开发分 | 回放 r1 | 回放 r2 | 与账本逐种子一致 |
|---|---|---|---|---|
| drawer_p6b（对照） | 15 | 15 | – | 15/15 |
| bowldrawer_p6b（对照） | 10 | 10 | – | 15/15（连 3 个 crash 都复现） |
| spatialtaskcookie | 12 | 12 | 12 | 15/15，15/15 |
| objtaskmilk | 13 | 8 | 8 | 6/15，8/15 |

- 结论：评测通路没有不一致（确定性的 policy 逐种子完全复现）。objtaskmilk 的差异来自 policy 自身的随机性，不是通路。
  证据强。

### (b) 泛化 / 选择
- objtaskmilk 的版本分为 9,1,8,2,8,13,6,7,4，冻结 13/15，版本均值 0.43，held-out 0.44。
  spatialtaskcookie 的版本分为 9,10,11,8,12,7，冻结 12/15，均值约 0.63，held-out 0.50。
- 在"各版本真实成功率相同"的零假设下，objtaskmilk 取到 ≥13 的概率是 0.006，所以 v5 在开发种子上可能确实偏好；
  但 held-out 回到了版本均值，说明这份"偏好"没有泛化。
- 相邻版本之间有 7–10/15 个种子翻转，61% 发生在非修复目标的种子上，而 v5→v8 的代码只改了 2–4 行。
- 跨所有 cell：多版本的 cell 冻结分比 held-out 平均高 +0.11，单版本的 cell 只高 +0.017。
  这不是 `_task` 子集特有的：drawer p5_r2（0.80→0.66）、bowldrawer r3（1.00→0.82）、bowldrawerclose p5（0.40→0.20）同样存在。
- 回放：全部版本在 unseen 66–80 上各跑一次，冻结版本跑两次。

| cell | 版本 | 开发分 | unseen 分 |
|---|---|---|---|
| objtaskmilk | v0–v8 | 9,1,8,2,8,**13**,6,7,4 | 6,2,5,1,6,**7/8**,7,8,7 |
| spatialtaskcookie | v0–v5 | 9,10,11,8,**12**,7 | 9,9,7,5,**7/7**,7 |

- 冻结版本的 unseen 分与 held-out 一致：objtaskmilk 7–8/15 对 0.44，spatialtaskcookie 7/15 对 0.50。
  开发分则偏高：13/15 对 0.44，12/15 对 0.50。
- 开发分与 unseen 分的相关：objtaskmilk 0.66，主要来自 v1、v3 两个明显坏掉的版本；去掉之后，各版本 unseen 都在 5–8。
  spatialtaskcookie 只有 0.21。
- 按开发分选：objtaskmilk 选中的冻结版本在 unseen 上处于中上（7.5，最好的是 8）；
  spatialtaskcookie 选中的冻结版本 unseen 是 7，而最早的 v0/v1 是 9。后面的修复在开发种子上加分，在 unseen 上减分，这是对 15 个开发布局过拟合。
- 结论：选择造成的乐观偏差有两种。
  - objtaskmilk 是噪声最大值：真实开发率约 8/15，13 是一次幸运抽样。
  - spatialtaskcookie 是确定性过拟合：12/15 在那 15 个布局上真实成立，但不泛化。
  证据强。

### (c) 种子分布
- 构造上：`LiberoHandle.reset` 先调 `env.seed(seed)` 再 `reset()`，之后
  `cap/envs/simulators/libero.py:166-171` 用 `init_states[(seed-1) % 50]` 调 `set_init_state`，然后又 `reset()` 一次。
  每个任务都只有 50 个 init state（`data/init_state_aliasing.json`），如果 init state 生效，51–65 应与 1–15 完全相同。
  实测并不相同：seed 51 与 seed 1 的第 0 步深度图有 3.3% 像素不同，而它与最近的 held-out 布局只差约 0.6–0.7%（`scripts/layout_match.py`）。
  可见第二次 `reset()` 会按种子化的 RNG 重新摆放物体，init state 实际没有用上。
  于是 1–50、51–65、66–80 来自同一个采样器的不同抽样，没有构造性差异。
  （与标准 LIBERO 评测用固定 init state 不同；这影响和 ASPIRE 的可比性，但不影响本问题。）
- 实测（`data/layout_stats.json`，第 0 步深度，8× 下采样）：
  - 开发 vs held-out 的能量距离置换检验：objtaskmilk p=0.65，spatialtaskcookie p=0.77；swap 对照 p=0.06–0.56。
  - 开发种子到最近 held-out 布局的距离，与 held-out 之间留一最近距离相同（0.0152 vs 0.0164，0.0552 vs 0.0550）。
  - 过拟合特征：对 objtaskmilk，最近开发布局在开发期通过的 held-out 种子只通过 21/46；
    spatialtaskcookie 为 17/36，反而低于最近布局失败的 8/14。即"长得像开发期通过的布局"并不帮助通过。
- 结论：不是种子分布问题；证据强。

### (d) 运行间不确定性
- objtaskmilk：同代码、同种子不可复现。
  - 根源是 GraspNet 推理时用未设种子的 `np.random.choice` 下采样点云（`contact_graspnet_pytorch/data.py:204`）。
    服务是长驻进程，全局 RNG 状态取决于同节点所有并发 trial 之前的请求。
  - 相邻版本里只改了后段 2–4 行，45 对日志都在 `post_pregrasp_0` 处分叉。
  - worker 自己也在 findings 里写过 "batch sweep is nondeterministic"。
- spatialtaskcookie：不调 plan_grasp，同 bundle 的重复 trial 日志 179 行完全一致，是确定性的。
  但它的 rim-pinch 处在摩擦临界，代码小改就让 3–6 个种子翻转。这是"对代码的混沌敏感"，而不是运行随机。
- 回放（同一 DLC 主机上的两次重跑）：
  - objtaskmilk 冻结版本两次重跑的逐种子一致率：51–65 为 9/15，66–80 为 6/15。
    在成功率约 0.5 时，这接近抛硬币（期望约 50% 一致）。
  - spatialtaskcookie 两段都是 15/15 一致。
  - 所以 objtaskmilk 每个种子的成败基本是 p≈0.5 的随机事件；15 个种子单次评分的标准差约 ±1.9 个种子，
    从 9 个版本里取最大，13 并不意外。

### Q1 结论
- 落差来自 **(b) 选择 + (d) 不确定性**，两个 cell 的机制不同：
  - objtaskmilk 的逐种子成败基本是 p≈0.5 的随机事件。冻结版本的 13/15 是 9 个版本里的噪声最大值，
    重跑两次都只有 8/15，unseen 7–8/15，held-out 0.44。
  - spatialtaskcookie 是确定性的。12/15 在那 15 个开发布局上真实成立，是一路修复对开发布局的过拟合；
    unseen 7/15，held-out 0.50。最早的 v0/v1 在 unseen 上反而有 9/15。
- (a) 评测通路：一致（确定性 policy 在 held-out 的函数下逐种子复现账本）。(c) 种子分布：无差异。
- 其他 cell 的 held-out"跟着开发期走"，是因为它们大多在第一个版本就 15/15、策略稳健（单版本 cell 差 +0.017），
  而不是 swap 与 task 子集有本质区别。
- unseen 66–80 的评分对 held-out 的预测远好于开发分（差 0.03–0.09，开发分高出 0.30–0.43），支持 M1 用 unseen 来选。
- 证据强度：(a)(c) 强；(b)(d) 强（账本 + 日志 + 345 次回放）。

## 7. 局限

- 失败分类由子代理读 replay.log、world 判定与关键帧完成（58 行，`data/failures_*.json`）。
  我抽查了 moka seed 52/53 的末帧，与结论一致；其余未逐条复核。
- twomoka 炉面 x 0.79–0.99、"cream cheese 无碰撞体"等是 worker 的结论，未独立核实。
  `solve_ik` 的 x≤0.75 截断已核实。
- 第 5 节的独立重复大多来自 p5 之前的 pipeline（预算与选择规则不同），用来估计量级而不是精确值。
- mugmicrowave l10、mugtaskmicrowave ns 仍在跑，表中数字是读取时的快照。

## 8. 回放作业与 DSW 使用

- 10-22 10:59–11:06，在协调端改为只用 DLC 之前，我在 DSW 卡 1/2 跑过一次 2 个种子的预演
  （objtaskmilk 冻结 v5，seed 51–52，约 6 分钟，服务已停、无残留进程）。
  结果在 `data/dsw-rehearsal-*`，根为 `/mnt/home/gewang/experiments/code-world-method-diagnosis-20261022/rehearsal`。此后未再用 DSW 跑 GPU。
- DLC 回放：
  - 根 `/mnt/home/gewang/experiments/code-world-method-diagnosis-20261022-replay`，23 个作业、345 次 trial。
  - 入口 `launch/replay-entry.sh`：SAM3 在卡 0，GraspNet 在卡 1，模拟器在卡 1–3，3 个作业 × N=4 并行；
    走 held-out 的 `run_parallel` / `prepare_seed` / `settle_seed`；480 min 后不再启动新作业。
  - 提交脚本 `submit-top-pool-replay.sh`（sha256 e0053209…），由协调端于 10-22 14:15 提交，job `dlcy9kjkbyp99mhb`，
    14:16–17:08 运行，Succeeded（GmtFinishTime 09:08:44Z）；23 个作业全部 rc=0，服务已停，无 crash 外的异常。
- 分析：`python3 scripts/analyze_replays.py <根> data/cells.json data/replay_analysis.json`；
  事件日志副本在 `data/replay_events.jsonl`。
