# P6d: method changes M2/M3/M4 on the p6c pipeline (CC_TASK_14, 2026-10-22)

Purpose: implement the task-independent method changes that CC_TASK_13
(`../code-world-method-diagnosis-20261022/REPORT.md`) recommends, on top of the
p6c pipeline repairs (CC_TASK_12 G5/G6), then stage a smoke and a 4-cell trial
on rbs-debug2. Target is still held-out >= 0.95 per task.

Every change sits behind one case key, `method_switches` (validated by
`scripts/libero/method_switches.py`). A case without it renders, admits, scores
and evaluates exactly as p6b/p6c did; that is what keeps the existing
1242-test baseline unchanged.

| Switch | Value in p6d | Change | REPORT.md |
|---|---|---|---|
| `subgoal_verification` | `true` | worker prompt + skill `subgoal-verification.md`: confirm every subgoal from observations, retry differently, stop acting once achieved | M3(a), 4.1, 4.2 |
| `judgment_calibration` | `true` | world goal judgment vs simulator, per seed, in `trial` / `status` / `check` | M3(b), 4.3 |
| `screening_gate` | `record_only` | rehearsal prediction-level rule and saturation refusal record instead of refuse | M3(c), 4.3 |
| `version_score` | `sweep_only` | a version's coverage and score come from its sweep rows only | M2(a) |
| `prefreeze_resweep` | `{margin: 2, max_candidates: 3}` | outer re-sweep of near-best complete candidates on 51-65, merged selection | M2(b)(c) |
| `unseen_seeds` | `66..80` | outer measurement of the frozen bundle after freeze, before held-out | M2(d), M1 |
| `heldout_watchdog` | `settle` | held-out policy timeout = failed seed (dev rule), not infrastructure | p6c open item |
| `api_revision` | `m4-r1` | solve_ik reports clipping / pose substitution; segmented move primitive; GraspNet seeded per request | M4(a)(b)(d) |
| `success_rule` | `libero_latch` | success at any control step within the 4000-step budget counts (standard LIBERO); robosuite's budget error ends the trial without a program error; development and held-out | coordinator decision on item 7 |

Skill files live outside `.claude/` (coordinator decision, 2026-10-22):
`cap/world_model/skills/subgoal-verification.md`, `cap/world_model/skills/api-revision-m4.md`
(frozen through `C_OVERLAY`, pinned by the runtime manifest, readable under the cell read guard).

## Roots and cells

| Launch | Result root / runtime root | Cells (N=4 each) | GPUs on rbs-debug2 |
|---|---|---|---|
| smoke | `/mnt/home/gewang/experiments/code-world-method-repair-p6d-20261022-smoke` / `/mnt/home/gewang/code/ASPIRE-code-world-method-repair-p6d-20261022-smoke` | `drawer_p6d_smoke1` (task of p6b drawer_p6b_r1; full path incl. resweep, unseen, held-out with seed 7 under a 60 s watchdog), `objtaskmilk_p6d_smoke2` (task of ns objtaskmilk_p6b_r1; Task 9 boundary note, 2 turns, GraspNet/solve_ik probe) | 0-3 (1 unit) |
| trial | `...-trial` / `ASPIRE-...-trial` | `mokastove_p6d_r1`, `objtaskmilk_p6d_r1`, `bowldrawerclose_p6d_r1`, `spatialtaskcookie_p6d_r1` (tasks of the ns / p6b cells of the same name) | 0-7 (2 units) |

Stager: `prepare-p6d-study.py --smoke|--trial --prepare|--verify` (imports the unchanged p6b stager).
Support overlay: p6b support, then p6c (`run_two_task_cell.py`, `worker_recovery.py`), then
`support/` here (p6d `run_two_task_cell.py`, `full_deadlines.py`; smoke root only: `smoke_p6d_driver.py`,
`smoke_heldout_inject.py`).

## Launch (coordinator, on rbs-debug2, as gewang)

1. `python3 host-gate.py --root <root> --kind smoke --replays` (GPU/identity/CUDA-under-model-env/env-check
   [+ GraspNet and step-budget replays]); writes `<root>/preflight/host-gate/<host>.json`.
2. `bash launch-smoke.sh` -> tmux `aspire-p6d-smoke`; after it ends, `tools/verify_p6d_smoke.py`.
3. `python3 host-gate.py --root <trial root> --kind trial`, then `bash launch-trial.sh` -> tmux `aspire-p6d-trial`.
Guards and log locations: `launch-common.sh` header.
