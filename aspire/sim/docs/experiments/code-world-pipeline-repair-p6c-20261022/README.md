# p6c pipeline repair source (CC_TASK_12, 2026-10-22)

Source only: nothing here is staged, frozen or submitted. A next round that wants
these repairs overlays `support/` on the p6b support set (the p6b stager's
`OVERLAY` mechanism) and freezes the worktree runtime files listed below.

| File | Change |
|---|---|
| `support/worker_recovery.py` | p6b copy + `failure_evidence` (G5) and a reason-aware `handoff` |
| `support/run_two_task_cell.py` | p6b copy + G5 recovery round (`worker_api_failure`) and G6 outer `close-infra` disposition |
| `scripts/common/native_cc_trial_process.py` (worktree) | `run_replay` persistence grace (60 s) for a policy that already finished |
| `scripts/libero/native_world_protocol.py` (worktree) | `watchdog_verdict`: persistence cut -> recorded outcome, policy timeout -> failed trial, stalled -> infrastructure |
| `scripts/libero/native_world_fixloop_state.py` (worktree) | `finish_trial` records the verdict and settles a settled one |
| `scripts/libero/runtime/native_world_protocol.py` (worktree) | `close-infra` settles a watchdog row from its own evidence |

`tools/make_replay_fixtures.py` builds `tests/fixtures/p6c_repair/` (read-only
copies of l10 mugmicrowave_p6b_r1 and twomoka_p6b_r1). Tests:
`tests/test_p6c_repair.py`. Root causes, rules, census and test results:
`docs/experiments/code-world-repair-parallel-p6-20261020/CC_TASK_12_HANDOFF.json`.
