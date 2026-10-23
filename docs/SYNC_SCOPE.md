# 核心源码与 Markdown 对齐

更新日期：2026-10-23。本轮发布 p4 覆盖门控、p5 开发预算、p6 并行/收尾、p6b–p6d 恢复与方法修复、4-cell unit 启动层后续修复及 BEHAVIOR 仿真接口更新。源码和协议共 1206 个文件，另同步本目录的五份共用文档（含 manifest）。逐文件身份见 [source-manifest.json](source-manifest.json)。

| 位置 | 路径 / 分支 |
| --- | --- |
| 远端开发源码 | `rbs-debug:/mnt/home/gewang/code/ASPIRE-world-revision-20260910-01a0894b` |
| 本地开发源码 | `/Users/wge/git/ASPIRE` |
| 本地个人发布副本 | `/Users/wge/git/ASPIRE-personal` |
| GitHub | [wge2002/aspire-world](https://github.com/wge2002/aspire-world)，`main` |

源根 `README.md` 映射为个人仓库的 `README.upstream.md`；个人 README、忽略清单和 Git 历史独立。三处对齐指本清单内的文件内容，不是要求各工作区的 Git 历史或实验数据相同。

源码集合摘要（排序 path → SHA256 的紧凑 JSON）：`bf1db4164aa1d8af55bc84ae2d90a366899e6d51a2af4bcce757b1ac02254849`。

## 本轮验证

- 在独立 Linux 发布副本分轮验证 20 组 CPU 回归，合计 **647 项及 12 个 subtests 通过**。其中完整首轮 607 项通过；补齐漏收录的三份 `p5/parallel/support` 源码后，40 项定向复核全部通过。未重复运行已经通过的用例。
- 1 项 r1pro 真实失败 mask 用例因外部图像缺失而跳过；21 项依赖未收录历史数据或内部提示片段的用例不在本次验证范围。完整首轮有 1 项真实回放测试因缺少外部 fixture 失败，已明确归入该排除范围；没有为通过测试而复制实验结果。
- 一个 0.3 秒子进程用例首轮在写出 `child.pid` 前超时，源码未改，定向复核通过；保留其时序敏感性说明。本页不宣称全仓所有测试无条件通过。

- 发布范围的 844 个 Python 文件通过 AST 解析，55 个 Shell 脚本通过 `bash -n`；新增/修改文件的暂存空白检查通过。
- 最新入口文档的本地链接、强凭据模式及新增文件的明文凭据赋值模式检查通过。暂存范围不含删除、真实机器人目录、原始实验结果、日志、模型权重或新媒体文件。
- 七个第三方 gitlink 固定提交保持原值。Contact-GraspNet 的三处工作区修改与已发布兼容补丁一致，不把下载的 submodule 内容内嵌进仓库。
- 未启动新的 GPU、模型服务或实验。本轮 CPU 回归不替代最新 p6d 的真实 smoke / trial 验收。10-16 的 8 卡 / 4-cell smoke 只属于当时冻结的源码，具体边界保留在 [QWEN](QWEN.md) 和历史 manifest 更新项中。

## 保留与排除

同步使用精确路径、覆盖前哈希检查与旧文件备份，拒绝并发变更；不对目录执行删除同步。远端旧基础环境、所有冻结运行树和现有实验输出保持原样。

历史 ledgers、对话、生成策略、真实回放 fixture、日志、权重和媒体不入 Git。只有已明确收录的合成测试输入、历史框架兼容源码与 SAM3 公开开发场景健康检查图作为例外。部分历史脚本和回归仍依赖仓库外材料，见 [REPOSITORY_SCOPE](REPOSITORY_SCOPE.md)。新运行需独立路径、完整外部依赖和对应协议；同步不会改写原轮次配置或授权。
