# Prediction-contract base study (v1): does an explicit code-world prediction, consumed by the policy, raise success?

Date: 2026-10-08. Status: Task 1 (p1 harness, tests, analysis) accepted 2026-10-09 00:25 by an
independent rerun (237 passed, 9 suites; see CC_TASK_1_HANDOFF.json, CC_TASK_1_TESTS.log).
Task 2 (staging + preflights) in progress. Nothing here is a result.

Positioning: `docs/research/2026-10-08-code-world-cvpr-positioning.md` (spine 2).
Runtime map: `docs/research/2026-10-08-fulltext-reading-hexaanything-zetta-vpw.md` §6.

## Question

The r1 executable-world contract already calls `world.predict(call)` before every
public API call and `world.observe(event)` after it, but nothing compares the two,
records a mismatch, or lets the policy read it. This study adds an opt-in
prediction contract `p1`: the world predicts, per call, the facts that should hold
at the next real observation; the framework resolves each prediction against the
observed layer, records match / mismatch / unknown / unresolved, and exposes the
checks to the policy through reserved world queries. The policy decides what to do
with a mismatch inside its ordinary control flow. The framework never routes
recovery and never grades predictions (that is Zetta's design, which we avoid).

Headline figure: held-out success per arm (`off` vs `p1`), with the p1 arm's
mismatch rate, policy-consumption rate and mismatch-vs-outcome table beside it.

## Arms

Same fresh C / `judgment` / r1 cell as `code-world-gate-ablation-20261005`
(local Qwen3.8-Flash-Next native CC, xhigh, 3 retries per dev seed 51-65, frozen
held-out seeds 1-50 graded by the environment), gate fixed to `oracle`. The only
declared difference is `prediction_contract`:

| arm | predict() contract | what the policy can read |
|---|---|---|
| `off` | legacy r1: free-form or Unsupported, logged, never compared (byte-identical path) | nothing new |
| `p1` | `{"facts": {name: finite JSON}, "tolerance": {name: number}}` or Unsupported; resolved against the observed layer | `world.query("prediction_checks", ...)`, `world.query("prediction_summary")` |

Cells: 5 tasks x 2 arms x 2 repeats = 20. Tasks as in the gate study
(`bowl`, `drawer`, `bowldrawer`, `wine`, `stove`). Cell id `<task>_<arm>_r<repeat>`.

## Known pipeline risk carried over

12 of the 30 gate-study cells were blocked by the development-phase watchdog
(`support/full_deadlines.py` `DEVELOPMENT = 10 * 3600`) firing as a `TimeoutExpired`
on the single solver turn. v1 sets `DEVELOPMENT = 14 * 3600` (TOTAL 74 520 s, DLC
max 1 242 min, estimate 12-20 h per cell) and keeps every other watchdog. Queue on
8 x 8-GPU DLC jobs as before; each worker serves at most 3 of the 20 cells.

## Metrics

- Held-out success per arm and per task (environment-graded).
- p1 only: predictions committed / supported / malformed; resolved match /
  mismatch / unknown; unresolved at episode end; per-fact residual distribution.
- Policy consumption: `world_use_audit` `prediction_use` (reserved-query call
  sites with a dependency into a motion argument or an acting branch).
- Mismatch-vs-outcome: per development trial, whether any mismatch occurred and
  whether the trial passed (oracle), as a 2x2.
- Development cost: retries spent, development seconds, solver turns.

## Reading guide

- p1 > off on held-out with a visible margin and non-trivial consumption: spine 2
  is supported; proceed to v2 (representation ablation) on top of p1.
- p1 ≈ off and consumption near zero: the signal exists but is not used; redesign
  the exposure (not the routing) before concluding anything.
- p1 < off: predictions cost retries or mislead; inspect mismatch false alarms.

## Tasks

- Task 1 (CC): harness implementation, CPU tests, analysis script. Spec in
  `CC_TASK_1.md`. Acceptance by the coordinator: tests green, legacy path
  byte-identical, synthetic end-to-end demo of all statuses, prompt section only
  under p1.
- Task 2 (CC, after acceptance): `prepare-prediction-study.py` adapted from
  `prepare-gate-study.py`, per-cell DSW preflight, queue submission, launch.
