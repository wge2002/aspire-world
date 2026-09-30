#!/usr/bin/env python3
"""Held-out evaluation of one frozen native cell over seeds 1-50.

The outer coordinator runs this. No solver process may invoke it and no
held-out outcome is ever returned to a solver: the selected bundle, world
program, inventory and runtime are immutable for the whole sweep, every seed
is attempted, and failures stay visible in the report.

Conditions B and C without a code-world profile carry the uncapped world
adapter, whose results land under output_root/run_name/seed_N/replay rather
than --args.output-dir. The in-process `simple` and `judgment` profiles run in
the ordinary replay, so their results stay under --args.output-dir; for those,
the frozen bundle is the policy and its matching world program, with no
inventory. Judgment additionally admits no observation fallback: its selected
pair must have executed in development.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "common"))
from native_cc_freeze import RuntimeChanged, verify_runtime
from native_cc_trial_process import run_replay
from native_cc_runtime import atomic_json
from native_world_fixloop_state import ProtocolError, bundle_identity, code_hash, minimal_fallback
from native_world_protocol import (CLEANUP_MARGIN_SECONDS, WORLD_CONDITIONS, adapter_world,
                                   in_process_world, is_judgment, load_case, parse_result,
                                   runtime_env, write_world_config, world_failure)

HELDOUT_SEEDS = tuple(range(1, 51))


def frozen_bundle(case: dict, task_dir: Path, evaluation: Path) -> tuple[dict, dict]:
    """Copy the selected files once, then evaluate every seed from those copies."""
    stage1 = json.loads((task_dir / "stage1_result.json").read_text())
    if not stage1.get("stage1_complete"):
        raise ProtocolError("stage 1 is not complete; finalize the development ledger first")
    frozen = evaluation / "frozen"
    frozen.mkdir(parents=True, exist_ok=True)
    sources = {"policy": task_dir / "fix_code.py"}
    if case["condition"] in WORLD_CONDITIONS:
        # Policy and world are frozen and re-verified as one pair for every seed;
        # only the legacy adapter profile adds an inventory.
        sources["world"] = task_dir / "fix_world_program.py"
        if not in_process_world(case):
            sources["inventory"] = task_dir / "fix_inventory.json"
    bundle, kept = {}, {}
    for key, source in sources.items():
        destination = frozen / source.name
        if not destination.is_file():
            destination.write_bytes(source.read_bytes())
        bundle[key] = code_hash(destination.read_text())
        kept[key] = destination
    if bundle != stage1["selected_bundle"]:
        raise ProtocolError("frozen files do not match the selected development bundle")
    # finalize writes tested_bundles as a digest-keyed mapping, not a list.
    tested = stage1["tested_bundles"]
    if not isinstance(tested, dict):
        raise ProtocolError("stage1_result.json tested_bundles must be a digest-keyed mapping")
    digest = bundle_identity(bundle)
    # The judgment profile has no observation fallback: its selected policy+world
    # pair must itself have executed in development. Refuse the bypass here even
    # if an upstream flag claims otherwise, rather than trusting the flag.
    fallback = (not is_judgment(case) and stage1.get("observation_fallback")
                and minimal_fallback(kept["policy"].read_text()))
    if digest not in tested and not fallback:
        raise ProtocolError("the selected bundle was never tested in development")
    if stage1.get("selection", {}).get("bundle_sha256") not in (None, digest):
        raise ProtocolError("the recorded selection does not match the frozen bundle")
    return bundle, kept


def assert_frozen(bundle: dict, kept: dict, seed: int) -> None:
    """Re-verify the frozen digests before every seed, not only once.

    A single check at the start cannot notice a mid-sweep edit; each seed of a
    held-out evaluation must demonstrably run the same bundle.
    """
    for key, path in kept.items():
        if code_hash(path.read_text()) != bundle[key]:
            raise ProtocolError(
                f"frozen {key} changed before seed {seed}; the held-out bundle is immutable")


def evaluate_seed(case: dict, repo: Path, evaluation: Path, bundle: dict, kept: dict,
                  seed: int) -> dict:
    verify_runtime(case, repo)
    assert_frozen(bundle, kept, seed)
    directory = evaluation / f"seed_{seed:02d}"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "code.py").write_bytes(kept["policy"].read_bytes())
    env = runtime_env(case, repo)
    env["ASPIRE_MAX_STEPS"] = str(case["max_steps"])
    command = [str(repo / ".venv-libero/bin/python3"), "scripts/libero/replay_trial.py",
               "--args.suite", case["suite"], "--args.task", case["task"],
               "--args.trial", str(seed), "--args.model", case["model"],
               "--args.config", case["env_config"],
               "--args.output-dir", str(directory / "results"),
               "--args.replay-code", str(directory / "code.py")]
    results = directory / "results"
    if case["condition"] in WORLD_CONDITIONS:
        sources = {"policy": directory / "code.py", "world": kept["world"]}
        if not in_process_world(case):
            sources["inventory"] = kept["inventory"]
        command += ["--args.world-model-config",
                    str(write_world_config(case, directory, sources))]
        if adapter_world(case, "heldout"):
            results = directory / "world/native_world" / f"seed_{seed}" / "replay"
    (directory / "command.json").write_text(json.dumps(
        {"command": command, "result_root": str(results)}, indent=2))
    grace = CLEANUP_MARGIN_SECONDS if adapter_world(case, "heldout") else 0
    exit_code, error = run_replay(command, repo=repo, env=env, directory=directory,
                                  timeout=case["trial_timeout"] + grace)
    result = parse_result(results, seed)
    try:
        verify_runtime(case, repo)
    except RuntimeChanged as exc:
        error, result = str(exc), None
    record = {"seed": seed, "directory": str(directory), "exit_code": exit_code,
              "error": error, "result": result,
              "finished_at": datetime.now(timezone.utc).isoformat()}
    world_error = world_failure(case, directory, "heldout", seed) if not error and exit_code not in (None, 0) else None
    if world_error:
        record.update(status="crash", world_error=world_error)
    elif error or exit_code != 0 or result is None:
        record["status"] = "infrastructure_error"
    elif result["sandbox_rc"]:
        record["status"] = "crash"
    else:
        record["status"] = "success" if result["task_completed"] else "failure"
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", type=Path, default=os.environ.get("ASPIRE_NATIVE_CASE"),
                        required=False)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    if not args.case:
        parser.error("--case or ASPIRE_NATIVE_CASE is required")
    case, repo, task_dir = load_case(Path(args.case))
    evaluation = Path(case["control"]) / "heldout"
    evaluation.mkdir(parents=True, exist_ok=True)
    ledger = evaluation / "heldout_state.json"
    bundle, kept = frozen_bundle(case, task_dir, evaluation)
    identity = {"cell": case["id"], "condition": case["condition"], "suite": case["suite"],
                "task": case["task"], "seeds": list(HELDOUT_SEEDS),
                **({"profile": case["profile"]} if "profile" in case else {}),
                "bundle": bundle, "bundle_sha256": bundle_identity(bundle),
                "config_sha256": code_hash((repo / case["env_config"]).read_text()),
                "max_steps": case["max_steps"], "trial_timeout": case["trial_timeout"]}
    if ledger.is_file():
        data = json.loads(ledger.read_text())
        if data["identity"] != identity:
            raise SystemExit("held-out identity changed; a resume must reuse the frozen inputs")
        if not args.resume:
            raise SystemExit("held-out state exists; pass --resume to continue it")
    else:
        data = {"identity": identity, "seeds": {}}
    for seed in HELDOUT_SEEDS:
        if str(seed) in data["seeds"]:
            continue  # Already recorded: never re-run and never overwrite its artifacts.
        directory = evaluation / f"seed_{seed:02d}"
        if directory.exists():
            # An interrupted seed left files behind but no ledger row. Replaying
            # invisibly over them would destroy the only evidence of what ran.
            raise SystemExit(
                f"seed {seed} has artifacts at {directory} but no recorded result; "
                "inspect and move that directory before continuing the sweep")
        data["seeds"][str(seed)] = evaluate_seed(case, repo, evaluation, bundle, kept, seed)
        atomic_json(ledger, data)  # Crash-safe per seed.
    records = [data["seeds"][str(seed)] for seed in HELDOUT_SEEDS]
    counts = {status: sum(r["status"] == status for r in records)
              for status in ("success", "failure", "crash", "infrastructure_error")}
    report = {"schema_version": 1, "cell": case["id"], "condition": case["condition"],
              "identity": identity, "seeds_evaluated": len(records),
              "all_seeds_accounted": len(records) == len(HELDOUT_SEEDS),
              "counts": counts,
              "success_rate": counts["success"] / len(HELDOUT_SEEDS),
              # Failures, crashes and blockers stay visible next to the rate.
              "per_seed": {str(r["seed"]): r["status"] for r in records},
              "unusable_seeds": [r["seed"] for r in records
                                 if r["status"] == "infrastructure_error"]}
    (evaluation / "heldout_result.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return int(bool(report["unusable_seeds"]) or not report["all_seeds_accounted"])


if __name__ == "__main__":
    raise SystemExit(main())
