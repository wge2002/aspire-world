# Stove / wine 四卡续跑（2026-10-16）

后续状态（2026-10-17）：用户改为朱子悦项目1493863单节点八卡四实验。
本四卡作业已确认 Stopped / StoppedByUser，取消前从未分配或执行，保留全部
准备与父台账。新作业见[八卡四实验](DLC_UNIT8_FOUR_20261017.md)。

用户先要求本机测试，随后明确改为在 DLC 申请一个四卡节点。本阶段继续
`supervised-repair-20261014` 的 stove / wine，默认空间 `1000309`，quota
`quotaabjvnuk8san`。不启动本机模型或试验，不停止其他项目的本机进程。

2026-10-16 23:29（北京时间）已提交 `dlcg4a7t7ff5ox1g`，平台确认默认
workspace `1000309`、quota `quotaabjvnuk8san`，一个 worker、4 GPU。
截至 2026-10-17 10:54（北京时间）仍为 Queuing，自提交以来约 11 小时
25 分，未分配节点、未启动节点入口、开发账本或时钟，未增加尝试次数。
云端状态历史只有 Creating → Queuing，无 Running；事件列表为空，平台
未给出更具体的调度原因。父账本及相关证据哈希复核保持不变。
核查证据：[status.json](/mnt/workspace/aspire-world-deployment-20261016-unit4-continuation/operator/status-20261017T025438Z/status.json)。
实际状态以提交回执、节点预检和开发账本更新；排队不是正式执行。

## 继承与预算

两项各继承父第 1 轮 seeds 51–65 的 15 次已用额度。此前 10 月 15 日
准备的接续作业一直排队，取消时未分配节点、未产生新账本或次数。
本阶段只继续第 2、3 轮，各最多新增 30 次，累计各最多 45 次。
旧账本、已过期父时钟和所有冻结运行保持不变。

本次速度配置及四卡共卡布局另记 runtime 身份。节点预检通过、正式 init
时才开始一次性 14 小时开发时钟；续跑不重置。单次试验 900 秒、冻结测试
5 小时 watchdog 保留，无整体五小时上限。节点基础设施预检不执行任务
policy，不提供额外免费开发试验。

## 四卡布局与模型

依据 main `401a5ba23feabe1a6a46ea2a301f14c1f897d846` 的共卡实测设计：

| 服务 / 任务 | GPU |
| --- | --- |
| 共享 Qwen TP4 | 0、1、2、3 |
| 共享 SAM3 | 0 |
| 共享 GraspNet | 1 |
| 共享 Pyroki | CPU |
| stove 仿真 / SAM2 / EGL | 2 |
| wine 仿真 / SAM2 / EGL | 3 |

Qwen 采用 decode CUDA graph：mode 0、`FULL_DECODE_ONLY`、capture sizes
`[1,2,4]`，移除 `--enforce-eager` 和冲突的 expandable-segments allocator。
显存预算 0.82，TP4、1M context、4 sequences；native CC 2.1.220、xhigh、
64K output、原权重和采样配置保持。实际容量以节点加载后验证为准。
上游共卡测量不替代本次节点上的预检，也不是整轮提速或成功率保证。

两个任务仍使用独立 OS 用户、HOME/control、代码、账本、SAM2 状态和
复盘证据。仅共享无任务状态的感知与模型服务；一个任务结束时不能提前
关闭共享服务。共享服务故障须终止受影响任务，保留次数和已有输出。

## 自动复盘与冻结

沿用已修复的自主轮间复盘：独立、无工具的同本地 Qwen/native CC 会话
只读取自己的开发证据；root 校验输入/模型/轮次身份并发布不可改的批准。
先审阅父第 1 轮，再执行第 2 轮，复盘后执行第 3 轮，不依赖主聊天唤醒。
保留之前为各自任务准备的姿态、排序、持物观测和到位检查修复。

所选同一 bundle 必须在新 runtime 中正常结束且有开发 oracle 成功，并
通过正常冻结检查，才能评估 held-out seeds 1–50；没有合格程序则跳过。
不得把历史成功、开发反馈或未完成测评写成最终成功率。

## 路径与预检

- 工程基线：`SZ-DEV` / `63a7fc7382fb258b962372490daaef586fc67fe8`，含未提交修复和速度移植；完整变更及 runtime SHA256 在冻结时另存。
- 部署：`/mnt/workspace/aspire-world-deployment-20261016-unit4-continuation/`。
- 运行源码：`/mnt/workspace/aspire-world-runtimes/unit4-continuation-SZ-DEV-20261016/`。
- 输出：`/mnt/workspace/aspire-world-runs/dlc-unit4-continuation-20261016/`。
- 工程记录：`/tmp/aspire-dlc-unit4-continuation-20261016/`。
- 父证据和修复起点：只读引用各自原准备目录下的 `parent-evidence/`、`seed-inputs/`、`priors/`，哈希保持。

云端凭据只用于外层提交，DLC 禁止注入；模型权重和已缓存的感知权重
离线使用，不向求解器提供凭据。保留 SAM3 权重原来源记录，不能声称重新
通过官方 gated 授权。节点须验证权重全量 SHA256、真实四卡和 EGL 映射、
1M 模型容量、native CC 路由、模型加载后 800×512 实际感知请求与 UID 隔离。

两个结果文档及所有副本保持协调端私有，冻结源码排除；实际 UID 必须
验证不能读取。真正启动后才新增实验矩阵与逐任务记录，当前没有新成绩。

## 部署验收与提交证据

四卡 launcher 是显式 `layout_profile=colocated-unit4-v1`，另须精确声明
`gpu_map` 和 800×512 GraspNet 探测。Qwen 完成加载/内存分析后才加载共享
感知，避免感知加载干扰 KV 容量测量。仅启动一套共享服务；任务退出不
提前停止共享服务，共享故障或超时终止受影响任务，并回收自己的子进程。

工程 CC 在开始实现后连续四次遇到模型 API `Request timed out`，主协调器
按 `CC_USAGE.md` 的 API 故障例外保留部分代码后接手；执行器已退出，
没有并行写入。CPU 回归 **116 passed**，包括原八卡、四卡真实替身进程
生命周期、自主复盘与累计预算。实际 UID 30701/30702 分别核验 8207/8331
项读取及不可写/不可读边界，父次数各 15 保留。冻结节点入口 validate-only
通过；真实节点的权重全量 SHA256、GPU/EGL、模型容量和感知探测仍待执行。

运行清单含 6993 个源码文件、74 个外部文件，SHA256：
`3cc1c8bbe8c31143def1bf995c1c57cc43c67bd885b66aa3c32b4bf62f9ab93a`。
Git 基线、完整工作树覆盖、差异和子模块记录见部署目录 root 私有的
`source-overlay/`、`source-change-manifest.json`、`source-changes.patch`、
`launch-source-identification.json`。未提交或推送代码。

只调用一次 CreateJob；回执为部署目录 `submission/submission-receipt.json`。
提交时云端排队；10 月 17 日 10:54 复核仍无节点入口或新开发账本，不新增实验成绩行。
