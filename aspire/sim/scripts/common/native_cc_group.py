#!/usr/bin/env python3
"""Run independent DLC cells without a caught cell failure killing its peers.

Each worker remains alive until every cell has recorded its terminal result.
Then the group fails if any cell failed. This cannot mask a node/pod failure
that the DLC platform itself terminates before the wrapper can run.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import time

from native_cc_runtime import atomic_json


def command_for(member, attempt_id, *, preflight=False):
    command = ["bash", member["entry"], "--case", member["case"], "--attempt-id", attempt_id]
    if preflight:
        return command + ["--preflight"]
    for key in ("resume_session", "resume_worker", "recover_trial"):
        if member.get(key):
            command.extend(["--" + key.replace("_", "-"), member[key]])
    return command


def run_member(config: dict, rank: int) -> int:
    members = config["members"]
    if not 0 <= rank < len(members):
        raise ValueError("unknown worker rank")
    root = Path(config["state_dir"])
    root.mkdir(parents=True, exist_ok=True)
    member = members[rank]
    name = member["id"]
    result_path = root / f"result-{rank}.json"
    claim = root / f"claim-{rank}.json"
    with claim.open("x") as out:
        json.dump(dict(rank=rank, case=name, host=os.uname().nodename, pid=os.getpid()), out)
        out.flush()
        os.fsync(out.fileno())
    deadline = time.monotonic() + config.get("timeout_seconds", 69000)
    interval = config.get("poll_seconds", 10)
    started = time.monotonic()
    process = None
    terminal = None

    def heartbeat():
        atomic_json(root / f"heartbeat-{rank}.json", dict(
            rank=rank, case=name, updated_at=time.time(), pid=os.getpid(),
            child_pid=process.pid if process else None,
            state="waiting_for_peers" if terminal else "running",
            case_result=terminal,
        ))

    def record_result(code, reason):
        nonlocal terminal
        terminal = dict(rank=rank, case=name, exit_code=code, reason=reason,
                        finished_at=time.time(), attempt_id=config["attempt_id"])
        atomic_json(result_path, terminal)
        print(json.dumps({"cell_finished": terminal, "action": "wait_for_peer_results"}), flush=True)

    def interrupted(signum, frame):
        raise InterruptedError(f"group worker received signal {signum}")

    handlers = {sig: signal.signal(sig, interrupted) for sig in (signal.SIGTERM, signal.SIGINT)}
    try:
        # These are operational cell locks, not a license to launch retries.
        with (Path(member["case"]).parent / "worker.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                process = subprocess.Popen(command_for(member, config["attempt_id"]),
                                           start_new_session=True)
            except (OSError, ValueError) as exc:
                record_result(1, f"launch_error: {exc}")
            heartbeat()
            while True:
                if terminal is None and process.poll() is not None:
                    record_result(process.returncode, "campaign_exited")
                heartbeat()
                results = []
                for peer in range(len(members)):
                    path = root / f"result-{peer}.json"
                    if path.is_file():
                        results.append(json.loads(path.read_text()))
                if len(results) == len(members):
                    failed = [r for r in results if r["exit_code"] != 0]
                    atomic_json(root / f"group-result-{rank}.json",
                                dict(complete=True, failed_cells=failed, results=results))
                    return int(bool(failed))
                if time.monotonic() >= deadline:
                    raise TimeoutError("group deadline exceeded before all cells finished")
                for peer in range(len(members)):
                    if any(r["rank"] == peer for r in results):
                        continue
                    path = root / f"heartbeat-{peer}.json"
                    if path.exists():
                        updated = json.loads(path.read_text())["updated_at"]
                        if time.time() - updated > config.get("stale_seconds", 300):
                            raise TimeoutError(f"worker {peer} heartbeat disappeared")
                    elif time.monotonic() - started > config.get("registration_timeout", 1800):
                        raise TimeoutError(f"worker {peer} did not register")
                time.sleep(interval)
    except (OSError, ValueError) as exc:
        if terminal is None:
            record_result(1, f"group_guard: {exc}")
        atomic_json(root / f"group-error-{rank}.json", dict(error=str(exc), time=time.time()))
        return 1
    finally:
        for sig, handler in handlers.items():
            signal.signal(sig, handler)
        if process is not None and process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=100)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--rank", type=int, required=True)
    parser.add_argument("--preflight", action="store_true")
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    if args.preflight:
        return subprocess.call(command_for(config["members"][args.rank], config["attempt_id"], preflight=True))
    return run_member(config, args.rank)


if __name__ == "__main__":
    raise SystemExit(main())
