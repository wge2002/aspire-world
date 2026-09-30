# Full Qwen CodeWorld C run — 2026-09-24

Authorization: 用户要求修复管线，然后用现有 codeworld 类型跑完整 Fix Loop 和 Qwen 结果。This supersedes the September 22 pilot's 51–53-only/no-heldout scope for this NEW run. It does not authorize changing old frozen studies.

## Frozen research scope to prepare

- One task: `libero_goal_swap / put_the_bowl_on_the_plate`.
- Existing C full, `profile=judgment`, `executable_world_revision=r1`: no high-level strategy MD; generated executable world plus self-evaluation and rehearsal/replay. No method redesign or A comparison.
- Native Claude Code 2.1.220 solver, local `qwen3.8-flash-next`, xhigh, context 1,000,000 and max output 64,000; preserve verified static YaRN deployment. No alternative solver/provider fallback.
- Development seeds 51–65; native initial generation, failure diagnosis/repair, tested-bundle selection. Preserve current C three-TOTAL-charged-executions-per-seed accounting, including real diagnostics. No global 15-revision cap, forced one-version-per-seed schedule or motor/recovery caps.
- Freeze exact selected policy/world/runtime, then outer-only evaluation of every seed 1–50. Required final artifact: all 50 seed records, hashes and separately counted success/failure/crash/infrastructure errors. Held-out data never returns to solver.
- Same declared prior-C starter as the September 22 C continuation. Fresh campaign, not a refund/reset of the old pilot. No old A/Qwen baseline solution or held-out trajectory enters solver inputs.
- Real perception and nonprivileged APIs only; resolve config, constructed class and actual calls during preflight.

## Engineering acceptance before launch

See [ENGINEERING_TASK.md](ENGINEERING_TASK.md). Regressions must cover own-image reads through canonical output links, other-cell/heldout rejection, replay timeout versus authored exception, transient cache retry, blocker propagation without burning budgets, REPL exceptions despite zero shell exit, real mechanism-coverage reporting and a complete mocked dev→freeze→50-eval path. Review the full supervisor, not only the worker driver: old pilot supervisor requires `pilot_complete` and forbids held-out.

Framework tests do not establish task success. Unknown rehearsal predictions remain unknown, with their actual coverage reported. Engineering supplies no task-specific world thresholds or successful robot policies.

## Preliminary infrastructure check

Read-only verified 2026-09-24:

- Engineering checkout: `rbs-debug:/mnt/home/gewang/code/ASPIRE-world-revision-20260910-01a0894b`, base commit `7ba73d3bcac8f6b6d4a7d67ed4040988f768d282`, with existing modifications retained.
- Existing engineering CC pane `%62`, Opus 5/high/acceptEdits. Previous saved phase complete/failed API; cleared at saved boundary and assigned this fresh repair phase.
- Qwen model directory and dedicated vLLM environment exist. Previous retry4 actually completed its native compatibility fixture; new framework/guard paths still require fresh regression verification.
- DSW has 8 L20Z, with 2/3/7 showing zero GPU memory at initial observation; this alone does not establish ownership. Check task/process ownership before claiming a preflight GPU. Other allocated processes are outside this task and will not be stopped. Use DLC for TP4 Qwen plus dedicated perception/simulation.
- Prior DLC `dlc1k572i99aj6ts` is terminal `Succeeded` at 2026-09-23T17:19:59Z; its result audit remains 2 failed graded executions, not a successful method evaluation.
- Read-only DLC authentication works using the existing wrapper. Querying running jobs is supported. Free quota capacity is not yet established by that listing; verify during submission planning.

## New output and resource plan

- Source documents: this directory.
- Intended runtime: `/mnt/home/gewang/code/ASPIRE-code-world-qwen-full-20260924`.
- Intended campaign: `/mnt/home/gewang/experiments/code-world-qwen-full-20260924`.
- Never reuse occupied output directories or retain a symlink to the old campaign.
- One DLC node, 8 L20Z, 160 CPU, 800 GiB RAM, 64 GiB shared memory, priority 9. Qwen TP4 GPUs 0–3; SAM3 4; GraspNet 5; simulator/PyRoKi 6; 7 spare.
- Calibrated image and CPFS mount from `/mnt/home/gewang/ops-docs/dsw-dlc-environment.md`; UID/GID 10011 via existing prelude before writes. Use installed GraspNet vendor and checkpoint paths and preserve the v4 Qwen capacity fix.
- Expected experiment duration initially 8–16 hours including development and evaluation, plus repair/preflight. Exact process and DLC deadlines will be recorded with the launch plan; the pilot's 300-minute cap cannot serve as the full-run default.
- Both required platform and real-work preflight gates precede submission. Any real development-seed preflight execution is recorded and charged; no silent refund. No empty job for occupying resources.

This file records intent and verified prerequisites. It is not a launch receipt. Report actual job ID, state/log paths, started time and updated ETA after successful submission and startup verification. No recurring automation is requested.
