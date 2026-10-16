"""Fresh p2/p3 study protocol entry; close lost infra evidence without refunding it.

The ordinary protocol and ledger remain authoritative. An ended infrastructure
attempt may be explicitly closed as unusable; its original status and all spent
attempts survive. It never becomes graded evidence. A later actual execution is
needed for coverage, and exhausted missing coverage remains terminal.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import native_world_protocol as protocol
from native_world_fixloop_state import NativeWorldState, ProtocolError

DISPOSITION = "closed_without_usable_evidence"
original_progress = NativeWorldState.progress
original_replay = protocol.run_replay


def run_replay(command, **kwargs):
    command = ["scripts/libero/runtime/replay_trial.py" if item == "scripts/libero/replay_trial.py"
               else item for item in command]
    kwargs["env"] = dict(kwargs["env"], ASPIRE_RUNTIME_EVIDENCE_DIR=str(kwargs["directory"]))
    return original_replay(command, **kwargs)


def closed(row):
    return (row.get("status") == "infrastructure_error"
            and row.get("recovery", {}).get("disposition") == DISPOSITION)


def progress(self):
    result = original_progress(self)
    closed_rows = [r for r in self.data["trials"] if closed(r)]
    directories = {r["directory"] for r in closed_rows}
    result["infrastructure_errors"] = [p for p in result["infrastructure_errors"] if p not in directories]
    result["closed_infrastructure_attempts"] = [
        {"seed": r["seed"], "phase": r["phase"], "directory": r["directory"],
         "spends_retry": r["spends_retry"], "status": r["status"], "recovery": r["recovery"]}
        for r in closed_rows]
    return result


def close_attempt(state, directory, reason):
    if not reason.strip():
        raise ProtocolError("a concrete infrastructure disposition reason is required")
    rows = [r for r in state.data["trials"] if r["directory"] == str(directory)]
    if len(rows) != 1 or rows[0]["status"] != "infrastructure_error":
        raise ProtocolError("close only one ended infrastructure_error row; running attempts need outer recovery")
    row = rows[0]
    if row["phase"] == "snapshot":
        raise ProtocolError("a lost single snapshot cannot be replaced or counted complete; report terminal cell")
    if protocol.parse_result(state.task_dir / directory / "results", row["seed"]) is not None:
        raise ProtocolError("usable-looking trial evidence exists; outer recovery must inspect it, do not discard it")
    if closed(row):
        return row  # Idempotent; never create a second attempt.
    # Preserve status, inputs and spent slot. No fabricated reward/sandbox_rc.
    state.resolve_interrupted(row, result=None, exit_code=row.get("process_exit_code"),
                              error=row.get("error", ""),
                              recovery={"bundle_sha256": row["bundle_sha256"],
                                        "disposition": DISPOSITION, "reason": reason,
                                        "executions_added": 0, "attempts_refunded": 0})
    return row


def main():
    case_path = Path(os.environ.get("ASPIRE_NATIVE_CASE", ""))
    if "--case" in sys.argv:
        case_path = Path(sys.argv[sys.argv.index("--case") + 1])
    case, repo, task = protocol.load_case(case_path)
    if case.get("prediction_contract") not in protocol.RUNTIME_CONTRACTS:
        raise ProtocolError("the fresh runtime entry is restricted to the runtime contracts "
                            f"{protocol.RUNTIME_CONTRACTS}")
    NativeWorldState.progress = progress
    protocol.run_replay = run_replay
    if "close-infra" not in sys.argv:
        return protocol.main()
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", type=Path, default=case_path)
    parser.add_argument("action", choices=["close-infra"])
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--reason", required=True)
    args = parser.parse_args()
    protocol.verify_runtime(case, repo)
    state = NativeWorldState(task, protocol.identity(case, repo), resume=True)
    with (task / ".trial.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        row = close_attempt(state, args.directory, args.reason)
    print(json.dumps({"closed": row, "progress": state.progress()}, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ProtocolError, OSError) as exc:
        print(json.dumps({"error": str(exc)}))
        raise SystemExit(2)
