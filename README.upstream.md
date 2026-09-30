# ASPIRE: Agentic /Skills Discovery for Robotics

[Project Page](https://research.nvidia.com/labs/gear/aspire/) &ensp;|&ensp; [Paper](https://arxiv.org/abs/2607.00272)

<img src="assets/media/covervideo.gif" alt="ASPIRE robot demonstrations" width="100%">

ASPIRE is a new type of continual learning: "training" is skill refinement instead of gradient descent. 

"Trained model" is a repo of sensorimotor skills instead of floating weights. 

“Distributed training” is a panel of agents each practicing a different skill instead of sharded minibatches.

## Quick Start

### Run with a coding agent

To get started with ASPIRE, launch a coding agent such as Codex or Claude Code and enter the following prompt:

```text
Clone the repo: https://github.com/NVlabs/ASPIRE/, Read AGENTS.md and 
run the complete ASPIRE LIBERO-Pro Goal-Swap Quick Start for all ten 
tasks in the libero_goal_swap suite.

Before executing, report the required GPUs, credentials, gated weights, 
services, expected runtime, seed partitions, and output paths. Wait for 
my confirmation before launching. Do not access real-robot code.
```

The canonical procedure for a quick start is [`aspire/sim/.claude/libero/fix-loop/QUICKSTART.md`](aspire/sim/.claude/libero/fix-loop/QUICKSTART.md). The agent must complete preflight and wait for confirmation before installing dependencies, starting services, or launching trials.

**Reference agent environments:** ASPIRE is coding-agent agnostic. Our simulation workflow is packaged for reproduction with Claude Code with Opus 4.6 1M, while the real-robot agent experiments were conducted with Codex. All coding agents can follow the model-neutral instructions in [`AGENTS.md`](AGENTS.md), although orchestration behavior may differ.

### Run with local DeepSeek-V4-Flash-Vision-Exp

The default local DeepSeek integration uses [`DeepSeek-V4-Flash-Vision-Exp`](https://huggingface.co/deepseek-ai/DeepSeek-V4-Flash-Vision-Exp), the multimodal counterpart of the Flash model. On the pinned checkouts below, its 167.83 GB directory is only 0.56% larger than the 166.90 GB text-only `DeepSeek-V4-Flash-0731` directory; it keeps the same 43 language layers and adds a 32-layer vision tower. The same model can therefore generate skills and inspect ASPIRE's visual feedback without a material increase in checkpoint storage. The launcher provides an OpenAI-compatible endpoint with four-way tensor parallelism plus expert parallelism, FP8 KV cache, DeepSeek reasoning parsing, structured tool calls, and streaming responses.

This experimental checkpoint needs a vision-capable vLLM build containing upstream DeepSeek-V4 Vision support; the vLLM 0.25 environment used by the older text-only checkpoint is not compatible. The [upstream vLLM recipe](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4-Flash-Vision-Exp) uses its dedicated image and is verified on GB200. For a native installation, use a current nightly containing [vLLM PR #54566](https://github.com/vllm-project/vllm/pull/54566) and validate it on the target GPUs before a benchmark run.

Download the 48-shard checkpoint (about 156 GiB on disk), prepare that vLLM environment, then run from `aspire/sim`:

```bash
cd aspire/sim

# Pin a revision for reproducible downloads.
hf download deepseek-ai/DeepSeek-V4-Flash-Vision-Exp \
  --revision 6821d6ad3681a4b137b066b76094fa82ebd0a380 \
  --local-dir /path/to/DeepSeek-V4-Flash-Vision-Exp

export DSV4_MODEL_PATH=/path/to/DeepSeek-V4-Flash-Vision-Exp
export DSV4_VLLM_BIN=/path/to/vision-capable/vllm
export DSV4_GPUS=0,1,2,3

# Optional on a shared host; the conservative default is 0.90.
# export DSV4_GPU_MEMORY_UTILIZATION=0.75

# Optional when CUDA compatibility libraries or nvcc are not on
# the default search paths.
# export DSV4_CUDA_COMPAT_DIR=/path/to/cuda/compat
# export DSV4_NVCC=/path/to/cuda/bin/nvcc
# export DSV4_CUDA_HOME=/path/to/cuda

# Only for an installation whose pip nvcc is newer than its CUDA headers.
# export DSV4_NVCC_PREPEND_FLAGS=-DCCCL_DISABLE_CTK_COMPATIBILITY_CHECK

bash scripts/common/serve_deepseek_v4_flash_vision_exp.sh start
bash scripts/common/serve_deepseek_v4_flash_vision_exp.sh smoke
```

The prepared `rbs-debug` installation uses:

```bash
export DSV4_MODEL_PATH=/mnt/workspace/gewang/models/DeepSeek-V4-Flash-Vision-Exp
export DSV4_VLLM_BIN=/mnt/workspace/gewang/envs/deepseek-v4-flash-vision-exp-vllm-nightly/bin/vllm
export DSV4_CUDA_COMPAT_DIR=/mnt/workspace/gewang/runtimes/cuda-compat-13-0/usr/local/cuda-13.0/compat
export DSV4_NVCC=/mnt/workspace/gewang/runtimes/cuda-home-13-shim/bin/nvcc
export DSV4_CUDA_HOME=/mnt/workspace/gewang/runtimes/cuda-home-13-shim
export DSV4_NVCC_PREPEND_FLAGS=-DCCCL_DISABLE_CTK_COMPATIBILITY_CHECK
export DSV4_STATE_DIR=/mnt/workspace/gewang/logs/deepseek-v4-flash-vision-exp
export DSV4_GPUS=0,1,2,3
```

That environment contains vLLM `0.28.1rc1.dev357+g4ae622828`. The four-L20Z validation passed endpoint identity plus real-image, reasoning-stream, structured-tool-call, and ASPIRE-client smoke tests.

For a detached start over SSH, set `DSV4_WAIT_SECONDS=0`; the command returns after recording the server PID, and `status` can be used until the endpoint becomes ready. A first native start can spend several minutes loading/repacking weights and warming JIT kernels.

Point an ASPIRE launch command at the resulting endpoint:

```bash
--server-url http://127.0.0.1:8120/v1/chat/completions \
--model deepseek-v4-flash-vision-exp \
--reasoning-effort max \
--use-visual-feedback True
```

When using one of the checked-in `*_multimodel_*` robosuite configs as a starting point, also pass `--use-parallel-ensemble False --use-multimodel False`; otherwise that config selects its ensemble models instead of the `--model` above.

The launcher uses the upstream measured 32,768-token context by default; change it with `DSV4_MAX_MODEL_LEN`. The checkpoint needs roughly 202 GB of VRAM before KV cache, so four 80 GiB cards are the practical minimum for this profile. The validated L20Z profile uses `DSV4_MAX_NUM_SEQS=4`, `DSV4_GPU_MEMORY_UTILIZATION=0.90`, and DSpark speculative decoding with three draft tokens. It leaves 21.85 GiB of KV cache per card and supports 3.44 concurrent 32,768-token requests. The validation smoke accepted 68.1% of draft tokens with a mean acceptance length of 3.04. Set `DSV4_ENABLE_DSPARK=0` to return to ordinary autoregressive decoding. The launcher defaults to NCCL with `--disable-custom-all-reduce`: the nightly vLLM custom kernel failed with `invalid argument` during full CUDA-graph capture on this host. Set `DSV4_DISABLE_CUSTOM_ALL_REDUCE=0` only after validating the custom backend on the target host. Use the same launcher with `status` or `stop` to inspect or stop the service.

The earlier text-only `serve_deepseek_v4_flash_0731.sh` launcher remains available as a rollback path, but it cannot consume image feedback.

### Run with local Qwen3.8-Flash-Next vision

ASPIRE also supports the multimodal `Qwen3.8-Flash-Next-FP8` checkpoint through an OpenAI-compatible vLLM endpoint. The client recognizes both the official Hugging Face model IDs and the shorter `qwen3.8-flash-next` served-model alias, preserves image data URLs, translates ASPIRE's MP4 feedback into Qwen's `video_url` message format, maps ASPIRE reasoning levels to Qwen's thinking controls, and reads the separate reasoning stream returned by vLLM.

Use a Qwen3.8-capable vLLM 0.29.0+ runtime. The official recipe currently requires the dedicated `vllm/vllm-openai:qwen38-flash-next` image; the older DeepSeek-V4 environment above is not sufficient. From `aspire/sim`:

```bash
cd aspire/sim

export QWEN38_MODEL_PATH=Qwen/Qwen3.8-Flash-Next-FP8
export QWEN38_VLLM_BIN=/path/to/qwen38-capable/vllm
export QWEN38_GPUS=0,1,2,3

bash scripts/common/serve_qwen3_8_flash_next.sh start
bash scripts/common/serve_qwen3_8_flash_next.sh smoke
```

The smoke test covers reasoning, an inline PNG vision request, and structured tool calling. Point ASPIRE at the endpoint and enable direct visual feedback with:

```bash
--server-url http://127.0.0.1:8121/v1/chat/completions \
--model qwen3.8-flash-next \
--reasoning-effort high \
--use-visual-feedback
```

The default ASPIRE temperature of `1.0` matches Qwen's thinking-mode recommendation. If you select non-thinking mode with `--reasoning-effort minimal`, Qwen recommends adding `--temperature 0.7`.

The launcher defaults to the native 262,144-token context and four-way tensor parallelism. Set `QWEN38_GPUS` to eight IDs to use the required TP8 plus expert-parallel configuration; plain TP8 is incompatible with the official block-FP8 checkpoint. On a four-GPU host, `QWEN38_ENABLE_EXPERT_PARALLEL=1` enables TEP4. If additional GPU memory is needed for KV cache or multimodal batches, set `QWEN38_PLE_CPU_OFFLOAD=1` to move the 51B n-gram embedding table to host memory; reserve at least 51 GB of host RAM plus runtime headroom.

This integration follows the upstream Qwen and vLLM launch recipes but has not yet been benchmarked on the repository's reference host. The model weights use the Qwen Community License 1.0 and are not distributed with ASPIRE.

### Reproduce full paper results

Name the suite and experiment explicitly. If neither is named, the agent should present this table and stop for selection.

<table>
  <thead>
    <tr>
      <th>Suite</th>
      <th>Experiment</th>
      <th>Runbook</th>
      <th>Video</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td>LIBERO-Pro</td>
      <td>Fix Loop</td>
      <td><a href="aspire/sim/.claude/libero/fix-loop/INSTRUCTIONS.md"><code>libero/fix-loop/</code></a></td>
      <td rowspan="3"><video src="https://github.com/user-attachments/assets/15c0b425-9fe9-4313-8a5b-8c7da54c24b7" width="240" controls></video></td>
    </tr>
    <tr>
      <td>Robosuite</td>
      <td>Fix Loop</td>
      <td><a href="aspire/sim/.claude/robosuite/fix-loop/INSTRUCTIONS.md"><code>robosuite/fix-loop/</code></a></td>
    </tr>
    <tr>
      <td>BEHAVIOR-1K</td>
      <td>Fix Loop</td>
      <td><a href="aspire/sim/.claude/behavior/fix-loop/INSTRUCTIONS.md"><code>behavior/fix-loop/</code></a></td>
    </tr>
    <tr>
      <td>LIBERO-Pro</td>
      <td>Evolutionary Search</td>
      <td><a href="aspire/sim/.claude/libero/evosearch/INSTRUCTIONS.md"><code>libero/evosearch/</code></a></td>
      <td rowspan="2"><video src="https://github.com/user-attachments/assets/edd2e5e1-b7d6-408a-bb62-23729df14db2" width="240" controls></video></td>
    </tr>
    <tr>
      <td>Robosuite</td>
      <td>Evolutionary Search</td>
      <td><a href="aspire/sim/.claude/robosuite/evosearch/INSTRUCTIONS.md"><code>robosuite/evosearch/</code></a></td>
    </tr>
    <tr>
      <td>LIBERO</td>
      <td>Zero-Shot Transfer</td>
      <td><a href="aspire/sim/.claude/libero/zeroshot-transfer/INSTRUCTIONS.md"><code>libero/zeroshot-transfer/</code></a></td>
      <td rowspan="4"><video src="https://github.com/user-attachments/assets/8676fa10-6719-477f-9d60-a4ad241a3de3" width="240" controls></video></td>
    </tr>
    <tr>
      <td>LIBERO-Long-Pro</td>
      <td>Library-Size Scaling</td>
      <td><a href="aspire/sim/.claude/libero/library-size-scaling/INSTRUCTIONS.md"><code>libero/library-size-scaling/</code></a></td>
    </tr>
    <tr>
      <td>LIBERO-Long-Pro</td>
      <td>Inference-Time Scaling</td>
      <td><a href="aspire/sim/.claude/libero/inference-time-scaling/INSTRUCTIONS.md"><code>libero/inference-time-scaling/</code></a></td>
    </tr>
    <tr>
      <td>Robosuite</td>
      <td>Training Law</td>
      <td><a href="aspire/sim/.claude/robosuite/training-law/INSTRUCTIONS.md"><code>robosuite/training-law/</code></a></td>
    </tr>
    <tr>
      <td>YAM Bimanual</td>
      <td>Sim-to-Real</td>
      <td><a href="aspire/real/README.md"><code>aspire/real/</code></a></td>
      <td><video src="https://github.com/user-attachments/assets/91b38cd3-3a38-4f56-9758-f986d50c5956" width="240" controls></video></td>
    </tr>
  </tbody>
</table>


Before a paper-scale launch, the agent must report the selected tasks, seed schedule, expected trial count and runtime, GPU and credential requirements, services, and output paths, then wait for explicit confirmation.

> [!WARNING]
> ASPIRE executes language-model-generated Python with full import access. Trial processes and watchdogs are not a security sandbox. Run generated code on an isolated host without credentials or sensitive mounts, restrict network access, and never grant a simulation agent access to physical hardware. Real-robot work requires the controls in [`aspire/real/AGENTS.md`](aspire/real/AGENTS.md) and separate operator authorization.

## Contribution Guidelines

Start with [CONTRIBUTING.md](CONTRIBUTING.md). Third-party contributions must include a Developer Certificate of Origin sign-off.

## License

ASPIRE material owned by NVIDIA or contributed under the project license is
available under the [Apache License 2.0](LICENSE). Third-party materials retain
their original terms. See [NOTICE](NOTICE) and the central
[licensing and compliance index](LICENSES/README.md) before using or
redistributing the full stack.

## Citation

If you find ASPIRE useful in your research, please cite:

```bibtex
@article{lu2026aspire,
  title   = {ASPIRE: Agentic /Skills Discovery for Robotics},
  author  = {Runyu Lu and Yubo Wu and Ethan Kou and Letian Fu and Wenli Xiao and
             Ajay Mandlekar and Yinzhen Xu and Guanya Shi and Ken Goldberg and
             Ang Chen and Mosharaf Chowdhury and Yuke Zhu and Linxi "Jim" Fan and Guanzhi Wang},
  year    = {2026},
  journal = {arXiv preprint arXiv:2607.00272},
  url     = {https://arxiv.org/abs/2607.00272}
}
```

ASPIRE was developed by researchers from NVIDIA, the University of Michigan, the University of Illinois Urbana-Champaign, UC Berkeley, and Carnegie Mellon University.
