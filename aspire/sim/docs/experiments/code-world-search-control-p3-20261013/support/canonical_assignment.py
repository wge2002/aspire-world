"""Deliver the frozen primary prompt through native PreToolUse.updatedInput.

Only the expected FIRST root general-purpose dispatch is normalized. Native
permission mode/tools/model fields are unchanged. Both proposal and effective
input are recorded; the existing exact-assignment guard validates the latter.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pilot_assignment_guard as guard

SENTINEL = "ASPIRE_FROZEN_PRIMARY_ASSIGNMENT"


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def normalize(payload, state, assignment):
    inp = payload.get("tool_input", {})
    first = (payload.get("tool_name") in guard.PRIMARY_TOOLS and not state.get("primary")
             and not guard.is_subagent(payload) and inp.get("subagent_type") == guard.PRIMARY_ROLE
             and not inp.get("resume"))
    if state.get("primary") and inp.get("prompt") == SENTINEL:
        raise guard.GuardError("primary already admitted; use SendMessage to that worker, never dispatch twice")
    effective = copy.deepcopy(payload)
    if first:
        if not isinstance(inp.get("prompt"), str):
            raise guard.GuardError("expected primary dispatch has no string prompt")
        effective["tool_input"]["prompt"] = assignment
    return effective, first


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", type=Path, required=True)
    parser.add_argument("--assignment", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    args = parser.parse_args()
    try:
        case = json.loads(args.case.read_text())
        if (case.get("prediction_contract") != "p3-search" or case.get("condition") != "C"
                or args.assignment.resolve() != (Path(case["control"]) / "worker-prompt.md").resolve()):
            raise guard.GuardError("canonical delivery is restricted to this fresh p3 cell's frozen primary assignment")
        payload = json.load(sys.stdin)
        assignment = args.assignment.read_text()
        effective, changed = normalize(payload, guard.load_state(args.state), assignment)
        if changed and not payload.get("tool_use_id"):
            raise guard.GuardError("native hook has no tool_use_id; cannot record exact delivered input")
        reason, state = guard.evaluate(effective, assignment_path=args.assignment,
                                       state_path=args.state, prompt_path=str(args.assignment))
        record = {"tool": payload.get("tool_name"), "tool_use_id": payload.get("tool_use_id"),
                  "session_id": payload.get("session_id"), "denied": bool(reason), "reason": reason,
                  "canonicalized_primary": changed, "primary_recorded": bool(state.get("primary")),
                  "proposed_input": payload.get("tool_input"), "effective_input": effective.get("tool_input"),
                  "proposal_sha256": digest(json.dumps(payload.get("tool_input"), sort_keys=True)),
                  "effective_sha256": digest(json.dumps(effective.get("tool_input"), sort_keys=True)),
                  "effective_prompt_sha256": digest(effective.get("tool_input", {}).get("prompt", "")),
                  "assignment_sha256": digest(assignment)}
        args.audit.parent.mkdir(parents=True, exist_ok=True)
        with args.audit.open("a") as out: out.write(json.dumps(record, ensure_ascii=False) + "\n")
        if reason:
            print(reason, file=sys.stderr); return 2
        if changed:
            print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                                       "updatedInput": effective["tool_input"]}}))
        return 0  # No permission override.
    except Exception as exc:
        print(f"Canonical assignment delivery failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
