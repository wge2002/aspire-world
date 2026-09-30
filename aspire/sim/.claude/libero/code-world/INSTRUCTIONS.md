# Code-world v0: passive mechanism pilot

Status: new research protocol, prepared for review. This is not the ASPIRE Fix
Loop reproduction protocol. No simulator result is implied by the existence of
this directory or by passing local synthetic tests.

The user has selected LIBERO-Pro Alphabet soup. The selected source task is:

```text
libero_object_swap / pick_up_the_alphabet_soup_and_place_it_in_the_basket
```

The user also requires the implementation to be opt-in through
`--args.world-model-config`. Omitting this argument must preserve the existing
entrypoint's behavior, output conventions, and service usage.

## Read before execution

1. Repository [AGENTS.md](../../../../../AGENTS.md).
2. Simulation [README.md](../../../README.md) and [CLAUDE.md](../../../CLAUDE.md).
3. Suite [CLAUDE.md](../CLAUDE.md) and [API reference](../api-reference.md).
4. This file and [SKILL.md](SKILL.md).
5. The detailed [protocol and interface guide](../../../docs/experiments/code-world-v0.md).
6. The motivating [research note](../../../docs/research/2026-09-08-code-world-sparse-verification.md).

## Scope of P1

P1 records a frozen policy's publicly observable action boundaries, then runs
numeric world-state observers in blind replay. Observers predict future
measurements, choose a limited number of measurements to receive, compare
prediction against evidence, and update their state. They do not change the
recorded policy's actions.

P1 can test prediction, relation contradictions, unavailable measurements, and
the cost of observer queries. It cannot establish closed-loop recovery,
task-success improvements caused by the observer, active viewpoint selection,
or transfer to a second task. Those require a later protocol and confirmation.

## Resolve scope and perform preflight

The user has authorized preparation and the experiment intent. Read-only
inspection, simulator-free implementation, and local synthetic checks do not
need another approval. The new protocol still contains unselected model,
development-seed use, conditions, and budget choices: present a concrete
preflight and obtain those explicit selections before the dependent live work.
Do not infer them from a historical Alphabet-soup campaign. Once the scope is
selected, carry out its necessary stages without repeated confirmation.

Apply any experiment-specific and paper-scale confirmation requirements from
repository AGENTS.md. This document does not add a separate approval for each
model request, numeric replay, or routine engineering action.

Record:

- Repository commit, local changes, isolated checkout, and frozen policy source.
- Host, driver/runtime, currently free GPUs, explicit worker/device ownership,
  and required ports. If using DLC, request only 4, 8, 16, or 24 total GPUs and
  follow `rbs-debug:/mnt/home/gewang/DSW_DLC_WORKFLOW.md` for mapping.
- Existing environments, allowed perception tools, available weights, and
  credential readiness without printing secrets. Generated code must not
  receive provider keys or physical-robot access.
- The exact experimental open-weights model, endpoint, harness, generation and
  revision budgets. `Qwen/Qwen3.8-Flash-Next-FP8` is a proposal, not an implicit
  selection; its historically used port 8121 is not proof of a live endpoint.
- The scope selected from the table below, calibration/diagnostic use, number
  of captures, observer cells, retries, model calls, and fresh output root.
- Estimated runtime and a measured-throughput update after the first capture.
- Known unsupported measurements or missing interfaces. Do not silently
  replace them with simulator state or a different model.

These are proposed P1 choices; they require confirmation before execution:

| Choice | Proposed specification | Status |
| --- | --- | --- |
| Basic common tape | One frozen nominal policy on development seeds 51–65: 15 captures | Recommended first P1 scope; pending confirmation |
| Internal development split | Seeds 51–55 for readout/schedule calibration and program rehearsal, 56–65 for diagnostic replay | Pending confirmation; all 15 remain development seeds |
| Observer budgets | 2 and 4 post-anchor object queries per episode | Pending confirmation |
| Optional extension | Nominal, changed transport rotation, and difficult grasp conditions on the same 15 seeds: 45 total captures | Separate scope choice; not automatic |
| Experimental generator | Qwen3.8-Flash-Next-FP8 through a verified local API | Proposed; endpoint readiness and budgets must be checked |

Held-out seeds **1–50 are excluded from P1**. Do not run their policy captures,
read their trial artifacts for diagnosis, select a world program using their
scores, or relabel a development subset as held-out.

## P1 execution order

1. Freeze the common action policy and its provenance using development-only
   evidence. It is input to this diagnostic study, not a newly reproduced
   ASPIRE baseline. Do not import external baseline programs or outputs.
2. Complete local tests of transforms, prediction-before-update order, query
   budgets, unavailable measurements, and blindness. Synthetic tests establish
   software behavior, not robot or model performance.
3. After confirmation, use the opt-in capture interface documented in the
   [interface guide](../../../docs/experiments/code-world-v0.md). Capture an
   initial observation and the implemented public action boundaries. Save the
   exact config, policy, capture status, and numeric tape in a fresh directory.
4. Calibrate readout tolerances only on the approved calibration prefix. Freeze
   tolerances and the target-identity/reference-point convention before using
   the diagnostic seeds. An unavailable or ambiguous measurement stays
   unavailable; it is not a zero displacement or a failed grasp.
5. Generate the experimental world program through the confirmed open-model
   API with recorded request/response provenance. A hand-authored program is a
   calibration control. A config containing a model name does not prove model
   generation. The current generator makes one request using the frozen policy
   and numeric contract; it does not ingest calibration tapes or automatically
   revise a world program from their errors. Do not describe this as learned
   representation refinement. If generation cannot be verified, report only a
   calibration/software rehearsal and stop short of method claims.
6. Freeze each observer and replay the same tapes in the 2×2 matrix below, at
   both budgets. Do not expose unrequested current/future object measurements,
   images, or probe values to the observer or its scheduler.
7. Score pre-update predictions on common independent probe points. Save query
   decisions, residuals, unknowns, costs, program/config identities, and all
   incomplete or failed records. Do not discard inconvenient episodes.
8. Report the P1 completion checklist and falsification findings. Stop at the
   P1 boundary; no held-out, closed-loop, or transfer launch follows implicitly.

## Core comparison

| Cell | Executable representation | Observer query schedule |
| --- | --- | --- |
| F-fixed | Strong world-frame numeric dynamics | Fixed, frozen positions and budget |
| R-fixed | Generated operation-related state/relations | The same fixed positions and budget |
| F-adaptive | The same strong world-frame dynamics | Adaptive within the same budget |
| R-adaptive | The same generated relation program | Adaptive within the same budget |

F must use measured end-effector motion and sensible dynamics. Merely holding
the last object position constant is not the principal baseline. If F and R
learn mathematically equivalent updates, report that equivalence; changing
coordinate frames alone is not evidence of a representational contribution.

The initial numeric anchor is reported separately and is identical across
cells. It is not a free ongoing stream of object estimates. Failed observer
queries consume budget. The policy's original vision costs are common to all
cells and must be listed separately from the observer's incremental costs.

An adaptive schedule must be executable using only the current admissible
history. Budget 2/4 experiments compare quality at equal caps; also report
actual queries used. A fixed schedule and tolerances are frozen using the
calibration prefix, not optimized on each diagnostic episode.

Current adaptive replay follows the generated program's history/action/state
query proposal. A quantitative next-program decision-utility algorithm is not
implemented. P1 can assess that proposal's empirical value; it cannot claim to
have established the stronger decision-relevant scheduling mechanism.

## P1 completion and stopping

Completion requires every confirmed seed/condition capture, explicit status
for each scheduled replay cell and budget, generator provenance for anything
called generated, common-probe metrics, query accounting, and a limitations
report. Completion does not require winning a baseline.

Stop and report the specific limitation if identity binding or the readout
cannot be made meaningful with allowed observations; if all useful conditions
lack observable relation changes; or if the requested generator or comparison
is not implemented. Do not expand trials to make a positive result appear.

The [detailed protocol](../../../docs/experiments/code-world-v0.md) specifies the
interpretation of occlusion, non-rigid readout drift, negative findings, and the
separate gates for future closed-loop and transfer work.
