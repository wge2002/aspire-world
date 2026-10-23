# Qwen 部署配置与接口

本文整理仓库已有配置，2026-10-23 随核心源码对齐；2026-10-15 起实验服务配置改为 CUDA graph 配置并按 4 卡 unit 布局启动（见下文“实验服务配置”与 [节点布局契约](../aspire/sim/docs/engineering/node-layout-20261015/DESIGN.md)）。源码入口包括：

- [通用服务启动/状态/停止脚本](../aspire/sim/scripts/common/serve_qwen3_8_flash_next.sh)
- [OpenAI 接口 smoke](../aspire/sim/scripts/common/smoke_qwen3_8_flash_next.py)
- [图像交付辅助服务](../aspire/sim/cap/serving/launch_vllm_image_source.py)与[图像接口回归](../aspire/sim/tests/test_vllm_image_source.py)
- [ASPIRE 客户端](../aspire/sim/cap/llm/client.py)
- [native CC 连接器](../aspire/sim/scripts/common/claude_with_local_model.sh)与[接口说明](../aspire/sim/docs/experiments/claude-code-local-models.md)
- [现行实验服务配置](../aspire/sim/scripts/common/node_layout/reference/model-server.json)（unit 0 模板）、[路径占位符示例](../deploy/qwen/model-server.example.json)、[客户端环境示例](../deploy/qwen/client.env.example)
- [unit 布局启动层](../aspire/sim/scripts/common/node_layout/)：`unit_layout.py`（GPU → unit → 端口/槽位、模型 argv）、`unit_supervisor.py`、`unit-entry.sh`
- 历史 eager 配置原件（只属于 2026-10-15 之前的轮次）：[code-world-qwen-debug-20260922](../aspire/sim/docs/experiments/code-world-qwen-debug-20260922/reference/model-server.json)
- [R2 报告交接兼容探测](../aspire/sim/docs/experiments/code-world-qwen-foundation-r2-20260930/support/qwen-native-compat-r2.py)
- [实验兼容探测](../aspire/sim/docs/experiments/code-world-qwen-debug-20260922/qwen-native-compat.py)

## 接口

| 用途 | 默认地址 / 名称 |
| --- | --- |
| health | `http://127.0.0.1:8121/health`（unit 0；unit u 为 8121+10u） |
| 模型标识/容量查询 | `http://127.0.0.1:8121/v1/models` |
| OpenAI chat completions | `http://127.0.0.1:8121/v1/chat/completions` |
| native CC base URL | `http://127.0.0.1:8121`（CC 追加 `/v1/messages`） |
| served model | `qwen3.8-flash-next` |
| checkpoint | `Qwen/Qwen3.8-Flash-Next-FP8`（权重另行准备） |

现有连接器只支持无凭据 loopback 服务。远程使用沿用明确配置的 SSH tunnel；不要把服务凭据写入仓库。

## 两种配置要区分

通用 launcher 的源码默认值是 262,144 context、TP4、256 sequences、GPU memory utilization 0.90；仅设置 max-model-len 不能替代实验完整部署配置。

## 实验服务配置（2026-10-15 起）

现行配置在 [`node_layout/reference/model-server.json`](../aspire/sim/scripts/common/node_layout/reference/model-server.json)，由 p3 冻结的 eager 配置只改三处得到（[DESIGN §1](../aspire/sim/docs/engineering/node-layout-20261015/DESIGN.md)）：

- 去掉 `--enforce-eager`；
- 加 `--compilation-config '{"mode": 0, "cudagraph_mode": "FULL_DECODE_ONLY", "cudagraph_capture_sizes": [1, 2, 4]}'`（decode 全图 CUDA graph，不用 torch.compile）；
- env 去掉 `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True`（它在 graph capture 时破坏 custom all-reduce：`custom_all_reduce.cuh:164 'invalid argument'`）；custom all-reduce 保持开启。

`--gpu-memory-utilization` 每个 unit 一个值，按共卡实测（DESIGN §4）取 **0.82**；下限约 0.76（KV pool 须容纳一个 1,000,000-token 请求）。其余不变：TP4 + expert parallel/triton、1,000,000 context、4 sequences、prefix caching、Qwen reasoning/tool parsers、最多 600 images 且 video=0、完整静态 YaRN override（factor 4.0，original_max_position_embeddings 262144）、thinking/`reasoning_effort xhigh`、采样覆盖与其他 env。native CC 配置请求输出为 64,000。

实测（DSW，4×L20Z）：decode 单流 148 tok/s（52k 与 263k context；eager 8.9），2 流每流 136，4 流每流 121；启动约 320 s（eager 820 s）；greedy 输出与 eager 在文本、图像和 150k-token 提示上一致（仅一处句末句号不同）。与 SAM3/GraspNet/两路模拟器共卡（util 0.84）时单流中位 142.5 tok/s、两流中位 124.6，感知突发时最多慢约 33%。TP2 放不下（TP4 下权重每卡 44.9 GiB）。

启动不再手写 argv：`unit_layout.model_server(reference, unit, util)` 为每个 unit 设定端口、`CUDA_VISIBLE_DEVICES` 与 util，并拒绝带 eager、`expandable_segments` 或关闭 custom all-reduce 的参考配置。节点布局：每 4 卡一个 unit（Qwen TP4 + SAM3 + GraspNet + PyRoKi(CPU) + 2 个 cell 槽位，感知与模拟器同卡），8 卡节点 = 2 个 unit；见 [LATEST_EXPERIMENTS](LATEST_EXPERIMENTS.md#节点布局2026-10-15-起)。

`model-server.example.json` 把模型、vLLM、CUDA 和 workspace 路径改成占位路径，其他参数与现行配置一致；使用前须按目标主机修正这些路径并验证容量。记录使用具备 Qwen3.8 支持的 vLLM 环境，具体依赖兼容性仍需在目标 GPU 上检验。

## 历史启动层验收（2026-10-16）

8 卡、两个独立 Qwen unit、四个 cell 的真实 smoke 已核验通过。每个 cell 的首条 charged seed-51 到终态后，由 supervisor 清理进程树并记录 `supervisor_smoke_stop`；四个 cell 均没有额外 trial，队列为 `native_smoke_complete`，节点为 `node_complete`。该结果对应 10-16 冻结的 supervisor；10-23 发布的源码已经包含后续修复，不能沿用旧哈希作为现版本的验收。`full` 模式不使用此停止条件。

两 unit 的 native compatibility 与 SAM3 实际推理健康检查通过；GraspNet 各收到 1 和 8 次成功请求，无 OOM。但此次没有逐卡显存采样，不能据此声称已量出 util 0.82 下 GraspNet 的峰值余量。此次 smoke 的四个首次 bowl trial 均 reward 0，其中一个 sandbox_rc 1；它验证服务与编排，不代表任务质量或完整研究结果。完整 50-seed held-out 运行未包含在本次验收中。验证范围见 [SYNC_SCOPE](SYNC_SCOPE.md)。

## 当前源码补充（2026-10-23）

Qwen reference 配置与上述 TP4 / 4-cell 布局保持不变。unit supervisor 已加入失败后的 session / 后代清理、有限基础设施重新排队、节点 cell 分配和按 case 并发计算时间界限。p6 的 replay 并发属于 cell 内部，不等于增加 Qwen cell 槽位。最新 p6b–p6d 协议、CPU 验证和未收录的历史数据依赖见 [最新实验](LATEST_EXPERIMENTS.md) 与 [同步验证](SYNC_SCOPE.md)。本次发布不部署模型或启动新实验。

## 连接已就绪的实验服务

从 `aspire/sim` 运行，先填写客户端示例中的路径：

```bash
source ../../deploy/qwen/client.env.example
bash scripts/common/claude_with_local_model.sh   http://127.0.0.1:8121 qwen3.8-flash-next
```

此命令只连接已部署服务。一般接口 smoke 不等于完整 native Fix Loop 验证；完整实验还要检查 Messages、streaming、tool-result、图像、Agent 路由、compaction 和实际容量，并遵守独立输出与 seed 记账协议。权重、KV cache、日志和所有运行产物留在仓库外。
