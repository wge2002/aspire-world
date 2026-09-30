# 仓库内容边界

这是独立复制出的源码快照，个人远端为 [wge2002/aspire-world](https://github.com/wge2002/aspire-world)；不复制原 `.git` 历史。原 ASPIRE checkout、remote、未提交修改和全部本地/远端实验产物保持原位。`source-manifest.json` 记录输入文件摘要、上游基准提交及第三方 gitlink；它是源码溯源清单，不是实验结果。

## 收录

- 仿真框架、模型和感知接口、运行脚本、环境配置、依赖清单、协议、技能与测试。
- 9 月 30 日 foundation 及其 9 月 26/24/22/20 日支持源码和核心设计文档。
- Qwen 通用启动脚本、当前实验服务配置、native CC 接口与无密钥示例。
- 许可证、归属、第三方固定 submodule 提交；必要的 Franka 静态网格是仿真输入，保留在源码中，不是模型权重或运行结果。

## 不收录

真实机器人 workspace、演示媒体、研究归档、运行日志、原始结果、轨迹、视频/图片、权重、检查点、下载的第三方源码、环境、缓存、密钥、本机 agent 设置。忽略规则只影响 Git，不删除本地文件。不要用 `git add -f` 绕过边界。

实验目录采用明确文件清单：新增实验时在 `aspire/sim/docs/experiments/.gitignore` 中放行经过检查的文档、源码和配置。禁止批量放行实验结果目录。`.gitignore` 对已经跟踪的文件不生效，所以本次使用独立新索引，并在首次发布前检查实际暂存文件。

## 运行与迁移限制

此整理不是新机器部署或实验复现。模拟器仍需要 Linux/NVIDIA、外部 submodule、权重与服务。最新 stager 使用原服务器的绝对路径、先前冻结的基础源码、已计费 diagnostic 输入和 SHA256 pins。部分材料仅存在原远端：例如 Sep-17 native-A1-source、Sep-14 package reference、Sep-23 retry4 launch 文件和当前 campaign diagnostic artifacts。不要把这些旧结果补进 solver，也不要改校验值来绕过缺项；新实验应按新授权建立新路径与协议输入。

四个依赖未收录的旧 frozen 场景产物的诊断测试没有复制：`test_live_world_diagnostic.py`、`test_scene_world_diagnostic.py`、`test_reference_scene_diagnostic.py`、`test_relational_scene_diagnostic.py`。其原文件保留在旧 checkout。其他测试保留，不能据此宣称完整测试已通过。

README.upstream.md 和部分历史文档保留原链接与主机路径；其中指向 real、历史 logs、results、figures 的链接在精简仓库中可能不可用。个人 README、最新实验索引与 Qwen 文档是本仓库导航入口。
