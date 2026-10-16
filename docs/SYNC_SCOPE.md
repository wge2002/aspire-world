# 核心源码与 Markdown 对齐

更新日期：2026-10-16。本轮发布 Qwen unit 节点启动层、p3 search-control、p2 runtime world、配套核心文档和配置。源码范围为 1093 个文件；逐文件 SHA256 与 README 映射见 [source-manifest.json](source-manifest.json)。

| 位置 | 路径 / 分支 |
| --- | --- |
| 远端开发源码 | `rbs-debug:/mnt/home/gewang/code/ASPIRE-world-revision-20260910-01a0894b` |
| 本地开发源码 | `/Users/wge/git/ASPIRE` |
| 本地个人发布副本 | `/Users/wge/git/ASPIRE-personal` |
| GitHub | [wge2002/aspire-world](https://github.com/wge2002/aspire-world)，`main` |

源仓库根 `README.md` 在个人仓库发布为 `README.upstream.md`；个人根 README 为导航入口。个人 `.gitignore` 和 Git 历史独立。当前源码包含远端已完成四槽位 smoke 的工程改动，保留本地较新的 CC 协作说明及历史协议记录；范围内额外源码不会通过整目录覆盖被删除。

源码集合摘要（按 path → SHA256 的排序 JSON）：`11d9fc1f5382139f977e2aeb8b7387dc93fb2103c99d40bde8391f98b33e0b45`。

## 本轮验证

- 独立发布副本的 10 组 CPU 回归：298 passed、1 skipped、12 subtests passed。跳过项依赖仓库外的历史 p2 ledgers，不为运行它而收录实验结果。
- 上述回归包含节点布局与 supervisor 强制停止的 40 项测试；另检查历史 smoke 准备/采样/收集脚本的语法。
- 2026-10-16 的 DLC 作业 dlcrv0c97s12ol8y 完成 8 卡、两个 unit、4 个 cell 的真实 smoke。逐个核对 queue-result 与账本：每个 cell 恰好一次 charged seed-51，终态 native_smoke_complete，supervisor_smoke_stop 生效，after_stop 为空，所属进程树清理通过；两个 unit 均 stopped，node_complete。两 unit 的 native compatibility 与 SAM3 实际推理健康检查通过，运行使用非特权 API。
- 验收验证启动、绑定、服务、停止边界与清理，不代表策略任务成功率：四次首次 bowl trial 的 reward 都为 0，其中一个 sandbox_rc 为 1。此次未运行完整开发/50-seed held-out 实验。
- GraspNet 两 unit 均收到实际请求且没有 OOM，但缺少逐卡显存采样，util 0.82 的峰值余量仍未定量验证；DLC pod 已退出，未留存任务结束后的 compute-apps 快照。
- Python AST、Shell 语法、导航链接、凭据模式和暂存文件范围均在发布前检查。工程广泛回归中的历史失败另有原始记录；本页不宣称整个仓库全量测试零失败。

覆盖前备份并检查旧哈希，避免覆盖并发修改。远端旧基础环境 `/mnt/home/gewang/code/ASPIRE`、所有冻结运行副本和现有实验输出保持原样。历史研究源码中的旧布局保留为原轮次记录；新运行入口见 [LATEST_EXPERIMENTS](LATEST_EXPERIMENTS.md)。

日志、原始结果、对话、权重、缓存和实验媒体不入 Git。仅按精确路径收录必要的合成回归 fixture 与 SAM3 健康检查输入图。真实 smoke 的原始证据保留在计算存储；本轮发布不会启动新的实验或 DLC 作业。
