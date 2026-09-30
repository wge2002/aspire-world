"""Bind NVIDIA EGL to the actual child and stop on recorded infrastructure faults.

This is orchestration only. It never alters a reward, attempt, or robot program.
"""
import argparse
import json
import os
from pathlib import Path
import signal
import sys
import threading


def bind_child_env(env, case):
    child = dict(env)
    child["__EGL_VENDOR_LIBRARY_FILENAMES"] = case["egl_vendor_config"]
    child["MUJOCO_EGL_DEVICE_ID"] = str(case["egl_device_id"])
    child["CUDA_VISIBLE_DEVICES"] = str(case["cuda_visible_devices"])
    return child


def fault(case):
    task = Path(case["sim"]) / "outputs/libero_fix_loop" / case["suite"] / case["task"]
    # Read only protocol ledgers, never traces or authored world judgments.
    ledgers = [task / "development_state.json",
               Path(case["control"]) / "heldout/heldout_state.json"]
    for ledger in ledgers:
        if not ledger.exists():
            continue
        data = json.loads(ledger.read_text())
        rows = data.get("trials", []) or list(data.get("seeds", {}).values())
        for row in rows:
            if row.get("status") == "infrastructure_error":
                return {"ledger": str(ledger), "seed": row.get("seed"),
                        "directory": row.get("directory"), "status": row["status"],
                        "kind": "charged_trial", "charged": row.get("charged", True)}
        # Uncharged screening blockers stop a trial before admission, so they
        # appear in no charged row. Resolved history is not a standing stop.
        for row in data.get("blockers", []):
            if not row.get("resolved"):
                return {"ledger": str(ledger), "seed": row.get("seed"),
                        "directory": row.get("resolved_by"),
                        "status": row.get("status", "blocked"),
                        "kind": "screening_blocker", "charged": row.get("charged", False),
                        "phase": row.get("phase"), "reason": row.get("reason")}
    return None


def monitor(process, case, audit):
    """Stop only the owned process group after evidence has been persisted."""
    def watch():
        while process.poll() is None:
            try:
                problem = fault(case)
            except (OSError, ValueError):
                problem = None  # The next read can observe an atomic replacement.
            if problem:
                Path(audit).write_text(json.dumps(problem, indent=2) + "\n")
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                return
            threading.Event().wait(2)
    threading.Thread(target=watch, daemon=True).start()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", type=Path, required=True)
    args = parser.parse_args()
    case = json.loads(args.case.read_text())
    payload = json.load(sys.stdin)
    command = payload.get("tool_input", {}).get("command", "")
    if payload.get("tool_name") == "Bash" and "native_world_protocol.py" in command and "trial" in command:
        try:
            problem = fault(case)
        except (OSError, ValueError) as exc:
            print(f"Cannot verify infrastructure ledger: {exc}", file=sys.stderr)
            return 2
        if problem:
            print(f"A recorded infrastructure failure ({problem['kind']}) must be resolved "
                  "before another trial. Preserve all charged attempts; inspect status "
                  "and report the blocker.", file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
