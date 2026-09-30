# ASPIRE World

个人维护的 ASPIRE 仿真、Code World 与 Qwen 实验代码仓库：[wge2002/aspire-world](https://github.com/wge2002/aspire-world)。基于 [NVlabs/ASPIRE](https://github.com/NVlabs/ASPIRE)，保留上游归属、许可证及第三方声明。当前整理日期：2026-10-01。

此仓库保存源码、协议、配置和必要测试；实验日志、原始结果、轨迹、视频、模型权重、环境及缓存保存在仓库外。原始工作目录及已有实验数据不受此副本影响。

## 阅读入口

| 内容 | 入口 |
| --- | --- |
| 仿真环境与依赖安装 | [Simulation README](aspire/sim/README.md) |
| 最新实验：Qwen foundation R2 | [最新实验索引](docs/LATEST_EXPERIMENTS.md) |
| Qwen 部署、1M 配置与模型接口 | [Qwen 接口](docs/QWEN.md) |
| Code World 执行实现 | [cap/world_model](aspire/sim/cap/world_model/) |
| 仓库收录范围与运行限制 | [内容边界](docs/REPOSITORY_SCOPE.md) |
| Agent 操作约定 | [AGENTS.md](AGENTS.md) |
| 上游介绍与引用 | [README.upstream.md](README.upstream.md) |
| 许可证与第三方归属 | [LICENSE](LICENSE)、[NOTICE](NOTICE)、[LICENSES](LICENSES/README.md) |

## 代码布局

- `aspire/sim/cap/`：仿真 API、感知与运动接口、模型客户端、Code World。
- `aspire/sim/scripts/`：模型服务、native agent、Fix Loop、重放与评估入口。
- `aspire/sim/env_configs/`、`.claude/`：环境配置、协议、提示与技能参考。
- `aspire/sim/docs/experiments/`：最新实验和必要历史支持源码，按明确文件清单收录。
- `aspire/sim/tests/`：框架测试；依赖已排除历史实验产物的四个旧诊断测试未复制。
- `deploy/qwen/`：无密钥的部署配置示例。

仿真依赖 Linux/NVIDIA 环境；本地 macOS 整理不代表运行验证。第三方源码保留为原提交固定的 Git submodule，不内嵌下载内容。克隆后按所需仿真套件的 README 初始化对应 submodule 并准备外部权重。

最新实验脚本仍保留已记录的服务器路径与冻结输入校验，不能直接在新机器无条件执行；先阅读内容边界。此仓库整理不启动实验或模型服务。
