"""Deliver the frozen primary prompt through native PreToolUse.updatedInput.

Only the expected FIRST root general-purpose dispatch is normalized. Native
permission mode/tools/model fields are unchanged. Both proposal and effective
input are recorded; the existing exact-assignment guard validates the latter.

p6b copy (CC_TASK_9, 2026-10-21) of the p3 hook; the p3 original stays frozen.
One addition: a later root general-purpose dispatch of the sentinel is accepted
exactly once per outer-driver recovery round. The driver opens the round by
writing `recovery_request` (status "open", generation N, the path and digest of
the generation-N assignment it wrote: the frozen assignment followed by its
handoff) into this hook's state file, which no tool of the cell can write. The
hook delivers those bytes verbatim, records the admission under `generations`
and closes the request. Without an open request a second sentinel dispatch is
refused as before.
"""
import argparse
import copy
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pilot_assignment_guard as guard

SENTINEL = "ASPIRE_FROZEN_PRIMARY_ASSIGNMENT"


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def recovery_assignment(payload, state, assignment):
    """The generation-N assignment for a sentinel dispatch after the primary, or a refusal."""
    inp = payload.get("tool_input", {})
    request = state.get("recovery_request") or {}
    if request.get("status") != "open":
        raise guard.GuardError("primary already admitted; use SendMessage to that worker, never dispatch "
                               "twice (a further primary is accepted only in an outer-driver recovery round)")
    if (guard.is_subagent(payload) or payload.get("tool_name") not in guard.PRIMARY_TOOLS
            or inp.get("subagent_type") != guard.PRIMARY_ROLE or inp.get("resume")):
        raise guard.GuardError("a recovery dispatch must be the coordinator's own general-purpose Agent call")
    text = Path(request["assignment"]).read_text()
    if digest(text) != request.get("assignment_sha256") or not text.startswith(assignment):
        raise guard.GuardError("the recovery assignment is not the driver's frozen assignment plus handoff")
    return text


def normalize(payload, state, assignment):
    inp = payload.get("tool_input", {})
    first = (payload.get("tool_name") in guard.PRIMARY_TOOLS and not state.get("primary")
             and not guard.is_subagent(payload) and inp.get("subagent_type") == guard.PRIMARY_ROLE
             and not inp.get("resume"))
    recovery = None
    if state.get("primary") and inp.get("prompt") == SENTINEL:
        recovery = recovery_assignment(payload, state, assignment)
    effective = copy.deepcopy(payload)
    if first:
        if not isinstance(inp.get("prompt"), str):
            raise guard.GuardError("expected primary dispatch has no string prompt")
        effective["tool_input"]["prompt"] = assignment
    if recovery is not None:
        effective["tool_input"]["prompt"] = recovery
    return effective, first or recovery is not None, recovery is not None


def admit_generation(state_path, payload, effective):
    state = guard.load_state(state_path)
    request = state["recovery_request"]
    entry = {"generation": request["generation"], "tool_use_id": payload.get("tool_use_id"),
             "session_id": payload.get("session_id"), "assignment": request["assignment"],
             "assignment_sha256": digest(effective["tool_input"]["prompt"]),
             "admitted_at": datetime.now(timezone.utc).isoformat()}
    request.update(status="admitted", admitted=entry)
    state.setdefault("generations", []).append(entry)
    guard.save_state(state_path, state)
    return state


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
        effective, changed, generation = normalize(payload, guard.load_state(args.state), assignment)
        if changed and not payload.get("tool_use_id"):
            raise guard.GuardError("native hook has no tool_use_id; cannot record exact delivered input")
        reason, state = guard.evaluate(effective, assignment_path=args.assignment,
                                       state_path=args.state, prompt_path=str(args.assignment))
        if generation and not reason:
            state = admit_generation(args.state, payload, effective)
        record = {"tool": payload.get("tool_name"), "tool_use_id": payload.get("tool_use_id"),
                  "session_id": payload.get("session_id"), "denied": bool(reason), "reason": reason,
                  "canonicalized_primary": changed, "primary_recorded": bool(state.get("primary")),
                  "recovery_generation": state["recovery_request"]["generation"] if generation else None,
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
