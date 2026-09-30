# Qwen Code World two-task paired comparison — 2026-09-26

The user requested two new LIBERO-Pro tasks of different observed difficulty, using the now-working native Qwen compatibility path to expose ordinary baseline and current method performance. This is a fresh comparison, not a continuation or reset of the bowl campaign.

## Task choice and evidence

Both tasks are in `libero_goal_swap`, keeping suite and simulator interface fixed:

| Task | Paper's repaired-program result, without Evo (seeds 1–50) | Role |
| --- | ---: | --- |
| `open_the_top_drawer_and_put_the_bowl_inside` | 43/50 | Relatively easier |
| `open_the_middle_drawer_of_the_cabinet` | 1/50 | Harder |

The previously tested `put_the_bowl_on_the_plate` is 18/50 in that same column. The column is a task-selection proxy, not a Qwen result or a prediction for the new runs. Source: ASPIRE arXiv:2607.00272v1, Appendix D.1, Table 7.

## Comparison

For each task, create fresh, independent native Qwen chains:

- A: ordinary original Fix Loop, high-level MD strategy files, no executable world.
- C: existing `judgment` / executable-world `r1` method, no high-level MD strategy files.

Use Claude Code 2.1.220 with the local `qwen3.8-flash-next` solver, xhigh, 1,000,000 context and 64,000 configured request output. The engineering CC model used to prepare the run is separate from this experimental solver. No cross-cell solutions, dev trajectories, skill promotions or outputs. The paired comparison measures the A package against the C package; it does not isolate world alone because the MD inputs differ.

Fresh task-specific generation from the public initial scene is required. Do not import the bowl prior-C starter, the bowl seed-51 diagnostic, old A solutions, any held-out trajectories, or old task-specific code. Both arms receive the same factual API references and original per-seed Fix Loop protocol.

Each cell: development seeds 51–65, at most three charged simulator executions per seed (including diagnostics); select a tested generalizable bundle; freeze; outer-only evaluate seeds 1–50 exactly once per frozen cell. Report initial development result, repair accounting, final-bundle development coverage, frozen held-out success/failure/crash/infrastructure statuses, model route, world use/feedback and runtime. Do not tune within a frozen cell using held-out evidence.

Use only nonprivileged traced LIBERO API with actual SAM3, GraspNet and PyRoKi service calls. Verify resolved environment/class and live perception during preflight; no privileged fallback. Preserve previous runtimes/results. Keep each new cell's runtime and output root independent and write-once.

## Execution and preflight

Use the proven Qwen full run at `code-world-qwen-full-20260924` as engineering reference. Adapt the task-specific stager, full driver and supervisor only as necessary to support these fresh cells; do not assume the bowl prior-C input or preflight import generalizes. Run meaningful engineering regressions and a task-specific real-work rehearsal before each submission. Record any diagnostic as a charged development attempt in its own cell. The platform preflight, DLC submission plan, resource check and run evidence are required before paid launch. Only submit an 8, 16 or 24 GPU job; on an 8 GPU worker the expected map is Qwen TP4 on 0–3, SAM3 4, GraspNet 5, simulator/PyRoKi 6, 7 spare.

Planned fresh study root: `/mnt/home/gewang/experiments/code-world-qwen-two-task-20260926`. Target runtimes under `/mnt/home/gewang/code/ASPIRE-code-world-qwen-two-task-20260926`; exact per-cell paths to be recorded in a write-once campaign plan. Initial estimate is 8–16 hours per cell based on the bowl C/full run's 7h50m; update after preflight/launch. Never overwrite or mix previous outputs.
