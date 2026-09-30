# Qwen 部署配置与接口

本文整理仓库已有配置，不表示本次启动或重新验证了模型服务。源码入口包括：

- [通用服务启动/状态/停止脚本](../aspire/sim/scripts/common/serve_qwen3_8_flash_next.sh)
- [OpenAI 接口 smoke](../aspire/sim/scripts/common/smoke_qwen3_8_flash_next.py)
- [ASPIRE 客户端](../aspire/sim/cap/llm/client.py)
- [native CC 连接器](../aspire/sim/scripts/common/claude_with_local_model.sh)与[接口说明](../aspire/sim/docs/experiments/claude-code-local-models.md)
- [实验服务配置原件](../aspire/sim/docs/experiments/code-world-qwen-debug-20260922/reference/model-server.json)、[路径占位符示例](../deploy/qwen/model-server.example.json)、[客户端环境示例](../deploy/qwen/client.env.example)
- [R2 报告交接兼容探测](../aspire/sim/docs/experiments/code-world-qwen-foundation-r2-20260930/support/qwen-native-compat-r2.py)
- [实验兼容探测](../aspire/sim/docs/experiments/code-world-qwen-debug-20260922/qwen-native-compat.py)

## 接口

| 用途 | 默认地址 / 名称 |
| --- | --- |
| health | `http://127.0.0.1:8121/health` |
| 模型标识/容量查询 | `http://127.0.0.1:8121/v1/models` |
| OpenAI chat completions | `http://127.0.0.1:8121/v1/chat/completions` |
| native CC base URL | `http://127.0.0.1:8121`（CC 追加 `/v1/messages`） |
| served model | `qwen3.8-flash-next` |
| checkpoint | `Qwen/Qwen3.8-Flash-Next-FP8`（权重另行准备） |

现有连接器只支持无凭据 loopback 服务。远程使用沿用明确配置的 SSH tunnel；不要把服务凭据写入仓库。

## 两种配置要区分

通用 launcher 的源码默认值是 262,144 context、TP4、256 sequences、GPU memory utilization 0.90；仅设置 max-model-len 不能替代实验完整部署配置。

最新实验引用的记录配置是 TP4 + expert parallel/triton、1,000,000 context、4 sequences、GPU memory utilization 0.95、eager execution、Qwen reasoning/tool parsers、最多 600 images 且 video=0，以及完整静态 YaRN override（factor 4.0，original_max_position_embeddings 262144）。服务 thinking/reasoning 使用 `xhigh`；native CC 配置请求输出为 64,000。保留 JSON 中的完整参数，不要将通用脚本默认 `high` 示例当作该实验 profile。

`model-server.example.json` 把模型、vLLM、CUDA 和 workspace 路径改成占位路径，其他参数来自记录中的服务配置；使用前须按目标主机修正这些路径并验证容量。记录使用具备 Qwen3.8 支持的 vLLM 环境，具体依赖兼容性仍需在目标 GPU 上检验。

## 连接已就绪的实验服务

从 `aspire/sim` 运行，先填写客户端示例中的路径：

```bash
source ../../deploy/qwen/client.env.example
bash scripts/common/claude_with_local_model.sh   http://127.0.0.1:8121 qwen3.8-flash-next
```

此命令只连接已部署服务。一般接口 smoke 不等于完整 native Fix Loop 验证；完整实验还要检查 Messages、streaming、tool-result、图像、Agent 路由、compaction 和实际容量，并遵守独立输出与 seed 记账协议。权重、KV cache、日志和所有运行产物留在仓库外。
