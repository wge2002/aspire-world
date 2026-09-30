#!/usr/bin/env python3
"""Coordinator accounting for the fixed, opt-in Opus bowl development pilot.

No generation or policy selection: run the same frozen program in both arms.
Each of the 30 scheduled rows is attempted at most once. A terminated attempt
is retained, and only untouched pending rows may resume. No simulator imports
occur in this coordinator; simulator work is delegated to replay_trial.py.
"""
from __future__ import annotations

import argparse
from collections import Counter
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time

SUITE = "libero_goal_swap"
TASK = "put_the_bowl_on_the_plate"
MODEL = "claude-opus-4-6"
SEEDS = list(range(51, 66))
TIMEOUT = 900
SIM = Path(__file__).resolve().parents[2]
REPLAY = SIM / "scripts/libero/replay_trial.py"
YAML = SIM / "env_configs/libero/franka_libero_traced.yaml"
MOTORS = {"goto_pose", "close_gripper", "open_gripper", "move_to_joints", "goto_home_joint_position"}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w") as f:
        json.dump(value, f, indent=2, allow_nan=False)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    temporary.replace(path)


def code_blocks(path):
    text = Path(path).read_text()
    parts = re.split(r"^# Code block \d+\s*\n", text, flags=re.MULTILINE)
    return [p.strip() for p in parts if p.strip()] or [text.strip()]


def plan():
    return [(seed, arm) for seed in SEEDS for arm in
            (("ordinary", "world") if seed % 2 else ("world", "ordinary"))]


def fingerprint(campaign):
    paths = {
        "policy": campaign / "frozen/shared_policy.py",
        "world": campaign / "frozen/world_program.py",
        "config": campaign / "frozen/live_config.json",
        "yaml": YAML, "replay": REPLAY, "supervisor": Path(__file__),
        "broker": SIM / "cap/world_model/live_broker.py",
        "numeric_worker": SIM / "cap/world_model/program.py",
        "numeric_runtime": SIM / "cap/world_model/runtime.py",
    }
    return {key: sha(path) for key, path in paths.items()}


def process_identity(pid):
    try:
        tail = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        if tail[0] == "Z":
            return None
        return tail[19]  # Linux field22: starttime, independent of command text.
    except (OSError, IndexError):
        return None


def stop_owned_tree(process):
    """Snapshot owned descendants before stopping a parent with detached children."""
    parent_map, identities = {}, {}
    for path in Path("/proc").glob("[0-9]*/stat"):
        try:
            pid = int(path.parent.name)
            tail = path.read_text().rsplit(")", 1)[1].split()
            parent_map[pid] = int(tail[1])
            identities[pid] = tail[19]
        except (OSError, ValueError, IndexError):
            continue
    owned = {process.pid}
    while True:
        expanded = owned | {pid for pid, parent in parent_map.items() if parent in owned}
        if expanded == owned:
            break
        owned = expanded
    for sig in (signal.SIGTERM, signal.SIGKILL):
        for pid in sorted(owned, reverse=True):
            if identities.get(pid) is not None and process_identity(pid) == identities[pid]:
                try:
                    os.kill(pid, sig)
                except ProcessLookupError:
                    pass
        if sig == signal.SIGTERM:
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


def child_environment():
    sys.path.insert(0, str(SIM))
    from cap.world_model.live_broker import _child_environment
    env = _child_environment(dict(os.environ))
    env.update(CUDA_VISIBLE_DEVICES="7", MUJOCO_EGL_DEVICE_ID="7", MUJOCO_GL="egl",
               ASPIRE_ROOT=str(SIM), PYTHON_ROOT=str(SIM.parents[1]),
               PYTHONPATH=os.pathsep.join((str(SIM.parents[1]), str(SIM))),
               TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD="1")
    for name, port in (("SAM3", 8114), ("GRASPNET", 8115), ("PYROKI", 8116)):
        env[f"{name}_SERVICE_URL"] = f"http://127.0.0.1:{port}"
    return env


def artifacts(directory, campaign, seed, arm):
    """Validate actual terminal artifacts; exit0 alone is never task evidence."""
    result = {"valid": False, "task_completed": None, "reward": None, "sandbox_rc": None}
    try:
        candidates = list(directory.rglob(f"trial_{seed:02d}_sandboxrc_*"))
        candidates = [p for p in candidates if p.is_dir()]
        if len(candidates) != 1:
            raise ValueError(f"terminal_trial_count:{len(candidates)}")
        trial = candidates[0]
        match = re.fullmatch(rf"trial_{seed:02d}_sandboxrc_(-?\d+)_reward_(-?\d+\.\d+)_taskcompleted_([01])", trial.name)
        if not match:
            raise ValueError("invalid_trial_name")
        rc, reward, success = int(match[1]), float(match[2]), bool(int(match[3]))
        if not math.isfinite(reward):
            raise ValueError("nonfinite_reward")
        if code_blocks(trial / "code.py") != code_blocks(campaign / "frozen/shared_policy.py"):
            raise ValueError("executed_policy_mismatch")
        summary = (trial / "summary.txt").read_text()
        if f"Trial {seed} — {SUITE}/{TASK}" not in summary:
            raise ValueError("summary_task_seed_mismatch")
        response = summary.split("\nEnvironment response:\n", 1)[1]
        if f"  Task Completed: {success}\n" not in response:
            raise ValueError("summary_success_mismatch")
        if f"  Sandbox failed: {rc}\n" not in response:
            raise ValueError("summary_sandbox_mismatch")
        summary_reward = re.search(r"^  Reward: (.+)$", response, re.MULTILINE)
        if not summary_reward or not math.isfinite(float(summary_reward[1])) or abs(float(summary_reward[1]) - reward) > .000501:
            raise ValueError("summary_reward_mismatch")
        trace = json.loads((trial / "trace.json").read_text())
        if not isinstance(trace, list):
            raise ValueError("invalid_trace")
        calls = Counter(e["function"] for e in trace)
        durations = {}
        for entry in trace:
            name = entry["function"]
            durations[name] = durations.get(name, 0.0) + float(entry.get("duration_ms", 0.0)) / 1000
        decisions = []
        for line in response.splitlines():
            candidate = line.strip()
            if candidate.startswith(("Stdout:", "Stderr:")):
                candidate = candidate.split(":", 1)[1].strip()
            try:
                entry = json.loads(candidate)
                if isinstance(entry, dict) and "policy_event" in entry:
                    decisions.append(entry)
            except ValueError:
                pass
        result.update(valid=True, task_completed=success, reward=reward, sandbox_rc=rc,
                      trial_dir=str(trial), policy_api_calls=dict(calls),
                      policy_api_seconds=durations, policy_motor_calls=sum(calls[n] for n in MOTORS),
                      policy_decisions=decisions,
                      recovery_used=any(e.get("recovery_used") is True for e in decisions))
        if arm == "world":
            live_paths = list(directory.rglob("live_manifest.json"))
            if len(live_paths) != 1:
                raise ValueError("live_manifest_count")
            live_dir = live_paths[0].parent
            mf = json.loads(live_paths[0].read_text())
            if (mf.get("suite"), mf.get("task"), mf.get("seed")) != (SUITE, TASK, seed):
                raise ValueError("live_identity_mismatch")
            for field, path in (("policy_sha256", "frozen_policy.py"),
                                ("world_program_sha256", "world_program.py"),
                                ("live_config_sha256", "live_config.json"),
                                ("yaml_sha256", "source_config.yaml"),
                                ("tape_sha256", "live_tape.jsonl")):
                if mf.get(field) != sha(live_dir / path):
                    raise ValueError(f"live_hash_mismatch:{field}")
            if mf["policy_sha256"] != sha(campaign / "frozen/shared_policy.py") or mf["world_program_sha256"] != sha(campaign / "frozen/world_program.py"):
                raise ValueError("live_frozen_source_mismatch")
            tr = mf.get("trial_result", {})
            if tr.get("task_completed") is not success or tr.get("sandbox_rc") != rc:
                raise ValueError("live_terminal_mismatch")
            tape = [json.loads(line) for line in (live_dir / "live_tape.jsonl").read_text().splitlines()]
            comparisons = [e for e in tape if e["event"] == "query_comparison"]
            result.update(mechanism_status=mf.get("status"), query_used=mf.get("query_used"),
                          broker_sensor_costs=mf.get("broker_sensor_costs"),
                          anchor_attempts=mf.get("anchor_attempts"),
                          comparison_counts=dict(Counter(e["comparison"]["status"] for e in comparisons)),
                          world_decisions=[e for e in tape if e["event"] in {"world_verify", "recovery_invoked"}],
                          live_manifest=str(live_paths[0]))
    except (OSError, ValueError, TypeError, KeyError, IndexError) as exc:
        result.update(valid=False, task_completed=None, artifact_error=str(exc))
    return result


def reconcile(manifest):
    rows = manifest["rows"]
    pairs = Counter()
    success = Counter()
    for seed in SEEDS:
        outcomes = []
        for arm in ("ordinary", "world"):
            a = rows[f"{seed}_{arm}"].get("artifacts", {})
            value = a.get("task_completed") if a.get("valid") else None
            outcomes.append(value)
            if value is True:
                success[arm] += 1
        ordinary, world = outcomes
        category = ("unknown" if None in outcomes else "both_success" if ordinary and world
                    else "ordinary_only" if ordinary else "world_only" if world else "both_failure")
        pairs[category] += 1
    return {"scheduled": 30, "attempted": sum(r["attempted"] for r in rows.values()),
            "pending": sum(not r["attempted"] for r in rows.values()),
            "statuses": dict(Counter(r["status"] for r in rows.values())),
            "success": {arm: success[arm] for arm in ("ordinary", "world")},
            "denominator_per_arm": 15,
            "pairs": {key: pairs[key] for key in ("world_only", "ordinary_only", "both_success", "both_failure", "unknown")}}


def run(campaign, environment, hashes):
    trials = campaign / "trials"
    path = trials / "paired_manifest.json"
    expected_keys = {f"{seed}_{arm}" for seed, arm in plan()}
    if path.exists():
        mf = json.loads(path.read_text())  # Corruption must abort, never reset.
        if mf.get("schema_version") != 3 or mf.get("frozen_hashes") != hashes or set(mf.get("rows", {})) != expected_keys:
            raise ValueError("existing campaign identity, plan or frozen hashes differ")
        for row in mf["rows"].values():
            if type(row.get("attempted")) is not bool or row.get("status") == "pending" and row["attempted"]:
                raise ValueError("invalid prior attempt state")
            if row["status"] == "running":
                if not row["attempted"]:
                    raise ValueError("running row was not registered")
                if row.get("pid"):
                    current = process_identity(row["pid"])
                    if current is not None and (row.get("process_start") is None or current == row["process_start"]):
                        raise ValueError("a previously launched trial is still alive")
                for receipt_path in Path(row.get("directory", trials / "absent")).rglob("child_exit.json"):
                    receipt = json.loads(receipt_path.read_text())
                    if receipt.get("pid") and receipt.get("process_start") is not None and process_identity(receipt["pid"]) == receipt["process_start"]:
                        raise ValueError("a previously launched nested trial is still alive")
                row["status"] = "interrupted"
    else:
        if any(p.name != ".coordinator.lock" for p in trials.iterdir()):
            raise ValueError("fresh manifest requested over existing artifacts")
        mf = {"schema_version": 3, "suite": SUITE, "task": TASK, "seeds": SEEDS,
              "status": "running", "frozen_hashes": hashes, "started_unix": time.time(),
              "timeout_seconds": TIMEOUT,
              "rows": {f"{seed}_{arm}": {"seed": seed, "arm": arm, "status": "pending", "attempted": False}
                       for seed, arm in plan()}}
    mf["reconciliation"] = reconcile(mf)
    save(path, mf)
    failures = 0
    for seed, arm in plan():
        row = mf["rows"][f"{seed}_{arm}"]
        if row["attempted"]:
            continue
        if fingerprint(campaign) != hashes:
            raise ValueError("frozen files changed during batch")
        directory = trials / "episodes" / f"seed_{seed}" / arm
        if directory.exists():
            raise ValueError("pending row already has artifacts; refusing overwrite")
        cmd = [sys.executable, str(REPLAY), "--args.suite", SUITE, "--args.task", TASK,
               "--args.trial", str(seed), "--args.model", MODEL,
               "--args.replay-code", str(campaign / "frozen/shared_policy.py"),
               "--args.config", str(YAML), "--args.output-dir", str(directory / "replay"), "--args.record-video"]
        if arm == "world":
            cmd += ["--args.world-model-config", str(directory / "world_config.json")]
        row.update(status="running", attempted=True, started_unix=time.time(), command=cmd, directory=str(directory))
        save(path, mf)  # Attempt registered before any per-episode side effects.
        process = None
        started = time.monotonic()
        interrupted = False
        try:
            directory.mkdir(parents=True, exist_ok=False)
            if arm == "world":
                config = json.loads((campaign / "frozen/live_config.json").read_text())
                config.update(world_program=str(campaign / "frozen/world_program.py"),
                              output_root=str(directory / "world_evidence"), run_name="world-arm")
                with (directory / "world_config.json").open("x") as f:
                    json.dump(config, f, indent=2, allow_nan=False)
            with (directory / "runner.stdout").open("x") as out, (directory / "runner.stderr").open("x") as err:
                process = subprocess.Popen(cmd, cwd=SIM, env=environment, stdout=out, stderr=err, start_new_session=True)
                row.update(pid=process.pid, process_start=process_identity(process.pid))
                save(path, mf)
                row["exit_code"] = process.wait(timeout=max(.01, TIMEOUT - (time.monotonic() - started)))
                row["status"] = "completed" if row["exit_code"] == 0 else "nonzero"
        except subprocess.TimeoutExpired:
            row["status"] = "timeout"
        except (KeyboardInterrupt, SystemExit):
            row["status"] = "interrupted"
            interrupted = True
        except Exception as exc:
            row.update(status="launch_error", error=type(exc).__name__)
        finally:
            if process is not None and process.poll() is None:
                stop_owned_tree(process)
            row.update(elapsed_seconds=time.monotonic() - started, finished_unix=time.time())
            row["artifacts"] = artifacts(directory, campaign, seed, arm)
            if row["status"] == "completed" and not row["artifacts"]["valid"]:
                row["status"] = "incomplete_evidence"
            if row["artifacts"].get("mechanism_status") not in (None, "complete"):
                row["status"] = "mechanism_failed"
            mf["reconciliation"] = reconcile(mf)
            save(path, mf)
        print(json.dumps({"seed": seed, "arm": arm, "status": row["status"],
                          "elapsed_seconds": row["elapsed_seconds"],
                          "task_completed": row["artifacts"].get("task_completed")}), flush=True)
        failures = failures + 1 if row["status"] != "completed" else 0
        if interrupted or failures >= 3:
            mf["status"] = "interrupted" if interrupted else "systemic_failure"
            save(path, mf)
            return 1
    mf["reconciliation"] = reconcile(mf)
    mf.update(status="complete" if all(r["status"] == "completed" for r in mf["rows"].values()) else "partial",
              finished_unix=time.time())
    save(path, mf)
    print(json.dumps(mf["reconciliation"]), flush=True)
    return 0 if mf["status"] == "complete" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    campaign = args.campaign.resolve()
    hashes = fingerprint(campaign)
    if args.dry_run:
        print(json.dumps({"kind": "plan_only_no_simulation", "episodes": plan(), "frozen_hashes": hashes}, indent=2))
        return 0
    environment = child_environment()
    from types import SimpleNamespace
    from cap.world_model.live_broker import load_live_config
    load_live_config(SimpleNamespace(api_key=None, replay_code=str(campaign / "frozen/shared_policy.py"),
                     interactive=False, suite=SUITE, task=TASK, trial=51,
                     world_model_config=str(campaign / "frozen/live_config.json"),
                     config=str(YAML), output_dir=str(campaign / "preflight_replay")))
    vendor = campaign.parents[1] / "control/nvidia-egl-vendor.json"
    if not vendor.is_file():
        raise ValueError("campaign EGL vendor file missing")
    environment["__EGL_VENDOR_LIBRARY_FILENAMES"] = str(vendor)
    trials = campaign / "trials"
    trials.mkdir(exist_ok=True)
    def terminate(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, terminate)
    with (trials / ".coordinator.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return run(campaign, environment, hashes)


if __name__ == "__main__":
    raise SystemExit(main())
