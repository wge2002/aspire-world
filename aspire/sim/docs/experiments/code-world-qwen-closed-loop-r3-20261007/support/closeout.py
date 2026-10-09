"""Bounded report-only closeout after the development watchdog, and its guard.

The development watchdog can expire in two very different states. r2 hit both:
one cell was still executing trials (39/45), the other had finished every graded
execution and selected a tested bundle but never wrote its report. This module
lets the driver tell them apart from structured evidence only, and recover the
second state without a single new simulator execution:

- `live_task_processes` is a bounded, read-only /proc scan for trial or solver
  processes this cell still owns. A CC process-group kill does not reach a
  replay child that runs in its own session, so quiescence is observed, never
  assumed. Nothing is signalled here.
- `classify` admits report-only recovery only for completed graded development
  with a selected, tested bundle whose remaining errors are all report-class
  (findings, task_analysis, the working-code copy). Anything else fails closed.
- `frozen_identity` pins the selected bundle and the charged ledger rows, so a
  recovery that touched either is refused.
- `ReadyEvidence` is the transport's `terminal_evidence`: a real strict protocol
  check, run only on turn/task boundaries whose watched files changed. Model
  prose and the outer-only `stage1_complete` are never evidence.
- `report_only_denial` (and `main`, the PreToolUse hook) denies every shell
  call, every new agent and every write outside the report artifacts. The
  original read guard stays installed beside it.

This adds no attempt, refunds none, and changes no deadline.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import sys
import time

#: Errors from `NativeWorldState.completion_errors` that only report artifacts
#: can clear. Every other error string is outside report-only recovery.
REPORT_ERRORS = (
    re.compile(r"missing task_analysis\.md"),
    re.compile(r"missing findings\.md"),
    re.compile(r"findings\.md contains placeholder text"),
    re.compile(r"findings\.md needs a nonempty '[^']+' section.*"),
    re.compile(r"working_codes copy must match fix_code\.py"),
)

#: Progress lists that must all be empty for development to count as complete.
INCOMPLETE_KEYS = ("seeds_needing_initial", "seeds_exhausted_without_graded_evidence",
                   "seeds_pending_repair", "blocked_notes_required")
UNRESOLVED_KEYS = ("infrastructure_errors", "unresolved_screening_blockers")

#: Command-line markers of a task trial, replay or evaluator process.
TRIAL_MARKERS = ("replay_trial.py", "native_world_protocol.py", "native_world_heldout.py",
                 "heldout_stop_on_infra.py")

#: Selected-bundle files whose bytes must survive recovery unchanged.
BUNDLE_FILES = ("fix_code.py", "fix_world_program.py", "fix_inventory.json")

#: Tools a report-only worker may call without a path check.
READ_TOOLS = {"Read", "Glob", "Grep", "SendMessage", "TaskOutput"}
WRITE_TOOLS = {"Write", "Edit", "MultiEdit"}

#: `native_cc_stream.run_native_cc`'s default uninterrupted first wait. It is
#: never shortened, so no closeout session starts with less reserve than this.
NATIVE_FIRST_WAIT = 300


def task_dir(case: dict) -> Path:
    return Path(case["sim"]) / "outputs/libero_fix_loop" / case["suite"] / case["task"]


def working_copy(case: dict) -> Path:
    return Path(case["sim"]) / "outputs/working_codes" / f"{case['suite']}_{case['task']}_fix.py"


def report_paths(case: dict) -> list[Path]:
    """The only files a report-only worker may write."""
    task = task_dir(case)
    return [task / "findings.md", task / "task_analysis.md", working_copy(case)]


def sha256(path: Path) -> str | None:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except FileNotFoundError:
        return None


# ---- terminal evidence ----------------------------------------------------------

def watched_paths(case: dict) -> list[Path]:
    task = task_dir(case)
    return [task / "development_state.json", *(task / n for n in BUNDLE_FILES),
            task / "findings.md", task / "task_analysis.md", working_copy(case)]


def fingerprint(case: dict) -> tuple:
    rows = []
    blocked = sorted((task_dir(case) / "attempts").glob("seed_*_BLOCKED.md"))
    for path in [*watched_paths(case), *blocked]:
        try:
            stat = path.stat()
            rows.append((str(path), stat.st_mtime_ns, stat.st_size))
        except OSError:
            rows.append((str(path), None, None))
    return tuple(rows)


def gated(event: dict) -> bool:
    """A parent turn end or a native task completion; never a stream token."""
    if event.get("type") == "result":
        return True
    return event.get("type") == "system" and event.get("subtype") == "task_notification"


def strict_ready(step: dict | None) -> bool:
    result = (step or {}).get("result")
    return (step or {}).get("exit_code") == 0 and isinstance(result, dict) \
        and result.get("ready") is True


class ReadyEvidence:
    """`terminal_evidence` backed by an actual strict protocol check.

    `run_check(name)` runs the original `check` and returns its recorded step.
    It is called only on a gated event, and only when a watched ledger, bundle
    or report file changed since the previous check, so a long stream costs a
    handful of checks rather than one per token. A failing check is a refusal,
    never an exception in the transport's dispatcher.
    """

    def __init__(self, run_check, case: dict, prefix: str):
        self.run_check = run_check
        self.case = case
        self.prefix = prefix
        self.last = None
        self.checks: list[dict] = []

    def __call__(self, event: dict) -> bool:
        if not gated(event):
            return False
        current = fingerprint(self.case)
        if current == self.last:
            return False
        self.last = current
        name = f"{self.prefix}-{len(self.checks):03d}"
        try:
            step = self.run_check(name)
        except Exception as exc:  # recorded refusal; the outer check still decides
            self.checks.append({"name": name, "ready": False,
                                "error": f"{type(exc).__name__}: {exc}"})
            return False
        ready = strict_ready(step)
        self.checks.append({"name": name, "ready": ready, "exit_code": step.get("exit_code"),
                            "event": event.get("type"), "subtype": event.get("subtype")})
        return ready

    def record(self) -> dict:
        return {"checks": list(self.checks), "accepted": any(c["ready"] for c in self.checks)}


# ---- quiescence -----------------------------------------------------------------

def _ancestors(pid: int, proc: Path) -> set[int]:
    seen = set()
    while pid > 1 and pid not in seen:
        seen.add(pid)
        try:
            pid = int((proc / str(pid) / "stat").read_text().rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            break
    return seen


def _case_env(entry: Path) -> str | None:
    try:
        for item in (entry / "environ").read_bytes().split(b"\0"):
            if item.startswith(b"ASPIRE_NATIVE_CASE="):
                return item.split(b"=", 1)[1].decode(errors="replace")
    except OSError:
        return None
    return None


def scan_task_processes(case: dict, case_path: Path, *, proc: Path = Path("/proc")) -> list[dict]:
    """Read-only: processes this cell still owns. Signals nothing.

    Owned means either the solver environment's `ASPIRE_NATIVE_CASE` names this
    case, or a trial/replay/native executable runs with its cwd inside this
    cell's isolated runtime or control directory. This driver and its ancestors
    are excluded; shared services carry neither mark.
    """
    roots = [Path(case["sim"]).resolve(), Path(case["control"]).resolve()]
    case_names = {str(case_path), str(Path(case_path).resolve())}
    claude = case.get("claude_bin")
    own = _ancestors(os.getpid(), proc)
    found = []
    for entry in proc.iterdir():
        if not entry.name.isdigit() or int(entry.name) in own:
            continue
        try:
            argv = [a.decode(errors="replace")
                    for a in (entry / "cmdline").read_bytes().split(b"\0") if a]
        except OSError:
            continue
        if not argv:  # kernel thread or zombie: no work can run
            continue
        try:
            cwd = Path(os.readlink(entry / "cwd"))
        except OSError:
            cwd = None
        marked = any(m in arg for arg in argv for m in TRIAL_MARKERS) or (
            claude is not None and argv[0] == claude)
        in_cell = cwd is not None and any(cwd == r or cwd.is_relative_to(r) for r in roots)
        by_env = _case_env(entry) in case_names
        if by_env or (marked and in_cell):
            found.append({"pid": int(entry.name), "argv": argv[:8],
                          "cwd": str(cwd) if cwd else None,
                          "owned_by": "solver_environment" if by_env else "cell_cwd_and_executable"})
    return found


def live_task_processes(case: dict, case_path: Path, *, settle_seconds: float = 60,
                        poll: float = 2.0, proc: Path = Path("/proc")) -> list[dict]:
    """Let an in-flight trial cleanup finish for a bounded time, then report.

    `run_replay` gives its detached child 15 s of TERM->KILL cleanup, so a scan
    taken at the instant of a CC-group kill would observe teardown rather than
    work. Waiting is the only action: nothing is signalled, and whatever is
    still alive afterwards blocks the closeout.
    """
    end = time.monotonic() + settle_seconds
    while True:
        found = scan_task_processes(case, case_path, proc=proc)
        if not found or time.monotonic() >= end:
            return found
        time.sleep(poll)


# ---- classification -------------------------------------------------------------

def classify(check: dict) -> dict:
    """May this not-ready check be cleared by report artifacts alone?

    Returns `{"eligible": bool, "reason_code", "reason", ...}`. Only an
    `incomplete` check with complete graded development, nothing unresolved, a
    selected tested bundle and nothing but report-class errors is eligible.
    """
    result = check.get("result") if isinstance(check, dict) else None
    if not isinstance(result, dict) or not isinstance(result.get("progress"), dict):
        return {"eligible": False, "reason_code": "invalid_protocol_check",
                "reason": "the protocol check returned no structured result"}
    progress = result["progress"]
    if result.get("ready") is True:
        return {"eligible": False, "reason_code": "already_ready",
                "reason": "the check is already ready; no recovery is needed"}
    latest = progress.get("latest_trial") or {}
    unresolved = {key: progress.get(key) for key in UNRESOLVED_KEYS if progress.get(key)}
    if latest.get("status") == "running":
        unresolved["latest_trial_running"] = latest.get("directory")
    if unresolved:
        return {"eligible": False, "reason_code": "unresolved_trials",
                "reason": "running, unresolved or infrastructure-error trial evidence "
                          "remains; development cannot be closed",
                "unresolved": unresolved}
    incomplete = {key: progress.get(key) for key in INCOMPLETE_KEYS
                  if progress.get(key) or not isinstance(progress.get(key), list)}
    if not progress.get("graded_executions"):
        incomplete["graded_executions"] = progress.get("graded_executions")
    if not progress.get("snapshot_complete"):
        incomplete["snapshot_complete"] = progress.get("snapshot_complete")
    if result.get("decision_type") != "incomplete":
        incomplete["decision_type"] = result.get("decision_type")
    if incomplete:
        return {"eligible": False, "reason_code": "incomplete_development",
                "reason": "graded development coverage is incomplete; report-only "
                          "recovery cannot replace missing executions",
                "incomplete": incomplete}
    selected = result.get("selected") or {}
    tested = progress.get("tested_bundles") or {}
    digest = selected.get("bundle_sha256")
    if not digest or not selected.get("reason", "").strip() or \
            not (tested.get(digest) or {}).get("trials"):
        return {"eligible": False, "reason_code": "no_selected_tested_bundle",
                "reason": "no recorded selection of an executed development bundle",
                "selected": selected or None}
    errors = result.get("errors")
    if not isinstance(errors, list) or not errors:
        return {"eligible": False, "reason_code": "invalid_protocol_check",
                "reason": "a not-ready check carries no errors"}
    other = [e for e in errors if not any(p.fullmatch(str(e)) for p in REPORT_ERRORS)]
    if other:
        return {"eligible": False, "reason_code": "non_report_errors",
                "reason": "errors outside report artifacts remain; report-only "
                          "recovery cannot clear them", "errors": other}
    return {"eligible": True, "reason_code": "report_gaps", "reason": "report-class gaps only",
            "report_gaps": errors, "selected_bundle_sha256": digest}


def frozen_identity(case: dict, check: dict) -> dict:
    """What a report-only recovery must leave byte-identical."""
    task = task_dir(case)
    ledger = json.loads((task / "development_state.json").read_text())
    progress = check["result"]["progress"]
    return {"selected_bundle_sha256": check["result"]["selected"]["bundle_sha256"],
            "selected_bundle": check["result"]["selected"].get("bundle"),
            "files": {name: sha256(task / name) for name in BUNDLE_FILES},
            "trial_rows": len(ledger.get("trials", [])),
            "trials_sha256": hashlib.sha256(json.dumps(
                ledger.get("trials", []), sort_keys=True).encode()).hexdigest(),
            "selected_sha256": hashlib.sha256(json.dumps(
                ledger.get("selected"), sort_keys=True).encode()).hexdigest(),
            "replay_invocations": progress.get("replay_invocations"),
            "retries_used_per_seed": progress.get("retries_used_per_seed")}


def identity_changes(before: dict, case: dict, check: dict) -> dict:
    """Differences between the frozen identity and the current one."""
    try:
        after = frozen_identity(case, check)
    except (OSError, KeyError, TypeError, ValueError) as exc:
        return {"unreadable": f"{type(exc).__name__}: {exc}"}
    return {key: {"frozen": before[key], "now": after.get(key)}
            for key in before if after.get(key) != before[key]}


# ---- report-only session ----------------------------------------------------------

def guard_hook(python: str, case_path: Path, audit: Path) -> list[str]:
    return [str(python), str(Path(__file__).resolve()), "--case", str(case_path),
            "--audit", str(audit)]


def report_only_settings(settings: dict, hook: list[str]) -> dict:
    """The run's settings plus a deny-by-default guard; nothing is removed."""
    closed = copy.deepcopy(settings)
    closed["hooks"]["PreToolUse"].append({
        "matcher": "*", "hooks": [{"type": "command", "timeout": 60,
                                   "command": shlex.join(hook)}]})
    return closed


def prompt(worker: str, case: dict, gaps: list[str], remaining_seconds: float) -> str:
    """Resume the SAME worker for report artifacts only."""
    task, copy_path = task_dir(case), working_copy(case)
    listed = "\n".join(f"- {gap}" for gap in gaps)
    return f"""Report-only closeout of this same cell. Development execution has ended:
the outer protocol check shows every graded development execution is recorded
and a tested bundle is selected, and only these report artifacts are missing or
invalid:
{listed}
Use SendMessage to resume the existing worker {worker} with this message:
"Development execution is closed. No trial, replay, diagnostic, shell or Python
command can run, and Bash is disabled. Do not modify fix_code.py,
fix_world_program.py, the selection or the ledger. Using only the native Read,
Write and Edit tools, complete exactly the missing artifacts from your existing
evidence: {task / 'findings.md'} (sections Root causes observed, What fixed them,
Generalizable patterns, Blocked seeds; write 'none' where applicable),
{task / 'task_analysis.md'}, and {copy_path}, which must be a byte-identical copy
of {task / 'fix_code.py'}. Then return."
Do not create another worker, run anything, or change any attempt. The outer
driver runs the original strict protocol check after you finish. About
{int(remaining_seconds // 60)} minutes of closeout wall-clock remain; it adds no
attempt.
"""


def report_only_denial(payload: dict, case: dict) -> str | None:
    """Deny everything except reads, worker messaging and report writes."""
    tool = payload.get("tool_name")
    if tool in READ_TOOLS:
        return None
    if tool == "Bash":
        return ("Report-only closeout: shell execution is disabled, so no trial, replay, "
                "diagnostic or inline program can run. Use native Read/Write/Edit for the "
                "report artifacts.")
    if tool in WRITE_TOOLS:
        value = (payload.get("tool_input") or {}).get("file_path")
        if value:
            target = (Path(payload.get("cwd") or case["sim"]) / str(value)).resolve()
            if target in {p.resolve() for p in report_paths(case)}:
                return None
        return ("Report-only closeout: only findings.md, task_analysis.md and the "
                "working_codes copy may be written; the selected bundle is frozen.")
    return (f"Report-only closeout: {tool} is unavailable. Resume the existing worker with "
            "SendMessage; no new agent or execution is allowed.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        case = json.loads(args.case.read_text())
        payload = json.load(sys.stdin)
        reason = report_only_denial(payload, case)
        with args.audit.open("a") as out:
            out.write(json.dumps({"tool": payload.get("tool_name"), "denied": bool(reason),
                                  "reason": reason}) + "\n")
    except Exception as exc:
        reason = f"Report-only closeout guard failed: {type(exc).__name__}: {exc}"
    if reason:
        print(reason, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
