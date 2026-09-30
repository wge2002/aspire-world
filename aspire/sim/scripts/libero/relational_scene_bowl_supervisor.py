"""Run the frozen relational-scene revision on the same development seeds.

This is a 15-episode single-arm diagnostic, with no model calls or code tuning.
Attempted rows are never retried, overwritten, or removed from the denominator.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from collections import Counter
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paired_bowl_supervisor import artifacts, child_environment, process_identity, save, sha, stop_owned_tree

SIM = Path(__file__).resolve().parents[2]
REPLAY = SIM / "scripts/libero/replay_trial.py"
YAML = SIM / "env_configs/libero/franka_libero_traced.yaml"
SUITE, TASK, MODEL = "libero_goal_swap", "put_the_bowl_on_the_plate", "claude-opus-4-6"
SEEDS = list(range(51, 66))
TIMEOUT = 900
OPUS_MODE = "opus46-relational-scene-diagnostic"
REFERENCE_MODE = "cc-reference-relational-scene-diagnostic"


def campaign_mode(campaign):
    mode = json.loads((campaign / "frozen/scene_config.json").read_text()).get("mode")
    if mode not in (OPUS_MODE, REFERENCE_MODE):
        raise ValueError("unsupported relational campaign mode")
    return mode


def fingerprint(campaign):
    paths = {"policy": campaign / "frozen/shared_policy.py", "world": campaign / "frozen/world_program.py",
             "config": campaign / "frozen/scene_config.json", "yaml": YAML, "replay": REPLAY,
             "supervisor": Path(__file__), "base_supervisor": Path(__file__).with_name("paired_bowl_supervisor.py"),
             "scene_broker": SIM / "cap/world_model/scene_broker.py",
             "relational_broker": SIM / "cap/world_model/relational_scene_broker.py", "live_broker": SIM / "cap/world_model/live_broker.py",
             "numeric_worker": SIM / "cap/world_model/program.py", "numeric_runtime": SIM / "cap/world_model/runtime.py"}
    if campaign_mode(campaign) == REFERENCE_MODE:
        paths["reference_broker"] = SIM / "cap/world_model/reference_scene_broker.py"
    return {name: sha(path) for name, path in paths.items()}


def reconcile(manifest):
    rows = list(manifest["rows"].values())
    valid = [r for r in rows if r.get("artifacts", {}).get("valid")]
    successes = [r["seed"] for r in valid if r["artifacts"]["task_completed"]]
    comparisons = Counter()
    for row in valid:
        comparisons.update(row["artifacts"].get("comparison_counts", {}))
    return {"planned": len(SEEDS), "attempted": sum(r["attempted"] for r in rows),
            "valid_terminal_results": len(valid), "success_count": len(successes), "success_seeds": successes,
            "pending": [r["seed"] for r in rows if not r["attempted"]],
            "status_counts": dict(Counter(r["status"] for r in rows)), "comparison_counts": dict(comparisons),
            "reference_queries": sum(r["artifacts"].get("query_purpose_counts", {}).get("reference", 0) for r in valid),
            "relation_queries": sum(r["artifacts"].get("query_purpose_counts", {}).get("relation", 0) for r in valid),
            "query_used": sum(r["artifacts"].get("query_used", 0) for r in valid),
            "recoveries": sum(r["artifacts"].get("recovery_used", False) for r in valid),
            "model_requests_during_replay": 0, "held_out_trials": 0}


def run(campaign, environment, hashes):
    mode = campaign_mode(campaign)
    trials = campaign / "trials"
    path = trials / "scene_manifest.json"
    if path.exists():
        manifest = json.loads(path.read_text())
        if (manifest.get("frozen_hashes") != hashes or manifest.get("mode") != mode
                or set(manifest.get("rows", {})) != {str(s) for s in SEEDS}):
            raise ValueError("existing campaign identity differs")
        for row in manifest["rows"].values():
            if type(row.get("attempted")) is not bool or row["status"] == "pending" and row["attempted"]:
                raise ValueError("invalid existing attempt state")
            if row["status"] == "running":
                if not row["attempted"]:
                    raise ValueError("unregistered running attempt")
                if row.get("pid") and process_identity(row["pid"]) is not None:
                    raise ValueError("previous trial process may still be alive")
                for receipt_path in Path(row["directory"]).rglob("child_exit.json"):
                    receipt = json.loads(receipt_path.read_text())
                    if receipt.get("pid") and process_identity(receipt["pid"]) == receipt.get("process_start") and receipt.get("process_start") is not None:
                        raise ValueError("previous nested child is alive")
                row["status"] = "interrupted"
    else:
        if any(p.name != ".coordinator.lock" for p in trials.iterdir()):
            raise ValueError("new manifest would mix existing trial outputs")
        manifest = {"schema_version": 1, "mode": mode, "suite": SUITE, "task": TASK,
                    "model": MODEL, "seeds": SEEDS, "status": "running", "started_unix": time.time(),
                    "frozen_hashes": hashes, "timeout_seconds": TIMEOUT,
                    "comparison": "development revision; previous scene results are historical context only",
                    "rows": {str(s): {"seed": s, "arm": "scene", "status": "pending", "attempted": False} for s in SEEDS}}
        if mode == REFERENCE_MODE:
            config = json.loads((campaign / "frozen/scene_config.json").read_text())
            manifest["model_provenance"] = config["model_provenance"]
            manifest["comparison"] = "CC engineering reference development diagnostic; no paired baseline rerun"
    manifest["reconciliation"] = reconcile(manifest)
    save(path, manifest)
    consecutive_errors = 0
    for seed in SEEDS:
        row = manifest["rows"][str(seed)]
        if row["attempted"]:
            continue
        if fingerprint(campaign) != hashes:
            raise ValueError("frozen source changed during batch")
        directory = trials / "episodes" / f"seed_{seed}" / "scene"
        if directory.exists():
            raise ValueError("pending row already has files")
        cmd = [sys.executable, str(REPLAY), "--args.suite", SUITE, "--args.task", TASK,
               "--args.trial", str(seed), "--args.model", MODEL,
               "--args.replay-code", str(campaign / "frozen/shared_policy.py"), "--args.config", str(YAML),
               "--args.output-dir", str(directory / "replay"), "--args.record-video",
               "--args.world-model-config", str(directory / "scene_config.json")]
        row.update(status="running", attempted=True, command=cmd, directory=str(directory), started_unix=time.time())
        save(path, manifest)
        process, interrupted = None, False
        started = time.monotonic()
        try:
            directory.mkdir(parents=True, exist_ok=False)
            config = json.loads((campaign / "frozen/scene_config.json").read_text())
            config.update(world_program=str(campaign / "frozen/world_program.py"),
                          output_root=str(directory / "world_evidence"), run_name="scene-arm")
            with (directory / "scene_config.json").open("x") as f:
                json.dump(config, f, indent=2, allow_nan=False)
            with (directory / "runner.stdout").open("x") as out, (directory / "runner.stderr").open("x") as err:
                process = subprocess.Popen(cmd, cwd=SIM, env=environment, stdout=out, stderr=err, start_new_session=True)
                row.update(pid=process.pid, process_start=process_identity(process.pid))
                save(path, manifest)
                row["exit_code"] = process.wait(timeout=max(.01, TIMEOUT-(time.monotonic()-started)))
                row["status"] = "completed" if process.returncode == 0 else "nonzero"
        except subprocess.TimeoutExpired:
            row["status"] = "timeout"
        except (KeyboardInterrupt, SystemExit):
            interrupted = True
            row["status"] = "interrupted"
        except Exception as exc:
            row.update(status="launch_error", error=type(exc).__name__)
        finally:
            if process is not None and process.poll() is None:
                stop_owned_tree(process)
            row.update(elapsed_seconds=time.monotonic()-started, finished_unix=time.time())
            # Reuse terminal-code, trace, hash, reward and world-tape checks.
            row["artifacts"] = artifacts(directory, campaign, seed, "world")
            if row["status"] == "completed" and not row["artifacts"]["valid"]:
                row["status"] = "incomplete_evidence"
            if row["artifacts"].get("mechanism_status") not in (None, "complete"):
                row["status"] = "mechanism_failed"
            if row["artifacts"].get("live_manifest"):
                evidence = json.loads(Path(row["artifacts"]["live_manifest"]).read_text())
                # Recovery can raise before the policy emits its next log line.
                # The broker records the consumed opportunity before execution.
                for name in ["scene_anchor_known", "scene_anchor_objects", "scene_anchor_unknown", "recovery_used", "query_purpose_counts"]:
                    row["artifacts"][name] = evidence.get(name)
            manifest["reconciliation"] = reconcile(manifest)
            save(path, manifest)
        print(json.dumps({"seed": seed, "status": row["status"], "task_completed": row["artifacts"].get("task_completed"),
                          "elapsed_seconds": row["elapsed_seconds"], "queries": row["artifacts"].get("query_used")}), flush=True)
        consecutive_errors = consecutive_errors + 1 if row["status"] != "completed" else 0
        if interrupted or consecutive_errors >= 3:
            manifest["status"] = "interrupted" if interrupted else "systemic_failure"
            save(path, manifest)
            return 1
    manifest.update(status="complete" if all(r["status"] == "completed" for r in manifest["rows"].values()) else "partial",
                    finished_unix=time.time(), reconciliation=reconcile(manifest))
    save(path, manifest)
    print(json.dumps(manifest["reconciliation"]), flush=True)
    return 0 if manifest["status"] == "complete" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    campaign = args.campaign.resolve()
    hashes = fingerprint(campaign)
    sys.path.insert(0, str(SIM))
    from cap.world_model.relational_scene_broker import load_relational_config
    load_config = load_relational_config
    mode = campaign_mode(campaign)
    if mode == REFERENCE_MODE:
        from cap.world_model.reference_scene_broker import load_reference_config
        load_config = load_reference_config
    load_config(SimpleNamespace(api_key=None, replay_code=str(campaign / "frozen/shared_policy.py"),
        interactive=False, suite=SUITE, task=TASK, trial=51,
        world_model_config=str(campaign / "frozen/scene_config.json"), config=str(YAML),
        output_dir=str(campaign / "preflight_replay")))
    if args.dry_run:
        print(json.dumps({"kind": "plan_only_no_simulation", "mode": mode, "episodes": SEEDS, "count": 15,
                          "frozen_hashes": hashes, "model_requests": 0}, indent=2))
        return 0
    environment = child_environment()
    environment["__EGL_VENDOR_LIBRARY_FILENAMES"] = str(campaign / "control/nvidia-egl-vendor.json")
    if not Path(environment["__EGL_VENDOR_LIBRARY_FILENAMES"]).is_file():
        raise ValueError("campaign EGL vendor manifest missing")
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
