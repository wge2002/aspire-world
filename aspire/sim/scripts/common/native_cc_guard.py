"""Native PreToolUse guard for frozen framework files and serial replay calls.

Allowed calls return no permission override. Native permissions still apply.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import sys
from pathlib import Path

from native_cc_freeze import verify_runtime


def settings(case_path: Path, repo: Path) -> dict:
    command = shlex.join([str(repo / ".venv-libero/bin/python3"),
                         str(repo / "scripts/common/native_cc_guard.py"), "--case", str(case_path.resolve())])
    return {"hooks": {"PreToolUse": [{"matcher": "Write|Edit|MultiEdit|Bash",
                                      "hooks": [{"type": "command", "command": command, "timeout": 60}]}]}}


AUTHORED_FILES = ("initial_code.py", "fix_code.py", "findings.md", "task_analysis.md",
                  "notes.md", "BLOCKED.md")
WORLD_FILES = ("initial_world_program.py", "initial_inventory.json",
               "fix_world_program.py", "fix_inventory.json", "diagnostic_session.py")


def denial(payload: dict, case: dict, repo: Path) -> str | None:
    name, values = payload.get("tool_name"), payload.get("tool_input", {})
    if name in {"Write", "Edit", "MultiEdit"}:
        path = (Path(payload.get("cwd", str(repo))) / values.get("file_path", "")).resolve()
        # `outputs` may be a symlink into the result parent, so both the allowed
        # task paths and the tool's own path are resolved before comparison.
        # Comparing against an unresolved task path rejects legitimate
        # initial_code/report writes.
        task = (repo / "outputs/libero_fix_loop" / case["suite"] / case["task"]).resolve()
        names = AUTHORED_FILES + (WORLD_FILES if case.get("condition") in {"B", "C"} else ())
        allowed = (path.is_relative_to((repo / case["skill_library_dir"]).resolve()) or
                   path.is_relative_to(task / "attempts") or
                   path.is_relative_to(task / "development") or
                   path in {(task / n).resolve() for n in names} or
                   path == (repo / "outputs/working_codes" /
                            f"{case['suite']}_{case['task']}_fix.py").resolve())
        if not allowed:
            return "Frozen campaign: write only this task's programs, reports, notes and skill library. Framework, configuration and recorded evidence are immutable."
    if name == "Bash":
        command = values.get("command", "")
        for protocol in ("native_cc_protocol.py", "native_world_protocol.py"):
            if protocol in command and re.search(r"\btrial\b", command):
                if command.count(protocol) != 1 or re.search(r"\b(for|while|until|xargs|parallel|nohup)\b", command):
                    return "Run ONE recorded trial per Bash call, then wait for that call to finish. Shell loops over seeds can be killed at the native background-task ceiling."
        if re.search(r"\b(?:replay_trial|run_fix_loop_validation|native_cc_recover_trial|native_world_heldout)\.py\b", command):
            return "Use the recorded protocol `trial` command; recovery and held-out evaluation belong to the outer coordinator."
        for statement in re.split(r"[;\n]|&&|\|\|", command):
            if re.search(r"\b(?:sed\s+-i|chmod|chown|rm|mv|cp|tee)\b", statement) and re.search(r"(?:cap/|scripts/|env_configs/|\.claude/)", statement):
                return "Do not mutate the frozen runtime with Bash; report an infrastructure blocker."
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", type=Path, required=True)
    args = parser.parse_args()
    try:
        case = json.loads(args.case.read_text())
        repo = Path(case["sim"]).resolve()
        verify_runtime(case, repo)
        payload = json.load(sys.stdin)
        reason = denial(payload, case, repo)
        event = {"tool": payload.get("tool_name"), "path": payload.get("tool_input", {}).get("file_path"),
                 "denied": bool(reason), "reason": reason}
        with (Path(case["control"]) / "guard-audit.jsonl").open("a") as log:
            log.write(json.dumps(event) + "\n")
    except Exception as exc:
        reason = f"Frozen-runtime guard failed: {type(exc).__name__}: {exc}"
    if reason:
        print(reason, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
