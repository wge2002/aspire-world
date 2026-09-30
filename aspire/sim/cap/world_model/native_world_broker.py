"""Uncapped native world replay adapter.

Runs one trial with a native-CC-authored world program and policy under the
same relational world mechanism as the frozen diagnostics -- valid state
advancement, grasp-reference measurement, predict/compare/assimilate, and
world_verify all come from RelationalSceneBroker unchanged. This module only
adapts three per-trial input files plus an output directory into that broker.

What it deliberately does NOT do:

* It has no execution caps. max_actions, max_recovery, and query_budget must all
  be the literal string "unlimited" (live_broker.UNLIMITED). Calls and queries
  are still counted and recorded; nothing is refused for exceeding a quota. A
  policy does not have to call use_recovery to retry -- the recovery helpers are
  accounting kept for interface compatibility, and under this mode they never
  return False.
* It does not import scripts.libero.world_fix_loop_state: no Ledger, no
  1+15+50 revision protocol, no latest-version selection, no request cap, and
  no fixed world-program digest. Seed and version selection belong to the
  native protocol built on top of this module, not to the adapter.
* It does not decide task solutions or add a second world model.

Preserved from the frozen path: the 900 s trial watchdog, the world worker's
ordinary 2 s per-call timeout, public observation only (no simulator ground
truth), and child-process credential isolation via _child_environment.
"""
from __future__ import annotations

import contextlib
import importlib.util
import json
import math
import os
import re
import signal
import sys
from pathlib import Path
from types import SimpleNamespace

from .live_broker import (SUITE, TASK, UNLIMITED, _child_environment,
                          _launch_child, _prepare_live, _sha256, _write_json)
from .relational_scene_broker import (MODE as RELATIONAL_MODE, SCHEDULE,
                                      RelationalSceneBroker)

MODE = "opus46-native-world-fix-loop"
SCHEMA = 7
IDENTITY_BINDING = "unique_high_score_mask"
INPUTS = ("world_program", "policy", "scene_inventory")
_SHA_RE = re.compile(r"[a-f0-9]{64}")
_CONFIG_KEYS = {
    "schema_version", "mode", "task_gate", "world_program",
    "world_program_sha256", "policy_sha256", "scene_inventory_path",
    "scene_inventory_sha256", "scene_inventory", "observation", "query_budget",
    "tolerance", "max_actions", "max_recovery", "trial_timeout_seconds",
    "identity_binding", "query_schedule", "output_root", "run_name",
    "model_provenance",
}
_ENTITY_KEYS = {"id", "label", "role", "confidence", "shape_prior"}
_PROV_KEYS = {"model_id", "policy_generator", "world_program_generator",
              "generation_context"}


def _digest(value, label):
    if not isinstance(value, str) or not _SHA_RE.fullmatch(value):
        raise ValueError(f"{label} must be a lowercase sha256 hex digest")
    return value


def check_inventory(inventory):
    """Validate a scene inventory: semantic metadata only, no coordinates."""
    if not isinstance(inventory, list) or not 2 <= len(inventory) <= 12:
        raise ValueError("scene inventory must contain 2-12 entities")
    ids = set()
    for entity in inventory:
        if not isinstance(entity, dict) or set(entity) != _ENTITY_KEYS:
            raise ValueError(
                "scene entities must contain only semantic metadata")
        if (not all(isinstance(v, str) and v for v in entity.values())
                or not entity["id"].replace("_", "").isalnum()
                or entity["id"] in ids or len(entity["label"]) > 160
                or entity["role"] not in {"manipulated", "target", "context"}):
            raise ValueError("invalid scene entity")
        ids.add(entity["id"])
    manipulated = [e["id"] for e in inventory if e["role"] == "manipulated"]
    if len(manipulated) != 1 or len(
            [e for e in inventory if e["role"] == "target"]) != 1:
        raise ValueError(
            "scene inventory needs exactly one manipulated and one target role")
    return manipulated[0]


def validate_native_config(config, directory):
    """Path-independent validation, shared by the parent and the child.

    ``directory`` resolves relative ``world_program`` / ``scene_inventory_path``
    entries. Returns the config with those two resolved and the inventory read
    back from disk, so the digest recorded in the config is what the broker
    actually runs.
    """
    if not isinstance(config, dict):
        raise ValueError("native world config must be an object")
    if config.get("schema_version") != SCHEMA:
        raise ValueError(f"native world config requires schema_version={SCHEMA}")
    if config.get("mode") != MODE:
        raise ValueError(f"config mode must be {MODE}")
    if set(config) - _CONFIG_KEYS:
        raise ValueError(
            "native world config has unsupported keys; keep credentials out")
    if not _CONFIG_KEYS - {"scene_inventory"} <= set(config):
        raise ValueError("native world config is missing required keys")
    gate = config["task_gate"]
    if not isinstance(gate, dict) or set(gate) != {"suite", "task"}:
        raise ValueError("task_gate requires exactly suite and task")
    if (gate["suite"], gate["task"]) != (SUITE, TASK):
        raise ValueError(f"task_gate must be {SUITE}/{TASK}")

    # Uncapped by construction: a number here would quietly reintroduce the very
    # 30/1/4 caps this mode exists to remove, so numbers are refused outright
    # rather than accepted and enlarged.
    for key in ("max_actions", "max_recovery", "query_budget"):
        if config[key] != UNLIMITED or not isinstance(config[key], str):
            raise ValueError(
                f"{key} must be {UNLIMITED!r} in the native world mode")

    tolerance = config["tolerance"]
    if (not isinstance(tolerance, (int, float)) or isinstance(tolerance, bool)
            or not math.isfinite(tolerance) or tolerance <= 0):
        raise ValueError("tolerance must be a positive finite number")
    timeout = config["trial_timeout_seconds"]
    if (not isinstance(timeout, (int, float)) or isinstance(timeout, bool)
            or not math.isfinite(timeout) or not 0 < timeout <= 900):
        raise ValueError("trial_timeout_seconds must be in (0, 900]")
    if config["identity_binding"] != IDENTITY_BINDING:
        raise ValueError(f"identity_binding must be {IDENTITY_BINDING}")
    if config["query_schedule"] != SCHEDULE:
        raise ValueError(f"query_schedule must be {SCHEDULE}")
    if not isinstance(config["run_name"], str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_.-]*", config["run_name"]):
        raise ValueError("run_name must be a single safe path component")

    provenance = config["model_provenance"]
    if not isinstance(provenance, dict) or set(provenance) != _PROV_KEYS:
        raise ValueError(f"model_provenance requires exactly {sorted(_PROV_KEYS)}")
    if any(not isinstance(v, str) or not v.strip()
           for v in provenance.values()):
        raise ValueError("provenance values must be nonempty strings")

    world = Path(config["world_program"])
    world = world if world.is_absolute() else directory / world
    _digest(config["world_program_sha256"], "world_program_sha256")
    if not world.is_file() or _sha256(world) != config["world_program_sha256"]:
        raise ValueError(f"world program missing or digest mismatch: {world}")
    config["world_program"] = str(world.resolve())

    inventory_path = Path(config["scene_inventory_path"])
    inventory_path = (inventory_path if inventory_path.is_absolute()
                      else directory / inventory_path)
    _digest(config["scene_inventory_sha256"], "scene_inventory_sha256")
    if (not inventory_path.is_file()
            or _sha256(inventory_path) != config["scene_inventory_sha256"]):
        raise ValueError(
            f"inventory missing or digest mismatch: {inventory_path}")
    config["scene_inventory_path"] = str(inventory_path.resolve())
    inventory = json.loads(inventory_path.read_text())
    manipulated = check_inventory(inventory)
    if "scene_inventory" in config and config["scene_inventory"] != inventory:
        raise ValueError("scene_inventory disagrees with the inventory file")
    config["scene_inventory"] = inventory

    observation = config["observation"]
    if not isinstance(observation, dict) or set(observation) - {
            "object_id", "segmentation_prompt", "min_score"}:
        raise ValueError("unsupported observation keys")
    for key in ("object_id", "segmentation_prompt"):
        if not isinstance(observation.get(key), str) or not observation[key]:
            raise ValueError(f"observation.{key} is required")
    if observation["object_id"] != manipulated:
        raise ValueError(
            "observation.object_id must be the manipulated inventory entity")
    labels = {e["id"]: e["label"] for e in inventory}
    if observation["segmentation_prompt"] != labels[manipulated]:
        raise ValueError(
            "observation.segmentation_prompt must be that entity's label")
    score = observation.get("min_score", 0.5)
    if (not isinstance(score, (int, float)) or isinstance(score, bool)
            or not math.isfinite(score) or not 0 <= score <= 1):
        raise ValueError("min_score must be finite in [0, 1]")
    _digest(config["policy_sha256"], "policy_sha256")
    return config


def load_native_config(args):
    """Parent-side load: validate the config plus the args it must agree with."""
    if not args.replay_code or args.interactive:
        raise ValueError(
            "native world mode requires --args.replay-code and noninteractive")
    if (args.suite, args.task) != (SUITE, TASK):
        raise ValueError(f"native world mode is restricted to {SUITE}/{TASK}")
    if type(args.trial) is not int or args.trial < 0:
        raise ValueError("trial seed must be a nonnegative integer")
    config_path = Path(args.world_model_config).expanduser().resolve()
    config = validate_native_config(
        json.loads(config_path.read_text()), config_path.parent)
    policy = Path(args.replay_code).expanduser()
    if not policy.is_file() or _sha256(policy) != config["policy_sha256"]:
        raise ValueError("policy source missing or digest mismatch")

    output_root = Path(config["output_root"]).expanduser().resolve()
    ordinary = Path(args.output_dir).expanduser().resolve()
    if (output_root.is_relative_to(ordinary)
            or ordinary.is_relative_to(output_root)):
        raise ValueError(
            "native world output_root must be separate from replay output")
    config["output_root"] = str(output_root)

    import yaml
    base = yaml.safe_load(Path(args.config).expanduser().read_text())
    cfg = base.get("env", {}).get("cfg", {})
    if (cfg.get("privileged", False)
            or cfg.get("low_level", {}).get("privileged", False)
            or cfg.get("apis") != ["FrankaLiberoApiReducedSkillLibraryTraced"]):
        raise ValueError(
            "native world replay requires the nonprivileged reduced traced API")
    return config, base


def mechanism_evidence(tape, manifest):
    """Summarize what the world mechanism actually did, with no cap judgment."""
    events = [json.loads(line) for line in tape.read_text().splitlines() if line]
    kinds = [e.get("event") for e in events]
    relations = [e for e in events if e.get("event") == "query_comparison"
                 and e.get("purpose") == "relation"]
    verdicts = [e["comparison"]["status"] for e in relations]
    return {
        "world_reference_established": bool(
            manifest.get("reference_established")),
        "world_relation_measurement_attempts": len(relations),
        "world_relation_verdicts": {
            status: verdicts.count(status)
            for status in ("SUPPORT", "CONTRADICT", "UNKNOWN")
            if verdicts.count(status)},
        "world_program_errors": kinds.count("program_error"),
        "world_action_count": manifest.get("action_count"),
        "world_attempted_actions": manifest.get("attempted_actions"),
        "world_action_limit_denials": manifest.get("action_limit_denials", 0),
        "world_query_used": manifest.get("query_used"),
        "world_recovery_used": manifest.get("recovery_used"),
        "world_verify_calls": kinds.count("world_verify"),
        # An attempted relation measurement that returned UNKNOWN is still
        # evidence that the mechanism ran; only zero attempts is not.
        "world_mechanism_evidence_observed": bool(verdicts),
        "world_relation_purpose_queries": manifest.get(
            "relation_purpose_queries"),
        "world_off_schedule_reference_requests": manifest.get(
            "off_schedule_reference_requests"),
    }


def prepare(args, config):
    """Write the trial directory; return its child_request.json path.

    Reuses _prepare_live for the policy/world/yaml/config/manifest layout so the
    directory shape and the four digests stay identical to the frozen path, then
    adds the inventory as a fourth preserved input with its own digest.
    """
    request_path = _prepare_live(args, config)
    directory = request_path.parent
    inventory_dest = directory / "scene_inventory.json"
    inventory_dest.write_bytes(Path(config["scene_inventory_path"]).read_bytes())
    request = json.loads(request_path.read_text())
    request["schema_version"] = SCHEMA
    _write_json(request_path, request)

    manifest_path = directory / "live_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest.update(
        schema_version=SCHEMA, mode=MODE, partition="native",
        identity_binding=config["identity_binding"],
        query_schedule=config["query_schedule"],
        execution_caps=UNLIMITED,
        scene_inventory_sha256=_sha256(inventory_dest),
        adapter_sha256=_sha256(Path(__file__)),
        relational_adapter_sha256=_sha256(
            Path(sys.modules[RelationalSceneBroker.__module__].__file__)),
        relational_mode_reused=RELATIONAL_MODE)
    _write_json(manifest_path, manifest)
    return request_path


class _ParentSignal(BaseException):
    """A signal delivered to this parent while the child process group runs."""


def _raise_parent_signal(number, _frame):
    raise _ParentSignal(f"parent received signal {number}")


@contextlib.contextmanager
def _signals_as_exceptions():
    """Turn parent SIGTERM/SIGINT into an exception inside ``_launch_child``.

    The child runs in its own process group. Default signal handling would kill
    this parent outright, leaving the group alive and ``child_exit.json``
    unwritten; ``_launch_child`` already catches ``BaseException``, calls
    ``_stop_group``, and records ``interrupted``, so raising here routes an
    outer kill into that existing cleanup path.
    """
    previous = {}
    try:
        for number in (signal.SIGTERM, signal.SIGINT):
            previous[number] = signal.signal(number, _raise_parent_signal)
    except ValueError:  # Not the main thread: leave handlers untouched.
        pass
    try:
        yield
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)


def run_native_world(args):
    """Parent entry point: prepare, launch the isolated child, validate."""
    config, _ = load_native_config(args)
    environment = _child_environment(dict(os.environ))
    request_path = prepare(args, config)
    directory = request_path.parent
    prepared = json.loads((directory / "live_manifest.json").read_text())
    with _signals_as_exceptions():
        receipt = _launch_child(request_path, environment,
                                config["trial_timeout_seconds"],
                                module="cap.world_model.native_world_broker")
    try:
        manifest = json.loads((directory / "live_manifest.json").read_text())
        valid = (
            manifest.get("status") == "complete"
            and manifest.get("mode") == MODE
            and manifest.get("schema_version") == SCHEMA
            and (manifest.get("suite"), manifest.get("task"),
                 manifest.get("seed")) == (SUITE, TASK, args.trial)
            and manifest.get("adapter_sha256") == prepared["adapter_sha256"]
            == _sha256(Path(__file__))
            # The uncapped budgets must survive into the record as the word, so
            # nobody later reads a count where a cap never existed.
            and manifest.get("max_actions") == UNLIMITED
            and manifest.get("query_budget") == UNLIMITED)
        for field, name in [
                ("policy_sha256", "frozen_policy.py"),
                ("world_program_sha256", "world_program.py"),
                ("scene_inventory_sha256", "scene_inventory.json"),
                ("live_config_sha256", "live_config.json"),
                ("yaml_sha256", "source_config.yaml"),
                ("tape_sha256", "live_tape.jsonl")]:
            valid = valid and manifest.get(field) == _sha256(directory / name)
            if field != "tape_sha256":
                valid = valid and manifest.get(field) == prepared.get(field)
        if valid:
            manifest["mechanism_evidence"] = mechanism_evidence(
                directory / "live_tape.jsonl", manifest)
            _write_json(directory / "mechanism_evidence.json",
                        manifest["mechanism_evidence"])
    except (OSError, ValueError, TypeError, KeyError):
        valid = False
    receipt["manifest_valid"] = bool(valid)
    _write_json(directory / "child_exit.json", receipt)
    if receipt["status"] != "completed" or not valid:
        raise RuntimeError(
            "native world trial child failed or evidence incomplete: "
            + str(directory))
    return directory


def _child_main(request_path):
    directory = request_path.parent
    request = json.loads(request_path.read_text())
    if request.get("schema_version") != SCHEMA:
        raise ValueError("unsupported native world child schema")
    manifest = json.loads((directory / "live_manifest.json").read_text())
    config = validate_native_config(
        json.loads((directory / "live_config.json").read_text()), directory)
    # The child re-derives the digests from the files it is about to run and
    # requires the parent's manifest to agree, so a swapped input between
    # prepare and launch cannot pass unnoticed.
    for field, name in [("policy_sha256", "frozen_policy.py"),
                        ("world_program_sha256", "world_program.py"),
                        ("scene_inventory_sha256", "scene_inventory.json")]:
        if manifest.get(field) != _sha256(directory / name):
            raise ValueError(f"{name} does not match the recorded {field}")
    if config["policy_sha256"] != _sha256(directory / "frozen_policy.py"):
        raise ValueError("frozen policy does not match the config digest")

    replay_path = (Path(__file__).resolve().parents[2]
                   / "scripts/libero/replay_trial.py")
    spec = importlib.util.spec_from_file_location(
        "_native_world_replay", replay_path)
    replay = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = replay
    spec.loader.exec_module(replay)
    if os.environ.get("SAM3_SERVICE_URL"):
        from aspire.sim.cap.integrations.vision import sam3
        sam3.SERVICE_URL = os.environ["SAM3_SERVICE_URL"]
    if os.environ.get("PYROKI_SERVICE_URL"):
        from aspire.sim.cap.integrations.motion import pyroki
        pyroki.init_pyroki.__defaults__ = (os.environ["PYROKI_SERVICE_URL"],)

    broker = RelationalSceneBroker(
        directory, config, manifest,
        (directory / "world_program.py").read_text())
    error = None
    try:
        replay._run_replay(
            SimpleNamespace(**request["args"]), _world_capture=broker)
    except BaseException as exc:
        error = type(exc).__name__
        raise
    finally:
        broker.close(error)
    if broker.manifest["status"] != "complete":
        raise RuntimeError("native world mechanism evidence incomplete")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--child-request", type=Path, required=True)
    _child_main(parser.parse_args().child_request)
