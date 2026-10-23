# P6b: r3 method + worker recovery (CC_TASK_9, 2026-10-21)

Purpose: rerun the four r3 cells that ended (or would end) without a score because of
pipeline gaps, with the gaps closed. No change to the method: same code world, prediction
contract, coverage gate, aspire-v1 budget (phase A seed 51 uncapped; phase B <= 8 versions /
<= 3 per mode), selection rule, 24 h cumulative development deadline, trial 900 s /
max_steps 4000, dev 51-65 then frozen held-out 1-50, N = 4.

| Node | Fresh cells (suite/task from the r3 case) | N |
|---|---|---|
| 0 | goaltaskbowldrawer_p6b_r1 (goaltaskbowldrawer_p6_r1), drawer_p6b_r1 (drawer_p6_r2, open_the_middle_drawer_of_the_cabinet), bowldrawerclose_p6b_r1 (bowldrawerclose_p6_r1), bowldrawer_p6b_r1 (bowldrawer_p6_r1) | 4 each |

No r3/p5 ledger, program, session or feedback is inherited. Roots:
`/mnt/home/gewang/experiments/code-world-worker-recovery-p6b-20261021`,
runtime `/mnt/home/gewang/code/ASPIRE-code-world-worker-recovery-p6b-20261021`.
Staged by `prepare-p6b-study.py` (imports the unchanged r3 stager).

Changes over r3 (all development orchestration / closeout):

1. Worker recovery. Native CC 2.1.220 cannot reload a returned subagent whose transcript
   exceeds 5 MiB ("could not be resumed: No transcript found"). When a coordinator turn's
   structured stream shows the primary's last SendMessage failed that way, with no later
   success and no subagent activity after it, the driver opens a recovery round: a new
   coordinator session dispatches generation N with the same frozen assignment plus a
   driver-written handoff (check/budget state, the worker's own reports and programs, ledger
   and trial paths). The hook admits that one dispatch only while the driver's request is
   open. Ledger, attempts, phase, versions, mode counts and the deadline are unchanged;
   `campaign_state.worker_generations` records lineage.
2. B1: unresolved infrastructure rows open an infrastructure round for the worker's
   close-infra flow; the same rows surviving 2 rounds stop development (phase B through
   end-development). At end-development, unresolved rows of a candidate other than the best
   complete one no longer block the freeze (reported as advisories); rows of a best
   complete candidate, the snapshot or an unattributable blocker still do.
3. B2: a nonzero / is_error solver turn with time left starts another round; 3 consecutive
   failed rounds without a ledger change stop (phase B closeout, phase A blocked).
4. B3: a phase-B `terminal` check goes through end-development (reason `terminal`) first.

Resources: one TOP job (workspace 1493863 / quota quotal96jnu33mnl, priority 9), 1 worker x
8 GPU, 48 CPU / 448Gi / 16Gi shm, node window 1890 min, DLC max 1900 min (same as r3 node 0:
four N=4 cells). Submission by the coordinator only.
