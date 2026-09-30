---
name: libero-code-world-v0
description: "Prepare and run the explicitly confirmed, opt-in Alphabet-soup Code-world passive mechanism pilot: frozen-policy capture, blind numeric-observer replay, prediction and query-cost analysis on development seeds only."
---

# LIBERO Code-world v0

Use [INSTRUCTIONS.md](INSTRUCTIONS.md) as the authoritative execution order and
[code-world-v0.md](../../../docs/experiments/code-world-v0.md) for method,
interfaces, metrics, and current implementation limits.

## User requirements

- Use the selected LIBERO-Pro Alphabet-soup task and a separately confirmed
  open-weights model API for experimental code generation and reasoning.
- Keep the existing skill set and action policy fixed for passive P1.
- Enable capture only with `--args.world-model-config`. Without it, preserve
  the original defaults and execution behavior.
- Study executable relation hypotheses, forward predictions, partial evidence,
  and decision-relevant checking. Do not present memory accumulation, code
  alone, sparse sampling alone, or a hand-written coordinate transform as the
  method's demonstrated innovation.

## Boundaries

This is a new research pilot, not the canonical Fix Loop campaign. A selected
task does not select the new model, seed use, trial conditions, or budget.
Resolve those choices in a concrete preflight before dependent live work,
then complete the selected scope without repeated approval. Existing user
authorization covers preparation and local simulator-free engineering checks.
Follow repository AGENTS.md for experiment-specific and paper-scale approval;
this skill adds no separate per-request approval. Do not launch physical-robot
services or access simulator ground truth or asset files.

P1 uses only development seeds 51–65. The proposed calibration/diagnostic split
51–55 / 56–65 is internal development use and must be confirmed. Seeds 1–50
remain outside this pilot. Generated programs and schedules must not see
unrequested tape measurements or independent scoring probes.

## Evidence discipline

1. Save a prediction before releasing the corresponding observation.
2. Compare prediction and evidence before correcting the state.
3. Preserve supported, contradicted, and insufficient-evidence outcomes.
4. Calibrate tolerances and target/reference conventions on the calibration
   prefix; freeze them for diagnostic replay.
5. Use actual measured end-effector state for numerical propagation. A target
   command is not the actual pose.
6. Report query attempts, successful readouts, initial anchors, and base-policy
   perception costs separately. Current capture does not establish camera
   acquisition savings.
7. Keep all failures and unavailable measurements in the report. Synthetic
   tapes, hand-authored controls, and generated programs have distinct labels.

## Comparison and interpretation

Run only implemented, verified interfaces. The target matrix is world-frame
strong dynamics versus generated relation dynamics, crossed with fixed versus
adaptive query schedules at budgets 2/4. A hand-written relation observer is a
calibration control and cannot substitute for a verified open-model program.

An adaptive policy evaluated on a fixed tape supports claims only about
passive checks along those actions. The current scheduler follows generated
history/action/state query proposals; it does not implement a future-program
decision-utility algorithm. Current generation is one policy-conditioned API
request, without calibration-tape ingestion or automatic program revision.
Viewpoint changes, recovery choices,
changed future actions, and task transfer require new live trials and their
own confirmed protocol. Do not infer closed-loop success from tape replay.

Record significant work in the current experiment log without overwriting
existing outputs. Finish with explicit completed counts, missing counts,
artifact paths, findings, and the next gate. Do not modify shared learned
skills from this diagnostic experiment by default.
