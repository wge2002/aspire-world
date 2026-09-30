"""Opt-in execution of ledger-admitted model programs; no model access here."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

from .live_broker import _child_environment, _launch_child, _prepare_live, _write_json
from .reference_scene_broker import _check_inventory
from .relational_scene_broker import RelationalSceneBroker
from scripts.libero.world_fix_loop_state import Ledger, MODEL, PROTOCOL, SUITE, TASK, read, sha

MODE = "opus46-world-fix-loop"
SCHEMA = 6
FIXED = {
    "schema_version": SCHEMA, "mode": MODE,
    "profile": "bowl-relational-world-one-fifteen-fifty",
    "task_gate": {"suite": SUITE, "task": TASK},
    "observation": {"object_id": "bowl", "segmentation_prompt": "bowl", "min_score": .5},
    "identity_binding": "unique_high_score_mask",
    "query_schedule": "close_reference_then_policy_verify",
    **{k: PROTOCOL[k] for k in ["query_budget", "tolerance", "max_actions", "max_recovery", "trial_timeout_seconds"]},
}
PROVENANCE = {"world_program_generator": MODEL, "policy_generator": MODEL, "model_id": MODEL,
              "generation_context": "native model response; paired sources frozen by campaign ledger"}
VARIABLE = {"campaign_root", "phase", "index", "identity_sha256", "generation_sha256",
            "world_program", "world_program_sha256", "policy_sha256", "scene_inventory",
            "output_root", "run_name", "model_provenance"}


def make_config(ledger, phase, index):
    record = ledger.admission(phase, index)
    sources = ledger.sources(record["version"])
    return {**FIXED, "campaign_root": str(ledger.root), "phase": phase, "index": index,
            "identity_sha256": ledger.identity_sha,
            "generation_sha256": sha(ledger.slot(record["version"]) / "result.json"),
            "world_program": str(sources["world"]), "world_program_sha256": sha(sources["world"]),
            "policy_sha256": sha(sources["policy"]), "scene_inventory": read(sources["inventory"]),
            "output_root": str(ledger.trial_dir(phase, index) / "evidence"),
            "run_name": "world", "model_provenance": PROVENANCE}


def load_config(args, config=None):
    if getattr(args, "api_key", None) or not args.replay_code or args.interactive:
        raise ValueError("frozen, noninteractive, credential-free replay required")
    config = read(args.world_model_config) if config is None else config
    if not isinstance(config, dict) or set(config) != set(FIXED) | VARIABLE:
        raise ValueError("unexpected world fix-loop config fields")
    for key, value in FIXED.items():
        if config[key] != value or isinstance(config[key], bool):
            raise ValueError("fixed protocol differs: " + key)
    ledger = Ledger(config["campaign_root"])
    ledger.verify_runtime()
    expected = make_config(ledger, config["phase"], config["index"])
    if config != expected:
        raise ValueError("config differs from ledger-admitted generation/phase")
    record = ledger.admission(config["phase"], config["index"])
    if (args.suite, args.task, args.trial) != (SUITE, TASK, record["seed"]):
        raise ValueError("task or seed differs from reservation")
    if sha(args.replay_code) != config["policy_sha256"]:
        raise ValueError("executed policy differs from generation")
    settings = ledger.identity["settings"]
    if sha(args.config) != settings["yaml_sha256"]:
        raise ValueError("base YAML differs from frozen identity")
    import yaml
    base = yaml.safe_load(Path(args.config).read_text())
    cfg = base.get("env", {}).get("cfg", {})
    if (cfg.get("privileged", False) or cfg.get("low_level", {}).get("privileged", False)
            or cfg.get("apis") != ["FrankaLiberoApiReducedSkillLibraryTraced"]):
        raise ValueError("only the nonprivileged reduced traced API is allowed")
    _check_inventory(config["scene_inventory"])
    # All output paths are derived by the coordinator from the immutable ledger.
    ordinary = Path(args.output_dir).resolve()
    if not ordinary.is_relative_to(ledger.trial_dir(config["phase"], config["index"])):
        raise ValueError("ordinary output outside reserved trial")
    return config, ledger


def mechanism_evidence(tape, manifest):
    """Compact, auditable record of what the world mechanism actually did.

    Derived from the tape, which records every attempted query, so an attempted
    measurement whose comparison was UNKNOWN is counted as an attempt and is
    never conflated with a frame at which nothing was measured. Call counts stay
    separate: `world_verify_calls` is a call count and remains reported as such.
    """
    comparisons = [e for e in tape if e["event"] == "query_comparison"]
    relation = [e for e in comparisons if e.get("purpose") == "relation"]
    reference = [e for e in comparisons if e.get("purpose") == "reference"]
    verdicts = {"support": 0, "contradict": 0, "unknown": 0}
    for event in relation:
        status = str(event["comparison"]["status"]).lower()
        if status in verdicts:
            verdicts[status] += 1
    established = sum(e.get("measurement_status") == "ok" for e in reference)
    evidence = {
        "world_query_used": manifest.get("query_used"),
        "world_reference_purpose_queries": len(reference),
        "world_reference_measurements_ok": established,
        "world_reference_established": established > 0,
        "world_relation_purpose_queries": len(relation),
        "world_relation_measurement_attempts": len(relation),
        "world_relation_verdicts": verdicts,
        "world_relation_decisive_verdicts": verdicts["support"] + verdicts["contradict"],
        "world_relation_checks_not_requested":
            sum(e["event"] == "relation_check_not_requested" for e in tape),
        "world_off_schedule_reference_requests":
            sum(e["event"] == "reference_request_off_schedule" for e in tape),
        "world_query_denials": sum(e["event"] == "query_denied" for e in tape),
        "world_reference_invalidations": list(manifest.get("reference_invalidations") or []),
    }
    # A real measurement attempt on both legs of the mechanism. Neither task
    # success, nor SUPPORT, nor a decisive verdict is required.
    evidence["world_mechanism_evidence_observed"] = bool(
        evidence["world_reference_established"]
        and evidence["world_relation_measurement_attempts"] > 0)
    return evidence


def terminal_outcome(directory, ledger, phase, index, child_receipt):
    """Preserve model failures; require real terminal and matching raw evidence."""
    from scripts.libero.paired_bowl_supervisor import artifacts
    directory = Path(directory)
    record = ledger.admission(phase, index)
    result = artifacts(directory, ledger.trial_dir(phase, index), record["seed"], "world")
    result["status"] = "infrastructure_error"
    if not result["valid"]:
        return result
    try:
        manifest = read(directory / "live_manifest.json")
        config = make_config(ledger, phase, index)
        for key, expected in {
            "schema_version": SCHEMA, "mode": MODE, "model_provenance": PROVENANCE,
            "identity_sha256": ledger.identity_sha, "generation_sha256": config["generation_sha256"],
            "phase": phase, "version": record["version"],
            "adapter_sha256": sha(Path(__file__)),
            "relational_adapter_sha256": sha(Path(__file__).with_name("relational_scene_broker.py")),
        }.items():
            if manifest.get(key) != expected:
                raise ValueError("manifest identity mismatch: " + key)
        if read(directory / "live_config.json") != config:
            raise ValueError("live config differs")
        if child_receipt.get("status") != "completed":
            raise ValueError("child did not complete")
        tape = [json.loads(line) for line in (directory / "live_tape.jsonl").read_text().splitlines()]
        program_errors = [e for e in tape if e["event"] == "program_error"]
        if any(e["event"] == "broker_error" for e in tape):
            raise ValueError("broker infrastructure error")
        if manifest.get("error") or not manifest.get("trial_result"):
            raise ValueError("missing clean replay terminal")
        if manifest.get("status") != "complete" and not program_errors:
            raise ValueError("unexplained incomplete world mechanism")
        summary = (Path(result["trial_dir"]) / "summary.txt").read_text(errors="replace")
        if result["sandbox_rc"] != 0 and any(marker in summary for marker in
            ["requests.exceptions.ConnectionError", "httpx.ConnectError", "Connection refused",
             "requests.exceptions.ReadTimeout", "httpx.ReadTimeout"]):
            raise ValueError("public service transport failed during policy execution")
        if any(manifest.get(k, 10**9) > cap for k, cap in
               [("action_count", 30), ("query_used", 4), ("recovery_used", 1)]):
            raise ValueError("execution budget exceeded")
        result["world_program_errors"] = program_errors
        result["world_verify_calls"] = sum(e["event"] == "world_verify" for e in tape)
        # Kept verbatim for existing readers (build_request, older summaries): a
        # call count, not evidence that anything was measured.
        result["world_participation_observed"] = result["world_verify_calls"] > 0
        result.update(mechanism_evidence(tape, manifest))
        result["status"] = ("program_error" if result["sandbox_rc"] != 0 or program_errors else
                            "success" if result["task_completed"] else "task_failure")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        result["validation_error"] = str(exc)
    return result


def run_fix_loop_scene(args):
    config, ledger = load_config(args)
    request_path = _prepare_live(args, config)
    directory = request_path.parent
    request = read(request_path)
    request["schema_version"] = SCHEMA
    _write_json(request_path, request)
    path = directory / "live_manifest.json"
    manifest = read(path)
    record = ledger.admission(config["phase"], config["index"])
    manifest.update(schema_version=SCHEMA, mode=MODE, phase=config["phase"], version=record["version"],
                    partition="heldout" if config["phase"] == "heldout" else "development",
                    identity_sha256=ledger.identity_sha, generation_sha256=config["generation_sha256"],
                    generation_model_requests_this_trial=0, adapter_sha256=sha(Path(__file__)),
                    relational_adapter_sha256=sha(Path(__file__).with_name("relational_scene_broker.py")))
    _write_json(path, manifest)
    receipt = _launch_child(request_path, _child_environment(dict(os.environ)),
                            config["trial_timeout_seconds"], module="cap.world_model.fix_loop_scene_broker")
    result = terminal_outcome(directory, ledger, config["phase"], config["index"], receipt)
    _write_json(directory / "outcome.json", result)
    if result["status"] == "infrastructure_error":
        raise RuntimeError("incomplete infrastructure evidence: " + str(directory))


def _child_main(request_path):
    directory = request_path.parent
    request, config, manifest = (read(request_path), read(directory / "live_config.json"),
                                read(directory / "live_manifest.json"))
    if request["schema_version"] != SCHEMA:
        raise ValueError("unsupported child schema")
    args = SimpleNamespace(**request["args"])
    load_config(args, config)
    for key, name in [("world_program_sha256", "world_program.py"), ("policy_sha256", "frozen_policy.py"),
                      ("live_config_sha256", "live_config.json"), ("yaml_sha256", "source_config.yaml")]:
        if manifest[key] != sha(directory / name):
            raise ValueError("child input hash mismatch: " + key)
    replay_path = Path(__file__).resolve().parents[2] / "scripts/libero/replay_trial.py"
    spec = importlib.util.spec_from_file_location("_fix_loop_world_replay", replay_path)
    replay = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = replay
    spec.loader.exec_module(replay)
    if os.environ.get("SAM3_SERVICE_URL"):
        from aspire.sim.cap.integrations.vision import sam3
        sam3.SERVICE_URL = os.environ["SAM3_SERVICE_URL"]
    if os.environ.get("PYROKI_SERVICE_URL"):
        from aspire.sim.cap.integrations.motion import pyroki
        pyroki.init_pyroki.__defaults__ = (os.environ["PYROKI_SERVICE_URL"],)
    broker = RelationalSceneBroker(directory, config, manifest, (directory / "world_program.py").read_text())
    error = None
    try:
        replay._run_replay(args, _world_capture=broker)
    except BaseException as exc:
        error = type(exc).__name__
        raise
    finally:
        broker.close(error)
    # A recorded world-program error is an experimental failure, not a missing
    # simulator terminal. The parent independently validates and classifies it.


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--child-request", type=Path, required=True)
    _child_main(parser.parse_args().child_request.resolve())
