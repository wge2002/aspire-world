# 核心源码与 Markdown 对齐

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
