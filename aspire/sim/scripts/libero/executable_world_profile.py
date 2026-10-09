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
AUTHORED_STATUSES = frozenset({"complete", "unsupported", "program_error"})
# What the screening actually established about the candidate, as opposed to how
# the offline process terminated. `unknown` is never evidence of success.
CONCLUSIONS = {"complete": "supported_pass", "unsupported": "unknown",
               "program_error": "authored_error"}


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
        raise ValueError("prediction_contract p1 requires the r1 executable world (condition C)")


#: Absent and "off" are the same contract-off cell.
PREDICTION_CONTRACTS = (None, "off", "p1")


def prediction_contract(case):
    """True only for a cell that explicitly opted into prediction contract p1."""
    return case.get("prediction_contract") == "p1"


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


def section(case):
    validate(case)
    arm = case["c_arm"]
    goal_instruction = ('Define explicit goal clauses with current public evidence, using the foundation state contract. Read the per-trial development calibration feedback and correct false positives/negatives or unknown causes. Keep self-evaluation observational in this study: do not add a new recovery or termination controller based on done().' if case.get("foundation_revision") else 'Use world.done() to make a concrete stopping/checking/recovery decision. Define a task goal from model state and admissible fresh observations. Unknown is not success; obtain evidence or handle uncertainty. The evaluator records a separate final judgment.')
    if closed_loop(case):
        goal_instruction = CLOSED_LOOP_GOAL
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
        text = text.replace(CLOSED_LOOP_ANCHOR, PREDICTION_CONTRACT.lstrip("\n") + "\n" + CLOSED_LOOP_ANCHOR)
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
        command.extend(["--prediction-contract", "p1"])
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
    unsupported prediction or replay divergence stays unknown.
    """
    if any(r["status"] == "program_error" for r in reports):
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
        # Same rule: a p1 screening never reuses a contract-off cache entry.
        identity["prediction_contract"] = "p1"
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
    p.add_argument("--prediction-contract", choices=("off", "p1"))
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
