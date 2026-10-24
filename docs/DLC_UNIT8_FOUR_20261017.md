# 朱子悦项目：单节点八卡 stove / wine 四实验（2026-10-17）

最新状态（23:14 北京时间）：作业 `dlc7fct8r6v95slf` 已结束，四项均已退出。
两个r2通过正常成功/冻结检查并完整完成held-out seeds1–50；wine r2于19:44:05
完成，stove r2于20:12:21完成。两项原始报告与最终成绩在协调器私有记录中。
两个r1未开展测评：stove r1用尽新增30次后开发失败、按门槛跳过。wine r1完成第3轮后，
15:01因缺少11份逐seed失败说明，冻结前检查未过而退出为stage1_incomplete。
20:12:40节点完成teardown，Qwen与共享感知服务均已退出。A/B退出1、C/D退出0，
因此平台整作业为Failed；单项成功、未测与完整报告分别保留，不能用整作业标签替代。
保留wine r1的全部证据、候选、累计45次与原时钟，不跳过检查或额外执行seed。
四项14小时开发时钟截止于10月18日01:58:57，均未重置；两项测试在各自5小时内完成。
保留相同 cases / runtime / phase 身份、父轮各15次与过期父时钟，不重置预算。
逐项结果仅维护在协调器私有的实验明细和核查快照，供用户查询。

用户授权迁到 DLC workspace `1493863`（朱子悦项目），quota
`quotal96jnu33mnl`，一个 worker、八张 GPU；确认 stove × 2、wine × 2。
默认空间四卡作业 `dlcg4a7t7ff5ox1g` 已确认 `Stopped / StoppedByUser`，
取消前从未 Running，无节点、新账本、时钟或尝试。旧准备版本与证据保留。

## 四个实验与预算

| slot | 实验 | 开发输入 | 本次最多新增尝试 |
| --- | --- | --- | --- |
| A | stove r1 | 只继承自己的 R07 第 1 轮、15 次已用尝试及已声明修复起点 | 30；仅第 2、3 轮 |
| B | wine r1 | 只继承自己的 R07 第 1 轮、15 次已用尝试及已声明修复起点 | 30；仅第 2、3 轮 |
| C | stove r2 | 独立初始公开场景；不提供历史策略、配置或反馈 | 45；第 1–3 轮 |
| D | wine r2 | 独立初始公开场景；不提供历史策略、配置或反馈 | 45；第 1–3 轮 |

每轮开发 seeds 51–65 各一次，单 seed 累计最多三次。r1 保留过期父时钟；
取消的 Oct15/Oct16 准备没有实际 init，不产生额外次数或开发窗口。
本次 runtime 另行冻结，每 cell 只在实际 init 记录一次 14 小时开发时钟。
不重启旧作业、不修改旧账本、不退款、不跨 seed 或 repeat 转移额度。
r1 是带历史输入的修复接续；r2 是从头生成的新重复，不把两种起点当作严格同条件独立重复。

独立重复显式声明 `independent_start=true`，绑定
`aspire-independent-round-start-v1` 开始清单：历史尝试 0、parent 为 null、
files 为空；任何历史文件或父记录都导致拒绝。r1 的原 parent/hash/导入检查保留。

## 模型、布局与自主复盘

- 显式 profile `shared-node8-four-v1`，只允许 1×8 GPU 和 A/B/C/D 四个 slot。
- 一套 Qwen3.8-Flash-Next TP4，GPU 4–7、端口 8121，显存预算 0.82。
  保留 decode CUDA graph、native CC 2.1.220、xhigh、1M/64K。
- 一套无会话状态的共享 SAM3（GPU0:8114）、GraspNet（GPU1:8115）、
  Pyroki（CPU:8116）；各 cell 的 simulator/SAM2/EGL 分别在 GPU0/1/2/3。
- 先完成 Qwen 加载/内存分析，再加载感知。服务就绪、完整 800×512
  无任务 GraspNet 容量探测、真实 UID/源文件/资产/GPU/EGL 核验后才初始化实验。
- 每 cell 独立用户、HOME、control、代码、账本与复盘。每轮结束由独立的
  同一本地 Qwen 原生会话分析本 cell 的开发记录，root 校验并封存审批，自动接续。
  不依赖聊天唤醒，不共享其他重复的新代码、反馈或技能。
- 单 cell 退出保留共享服务；共享故障终止受影响 cell；所有 cell 结束后回收服务。

没有新的整体五小时上限。保留单 trial 900 秒、开发 14 小时、测试 5 小时
watchdog。仅当前 runtime 下选定的同一 bundle 正常退出并得到外层 oracle
成功，且通过正常冻结检查，才测试 held-out 1–50；没有合格候选则跳过。
最多新增 150 次开发执行、合格时最多 200 次 held-out；各 cell 并行开发，
最长开发与测评窗口约 19 小时，另加预检/模型启动时间。

## 路径与记录

- 工程：RS `/root/github/aspire-world`，分支 `SZ-DEV`，基线
  `63a7fc7382fb258b962372490daaef586fc67fe8`，包含未提交修复。
- 部署：`/mnt/workspace/aspire-world-deployment-20261017-unit8-four/`。
- 新运行源码：`/mnt/workspace/aspire-world-runtimes/unit8-four-SZ-DEV-20261017/`。
- 输出：`/mnt/workspace/aspire-world-runs/dlc1493863-unit8-four-20261017/`。
- 工程证据：`/tmp/aspire-dlc-unit8-four-20261017/`。

云凭据仅用于外层提交，不注入 DLC，不进入求解器。权重使用已缓存并绑定
哈希的本地文件；节点实际执行仍须重新预检。DSW 不启动模型或试验服务。
四项真正初始化后已登记 R08 / V07，矩阵只填完整冻结测试成功率。
结果文档仅协调器可读，排除在求解器 runtime 外，使用真实实验 UID 核验拒绝访问。

工程 CC 已开始读源文件后遇到模型接口 400 错误退出；主协调器依
`CC_USAGE.md` 的实际 API 故障例外接手，证据为临时目录
`engineering-takeover.json`。本次不提交或推送 Git。

## 提交与启动状态

2026-10-17 11:18:37（北京时间）首次提交作业 `dlc12rui2zglkj7r`。
11:19 平台确认 workspace `1493863`、quota `quotal96jnu33mnl`、1×8 GPU，
当时状态 `EnvPreparing`。11:19:52 已进入 `Running`，节点
`dlc12rui2zglkj7r-master-0` 于 11:20:47 实际启动入口，正在节点预检。
尚无正式开发账本或新增尝试。证据：部署目录 `operator-launch-status.json`
与 `operator/status-running-first.json`。
实际开发 init、时钟和结果仍以节点/账本为准，不把调度当作执行完成。

验收：137 项相关 CPU 检查通过，四个真实 UID 的只读/跨 cell/成绩文档
隔离检查通过，入口 validate-only 的四项 case 与依赖均通过。真实节点仍将
完整核验权重哈希、八卡 GPU/EGL、服务健康和容量后才初始化。

实际绑定的冻结清单为部署目录 `runtime-freeze-v2.json`，SHA256：
`e47624c88c4b6d19afd2f72db6ab76b4c2407d3a14b9faaa747db06b7e453662`。
含 6993 个运行文件、74 个共同外部文件。首次预检清单把两项私有开始文件
列入共同可读依赖，权限检查拒绝；该清单保留，只另建 v2，将私有开始文件
继续由各自 case / identity 单独绑定。源码运行树、旧清单和历史台账均未改写。
证据：`runtime-freeze-correction.json`、`actual-uid-and-parent-preflight.json`、
`operator/node-preflight.json`、`accepted-preflight.json`、`submission/`。

DSW 开跑记录器于11:59:03完成四项的 Git、冻结版本、时钟、预算和路径登记；
启动身份保存在 `operator-experiment-start.json`，不启动 DSW 模型或模拟器。

## 首趟退出与基础设施重试

Qwen于11:30:50就绪，其余共享服务于11:31:32前就绪，800×512容量检查通过。
四个原生campaign启动时，Numba试图写只读依赖旁的缓存，随后回退到各用户
HOME/.cache；该目录被前置root导入建成root所有，导致四项均在import阶段
报`cannot cache function mat2quat: no locator available`。11:32:08节点完成清理，
云端Failed。未创建campaign状态或开发账本，没有任何策略执行和开发时钟。

只调整各cell自己HOME内.cache/numba、.config/matplotlib及父目录的所有者和权限，
不改变缓存算法、模型参数、冻结源码或case。使用四个真实非root用户、原生
campaign环境完成无GPU的import验证，四项均exit0。旧operator逐cell日志移动
到`operator/failed-launch1/`归档并保存路径映射，原节点attempt01与首次提交
回执保持。重试使用`submission-retry1/`的新回执目录，防止重复提交。

计时仍从正式初始化开始：每cell开发最多14小时，达标后的held-out最多5小时；
四项并行，最多约19小时的阶段预算另加启动/清理。首趟提交到服务就绪实测
约13分钟。当前开发包含策略生成、反馈分析与实际执行，少量早期seed耗时
不足以给出精确结束时间。实际开发窗口已于11:58:57开始，截止10月18日
01:58:57；通过当前候选成功门槛与冻结检查后才启动最多5小时的held-out窗口。
