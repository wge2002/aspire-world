# 仓库内容边界

个人远端为 [wge2002/aspire-world](https://github.com/wge2002/aspire-world)。仓库保留仿真核心源码、协议、配置、必要测试和上游归属。开发源码按 [SYNC_SCOPE](SYNC_SCOPE.md) 与远端工程 checkout 对齐；Git 历史独立于 NVlabs/ASPIRE。

## 收录

- 仿真框架、模型/感知接口、启动脚本、环境配置、依赖清单、agent 协议与测试。
- 最新 prediction-contract、gate ablation、闭环和 pipeline 修复；保留已有 foundation 及必要历史支持源码。
- Qwen 部署配置、native CC 连接与图像接口；无密钥的路径示例。
- 许可证、第三方固定 submodule 提交，以及必要的 Franka 静态网格。网格是仿真输入，不是模型权重。
- 测试使用的小型合成 fixture；其中 4 个 prediction-contract JSONL 和 1 个 native event JSON 按精确路径收录，仅用于回归，不是机器人实验结果。

## 不收录

真实机器人 workspace、运行日志、原始结果、生成轨迹、图像/视频、权重、检查点、环境、缓存、凭据、本机设置、下载的第三方源码和旧研究归档。实验目录按明确文件名单收录，不通过 `git add -f` 绕过规则。忽略规则不删除任何本地或远端文件。

以下旧诊断测试需要未收录的历史冻结输入或结果，保留在原有源码/存储，不纳入个人仓库：`test_live_world_diagnostic.py`、`test_scene_world_diagnostic.py`、`test_reference_scene_diagnostic.py`、`test_relational_scene_diagnostic.py`、`test_judgment_profile.py`、`test_judgment_world.py`、`test_shadow_condition_broker.py`。

## 运行边界

这是源码对齐，不是新实验授权或新机器部署。仿真需要 Linux/NVIDIA、外部 submodule、权重与感知服务。现有 stager 中的主机路径、冻结输入、diagnostic 记账和 SHA256 pins 保持原义；新运行须有独立路径和协议，不能把旧结果补进 solver 或修改校验绕过缺项。

旧文档的 results/figures/logs/real 链接可能指向本仓库未收录的材料；以最新实验索引和 Qwen 文档作为当前入口。测试通过只说明所检查的软件行为，不代表机器人任务效果或完整实验复现。
