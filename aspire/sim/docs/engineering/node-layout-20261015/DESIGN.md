# Node layout · 2 Qwen units × 2 cells per 8-GPU node (locked 2026-10-15)

User decision 2026-10-15 ~21:30 CST, after the vLLM benchmark and the packing
analysis: "合理啊，赶快把这个逻辑定下来 … 一次 4 cell … 后面把旧逻辑删除，相关 md 都更新成按照这个逻辑跑实验".
This file is the contract for every new run; evidence is in
`aspire/sim/docs/logs/2026-10-15.md` and on DSW under
`/mnt/home/gewang/experiments/vllm-bench-20261015/` (`results.json`,
`quality.json`, `driver.round{1,2}.log`, `driver.log`, `quality.log`).

## 1. Model server (replaces the eager config)

Start from the p3 frozen `launch/reference/model-server.json` and change only:

- remove `--enforce-eager`;
- add `--compilation-config '{"mode": 0, "cudagraph_mode": "FULL_DECODE_ONLY", "cudagraph_capture_sizes": [1, 2, 4]}'`;
- remove `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True` from the env
  (it breaks custom all-reduce at graph capture: `custom_all_reduce.cuh:164
  'invalid argument'`); keep custom all-reduce on;
- `--gpu-memory-utilization`: one value per unit, set by the co-location
  measurement (§4); expected ≈ 0.84. Floor ≈ 0.76, because the KV pool must
  hold one 1,000,000-token request.

Unchanged: model and weights, TP4 + `--enable-expert-parallel --moe-backend
triton`, `--max-model-len 1000000`, `--max-num-seqs 4`, prefix caching,
parsers, chat-template kwargs (thinking, `reasoning_effort xhigh`), sampling
override, YaRN override, image limits, other env.

Measured (DSW, 4×L20Z, same GPUs for both): decode 148 tok/s single stream at
52k and 263k context (eager 8.9), 136 per stream at 2 streams, 121 at 4;
startup ~320 s (eager 820 s); greedy outputs identical to eager on text,
image and a 150k-token prompt (one trailing period differs). torch.compile
gave no gain (139 tok/s) and OOMs in inductor autotune unless autotune is
deferred — not used. TP2 does not fit (weights are 44.9 GiB per card at TP4).

## 2. Unit = 4 GPUs

One unit = one Qwen TP4 server (§1) + one SAM3 + one GraspNet + PyRoKi
(CPU) + **two cell slots**. Perception and simulators sit on the unit's own
four Qwen cards. An 8-GPU node runs two units (GPUs 0–3 and 4–7, one per
NUMA node); a 4-GPU DLC job runs one unit. Units are independent: separate
ports, logs, health checks; a failed unit marks its own cells as
infrastructure failures and does not stop the other unit.

Default in-unit placement (relative card index within the unit): SAM3 on 0,
GraspNet on 1, slot-a simulator on 2, slot-b simulator on 3; final placement
follows §4.

## 3. Cells and the queue

- Each slot claims the next cell from the shared queue (same atomic-claim
  semantics as p3) and runs it to completion before claiming again.
- A cell is bound to one unit for its whole life: its model endpoint, SAM3 /
  GraspNet / PyRoKi URLs and simulator GPU / EGL device come from its slot,
  and the binding is recorded in the cell's run record. Do not load-balance
  one cell across servers (vLLM data parallel would scatter its prefix cache;
  the solver's prefix-cache hit rate is 96–97 %).
- Why two cells per server and not four: per-cell in-flight context in p3
  was p50 0.23–0.30M, p95 0.38–0.53M, max 0.51–0.74M tokens; a server at
  util ≈ 0.84 holds ≈ 1.6M tokens, i.e. ≈ 0.8M per cell for two cells, while
  one util-0.95 server shared by four cells leaves 0.57M per cell. An evicted
  0.5M prefix costs ~20 s to re-prefill vs ~3.5 s to decode 512 tokens.

## 4. Co-location measurement (coordinator, DSW, before the first run)

On four free cards: one unit with §1 at a candidate util, SAM3 / GraspNet
on their cards, two real replay trials as the simulator + perception load,
and a concurrent decode benchmark. Record per-card peak memory, decode
tok/s with and without perception load, replay outcomes, and any service
errors. The chosen util and placement are written here when measured.

Measured 2026-10-15 22:31–22:58 on DSW GPUs 0,3,4,7 (L20Z, 81,559 MiB
each), util 0.84, script `vllm-bench-20261015/scripts/colocate.py`, output
`colocate-0347-u0.84/summary.json` (+ `nvidia-smi.csv`, service and replay
logs). Qwen ready in 310 s; KV pool 1,561,867 tokens (19.13 GiB).

| card (unit index) | tenant besides Qwen | peak MiB | free at peak |
|---|---|---|---|
| 0 (GPU 0) | SAM3 | 76,961 | 4,598 |
| 1 (GPU 3) | GraspNet | 78,907 | 2,652 |
| 2 (GPU 4) | slot-a simulator (+ PyRoKi env, no GPU memory) | 72,082 | 9,477 |
| 3 (GPU 7) | slot-b simulator | 72,082 | 9,477 |

Qwen alone shows 71,252 MiB per card in nvidia-smi at util 0.84 (≈ 2.7 GiB
above util × capacity: CUDA context, NCCL / all-reduce buffers). Tenant peaks:
SAM3 5.6 GiB, GraspNet 7.5 GiB, simulator 0.8 GiB.

Decode (tok/s): before load 147.6 single, 118 / 136 at two streams; during
the replays (34 rounds) single median 142.5 (min 98.6), two-stream median
124.6 (min 94.2); after 147.6, 125 / 135. Co-located perception costs ~3 %
at the median and up to ~33 % during perception bursts.

Replays: 6 trials (bowl_p3_r1 fix code, dev seeds 55/56/59 on slot a,
60/62/57 on slot b), all exit 0, 93–146 s each, 5 rewarded (seed 62 failed;
this code's held-out failure rate is 24 %, so one failure is not evidence
about co-location). No OOM, no tracebacks; all cards back to their prior
state afterwards.

**Chosen: util 0.82, placement as §2 default (SAM3 card 0, GraspNet card 1,
simulators cards 2 / 3, PyRoKi CPU).** 0.84 left only 2.6 GiB on the
GraspNet card at a peak seen over six trials; 0.82 adds ~1.6 GiB there
(≈ 4.2 GiB free) and shrinks the KV pool to an estimated ≈ 1.43M tokens
(81.6k tokens/GiB), still above one 1M request and two cells' p95 sum
(≈ 1.06M). The CC smoke (Task 4 step 三) must record the actual KV pool and
per-card peaks at 0.82.

## 5. What "old logic" means and what stays

Removed from the forward path (shared code, examples, run docs): the eager
model-server config, `expandable_segments`, and the one-cell-per-node GPU map
(Qwen 0–3, SAM3 4, GraspNet 5, sim 6, card 7 idle). Historical study
directories, their frozen `support/` / `launch/` copies and all results stay
untouched: they are the record of what those runs used (`docs/LATEST_EXPERIMENTS.md`:
"旧协议和支持源码保留原路径"), and the p3 r2 cells are still running from the
p3 results root.
