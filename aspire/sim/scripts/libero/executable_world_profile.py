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


def section(case):
    validate(case)
    arm = case["c_arm"]
    goal_instruction = ('Define explicit goal clauses with current public evidence, using the foundation state contract. Read the per-trial development calibration feedback and correct false positives/negatives or unknown causes. Keep self-evaluation observational in this study: do not add a new recovery or termination controller based on done().' if case.get("foundation_revision") else 'Use world.done() to make a concrete stopping/checking/recovery decision. Define a task goal from model state and admissible fresh observations. Unknown is not success; obtain evidence or handle uncertainty. The evaluator records a separate final judgment.')
    return f"""
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
{'Do not invoke offline candidate screening; generate and repair from real development feedback under the same three-total-attempt budget.' if arm == 'no_rehearsal' else 'Before each changed candidate, run the offline check below, inspect its actual report, and fix supported problems before spending a real attempt. A matching cached report is reused by trial admission. Unsupported prediction/replay divergence is not a predicted failure and does not authorize inventing observations.'}

Offline check (only the rehearsal-enabled arms):
  .venv-libero/bin/python3 scripts/libero/executable_world_profile.py --code "$TASK_DIR/initial_code.py" --world-program "$TASK_DIR/initial_world_program.py"
Use the current candidate paths when repairing. It executes the SAME policy
source with generated simulate() API effects and exact pure math helpers; when
a new development tape exists it also checks the recorded prefix. A changed
action stops replay before the old future observation. No simulator runs here;
rehearsal costs and rejected candidates are recorded separately from real trials.
Unknown/unsupported checks do not block a first real experiment. Actual Python
errors in a supported rehearsal block admission until repaired, without charging
a simulator attempt. Do not claim a successful mock execution establishes real
task success, and do not branch policy behavior on the binding mode.

The report's `status` says how the check ended; its `conclusions` say what the
check established. `supported_pass` means the supported part ran without an
authored error. `unknown` means an unsupported prediction or a replay divergence
gave no verdict — it is not a pass. `authored_error` is yours to repair. A
`blocked` screening is the infrastructure's failure, not your candidate's: it is
not cached, does not reject the candidate, and does not consume an attempt.

Everything else below retains the original native failure-by-failure Fix Loop,
51–65 development budget, tested-bundle selection and outer frozen evaluation.
"""


def latest_tape(state):
    for row in reversed(state.data["trials"]):
        if row.get("charged") and row.get("executed") and row.get("status") != "running":
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
    """No simulation, no ledger charge; cache only identical sources AND tape.

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
    root = state.task_dir / "attempts/offline"
    root.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    index = root / (key + ".json")
    if index.exists():
        prior = json.loads(index.read_text())
        if prior["identity"] != identity:
            raise ValueError("offline cache identity changed")
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
    if status != "blocked":
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
    a = p.parse_args()
    repo = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(repo.parents[1]))
    if a.internal:
        from aspire.sim.cap.world_model.executable_world import run_offline
        report = run_offline(a.code, a.world_program, a.output, arm=a.arm, mode=a.mode,
                             tape=a.tape, task_language=a.task_language,
                             pure_source=repo / "cap/integrations/franka/libero_reduced_skill_library.py")
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
