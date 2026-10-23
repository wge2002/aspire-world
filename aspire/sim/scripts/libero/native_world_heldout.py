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

CC_TASK_7: a case may set `heldout_parallel` (N, default 1). N = 1 is the serial
loop below, unchanged. N > 1 keeps up to N seeds running at once from one
`ReplayBatch`: every seed is still its own replay process in its own session
and directory with the same timeout and the same frozen-pair re-verification;
this process stays the only writer of `heldout_state.json` and writes it
atomically as each seed finishes, rows ordered by seed. Identity, `--resume`,
the refusal of a seed directory without a row, the report and its per-seed
order are the serial ones, and N is not part of the identity: a sweep may be
resumed at another N. Two differences, both stricter: every pending seed's
directory is checked before anything starts, and once a ledger write raises
(e.g. `heldout_stop_on_infra` stopping after an infrastructure row), a seed
fails to prepare, or the batch is signalled, no further seed starts — the
seeds already running finish (a signal ends them) and are recorded first, then
the sweep stops with that first error.

CC_TASK_14 (p6d, only for a case with `method_switches`):
  * `heldout_watchdog: settle` judges a watchdog kill from the seed's own evidence
    exactly as development does (`watchdog_verdict`): a policy still issuing API
    calls when its trial budget ran out is a failed seed (`failure`, the row keeps
    the verdict), a replay killed while saving an outcome it had already recorded is
    that outcome; only a stalled / unjudgeable replay stays `infrastructure_error`.
  * `--seed-set unseen` runs the frozen bundle on the case's `unseen_seeds` (66-80)
    into `<control>/heldout/unseen/` (`unseen_state.json`, `unseen_result.json`):
    the same frozen pair, preparation, settlement and report shape as held-out. The
    outer driver runs it after the freeze and before held-out; no solver sees it.
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
from native_cc_trial_process import ReplayBatch, parallel_env, run_replay
from native_cc_runtime import atomic_json
from native_world_fixloop_state import ProtocolError, bundle_identity, code_hash, minimal_fallback
from native_world_protocol import (CLEANUP_MARGIN_SECONDS, WORLD_CONDITIONS, adapter_world,
                                   in_process_world, is_judgment, load_case, parallel_settings,
                                   parse_result, runtime_env, watchdog_verdict, write_world_config,
                                   world_failure)
import method_switches

HELDOUT_SEEDS = tuple(range(1, 51))
#: CC_TASK_14: seed set name -> (evaluation subdirectory, ledger, report) under <control>/heldout.
SEED_SETS = {"heldout": ("", "heldout_state.json", "heldout_result.json"),
             "unseen": ("unseen", "unseen_state.json", "unseen_result.json")}


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
    launch = prepare_seed(case, repo, evaluation, bundle, kept, seed)
    exit_code, error = run_replay(launch["command"], repo=repo, env=launch["env"],
                                  directory=launch["directory"], timeout=launch["timeout"])
    return settle_seed(case, repo, launch, exit_code, error)


def prepare_seed(case: dict, repo: Path, evaluation: Path, bundle: dict, kept: dict,
                 seed: int) -> dict:
    """Everything before the replay starts: verification, directory, command."""
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
    return {"seed": seed, "directory": directory, "results": results, "command": command,
            "env": env, "timeout": case["trial_timeout"] + grace}


def settle_seed(case: dict, repo: Path, launch: dict, exit_code: int | None, error: str) -> dict:
    """Everything after the replay ended: the seed's ledger row."""
    seed, directory = launch["seed"], launch["directory"]
    result = parse_result(launch["results"], seed)
    try:
        verify_runtime(case, repo)
    except RuntimeChanged as exc:
        error, result = str(exc), None
    record = {"seed": seed, "directory": str(directory), "exit_code": exit_code,
              "error": error, "result": result,
              "finished_at": datetime.now(timezone.utc).isoformat()}
    if error and method_switches.enabled(case, "heldout_watchdog") == "settle":
        # CC_TASK_14: the development rule. A policy timeout is a failed seed, a
        # persistence cut is the outcome already recorded; a stall stays infrastructure.
        verdict = watchdog_verdict(case, directory, launch["results"], "heldout", seed, error)
        if verdict:
            record["watchdog"] = {k: v for k, v in verdict.items() if k != "result"}
        if verdict and verdict["settled"]:
            if verdict["kind"] == "persistence_cut":
                settled = dict(verdict["result"])
                status = "crash" if settled["sandbox_rc"] else "success" if settled["task_completed"] else "failure"
            else:  # policy_timeout: the seed failed, as in development (ASPIRE counts it a failure).
                settled = {"sandbox_rc": 1, "reward": 0.0, "task_completed": 0, "trial_dir": str(directory)}
                status = "failure"
            record.update(result=settled, error="", status=status)
            return record
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


def run_parallel(case: dict, repo: Path, evaluation: Path, bundle: dict, kept: dict,
                 ledger: Path, data: dict, parallel: int, threads: int, seeds=None) -> None:
    """Every seed without a row, up to `parallel` at once; this thread writes the ledger.

    Rows are recorded as seeds finish, each by one atomic write of the whole
    ledger with its rows in seed order, so the file a serial sweep would have
    written is reproduced row for row. The first error stops new starts; the
    running seeds are still recorded before it is raised.
    """
    seeds = HELDOUT_SEEDS if seeds is None else seeds  # The module global, looked up now.
    pending = [seed for seed in seeds if str(seed) not in data["seeds"]]
    launches, first_error = {}, None
    with ReplayBatch(parallel) as batch:
        while pending or batch.running():
            while pending and batch.free() and first_error is None:
                seed = pending.pop(0)
                try:
                    launch = prepare_seed(case, repo, evaluation, bundle, kept, seed)
                except BaseException as exc:  # Verification failed: nothing of this seed ran.
                    first_error = exc
                    break
                launches[seed] = launch
                # The module attribute, looked up now, so a wrapper's patch applies.
                batch.submit(seed, globals()["run_replay"], launch["command"], repo=repo,
                             env=parallel_env(launch["env"], threads),
                             directory=launch["directory"], timeout=launch["timeout"])
            if first_error is not None or batch.stopping:
                pending = []
            for seed, (exit_code, error) in batch.wait():
                data["seeds"][str(seed)] = settle_seed(case, repo, launches.pop(seed), exit_code, error)
                data["seeds"] = {key: data["seeds"][key] for key in sorted(data["seeds"], key=int)}
                try:
                    atomic_json(ledger, data)  # Crash-safe per seed.
                except BaseException as exc:
                    first_error = first_error or exc
    if first_error is not None:
        raise first_error
    if batch.stopping:
        raise SystemExit(f"held-out sweep interrupted ({batch.interrupt.reason}); every running "
                         "seed was recorded")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", type=Path, default=os.environ.get("ASPIRE_NATIVE_CASE"),
                        required=False)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--seed-set", choices=tuple(SEED_SETS), default="heldout",
                        help="CC_TASK_14: `unseen` runs the case's unseen_seeds (66-80) after the freeze")
    args = parser.parse_args()
    if not args.case:
        parser.error("--case or ASPIRE_NATIVE_CASE is required")
    case, repo, task_dir = load_case(Path(args.case))
    if case.get("closed_loop_revision") or case.get("prediction_contract"):
        # Refuse an incompatible combination before a frozen bundle is copied.
        from executable_world_profile import validate
        validate(case)
    parallel, threads = parallel_settings(case, "heldout")  # Refused before anything is copied.
    seeds = HELDOUT_SEEDS
    if args.seed_set == "unseen":
        seeds = method_switches.unseen_seeds(case)
        if not seeds:
            raise SystemExit("this case defines no unseen seeds (method switch unseen_seeds)")
        if set(seeds) & set(HELDOUT_SEEDS):
            raise SystemExit("unseen seeds overlap held-out seeds 1-50")
    folder, ledger_name, report_name = SEED_SETS[args.seed_set]
    evaluation = Path(case["control"]) / "heldout" / folder if folder else Path(case["control"]) / "heldout"
    evaluation.mkdir(parents=True, exist_ok=True)
    ledger = evaluation / ledger_name
    bundle, kept = frozen_bundle(case, task_dir, evaluation)
    identity = {"cell": case["id"], "condition": case["condition"], "suite": case["suite"],
                "task": case["task"], "seeds": list(seeds),
                **({"seed_set": args.seed_set} if args.seed_set != "heldout" else {}),
                **({"profile": case["profile"]} if "profile" in case else {}),
                # Only when set: legacy held-out ledgers keep their identity, and a
                # closed-loop sweep cannot resume over an observational one.
                **({"closed_loop_revision": case["closed_loop_revision"]}
                   if case.get("closed_loop_revision") else {}),
                # Same rule: a p1 sweep cannot resume over a contract-off one.
                **({"prediction_contract": case["prediction_contract"]}
                   if case.get("prediction_contract") in ("p1", "p2-runtime", "p3-search") else {}),
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
    for seed in seeds:
        if str(seed) in data["seeds"]:
            continue  # Already recorded: never re-run and never overwrite its artifacts.
        directory = evaluation / f"seed_{seed:02d}"
        if directory.exists():
            # An interrupted seed left files behind but no ledger row. Replaying
            # invisibly over them would destroy the only evidence of what ran.
            raise SystemExit(
                f"seed {seed} has artifacts at {directory} but no recorded result; "
                "inspect and move that directory before continuing the sweep")
        if parallel > 1:
            continue  # Every pending seed is checked before the batch starts any.
        data["seeds"][str(seed)] = evaluate_seed(case, repo, evaluation, bundle, kept, seed)
        atomic_json(ledger, data)  # Crash-safe per seed.
    if parallel > 1:
        run_parallel(case, repo, evaluation, bundle, kept, ledger, data, parallel, threads,
                     seeds=None if args.seed_set == "heldout" else seeds)
    records = [data["seeds"][str(seed)] for seed in seeds]
    execution = ({"execution": {"heldout_parallel": parallel, "trial_threads": threads}}
                 if parallel > 1 else {})
    counts = {status: sum(r["status"] == status for r in records)
              for status in ("success", "failure", "crash", "infrastructure_error")}
    report = {"schema_version": 1, "cell": case["id"], "condition": case["condition"],
              "identity": identity, "seeds_evaluated": len(records),
              "all_seeds_accounted": len(records) == len(seeds),
              "counts": counts,
              "success_rate": counts["success"] / len(seeds),
              # Failures, crashes and blockers stay visible next to the rate.
              "per_seed": {str(r["seed"]): r["status"] for r in records},
              "unusable_seeds": [r["seed"] for r in records
                                 if r["status"] == "infrastructure_error"], **execution}
    (evaluation / report_name).write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return int(bool(report["unusable_seeds"]) or not report["all_seeds_accounted"])


if __name__ == "__main__":
    raise SystemExit(main())
