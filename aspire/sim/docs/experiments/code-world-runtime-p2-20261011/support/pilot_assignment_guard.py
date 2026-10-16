# SPDX-License-Identifier: MIT
"""PreToolUse gate: the primary assignment must be dispatched verbatim.

The coordinator is told to pass `worker-prompt.md` verbatim as the `prompt` of
its one `Agent` dispatch. A summarized or rewritten dispatch produces a worker
that was never given this cell's protocol, and the post-native lineage audit can
only discover that *after* the cell has already spent real simulator attempts.
So this hook refuses the rewrite before an agent is created, and keeps the
protocol trial path unreachable until a verbatim dispatch has been recorded:

- `Agent`/`Task`, before any primary is recorded: the call must be the intended
  general-purpose role with `prompt` byte-identical (modulo surrounding
  whitespace) to the frozen assignment. Anything else is denied.
- `Bash`, before any primary is recorded: a protocol *trial* call or a direct
  runner is denied, so no real attempt can execute ahead of the dispatch. The
  read-only protocol actions (`init`, `status`, `check`, `select`, `finalize`)
  stay available, and the common native guards still prohibit direct runners on
  their own.
- After a valid primary dispatch: ordinary workflow. Helper agents dispatched by
  the coordinator or by the worker are unaffected.

Identity is taken from the native hook payload conservatively: an *unknown*
initial identity is not a free pass, so a call that cannot be shown to come from
inside an existing subagent is held to the primary rule. Hook failure denies.

State is task-scoped and written atomically, so a resumed cell remembers that
its primary was already admitted. This is a reliability/protocol guard, not a
hardened sandbox; the post-native lineage audit (`support/lineage.py`) is still
what produces the final evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

PRIMARY_ROLE = "general-purpose"
PRIMARY_TOOLS = ("Agent", "Task")

#: The one protocol action that executes a simulator attempt.
TRIAL_ACTION = re.compile(r"native_world_protocol\.py\b[^|;&]*?(?<![\w-])trial(?![\w-])")

#: Direct runners. The common native guards already prohibit these; denying them
#: here too means "no real attempt before the dispatch" holds for one reason.
DIRECT_RUNNERS = re.compile(
    r"\b(?:replay_trial(?:_robosuite)?\.py|run_fix_loop_validation\.py|evaluate\.py"
    r"|native_world_heldout\.py|native_world_fixloop\.py)\b")

DENY_REWRITE = (
    "Primary assignment gate: this cell's first Agent dispatch must pass "
    "{prompt_path} VERBATIM as `prompt` with subagent_type='{role}'. "
    "{detail} No agent is created and no simulator attempt is reachable until an "
    "exact dispatch is recorded. Re-dispatch the frozen prompt unchanged.")
DENY_UNKNOWN = (
    "Primary assignment gate: this Agent/Task call cannot be attributed to an "
    "admitted primary dispatch, and an unknown initial identity is not accepted "
    "as one. Dispatch the frozen assignment in {prompt_path} verbatim first.")
DENY_TRIAL = (
    "Primary assignment gate: no simulator attempt may execute before the frozen "
    "assignment in {prompt_path} has been dispatched verbatim. Read-only protocol "
    "actions (status/check) remain available.")


class GuardError(RuntimeError):
    """The guard cannot evaluate this call; the caller denies."""


# ---- state --------------------------------------------------------------------

def load_state(path: Path) -> dict:
    path = Path(path)
    if not path.is_file():
        return {"version": 1, "primary": None, "denials": 0}
    state = json.loads(path.read_text())
    if state.get("version") != 1:
        raise GuardError(f"unreadable assignment-guard state version: {state.get('version')!r}")
    return state


def save_state(path: Path, state: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n")
    os.replace(temporary, path)


# ---- identity -----------------------------------------------------------------

_SUBAGENT_KEYS = ("parent_tool_use_id", "agent_id", "subagent_id", "agent_session_id")
_ROOT_AGENTS = {"", "root", "main", "coordinator", "primary"}


def is_subagent(payload: dict) -> bool:
    """True only when the payload positively shows a call from inside a subagent.

    Conservative on purpose: absence of evidence returns False, so an unattributed
    call is held to the primary rule instead of slipping through as a helper.
    """
    if any(payload.get(key) for key in _SUBAGENT_KEYS):
        return True
    agent = payload.get("agent_type") or payload.get("agent") or ""
    return str(agent).strip().lower() not in _ROOT_AGENTS


def session_identity(payload: dict) -> dict:
    """The metadata worth persisting about an admitted primary dispatch."""
    return {"session_id": payload.get("session_id"),
            "transcript_path": payload.get("transcript_path"),
            "cwd": payload.get("cwd"),
            "tool_name": payload.get("tool_name")}


# ---- classification -----------------------------------------------------------

def is_trial_command(command: str) -> bool:
    """A Bash call that would execute a real simulator attempt."""
    text = str(command or "")
    return bool(TRIAL_ACTION.search(text) or DIRECT_RUNNERS.search(text))


def dispatch_mismatch(tool_input: dict, assignment: str) -> str | None:
    """Why this dispatch is not the frozen primary, or None when it is exact."""
    role = tool_input.get("subagent_type")
    prompt = tool_input.get("prompt")
    if not isinstance(prompt, str):
        return "The call carries no `prompt` string."
    if role != PRIMARY_ROLE:
        return f"Its subagent_type is {role!r}, not {PRIMARY_ROLE!r}."
    if prompt.strip() != assignment.strip():
        return (f"Its prompt is {len(prompt.strip())} characters against the frozen "
                f"{len(assignment.strip())}; the dispatched text is not the frozen file.")
    return None


def denial(payload: dict, state: dict, assignment: str, prompt_path: str) -> str | None:
    """The denial reason for this PreToolUse payload, or None to allow it."""
    tool = payload.get("tool_name")
    if tool in PRIMARY_TOOLS:
        if state.get("primary"):
            return None  # ordinary workflow: helpers and follow-up dispatches
        if is_subagent(payload):
            return DENY_UNKNOWN.format(prompt_path=prompt_path)
        detail = dispatch_mismatch(payload.get("tool_input") or {}, assignment)
        if detail:
            return DENY_REWRITE.format(prompt_path=prompt_path, role=PRIMARY_ROLE,
                                       detail=detail)
        return None
    if tool == "Bash":
        if state.get("primary"):
            return None
        if is_trial_command((payload.get("tool_input") or {}).get("command", "")):
            return DENY_TRIAL.format(prompt_path=prompt_path)
    return None


def admits_primary(payload: dict, state: dict, assignment: str) -> bool:
    """True when this allowed call is the primary dispatch to record."""
    return (payload.get("tool_name") in PRIMARY_TOOLS
            and not state.get("primary")
            and not is_subagent(payload)
            and dispatch_mismatch(payload.get("tool_input") or {}, assignment) is None)


def record_primary(state: dict, payload: dict, assignment: str) -> dict:
    """Pin the admitted primary by assignment digest and session metadata."""
    state["primary"] = {
        "assignment_sha256": hashlib.sha256(assignment.strip().encode()).hexdigest(),
        "assignment_bytes": len(assignment.strip().encode()),
        "subagent_type": PRIMARY_ROLE,
        **session_identity(payload),
    }
    return state


# ---- hook ---------------------------------------------------------------------

def hook_command(python: str, guard: str, case: str, assignment: str,
                 state: str, audit: str | None = None) -> list[str]:
    """The argv the driver installs as this cell's PreToolUse command."""
    argv = [str(python), str(guard), "--case", str(case), "--assignment", str(assignment),
            "--state", str(state)]
    if audit:
        argv += ["--audit", str(audit)]
    return argv


def evaluate(payload: dict, *, assignment_path: Path, state_path: Path,
             prompt_path: str | None = None) -> tuple[str | None, dict]:
    """Decide one call and persist the resulting state. Returns (reason, state)."""
    assignment = Path(assignment_path).read_text()
    if not assignment.strip():
        raise GuardError(f"the frozen assignment is empty: {assignment_path}")
    state = load_state(state_path)
    reason = denial(payload, state, assignment, prompt_path or str(assignment_path))
    if reason:
        state["denials"] = int(state.get("denials", 0)) + 1
        save_state(state_path, state)
        return reason, state
    if admits_primary(payload, state, assignment):
        record_primary(state, payload, assignment)
        save_state(state_path, state)
    return None, state


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PreToolUse primary-assignment gate")
    parser.add_argument("--case", type=Path, required=True)
    parser.add_argument("--assignment", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--audit", type=Path)
    args = parser.parse_args(argv)
    payload: dict = {}
    try:
        case = json.loads(args.case.read_text())
        payload = json.load(sys.stdin)
        prompt_path = case.get("control") and f"{case['control']}/worker-prompt.md"
        reason, state = evaluate(payload, assignment_path=args.assignment,
                                 state_path=args.state,
                                 prompt_path=prompt_path or str(args.assignment))
        if args.audit:
            event = {"tool": payload.get("tool_name"), "denied": bool(reason),
                     "session_id": payload.get("session_id"),
                     "subagent_type": (payload.get("tool_input") or {}).get("subagent_type"),
                     "primary_recorded": bool(state.get("primary")), "reason": reason}
            args.audit.parent.mkdir(parents=True, exist_ok=True)
            with args.audit.open("a") as out:
                out.write(json.dumps(event, ensure_ascii=False) + "\n")
    except Exception as exc:
        # Fail closed: an unevaluable call is not an admitted one.
        reason = f"Primary assignment gate failed: {type(exc).__name__}: {exc}"
    if reason:
        print(reason, file=sys.stderr)
        return 2
    return 0  # success adds no permission override


if __name__ == "__main__":
    raise SystemExit(main())
