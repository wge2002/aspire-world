"""Opt-in scene adapter with budgeted reference and independent relation checks.

The generated world supplies two numerical predictions. This coordinator-owned
adapter meters evidence and applies a fixed, explicit hypothesis comparison rule.
Neither reference fitting nor unknown evidence establishes grasp success.
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

from .live_broker import (SUITE, TASK, _child_environment, _launch_child,
                         _prepare_live, _sha256, _vector, _write_json,
                         budget_exhausted)
from .program import ProgramExecutionError
from .runtime import _copy
from .scene_broker import SceneBroker

MODE = "opus46-relational-scene-diagnostic"
PROFILE = "bowl-on-plate-relational-scene-dev"
POLICY_SHA = "be5a7cb07f8e0d38892bfca4d424da08015efa56b484882aa2b6633a58da7ec2"


def compare_grasp_hypotheses(check, measurement, frame, tolerance, purpose):
    """Compare precommitted alternatives, without fitting the current sample."""
    result = {"status": "UNKNOWN", "reason": None, "error_m": None,
              "attached_error_m": None, "free_error_m": None, "separation_m": None}
    if measurement.get("object_id") != check.get("object_id"):
        # A reading of another entity is not evidence about this one, whatever
        # its numerical agreement with either hypothesis happens to be.
        return dict(result, reason="measurement_identity_mismatch")
    if purpose == "reference":
        return dict(result, reason="reference_only")
    if measurement["status"] != "ok":
        return dict(result, reason="measurement_unavailable")
    reference = check.get("reference_frame")
    attached, free = check.get("attached_position"), check.get("free_position")
    if type(reference) is not int or reference >= frame or attached is None or free is None:
        return dict(result, reason="missing_independent_reference")
    observed = _vector(measurement["position"], 3, "observed position")
    attached = _vector(attached, 3, "attached prediction")
    free = _vector(free, 3, "free prediction")
    distance = lambda x, y: math.sqrt(sum((a-b)**2 for a, b in zip(x, y)))
    ae, fe, separation = distance(attached, observed), distance(free, observed), distance(attached, free)
    result.update(error_m=ae, attached_error_m=ae, free_error_m=fe, separation_m=separation)
    if separation <= tolerance:
        return dict(result, reason="insufficient_motion")
    attached_fits, free_fits = ae <= tolerance, fe <= tolerance
    if attached_fits and not free_fits:
        return dict(result, status="SUPPORT", reason="attached_only_consistent")
    if free_fits and not attached_fits:
        return dict(result, status="CONTRADICT", reason="free_only_consistent")
    return dict(result, reason="both_consistent" if attached_fits else "both_inconsistent")


SCHEDULE = "close_reference_then_policy_verify"


class RelationalSceneBroker(SceneBroker):
    """Schedule: a reference is sampled only at the successful close_gripper frame.

    The schedule itself is unchanged. What is added is feedback: a reference
    requested at any other frame, and a world_verify call at a frame whose
    committed prediction did not request a relation, are recorded as explicit
    off-schedule events instead of looking like a measurement that merely
    returned UNKNOWN.
    """

    def __init__(self, directory, config, manifest, program_source):
        super().__init__(directory, config, manifest, program_source)
        self._reference_frame = None
        self._steps_advanced = 0
        self._references_invalidated = []
        self._closed_frame = None
        self._off_schedule_reference_requests = 0
        self._verify_feedback_frames = set()

    def _drop_reference(self, reason, action=None):
        if self._reference_frame is not None:
            self._references_invalidated.append(reason)
            self._emit("reference_invalidated", reason=reason, action=action,
                       invalidated_frame=self._reference_frame)
        self._reference_frame = None

    def capture(self, action, arguments, *, action_error=None):
        # Invalidate even if proprioception is unavailable after open/close.
        if action in {"open_gripper", "close_gripper"}:
            self._drop_reference("gripper_state_changed", action)
            self._closed_frame = None
        before = self._steps_advanced
        result = super().capture(action, arguments, action_error=action_error)
        if self._initialized and self._steps_advanced == before:
            # The world program was never stepped for this action (missing
            # proprioception or a mechanism failure), so it did not see the
            # motion. A reference calibrated before an unobserved action can no
            # longer support a relation check.
            self._drop_reference("capture_gap", action)
        return result

    def _validate_check(self):
        if self._pending.get("query_purpose") not in {"none", "reference", "relation"}:
            raise ProgramExecutionError("invalid relational query purpose")
        check = self._pending.get("grasp_check")
        if not isinstance(check, dict) or set(check) != {"object_id", "reference_frame", "attached_position", "free_position"}:
            raise ProgramExecutionError("invalid grasp hypothesis record")
        if check["object_id"] != self._object_id:
            raise ProgramExecutionError("grasp identity changed")
        reference = check["reference_frame"]
        if reference is not None and (type(reference) is not int or reference < 0):
            raise ProgramExecutionError("invalid reference frame")
        for name in ["attached_position", "free_position"]:
            if check[name] is not None:
                _vector(check[name], 3, name)

    def _do_step(self, step, action):
        super()._do_step(step, action)
        if self._pending is None:
            # advance/predict failed: the program holds no committed prediction
            # for this action, so this frame does not count as advanced and the
            # caller invalidates any reference calibrated before it.
            return
        self._steps_advanced += 1
        if step.get("action_error") is not None:
            # The pose actually reached is unknown, so a hand-relative reference
            # calibrated earlier can no longer be evaluated at this frame. This
            # does not wait for the policy to call world_verify.
            self._drop_reference("action_error", action)
        try:
            self._validate_check()
        except Exception as exc:
            self._invalidate("validate_hypotheses", action)
            self._emit("program_error", operation="validate_hypotheses", error=str(exc))
            self._pending = None
            return
        closed = action == "close_gripper" and not step.get("action_error")
        if closed:
            self._closed_frame = step["index"]
        wants_reference = self._pending["request_query"] and self._pending["query_purpose"] == "reference"
        if closed and wants_reference:
            self._last_verdict = self._query(step["index"], "reference")
        elif wants_reference:
            # The reference sample is scheduled at the successful close_gripper
            # frame only. A request at any other frame buys nothing, so say so
            # rather than leaving a silent no-op that later reads as UNKNOWN.
            self._off_schedule_reference_requests += 1
            self._emit("reference_request_off_schedule", action=action,
                       prediction_frame=step["index"], query_schedule=SCHEDULE,
                       closed_frame=self._closed_frame,
                       reference_frame=self._reference_frame,
                       action_error=step.get("action_error"),
                       reason=("reference_requested_after_close_frame"
                               if self._closed_frame is not None else
                               "reference_requested_without_successful_close"),
                       required="request query_purpose='reference' on the successful "
                                "close_gripper frame itself; a later frame requests "
                                "query_purpose='relation' naming that reference frame")

    def _query_measurement(self):
        try:
            obs = copy.deepcopy(self._sensor_call("get_observation"))
            return self._scene_measurement(obs, {"id": self._object_id, "label": self._seg_prompt})
        except Exception as exc:
            return {"object_id": self._object_id, "status": "unknown", "position": None,
                    "visible_bounds": None, "reason": "query_error:" + type(exc).__name__}

    def _query(self, frame, purpose):
        # The frame is marked before the budget test as well, so a denied query
        # is decided once per frame instead of re-emitting on every repeated
        # world_verify call at that frame.
        self._queried_frame = frame
        if budget_exhausted(self._query_budget, self._query_used):
            self._emit("query_denied", reason="budget_exhausted", purpose=purpose, prediction_frame=frame)
            return self._unknown("budget_exhausted")
        self._query_used += 1
        reported = self._query_measurement()
        measurement = reported
        if reported.get("object_id") != self._object_id:
            # A measurement of some other entity is not a measurement of this
            # one. It is downgraded to unavailable rather than relabelled, so it
            # can neither verify the relation nor be folded into this object's
            # position. The comparison below is still handed the reported
            # identity, so the mismatch is enforced independently there too.
            self._emit("measurement_identity_mismatch", purpose=purpose,
                       reported_object_id=reported.get("object_id"),
                       expected_object_id=self._object_id, prediction_frame=frame)
            measurement = {"object_id": self._object_id, "status": "unknown",
                           "position": None, "visible_bounds": None,
                           "reason": "measurement_identity_mismatch"}
        check = self._pending["grasp_check"]
        comparison = compare_grasp_hypotheses(check, reported, frame, self._tolerance, purpose)
        released = self._release_axes(measurement, (0, 1, 2))
        self._emit("query_comparison", purpose=purpose, comparison=comparison, measurement=released,
                   measurement_status=measurement["status"], rich_measurement=measurement,
                   query_number=self._query_used, prediction_frame=frame, requested_axes=[0, 1, 2],
                   decision_object=self._object_id, full_scene_prediction=self._pending)
        evidence = {"object_id": self._object_id, "purpose": purpose, "status": measurement["status"],
                    "position": measurement["position"], "visible_bounds": measurement.get("visible_bounds"),
                    "robot_state": self._pending_step["robot_state"], "frame": frame,
                    "reason": measurement.get("reason"), "comparison": comparison}
        verdict = {"status": comparison["status"].lower(), "reason": comparison["reason"],
                   "prediction": check["attached_position"], "free_prediction": check["free_position"],
                   "observed": released["values"], "error_m": comparison["attached_error_m"],
                   "free_error_m": comparison["free_error_m"], "frame": frame,
                   "action": self._pending_step["action"]["api"],
                   "recovery_available": self.recovery_available()}
        try:
            self._state = self.program.call("assimilate", _copy(self._state), _copy(evidence))
            attachment = self._state["objects"][self._object_id]["attachment"]
            expected = {"SUPPORT": "attached", "CONTRADICT": "free"}.get(comparison["status"])
            if expected is not None and attachment != expected:
                raise ProgramExecutionError("evidence did not revise attachment as specified")
            if comparison["status"] == "UNKNOWN" and attachment == "attached":
                raise ProgramExecutionError("unknown evidence cannot confirm attachment")
            self._world_version += 1
            self._emit("world_assimilated", purpose=purpose, state=self._state, evidence=evidence,
                       world_version=self._world_version, prediction_frame=frame)
            if purpose == "reference":
                # Coordinator-side record only. A relation check additionally
                # requires the program to name the same reference frame, so
                # neither side alone can establish a current reference.
                if measurement["status"] == "ok":
                    self._reference_frame = frame
                else:
                    self._drop_reference("reference_measurement_unavailable")
            elif comparison["status"] == "CONTRADICT":
                self._drop_reference("relation_refuted")
        except Exception as exc:
            self._invalidate("assimilate:" + type(exc).__name__)
            self._emit("program_error", operation="assimilate", error=str(exc))
            verdict = self._unknown("assimilation_failed")
        return verdict

    def world_verify(self):
        frame = self._frame - 1
        if self._mechanism_failed or self._pending is None or self._pending_step["index"] != frame:
            verdict = self._unknown("no_current_committed_prediction")
        elif self._queried_frame == frame:
            verdict = _copy(self._last_verdict)
        elif self._pending_step.get("action_error") is not None:
            # The action failed, so the pose actually reached is unknown and no
            # measurement at this frame can verify a hand-relative hypothesis.
            # _do_step already dropped the reference; this repeats the rule at
            # the decision point so it cannot depend on step bookkeeping.
            self._drop_reference("action_failed_at_verify")
            verdict = self._unknown("action_failed_no_valid_relation_evidence")
        elif self._pending_step.get("robot_state") is None:
            verdict = self._unknown("no_measured_pose_for_relation_check")
        elif self._pending["query_purpose"] != "relation" or not self._pending["request_query"]:
            # No measurement was attempted at all. Distinguish that from a real
            # query whose comparison was UNKNOWN, and say what was expected.
            verdict = dict(self._unknown("program_did_not_request_relation_check"),
                           measurement_attempted=False, query_schedule=SCHEDULE,
                           requested_purpose=self._pending["query_purpose"],
                           request_query=self._pending["request_query"],
                           reference_frame=self._reference_frame,
                           guidance="world_verify samples evidence only when this frame's "
                                    "prediction sets request_query=true and "
                                    "query_purpose='relation' naming the reference frame "
                                    "recorded at the successful close_gripper frame")
            if frame not in self._verify_feedback_frames:
                self._verify_feedback_frames.add(frame)
                self._emit("relation_check_not_requested", prediction_frame=frame,
                           action=self._pending_step["action"]["api"],
                           requested_purpose=self._pending["query_purpose"],
                           request_query=self._pending["request_query"],
                           reference_frame=self._reference_frame,
                           closed_frame=self._closed_frame, query_schedule=SCHEDULE,
                           off_schedule_reference_requests=self._off_schedule_reference_requests)
        elif self._reference_frame is None or self._pending["grasp_check"]["reference_frame"] != self._reference_frame:
            verdict = self._unknown("missing_current_grasp_reference")
        else:
            verdict = self._query(frame, "relation")
        self._last_verdict = _copy(verdict)
        self._emit("world_verify", verdict=verdict)
        return _copy(verdict)

    def close(self, error=None):
        purposes, verdicts = {}, {"support": 0, "contradict": 0, "unknown": 0}
        references_ok = 0
        for event in self._events:
            if event["event"] != "query_comparison":
                continue
            purpose = event["purpose"]
            purposes[purpose] = purposes.get(purpose, 0) + 1
            if purpose == "reference" and event.get("measurement_status") == "ok":
                references_ok += 1
            elif purpose == "relation":
                status = event["comparison"]["status"].lower()
                if status in verdicts:
                    verdicts[status] += 1
        self.manifest["query_purpose_counts"] = purposes
        self.manifest["reference_invalidations"] = list(self._references_invalidated)
        self.manifest["steps_advanced"] = self._steps_advanced
        # Real-mechanism evidence: an attempted measurement that returned UNKNOWN
        # is recorded as an attempt, and is not conflated with no measurement.
        self.manifest["reference_purpose_queries"] = purposes.get("reference", 0)
        self.manifest["relation_purpose_queries"] = purposes.get("relation", 0)
        self.manifest["reference_measurements_ok"] = references_ok
        self.manifest["reference_established"] = references_ok > 0
        self.manifest["relation_verdicts"] = verdicts
        self.manifest["off_schedule_reference_requests"] = self._off_schedule_reference_requests
        self.manifest["query_schedule_enforced"] = SCHEDULE
        super().close(error)


def load_relational_config(args):
    if getattr(args, "api_key", None) or not args.replay_code or args.interactive:
        raise ValueError("scene replay requires frozen code, no API key, and noninteractive mode")
    if args.suite != SUITE or args.task != TASK or type(args.trial) is not int or not 51 <= args.trial <= 65:
        raise ValueError("scene profile permits bowl-on-plate development seeds 51-65 only")
    config = json.loads(Path(args.world_model_config).read_text())
    expected = {"schema_version", "mode", "profile", "task_gate", "dev_seeds", "world_program",
                "observation", "query_budget", "tolerance", "max_actions", "max_recovery",
                "trial_timeout_seconds", "output_root", "run_name", "model_provenance",
                "scene_inventory", "identity_binding", "query_schedule", "world_program_sha256"}
    if not isinstance(config, dict) or set(config) != expected:
        raise ValueError("unexpected scene configuration fields")
    checks = {"schema_version": 4, "mode": MODE, "profile": PROFILE,
              "task_gate": {"suite": SUITE, "task": TASK}, "dev_seeds": [51, 65],
              "observation": {"object_id": "bowl", "segmentation_prompt": "bowl", "min_score": .5},
              "query_budget": 4, "tolerance": .03, "max_actions": 30, "max_recovery": 1,
              "trial_timeout_seconds": 900, "identity_binding": "unique_high_score_mask",
              "query_schedule": "close_reference_then_policy_verify"}
    for key, value in checks.items():
        if config.get(key) != value or isinstance(config.get(key), bool):
            raise ValueError("scene configuration differs from frozen protocol: " + key)
    import re
    if not isinstance(config["run_name"], str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", config["run_name"]):
        raise ValueError("run_name must be a single safe path component")
    world = Path(config["world_program"])
    if not world.is_absolute():
        world = Path(args.world_model_config).parent / world
    if (not isinstance(config["world_program_sha256"], str)
            or not re.fullmatch(r"[a-f0-9]{64}", config["world_program_sha256"])
            or not world.is_file() or _sha256(world) != config["world_program_sha256"]):
        raise ValueError("world source differs from the frozen relational program digest")
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


def run_relational_scene(args):
    config, _ = load_relational_config(args)
    environment = _child_environment(dict(os.environ))
    request_path = _prepare_live(args, config)
    directory = request_path.parent
    request = json.loads(request_path.read_text())
    request["schema_version"] = 4
    _write_json(request_path, request)
    path = directory / "live_manifest.json"
    prepared = json.loads(path.read_text())
    prepared.update(schema_version=4, mode=MODE, identity_binding=config["identity_binding"],
                    query_schedule=config["query_schedule"], generation_model_requests_this_trial=0,
                    adapter_sha256=_sha256(Path(__file__)))
    _write_json(path, prepared)
    receipt = _launch_child(request_path, environment, config["trial_timeout_seconds"],
                            module="cap.world_model.relational_scene_broker")
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
    if request.get("schema_version") != 4:
        raise ValueError("unsupported scene child schema")
    replay_path = Path(__file__).resolve().parents[2] / "scripts/libero/replay_trial.py"
    spec = importlib.util.spec_from_file_location("_relational_scene_world_replay", replay_path)
    replay = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = replay
    spec.loader.exec_module(replay)
    if os.environ.get("SAM3_SERVICE_URL"):
        from aspire.sim.cap.integrations.vision import sam3
        sam3.SERVICE_URL = os.environ["SAM3_SERVICE_URL"]
    if os.environ.get("PYROKI_SERVICE_URL"):
        from aspire.sim.cap.integrations.motion import pyroki
        pyroki.init_pyroki.__defaults__ = (os.environ["PYROKI_SERVICE_URL"],)
    broker = RelationalSceneBroker(directory, json.loads((directory / "live_config.json").read_text()),
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
