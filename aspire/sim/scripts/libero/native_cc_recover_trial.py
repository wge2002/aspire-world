#!/usr/bin/env python3
"""Complete one explicitly selected interrupted trial with its frozen program.

The original command, logs, code and results stay untouched. A separate
infrastructure attempt records the replay; no new development slot is created.
This command is used by the infrastructure coordinator before resuming CC.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys

from native_cc_protocol import identity, load_case, runtime_env, check_policy
from fix_loop_state import Stage1State, ProtocolError, code_hash

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "common"))
from native_cc_runtime import atomic_json
from native_cc_freeze import verify_runtime
from native_cc_trial_process import run_replay


def recover(case_path: Path, directory: str, attempt_dir: Path) -> dict:
    case, repo, task_dir = load_case(case_path)
    verify_runtime(case, repo)
    state = Stage1State(task_dir, identity(case, repo), resume=True)
    pending = [r for r in state.data["trials"] if r["status"] != "complete"]
    if len(pending) != 1 or pending[0]["directory"] != directory or pending[0] is not state.data["trials"][-1]:
        raise ProtocolError("recovery requires the one explicitly selected unfinished latest trial")
    record = pending[0]
    if record["phase"] not in {"smoke", "initial", "repair"}:
        raise ProtocolError("snapshot recovery requires explicit image provenance reconciliation")
    original = (task_dir / directory).resolve()
    if not original.is_relative_to(task_dir) or record["seed"] not in case["dev_seeds"]:
        raise ProtocolError("recovery must stay within this task's development partition")
    source = (original / "code.py").read_text()
    if code_hash(source) != record["code_sha256"]:
        raise ProtocolError("interrupted program changed; cannot recover")
    check_policy(source)
    if record.get("infrastructure_recoveries"):
        raise ProtocolError("this trial already used its one infrastructure recovery; inspect the blocker")
    lifecycle = original / "lifecycle.json"
    if lifecycle.exists():
        import socket
        saved = json.loads(lifecycle.read_text())
        proc = Path(f"/proc/{saved.get('pid')}/stat")
        if saved.get("host") == socket.gethostname() and proc.exists():
            if proc.read_text().rsplit(")", 1)[1].split()[19] == saved.get("proc_start_ticks"):
                raise ProtocolError("original replay process is still alive; do not duplicate it")
    # Existing final artifacts need manual provenance reconciliation, not an
    # automatic rerun that silently discards already-produced evidence.
    if any(p.is_dir() for p in (original / "results").rglob("trial_*")):
        raise ProtocolError("interrupted trial already has result artifacts; inspect them before replaying")
    recovery = original / "infrastructure-recovery" / attempt_dir.name
    command = [str(repo / ".venv-libero/bin/python3"), "scripts/libero/replay_trial.py",
               "--args.suite", case["suite"], "--args.task", case["task"],
               "--args.trial", str(record["seed"]), "--args.model", case["model"],
               "--args.replay-code", str(original / "code.py"),
               "--args.config", "env_configs/libero/franka_libero_traced.yaml",
               "--args.output-dir", str(original / "results")]
    if json.loads((original / "command.json").read_text()) != command:
        raise ProtocolError("recorded command differs from the frozen case and program")
    recovery.mkdir(parents=True, exist_ok=False)
    (recovery / "ledger-before.json").write_bytes(state.path.read_bytes())
    command[-1] = str(recovery / "results")
    atomic_json(recovery / "recovery.json", dict(reason=record.get("error") or "interrupted trial with no final artifacts",
                original_record=record.copy(), command=command, code_sha256=record["code_sha256"]))
    env = runtime_env(case, repo)
    env["SNAPSHOT_DIR"] = str(task_dir)
    exit_code, error = run_replay(command, repo=repo, env=env, directory=recovery,
                                  timeout=case.get("trial_timeout", 900))
    pattern = re.compile(rf"trial_{record['seed']:02d}_sandboxrc_(\d+)_reward_([\d.]+)_taskcompleted_(\d+)")
    matches = [(p, pattern.fullmatch(p.name)) for p in (recovery / "results").rglob("trial_*") if p.is_dir()]
    matches = [(p, match) for p, match in matches if match]
    result = None
    if len(matches) == 1:
        path, match = matches[0]
        result = dict(sandbox_rc=int(match[1]), reward=float(match[2]), task_completed=int(match[3]),
                      trial_dir=str(path.relative_to(task_dir)))
    record.setdefault("infrastructure_recoveries", []).append(str(recovery.relative_to(task_dir)))
    try:
        verify_runtime(case, repo)
    except RuntimeError as exc:
        error = str(exc)
    if error:
        result = None
    state.finish_trial(record, result=result, exit_code=exit_code, error=error)
    atomic_json(attempt_dir / "recovered-trial.json", record)
    if record["status"] != "complete":
        raise ProtocolError(f"recovery is still incomplete: {recovery}")
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", type=Path, required=True)
    parser.add_argument("--directory", required=True)
    parser.add_argument("--attempt-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(recover(args.case, args.directory, args.attempt_dir), indent=2))


if __name__ == "__main__":
    main()
