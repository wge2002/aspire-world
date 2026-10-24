# 核心源码与 Markdown 对齐

## 2026-10-24：发布 RS 10 月 19 日 wine 修复源码

用户授权将 RS 的最新部署源码同步到本地 `SZ-DEV` 并推送至
`wge2002/aspire-world` 同名分支。来源为
`rs:/mnt/data/aspire-world-deployment-20261019-wine-repair/source-overlay/`，
基线 `63a7fc7382fb258b962372490daaef586fc67fe8`。启动时为含未提交改动的
`SZ-DEV`；本次发布不追称为历史启动时的 Git 身份。

`source-change-manifest.json` 共 77 个文件：相对本地基线，30 个已有文件
改变、47 个新增。传输前后逐文件 SHA256 全部一致。来源清单 SHA256：
`974bbae6390bef6cf2acb88ce63744072bd824ac76d77108bbc6877db67bde2a`；
配套封存 patch SHA256：
`9e28165228e0cd2f05831d62ab5e4fafc05e7328cbaaa904113c5eb125e009fe`；
实际 `runtime-freeze-v2.json` SHA256：
`71dee4901443f03f16be14c3fed7a3e2ee751b1e985bc89523137ff8494d4e3b`。
上述清单、patch、冻结树与原始实验产物保留在 RS，不复制进 Git。

源码、测试及配置保持快照字节；发布时仅补充导航、结果记录和停止状态：
`AGENTS.md`、`README.md`、`docs/LATEST_EXPERIMENTS.md`、
`docs/DLC_WINE_REPAIR_20261019.md`、`docs/EXPERIMENT_RESULTS{,_DETAILS}.md`、
`docs/RS_HANDOFF_20261012.md` 和本同步说明。停止记录为 10 月 19 日用户取消，
18/45 次开发、0 正常成功、held-out 未开始；三份现存开发/campaign/pair 状态
文件仍与停止时哈希一致。

本次同步不启动服务或实验，不修改 RS 冻结树、账本、时钟或旧输出；日志、
媒体、权重、凭据、生成策略及原始结果均保留在仓库外。协调端结果文档继续
受求解器不可读的运行时排除规则约束。

本地验证：52 个 Python 文件通过 AST 解析，示例 JSON 可解析，提交差异格式
检查通过，未发现明显凭据。17 组目标 CPU 回归实际得到 247 passed、2 skipped；
另 41 项部署测试在共享夹具 `os.chown(..., 999, 999)` 处因本机非 root 权限
报错，未进入被测逻辑。这 41 项不能记为本地通过；本次未修改源码绕过权限。
RS 留存的 311 项核心和 17 项策略验收是历史证据，与本地结果分开记录。

## 历史对齐记录

2026-10-12 当前 RS 线补充：本地工程目录 `/home/zheng/github/aspire-world`，
服务器工程目录 `rs:/root/github/aspire-world`，GitHub 发布分支 `SZ-DEV`。
接手状态、验证范围及部署产物地址见 [RS_HANDOFF_20261012.md](RS_HANDOFF_20261012.md)。
下文2026-10-09的路径/963文件摘要是历史对齐记录，不是当前新增源码清单。

对齐日期：2026-10-09。范围：963 个源码/协议输入文件，以及此目录下的共同导航文档。逐文件 SHA256 与发布路径见 [source-manifest.json](source-manifest.json)。

| 位置 | 路径 / 分支 |
| --- | --- |
| 远端开发源码 | `rbs-debug:/mnt/home/gewang/code/ASPIRE-world-revision-20260910-01a0894b` |
| 本地开发源码 | `/Users/wge/git/ASPIRE` |
| 本地 Git 发布副本 | `/Users/wge/git/ASPIRE-personal` |
| GitHub | [wge2002/aspire-world](https://github.com/wge2002/aspire-world)，`main` |

`AGENTS.md`、`CC_USAGE.md`、`CLAUDE.md` 和选定的仿真代码/文档按内容对齐。源仓库根 `README.md` 在个人仓库发布为 `README.upstream.md`；个人根 `README.md` 是该 GitHub 仓库的导航入口。除这一显式映射外，源码保持相同相对路径。个人 `.gitignore` 和 Git 元数据不反向覆盖开发仓库。

源码集合摘要（按 path → SHA256 的排序 JSON 计算）：`3539ceaad64af33fe4e018d43912a2aeb67b8a304121ded395c44b9cb9fb70da`。

本轮合并保留本地较新的部署说明、GPU 规则、协议和 Robosuite 环境修复；补入远端的 native runtime 冻结文件清单及不依赖旧结果的相关测试。补齐必要的小型回归 fixture，并将两个旧测试适配已有冻结清单和最后一次 graded attempt 保留规则；p1 golden 对比消除 framework caller 的机器路径差异，保留策略调用、函数和业务事件精确比对。未更改运行协议。双方独有的在范围内源码保留，不用整目录覆盖，不删除任何额外文件。覆盖前保存源码备份；写入前再次比较原哈希，防止覆盖审阅期间发生的新修改。

远端 `/mnt/home/gewang/code/ASPIRE` 是旧基础与环境目录，不属于本次开发源码写入目标。已冻结的 `ASPIRE-code-world-*` cells、实验输出、模型、权重、日志和原始结果不参与同步。当前源码和协议在同步时一致，不代表过去每个运行目录被改成最新版，也不代表实验完成。

验证：在远端隔离临时源码副本执行 20 组 CPU 回归，419 tests、14 subtests 全部通过（41.63 秒；5 条第三方 deprecation warnings）。覆盖 prediction contract、world/foundation、development gate、native fix loop、队列、终态和冻结完整性。所有收录 Python 做 AST 解析、Shell 做语法检查；本轮没有启动新模型服务或机器人实验。

来源清单保留上游基准 commit；新研究的可复现身份还需结合其固定 case/runtime manifests 和外部环境。发布仓库不收录这些原始运行产物。
