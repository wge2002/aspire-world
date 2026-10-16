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


def classify(data, *, heldout=False):
    rows = data.get("trials", []) or list(data.get("seeds", {}).values())
    if heldout:
        hit = next((r for r in rows if r.get("status") == "infrastructure_error"), None)
        return dict(hit, kind="heldout_infrastructure") if hit else None
    for row in rows:
        if row.get("status") == "infrastructure_error" and row.get("phase") == "snapshot":
            return dict(row, kind="terminal_snapshot")
    for seed in range(51, 66):
        attempts = [r for r in rows if r.get("seed") == seed and r.get("spends_retry")]
        graded = any(r.get("status") in {"complete", "world_program_error"}
                     and r.get("phase") != "diagnostic" for r in attempts)
        if len(attempts) >= 3 and not graded and not any(r.get("status") == "running" for r in attempts):
            return {"kind": "terminal_missing_graded_evidence", "seed": seed,
                    "attempts_spent": len(attempts)}
    return None


def pending(data):
    return [r for r in data.get("trials", []) if r.get("status") == "infrastructure_error"
            and r.get("recovery", {}).get("disposition") != "closed_without_usable_evidence"]


def fault(case):
    task = Path(case["sim"]) / "outputs/libero_fix_loop" / case["suite"] / case["task"]
    for path, heldout in [(task / "development_state.json", False),
                          (Path(case["control"]) / "heldout/heldout_state.json", True)]:
        if path.exists():
            issue = classify(json.loads(path.read_text()), heldout=heldout)
            if issue:
                return dict(issue, ledger=str(path))
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
    from runtime_limits import trial_timeout_error
    timeout_error = trial_timeout_error(payload, case)
    if timeout_error:
        print(timeout_error, file=sys.stderr)
        return 2
    if payload.get("tool_name") == "Bash" and "native_world_protocol.py" in command and "trial" in command:
        try:
            problem = fault(case)
        except (OSError, ValueError) as exc:
            print(f"Cannot verify infrastructure ledger: {exc}", file=sys.stderr)
            return 2
        task = Path(case["sim"]) / "outputs/libero_fix_loop" / case["suite"] / case["task"]
        path = task / "development_state.json"
        unresolved = pending(json.loads(path.read_text())) if path.exists() else []
        if unresolved and not problem:
            print("Inspect preserved infra evidence and use close-infra for ended unusable rows before another trial; no retries are refunded.", file=sys.stderr)
            return 2
        if problem:
            print(f"A recorded infrastructure failure ({problem['kind']}) must be resolved "
                  "before another trial. Preserve all spent retries; inspect status "
                  "and report the blocker.", file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
