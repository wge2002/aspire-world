"""Ordinary, world-free execution of ledger-admitted policies (condition A).

The matched A/B/C study needs a condition that runs the same frozen policy
machinery and the same execution budgets as the world conditions while having no
world model at all: no world program, scene inventory, prediction, world sensor
query, assimilation or `world_verify` callable, and no world config field for one
to hide in. This adapter is deliberately separate from fix_loop_scene_broker,
whose config and child both require a world program to exist; instantiating a
fake world broker only to count motor actions would put world structure into A's
evidence and make the conditions harder, not easier, to compare.

The two helpers the policy does keep -- `recovery_available()` and
`use_recovery()` -- are execution-budget accounting, not world inference. They
answer with exactly the Boolean contract the world conditions use, so the
recovery gate is matched across A/B/C rather than silently absent in A.
"""
from __future__ import annotations

import functools
import importlib.util
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from typing import Any

from .live_broker import (MOTOR_ACTIONS, SUITE, TASK, _child_environment, _launch_child,
                          _numeric, _sha256, _write_json)
from .runtime import _copy
from scripts.libero.world_fix_loop_state import Ledger, MODEL, PROTOCOL, read, sha

MODE = "opus46-ordinary-fix-loop"
SCHEMA = 1
RUN_NAME = "ordinary_budget"
FIXED = {
    "schema_version": SCHEMA, "mode": MODE,
    "profile": "bowl-ordinary-policy-one-fifteen-fifty",
    "task_gate": {"suite": SUITE, "task": TASK},
    **{k: PROTOCOL[k] for k in ["max_actions", "max_recovery", "trial_timeout_seconds"]},
}
PROVENANCE = {"policy_generator": MODEL, "model_id": MODEL,
              "generation_context": "native model response; policy frozen by campaign ledger"}
VARIABLE = {"campaign_root", "phase", "index", "identity_sha256", "generation_sha256",
            "policy_sha256", "output_root", "run_name", "model_provenance"}
# Kept local rather than imported from the world adapter: a public-service
# transport failure is an infrastructure fact about the ordinary perception
# services, and A must not depend on the world adapter to name it.
TRANSPORT_MARKERS = ("requests.exceptions.ConnectionError", "httpx.ConnectError",
                     "Connection refused", "requests.exceptions.ReadTimeout",
                     "httpx.ReadTimeout")


def make_config(ledger, phase, index):
    record = ledger.admission(phase, index)
    sources = ledger.sources(record["version"])
    if set(sources) != {"policy"}:
        raise ValueError("ordinary execution requires a policy-only source schema")
    return {**FIXED, "campaign_root": str(ledger.root), "phase": phase, "index": index,
            "identity_sha256": ledger.identity_sha,
            "generation_sha256": sha(ledger.slot(record["version"]) / "result.json"),
            "policy_sha256": sha(sources["policy"]),
            "output_root": str(ledger.trial_dir(phase, index) / "evidence"),
            "run_name": RUN_NAME, "model_provenance": PROVENANCE}


def load_config(args, config=None):
    if getattr(args, "api_key", None) or not args.replay_code or args.interactive:
        raise ValueError("frozen, noninteractive, credential-free replay required")
    # A world config alongside the ordinary one would mean two adapters claim the
    # same trial; refuse instead of letting precedence decide the condition.
    if getattr(args, "world_model_config", None):
        raise ValueError("ordinary condition carries no world-model config")
    config = read(args.ordinary_budget_config) if config is None else config
    if not isinstance(config, dict) or set(config) != set(FIXED) | VARIABLE:
        raise ValueError("unexpected ordinary fix-loop config fields")
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
    # All output paths are derived by the coordinator from the immutable ledger.
    ordinary = Path(args.output_dir).resolve()
    if not ordinary.is_relative_to(ledger.trial_dir(config["phase"], config["index"])):
        raise ValueError("ordinary output outside reserved trial")
    return config, ledger


class OrdinaryBudgetGuard:
    """Counts motor actions and the single recovery for a world-free policy run.

    Attaches through the same replay hook the world brokers use, but performs no
    scene capture, world program call, prediction or verification: the policy
    namespace gains the two budget helpers and nothing else, and ordinary
    perception reaches the reduced API unwrapped.
    """

    def __init__(self, directory: Path, config: dict, manifest: dict):
        self.directory = Path(directory)
        self.config = config
        self.manifest = manifest

        self.env = None
        self.api = None
        self._restorations: list[tuple[Any, bool, Any]] = []
        self._inside = False
        self._frame = 0

        self._max_actions = int(config["max_actions"])
        self._action_count = 0
        # Attempts include the calls the cap refused; _action_count counts only
        # the motor calls that were actually allowed to run, exactly as the world
        # brokers do, so the two conditions' budgets are the same measurement.
        self._action_attempts = 0
        self._action_limit_denials = 0
        self._max_recovery = int(config["max_recovery"])
        self._recovery_used = 0

        self._tape = (self.directory / "ordinary_tape.jsonl").open("x")
        self._events: list[dict] = []
        self._finished = False

    # -- events ----------------------------------------------------------

    def _emit(self, kind: str, **fields: Any) -> None:
        event = _copy({"event_id": len(self._events), "event": kind,
                        "frame": self._frame, **fields})
        self._events.append(event)
        try:
            self._tape.write(
                json.dumps(event, ensure_ascii=False, allow_nan=False) + "\n")
            self._tape.flush()
        except Exception:
            self.manifest.setdefault("broker_errors", []).append(
                f"event_write:frame_{self._frame}")

    # -- env attachment ---------------------------------------------------

    def attach(self, env: Any) -> None:
        self.env = env
        apis = getattr(env, "_apis", None)
        if not isinstance(apis, dict) or len(apis) != 1:
            raise ValueError("guard expects one reduced API on the environment")
        self.api = next(iter(apis.values()))
        for name in ("functions", "get_observation"):
            if not callable(getattr(self.api, name, None)):
                raise ValueError(f"guard API is missing {name}")

        api = self.api
        original_functions = api.functions
        owned = "functions" in vars(api)
        self._restorations.append((api, owned, vars(api).get("functions")))
        guard = self

        def functions():
            bindings = original_functions()
            wrapped = {name: (guard._wrap(fn, name) if name in MOTOR_ACTIONS else fn)
                       for name, fn in bindings.items()}
            wrapped.update(guard._policy_extras())
            return wrapped

        api.functions = functions

    def _policy_extras(self) -> dict:
        """Budget helpers only; no world_verify exists in this condition."""
        return {"recovery_available": self.recovery_available,
                "use_recovery": self.use_recovery}

    def _wrap(self, fn: Any, name: str):
        @functools.wraps(fn)
        def call(*args, **kwargs):
            if self._inside:
                return fn(*args, **kwargs)
            self._action_attempts += 1
            if self._action_attempts > self._max_actions:
                # Refuse the call WITHOUT counting it as an executed action, so
                # action_count stays at the cap. A correctly enforced limit is
                # the policy's own counted budget failure, not a broken runtime.
                self._action_limit_denials += 1
                self._emit("action_limit_denied", action=name,
                           max_actions=self._max_actions,
                           action_count=self._action_count,
                           attempted_actions=self._action_attempts,
                           denied_calls=self._action_limit_denials)
                raise RuntimeError(
                    f"Global motor-action limit ({self._max_actions}) exceeded")
            self._action_count += 1
            self._inside = True
            action_error = None
            try:
                return fn(*args, **kwargs)
            except BaseException as exc:
                action_error = type(exc).__name__
                raise
            finally:
                try:
                    arguments = {
                        f"arg{i}": _numeric(v) for i, v in enumerate(args)}
                    arguments.update(
                        {k: _numeric(v) for k, v in kwargs.items()})
                    self.capture(name, arguments, action_error=action_error)
                except Exception as exc:
                    self.manifest.setdefault("broker_errors", []).append(
                        f"capture:{type(exc).__name__}")
                finally:
                    self._inside = False
        return call

    # -- replay hook ------------------------------------------------------

    def capture(self, action: str, arguments: dict, *,
                action_error: str | None = None) -> None:
        """Record one policy frame. No scene, world or prediction processing."""
        if action == "initial":
            self._emit("trial_reset")
        else:
            self._emit("motor_action", action=action, args=arguments,
                        action_error=action_error,
                        action_count=self._action_count,
                        attempted_actions=self._action_attempts)
        self._frame += 1

    # -- policy-callable functions ----------------------------------------

    def recovery_available(self) -> bool:
        return self._recovery_used < self._max_recovery

    def use_recovery(self) -> bool:
        """Consume the in-episode recovery; report whether it was granted.

        Same contract as the world conditions: a refusal returns False without
        consuming budget and without raising.
        """
        if self._recovery_used >= self._max_recovery:
            self._emit("recovery_denied", reason="recovery_budget_exhausted",
                        recovery_used=self._recovery_used,
                        max_recovery=self._max_recovery)
            return False
        self._recovery_used += 1
        self._emit("recovery_invoked", recovery_number=self._recovery_used)
        return True

    # -- lifecycle --------------------------------------------------------

    def complete(self, **result: Any) -> None:
        self._finished = True
        self.manifest["trial_result"] = result

    def close(self, error: str | None = None) -> None:
        for api, owned, old in reversed(self._restorations):
            if owned:
                api.functions = old
            else:
                vars(api).pop("functions", None)
        self._restorations.clear()
        self._tape.close()
        if self.env is not None and callable(getattr(self.env, "close", None)):
            try:
                self.env.close()
            except Exception as exc:
                self.manifest["cleanup_error"] = type(exc).__name__
        self.manifest.update(
            status=("complete" if self._finished and error is None
                    and not self.manifest.get("broker_errors") else "failed"),
            frame_count=self._frame,
            action_count=self._action_count,
            max_actions=self._max_actions,
            attempted_actions=self._action_attempts,
            action_limit_denials=self._action_limit_denials,
            recovery_used=self._recovery_used,
            max_recovery=self._max_recovery,
            events_count=len(self._events),
            tape_sha256=_sha256(self.directory / "ordinary_tape.jsonl"))
        if error:
            self.manifest["error"] = error
        _write_json(self.directory / "ordinary_manifest.json", self.manifest)


# -- parent entry: prepare directory and launch child --------------------

def _prepare_ordinary(args: Any, config: dict) -> Path:
    directory = Path(config["output_root"]) / config["run_name"] / f"seed_{args.trial}"
    directory.mkdir(parents=True, exist_ok=False)
    policy_path = directory / "frozen_policy.py"
    policy_path.write_bytes(Path(args.replay_code).read_bytes())
    yaml_path = directory / "source_config.yaml"
    yaml_path.write_bytes(Path(args.config).expanduser().read_bytes())
    _write_json(directory / "ordinary_config.json", config)
    manifest = {
        "schema_version": SCHEMA, "mode": MODE, "status": "running",
        "suite": args.suite, "task": args.task, "seed": args.trial,
        "partition": "development",
        "max_actions": config["max_actions"],
        "max_recovery": config["max_recovery"],
        "policy_sha256": _sha256(policy_path),
        "ordinary_config_sha256": _sha256(directory / "ordinary_config.json"),
        "yaml_sha256": _sha256(yaml_path),
        "model_provenance": config["model_provenance"],
        "world_model_present": False,
        "frame_count": 0, "action_count": 0,
        "attempted_actions": 0, "action_limit_denials": 0, "recovery_used": 0,
    }
    _write_json(directory / "ordinary_manifest.json", manifest)
    request = {"schema_version": SCHEMA, "args": {
        "suite": args.suite, "task": args.task, "trial": args.trial,
        "world_model_config": None, "ordinary_budget_config": None,
        "output_dir": str(directory / "replay"),
        "replay_code": str(policy_path), "config": str(yaml_path),
        "model": config["model_provenance"]["model_id"],
        "record_video": bool(args.record_video),
        "debug": bool(getattr(args, "debug", False)), "interactive": False,
    }}
    request_path = directory / "child_request.json"
    _write_json(request_path, request)
    return request_path


def terminal_outcome(directory, ledger, phase, index, child_receipt):
    """Preserve model/program failures; require a real terminal and raw evidence.

    A denied 31st motor call reaches this function as a nonzero sandbox return
    with action_count still at the cap, so it is classified as a counted program
    failure. An absent child terminal is never labelled success or task failure.
    """
    from scripts.libero.paired_bowl_supervisor import artifacts
    directory = Path(directory)
    record = ledger.admission(phase, index)
    result = artifacts(directory, ledger.trial_dir(phase, index), record["seed"], "ordinary")
    result["status"] = "infrastructure_error"
    if not result["valid"]:
        return result
    try:
        manifest = read(directory / "ordinary_manifest.json")
        config = make_config(ledger, phase, index)
        for key, expected in {
            "schema_version": SCHEMA, "mode": MODE, "model_provenance": PROVENANCE,
            "identity_sha256": ledger.identity_sha,
            "generation_sha256": config["generation_sha256"],
            "phase": phase, "version": record["version"],
            "world_model_present": False,
            "adapter_sha256": sha(Path(__file__)),
        }.items():
            if manifest.get(key) != expected:
                raise ValueError("manifest identity mismatch: " + key)
        if read(directory / "ordinary_config.json") != config:
            raise ValueError("ordinary config differs")
        for key, name in [("policy_sha256", "frozen_policy.py"),
                          ("ordinary_config_sha256", "ordinary_config.json"),
                          ("yaml_sha256", "source_config.yaml")]:
            if manifest.get(key) != _sha256(directory / name):
                raise ValueError("child input hash mismatch: " + key)
        if child_receipt.get("status") != "completed":
            raise ValueError("child did not complete")
        if manifest.get("error") or not manifest.get("trial_result"):
            raise ValueError("missing clean replay terminal")
        if manifest.get("status") != "complete":
            raise ValueError("incomplete ordinary budget evidence")
        # The generic "ordinary" artifacts branch checks the trial directory, not
        # this adapter's own tape, so the raw budget evidence is verified here: a
        # missing, truncated or rewritten tape cannot become a valid terminal.
        tape_path = directory / "ordinary_tape.jsonl"
        if not tape_path.is_file():
            raise ValueError("missing ordinary budget tape")
        if manifest.get("tape_sha256") != _sha256(tape_path):
            raise ValueError("ordinary tape differs from recorded digest")
        tape = [json.loads(line) for line in tape_path.read_text().splitlines()]
        if manifest.get("events_count") != len(tape):
            raise ValueError("ordinary tape event count differs")
        denials = sum(e["event"] == "action_limit_denied" for e in tape)
        if manifest.get("action_limit_denials") != denials:
            raise ValueError("recorded motor denials differ from the tape")
        if sum(e["event"] == "recovery_invoked" for e in tape) != manifest.get("recovery_used"):
            raise ValueError("recorded recovery usage differs from the tape")
        summary = (Path(result["trial_dir"]) / "summary.txt").read_text(errors="replace")
        if result["sandbox_rc"] != 0 and any(m in summary for m in TRANSPORT_MARKERS):
            raise ValueError("public service transport failed during policy execution")
        if any(manifest.get(k, 10**9) > cap for k, cap in
               [("action_count", PROTOCOL["max_actions"]),
                ("recovery_used", PROTOCOL["max_recovery"])]):
            raise ValueError("execution budget exceeded")
        result.update(
            world_model_present=False,
            ordinary_action_count=manifest.get("action_count"),
            ordinary_attempted_actions=manifest.get("attempted_actions"),
            ordinary_action_limit_denials=manifest.get("action_limit_denials"),
            ordinary_recovery_used=manifest.get("recovery_used"),
            ordinary_frame_count=manifest.get("frame_count"),
            ordinary_manifest=str(directory / "ordinary_manifest.json"))
        result["status"] = ("program_error" if result["sandbox_rc"] != 0 else
                            "success" if result["task_completed"] else "task_failure")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        result["validation_error"] = str(exc)
    return result


def run_ordinary_fix_loop(args: Any) -> None:
    config, ledger = load_config(args)
    request_path = _prepare_ordinary(args, config)
    directory = request_path.parent
    path = directory / "ordinary_manifest.json"
    manifest = read(path)
    record = ledger.admission(config["phase"], config["index"])
    manifest.update(phase=config["phase"], version=record["version"],
                    partition="heldout" if config["phase"] == "heldout" else "development",
                    identity_sha256=ledger.identity_sha,
                    generation_sha256=config["generation_sha256"],
                    generation_model_requests_this_trial=0,
                    adapter_sha256=sha(Path(__file__)))
    _write_json(path, manifest)
    receipt = _launch_child(request_path, _child_environment(dict(os.environ)),
                            config["trial_timeout_seconds"],
                            module="cap.world_model.ordinary_fix_loop_guard")
    result = terminal_outcome(directory, ledger, config["phase"], config["index"], receipt)
    _write_json(directory / "outcome.json", result)
    if result["status"] == "infrastructure_error":
        raise RuntimeError("incomplete infrastructure evidence: " + str(directory))


# -- child entry ---------------------------------------------------------

def _child_main(request_path: Path) -> None:
    directory = request_path.parent
    request, config, manifest = (read(request_path), read(directory / "ordinary_config.json"),
                                read(directory / "ordinary_manifest.json"))
    if request["schema_version"] != SCHEMA:
        raise ValueError("unsupported child schema")
    args = SimpleNamespace(**request["args"])
    load_config(args, config)
    for key, name in [("policy_sha256", "frozen_policy.py"),
                      ("ordinary_config_sha256", "ordinary_config.json"),
                      ("yaml_sha256", "source_config.yaml")]:
        if manifest[key] != _sha256(directory / name):
            raise ValueError("child input hash mismatch: " + key)
    replay_path = Path(__file__).resolve().parents[2] / "scripts/libero/replay_trial.py"
    spec = importlib.util.spec_from_file_location("_ordinary_fix_loop_replay", replay_path)
    replay = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = replay
    spec.loader.exec_module(replay)
    if os.environ.get("SAM3_SERVICE_URL"):
        from aspire.sim.cap.integrations.vision import sam3
        sam3.SERVICE_URL = os.environ["SAM3_SERVICE_URL"]
    if os.environ.get("PYROKI_SERVICE_URL"):
        from aspire.sim.cap.integrations.motion import pyroki
        pyroki.init_pyroki.__defaults__ = (os.environ["PYROKI_SERVICE_URL"],)
    guard = OrdinaryBudgetGuard(directory, config, manifest)
    error = None
    try:
        replay._run_replay(args, _world_capture=guard)
    except BaseException as exc:
        error = type(exc).__name__
        raise
    finally:
        guard.close(error)
    # A policy failure (including a refused 31st motor call) is an experimental
    # failure, not a missing simulator terminal. The parent classifies it.


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--child-request", type=Path, required=True)
    _child_main(parser.parse_args().child_request.resolve())
