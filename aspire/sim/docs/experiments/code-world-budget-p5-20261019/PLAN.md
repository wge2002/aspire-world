# p5 · p4 method + fixes + ASPIRE v1.0 development budget (drafted 2026-10-19 CST)

Parent: [p4 coverage](../code-world-coverage-p4-20261016/PLAN.md), its fixes
CC_TASK_2 / 4 / 5, the multi-suite stager of
[p4n](../code-world-hardest-p4n-20261018/) and the
[p4fix](../code-world-p4fix-20261018/) re-run. Task: `../code-world-coverage-p4-20261016/CC_TASK_6.md`.

## Method

p5 = the p4 method as it stands today, plus the development budget of ASPIRE v1.0.
Nothing else changes: code world, prediction contract (p3-search with the p4
coverage gate, triage and progress reasons), coverage gate, oracle development
gate, model (local Qwen, xhigh), protocol (development seeds 51–65, held-out
1–50, trial_timeout 900, max_steps 4000).

- **p4 fixes carried**: CC_TASK_2 (driver stops on structured terminal states and
  on 3 stalled rounds; sweep counted apart from repair; supervisor session
  teardown, final sweep, infra requeue), CC_TASK_4 (no sweep-candidate cap;
  freezable tie-break; `no_legal_action` terminal guard), CC_TASK_5 (derived
  held-out watchdog; held-out supplement path, `heldout_supplement.py` frozen).
- **Development budget aspire-v1** (`development_budget: "aspire-v1"` in every p5
  case; part of the protocol identity; opt-in, so every earlier cell is unchanged):
  - *phase A* — until development seed 51 actually passes: every attempt runs on
    seed 51, attempts there are uncapped; no sweep, no `select`, no freeze; phase A
    is never terminal, only the development deadline ends it;
  - *phase B* — the bundle that first passed seed 51 is v0 and is swept free over
    52–65; then at most **8 versions**, each run on **all 15** development seeds
    (first run `trial --phase repair --mode <mode> --mode-seeds <seeds>`, the rest
    `--phase sweep`) before the next is admitted; each version declares the failure
    mode it targets; **at most 3 versions per mode**, after which the mode is
    blocked (`attempts/mode_<mode>_BLOCKED.md`: root cause, what was tried) and gets
    nothing more; the per-seed 3-retry limit no longer applies;
  - *freeze* — only when the selected version passes 15/15, all 8 versions are
    used, or every development seed it still fails is in a blocked mode; selection =
    most passes, then fewer crashes (program_ok=false), then the simpler version;
  - *probes* — `trial --phase probe`: an observation-only REPL session (no motor
    command, no `step()`, `env` limited to the task language), never a version,
    spends nothing.
- **Deadlines**: development 24 h (was 14 h); held-out derived per CC_TASK_5
  (51120 s for 50 seeds); worst-case cell 139320 s (38.7 h); node window 2420 min,
  DLC max 2430 min.

Source of the budget rules: NVlabs/ASPIRE `main`
`.claude/skills/iterative-debugging/main-agent-prompt.md` §3 and
`subagent-prompt.md` Stage 1, as quoted in CC_TASK_6.md (read by the coordinator
2026-10-19; not re-downloaded here — the DSW had no route to GitHub).

## Cells (fresh; no ledger inherited)

| cell | suite | task (case of) |
|---|---|---|
| drawer_p5_r1, drawer_p5_r2 | libero_goal_swap | open_the_middle_drawer_of_the_cabinet |
| bowl_p5_r1 | libero_goal_swap | put_the_bowl_on_the_plate |
| bowldrawer_p5_r1 | libero_goal_swap | open_the_top_drawer_and_put_the_bowl_inside |
| goaltaskbowldrawer_p5_r1 | libero_goal_task | as goaltaskbowldrawer_p4n_r1b |
| spatialdrawer_p5_r1, spatialbetween_p5_r1 | libero_spatial_swap | as the p4n cells |
| objswapsoup_p5_r1 | libero_object_swap | as objswapsoup_p4n_r1 |
| bowldrawerclose_p5_r1, mugmicrowave_p5_r1, twomoka_p5_r1, mokastove_p5_r1 | libero_10_swap | as the p4n cells |

Three 1×8-GPU jobs (top pool), one per node, two units × two slots per node,
cells pinned to nodes by `study.json` `node_cells` (CC_TASK_6 supervisor change);
grouping by expected duration: see `CC_TASK_6_HANDOFF.json` `p5.assignment_basis`.

## What this round cannot say

- It changes the development budget, not the method's world or tools; a score
  change is the budget's (phase A focus on seed 51 + 8 fully swept versions), and
  it cannot be separated from run-to-run variance with one repeat per position.
- Held-out remains a score only; no held-out-derived rule enters the contract.
