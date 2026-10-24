#!/usr/bin/env python3
"""Held-out evaluation of one frozen SAM2-streaming native cell over seeds 1-50.

Profile `sam2-streaming-native-v1` under `libero-pro-common-v1`. The outer
coordinator runs this, never the solver: no held-out outcome is returned to a
solver, there are no repairs and no per-seed program choice. One frozen bundle
is evaluated on every seed:

* policy and authored streaming config, written by the cell solver (Qwen):
  policy code, public SAM3 seed prompts, cadence, relation predicates;
* the engineered World runtime, SAM2 source and checkpoint, written by
  engineering and never labeled solver-generated;
* the bound model-service, perception and runtime-dependency manifests.

No object inventory and no high-level strategy MD exist in this profile.
Everything is checked against the development ledger and stage-1 result before
the first seed, and re-verified before and after every seed.

Scoring follows native_world_heldout.py: only a normal run with the oracle's
`task_completed` is a success; `sandbox_rc != 0` is a crash even when the
oracle reported completion; runner, timeout and verification failures are
`infrastructure_error`, reported separately. The denominator is always 50.

Budget: each seed's watchdog is min(900 s, time left); the evaluation phase has
an absolute 18,000 s deadline recorded on first start and never reset by a
resume. The runner is serial and records each seed as `running` in the
persistent ledger before its process starts. After a timeout, interrupt or
crash the ledger and executed seeds stay; an in-flight seed is never re-run or
overwritten, only settled from its recorded evidence by `--resolve-interrupted`.
After the deadline no further seed starts, and unexecuted seeds are reported as
`not_run` with the cell marked incomplete; no result is invented for them.

The outer-authorization marker and the separate `<control>/heldout` directory
are experiment-integrity controls, not a security sandbox: generated code runs
with full import access and could read anything its process can reach.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import streaming_fixloop_protocol as sp  # noqa: E402
from native_world_fixloop_state import ProtocolError, bundle_identity, code_hash  # noqa: E402
from native_world_protocol import parse_result, read_json  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "common"))
from native_cc_freeze import RuntimeChanged, verify_runtime  # noqa: E402
from native_cc_runtime import atomic_json  # noqa: E402
from native_cc_trial_process import run_replay  # noqa: E402

VERSION = 1
HELDOUT_SEEDS = tuple(range(1, 51))
FINAL_STATUSES = ("success", "failure", "crash", "infrastructure_error")
GRADED_PHASES = ("smoke", "initial", "repair")
OUTER_MARKER = "OUTER_HELDOUT_AUTHORIZED"
FROZEN_FILES = {"policy": "fix_code.py", "authored_streaming_config": "fix_streaming_config.json"}


class SweepStopped(ProtocolError):
    """Verification failed between seeds; nothing further may start."""


def runner_sha256() -> str:
    return code_hash(Path(__file__).read_text())


def require_outer(case: dict) -> None:
    """An outer coordinator writes this marker; a solver has no reason to.

    It is an integrity check plus guard-rule target, not a security boundary.
    """
    marker = Path(case["control"]) / OUTER_MARKER
    if not marker.is_file() or marker.read_text().strip() != case["id"]:
        raise ProtocolError(f"held-out evaluation is outer-only; write the cell id to {marker}")


def development_evidence(task_dir: Path, current: dict) -> tuple[dict, dict]:
    stage1 = read_json(task_dir / "stage1_result.json")
    ledger = read_json(task_dir / "development_state.json")
    if not stage1 or not stage1.get("stage1_complete") or not ledger or not ledger.get("stage1_complete"):
        raise ProtocolError("stage 1 is not complete; finalize the development ledger first")
    if stage1.get("profile") != current["profile"] or stage1.get("protocol_id") != sp.PROTOCOL_ID:
        raise ProtocolError("stage1_result.json does not match this streaming method profile")
    if ledger["identity"] != current:
        # Engineering World, SAM2, model/perception/runtime manifests, config,
        # protocol, model and task must all equal what development froze.
        raise RuntimeChanged("current runtime identity differs from the development ledger")
    return stage1, ledger


def freeze(task_dir: Path, evaluation: Path, stage1: dict, ledger: dict) -> tuple[dict, dict, dict]:
    """Copy the selected files once and prove they are the tested selection."""
    frozen = evaluation / "frozen"
    first = not frozen.exists()
    # The first run validates the cell's files and copies them only once they
    # pass, so a refused freeze leaves no stale copy; later runs use the copy.
    kept = {key: (task_dir if first else frozen) / name for key, name in FROZEN_FILES.items()}
    problems = sp.streaming_config_errors(kept["authored_streaming_config"].read_text(), ledger["identity"]["profile"])
    if problems:
        raise ProtocolError("; ".join(problems))
    bundle, components = frozen_bundle(ledger["identity"], kept)
    digest = bundle_identity(bundle)
    if bundle != stage1["selected_bundle"] or digest != stage1.get("selected_bundle_sha256"):
        raise ProtocolError("frozen files do not match the selected development bundle")
    if (ledger.get("selected") or {}).get("bundle_sha256") != digest or \
            (stage1.get("selection") or {}).get("bundle_sha256") != digest:
        raise ProtocolError("the recorded selection does not match the frozen bundle")
    # The ledger rows are the evidence; a stage-1 flag alone is not trusted.
    executed = [r for r in ledger["trials"] if r.get("bundle_sha256") == digest
                and r.get("phase") in GRADED_PHASES and r.get("status") == "complete"]
    if not executed or digest not in (stage1.get("tested_bundles") or {}):
        raise ProtocolError("the selected bundle was never tested in development")
    if stage1.get("observation_fallback"):
        raise ProtocolError("this profile has no observation fallback")
    if "supervised_repair" in ledger["identity"]:
        supervised_gate(ledger, stage1, digest)
    if first:
        frozen.mkdir(parents=True)
        for key, name in FROZEN_FILES.items():
            (frozen / name).write_bytes(kept[key].read_bytes())
        kept = {key: frozen / name for key, name in FROZEN_FILES.items()}
        if frozen_bundle(ledger["identity"], kept)[0] != bundle:
            raise ProtocolError("cell files changed while being frozen")
    identity = ledger["identity"]
    manifest = {
        "bundle": bundle, "bundle_sha256": digest,
        "policy": {"path": str(kept["policy"]), "sha256": bundle["policy"],
                   "author": "cell solver (Qwen): policy code and relation predicates"},
        "authored_streaming_config": {
            "path": str(kept["authored_streaming_config"]),
            "sha256": components["authored_streaming_config_sha256"],
            "author": "cell solver (Qwen): public SAM3 prompts and cadence"},
        "engineered_world": identity["engineered_world"], "sam2": identity["sam2"],
        "model_service": identity["model_service"], "perception": identity["perception_services"],
        "runtime_manifest_sha256": identity.get("runtime_manifest_sha256"),
        "dependency_evidence": identity["dependency_evidence"],
        "config_sha256": identity["config_sha256"], "protocol_sha256": identity["protocol_sha256"],
        "object_inventory": None, "strategy_md": None,
        "development_evidence": [r["directory"] for r in executed]}
    return bundle, kept, manifest


def supervised_gate(ledger: dict, stage1: dict, digest: str) -> None:
    """Supervised repair study: re-derive the gate from the ledger rows themselves.

    The study must be closed by exhausted rounds or an outer freeze, and the
    frozen bundle must have its own normal oracle success in this study's rows.
    A stage-1 flag, another bundle's success or a historical one is not enough.
    """
    sup = ledger.get("supervised") or {}
    closure = sup.get("closure") or {}
    if sup.get("state") != "closed" or closure.get("reason") not in ("rounds_exhausted", "outer_freeze"):
        raise ProtocolError("supervised development is not closed by exhausted rounds or an outer "
                            "freeze; no held-out evaluation")
    own = [r for r in ledger["trials"] if r.get("bundle_sha256") == digest and sp.rounds.passed_row(r)]
    if not own:
        raise ProtocolError("development_failed/heldout_skipped: the selected bundle has no oracle "
                            "success of its own in this study")
    if (stage1.get("supervised_repair") or {}).get("heldout_gate") != "passed":
        raise ProtocolError("stage1_result.json does not record a passed supervised held-out gate")


def frozen_bundle(identity: dict, kept: dict) -> tuple[dict, dict]:
    digest, components = sp.world_digest(
        identity, code_hash(kept["authored_streaming_config"].read_text()))
    return {"policy": code_hash(kept["policy"].read_text()), "world": digest}, components


def verify_frozen(case: dict, repo: Path, identity: dict, bundle: dict, kept: dict,
                  when: str) -> None:
    """Before and after every seed: runtime freeze, cell identity, frozen bundle."""
    verify_runtime(case, repo)
    if sp.identity(case, repo) != identity:
        raise RuntimeChanged(f"runtime identity changed {when}")
    if frozen_bundle(identity, kept)[0] != bundle:
        raise RuntimeChanged(f"frozen bundle changed {when}; the held-out bundle is immutable")


def classify(exit_code, error: str, result: dict | None, receipt: dict | None) -> dict:
    """native_world_heldout.py semantics; an authored seed-prompt fault is a crash."""
    if not error and exit_code not in (None, 0) and receipt and receipt.get("authored"):
        return {"status": "crash", "world_error": {"stage": "streaming_startup", **receipt}}
    if error or exit_code != 0 or result is None:
        return {"status": "infrastructure_error"}
    if result["sandbox_rc"]:
        return {"status": "crash"}  # Even when the oracle reported completion.
    return {"status": "success" if result["task_completed"] else "failure"}


class HeldoutLedger:
    def __init__(self, path: Path, identity: dict, *, resume: bool, clock):
        self.path, self.clock = path, clock
        if path.is_file():
            if not resume:
                raise ProtocolError("held-out state exists; pass --resume to continue it")
            self.data = json.loads(path.read_text())
            if self.data.get("version") != VERSION or self.data.get("identity") != identity:
                raise ProtocolError("held-out identity changed; a resume must reuse the frozen inputs")
        else:
            if resume:
                raise ProtocolError("no held-out ledger to resume")
            now = clock()
            seconds = identity["evaluation_watchdog_seconds"]
            # Recorded once at first start; a resume never refreshes it.
            self.data = {"version": VERSION, "identity": identity, "seeds": {}, "stops": [],
                         "phase_clock": {"evaluation": {"started_at": now,
                                                        "watchdog_seconds": seconds,
                                                        "deadline": now + seconds}}}
            self.save()

    def save(self) -> None:
        atomic_json(self.path, self.data)

    def remaining(self) -> float:
        return self.data["phase_clock"]["evaluation"]["deadline"] - self.clock()

    def running(self) -> list[int]:
        return [int(s) for s, r in self.data["seeds"].items() if r["status"] == "running"]


def seed_env(case: dict, repo: Path, task_dir: Path, directory: Path) -> dict:
    env = sp.trial_env(case, repo, task_dir)
    env["SNAPSHOT_DIR"] = str(directory)  # Nothing is written into the development cell.
    return env


def evaluate_seed(case, repo, task_dir, evaluation, ledger, bundle, kept, seed) -> dict:
    identity = ledger.data["identity"]["development"]
    directory = evaluation / f"seed_{seed:02d}"
    if directory.exists():
        # Files without a ledger row: never replay over the only evidence.
        raise SweepStopped(f"seed {seed} has artifacts at {directory} but no recorded row; "
                           "inspect and move that directory before continuing")
    verify_frozen(case, repo, identity, bundle, kept, f"before seed {seed}")
    started = ledger.clock()
    timeout = min(case["trial_timeout"], max(1, int(ledger.remaining())))
    record = {"seed": seed, "status": "running", "directory": str(directory),
              "bundle_sha256": bundle_identity(bundle), "started_at": started,
              "watchdog_seconds": timeout, "deadline_clipped": timeout < case["trial_timeout"]}
    ledger.data["seeds"][str(seed)] = record
    ledger.save()  # Pre-recorded: an interrupted seed stays visible and is never re-run.
    directory.mkdir(parents=True)
    (directory / "code.py").write_bytes(kept["policy"].read_bytes())
    config = sp.materialize_config(case, directory, kept["authored_streaming_config"])
    command = sp.trial_command(case, repo, directory, "heldout", seed, config)
    results = directory / "results"
    (directory / "command.json").write_text(json.dumps(
        {"command": command, "result_root": str(results), "profile": case["profile"]}, indent=2))
    exit_code, error = run_replay(command, repo=repo, env=seed_env(case, repo, task_dir, directory),
                                  directory=directory, timeout=timeout)
    result = parse_result(results, seed)
    stop = None
    try:
        verify_frozen(case, repo, identity, bundle, kept, f"during seed {seed}")
    except RuntimeChanged as exc:
        error, result, stop = str(exc), None, str(exc)
    receipt = sp.startup_error(results)
    finished = ledger.clock()
    record.update(exit_code=exit_code, error=error, result=result,
                  timed_out="TimeoutExpired" in (error or ""), finished_at=finished,
                  duration_seconds=finished - started,
                  **({"streaming_startup": receipt} if receipt else {}),
                  **classify(exit_code, error, result, receipt))
    ledger.save()
    if stop:
        raise SweepStopped(stop)
    return record


def resolve_interrupted(ledger: HeldoutLedger, seed: int) -> dict:
    """Outer coordinator only: settle a `running` seed from recorded evidence.

    Nothing is started or replayed. The consumed run becomes a final status.
    """
    record = ledger.data["seeds"].get(str(seed))
    if not record or record["status"] != "running":
        raise ProtocolError(f"seed {seed} has no unresolved running row")
    directory = Path(record["directory"])
    lifecycle = read_json(directory / "lifecycle.json") or {}
    pid = lifecycle.get("pid")
    if pid and "finished_at" not in lifecycle and process_alive(pid, lifecycle.get("proc_start_ticks")):
        raise ProtocolError(f"seed {seed} process {pid} is still alive; wait or stop it first")
    result = parse_result(directory / "results", seed) if directory.exists() else None
    exit_code = lifecycle.get("process_exit_code")
    error = lifecycle.get("error") or ("" if "finished_at" in lifecycle else
                                       "runner interrupted before the seed finished")
    receipt = sp.startup_error(directory / "results") if directory.exists() else None
    record.update(exit_code=exit_code, error=error, result=result,
                  recovery={"by": "outer coordinator", "at": ledger.clock(), "replayed": False},
                  **classify(exit_code, error, result, receipt))
    ledger.save()
    return record


def process_alive(pid: int, ticks) -> bool:
    stat = Path(f"/proc/{pid}/stat")
    if not stat.exists():
        return False
    return ticks is None or stat.read_text().rsplit(")", 1)[1].split()[19] == ticks


def report(case: dict, ledger: HeldoutLedger, manifest: dict, stop: str | None) -> dict:
    rows = ledger.data["seeds"]
    per_seed = {str(s): rows[str(s)]["status"] if str(s) in rows else "not_run"
                for s in HELDOUT_SEEDS}
    counts = {status: sum(v == status for v in per_seed.values())
              for status in (*FINAL_STATUSES, "running", "not_run")}
    complete = counts["running"] == 0 and counts["not_run"] == 0
    expired = ledger.remaining() <= 0
    return {
        "schema_version": 1, "profile": case["profile"], "protocol_id": sp.PROTOCOL_ID,
        "cell": case["id"], "method": case["method"], "repeat": case["repeat"],
        "task": case["task"], "condition": "C", "identity": ledger.data["identity"],
        "frozen_manifest": manifest, "phase_clock": ledger.data["phase_clock"],
        "deadline_expired": expired, "stop_reason": stop,
        "completed": complete, "incomplete": not complete,
        "seeds_evaluated": sum(counts[s] for s in FINAL_STATUSES),
        "counts": counts, "success_rate_denominator": len(HELDOUT_SEEDS),
        "success_rate": counts["success"] / len(HELDOUT_SEEDS),
        "per_seed": per_seed,
        "not_run_seeds": [int(s) for s, v in per_seed.items() if v == "not_run"],
        "unresolved_running_seeds": ledger.running(),
        "infrastructure_error_seeds": [int(s) for s, v in per_seed.items()
                                       if v == "infrastructure_error"],
        "durations_seconds": {s: r.get("duration_seconds") for s, r in rows.items()},
        "failure_reasons": {s: r.get("error") or r.get("world_error") for s, r in rows.items()
                            if r["status"] in ("crash", "infrastructure_error")},
        "dependency_evidence": ledger.data["identity"]["development"]["dependency_evidence"],
    }


def run(case_path: Path, *, resume: bool, resolve: int | None, clock=time.time) -> dict:
    case, repo, task_dir = sp.load_case(case_path)
    require_outer(case)
    evaluation = Path(case["control"]).resolve() / "heldout"
    if evaluation.is_relative_to(repo) or evaluation.is_relative_to(task_dir):
        raise ProtocolError("held-out outputs live in <control>/heldout, outside repo and cell")
    evaluation.mkdir(parents=True, exist_ok=True)
    with (evaluation / ".heldout.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ProtocolError("another held-out runner holds this cell; the sweep is serial") from exc
        current = sp.identity(case, repo)
        verify_runtime(case, repo)
        stage1, development = development_evidence(task_dir, current)
        bundle, kept, manifest = freeze(task_dir, evaluation, stage1, development)
        manifest_path = evaluation / "frozen_manifest.json"
        if manifest_path.is_file():
            if json.loads(manifest_path.read_text()) != manifest:
                raise ProtocolError("frozen manifest changed; a resume must reuse the frozen inputs")
        else:
            atomic_json(manifest_path, manifest)
        identity = {"development": current, "bundle": bundle,
                    "bundle_sha256": bundle_identity(bundle), "seeds": list(HELDOUT_SEEDS),
                    "frozen_manifest_sha256": sp.file_sha256(manifest_path),
                    "runner_sha256": runner_sha256(),
                    "evaluation_watchdog_seconds": case["evaluation_watchdog_seconds"],
                    "trial_timeout": case["trial_timeout"], "max_steps": case["max_steps"]}
        ledger = HeldoutLedger(evaluation / "heldout_state.json", identity,
                               resume=resume or resolve is not None, clock=clock)
        stop = None
        if resolve is not None:
            resolve_interrupted(ledger, resolve)
        elif ledger.running():
            stop = (f"seeds {ledger.running()} were in flight when the runner stopped; settle them "
                    "with --resolve-interrupted (no replay) before continuing")
        else:
            for seed in HELDOUT_SEEDS:
                if str(seed) in ledger.data["seeds"]:
                    continue  # Recorded: never re-run, never overwritten.
                if ledger.remaining() <= 0:
                    stop = "evaluation deadline passed; remaining seeds are not_run"
                    break
                try:
                    evaluate_seed(case, repo, task_dir, evaluation, ledger, bundle, kept, seed)
                except (SweepStopped, RuntimeChanged) as exc:
                    stop = str(exc)
                    break
        if stop:
            ledger.data["stops"].append({"at": clock(), "reason": stop})
            ledger.save()
        result = report(case, ledger, manifest, stop)
        atomic_json(evaluation / "heldout_result.json", result)
        return result


def main(argv: list[str] | None = None, clock=time.time) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--case", type=Path, default=os.environ.get("ASPIRE_NATIVE_CASE"))
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--resolve-interrupted", type=int, metavar="SEED")
    args = parser.parse_args(argv)
    if not args.case:
        parser.error("--case or ASPIRE_NATIVE_CASE is required")
    try:
        result = run(args.case, resume=args.resume, resolve=args.resolve_interrupted, clock=clock)
    except (ProtocolError, RuntimeChanged, OSError) as exc:
        print(json.dumps({"error": str(exc), "type": type(exc).__name__}, ensure_ascii=False))
        return 2
    print(json.dumps({k: result[k] for k in (
        "cell", "completed", "incomplete", "stop_reason", "counts", "success_rate",
        "not_run_seeds", "unresolved_running_seeds", "infrastructure_error_seeds")}, indent=2))
    return int(result["incomplete"] or bool(result["infrastructure_error_seeds"]))


if __name__ == "__main__":
    raise SystemExit(main())
