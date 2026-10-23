# p4 development-coverage study · plan (drafted 2026-10-16 CST)

Parent: [p3 search-control](../code-world-search-control-p3-20261013/RESULTS.md).
Status: design only. No cells staged, no jobs submitted.

## Where the p3 round leaves the score

`p3 RESULTS.md` §1: stove 1.00 / 0.76, drawer 0.70 / 0.40†, bowl 0.76 (control);
mean 0.81 over the four solver-selected cells against p2's 0.52 at the same
positions. The round's own stated limit is that nothing separates the three
p3-search layers from run-to-run variance, and that each position ran once.

The coordinator read every cell's development ledger on 2026-10-16 and found a
**second, independent gap that p3 did not name**: the bundle a cell freezes has
been tested on only a handful of development seeds.

| cell | seeds the frozen bundle ran | passes | held-out |
|---|---|---|---|
| stove_p3_r1 | 10 | 10 | 1.00 |
| stove_p3_r2 | 6 | 6 | 0.76 |
| bowl_p3_r1 | 5 | 4 | 0.76 |
| drawer_p3_r1 | 2 | 2 | 0.70 |
| drawer_p3_r2 † (supplementary) | 5 | 4 | (0.40) |

Every cell reports "10–12 development passes", but those passes are spread
across different bundles; the frozen bytes saw far fewer seeds, and the seeds
the early stop deferred were never re-run against the frozen bundle. The one
cell whose frozen bundle covered its whole initial batch (stove_p3_r1) is also
the one that held up at 1.00.

Two further findings from the same read:

- **The solver's own selection reason overclaims.** stove_p3_r2's says the
  bundle "passed 12 of 15 dev seeds"; the frozen bytes ran 6 trials. The
  selection evidence is therefore not auditable from the ledger alone.
- **The progress channel is dead on drawer.** `search_control_report.json` for
  drawer_p3_r1 shows `observed_attempts: 0` in every primitive class. Its world
  returns `None` from `progress()` whenever the handle fact is not `known`
  (`fix_world_program.py::progress` → `if h["status"] != "known" or
  disp["status"] != "known": return None`), so layer 3 had no evidence at all
  on the task that needed it most.

## Claim under test

The p3 round supports one mechanism cleanly: the ledger decides when a trial
stops carrying new information, and the budget it recovers is spent on new
primitive classes. p4 tests the **other half of the same idea**: the ledger
decides when a candidate is actually finished being tested.

> A candidate bundle is only *evidence* once it has been run on the whole
> development seed set, and the failure modes that set exposes are what the
> next repair round is aimed at. Selecting on partial coverage is how a
> development-fitted candidate reaches held-out with an unknown failure rate.

This is the same story as spine 2 of the paper positioning (an executable world
that the agent can inspect, predict against and audit), one level up: p3 made
the world decide *whether to keep testing a class*; p4 makes it decide *whether
a candidate is verified*.

## Three changes, all inside the existing contract

### 1. Sweep the full development seed set before freezing

Today `check` / `select` accept a bundle whose coverage is partial. Required
change: a cell may record `selected` only for a bundle whose own executed
development trials cover every seed in 51–65 — deferral by early stop included.

- Missing seeds are run with the candidate's exact bytes (the same
  `fix_code.py` / `fix_world_program.py` digests), charged against the banked
  attempts the early stop released, before the cell finalizes.
- If the banked attempts cannot cover the sweep, that is reported as a blocker
  with the per-seed record, never silently skipped.
- Held-out stays sealed throughout; the sweep only spends development seeds that
  the cell already had a budget for.

### 2. Triage the sweep's failures by signature, then repair once

The sweep produces a per-seed pass/fail vector for one bundle. Failures are
grouped by the ledger's own signature (`search_control.trial_signature`: rounded
object-level residuals + world facts + outcome) so the solver repairs a named
mode rather than re-drawing. Example from bowl_p3_r1: six no-lift seeds
(rim rake) and six lift-then-release seeds are two modes with different fixes,
and the p3 solver never saw them as modes.

- The triage table is written to the cell (mode → seeds → signature fields) and
  handed to the solver as the next repair's input.
- One repair round is allowed. It must also be swept in full before freezing.
- Repair uses the same budgets and the same world; no new tools, no held-out.

### 3. Make `progress()` fail loudly, and fix drawer's world

The progress channel must distinguish "not observed" from "observation
channel broken". Changes:

- Contract: when `progress()` returns `None`, the world must record *why* —
  `unobserved` (target not re-observed since the last manipulation) or
  `unavailable` (the world cannot resolve the fact at all). The report carries
  the two counters separately, so a cell whose channel is dead is visible
  instead of being counted as "no evidence".
- `drawer_p3_r1` is the proof case. The reporting layer (CC Task 3) is done;
  what is missing is the world-side rule that keeps the handle association
  recoverable instead of dropping it. The fix goes into the world template and
  the prompt section, not into a task-specific patch.

## Cells

- stove × 2, drawer × 2, bowl × 1 (same as p3, so the p3 comparison is
  position-for-position), plus wine × 1 and bowldrawer × 1 — the two tasks p2
  left at 0.30/0.02 and 0.04/0.00, which is where the main table has the most
  room.
- 7 cells total. One 8-GPU node holds two units × two slots = 4 cells, so the
  round is two nodes (four slots on the first, three on the second) and fits in
  one pass if each cell needs only its sweep; a cell that also spends a repair
  round and a second sweep may need a third node.
- Run on the locked node layout (`docs/engineering/node-layout-20261015/DESIGN.md`):
  unit = Qwen TP4 (B5 config, util 0.82) + SAM3 + GraspNet + PyRoKi + two slots.
  This is the layout's first full-mode run; the smoke validated the wiring, the
  supervisor stop and GraspNet under grasp load.
- No p2 / p3 cells are re-run or re-used as controls. The p3 numbers are the
  reference point of record for the five shared positions; the claim for this
  round is read from the ledger (coverage, modes, repaired modes), not from a
  fresh control.

## Metrics

1. **Coverage at freeze** — seeds the frozen bundle ran, out of 15. Expect 15/15
   under p4 against 2–10 in p3.
2. **Held-out success (seeds 1–50)** per cell, against the p3 position.
3. **Sweep-failure modes** — how many distinct signature modes the sweep
   exposed, and how many the repair round cleared.
4. **Budget** — charged attempts, early-stop savings, and the split between
   initial / repair / sweep / triage.
5. **Wall clock** — development and held-out hours per cell, to check the
   node-layout projection (dev 2–4 h against p3's 8.4–14 h).

## What this round cannot say

- It still cannot separate the p3 layers from run-to-run variance, or from the
  node layout's change in serving speed. p4 adds one change on top of p3; if the
  score moves, the sweep and the failure-mode triage move together.
- Held-out remains a score, not a diagnostic: no held-out-derived rule may enter
  the contract.

## Decisions taken 2026-10-16 (user)

- Run p4 ("试试呗，你可以"). Story first, ablations later: the two spines are
  (1) the world's state is executable code and (2) world prediction drives
  repair; both halves must get evidence each round.
- The round does not need to be fast; the node layout is simply expected to make
  it faster than p3. No hard wall-clock target.
- The bowl control from p3 is not a constraint and does not gate a change.
