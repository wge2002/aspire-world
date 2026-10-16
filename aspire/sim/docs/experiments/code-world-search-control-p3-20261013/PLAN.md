# p3 search-control study · plan (drafted 2026-10-13 21:30 CST)

Parent: [code-world-runtime-p2-20261011](../code-world-runtime-p2-20261011/RESULTS.md).
Status: design + implementation task only. No cells staged, no jobs submitted.

## Claim under test

The p2 round showed that run-time consumption of prediction feedback does not
set held-out success (RESULTS.md §2–3). What sets it is whether the 45-attempt
fix loop finds a working physical primitive, and the two reps of a task differ
mainly in how many of those 45 attempts carry new information:

| | stove_r1 ✓ | stove_r2 ✗ | drawer_r1 ✓ | drawer_r2 ✗ |
|---|---|---|---|---|
| initial attempts (identical outcome signature) | 15 (13) | 16 (13) | 16 (6) | 15 (2†) |
| diagnostic probes | 4 | 5 | 7 | 6 |
| repair bundles (never-won) | 3 (1) | 15 (14) | 12 (10) | 21 (21) |
| identical bundle re-run after ≥2 failures | 0 | 6 | 0 | 2 |
| first success at attempt | 21 | 30 | 34 | – |

† signature counted on prediction-status counts only; all 15 failed on the
same handle-identity bug per findings.md.

The stuck reps measured the same residuals as the winning reps (knob rotation
0–0.45 rad vs ~0.8 needed; handle displacement 0 vs predicted 0.03–0.11 m) and
read them as "tune parameters" / "physically infeasible" instead of "change
primitive class". Hypothesis: **the world's prediction/observation ledger is
enough to turn trial-and-error into hypothesis testing — each charged trial must
test a hypothesis the ledger has not already refuted** — and that raises the
fraction of informative attempts, which is what the success rate depends on.

## Three layers (implement 1 and 3 first; 2 is a later task)

1. **Signature early stop of the initial batch.** A trial's signature = the
   ledger's prediction-status counts per entity/fact + rounded object-level
   residuals + final world facts + outcome. After k = 3 identical signatures
   in the initial phase the remaining initial seeds are deferred (not charged)
   and their attempts are banked for repair. Stop condition is "no new
   information", not "n failures".
2. **Refuted-hypothesis rejection** (later): rehearse a new bundle offline on
   recorded tapes, extract primitive class + commanded contact geometry, refuse
   to charge a trial whose hypothesis the ledger already refuted (same class,
   geometry within tolerance, predicted object effect already observed absent).
3. **Saturation ⇒ class pivot.** Per primitive class (declared by the policy),
   track best object-level progress toward the goal across bundles. When ≥ K
   bundles and ≥ N attempts of one class leave best progress < f·goal while
   gripper-level predictions match (arm went where it was told, object did not
   respond), the class is *saturated*: the next bundle must declare a new
   class; "physically infeasible" is not an accepted BLOCKED root cause while
   untried classes remain (benchmark tasks are feasible by construction).

Required contract additions (`p3-search`, superset of `p2-runtime`): the world
program exposes `progress() -> float in [0,1]` (its own estimate of goal
progress from observed facts, sibling of `done()`); at least one object-level
prediction per manipulation primitive (audit A's rule); the policy declares
`PRIMITIVE_CLASS` for its contact strategy.

## Validation before any new cells

Retrospective on the frozen p2 ledgers (read-only): the saturation signal must
fire in stove_r2 and drawer_r2 before attempt 30 and must not fire in stove_r1
before attempt 21 or in drawer_r1 before attempt 34; early stop must trigger at
attempt ≤ 5 in all four initial batches. Object-level progress for the
retrospective comes from recorded world facts (knob lever angle delta, handle
displacement along pull direction) and is labelled as a proxy.

## Later: cells

stove × 2, drawer × 2 under `p3-search` (same model, seeds, budget, oracle gate
as p2), plus bowl × 1 as a should-not-change control. Metrics: informative-
attempt fraction, attempts to first development success, number of distinct
primitive classes tried, held-out 1–50. Expected: no 0.00 rep on stove/drawer;
bowl unchanged at 1.00. Not expected: elimination of variance.

## Decisions after CC Task 1 (2026-10-14, user delegated to coordinator judgment)

1. Early stop stays on in every p3 cell; no dedicated early-stop experiment.
   Signature = prediction-status counts + rounded object residuals + outcome;
   the rounded object-fact-change term is dropped (it tracked perception noise:
   drawer_r1 11 → 4; retro early stop 5/4/4/3).
2. No further retrospective for saturation. The task-level proxy has no object
   identity (drawer_r2 credits top-drawer motion, stove_r2 credits a smoke
   angle-estimate swing). Layer 3 is validated on the new p3 cells from
   `search_control_report.json`: when saturation fired, which class followed,
   and whether progress/success changed after the pivot.
3. Contract adds: every manipulation primitive commits ≥ 1 gripper-level and
   ≥ 1 object-level prediction. Without the gripper-level row saturation cannot
   tell "arm did not arrive" (tune parameters) from "arm arrived, object did not
   respond" (change class).

Both layers run together; attribution comes from the per-trigger ledger trail,
not from an ablation. An ablation is deferred until the first p3 round is in.
