"""Opt-in online adapter for the frozen Opus 4.6 multi-object scene program.

The generated source is not rewritten. This adapter builds current public
measurements, translates interfaces, and meters bowl queries at policy gates.
The scheduler and identity binding are coordinator code, not model discoveries.
"""
from __future__ import annotations

import copy
import importlib.util
import json
import math
import os
from pathlib import Path
import sys
from types import SimpleNamespace

from .live_broker import (
    LiveBroker, SUITE, TASK, _child_environment, _launch_child, _numeric,
    _prepare_live, _sha256, _vector, _write_json, budget_exhausted,
)
from .program import ProgramExecutionError
from .runtime import _copy


MODE = "opus46-scene-diagnostic"
PROFILE = "bowl-on-plate-scene-dev"
WORLD_SHA = "f0f5acd2488723fa8ae2559fb83d436573e7d2b6b1e93c64c88bbb3a85781794"
POLICY_SHA = "be5a7cb07f8e0d38892bfca4d424da08015efa56b484882aa2b6633a58da7ec2"


class SceneBroker(LiveBroker):
    def __init__(self, directory, config, manifest, program_source):
        super().__init__(directory, config, manifest, program_source)
        self._inventory = _copy(config["scene_inventory"])
        self._entity_ids = {e["id"] for e in self._inventory}
        self._pending = None
        self._pending_step = None
        self._queried_frame = None

    def _scene_measurement(self, obs, entity):
        """One unique high-score mask; no coordinates from another image."""
        import numpy as np

        measured = {"object_id": entity["id"], "status": "unknown", "position": None,
                    "visible_bounds": None, "reason": "not_measured"}
        try:
            cam = obs["agentview"]
            masks = self._sensor_call("segment_sam3_text_prompt", cam["images"]["rgb"], entity["label"])
            eligible = []
            for item in masks:
                score = item.get("score", 0)
                mask = item.get("mask")
                if (isinstance(score, (float, int)) and math.isfinite(score)
                        and score >= self._min_score and mask is not None and np.asarray(mask).any()):
                    eligible.append(item)
            measured["eligible_count"] = len(eligible)
            if len(eligible) != 1:
                measured["reason"] = "ambiguous_segmentation" if eligible else "no_eligible_mask"
                return measured
            item = eligible[0]
            points = np.asarray(self._sensor_call("mask_to_world_points", item["mask"],
                cam["images"]["depth"], cam["intrinsics"], cam["pose_mat"]), dtype=float)
            if points.ndim != 2 or points.shape[1] != 3:
                measured["reason"] = "invalid_point_shape"
                return measured
            points = points[np.isfinite(points).all(axis=1)]
            if len(points) < 20:
                measured["reason"] = "insufficient_finite_depth"
                return measured
            measured.update(status="ok", position=np.median(points, axis=0).tolist(),
                visible_bounds=np.quantile(points, [.02, .98], axis=0).tolist(),
                point_count=len(points), mask_score=float(item["score"]), reason=None,
                geometry_semantics="visible_surface_quantiles; not full object geometry",
                uncertainty={"hidden_geometry": "unknown", "mass": "unspecified_simplified_parameter",
                             "statistical_coverage": "uncalibrated"})
            measured["matched_image_box"] = _numeric(item.get("box"))
        except Exception as exc:
            measured["reason"] = "measurement_error:" + type(exc).__name__
        return measured

    def _do_initialize(self, step):
        if self._initialization_attempted:
            self._invalidate("initialize_already_attempted")
            return
        self._initialization_attempted = True
        try:
            obs = copy.deepcopy(self._sensor_call("get_observation"))
            entities = [dict(item, measurement=self._scene_measurement(obs, item)) for item in self._inventory]
            cam = obs["agentview"]
            context = {"schema": 1, "coordinate_frame": "public_API_shared_robot_base_frame",
                       "task": "put the bowl on the plate", "entities": entities,
                       "robot_state": step["robot_state"], "budget": self._budget_snapshot(),
                       "camera": {"intrinsics": _numeric(cam["intrinsics"]),
                                  "camera_to_reference": _numeric(cam["pose_mat"])},
                       "uncertainty": "visible geometry only; no exact mass, full shape or contact truth"}
            self._emit("scene_anchor_committed", context=context,
                       identity_binding="unique_score_ge_0.5; frozen semantic catalog; no stale image box")
            self._state = self.program.call("initialize", context)
            self._initialized = True
            self.manifest["scene_anchor_objects"] = len(entities)
            self.manifest["scene_anchor_known"] = sum(e["measurement"]["status"] == "ok" for e in entities)
            self.manifest["scene_anchor_unknown"] = [e["id"] for e in entities if e["measurement"]["status"] != "ok"]
            self._emit("world_initialized", state=self._state,
                       anchor_object_count=len(entities), world_version=self._world_version)
        except Exception as exc:
            self._invalidate("initialize:" + type(exc).__name__)
            self._emit("program_error", operation="initialize", error=str(exc))

    def _do_step(self, step, action):
        self._pending = None
        self._pending_step = None
        self._last_verdict = self._unknown("awaiting_policy_verification", action)
        full_step = {**step, "action": {"api": action, "arguments": step["action"].get("args", {})},
                     "budget": self._budget_snapshot()}
        try:
            self._state = self.program.call("advance", _copy(self._state), _copy(full_step))
            self._world_version += 1
            self._emit("world_advanced", action=action, state=self._state, world_version=self._world_version)
            # predict is read-only: it receives a detached copy of the state and
            # only its returned prediction is used, so any mutation the program
            # makes to that argument is discarded. State persists solely through
            # what advance (above) and assimilate return.
            raw = self.program.call("predict", _copy(self._state), _copy(full_step))
            if not isinstance(raw, dict) or not isinstance(raw.get("objects"), dict):
                raise ProgramExecutionError("scene prediction must contain objects")
            if set(raw["objects"]) != self._entity_ids or type(raw.get("request_query")) is not bool:
                raise ProgramExecutionError("scene prediction entity set or query flag invalid")
            for item in raw["objects"].values():
                if not isinstance(item, dict) or "position" not in item:
                    raise ProgramExecutionError("scene object prediction must contain position")
                if item["position"] is not None:
                    _vector(item["position"], 3, "object prediction")
            self._pending = _copy(raw)
            self._pending_step = _copy(full_step)
            self._emit("prediction_committed", prediction=self._pending,
                       world_version=self._world_version, query_schedule="policy_world_verify_only")
        except Exception as exc:
            self._invalidate("advance_predict:" + type(exc).__name__, action)
            self._emit("program_error", operation="advance_predict", error=str(exc))

    def _unknown(self, reason, action=None):
        return {"status": "unknown", "prediction": None, "observed": None,
                "error_m": None, "reason": reason, "action": action,
                "frame": self._frame, "recovery_available": self.recovery_available()}

    def world_verify(self):
        frame = self._frame - 1
        if self._mechanism_failed or self._pending is None or self._pending_step["index"] != frame:
            verdict = self._unknown("no_current_committed_prediction")
        elif self._queried_frame == frame:
            verdict = _copy(self._last_verdict)
        elif not self._pending["request_query"]:
            verdict = self._unknown("program_did_not_request_query")
        elif budget_exhausted(self._query_budget, self._query_used):
            self._emit("query_denied", reason="budget_exhausted", prediction_frame=frame)
            verdict = self._unknown("budget_exhausted")
        else:
            self._queried_frame = frame
            self._query_used += 1
            position = self._pending["objects"][self._object_id]["position"]
            measurement = self._run_object_query()
            released = self._release_axes(measurement, (0, 1, 2))
            comparison = self._compare({"position": position}, released, (0, 1, 2))
            self._emit("query_comparison", comparison=comparison, measurement=released,
                       measurement_status=measurement["status"], query_number=self._query_used,
                       requested_axes=[0, 1, 2], prediction_frame=frame,
                       decision_object=self._object_id, full_scene_prediction=self._pending)
            verdict = {"status": comparison["status"].lower(), "prediction": position,
                       "observed": released["values"], "observed_axes": [0, 1, 2],
                       "error_m": comparison.get("error_m"), "frame": frame,
                       "action": self._pending_step["action"]["api"],
                       "recovery_available": self.recovery_available()}
            evidence = {"object_id": self._object_id, "status": measurement["status"],
                        "position": released["values"]}
            try:
                self._state = self.program.call("assimilate", _copy(self._state), _copy(evidence))
                self._world_version += 1
                self._emit("world_assimilated", state=self._state, evidence=evidence,
                           world_version=self._world_version, prediction_frame=frame)
            except Exception as exc:
                self._invalidate("assimilate:" + type(exc).__name__)
                self._emit("program_error", operation="assimilate", error=str(exc))
                verdict = self._unknown("assimilation_failed")
        self._last_verdict = _copy(verdict)
        self._emit("world_verify", verdict=verdict)
        return _copy(verdict)


def load_scene_config(args):
    if getattr(args, "api_key", None) or not args.replay_code or args.interactive:
        raise ValueError("scene replay requires frozen code, no API key, and noninteractive mode")
    if args.suite != SUITE or args.task != TASK or type(args.trial) is not int or not 51 <= args.trial <= 65:
        raise ValueError("scene profile permits bowl-on-plate development seeds 51-65 only")
    config = json.loads(Path(args.world_model_config).read_text())
    expected = {"schema_version", "mode", "profile", "task_gate", "dev_seeds", "world_program",
                "observation", "query_budget", "tolerance", "max_actions", "max_recovery",
                "trial_timeout_seconds", "output_root", "run_name", "model_provenance",
                "scene_inventory", "identity_binding", "query_schedule"}
    if not isinstance(config, dict) or set(config) != expected:
        raise ValueError("unexpected scene configuration fields")
    checks = {"schema_version": 3, "mode": MODE, "profile": PROFILE,
              "task_gate": {"suite": SUITE, "task": TASK}, "dev_seeds": [51, 65],
              "observation": {"object_id": "bowl", "segmentation_prompt": "bowl", "min_score": .5},
              "query_budget": 4, "tolerance": .03, "max_actions": 30, "max_recovery": 1,
              "trial_timeout_seconds": 900, "identity_binding": "unique_high_score_mask",
              "query_schedule": "policy_world_verify_only"}
    for key, value in checks.items():
        if config.get(key) != value or isinstance(config.get(key), bool):
            raise ValueError("scene configuration differs from frozen protocol: " + key)
    import re
    if not isinstance(config["run_name"], str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", config["run_name"]):
        raise ValueError("run_name must be a single safe path component")
    world = Path(config["world_program"])
    if not world.is_absolute():
        world = Path(args.world_model_config).parent / world
    if not world.is_file() or _sha256(world) != WORLD_SHA:
        raise ValueError("world source must be the exact frozen Opus 4.6 scene program")
    if _sha256(Path(args.replay_code)) != POLICY_SHA:
        raise ValueError("policy source must match the frozen Opus 4.6 shared policy")
    config["world_program"] = str(world.resolve())
    prov = config["model_provenance"]
    if not isinstance(prov, dict) or set(prov) != {"model_id", "policy_generator", "world_program_generator", "generation_context"}:
        raise ValueError("invalid provenance fields")
    if any(prov[k] != "claude-opus-4-6" for k in ("model_id", "policy_generator", "world_program_generator")):
        raise ValueError("scene diagnostic requires Opus 4.6 provenance")
    inventory = config["scene_inventory"]
    if not isinstance(inventory, list) or not 2 <= len(inventory) <= 12:
        raise ValueError("scene inventory must contain 2-12 entities")
    ids = set()
    for entity in inventory:
        if not isinstance(entity, dict) or set(entity) != {"id", "label", "role", "confidence", "shape_prior"}:
            raise ValueError("scene entities must contain only semantic metadata")
        if (not all(isinstance(v, str) and v for v in entity.values())
                or not entity["id"].replace("_", "").isalnum() or entity["id"] in ids
                or len(entity["label"]) > 160 or entity["role"] not in {"manipulated", "target", "context"}):
            raise ValueError("invalid scene entity")
        ids.add(entity["id"])
    if [e["id"] for e in inventory if e["role"] == "manipulated"] != ["bowl"] or [e["id"] for e in inventory if e["role"] == "target"] != ["plate"]:
        raise ValueError("scene roles must identify bowl and plate")
    output = Path(config["output_root"]).expanduser().resolve()
    ordinary = Path(args.output_dir).expanduser().resolve()
    if output.is_relative_to(ordinary) or ordinary.is_relative_to(output):
        raise ValueError("scene evidence output must be separate from ordinary replay output")
    config["output_root"] = str(output)
    import yaml
    base = yaml.safe_load(Path(args.config).read_text())
    cfg = base.get("env", {}).get("cfg", {})
    if cfg.get("privileged", False) or cfg.get("low_level", {}).get("privileged", False) or cfg.get("apis") != ["FrankaLiberoApiReducedSkillLibraryTraced"]:
        raise ValueError("scene replay requires the nonprivileged reduced traced API")
    return config, base


def run_scene(args):
    config, _ = load_scene_config(args)
    environment = _child_environment(dict(os.environ))
    request_path = _prepare_live(args, config)
    directory = request_path.parent
    request = json.loads(request_path.read_text())
    request["schema_version"] = 3
    _write_json(request_path, request)
    path = directory / "live_manifest.json"
    prepared = json.loads(path.read_text())
    prepared.update(schema_version=3, mode=MODE, identity_binding=config["identity_binding"],
                    query_schedule=config["query_schedule"], generation_model_requests_this_trial=0,
                    adapter_sha256=_sha256(Path(__file__)))
    _write_json(path, prepared)
    receipt = _launch_child(request_path, environment, config["trial_timeout_seconds"],
                            module="cap.world_model.scene_broker")
    try:
        manifest = json.loads(path.read_text())
        valid = (manifest.get("status") == "complete" and manifest.get("mode") == MODE
                 and (manifest.get("suite"), manifest.get("task"), manifest.get("seed")) == (SUITE, TASK, args.trial)
                 and manifest.get("adapter_sha256") == prepared["adapter_sha256"] == _sha256(Path(__file__)))
        for field, name in [("policy_sha256", "frozen_policy.py"), ("world_program_sha256", "world_program.py"),
                            ("live_config_sha256", "live_config.json"), ("yaml_sha256", "source_config.yaml"),
                            ("tape_sha256", "live_tape.jsonl")]:
            valid = valid and manifest.get(field) == _sha256(directory / name)
            if field != "tape_sha256":
                valid = valid and manifest.get(field) == prepared.get(field)
    except (OSError, ValueError, TypeError):
        valid = False
    receipt["manifest_valid"] = bool(valid)
    _write_json(directory / "child_exit.json", receipt)
    if receipt["status"] != "completed" or not valid:
        raise RuntimeError("scene trial child failed or evidence incomplete: " + str(directory))


def _child_main(request_path):
    directory = request_path.parent
    request = json.loads(request_path.read_text())
    if request.get("schema_version") != 3:
        raise ValueError("unsupported scene child schema")
    replay_path = Path(__file__).resolve().parents[2] / "scripts/libero/replay_trial.py"
    spec = importlib.util.spec_from_file_location("_scene_world_replay", replay_path)
    replay = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = replay
    spec.loader.exec_module(replay)
    if os.environ.get("SAM3_SERVICE_URL"):
        from aspire.sim.cap.integrations.vision import sam3
        sam3.SERVICE_URL = os.environ["SAM3_SERVICE_URL"]
    if os.environ.get("PYROKI_SERVICE_URL"):
        from aspire.sim.cap.integrations.motion import pyroki
        pyroki.init_pyroki.__defaults__ = (os.environ["PYROKI_SERVICE_URL"],)
    broker = SceneBroker(directory, json.loads((directory / "live_config.json").read_text()),
                         json.loads((directory / "live_manifest.json").read_text()),
                         (directory / "world_program.py").read_text())
    error = None
    try:
        replay._run_replay(SimpleNamespace(**request["args"]), _world_capture=broker)
    except BaseException as exc:
        error = type(exc).__name__
        raise
    finally:
        broker.close(error)
    if broker.manifest["status"] != "complete":
        raise RuntimeError("scene world mechanism evidence incomplete")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--child-request", type=Path, required=True)
    _child_main(parser.parse_args().child_request.resolve())
