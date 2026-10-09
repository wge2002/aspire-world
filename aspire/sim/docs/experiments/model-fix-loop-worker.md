# Shared model worker for LIBERO Fix Loop

`scripts/libero/fix_loop_worker.py` is the common Stage 0/1 execution loop for
local DeepSeek and Qwen endpoints. `deepseek_fix_loop_worker.py` and
`qwen_fix_loop_worker.py` are thin entrypoints selecting request defaults.
They do not contain separate scheduling, completion, or memory implementations.
Native Claude Code coordination remains a separate execution harness.
To connect these models while retaining the native CC harness, use the
[Claude Code connection guide](claude-code-local-models.md) instead. Explicitly
record the chosen harness in the experiment preflight.

The worker follows the canonical development partition, seeds 51–65, with an
initial batch and at most three repair replays **per failed seed**. It never
starts held-out evaluation itself. Task analysis, policies, diagnoses, repair
choices, notes, and findings are produced by the experimental model.

## What changed after the DeepSeek debugging run

The September 4 Qwen worker retained its message history while shortening old
tool payloads. The September 5 DeepSeek fork instead discarded older messages,
with `keep_turns=10` actually counting messages. On September 6 another roughly
400 lines of orchestration were added, including task-specific diagnosis text
and milestone gates. Those gates accidentally capped the whole task at three
repairs, blocked later evidence reads, and accepted placeholder findings.
This was a change to the experimental harness, not an unchanged framework
suddenly failing. See the [attribution audit](../logs/2026-09-06-deepseek-audit.md).

The shared worker replaces those gates with runner-recorded evidence:

- `run_trial(phase, seed, code_path)` allocates a separate directory and code
  snapshot for every scene/smoke/initial/repair invocation. Traces for repeated
  seeds cannot overwrite each other. The model does not maintain trial counts.
- Each initially failed seed must have a successful repair or three failed
  repairs plus its own BLOCKED note. Finishing seed 51 leaves 52–65 available.
- Code, API, trace, image, and report reads remain available in every phase.
  There is no read-count quota or hard-coded task diagnosis.
- History retention counts complete assistant/tool turns. Actual trial errors,
  per-seed budgets, paths, and model-written `notes.md` are reintroduced after
  trimming or restart. Context rejection reduces retained turns, not the fixed
  output-token allocation. An oversized fixed prompt stops as a configuration
  error instead of silently changing the experiment's budget.
- `development_state.json` persists counts, model usage, elapsed active time,
  and settings. `--resume` requires the same settings and framework/prompt
  identity. It does not grant a fresh set of repair or model-step budgets.
- Completion requires the full development protocol, substantive findings,
  matching final/working-code copies, and development evidence for the selected
  program. Any synthesis must be tested within the existing repair budget.
  The canonical minimal observation fallback is allowed when every candidate
  crashes, and its lack of development evaluation is reported explicitly.
- The held-out validator rechecks shared-worker evidence before launching a
  process. Native CC campaigns without this ledger keep their original workflow.

## Launch after the required experiment preflight and confirmation

Use a fresh isolated checkout with the approved runtime patches, dependencies,
and unchanged initial skill template. Do not copy historical experiment outputs.
This implementation does not resume the September 5/6 debugging campaign and
does not migrate its old logs into a new ledger.

From `aspire/sim`, the DeepSeek entrypoint is:

```bash
.venv-libero/bin/python3 scripts/libero/deepseek_fix_loop_worker.py \
  --repo . --suite libero_object_swap \
  --task pick_up_the_alphabet_soup_and_place_it_in_the_basket \
  --gpu 4 --cuda-visible-devices 4,0 --egl-device-id 0 \
  --endpoint http://127.0.0.1:8120/v1/chat/completions \
  --model deepseek-v4-flash-vision-exp --reasoning-effort max
```

The `4,0`/EGL-0 mapping is the previously validated L20Z host workaround, not
a portable default. Recheck ownership and mapping during the next preflight.
The worker itself defaults to the assigned GPU alone. Qwen uses the other thin
entrypoint with its approved endpoint/model/reasoning settings; all protocol
logic and tools are shared. Explicitly freeze request budgets for a comparison.

Defaults are one separately recorded crash smoke, three repairs per failed seed,
4,096 output tokens per request, eight complete recent turns, 400 model steps,
and 12 hours of active worker time. The latter limits cover the entire campaign,
including resumes. Reaching them yields an incomplete result. They must be
reviewed against the intended experiment scope before launch.

After Stage 1 succeeds and the coordinator has completed the required findings
review/skill-promotion record, use the existing `run_fix_loop_validation.py` with
the selected `--fix-code`, `--gpu`, and the same explicit
`--cuda-visible-devices 4,0 --egl-device-id 0` if that mapping was approved.
Its default held-out partition remains seeds 1–50. Do not invoke the old
campaign-specific supervisors, which contain obsolete paths and arguments.

## Validation and limits

Offline regression tests run without model requests or simulator imports:

```bash
python3 -m unittest discover -s tests -p test_model_fix_loop_worker.py -v
```

Coverage includes a complete synthetic 15-initial/45-repair campaign, per-seed
budgets, persistent resumes, late evidence reads, tool-message grouping,
context retries, invalid completion, final-program selection, and held-out
admission. These tests establish harness behavior with fake model/simulator
returns; they are not a new DeepSeek/Qwen benchmark or live inference smoke.

The shell and generated Python remain a reliability boundary, not a security
sandbox. Keep the established isolated-host/checkouts and credential rules.
No physical-robot workflow is involved.
