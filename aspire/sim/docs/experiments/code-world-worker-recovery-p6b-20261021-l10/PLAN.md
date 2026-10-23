# P6b-l10: p6b method on the two libero_10 cells (CC_TASK_10, 2026-10-21)

Purpose: give mugmicrowave and twomoka a fresh cell each under the p6b pipeline (r3 method +
CC_TASK_9 worker recovery + B1/B2/B3 closeout), on one 4-GPU unit. No method or pipeline
change: code world, prediction contract, coverage gate, aspire-v1 budget, selection rule,
24 h cumulative development deadline, trial 900 s, dev 51-65 then frozen held-out 1-50 are
p6b node 0's (`../code-world-worker-recovery-p6b-20261021/PLAN.md`).

| Node | Fresh cell (suite/task from the r3 case) | N |
|---|---|---|
| 0 (one unit, two slots) | twomoka_p6b_r1 (twomoka_p6_r1: libero_10_swap KITCHEN_SCENE8_put_both_moka_pots_on_the_stove) | 1 |
| | mugmicrowave_p6b_r1 (mugmicrowave_p6_r1: libero_10_swap KITCHEN_SCENE6_put_the_yellow_and_white_mug_in_the_microwave_and_close_it) | 2 |

N follows r3 / Task 7's libero_10 rule (twomoka 1, mugmicrowave 2); no new evidence to change it.
No r3/p5/p6b ledger, program, session or feedback is inherited. Roots:
`/mnt/home/gewang/experiments/code-world-worker-recovery-p6b-20261021-l10`,
runtime `/mnt/home/gewang/code/ASPIRE-code-world-worker-recovery-p6b-20261021-l10`.
Staged by `prepare-p6b-l10-study.py` (imports the unchanged p6b stager, which imports the
unchanged r3 stager).

Differences from p6b node 0 (`verify-extra.json` `byte_diff_vs_p6b_node0`): cell list, N,
roots/name, layout (one 4-GPU unit instead of two). Support, runtime, launch files other than
`study.json`, baseline contract are byte-identical; prompts differ only in the N-derived sweep
lines (`prompt-diff-vs-p6b-node0.txt`) and equal r3's same-task prompts after normalization.

Resources: one TOP job (workspace 1493863 / quota quotal96jnu33mnl, priority 9), 1 worker x
4 GPU (Qwen TP over 0-3; SAM3 0, GraspNet 1, slot simulators 2 and 3), 24 CPU / 224Gi / 16Gi
shm (the smoke shape), node window 2520 min (supervisor minimum 2369 for twomoka N=1; r3 node 1
used 2520 for these cells), DLC max 2530 min. Submission by the coordinator only
(`submit-top-pool-node0.sh`).
