# Coordinator prompt: Code-world v0 P1

Read [INSTRUCTIONS.md](INSTRUCTIONS.md) and [SKILL.md](SKILL.md) before acting.
Follow the repository and LIBERO suite constitutions. The user has selected
the LIBERO-Pro Alphabet-soup task and requires opt-in
`--args.world-model-config`; all ordinary entrypoint behavior must remain
unchanged when the option is omitted.

Your job is to execute only the approved P1 scope and maintain an honest
evidence record. P1 captures a frozen policy and evaluates numeric observers
in blind replay. It does not launch a Fix Loop, alter actions using observer
feedback, run held-out seeds, or transfer to another task.

Before dependent live work, resolve any unselected model, seed use, condition,
and budget choices. Local simulator-free engineering checks are already within
the user's authorized preparation scope:

1. Resolve the implementation's actual interfaces and run local synthetic
   checks; do not invent command flags or model-generation support.
2. Report current host/GPU ownership, runtime, model/endpoint status, required
   services, credentials readiness, exact scope/budgets, expected duration,
   frozen policy provenance, and fresh outputs.
3. Verify explicit selection of the unresolved scope. The optional 45-capture
   extension and the proposed model are not implied by task selection. Follow
   repository AGENTS.md for experiment-specific and paper-scale confirmation;
   do not invent an additional approval for each routine request or replay.

Once the preflight is confirmed, continue the approved stages autonomously.
Keep experimental open-model requests and generated programs separate from
infrastructure coordination. Do not put provider secrets in generated-code
processes, prompts, output metadata, or logs.

Use a common capture tape for each seed/condition. Release only the initial
anchor, current measured robot state/action metadata, and observations the
observer requests. Keep independent probe values outside the observer's
context. Predictions must precede observations and corrections. Score all
arms at the same probe points, including predictions made without querying.

Freeze development-calibrated tolerances, programs, schedules, and identity
conventions before diagnostic replay. Numeric updates within an episode are
allowed; rewriting a past prediction or changing a program after seeing that
episode's future is not. Treat ambiguous segmentation and occlusion as
insufficient evidence. Do not substitute hidden simulator state.

Report the core 2×2 comparisons at budgets 2/4 only when all interfaces exist
and generated-program provenance is real. Otherwise report exactly which
calibration or software rehearsal completed. Never label a hand-authored
observer as an open-model result because its config names a model.

P1 is complete when the approved captures and cells have explicit outcomes,
common-probe prediction/contradiction metrics, observer cost accounting, and a
report of failures, unavailable evidence, and interpretation limits. A
negative result is valid. Preserve old artifacts; create a fresh attempt path
for any authorized retry. No unapproved extra seeds or conditions follow.

At the end, state whether the evidence supports the representation, scheduling,
both, neither, or is uninformative. Stop before closed-loop, transfer, or
held-out work and present the concrete next protocol gate.
