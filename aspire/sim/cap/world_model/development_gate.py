"""Opt-in development gate: what grades a development trial inside the Fix Loop.

Three gates. `oracle` is the absent-key default and the legacy behaviour byte for
byte: the simulator's `task_completed` label is read from the trial directory
name, drives progress and selection, and feeds the per-trial calibration file.

`self_eval` and `vlm_judge` SEAL that label away from the solver. The trial still
runs in the real simulator and the outer evaluator still grades held-out seeds
with the environment, but during development:

- the replay writes the real outcome to a sealed file under the control root,
  outside the solver's read boundary, and names its public artifacts without it;
- the public ledger row carries a `gate` verdict instead of `task_completed`:
  the world's own final `done()` judgment (`self_eval`), or an independent
  same-backbone VLM reading three rollout frames (`vlm_judge`);
- progress, repair triage and selection use that verdict; `unknown`/`unsure`
  never pass;
- the oracle and the gate are recorded side by side in `control/sealed/` so the
  outer analysis can report gate agreement and false accept/reject rates.

This is a protocol boundary, enforced by the recorded protocol, the read guard
and the sandbox proxy. It is not a hardened security sandbox.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

GATES = ("oracle", "self_eval", "vlm_judge")
DEFAULT = "oracle"
#: The name the replay gives a trial directory when the outcome is sealed.
SEALED_SUFFIX = "sealed"
#: Environment variable the protocol sets for a sealed replay: the file that
#: receives the real outcome. Absent in every legacy and held-out run.
SEALED_ENV = "ASPIRE_SEALED_OUTCOME"
#: Public-artifact tokens that would reveal the sealed outcome. Checked by tests
#: and by the launch preflight over a real sealed trial directory.
FORBIDDEN_PUBLIC_TOKENS = ("taskcompleted", "task_completed", "Task Completed",
                           "_reward_", "reward=", "Reward:", "terminated=True",
                           "terminated=False", "video_0.", "video_1.")
#: Authored-source tokens a sealed cell refuses in addition to the legacy
#: forbidden simulator APIs: every route from generated code to the oracle.
SEALED_SOURCE_PATTERNS = (
    r"\b(?:task_completed|compute_reward|check_success|_check_success)\b",
    r"__self__", r"\bAPIS\b", r"\blow_level_env\b",
    r"\bhandle\.(?!task_language\b)",
)
NOT_EVALUATED = "not_evaluated"


def gate(case: dict) -> str:
    return case.get("development_gate") or DEFAULT


def sealed(case: dict) -> bool:
    return gate(case) != DEFAULT


def revision_errors(case: dict) -> list[str]:
    """Refuse an incompatible combination before anything is staged or run."""
    value = case.get("development_gate")
    if not value:
        return []
    errors = []
    if value not in GATES:
        errors.append(f"unknown development_gate {value!r}; expected one of {GATES}")
    if value == DEFAULT:
        return errors  # Spelled-out default: legacy behaviour, nothing else to check.
    if case.get("executable_world_revision") != "r1":
        errors.append("a sealed development gate requires executable_world_revision 'r1'")
    if case.get("foundation_revision") != "r1":
        errors.append("a sealed development gate requires foundation_revision 'r1' (clause-validated done())")
    if case.get("condition") != "C" or case.get("profile") != "judgment":
        errors.append("a sealed development gate is a C-only judgment-profile opt-in")
    if case.get("c_arm") != "full":
        errors.append("a sealed development gate requires c_arm 'full': every gate arm records the "
                      "same shadow self-evaluation, so the arms stay comparable")
    if case.get("closed_loop_revision"):
        errors.append("a sealed development gate is not combined with closed_loop_revision in this build")
    if value == "vlm_judge":
        judge = case.get("judge") or {}
        if not judge.get("endpoint") and not case.get("inference_endpoint"):
            errors.append("vlm_judge needs case['judge']['endpoint'] or case['inference_endpoint']")
        if not judge.get("model") and not case.get("model"):
            errors.append("vlm_judge needs case['judge']['model'] or case['model']")
    return errors


def validate(case: dict) -> None:
    problems = revision_errors(case)
    if problems:
        raise ValueError("; ".join(problems))


def judge_config(case: dict) -> dict:
    """The judge's served model: by default the solver's own backbone."""
    judge = dict(case.get("judge") or {})
    return {"endpoint": judge.get("endpoint") or case["inference_endpoint"],
            "model": judge.get("model") or case["model"],
            "effort": judge.get("effort") or case.get("effort", "high"),
            "max_tokens": int(judge.get("max_tokens", 4096)),
            "timeout": int(judge.get("timeout", 600)),
            "frames": int(judge.get("frames", 3))}


def sealed_root(case: dict) -> Path:
    return Path(case["control"]) / "sealed"


def sealed_trial_dir(case: dict, relative_directory: str) -> Path:
    return sealed_root(case) / relative_directory


def sealed_ledger(case: dict) -> Path:
    return sealed_root(case) / "development_outcomes.jsonl"


def source_errors(source: str) -> list[str]:
    """Routes from authored code to the oracle that a sealed cell refuses."""
    return [f"sealed cell refuses source matching {pattern}"
            for pattern in SEALED_SOURCE_PATTERNS if re.search(pattern, source)]


# --- reading the pieces -------------------------------------------------------


def read_sealed_outcome(path: Path) -> dict | None:
    try:
        outcome = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError):
        return None
    required = ("sandbox_rc", "reward", "task_completed", "trial_dir")
    if not isinstance(outcome, dict) or any(k not in outcome for k in required):
        return None
    return outcome


def final_self_evaluation(trial_directory: Path) -> dict | None:
    """The framework's final shadow judgment recorded by the executable world."""
    try:
        manifest = json.loads((Path(trial_directory) / "judgment_world/manifest.json").read_text())
    except (OSError, json.JSONDecodeError):
        return None
    rows = [e for e in manifest.get("self_evaluations") or [] if isinstance(e, dict)]
    shadow = [e for e in rows if e.get("binding") == "shadow"]
    final = [e for e in shadow if e.get("origin") == "framework_final"]
    chosen = final[-1] if final else (shadow[-1] if shadow else None)
    if chosen is None:
        return None
    return {**chosen, "manifest_status": manifest.get("status")}


def self_eval_gate(trial_directory: Path) -> dict:
    final = final_self_evaluation(trial_directory)
    if final is None:
        return {"source": "self_eval", "verdict": "unavailable", "passed": False,
                "reason": "no final shadow self-evaluation was recorded for this trial"}
    verdict = final.get("verdict")
    if verdict not in {"true", "false", "unknown"}:
        verdict = "unavailable"
    return {"source": "self_eval", "verdict": verdict, "passed": verdict == "true",
            "reason": final.get("reason"), "evidence_ids": final.get("evidence_ids"),
            "clauses": [{k: c.get(k) for k in ("name", "verdict", "reason")}
                        for c in (final.get("clauses") or []) if isinstance(c, dict)],
            "authored_verdict": final.get("authored_verdict")}


def vlm_judge_gate(case: dict, trial_directory: Path, *, judge=None) -> dict:
    from .vlm_judge import judge as default_judge
    config = judge_config(case)
    report = (judge or default_judge)(
        config["endpoint"], config["model"], case["task"].replace("_", " "),
        Path(trial_directory), effort=config["effort"], max_tokens=config["max_tokens"],
        timeout=config["timeout"], frames=config["frames"])
    verdict = report.get("verdict") if report.get("status") == "complete" else "unavailable"
    return {"source": "vlm_judge", "verdict": verdict or "unavailable",
            "passed": verdict == "success", "reason": report.get("reason"),
            "frames": report.get("frames"), "model": report.get("model"),
            "judge_status": report.get("status"), "elapsed_seconds": report.get("elapsed_seconds")}


def not_evaluated(case: dict, reason: str) -> dict:
    return {"source": gate(case), "verdict": NOT_EVALUATED, "passed": False, "reason": reason}


def evaluate(case: dict, attempt_directory: Path, *, trial_dir: Path | None = None,
             judge=None) -> dict:
    """The gate verdict for one finished development trial. Never the oracle.

    `attempt_directory` holds the world manifest (self-evaluation); `trial_dir`
    is the replay's public trial directory with the keyframes the judge reads.
    """
    which = gate(case)
    if which == "self_eval":
        return self_eval_gate(attempt_directory)
    if which == "vlm_judge":
        return vlm_judge_gate(case, trial_dir or attempt_directory, judge=judge)
    raise ValueError("the oracle gate has no sealed verdict to compute")


def calibration(self_eval_verdict, task_completed) -> str:
    if self_eval_verdict not in {"true", "false"}:
        return "unknown" if self_eval_verdict == "unknown" else "unavailable"
    return {("true", True): "true_positive", ("true", False): "false_positive",
            ("false", True): "false_negative", ("false", False): "true_negative"}[
                (self_eval_verdict, bool(task_completed))]


def public_result(oracle: dict, gate_report: dict) -> dict:
    """What the public ledger row stores for a sealed trial: no label, no reward."""
    return {"sandbox_rc": int(oracle["sandbox_rc"]), "trial_dir": oracle["trial_dir"],
            "outcome": "sealed", "gate": gate_report}


def seal(case: dict, record: dict, oracle: dict | None, gate_report: dict,
         self_eval: dict | None = None, judge_report: dict | None = None) -> dict:
    """Append the oracle beside the gate to the sealed ledger; never public."""
    row = {"schema_version": 1, "cell": case["id"], "development_gate": gate(case),
           "phase": record.get("phase"), "seed": record.get("seed"),
           "attempt": record.get("attempt"), "directory": record.get("directory"),
           "bundle_sha256": record.get("bundle_sha256"), "bundle": record.get("bundle"),
           "status": record.get("status"),
           "oracle": None if oracle is None else {
               "sandbox_rc": int(oracle.get("sandbox_rc", 0)), "reward": float(oracle.get("reward", 0.0)),
               "task_completed": bool(oracle.get("task_completed")),
               "terminated": oracle.get("terminated"), "truncated": oracle.get("truncated"),
               "trial_dir": oracle.get("trial_dir")},
           "gate": gate_report,
           "self_eval_verdict": (self_eval or {}).get("verdict"),
           "judge_verdict": (judge_report or {}).get("verdict")}
    if oracle is not None:
        row["gate_false_accept"] = bool(gate_report.get("passed")) and not bool(oracle["task_completed"])
        row["gate_false_reject"] = (not gate_report.get("passed")) and bool(oracle["task_completed"])
        row["self_eval_calibration"] = calibration(row["self_eval_verdict"], oracle["task_completed"])
    root = sealed_root(case)
    root.mkdir(parents=True, exist_ok=True)
    with sealed_ledger(case).open("a") as stream:
        stream.write(json.dumps(row, sort_keys=True) + "\n")
    if record.get("directory"):
        folder = sealed_trial_dir(case, record["directory"])
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "oracle.json").write_text(json.dumps(row, indent=2, sort_keys=True) + "\n")
    return row


def read_sealed_ledger(case_or_path) -> list[dict]:
    path = Path(case_or_path) if isinstance(case_or_path, (str, Path)) else sealed_ledger(case_or_path)
    rows = []
    try:
        for line in Path(path).read_text().splitlines():
            if line.strip():
                rows.append(json.loads(line))
    except (OSError, json.JSONDecodeError):
        return rows
    return rows


# --- leak check ---------------------------------------------------------------

#: Solver-authored sources may legitimately say "reward" in a comment; they are
#: not framework output and are excluded from the scan.
AUTHORED_NAMES = {"code.py", "world_program.py", "initial_code.py", "fix_code.py",
                  "initial_world_program.py", "fix_world_program.py"}
TEXT_SUFFIXES = {".txt", ".log", ".json", ".jsonl", ".md", ".py", ".yaml"}


def public_leaks(trial_directory: Path) -> list[str]:
    """Names and framework-written text under a sealed trial that reveal the outcome."""
    leaks = []
    root = Path(trial_directory)
    for path in sorted(root.rglob("*")):
        relative = str(path.relative_to(root))
        lowered = relative.lower()
        if any(token.lower() in lowered for token in ("taskcompleted", "_reward_", "video_0.", "video_1.")):
            leaks.append(f"name: {relative}")
        if path.is_file() and path.suffix in TEXT_SUFFIXES and path.name not in AUTHORED_NAMES \
                and "arrays" not in path.parts:
            try:
                text = path.read_text(errors="replace")
            except OSError:
                continue
            for token in FORBIDDEN_PUBLIC_TOKENS:
                if token in text:
                    leaks.append(f"text: {relative} contains {token!r}")
    return leaks
