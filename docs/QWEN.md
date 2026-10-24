# Qwen 部署配置与接口

2026-10-16 从 main 的 [401a5ba](https://github.com/wge2002/aspire-world/commit/401a5ba23feabe1a6a46ea2a301f14c1f897d846) 单独接入 decode CUDA graph 速度修复。完成配置与启动器的 CPU 验证；未启动模型、仿真或 DLC 实验。源码入口包括：

- [通用服务启动/状态/停止脚本](../aspire/sim/scripts/common/serve_qwen3_8_flash_next.sh)
- [OpenAI 接口 smoke](../aspire/sim/scripts/common/smoke_qwen3_8_flash_next.py)
- [图像交付辅助服务](../aspire/sim/cap/serving/launch_vllm_image_source.py)与[图像接口回归](../aspire/sim/tests/test_vllm_image_source.py)
- [ASPIRE 客户端](../aspire/sim/cap/llm/client.py)
- [native CC 连接器](../aspire/sim/scripts/common/claude_with_local_model.sh)与[接口说明](../aspire/sim/docs/experiments/claude-code-local-models.md)
- [实验服务配置原件](../aspire/sim/docs/experiments/code-world-qwen-debug-20260922/reference/model-server.json)、[路径占位符示例](../deploy/qwen/model-server.example.json)、[客户端环境示例](../deploy/qwen/client.env.example)
- [现有部署清单的速度配置转换器](../aspire/sim/scripts/common/qwen_decode_config.py)与[CPU 回归测试](../aspire/sim/tests/test_qwen_decode_config.py)
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

实验完整 profile 是 TP4 + expert parallel/triton、1,000,000 context、4 sequences、Qwen reasoning/tool parsers、最多 600 images 且 video=0，以及完整静态 YaRN override（factor 4.0，original_max_position_embeddings 262144）。服务 thinking/reasoning 使用 `xhigh`；native CC 配置请求输出为 64,000。保留 JSON 中的完整参数，不要将通用脚本默认 `high` 示例当作该实验 profile。

历史配置原件使用 GPU memory utilization 0.95 和 eager execution，保持原记录。当前 `model-server.example.json` 与上述 main 提交一致：使用 decode CUDA graph，示例显存预算为 0.82，模型、vLLM、CUDA 和 workspace 路径仍为占位路径。已有 RS 部署使用 0.80；下面的转换器会保留输入清单的预算、GPU 编号与端口，不会套用示例的 0.82。具体容量和依赖兼容性需在目标 GPU 上检验。

## 速度修复与现有部署接入

原配置的 `--enforce-eager` 禁用了 CUDA graph。速度修复移除该选项，添加：

```json
{"mode": 0, "cudagraph_mode": "FULL_DECODE_ONLY", "cudagraph_capture_sizes": [1, 2, 4]}
```

同时移除包含 `expandable_segments` 的 `PYTORCH_CUDA_ALLOC_CONF`；上游测量中该设置会使 custom all-reduce 在 graph capture 时报 `invalid argument`。保留 custom all-reduce，不启用 torch.compile。模型、权重、上下文、采样和推理配置保持输入清单的原值。

上游在 DSW 同一组 4×L20Z 上报告单流 decode 约从 8.9 提升至 148 token/s，模型启动约从 820 秒缩短至 320 秒，见[上游设计记录第 1 节](https://github.com/wge2002/aspire-world/blob/401a5ba23feabe1a6a46ea2a301f14c1f897d846/aspire/sim/docs/engineering/node-layout-20261015/DESIGN.md#1-model-server-replaces-the-eager-config)。这些是上游基准，未在当前 RS/DLC 部署复测，也不等于整轮实验同比提速或任务成功率提高。

从仓库根目录，将现有清单转换为一个新文件（填写实际路径）：

```bash
python3 aspire/sim/scripts/common/qwen_decode_config.py \
  --source /path/to/existing/model-service.json \
  --out /path/to/new/model-service-decode.json
```

转换器仅支持已验证的 TP4 profile，拒绝不同的 compilation config、关闭 custom all-reduce 或覆盖已有输出。它重算已有的 argv SHA256，并记录输入清单与上游 Git 身份。现有 DLC 启动器可以直接读取新清单；转换器本身不启动服务，也不改写 case、冻结目录、账本或时钟。

下一次获授权运行前，须把新清单及 SHA256 绑定到新的 case/runtime 身份，并在目标节点完成权重全量校验、模型加载、容量、native CC 与实际感知预检。已取消实验的冻结清单保持原字节。此次未引入 main 的新 GPU 调度或 p2/p3 实验机制。

本次 CPU 验证：转换器及现有 DLC 启动器相关测试 56 项通过；真实 RS 清单转换后通过模型配置校验，保留 GPU `2,3,4,5`、显存预算 `0.80`，检查 144 个权重文件的存在与大小。未加载模型或开展 GPU 性能测试。

## 连接已就绪的实验服务

从 `aspire/sim` 运行，先填写客户端示例中的路径：

```bash
source ../../deploy/qwen/client.env.example
bash scripts/common/claude_with_local_model.sh   http://127.0.0.1:8121 qwen3.8-flash-next
```

此命令只连接已部署服务。一般接口 smoke 不等于完整 native Fix Loop 验证；完整实验还要检查 Messages、streaming、tool-result、图像、Agent 路由、compaction 和实际容量，并遵守独立输出与 seed 记账协议。权重、KV cache、日志和所有运行产物留在仓库外。
