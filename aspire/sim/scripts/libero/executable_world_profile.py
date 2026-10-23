"""C-only continuation prompt and recorded, simulator-free candidate checks."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

INTERFACE = "docs/experiments/code-world-c-opt-ablation-20260920/NATIVE_WORLD_INTERFACE.md"
# How this cell's C bundle comes into existence. `repair` is the original
# authorized continuation from one pinned prior-C starter; `fresh` is a first
# generation for a task that has no prior C bundle at all. An absent key means
# `repair`, so every study staged before this key existed renders byte-identically.
C_LINEAGES = ("repair", "fresh")
# The offline check runs the candidate policy against generated simulate() effects
# and, in replay, a recorded tape. It is CPU-bound Python plus array decode, not a
# simulator, but a large tape or a slow shared filesystem can still exceed a tight
# watchdog. The old 90 s ceiling turned that into a permanent candidate rejection.
OFFLINE_TIMEOUT_SECONDS = 600
# An offline report the candidate itself produced. Anything else -- no report, a
# watchdog kill, a nonzero exit with no report -- is the infrastructure's problem.
AUTHORED_STATUSES = frozenset({"complete", "unsupported", "program_error", "unsupported_contract"})
# What the screening actually established about the candidate, as opposed to how
# the offline process terminated. `unknown` is never evidence of success.
CONCLUSIONS = {"complete": "supported_pass", "unsupported": "unknown",
               "program_error": "authored_error",
               # p3-search only: the candidate lacks progress() or PRIMITIVE_CLASS.
               "unsupported_contract": "authored_error"}


def enabled(case):
    return case.get("executable_world_revision") == "r1"


def closed_loop_module():
    """The closed-loop revision module, located rather than imported by package.

    Same reason as the world-use reader: one profile file must work in this
    engineering checkout and in a frozen cell tree, and an unflagged case never
    loads it, so it cannot change an old study's behaviour.
    """
    import importlib.util
    path = Path(__file__).resolve().parents[2] / "cap/world_model/decision_revision.py"
    spec = importlib.util.spec_from_file_location("aspire_decision_revision", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def closed_loop(case):
    """True only for a cell that explicitly opted into online closed-loop decisions."""
    return bool(case.get("closed_loop_revision"))


def gate_module():
    """The opt-in development gate, located like the other revision modules."""
    import importlib.util
    path = Path(__file__).resolve().parents[2] / "cap/world_model/development_gate.py"
    spec = importlib.util.spec_from_file_location("aspire_development_gate", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def development_gate(case):
    return case.get("development_gate") or "oracle"


def sealed(case):
    """True only for a cell whose development outcome is sealed from the solver."""
    return development_gate(case) != "oracle"


def lineage(case):
    """`repair` (the original, default) or `fresh`. Never inferred from the tree."""
    value = case.get("c_lineage", "repair")
    if value not in C_LINEAGES:
        raise ValueError(f"invalid C lineage {value!r}; expected one of {C_LINEAGES}")
    return value


def interface_doc(case):
    """This cell's staged interface document, or the original pinned path."""
    if case.get("prediction_contract") in RUNTIME_CONTRACTS:
        return RUNTIME_INTERFACE
    return case.get("world_interface_doc") or INTERFACE


def validate(case):
    if case.get("executable_world_revision") not in (None, "r1"):
        raise ValueError("unsupported executable-world revision")
    if case.get("foundation_revision") not in (None, "r1"):
        raise ValueError("unsupported foundation revision")
    if enabled(case):
        if case.get("condition") != "C" or case.get("profile") != "judgment":
            raise ValueError("executable world is a C-only opt-in")
        if case.get("c_arm") not in {"full", "no_self_eval", "no_rehearsal"}:
            raise ValueError("invalid C ablation arm")
        # The lineage and its one permitted prior input are declared together, so
        # a fresh cell cannot quietly carry a starter and a repair cell cannot
        # quietly lose the input its prompt names.
        if lineage(case) == "repair":
            if not case.get("c_starter"):
                raise ValueError("a repair-lineage C cell must declare its c_starter input")
        elif case.get("c_starter"):
            raise ValueError("a fresh-lineage C cell must not declare a c_starter input")
    if closed_loop(case):
        # Refused up front, before anything is staged or a retry is spent: an unknown
        # revision, a non-C/non-executable cell, or the no_self_eval ablation
        # (which has no online goal to branch on) is not silently adapted.
        problems = closed_loop_module().revision_errors(case)
        if problems:
            raise ValueError("; ".join(problems))
    if case.get("development_gate"):
        problems = gate_module().revision_errors(case)
        if problems:
            raise ValueError("; ".join(problems))
    if case.get("prediction_contract") not in PREDICTION_CONTRACTS:
        raise ValueError(f"unknown prediction_contract {case.get('prediction_contract')!r}")
    if prediction_contract(case) and not enabled(case):
        raise ValueError(f"prediction_contract {case['prediction_contract']} requires the r1 executable world (condition C)")
    if case.get("development_budget") is not None:
        if case["development_budget"] != "aspire-v1":
            raise ValueError(f"unknown development_budget {case['development_budget']!r}")
        if case.get("prediction_contract") != "p3-search":
            raise ValueError("the aspire-v1 development budget requires prediction_contract p3-search")
    problems = parallel_errors(case)
    if problems:
        raise ValueError("; ".join(problems))
    from method_switches import errors as method_errors
    problems = method_errors(case)
    if problems:
        raise ValueError("; ".join(problems))


#: Absent and "off" are the same contract-off cell.
PREDICTION_CONTRACTS = (None, "off", "p1", "p2-runtime", "p3-search")
#: Contracts that run the p2 runtime template; p3-search adds search control.
RUNTIME_CONTRACTS = ("p2-runtime", "p3-search")


def prediction_contract(case):
    """True only for a cell that explicitly opted into a supported contract."""
    return case.get("prediction_contract") in ("p1", *RUNTIME_CONTRACTS)


#: The authorized continuation from one pinned prior-C bundle. Byte-exact as it
#: was before `c_lineage` existed; the `repair` default keeps it the only text a
#: previously staged study can render.
REPAIR_LINEAGE = """This is an explicitly authorized C CONTINUATION/REPAIR, not a from-scratch
reproduction. The sole permitted prior input is `{starter}`: the same
task's previous C policy/world and declared DEVELOPMENT-only evidence. Read that
input, repair its data-flow/semantics flaws, and optimize the policy/world jointly.
This permission overrides the template's ban on previous outputs only for that
one frozen input. Other conditions, other tasks and seeds1–50 remain unavailable.
Do not copy A solutions, inspect historical held-out data, or modify framework."""

#: A first generation for a task with no prior C bundle. It grants nothing: the
#: template's ban on previous outputs stays whole, because there is no frozen
#: input to except from it.
FRESH_LINEAGE = """This is a FRESH first generation for this task, not a continuation. There is no
prior C policy, world program or development evidence for it, and none is
supplied: the template's ban on previous outputs applies in full, with no
exception. Author both programs from the public initial scene, the factual API
reference and the development evidence you record in this cell. Other conditions,
other tasks, other studies' bundles and seeds 1–50 remain unavailable. Do not
copy A solutions, inspect historical held-out data, or modify framework."""


#: The heading names what the cell actually does. `repair` keeps the original
#: wording exactly; a fresh cell must not be told it is repairing something.
HEADINGS = {"repair": "Executable C repair and ablation",
            "fresh": "Executable C fresh generation and ablation"}


def lineage_section(case):
    if lineage(case) == "fresh":
        return FRESH_LINEAGE
    return REPAIR_LINEAGE.format(starter=case["c_starter"])


#: Replaces the observational foundation instruction only in a closed-loop cell.
#: The legacy instruction told foundation cells to keep done() observational;
#: that remains the text every unflagged cell renders.
CLOSED_LOOP_GOAL = ('Define explicit goal clauses with current public evidence, using the foundation state contract. '
                    'This cell opts into the closed-loop revision: world judgments now drive online '
                    'decisions, as specified in the closed-loop contract below. Read the per-trial '
                    'development calibration feedback and correct false positives/negatives or unknown causes.')

#: The closed-loop decision contract, inserted only when the cell opted in.
CLOSED_LOOP_CONTRACT = """
### Closed-loop revision r1: online decisions from grounded world state

For this explicitly opted-in revision, this section supersedes any instruction
in the pinned base interface that keeps done() observational or defers a
goal-driven recovery/termination controller. Its evidence and public-API
requirements still apply. Unflagged studies retain the base interface unchanged.

At each meaningful manipulation milestone (after acquiring the target, after a
grasp, after a transport, before declaring the task finished) the policy calls
`update` with a fresh real observation, then `query`/`done`, and acts on the
answer. `done()` returns `verdict` and `branch`:
  - `true` with fresh admissible observed evidence may choose `stop`.
  - `unknown` means the state is not established: choose `observe` (reobserve,
    relocalize, search). Unknown is never success and never a stop.
  - grounded `false` or measured lack of progress may choose `recover`, and the
    generated policy then performs its own recovery actions; or `continue`.
The framework refuses a branch the evidence cannot support (for example a stop on
stale, missing or predicted evidence) and records it as `observe`; the policy must
read the returned branch rather than the one it requested.

Three kinds of statement are kept apart. `observed` facts come from real public
observations in this episode. `predicted` facts (the world's modelled effects)
may guide where to look or move next, and may never establish a grasp target,
measured progress or goal completion. `unknown` is the absence of admissible
current evidence. Use `state.observed_only(name)` for any decision that needs an
observed value; a missing detection must invalidate the observed fact, not keep or
relabel the prediction.

Preserve target identity from public scene relations. Use
`state.confirm_identity(name, candidates, match=..., assumed=..., evidence_ids=...)`
(or an equivalent authored rule) so that zero or several matching candidates, a
changed association, or an unverified orientation yields unknown and a
reobservation. Once a target has been acquired, pass the identity you are still
tracking as `assumed` on every later call, so a match against a *different*
object invalidates the fact instead of silently re-pointing at it:

    fact = state.confirm_identity('target', candidates, assumed=original_identity,
                                  match=lambda c: c['label'] == target_label,
                                  evidence_ids=[obs_id])
    if fact['status'] != 'known':      # missing, ambiguous, or a changed identity
        fact = state.invalidate('target', 'perception gave no usable target',
                                identity=original_identity)
        return fact                    # reobserve; never fall back to a prediction

`assumed=None` is first acquisition and establishes the identity; afterwards the
identity you keep must be the one already established. A missing detection must
go through `invalidate` (or an equivalent), never leave the previous value or a
prediction standing in for the measurement. Do not choose a relative target by
position in an incomplete detection set. Do not hardcode scene coordinates,
task-specific grasp recipes, simulator assets or fixed universal geometric
thresholds; estimate parameters from development evidence.

There is no cap on actions, recoveries, queries or revisions beyond the existing
three-retry development budget per seed and watchdogs. The offline check
also reports structural findings (a world result discarded unused, a policy that
never consults the judgment, predicted facts present in the world). They are
screening notes, not proof of runtime behaviour, and do not by themselves block a
candidate. The framework's final shadow self-evaluation after the program ends is
recorded separately from the online decisions your policy made.
"""

#: Fixed anchor for the closed-loop insertion; legacy text is unchanged.
CLOSED_LOOP_ANCHOR = "Everything else below retains the original native failure-by-failure Fix Loop,"

#: The prediction contract, inserted at the same anchor only when the cell opted in.
PREDICTION_CONTRACT = """
### Prediction contract p1: predictions checked against the next real observation

In this cell `predict(call)` has a fixed return schema. Before every public API
call the framework calls it with that call and expects either

    {"facts": {name: value, ...}, "tolerance": {name: number, ...}}

or `raise Unsupported(reason)`. `facts` maps a fact name in your world state to
the value you expect it to have after this call; each value must be finite JSON
(number, list of numbers, bool, str or None). `tolerance` is optional and gives
the largest acceptable absolute error per numeric fact (default 1e-6). Any other
return (not a dict, no `facts`, a NaN or infinite value, a negative tolerance)
is recorded as `malformed`; it is counted, never fatal. `Unsupported` is recorded
as `unsupported`.

The framework writes your predicted facts into the state's predicted layer,
tagged with the call id. A prediction never overwrites an observed fact. It
targets the NEXT REAL OBSERVATION after this call: a prediction from call i is
checked when the observed layer next receives a value for the same fact name
whose evidence id is greater than i, whether `observe` or `update` wrote it.
Numbers and numeric lists compare within tolerance (the largest elementwise
error for lists); bool, str and None must be equal. An observed fact that is
invalid or None gives `unknown`; a prediction no observation reached before the
episode ends is `unresolved`. Each check is recorded as a `prediction_check`
event with the predicted and observed values, the residual and the status.

Two query names are reserved and answered by the framework, not by your
`query()`:
  - `world.query("prediction_checks", since_call=None, fact=None)` returns the
    list of checks so far, optionally only from calls >= `since_call` or for one
    fact name;
  - `world.query("prediction_summary")` returns counts `committed`, `supported`,
    `malformed`, `match`, `mismatch`, `unknown`, `unresolved` (plus
    `unsupported`) and `latest_mismatch`, the most recent mismatch check or None.

A mismatch is information, not a verdict: it says the world's model of that
call disagreed with what was then measured. Your policy may use it within its
existing control flow, for example to re-measure, retry the same skill, or take
its own error-handling path. The framework does not route recovery, does not
score predictions, and does not gate trials on them. Fabricated predictions are
pointless: nothing grades prediction accuracy, and the cell is judged only by
held-out task success.
"""

RUNTIME_INTERFACE = "cap/world_model/RUNTIME_INTERFACE.md"

RUNTIME_CONTRACT = """
### Runtime world p2: observed template and prediction-driven policy decisions

Read `cap/world_model/RUNTIME_INTERFACE.md` in full. This
opt-in section replaces the p1 fact-name contract. Declare
`STATE_TEMPLATE_REVISION = "p2-runtime"` in your world module. `WorldState()`
is one authoritative observed store. A public perception adapter in observe()
or update() fills it; the policy reads facts through `world.query("state",
entity=..., fact=..., frame=..., unit=...)`. Raw public arrays can be processed
by the adapter; do not keep a second authoritative scene state in the policy.
The framework provides no inventory, task geometry, predicate or dynamics.

Register stable entity identities using `state.entity(id, label=..., evidence_ids=...,
source={"api": ..., "method": ...}, uncertainty={"status": "estimated"})`.
Write facts with `state.set_fact(entity, fact, value, frame=..., unit=...,
evidence_ids=..., source=..., uncertainty=..., kind="fact"|"geometry"|"relation",
related_entities=[...])`. Association, missing detections, predicates, dynamics
and tolerances are your responsibility. Unknown measurements invalidate the
observed slot. Coordinates require a documented frame and unit. Predictions
never become observed values, grasp evidence, progress evidence or success.

Before each public call your `predict(call)` either raises Unsupported for
unmodelled semantics (including math helpers), or returns a nonempty list:

    {"predictions": [{"entity": entity_id, "fact": fact_name,
                      "frame": frame_name, "unit": unit_name,
                      "kind": "geometry", "expected": modelled_value,
                      "tolerance": authored_bound,
                      "target": {"kind": "next_observation"},
                      "valid_for_calls": authored_horizon}]}

The horizon counts public API call IDs from the source call; choose it to cover
your perception pipeline. `same_call` targets the actual get_observation return
of the source call. `checkpoint` with a name targets the fresh observation at
`world.checkpoint(name)`. By default ANY intervening motor command supersedes
the old pending prediction even when that command's predict is Unsupported.
Only an explicitly modelled multi-action checkpoint may declare
`intervening_motion: "retain"`; it still expires at its declared horizon.
Facts match entity, frame, unit, kind, relation identities and exact target
observation; missing or inadmissible measurements produce unknown, never a
manufactured mismatch. Finish derived perception/update before asking feedback.

At a meaningful decision checkpoint, use the runtime world as an interface:
read the relevant observed state and `world.query("prediction_feedback",
entity=..., fact=..., source_call=...)` (filters optional). The result contains
only the latest source window per entity/fact with match/mismatch/unknown,
pending, superseded or expired. Write your OWN branch which consumes these
answers to continue, reobserve or revise the next action. A mismatch can mean
bad dynamics, association or action execution; diagnose it with public evidence.
Unknown is not success. Neither a final print nor an unused query establishes
use. Do not query mechanically before every motor call, chase query counts,
or add a fixed recovery sequence to satisfy this interface.

After selecting the branch, call `world.record_decision(branch, feedback=feedback,
facts=[fact_answer], reason=...)` with the exact current query answers, then
execute the public call your branch selected. This records dataflow and the next
actual call; it does not execute, approve, substitute or route any action.
Declarations and static dependencies are not causal proof. The runtime reports
no_query/logging_only/inconclusive/action_evidence beside task outcomes. Read
the per-trial `runtime_mechanism` feedback and repair missing state/prediction/
decision dataflow, rather than printing a history summary at episode end.

The older instruction that keeps done() observational concerns the GOAL SELF
EVALUATOR. It does not prohibit p2 prediction residuals and grounded low-level
state from driving ordinary policy decisions. This profile does not by itself
enable goal-driven termination/recovery from done(). Keep the original initial
generation, dev seeds 51–65 and three total attempts per seed, tested-bundle
selection, frozen heldout 1–50, native tools, model and budgets unchanged.
"""

#: Replaces the foundation goal instruction in a sealed cell: there is no
#: evaluator label, so there is no calibration feedback to read.
SEALED_GOAL = {
    "self_eval": ('Define explicit goal clauses with current public evidence, using the foundation state '
                  'contract. In this cell your world\'s FINAL done() verdict IS the development grade: '
                  'there is no evaluator label and no calibration feedback. A `true` verdict passes the '
                  'seed; `false` and `unknown` do not. Keep self-evaluation observational: do not add a '
                  'new recovery or termination controller based on done().'),
    "vlm_judge": ('Define explicit goal clauses with current public evidence, using the foundation state '
                  'contract. In this cell an independent judge model reading three rollout frames grades '
                  'each trial; your done() is recorded in shadow but is not the grade and receives no '
                  'calibration feedback. Keep self-evaluation observational: do not add a new recovery '
                  'or termination controller based on done().'),
}

SEALED_GATE_DETAIL = {
    "self_eval": """Gate = your world's FINAL shadow `done()` after the program ends: the framework's own
call, recorded with origin `framework_final` in `judgment_world/manifest.json`.
`true` passes the seed; `false` and `unknown` do not. `unknown` is not failure
evidence either: it means your predicate could not decide, and the seed stays
pending repair until a trial's done() decides. Make done() decide from fresh
public observations of the task's actual end state.""",
    "vlm_judge": """Gate = an independent judge (the same served model, in a separate request) shown
the initial, midway and final agentview keyframes of the trial together with the
task instruction, answering `success`, `failure` or `unsure`. `success` passes the
seed; `failure` and `unsure` do not. The judge sees no world state, trace, code or
label; its one-sentence reason is recorded in `gate.reason`. Your done() still runs
in shadow for the record and never changes the grade.""",
}

SEALED_GATE_CONTRACT = """
### Development gate: {gate}

The simulator's task label is sealed in this cell. During development no file,
log, directory name or protocol output names the reward or `task_completed`:
replay directories are `trial_<seed>_sandboxrc_<rc>_sealed`, the video is
`video.mp4`, and `summary.txt` reports stdout/stderr and the sandbox exit code
only. Each recorded trial result instead carries `gate`:
`{{"source": "{gate}", "verdict": ..., "passed": true|false, "reason": ...}}`.
`status` and `check` count a seed as passed only when its gate passed; triage,
repair and selection work from that verdict.

{detail}

The frozen held-out evaluation on seeds 1–50 is graded by the environment, not
by this gate. A trial that satisfies the gate without achieving the task in the
simulator passes development and fails the evaluation, and the outer analysis
reports exactly that disagreement. Do not try to recover the sealed label:
generated programs may read `env.handle.task_language` and nothing else from
`env`; `APIS`, `__self__`, `task_completed`, `compute_reward`, `check_success`
and other `handle` attributes are refused at admission, and the diagnostic REPL
offers no `step()` helper.
"""

#: Appended to the runtime section only in a p3-search cell; p2-runtime renders unchanged.
SEARCH_CONTRACT = """
### Search control p3: hypothesis-testing development

This cell adds search control to the runtime world above. Every charged trial
should test a hypothesis your ledger has not already refuted. A candidate
missing any declaration below is refused before any attempt is spent (offline
status `unsupported_contract`; nothing is charged):
  - the world module defines `progress()`, returning a float in [0, 1]: its own
    estimate of how far the TARGET object of the task has moved toward the goal,
    from OBSERVED facts only (a sibling of done(); never from commands or
    predictions). It is identity-aware: only the object the instruction names
    counts. Moving a different object (for example the wrong drawer or a
    neighbouring knob), or a jittering pose estimate of a target that did not
    move, is NOT progress. It returns None when the target has not been
    observed since the last manipulation. It is recorded at the end of every
    trial;
  - the policy declares, at module level, `PRIMITIVE_CLASS = "<contact strategy>"`,
    a short stable name for HOW the gripper makes the object move (for example
    "top_grasp_pull", "hook_behind_handle_drag", "side_push_sweep",
    "pinch_rotate_lever"). The class is the contact strategy. Changing class
    means changing the contact geometry or the grasp mode (where and how the
    gripper touches the object, and in which direction it applies force).
    Re-tuning offsets, heights, angles, speeds or step counts keeps the class;
    renaming the class without changing the strategy also keeps it, and bundles
    are audited for that (the report lists every bundle digest per class and the
    policies that differ only in their PRIMITIVE_CLASS line);
  - every trial commits at least one gripper-level AND at least one
    object-level prediction, judged on the recorded rehearsal: gripper-level
    entities are measured only from get_observation's robot state; object-level
    entities are measured from perception. Do this for every manipulation
    primitive (approach, contact, move): predict where the gripper will be AND
    what the object will do. The gripper row says whether the arm arrived; the
    object row says whether the object responded. Without both, "arm did not
    arrive" (tune parameters) cannot be told apart from "arm arrived, object did
    not respond" (change class).

Re-observe the target. After every manipulation primitive, and once more
before the trial ends, perceive the TARGET object again (move the gripper out
of the way first if it occludes the target), so that its object-level
predictions are judged (match/mismatch) and progress() reflects the state you
observed. If the target has not been observed since the last manipulation,
progress() returns None. Reason: a trial that re-checks only the gripper shows
"the arm arrived" with no evidence about the object, which the saturation rule
would otherwise read as "the arm arrived and the object did not move". A trial
whose progress() is None, or that judged no object-level prediction, is
recorded as unobserved: it still spends its attempt, but it is not evidence for
saturation, and its `search_control_next` says so ("target not re-observed").

Say WHY progress() returned None. A bare None is ambiguous between "this trial
did not look" and "the world cannot resolve the fact at all", and those need
different repairs. Declare a module-level `progress_reason()` alongside
`progress()` returning one of two strings:
  - "unobserved"  — the target was simply not re-perceived since the last
    manipulation. The channel is fine; look again.
  - "unavailable" — the world CANNOT resolve the fact: the association is
    lost, the target is no longer identified, or the entity it was bound to is
    gone. The measurement channel itself is broken. Do not keep spending
    attempts on a world in this state: repair the channel first.
Without `progress_reason()` a None is recorded as "unobserved", which is what a
world that predates this rule means. The report counts observed, unobserved and
unavailable attempts separately, and a class whose attempts are all
`unavailable` is named in `unavailable_classes` — that is a broken channel to
repair, not "no evidence" to accumulate more of.
RECOVERY: `unavailable` must be recoverable, not permanent silence. Re-observe
the target with the ordinary perception API and re-bind the association to the
entity id your world already uses (the same public association rule), then
`progress()` returns a value again. A world that can never leave `unavailable`
is a broken world, not a correct one: the identity that the task names must be
re-established from fresh observation whenever it is lost.

Initial batch: a trial's signature is its prediction-status counts per
entity/fact, rounded object-level residuals and its outcome. After 3 identical
failing signatures the remaining initial seeds are deferred (`deferred_initial`,
no attempt spent) and their attempts are banked for repair: repeating an
experiment whose result is already known is not information. (The deferred
seeds are covered later by the sweep, which is counted apart from these retries.)

Coverage before freezing (p4). A candidate is only EVIDENCE once it has run on
the WHOLE development set 51–65. Runs that passed under a DIFFERENT bundle do
not count for the bytes you freeze: the protocol computes coverage from the
selected bundle's own executed trials, and refuses `select` while any
development seed is missing. Missing seeds are swept with the candidate's exact
bytes (same policy/world digests) using `trial --phase sweep --seed N` with the
same --code/--world-program files. A sweep run is COUNTED SEPARATELY: it spends
none of the seed's three repair retries, so spending every repair retry never
makes the gate impossible. Its own limits: only an already tested candidate can
be swept (new bytes are tested by a charged repair) and each candidate runs each
seed at most once. There is no limit on how many candidates you sweep; only the
development deadline bounds it. Sweep only the candidate you intend to freeze —
a partial sweep of anything else spends wall clock for nothing — and never
select a candidate whose coverage is incomplete: the gate refuses it. If no
candidate can ever satisfy the gate, or `check` still has errors that no
admissible action can change, `check` reports `decision_type: terminal` with a
structured `terminal_reasons` entry and the cell stops as BLOCKED — the gate is
never lowered and never silently skipped.
When NO bundle succeeded, the fewer-crashes tie-break compares only candidates
that can still be frozen. Once your selection covers all of 51–65, a candidate
that ran fewer seeds and so shows fewer crashes is not a better record, only a
partial one; that finding is reported under `advisories` and does not block
`ready`. If a FULLY covered candidate has fewer crashes, select it.
Held-out stays sealed throughout; the sweep only spends seeds 51–65. `check`,
`select` and the finalized result all carry `coverage_gate` with
`required_seeds`, `covered_seeds`, `missing`, `sweep_runs_needed`, `sweepable`
and `unsweepable`, all read from the ledger at that moment; `check` also lists
`legal_actions` and `advisories`.
Selection reasons are audited: any "N of M" pass count you write is recomputed
from the ledger, and a claim the ledger cannot evidence is recorded as a warning
and replaced by the ledger's own figures in the report.

Triage the sweep by mode, then repair once (p4). After the sweep, its FAILURES
are grouped by the same trial signature (identical signature digest = one mode)
and written to `coverage_triage.json` in the task directory: `mode -> seeds ->
the deciding signature fields (object-level residuals / world facts / outcome)
-> what the failures in that group did`. Read it before writing the next
bundle. Repair the MODE, not one seed: a mode is one defect, and a group of six
seeds with the same signature is six chances to fix one thing, not six
independent draws. Seeds that failed the same way are ONE problem; a mode of
size one is still a mode. One repair round is allowed, and the repaired bundle
must itself sweep the whole development set before it can be frozen; a second
triage after it is recorded the same way.

Repair: before every charged repair the protocol writes
`search_control_report.json` in the task directory, and every trial result
carries `search_control` (the report as it stood when that trial was admitted)
and `search_control_next` (the report after it, which governs your next
repair); `status` prints the same. Read it before writing the next bundle.
Saturation: a class is saturated after >= 5 bundles and >= 8 observed graded
attempts of that class leave its best observed progress() below 0.3, with no
pass, while its judged gripper-level predictions match >= 80% (the arm went
where it was told; the object did not respond). A repair whose PRIMITIVE_CLASS is saturated is refused
without spending an attempt: change the contact strategy. To deliberately
re-run a saturated class (for example to confirm a borderline result on a new
seed), pass `--replicate "<reason>"` to `trial`; the reason is recorded with the
attempt and counts against the same budget.

"Physically infeasible" is not an accepted BLOCKED root cause in this cell
while any contact strategy remains untried: benchmark tasks are feasible by
construction. A BLOCKED note names the classes tried, their best progress, and
the next untried class you would test.
"""


#: CC_TASK_6 (p5): the ASPIRE v1.0 development budget, appended after the search
#: contract only when the case sets `development_budget: "aspire-v1"`. Every
#: other render is unchanged, and so is `SEARCH_CONTRACT`'s own text and digest.
BUDGET_REVISION = "aspire-v1"
BUDGET_CONTRACT = """
### Development budget aspire-v1 (the ASPIRE v1.0 budget)

This cell's development budget replaces the per-seed retry count. The ledger
enforces it; `status` and `check` report it under `development_budget`
(`phase`, `versions_used`, `versions_left`, `mode_versions`, `blocked_modes`,
`freeze_conditions`, `next`).

Phase A — until development seed 51 actually passes. Write the initial program
and run it on seed 51 (`--phase smoke` first if you like, then `--phase
initial --seed 51`); then iterate on seed 51 with `--phase repair --seed 51`
and `--phase diagnostic --seed 51`. Attempts on seed 51 are NOT capped: keep
going while your diagnostics make progress. Every other seed, every sweep,
`select` and the freeze are refused until seed 51 passes. Phase A never ends
early: before the first pass the cell stays pending, and only the development
wall-clock deadline stops it.

Phase B — after seed 51 has passed. The bundle that first passed it is v0. Run
v0 on the other 14 development seeds with `--phase sweep` (no cost). Then
triage its failures into failure modes (`coverage_triage.json` groups them by
signature) and write at most 8 VERSIONS in total. A version is new policy/world
bytes; every version runs on ALL 15 development seeds: its first run is

  trial --phase repair --seed <N> --mode <mode> --mode-seeds <seeds> ...

where <mode> is a short stable name of the failure mode it targets and <seeds>
are that mode's failing seeds (comma separated, each must have failed under a
fully swept version, <N> is one of them); the remaining seeds are `--phase
sweep` runs of the same bytes, which spend nothing. The next version is refused
until the current one has run every seed. Per-seed retries no longer apply.
At most 3 versions may target the same mode. A mode still unfixed after 3
versions is BLOCKED: write `attempts/mode_<mode>_BLOCKED.md` with `## Root
Cause:`, `## Details:` and `## What Was Tried:`, and spend no more versions on
it — move to the next mode. A renamed mode is the same mode.

Probes do not count. `--phase probe --seed <N> --code <file>` runs an
observation-only REPL session on any development seed: perception and
computation only (get_observation, segmentation, point clouds, grasp planning,
solve_ik, geometry helpers), no motor command, no step(), `env` limited to
env.handle.task_language. It is not a version, grades nothing and spends
nothing. A probe that names a motor command is refused before it runs; one
that moves the robot anyway is recorded as a protocol violation.

Freeze only when one of these holds for the version you select: it passes all
15 development seeds; all 8 versions are used; or every development seed it
still fails belongs to a blocked mode. Select, among the versions that ran all
15 seeds, the one with the most passes, then fewer crashes (program_ok=false),
then the simpler one. Remaining failures are reported honestly; 100% success
is not required.
"""
#: The p3 search-contract sentences the budget supersedes, each replaced in the
#: budget render only (anchor -> replacement; every anchor must occur once).
BUDGET_SEARCH_EDITS = (
    ("""After 3 identical
failing signatures the remaining initial seeds are deferred (`deferred_initial`,
no attempt spent) and their attempts are banked for repair: repeating an
experiment whose result is already known is not information. (The deferred
seeds are covered later by the sweep, which is counted apart from these retries.)
""", """Under the development
budget below the initial program runs only on seed 51 (phase A), so the
initial early stop never applies; the other seeds are covered by sweeping the
bundle that first passes seed 51.
"""),
    ("""A sweep run is COUNTED SEPARATELY: it spends
none of the seed's three repair retries, so spending every repair retry never
makes the gate impossible.""", """A sweep run is COUNTED SEPARATELY: it spends
no version and no attempt, so the gate is always payable."""),
    ("""(new bytes are tested by a charged repair)""", """(new bytes are a version: `--phase repair --mode`)"""),
    ("""When NO bundle succeeded, the fewer-crashes tie-break compares only candidates
that can still be frozen. Once your selection covers all of 51–65, a candidate
that ran fewer seeds and so shows fewer crashes is not a better record, only a
partial one; that finding is reported under `advisories` and does not block
`ready`. If a FULLY covered candidate has fewer crashes, select it.
""", """Selection follows the development budget below: among the versions that ran
all of 51–65, the most passes, then fewer crashes, then the simpler one.
"""),
    ("""Sweep only the candidate you intend to freeze —
a partial sweep of anything else spends wall clock for nothing — and never
select a candidate whose coverage is incomplete: the gate refuses it.""", """Under the development budget below every version
is swept in full before the next one is admitted, and you never select a
candidate whose coverage is incomplete: the gate refuses it."""),
    ("""Triage the sweep by mode, then repair once (p4).""", """Triage the sweep by mode, then repair the mode (p4)."""),
    ("""One repair round is allowed, and the repaired bundle
must itself sweep the whole development set before it can be frozen; a second
triage after it is recorded the same way.""", """Each repair is a VERSION that declares the mode
it targets (development budget below); every version must itself sweep the
whole development set, and its triage is recorded the same way."""),
)
#: The p2 runtime-contract sentence the budget supersedes.
BUDGET_RUNTIME_EDIT = ("""Keep the original initial
generation, dev seeds 51–65 and three total attempts per seed, tested-bundle
selection, frozen heldout 1–50, native tools, model and budgets unchanged.""", """Keep the original initial
generation, dev seeds 51–65 under the aspire-v1 development budget below,
tested-bundle selection, frozen heldout 1–50, native tools and model unchanged.""")


def budgeted(case):
    return case.get("development_budget") == BUDGET_REVISION


def edited(text, edits):
    for old, new in edits:
        if text.count(old) != 1:
            raise ValueError(f"budget edit anchor changed: {old[:60]!r}")
        text = text.replace(old, new)
    return text


def budget_search_contract():
    """SEARCH_CONTRACT as an aspire-v1 cell reads it, with the budget appended."""
    return edited(SEARCH_CONTRACT, BUDGET_SEARCH_EDITS) + BUDGET_CONTRACT


#: CC_TASK_7 (p6): replays one cell may run at once and the CPU threads each may
#: use. `heldout_parallel` is the outer held-out sweep's (the solver never sees
#: it); `sweep_parallel` offers the batch sweep and appends `BATCH_SWEEP_CONTRACT`
#: to the search contract. Absent keys: serial, every render and run unchanged.
MAX_PARALLEL = 8
DEFAULT_TRIAL_THREADS = 2
MAX_TRIAL_THREADS = 32
PARALLEL_KEYS = ("heldout_parallel", "sweep_parallel")


def parallel_errors(case):
    problems = []
    for key in PARALLEL_KEYS:
        value = case.get(key, 1)
        if type(value) is not int or not 1 <= value <= MAX_PARALLEL:
            problems.append(f"{key} must be an integer 1..{MAX_PARALLEL}, not {value!r}")
    threads = case.get("trial_threads", DEFAULT_TRIAL_THREADS)
    if type(threads) is not int or not 1 <= threads <= MAX_TRIAL_THREADS:
        problems.append(f"trial_threads must be an integer 1..{MAX_TRIAL_THREADS}, not {threads!r}")
    if "sweep_parallel" in case and case.get("prediction_contract") != "p3-search":
        problems.append("sweep_parallel batches the coverage sweep, which exists only under p3-search")
    return problems


BATCH_SWEEP_CONTRACT = """
### Batch sweep

Sweep a candidate's missing development seeds with ONE command instead of one
`--phase sweep --seed N` call per seed:

  trial --phase sweep --seeds all --code <policy> --world-program <world>

(`--seeds 53,57` names seeds instead of `all`). It runs exactly the sweep trials
the single-seed calls would run — same admission, same rows, same limits,
nothing spent — up to {parallel} seeds at once, and prints every seed's record
plus the candidate's coverage. It starts no further seed after an
infrastructure error. Wait for it like any trial (it may take
{slots} trial lengths); never wrap it in `timeout`.
"""


#: CC_TASK_14 (p6d) method switches (`scripts/libero/method_switches.py`). Each piece is
#: rendered only when its switch is set; a case without `method_switches` renders the
#: p6b text byte for byte. Task-independent: no task, object or answer is named.
SUBGOAL_SKILL = "cap/world_model/skills/subgoal-verification.md"
API_M4_SKILL = "cap/world_model/skills/api-revision-m4.md"
#: The foundation goal sentence the verification contract replaces (anchor -> replacement).
SUBGOAL_GOAL_EDIT = ("Keep self-evaluation observational in this study: do not add a new recovery or "
                     "termination controller based on done().",
                     "Verify every subgoal: after each subgoal the policy confirms it from fresh "
                     "observations before going on, retries with a different approach when it is "
                     "not confirmed, and stops issuing actions as soon as the whole instruction is "
                     "confirmed (subgoal verification contract below).")
SUBGOAL_CONTRACT = """
### Subgoal verification contract

Read `{skill}` before writing the initial program. Every program in this cell:
  - splits the instruction into its subgoals (for example: object grasped and
    lifted, object moved to a place, object released in or on a place, a part
    opened or closed, a device switched on or off) and, after the action for
    each subgoal, CONFIRMS it from fresh observations before starting the next
    one. A motion or gripper command that returned without an error is not a
    confirmation;
  - when a subgoal is not confirmed, retries it with a DIFFERENT approach
    (re-perceive, a different contact point, grasp, push direction or
    approach), within a bounded number of retries, instead of continuing with
    the rest of the task on an unconfirmed state;
  - stops issuing actions as soon as every subgoal of the instruction is
    confirmed: return from the program. Extra motion can undo the result, and
    the episode has a fixed step budget after which every further action fails.
Use generic observable evidence: before/after image comparison of the region
you acted on, a change of colour, brightness or appearance there, displacement
or height change of the object from segmentation plus depth, depth change at a
place, gripper opening after closing (fully closed = nothing between the
fingers), and the wrist camera view. Say in your findings which observation
confirms each subgoal and what the program does when it does not hold.
"""
CALIBRATION_CONTRACT = """
### World goal-judgment calibration

After every development trial, the `trial` result carries
`judgment_calibration`: this trial's world goal verdict against the simulator
outcome, and for the world program it ran, the development seeds where the
world judged the task done while the simulator says it failed (`false_accept`)
or the reverse (`false_reject`). `status` and `check` print the same table for
every world program. A false accept means your goal definition accepts a
state that is not the goal: look at that trial's keyframes and the clauses of
its final self-evaluation, and tighten the definition from what the
observations showed. Only development seeds are ever reported.
"""
SCREENING_RECORD_ONLY = """
### Screening and predictions are recorded, not gates

In this cell the offline screening refuses a candidate only for an authored
Python error (or a missing `progress()` / `PRIMITIVE_CLASS` declaration).
Predictions, the prediction-level counts of the rehearsal, prediction matches
or mismatches and a saturated PRIMITIVE_CLASS are recorded with the trial
(`screening`, `search_control.would_refuse`) for your diagnosis; none of them
refuses, blocks or charges a trial.
"""
SCREENING_RECORD_ONLY_EDITS = (
    ("""  - every trial commits at least one gripper-level AND at least one
    object-level prediction, judged on the recorded rehearsal:""",
     """  - (recorded, not refused in this cell) every trial should commit at least
    one gripper-level AND at least one object-level prediction, judged on the
    recorded rehearsal:"""),
    ("""A repair whose PRIMITIVE_CLASS is saturated is refused
without spending an attempt: change the contact strategy.""",
     """In this cell a saturated PRIMITIVE_CLASS is reported
(`search_control.would_refuse`), not refused: treat it as strong evidence to
change the contact strategy."""),
)
SWEEP_ONLY_EDITS = (
    ("""where <mode> is a short stable name of the failure mode it targets and <seeds>
are that mode's failing seeds (comma separated, each must have failed under a
fully swept version, <N> is one of them); the remaining seeds are `--phase
sweep` runs of the same bytes, which spend nothing.""",
     """where <mode> is a short stable name of the failure mode it targets and <seeds>
are that mode's failing seeds (comma separated, each must have failed under a
fully swept version, <N> is one of them); then ALL 15 development seeds, <N>
included, are `--phase sweep` runs of the same bytes, which spend nothing. A
version's coverage and score come from its sweep runs only: the repair run that
made <N> pass is your evidence, not part of the score (v0 likewise is swept on
seed 51 too)."""),
)
RESWEEP_CONTRACT = """
### Selection at the freeze

When development ends (you reach a freeze condition, or the deadline), the
outer coordinator runs every complete candidate within {margin} passes of the
best one (at most {count}) once more on all 15 development seeds and freezes the
one with the most passes over both runs, then fewer crashes. This costs you no
version and needs no action from you; select by the rule above as usual.
"""
API_M4_CONTRACT = """
### Policy API revision m4-r1

Read `{skill}`. `solve_ik` prints a `[solve_ik]` line whenever it clipped your
target to the workspace or used another orientation; `goto_pose(...,
max_step=0.05)` performs a segmented move; `plan_grasp` and
`subsample_point_cloud` return the same result for the same input.
"""

SUCCESS_RULE_CONTRACT = """
### Success rule libero_latch

A trial (development and held-out alike) counts as a success if the task was
achieved at ANY simulator step within the step budget, the standard LIBERO
rule, even if a later motion undid it. When the budget is used up every
further action raises `executing action in terminated episode`; that ends the
trial and is not a program error: it is scored by whether success was reached.
"""


def method_edits(case):
    """Anchored replacements of the search/budget contract text the switches change."""
    from method_switches import enabled
    edits = ()
    if enabled(case, "screening_gate") == "record_only":
        edits += SCREENING_RECORD_ONLY_EDITS
    if enabled(case, "version_score") == "sweep_only":
        edits += SWEEP_ONLY_EDITS
    return edits


def method_contract(case):
    """The sections the switches add, after the batch-sweep contract."""
    from method_switches import enabled
    text = ""
    if enabled(case, "subgoal_verification"):
        text += SUBGOAL_CONTRACT.format(skill=SUBGOAL_SKILL)
    if enabled(case, "judgment_calibration"):
        text += CALIBRATION_CONTRACT
    if enabled(case, "screening_gate") == "record_only":
        text += SCREENING_RECORD_ONLY
    resweep = enabled(case, "prefreeze_resweep")
    if resweep:
        text += RESWEEP_CONTRACT.format(margin=resweep["margin"], count=resweep["max_candidates"])
    if enabled(case, "api_revision") == "m4-r1":
        text += API_M4_CONTRACT.format(skill=API_M4_SKILL)
    if enabled(case, "success_rule") == "libero_latch":
        text += SUCCESS_RULE_CONTRACT
    return text


def batch_sweep_contract(case):
    parallel = case["sweep_parallel"]
    return BATCH_SWEEP_CONTRACT.format(parallel=parallel,
                                       slots=-(-(len(case["dev_seeds"]) - 1) // parallel))

def section(case):
    validate(case)
    arm = case["c_arm"]
    goal_instruction = ('Define explicit goal clauses with current public evidence, using the foundation state contract. Read the per-trial development calibration feedback and correct false positives/negatives or unknown causes. Keep self-evaluation observational in this study: do not add a new recovery or termination controller based on done().' if case.get("foundation_revision") else 'Use world.done() to make a concrete stopping/checking/recovery decision. Define a task goal from model state and admissible fresh observations. Unknown is not success; obtain evidence or handle uncertainty. The evaluator records a separate final judgment.')
    if closed_loop(case):
        goal_instruction = CLOSED_LOOP_GOAL
    if (case.get("method_switches") or {}).get("subgoal_verification") and case.get("foundation_revision"):
        goal_instruction = edited(goal_instruction, (SUBGOAL_GOAL_EDIT,))
    if sealed(case):
        goal_instruction = SEALED_GOAL[development_gate(case)]
    text = f"""
---
## {HEADINGS[lineage(case)]}: {arm}

Read `{interface_doc(case)}` in full before editing. Its executable world contract replaces
the old judgment-world contract: define update, query, snapshot, predict, observe,
simulate, done. Save initial_world_program.py and fix_world_program.py alongside
the matching policy files. There is no object inventory or high-level MD library.

{lineage_section(case)}

All arms must use real observations rather than commanded targets as evidence.
Public API results now automatically reach observe(event); update() for derived
quantities must cite genuine observation IDs. A successful motion command is
never proof of attachment, object motion or task completion. Use the actual API
reference conventions (including clipping), estimate numeric parameters from
development evidence, and include parameter provenance in the generated world.
Preserve useful behavior while revising both world and policy; do not retain an
incorrect world just because it imports without errors.

Arm switches are frozen. self-evaluation={'OFF' if arm == 'no_self_eval' else 'ON'};
candidate rehearsal/replay={'OFF' if arm == 'no_rehearsal' else 'ON'}.
{'Do not call done() or substitute another online goal predicate. Keep grounded low-level state/queries and ordinary action completion. No self-evaluation drives stopping/recovery/selection.' if arm == 'no_self_eval' else goal_instruction}
{'Do not invoke offline candidate screening; generate and repair from real development feedback under the same three-retry budget per seed.' if arm == 'no_rehearsal' else 'Before each changed candidate, run the offline check below, inspect its actual report, and fix supported problems before spending a real attempt. A matching cached report is reused by trial admission. Unsupported prediction/replay divergence is not a predicted failure and does not authorize inventing observations.'}

Offline check (only the rehearsal-enabled arms):
  .venv-libero/bin/python3 scripts/libero/executable_world_profile.py --code "$TASK_DIR/initial_code.py" --world-program "$TASK_DIR/initial_world_program.py"
Use the current candidate paths when repairing. It executes the SAME policy
source with generated simulate() API effects and exact pure math helpers; when
a new development tape exists it also checks the recorded prefix. A changed
action stops replay before the old future observation. No simulator runs here;
rehearsal costs and rejected candidates are recorded separately from real trials.
Unknown/unsupported checks do not block a first real experiment. Actual Python
errors in a supported rehearsal block admission until repaired, without spending
a simulator attempt. Do not claim a successful mock execution establishes real
task success, and do not branch policy behavior on the binding mode.

The report's `status` says how the check ended; its `conclusions` say what the
check established. `supported_pass` means the supported part ran without an
authored error. `unknown` means an unsupported prediction or a replay divergence
gave no verdict — it is not a pass. `authored_error` is yours to repair. A
`blocked` screening is the infrastructure's failure, not your candidate's: it is
not cached, does not reject the candidate, and does not spend a retry.

Everything else below retains the original native failure-by-failure Fix Loop,
51–65 development budget, tested-bundle selection and outer frozen evaluation.
"""
    if closed_loop(case):
        assert text.count(CLOSED_LOOP_ANCHOR) == 1
        text = text.replace(CLOSED_LOOP_ANCHOR, CLOSED_LOOP_CONTRACT.lstrip("\n") + "\n" + CLOSED_LOOP_ANCHOR)
    if sealed(case):
        assert text.count(CLOSED_LOOP_ANCHOR) == 1
        contract = SEALED_GATE_CONTRACT.format(gate=development_gate(case),
                                               detail=SEALED_GATE_DETAIL[development_gate(case)])
        text = text.replace(CLOSED_LOOP_ANCHOR, contract.lstrip("\n") + "\n" + CLOSED_LOOP_ANCHOR)
    if prediction_contract(case):
        assert text.count(CLOSED_LOOP_ANCHOR) == 1
        contract = (RUNTIME_CONTRACT if case.get("prediction_contract") in RUNTIME_CONTRACTS
                    else PREDICTION_CONTRACT)
        if case.get("prediction_contract") == "p3-search":
            if budgeted(case):
                contract = edited(contract, (BUDGET_RUNTIME_EDIT,)) + budget_search_contract()
            else:
                contract += SEARCH_CONTRACT
            if "sweep_parallel" in case:
                contract += batch_sweep_contract(case)
            if case.get("method_switches"):
                contract = edited(contract, method_edits(case)) + method_contract(case)
        text = text.replace(CLOSED_LOOP_ANCHOR, contract.lstrip("\n") + "\n" + CLOSED_LOOP_ANCHOR)
    return text


def latest_tape(state):
    for row in reversed(state.data["trials"]):
        if row.get("spends_retry") and row.get("executed") and row.get("status") != "running":
            p = state.task_dir / row["directory"] / "judgment_world/public_tape.jsonl"
            if p.is_file():
                return p.resolve()
    return None


def run_mode(case, repo, env, folder, sources, mode, tape, timeout):
    """One offline binding. Separates what the candidate did from how it ended.

    A report the candidate produced is authored evidence — including one that
    says the world module raised while loading, which `run_offline` now emits
    instead of dying with no artifact. A watchdog kill, a launch failure, a
    missing report, or a truncated/unparsable one is infrastructure: the
    candidate never got a verdict, so it must not be recorded as one.
    """
    output = folder / mode
    command = [str(repo / ".venv-libero/bin/python3"), str(Path(__file__).resolve()),
               "--internal", "--code", str(sources["policy"]), "--world-program", str(sources["world"]),
               "--mode", mode, "--arm", case["c_arm"], "--output", str(output),
               "--task-language", case["task"].replace("_", " ")]
    if tape:
        command.extend(["--tape", str(tape)])
    if closed_loop(case):
        command.append("--closed-loop")
    if prediction_contract(case):
        command.extend(["--prediction-contract", case["prediction_contract"]])
    def blocked(reason, **extra):
        return {"mode": mode, "status": "infrastructure_error", "retryable": True,
                "reason": reason, "conclusion": "unknown", "output": str(output), **extra}
    with (folder / (mode + ".log")).open("w") as log:
        try:
            proc = subprocess.run(command, cwd=repo, env=env, stdout=log,
                                  stderr=subprocess.STDOUT, timeout=timeout)
        except subprocess.TimeoutExpired:
            return blocked(f"offline process watchdog expired ({timeout}s)")
        except OSError as exc:
            # The interpreter or this script could not be launched at all. No
            # candidate verdict exists, so it must stay a retryable blocker
            # rather than escape as an unhandled error from the trial path.
            return blocked(f"offline process could not be launched: {exc}")
        path = output / "offline_result.json"
        if not path.exists():
            return blocked("offline process produced no report", exit_code=proc.returncode)
        try:
            report = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            # A truncated or unreadable report is the same evidentiary state as
            # no report: the candidate never got a verdict.
            return blocked(f"offline report is unreadable: {exc}", exit_code=proc.returncode)
    if not isinstance(report, dict):
        return blocked("offline report is not a JSON object")
    if report.get("status") not in AUTHORED_STATUSES:
        return {**report, "status": "infrastructure_error", "retryable": True,
                "reason": f"offline report status {report.get('status')!r} is not a candidate verdict",
                "conclusion": "unknown", "output": str(output)}
    return {**report, "retryable": False, "conclusion": CONCLUSIONS[report["status"]],
            "output": str(output)}


def screening_infrastructure_ok(reports):
    """Every requested screening returned an authored outcome, not a service fault."""
    return (isinstance(reports, list) and bool(reports)
            and all(isinstance(r, dict) and isinstance(r.get("status"), str)
                    and r["status"] in AUTHORED_STATUSES
                    for r in reports))


def screening_status(reports):
    """Authored errors reject. Infrastructure blocks but never rejects.

    `checked` says the supported part of the screening ran; it does NOT say the
    candidate would succeed. `conclusions` carries that, and an `unknown` from an
    unsupported prediction or replay divergence stays unknown. A report marked
    `recorded_only` (CC_TASK_14 screening_gate record_only: the rehearsal's
    prediction-level rule) is kept for the record and rejects nothing.
    """
    if any(r["status"] in ("program_error", "unsupported_contract") and not r.get("recorded_only")
           for r in reports):
        return "rejected"
    if any(r["status"] == "infrastructure_error" for r in reports):
        return "blocked"
    return "checked"


def checks(case, repo, state, sources, env, *, timeout=None):
    """No simulation, no retry spent; cache only identical sources AND tape.

    Only an authored outcome is cached. An infrastructure failure is appended to
    the audit log and returned as `blocked`, so the same candidate can be screened
    again once the infrastructure recovers: a transient watchdog expiry must never
    become a permanent verdict on a frozen candidate.
    """
    validate(case)
    if not enabled(case) or case["c_arm"] == "no_rehearsal":
        return {"status": "disabled", "reports": []}
    tape = latest_tape(state)
    identity = {"arm": case["c_arm"],
                "policy": hashlib.sha256(Path(sources["policy"]).read_bytes()).hexdigest(),
                "world": hashlib.sha256(Path(sources["world"]).read_bytes()).hexdigest(),
                "tape": str(tape) if tape else None,
                "tape_sha256": hashlib.sha256(tape.read_bytes()).hexdigest() if tape else None}
    if closed_loop(case):
        # Added only when set, so every legacy cache key is unchanged and a
        # closed-loop screening can never reuse an observational one.
        identity["closed_loop_revision"] = case["closed_loop_revision"]
    if prediction_contract(case):
        # A prediction revision never reuses another revision's cache entry.
        identity["prediction_contract"] = case["prediction_contract"]
    record_only = (case.get("method_switches") or {}).get("screening_gate") == "record_only"
    if record_only:
        identity["screening_gate"] = "record_only"  # Never reuses a gated cache entry.
    root = state.task_dir / "attempts/offline"
    root.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    index = root / (key + ".json")
    if index.exists():
        prior = json.loads(index.read_text())
        if prior["identity"] != identity:
            raise ValueError("offline cache identity changed")
        # Older caches may contain a mixed authored/infrastructure failure:
        # aggregate rejection never makes the infrastructure outcome reusable.
        if screening_infrastructure_ok(prior.get("reports")):
            return {**prior, "cached": True}
    if timeout is None:
        timeout = case.get("offline_check_timeout", OFFLINE_TIMEOUT_SECONDS)
    folder = root / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:8])
    folder.mkdir()
    reports = [run_mode(case, repo, env, folder, sources, mode, tape, timeout)
               for mode in (("rehearsal", "replay") if tape else ("rehearsal",))]
    if record_only:
        for report in reports:
            if report.get("status") == "unsupported_contract" and report.get("stage") == "prediction_levels":
                report["recorded_only"] = True  # CC_TASK_14 M3(c): a finding, not a refusal.
    status = screening_status(reports)
    result = {"identity": identity, "reports": reports, "status": status,
              "conclusions": [r["conclusion"] for r in reports],
              # Nothing here ran a simulator, and an unsupported check is not a pass.
              "establishes_task_success": False,
              "retryable": status == "blocked", "cached": False,
              "real_simulator_executions": 0, "directory": str(folder)}
    if closed_loop(case):
        # Structural notes ride beside the verdict and never change `status`: a
        # concern is something to fix, not a rejection, and an inconclusive
        # analysis is not a defect. Computed here, in-process, so it cannot
        # time out, block, or spend anything.
        try:
            result["closed_loop_findings"] = closed_loop_module().source_findings(
                Path(sources["world"]).read_text(), Path(sources["policy"]).read_text())
        except (OSError, ValueError, RecursionError) as exc:
            result["closed_loop_findings"] = {"findings": [{
                "kind": "analysis_unavailable", "severity": "inconclusive",
                "detail": f"{type(exc).__name__}: {exc}"}]}
    if screening_infrastructure_ok(reports):
        index.write_text(json.dumps(result, indent=2) + "\n")
    with (root / "checks.jsonl").open("a") as log:
        log.write(json.dumps(result) + "\n")
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--code", type=Path, required=True)
    p.add_argument("--world-program", type=Path, required=True)
    p.add_argument("--internal", action="store_true")
    p.add_argument("--case", type=Path, default=os.environ.get("ASPIRE_NATIVE_CASE"))
    p.add_argument("--mode", choices=("rehearsal", "replay"), default="rehearsal")
    p.add_argument("--arm", choices=("full", "no_self_eval", "no_rehearsal"))
    p.add_argument("--output", type=Path)
    p.add_argument("--tape", type=Path)
    p.add_argument("--task-language", default="")
    p.add_argument("--closed-loop", action="store_true")
    p.add_argument("--prediction-contract", choices=("off", "p1", "p2-runtime", "p3-search"))
    a = p.parse_args()
    repo = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(repo.parents[1]))
    if a.internal:
        from aspire.sim.cap.world_model.executable_world import run_offline
        report = run_offline(a.code, a.world_program, a.output, arm=a.arm, mode=a.mode,
                             tape=a.tape, task_language=a.task_language,
                             pure_source=repo / "cap/integrations/franka/libero_reduced_skill_library.py",
                             closed_loop=a.closed_loop, prediction_contract=a.prediction_contract)
    else:
        import native_world_protocol as protocol
        if not a.case:
            p.error("--case or ASPIRE_NATIVE_CASE required")
        case, repo, task_dir = protocol.load_case(a.case)
        protocol.verify_runtime(case, repo)
        validate(case)
        if not enabled(case) or case["c_arm"] == "no_rehearsal":
            p.error("candidate screening is disabled for this arm")
        state = protocol.NativeWorldState(task_dir, protocol.identity(case, repo))
        _, sources = protocol.collect_bundle(case, task_dir, "repair", a.code, a.world_program, None)
        report = checks(case, repo, state, sources, protocol.runtime_env(case, repo))
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
