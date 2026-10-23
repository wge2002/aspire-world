# P6b-ns: p6b method on three never-run LIBERO-Pro suites + mokastove (CC_TASK_11, 2026-10-21)

Purpose: first cells in libero_spatial_task, libero_object_task and libero_10_task, plus a
mokastove rerun, under the p6b pipeline (r3 method + CC_TASK_9 worker recovery + B1/B2/B3).
No method or driver change: code world, prediction contract, coverage gate, aspire-v1 budget,
selection rule, 24 h development deadline, trial 900 s / max_steps 4000, dev 51-65 then
frozen held-out 1-50 are p6b node 0's.

| Cell (claim order) | Suite / bddl stem | Runtime task_language (seed 51) | N |
|---|---|---|---|
| mugtaskmicrowave_p6b_r1 | libero_10_task / KITCHEN_SCENE6_put_the_yellow_and_white_mug_in_the_microwave_and_close_it | put the white mug in the microwave and close it | 2 |
| mokastove_p6b_r1 | libero_10_swap / KITCHEN_SCENE3_turn_on_the_stove_and_put_the_moka_pot_on_it | turn on the stove and put the moka pot on it | 4 |
| spatialtaskcookie_p6b_r1 | libero_spatial_task / pick_up_the_black_bowl_next_to_the_ramekin_and_place_it_on_the_plate | Pick the akita black bowl next to the cookie box and place it on the plate | 4 |
| objtaskmilk_p6b_r1 | libero_object_task / pick_up_the_ketchup_and_place_it_in_the_basket | Pick the milk and place it in the basket | 4 |

`_task` suites remap the instruction: cell names follow the runtime goal, not the stem. The
worker's goal is `env.handle.task_language` (replay_trial fills `Goal:` from it; the worker
prompt's task-exploration step says the task language overrides the filename). Choice basis
and N reasons: `../code-world-repair-parallel-p6-20261020/CC_TASK_11_HANDOFF.json`; runtime
languages of all 30 tasks of the three suites: `task-language-probe.jsonl`.

Adaptation is stager-only (`prepare-p6b-ns-study.py`, importing the unchanged p6b stager):
the three positions are added to the position -> (suite, task) table; suite per cell is the
p4n mechanism; env_config franka_libero_traced.yaml as every libero_10 cell so far. No
shared-layer byte changes.

Roots: `/mnt/home/gewang/experiments/code-world-worker-recovery-p6b-20261021-ns`, runtime
`/mnt/home/gewang/code/ASPIRE-code-world-worker-recovery-p6b-20261021-ns`.
Resources: one TOP job (workspace 1493863 / quota quotal96jnu33mnl, priority 9), 1 worker x
8 GPU (two units x two slots, one libero_10 cell per unit), 48 CPU / 448Gi / 16Gi shm, node
window 2100 min (supervisor minimum 1944 for N=2), DLC max 2110 min. Submission by the
coordinator only (`submit-top-pool-node0.sh`).
