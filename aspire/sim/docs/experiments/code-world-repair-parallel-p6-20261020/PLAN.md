# P6: repair + parallel, fresh independent C cells (2026-10-20)

User authorized preparation, integration, preflight, two TOP-pool submissions and startup verification in this same GPT-6.1 Sol chat. No engineering CC handoff or additional implementation agents. Experimental native Qwen workers remain the method itself. No P5 program/ledger inheritance, cross-cell skills, old P5 supplementation, external baselines, physical robot access, or pushes.

Authoritative source: rbs-debug:/mnt/home/gewang/code/ASPIRE-world-revision-20260910-01a0894b/aspire/sim. Task8 accepted; old engineering CC remains idle. Local old source preserved.

Method: P5 + accepted Task7 parallel + Task8 closeout/stagnation fixes. Qwen3.8-Flash-Next / xhigh, native CC 2.1.220, context 1,000,000 / output 64,000, actual perception and nonprivileged APIs. Each fresh cell develops seeds 51–65, then freezes and evaluates held-out 1–50. Phase A: seed51 until first actual success, uncapped attempts, no freeze; original cumulative development bound 24h. Phase B: <=8 versions / <=3 per failure mode. Trial 900s, max_steps 4000. N is held-out and free phase-B sweep concurrency; phase A and charged trials stay serial. trial_threads=2; N1 preserves the original serial execution.

| Node | Fresh cells | N |
|---|---|---|
| 0 | goaltaskbowldrawer_p6_r1, bowldrawer_p6_r1, objswapsoup_p6_r1, drawer_p6_r2 | 4 each |
| 1 | twomoka_p6_r1, mugmicrowave_p6_r1, drawer_p6_r1, bowldrawerclose_p6_r1 | 1,2,4,4 respectively |

Both are independent 1 worker x8-GPU jobs, TOP workspace 1493863 / quota quotal96jnu33mnl / priority9. Each worker requests 48CPU /448Gi RAM /16Gi shm, existing validated image and CPFS mount. Two 4-card units per worker: Qwen TP4 across the unit; unit-relative SAM3=0, GraspNet=1, slot-a sim=2, slot-b sim=3, PyRoKi CPU. Unit0 physical GPUs0–3, unit1 GPUs4–7. Ports: unit0 model8121/SAM3 8114/GraspNet8115/PyRoKi8116; unit1 +10. Nodes have separate DLC containers. Cell/node assignment is frozen; existing first-ready queue determines actual unit/slot, which the startup handoff records. No scheduler redesign; even the two N4 cells sharing a unit stay within Task7's measured maximum of eight replay processes per unit. Drawer repeats are on different nodes.

Fresh formal result root: `/mnt/home/gewang/experiments/code-world-repair-parallel-p6-20261020-r3`; runtime root: `/mnt/home/gewang/code/ASPIRE-code-world-repair-parallel-p6-20261020-r3`. Preparation refuses existing runtime cells/receipts. Separate native-smoke roots use `-native-smoke`; no smoke code, ledger, native session or feedback enters the formal cells.

Integration: P3 support -> P4 -> P5 -> accepted parallel/support -> P6-specific mixed-N overlay. Cases validate N1/2/4 and matching sweep N. Driver derives each sweep bound from its own N; supervisor derives its cell allowance from the slowest assigned case. Node0 held-out bound =13380s; node1 =51120s. Node cell allowances (including 24h development +1800s finalization): 101580s /139320s. Windows reserve 2400s cold service startup, all three existing 1920s native compatibility attempts, 900s claim-stop reserve and90s cleanup, plus rounding/headroom. Planned node windows1890/2520min; DLC max1900/2530min. These watchdog upper bounds are not expected runtimes or new action/retry limits; estimated first results8–16h, hard cells24–32h, plus queue delay.

Two integration details must be pinned: current `native_cc_trial_process.py` is explicitly added to the old runtime overlay list; the Task8 tracker is frozen as standalone `support/cell_process_tree.py`, preserving its class implementation without importing the supervisor/service stack (minimal cell runtimes omit node_layout and cache an older native_cc_runtime without stop_sessions). Old frozen P5/r2 copies stay unchanged; the engineering Task8 driver also receives the timeout-exception correction. The optional shared supervisor per-case timeout hook preserves old studies' exact timeout fallback.

Validation before submission: mixed N sizing/refusals/supplement remaining-seed bounds; supervisor legacy fallback, cleanup and queue tests; prepare/verify, fresh inputs and manifests, actual suite/task/env/task_language for all eight cells via separate observation-only seed51 checks; nonprivileged constructed API records; one real native Qwen unit smoke on free DSW GPUs4–7; frozen launch imports/driver tracker dependency; supervisor + DLC preflight for both final node commands. Reuse Task7 measured N1/N2/N4 perception/parallel evidence and Task8 development-only smoke/40 regressions. Read and verify final submit scripts, then submit each once. Do not infer startup from submission: verify job start times, health, all8 claims, native replies and first trial/perception evidence. No automatic reminders. Register ETAs only at final handoff through eta-float.

The initial pre-submit staged roots and service-only native smoke were preserved after the import-order audit. Formal submission uses fresh `-r2` roots. The tracker dependency test now reproduces actual campaign import order before importing the standalone tracker. No paid job was submitted from the superseded staging.


## Astra review and authorized r3 restart

Astra independently found that a genuine `subprocess.TimeoutExpired` bypassed the outer closeout path. The user explicitly authorized stopping both submitted r2 jobs, preserving all evidence, fixing and regression-testing the exception path, then starting completely fresh r3. r2 jobs `dlcgw7z7vz7mjrib` and `dlcw5ln9nu51y2g4` are confirmed Stopped; they are cancelled attempts, not completed experiments.

The r3 driver catches only `subprocess.TimeoutExpired`, records code124 and its timeout, then executes existing owned descendant cleanup, transcript/provenance/runtime/ownership checks and infrastructure refusal before existing end-development. True cumulative expiry uses deadline; a shorter per-turn timeout uses solver_exit. Phase A without eligible evidence and infrastructure faults still cannot finalize. No caps, budgets, prompts, ledgers or selection criteria change. r3 formal inputs inherit no r2/smoke policy, ledger, session or feedback.
