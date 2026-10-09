"""Opt-in shadow evaluation of two world-condition programs on one trial.

Neither program controls the robot. A fixed, broker-owned phase schedule decides
when one public measurement is taken; every variant commits its prediction
before that measurement is released, and the numerical comparison is written to
the tape before any assimilation is attempted. Comparisons report residuals,
candidate compatibility and distinguishability with explicit reasons. They never
impose an attachment label on a program's state, and an assimilation failure
never erases a completed comparison.

The legacy paths are untouched. ``initialize/advance/predict/assimilate`` keep
their meanings, the frozen diagnostics and the native fix loop keep their modes
and schemas, and this module adds a new mode plus a new schema instead of
redefining any recorded verdict or state semantics. A variant that only supports
the grasp phase is recorded as *not requesting* the later checks -- never as an
error, and never quietly dropped from the like-for-like comparison.
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import math
import os
import re
import sys
from pathlib import Path
from types import SimpleNamespace

from .live_broker import (SUITE, TASK, UNLIMITED, _child_environment,
                          _launch_child, _numeric, _prepare_live, _sha256,
                          _vector, _write_json)
from .program import ProgramExecutionError
from .relational_scene_broker import compare_grasp_hypotheses
from .runtime import _copy
from .scene_broker import SceneBroker

MODE = "cc-world-condition-shadow-diagnostic"
SCHEMA = 8
IDENTITY_BINDING = "unique_high_score_mask"

# Fixed before execution and owned by the broker, not by either program: no
# variant can widen or narrow when a sample is taken, so both are scored on the
# same opportunities.
SHADOW_SCHEDULE = ("broker_phase_schedule_v1:"
                   "initial_anchor,close_reference,closed_phase_motion_relation,"
                   "open_release,final_post_release")
MOTION_ACTIONS = ("move_to_joints", "goto_pose", "goto_home_joint_position")
# Deliberately not called "settled": this frame observes the post-release state,
# it does not establish that the object has physically settled.
POST_RELEASE_ACTION = "post_release_observation"

# Purposes the broker schedules. A variant that does not support one of them is
# recorded as not having requested it (rule: no forced label, no silent drop).
PURPOSES = ("reference", "relation", "release", "post_release")
SUPPORTED_PURPOSES = "supported_purposes"

# Scored purposes are fixed before execution. ``post_release`` is deliberately
# absent: after open_gripper the policy takes no further motor action, so the
# synthetic final frame shares the release frame's physical sample and cannot be
# a new independent observation. It stays an unscored diagnostic.
SCORED_PURPOSES = ("relation", "release")
EVIDENCE_CLASSES = ("calibration", "scored", "unscored_diagnostic")

# Why a scored comparison produced no identification. Kept distinct from the
# frozen relational taxonomy so no historical verdict is redefined.
APPLICABILITY = ("applicable", "not_requested", "purpose_unsupported",
                 "no_committed_prediction", "variant_degraded",
                 "reference_invalidated")


def _distance(a, b):
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def _counts(values):
    out = {}
    for value in values:
        out[value] = out.get(value, 0) + 1
    return out


# Which precommitted candidate plays the "object is still held by the gripper"
# role, per purpose. Pairing residuals across variants is only like-for-like if
# the same hypothesis is compared, and candidate names differ by purpose. An
# empty tuple means the purpose has no attached hypothesis to pair.
ATTACHED_CANDIDATE = {
    "reference": (),
    "relation": ("attached",),
    "release": ("still_carried", "attached"),
    "post_release": ("moved_with_gripper",),
}


def attached_residual(comparison, purpose):
    """Residual of the attached hypothesis, or ``None`` if it has no residual.

    Reads only ``residuals`` (the scored field), never ``diagnostic_residuals``,
    so an unscored sample can never contribute to a paired mean.
    """
    residuals = (comparison or {}).get("residuals") or {}
    for name in ATTACHED_CANDIDATE.get(purpose, ()):
        if residuals.get(name) is not None:
            return name, residuals[name]
    return None, None


def _residuals(candidates, observed):
    """Per-candidate distance to the reading; ``None`` for abstentions."""
    out, named = {}, {}
    for name, position in candidates.items():
        if position is None:
            out[name] = None
            continue
        point = _vector(position, 3, f"candidate {name}")
        named[name] = point
        out[name] = _distance(point, observed)
    return out, named


def compare_candidates(candidates, measurement, tolerance, *, purpose,
                       scored, reference_frame, frame, expected_object_id=None,
                       evidence_class=None):
    """Numerical residuals for precommitted candidates. No attachment label.

    Absolute compatibility and mutual distinguishability are separate fields and
    are both always evaluated: two predictions that are indistinguishable from
    each other can still both be refuted by the reading, and that stays
    MODEL_MISMATCH. The caller records this verbatim; nothing here writes to a
    world state, and the result never says which physical condition holds.
    """
    if evidence_class is None:
        evidence_class = "scored" if scored else "calibration"
    out = {"status": "UNDETERMINED", "reason": None, "residuals": {},
           "diagnostic_residuals": {},
           "compatible": [], "incompatible": [], "min_separation_m": None,
           "distinguishable": None, "tolerance_m": tolerance,
           "candidate_count": len(candidates), "scored": bool(scored),
           "evidence_class": evidence_class,
           "purpose": purpose, "reference_frame": reference_frame,
           "expected_object_id": expected_object_id,
           "measured_object_id": measurement.get("object_id")}
    if not candidates:
        return dict(out, reason="no_candidates_committed")
    # Identity first: a reading of another entity is not evidence about this one,
    # whatever its numerical agreement with either candidate happens to be.
    measured_id = measurement.get("object_id")
    if measured_id is None or (expected_object_id is not None
                               and measured_id != expected_object_id):
        return dict(out, reason="measurement_identity_mismatch")
    observed = None
    if measurement.get("status") == "ok" and measurement.get("position") is not None:
        observed = _vector(measurement["position"], 3, "observed position")
        out["diagnostic_residuals"] = _residuals(candidates, observed)[0]
    if not scored:
        # Calibration data, or a sample that is not new physical evidence.
        # Deliberately produces no scored verdict: a reference cannot verify
        # itself, and a re-read at an unchanged physical state is not a new draw.
        return dict(out, reason=("reference_only" if purpose == "reference"
                                 else "unscored_diagnostic_sample"))
    if observed is None:
        # Absence of a reading is not evidence against any candidate.
        return dict(out, reason="measurement_unavailable")
    if type(reference_frame) is not int or reference_frame >= frame:
        return dict(out, reason="missing_independent_reference")
    out["residuals"], named = _residuals(candidates, observed)
    if len(named) < 2:
        return dict(out, reason="fewer_than_two_numerical_candidates")
    separations = [_distance(named[a], named[b])
                   for i, a in enumerate(sorted(named))
                   for b in sorted(named)[i + 1:]]
    out["min_separation_m"] = min(separations)
    out["distinguishable"] = out["min_separation_m"] > tolerance
    # Absolute fit is evaluated regardless of separation.
    out["compatible"] = sorted(n for n in named
                               if out["residuals"][n] <= tolerance)
    out["incompatible"] = sorted(n for n in named
                                 if out["residuals"][n] > tolerance)
    if not out["compatible"]:
        # Every precommitted alternative is refuted: the model is wrong, which
        # is not the same as evidence for any of its alternatives. This holds
        # even when the alternatives are too close to tell apart.
        return dict(out, status="MODEL_MISMATCH", reason="all_candidates_refuted")
    if not out["distinguishable"]:
        return dict(out, reason="candidates_not_separated")
    if len(out["compatible"]) == 1:
        return dict(out, status="IDENTIFIED",
                    reason="single_compatible_candidate")
    return dict(out, status="AMBIGUOUS", reason="multiple_compatible_candidates")


class ShadowVariant:
    """One world program under shadow evaluation. Holds no scoring authority.

    ``source_sha256`` identifies the program text; ``state_revision`` counts how
    many times its state was replaced. The two are recorded separately so a
    variant that never revises its state is distinguishable from one whose
    source differs, and ``errors`` is append-only so no later failure can erase
    an earlier one.
    """

    def __init__(self, name, source, program, *, supported_purposes, provenance):
        self.name = name
        self.source_sha256 = hashlib.sha256(source.encode()).hexdigest()
        self.program = program
        self.supported_purposes = tuple(supported_purposes)
        self.provenance = dict(provenance)
        self.state = None
        self.state_revision = 0
        self.initialized = False
        self.degraded = False
        self.errors: list[dict] = []
        self.pending = None
        self.pending_frame = None
        self.comparisons: list[dict] = []

    def fail(self, operation, exc, frame):
        record = {"variant": self.name, "operation": operation,
                  "error_type": type(exc).__name__, "error": str(exc),
                  "frame": frame, "state_revision": self.state_revision}
        self.errors.append(record)
        if operation in ("initialize", "advance", "predict"):
            # Prediction-side failures leave this variant with nothing to score
            # at later frames; assimilation failures do not, and specifically do
            # not invalidate the comparison already recorded for this frame.
            self.degraded = True
        return record

    def revise(self, state):
        self.state = state
        self.state_revision += 1

    def summary(self):
        return {"variant": self.name, "source_sha256": self.source_sha256,
                "state_revision": self.state_revision,
                "supported_purposes": list(self.supported_purposes),
                "provenance": self.provenance, "degraded": self.degraded,
                "program_errors": len(self.errors),
                "program_error_operations": sorted(
                    {e["operation"] for e in self.errors})}


class ShadowConditionBroker(SceneBroker):
    """Shadow-evaluates several world programs on one policy execution.

    Neither variant controls the robot: the policy's motor and decision code is
    untouched, ``world_verify`` returns a fixed neutral verdict that carries no
    measurement, and every scheduled sample is taken by the broker on its own
    fixed schedule. One measurement per scheduled frame is shared by all
    variants under one evidence id, after every variant has committed.
    """

    def __init__(self, directory, config, manifest, program_source):
        super().__init__(directory, config, manifest, program_source)
        from .program import FrozenPythonProgram

        self._variants = []
        for spec in config["variants"]:
            source = Path(spec["world_program"]).read_text()
            digest = hashlib.sha256(source.encode()).hexdigest()
            if digest != spec["world_sha256"]:
                raise ValueError(
                    f"variant {spec['name']} world digest mismatch")
            self._variants.append(ShadowVariant(
                spec["name"], source,
                FrozenPythonProgram(source, timeout_s=2.0),
                supported_purposes=spec[SUPPORTED_PURPOSES],
                provenance=spec["provenance"]))
        if len({v.name for v in self._variants}) != len(self._variants):
            raise ValueError("variant names must be unique")

        self._target_id = config["observation"].get("target_object_id")
        self._phase = "pre_grasp"
        self._reference = None
        # The grasp calibration as it stood immediately before open_gripper.
        # Release predictions legitimately depend on this history, so it is kept
        # separately from ``_reference``, which means "a currently valid grasp
        # calibration" and is dropped by the gripper transition.
        self._release_reference = None
        self._reference_invalidations = []
        # (evidence_id, variant) -> first use index. Repeated comparisons by one
        # variant against one physical sample are the same evidence.
        self._evidence_uses = {}
        self._shared_measurements = []
        self._verify_calls = 0
        self._post_release_done = False
        self._schedule = SHADOW_SCHEDULE
        self._steps_advanced = 0
        # One physical epoch per captured motor frame. A synthetic frame that
        # steps no simulator shares the epoch of the frame before it.
        self._physical_epoch = 0
        self._last_scored_evidence_id = None
        # Added sensing cost, separated so inherited query metadata cannot read
        # as zero: scene samples, per-entity readouts, per-variant comparisons.
        self._scene_samples = 0
        self._entity_readouts = 0
        self._anchor_scene_samples = 0
        self._anchor_entity_readouts = 0
        self._variant_comparisons = 0
        seed = manifest.get("seed") if isinstance(manifest, dict) else None
        self._seed = seed
        self._trial_uid = "{}:{}:seed{}".format(
            (manifest or {}).get("suite", SUITE),
            (manifest or {}).get("task", TASK), seed)

    # -- reference lifecycle ---------------------------------------------

    def _drop_reference(self, reason, action=None, *, keep_for_release=False):
        if self._reference is not None:
            if keep_for_release:
                # Historical calibration for the release check only, with its
                # own explicit applicability contract (see _calibration_for).
                self._release_reference = dict(self._reference,
                                               role="release_history",
                                               retained_at_action=action)
            self._reference_invalidations.append(
                {"reason": reason, "action": action,
                 "reference_frame": self._reference["frame"],
                 "retained_for_release": bool(keep_for_release)})
            self._emit("shadow_reference_invalidated", reason=reason,
                       action=action, retained_for_release=bool(keep_for_release),
                       reference_frame=self._reference["frame"])
        self._reference = None

    def _drop_release_reference(self, reason, action=None):
        if self._release_reference is not None:
            self._reference_invalidations.append(
                {"reason": reason, "action": action, "scope": "release_history",
                 "reference_frame": self._release_reference["frame"]})
            self._emit("shadow_release_reference_invalidated", reason=reason,
                       action=action,
                       reference_frame=self._release_reference["frame"])
        self._release_reference = None

    def capture(self, action, arguments, *, action_error=None):
        if action_error is not None:
            self._drop_reference("action_error", action)
            self._drop_release_reference("action_error", action)
        # A gripper transition changes the physical dependency the reference was
        # calibrated under, so it is dropped before the frame is even stepped.
        # open_gripper keeps the calibration as release history, because the
        # release check is precisely a question about the grasp that just ended.
        if action == "open_gripper":
            self._drop_reference("gripper_state_changed", action,
                                 keep_for_release=self._phase == "closed")
        elif action == "close_gripper":
            # A new grasp cycle: an older release history no longer applies.
            self._drop_release_reference("regrasped", action)
            self._drop_reference("gripper_state_changed", action)
        elif action not in MOTION_ACTIONS and action != "initial":
            self._drop_reference("unsupported_motion", action)
            self._drop_release_reference("unsupported_motion", action)
        before = self._steps_advanced
        result = super().capture(action, arguments, action_error=action_error)
        if self._initialized and self._steps_advanced == before:
            # The frame was never stepped (missing proprioception or a broker
            # mechanism failure), so no variant saw the motion.
            self._drop_reference("capture_gap", action)
            self._drop_release_reference("capture_gap", action)
        return result

    # -- lifecycle: initialize -------------------------------------------

    def _do_initialize(self, step):
        if self._initialization_attempted:
            self._invalidate("initialize_already_attempted")
            return
        self._initialization_attempted = True
        self._anchor_scene_samples += 1
        try:
            obs = copy.deepcopy(self._sensor_call("get_observation"))
            self._anchor_entity_readouts += len(self._inventory)
            entities = [dict(item, measurement=self._scene_measurement(obs, item))
                        for item in self._inventory]
        except Exception as exc:
            self._invalidate("anchor:" + type(exc).__name__)
            self._emit("program_error", operation="anchor", error=str(exc))
            return
        cam = obs["agentview"]
        context = {"schema": 1, "task": {"suite": SUITE, "task": TASK},
                   "coordinate_frame": "world", "entities": entities,
                   "robot_state": step["robot_state"],
                   "camera": {"intrinsics": _numeric(cam["intrinsics"]),
                              "camera_to_reference": _numeric(cam["pose_mat"])},
                   "uncertainty": {"identity_binding": IDENTITY_BINDING,
                                   "geometry": "visible_surface_only",
                                   "physical_support": "not_observable"},
                   "budget": self._budget_snapshot()}
        # Anchor readings are calibration inputs, never scored evidence.
        self._emit("shadow_anchor_committed", context=context,
                   calibration=True, scored=False,
                   anchor_object_count=len(entities),
                   measurement_status={e["id"]: e["measurement"]["status"]
                                       for e in entities},
                   schedule=self._schedule)
        for variant in self._variants:
            try:
                variant.revise(variant.program.call("initialize", _copy(context)))
                variant.initialized = True
                self._emit("shadow_variant_initialized", variant=variant.name,
                           state=variant.state,
                           state_revision=variant.state_revision,
                           source_sha256=variant.source_sha256)
            except Exception as exc:
                self._emit("shadow_variant_program_error",
                           **variant.fail("initialize", exc, self._frame))
        self._initialized = True
        self._emit("shadow_initialized", schedule=self._schedule,
                   variants=[v.summary() for v in self._variants])

    # -- lifecycle: one frame --------------------------------------------

    def _scheduled_purpose(self, step, action):
        """Fixed schedule, decided from the action and phase only.

        It never depends on a measurement outcome, so which frames are scored is
        settled before any reading is taken.
        """
        if step.get("action_error") is not None:
            return None
        if action == "close_gripper":
            return "reference"
        if action == "open_gripper":
            return "release" if self._phase == "closed" else None
        if action == POST_RELEASE_ACTION:
            return "post_release"
        if action in MOTION_ACTIONS and self._phase == "closed":
            return "relation"
        return None

    def _do_step(self, step, action):
        self._steps_advanced += 1
        if not step.get("synthetic_frame"):
            # A real captured motor frame changes the physical state; the
            # synthetic final frame deliberately does not and keeps this epoch.
            self._physical_epoch += 1
        purpose = self._scheduled_purpose(step, action)
        full_step = {**step, "budget": self._budget_snapshot()}
        for variant in self._variants:
            variant.pending = None
            variant.pending_frame = None
            if variant.degraded or not variant.initialized:
                continue
            try:
                variant.revise(variant.program.call(
                    "advance", _copy(variant.state), _copy(full_step)))
            except Exception as exc:
                self._emit("shadow_variant_program_error",
                           **variant.fail("advance", exc, self._frame))
                continue
            self._world_version += 1
            try:
                raw = variant.program.call(
                    "predict", _copy(variant.state), _copy(full_step))
                prediction = self._validate_prediction(raw)
            except Exception as exc:
                self._emit("shadow_variant_program_error",
                           **variant.fail("predict", exc, self._frame))
                continue
            variant.pending = prediction
            variant.pending_frame = step["index"]
            # Committed before any scored reading exists for this frame.
            self._emit("shadow_prediction_committed", variant=variant.name,
                       prediction=prediction, action=action,
                       scheduled_purpose=purpose,
                       state_revision=variant.state_revision,
                       measurement_taken=False)
        if purpose is not None:
            self._scheduled_check(purpose, step, action)
        if action == "close_gripper" and step.get("action_error") is None:
            self._phase = "closed"
        elif action == "open_gripper" and step.get("action_error") is None:
            self._phase = "released"

    def _validate_prediction(self, raw):
        if not isinstance(raw, dict) or set(raw.get("objects", {})) != self._entity_ids:
            raise ProgramExecutionError("prediction objects must match inventory")
        if type(raw.get("request_query")) is not bool:
            raise ProgramExecutionError("request_query must be a bool")
        if raw.get("query_purpose") not in ("none",) + PURPOSES:
            raise ProgramExecutionError("invalid query_purpose")
        for entity, value in raw["objects"].items():
            if not isinstance(value, dict) or value.get("attachment") not in (
                    "attached", "free", "unknown"):
                raise ProgramExecutionError(f"invalid attachment for {entity}")
            if value.get("position") is not None:
                _vector(value["position"], 3, f"{entity} position")
        check = raw.get("grasp_check")
        if check is not None and (not isinstance(check, dict) or set(check) != {
                "object_id", "reference_frame", "attached_position",
                "free_position"}):
            raise ProgramExecutionError("invalid grasp_check shape")
        if check is not None and check["object_id"] != self._object_id:
            raise ProgramExecutionError("grasp prediction identity mismatch")
        extra = raw.get("condition_check")
        if extra is not None:
            required = {"object_id", "reference_frame", "candidates"}
            # ``reference_slot`` is optional and declarative only: it records
            # which calibration the program believes it committed against. The
            # broker checks the frame itself and never trusts this field.
            if not isinstance(extra, dict) or not required <= set(extra) or (
                    set(extra) - required - {"reference_slot"}):
                raise ProgramExecutionError("invalid condition_check shape")
            if not isinstance(extra["candidates"], dict) or not extra["candidates"]:
                raise ProgramExecutionError("condition_check needs candidates")
            if extra["object_id"] != self._object_id:
                raise ProgramExecutionError("condition prediction identity mismatch")
        return _copy(raw)

    # -- one shared measurement, then per-variant comparison --------------

    def _measure(self, entity_ids):
        try:
            obs = copy.deepcopy(self._sensor_call("get_observation"))
        except Exception as exc:
            return {eid: {"object_id": eid, "status": "unknown",
                          "position": None, "visible_bounds": None,
                          "reason": "observation_error:" + type(exc).__name__}
                    for eid in entity_ids}
        out = {}
        for item in self._inventory:
            if item["id"] in entity_ids:
                out[item["id"]] = self._scene_measurement(obs, item)
        return out

    def _scheduled_check(self, purpose, step, action):
        frame = step["index"]
        # Fixed before execution: post_release is never scored (see
        # SCORED_PURPOSES), so this cannot depend on any reading.
        scored = purpose in SCORED_PURPOSES
        evidence_class = ("calibration" if purpose == "reference"
                          else "scored" if scored else "unscored_diagnostic")
        wanted = {self._object_id}
        if purpose in ("release", "post_release") and self._target_id:
            wanted.add(self._target_id)
        readings = self._measure(wanted)
        # Added sensing cost, recorded as a real cost rather than inherited
        # metadata: one scene sample, N entity readouts.
        self._scene_samples += 1
        self._entity_readouts += len(readings)
        self._query_used += 1
        primary = readings[self._object_id]
        evidence_id = (f"{self._trial_uid}_frame{frame}_epoch"
                       f"{self._physical_epoch}_{purpose}_{self._object_id}")
        target = readings.get(self._target_id) if self._target_id else None
        relative = None
        if (target is not None and target.get("status") == "ok"
                and primary.get("status") == "ok"):
            relative = {
                "target_object_id": self._target_id,
                "displacement_xyz": [a - b for a, b in zip(
                    primary["position"], target["position"])],
                "planar_distance_m": _distance(primary["position"][:2],
                                               target["position"][:2]),
                "interpretation": "relative_displacement_readout_only",
                "not_a_support_proof": (
                    "XY proximity does not establish stable support; the "
                    "physical support condition is not identifiable from these "
                    "public observations"),
            }
        record = {"evidence_id": evidence_id, "purpose": purpose,
                  "scored": scored, "calibration": purpose == "reference",
                  "evidence_class": evidence_class,
                  "physical_epoch": self._physical_epoch,
                  "synthetic_frame": bool(step.get("synthetic_frame")),
                  "shares_physical_sample_with": (
                      self._last_scored_evidence_id
                      if step.get("synthetic_frame") else None),
                  "frame": frame,
                  "robot_state": _copy(step["robot_state"]),
                  "action": action, "primary": primary, "target": target,
                  "relative_readout": relative,
                  "reference_frame": (self._reference or {}).get("frame"),
                  "release_reference_frame": (
                      self._release_reference or {}).get("frame"),
                  "sensor_costs": _copy(self._costs),
                  "added_sensing": {"scene_samples": self._scene_samples + self._anchor_scene_samples,
                                    "entity_readouts": self._entity_readouts + self._anchor_entity_readouts,
                                    "scheduled_scene_samples": self._scene_samples,
                                    "query_used": self._query_used}}
        self._shared_measurements.append(record)
        if scored:
            self._last_scored_evidence_id = evidence_id
        self._emit("shadow_measurement", **record,
                   shared_by=[v.name for v in self._variants])
        for variant in self._variants:
            self._compare_variant(variant, record, step)
        if purpose == "reference":
            self._set_reference(step, primary)

    def _set_reference(self, step, measurement):
        if measurement.get("status") != "ok":
            self._emit("shadow_reference_unavailable",
                       reason=measurement.get("reason"),
                       frame=step["index"])
            self._reference = None
            return
        self._reference = {
            "frame": step["index"], "object_position": measurement["position"],
            "ee_position": step["robot_state"]["position"],
            "gripper": step["robot_state"]["gripper"],
            "role": "calibration_input"}
        self._emit("shadow_reference_set", scored=False, **self._reference)

    def _calibration_for(self, purpose):
        """Which calibration a purpose may be scored against, and its validity.

        ``relation`` needs a currently valid grasp calibration. ``release`` and
        ``post_release`` are questions about the grasp that has just ended, so
        they use the retained release history -- but only when that history was
        never invalidated by anything other than the release itself.
        """
        if purpose in ("release", "post_release"):
            return self._release_reference, "release_history"
        if purpose == "relation":
            return self._reference, "current_grasp_calibration"
        return None, "not_required"

    def _applicability(self, variant, purpose, committed_reference_frame):
        if variant.degraded or not variant.initialized:
            return "variant_degraded", None
        if variant.pending is None:
            return "no_committed_prediction", None
        if purpose not in variant.supported_purposes:
            # Declared out of scope for this program. Not an error, and not
            # dropped: the frame stays visible as a coverage gap.
            return "purpose_unsupported", None
        if not variant.pending.get("request_query") or (
                variant.pending.get("query_purpose") != purpose):
            return "not_requested", None
        calibration, scope = self._calibration_for(purpose)
        contract = {"scope": scope,
                    "broker_reference_frame": (calibration or {}).get("frame"),
                    "committed_reference_frame": committed_reference_frame}
        if scope == "not_required":
            return "applicable", contract
        if calibration is None:
            # An invalidating event happened after this calibration, so it is
            # stale: it is not scored, and it is not an error either.
            return "reference_invalidated", contract
        if committed_reference_frame != calibration["frame"]:
            # The variant committed against a calibration the broker no longer
            # holds as valid for this purpose.
            return "reference_invalidated", contract
        return "applicable", contract

    def _candidates(self, variant, purpose):
        check = variant.pending.get("condition_check")
        if isinstance(check, dict) and purpose in ("release", "post_release"):
            return dict(check["candidates"]), check.get("reference_frame")
        legacy = variant.pending.get("grasp_check")
        if isinstance(legacy, dict):
            return ({"attached": legacy["attached_position"],
                     "free": legacy["free_position"]},
                    legacy["reference_frame"])
        if isinstance(check, dict):
            return dict(check["candidates"]), check.get("reference_frame")
        return {}, None

    def _compare_variant(self, variant, record, step):
        purpose, frame = record["purpose"], record["frame"]
        candidates, reference_frame = ({}, None)
        if variant.pending is not None:
            candidates, reference_frame = self._candidates(variant, purpose)
        applicability, contract = self._applicability(
            variant, purpose, reference_frame)
        if contract is not None and variant.pending is not None:
            check = variant.pending.get("condition_check")
            contract["declared_slot"] = (
                check.get("reference_slot") if isinstance(check, dict) else None)
        entry = {"variant": variant.name, "evidence_id": record["evidence_id"],
                 "purpose": purpose, "frame": frame,
                 "applicability": applicability, "scored": record["scored"],
                 "evidence_class": record["evidence_class"],
                 "physical_epoch": record["physical_epoch"],
                 "reference_contract": contract,
                 "source_sha256": variant.source_sha256,
                 "state_revision_before": variant.state_revision,
                 "measurement_status": record["primary"].get("status"),
                 "measurement_reason": record["primary"].get("reason"),
                 "action": record["action"], "comparison": None,
                 "legacy_comparison": None,
                 # First use of this physical sample by THIS variant. Both
                 # variants read the same sample, so it is never an independent
                 # observation across variants -- that is the point of pairing.
                 "first_use_of_sample": True,
                 "independent_physical_sample": False,
                 "shared_physical_sample": True,
                 "independent_across_variants": False,
                 "claim_vs_evidence": None}
        if applicability == "applicable":
            key = (record["evidence_id"], variant.name)
            uses = self._evidence_uses[key] = self._evidence_uses.get(key, 0) + 1
            self._variant_comparisons += 1
            # Repeated checks by one variant against one sample are the same
            # evidence, counted per variant rather than against a global total.
            entry["first_use_of_sample"] = uses == 1
            entry["independent_physical_sample"] = bool(
                uses == 1 and record["scored"] and not record["synthetic_frame"])
            entry["evidence_use_index"] = uses
            entry["candidates"] = _copy(candidates)
            entry["comparison"] = compare_candidates(
                candidates, record["primary"], self._tolerance, purpose=purpose,
                scored=record["scored"], reference_frame=reference_frame,
                frame=frame, expected_object_id=self._object_id,
                evidence_class=record["evidence_class"])
            legacy = variant.pending.get("grasp_check")
            if isinstance(legacy, dict):
                # The unchanged frozen rule at the unchanged 3 cm tolerance, so
                # the common readout stays like-for-like across variants.
                entry["legacy_comparison"] = compare_grasp_hypotheses(
                    legacy, record["primary"], frame, self._tolerance,
                    purpose if purpose in ("reference", "relation") else "relation")
            claimed = variant.pending["objects"][self._object_id]["attachment"]
            entry["claim_vs_evidence"] = {
                "claimed_attachment": claimed,
                "identified": entry["comparison"]["status"],
                "compatible": entry["comparison"]["compatible"],
                "scored": record["scored"]}
        # Immutable: appended before assimilation is attempted, and never
        # rewritten by its outcome.
        variant.comparisons.append(entry)
        self._emit("shadow_variant_comparison", **entry)
        if applicability == "applicable":
            self._assimilate(variant, record, entry)

    def _assimilate(self, variant, record, entry):
        primary = record["primary"]
        legacy = entry["legacy_comparison"] or {
            "status": "UNKNOWN", "reason": entry["comparison"]["reason"],
            "error_m": None, "attached_error_m": None, "free_error_m": None,
            "separation_m": None}
        evidence = {
            # Legacy keys, unchanged in name and meaning.
            "object_id": primary["object_id"], "purpose": record["purpose"],
            "status": primary.get("status", "unknown"),
            "position": primary.get("position"),
            "visible_bounds": primary.get("visible_bounds"),
            "robot_state": _copy(record["robot_state"]),
            "frame": record["frame"], "reason": primary.get("reason"),
            "comparison": _copy(legacy),
            # Additive: a program that ignores these behaves exactly as before.
            "evidence_id": record["evidence_id"], "scored": record["scored"],
            "calibration": record["calibration"],
            "evidence_class": record["evidence_class"],
            "physical_epoch": record["physical_epoch"],
            "independent_physical_sample": entry["independent_physical_sample"],
            "shares_physical_sample_with": record["shares_physical_sample_with"],
            "candidate_comparison": _copy(entry["comparison"]),
            "relative_readout": _copy(record["relative_readout"]),
        }
        try:
            variant.revise(variant.program.call(
                "assimilate", _copy(variant.state), _copy(evidence)))
            self._emit("shadow_variant_assimilated", variant=variant.name,
                       evidence_id=record["evidence_id"],
                       state=variant.state,
                       state_revision=variant.state_revision,
                       comparison_preserved=True)
        except Exception as exc:
            # The comparison above stays exactly as recorded; only an error is
            # added, and this variant is not marked degraded by it.
            self._emit("shadow_variant_program_error",
                       comparison_preserved=True,
                       evidence_id=record["evidence_id"],
                       **variant.fail("assimilate", exc, record["frame"]))

    # -- policy-facing surface: no control, no feedback -------------------

    def _policy_extras(self):
        return {"world_verify": self.world_verify,
                "recovery_available": self.recovery_available,
                "use_recovery": self.use_recovery}

    def world_verify(self):
        """Present for API compatibility, deliberately inert in shadow mode."""
        self._verify_calls += 1
        verdict = {"status": "unknown", "prediction": None, "observed": None,
                   "error_m": None, "action": None, "frame": self._frame,
                   "reason": "shadow_mode_no_feedback",
                   "recovery_available": self.recovery_available()}
        self._emit("shadow_verify_ignored", verdict=verdict,
                   verify_calls=self._verify_calls)
        self._last_verdict = _copy(verdict)
        return verdict

    # -- post-release check on the broker's own final frame ---------------

    def complete(self, **result):
        """One synthetic post-release frame before the parent finishes.

        A1 ends with observation calls only, and observations are not motor
        frames, so no policy action can carry the post-release check. This frame
        is labelled synthetic, steps no simulator, and is explicitly *not* new
        physical evidence: it shares the release frame's physical epoch, is
        recorded as an unscored diagnostic, and never enters a scored or
        temporal denominator. The release itself is assessed on the actual
        post-open reading taken at the open_gripper frame.
        """
        if (self._initialized and not self._mechanism_failed
                and not self._post_release_done and self._phase == "released"):
            self._post_release_done = True
            robot_state = self._read_proprio()
            if robot_state is None:
                self._emit("shadow_post_release_skipped",
                           reason="missing_proprio")
            else:
                step = {"index": self._frame,
                        "frame_id": f"live_{self._frame}",
                        "action": {"api": POST_RELEASE_ACTION, "args": {}},
                        "robot_state": robot_state, "synthetic_frame": True}
                self._emit("shadow_post_release_frame", synthetic_frame=True,
                           scored=False, evidence_class="unscored_diagnostic",
                           physical_epoch=self._physical_epoch,
                           shares_physical_sample_with=(
                               self._last_scored_evidence_id),
                           interpretation=(
                               "diagnostic read at an unchanged physical state; "
                               "not a new independent observation and not proof "
                               "that the object has settled"),
                           robot_state=robot_state)
                self._do_step(step, POST_RELEASE_ACTION)
                self._frame += 1
        super().complete(**result)

    # -- accounting -------------------------------------------------------

    def _variant_report(self, variant):
        """Per-purpose counts, with scored and unscored frames kept apart.

        ``residual_m`` holds scored attached-hypothesis residuals only.
        Per-candidate residuals are reported in full under ``candidate_residuals``
        so release and post-release candidates (whose names are not ``attached``)
        are visible without being mixed into the paired statistic. Unscored
        diagnostic geometry lives in its own field and enters no denominator.
        """
        by_purpose = {}
        for entry in variant.comparisons:
            item = by_purpose.setdefault(entry["purpose"], {
                "frames": 0, "scored_frames": 0, "unscored_frames": 0,
                "applicable": 0, "applicability": {},
                "evidence_class": {}, "status": {}, "reason": {},
                "attached_candidate": None, "residual_m": [],
                "candidate_residuals": {}, "diagnostic_residuals": {},
                "first_use_frames": 0, "repeat_use_frames": 0})
            item["frames"] += 1
            item["scored_frames"] += int(bool(entry["scored"]))
            item["unscored_frames"] += int(not entry["scored"])
            item["applicability"][entry["applicability"]] = item[
                "applicability"].get(entry["applicability"], 0) + 1
            klass = entry["evidence_class"]
            item["evidence_class"][klass] = item["evidence_class"].get(klass, 0) + 1
            if entry["applicability"] != "applicable":
                continue
            item["applicable"] += 1
            if entry["first_use_of_sample"]:
                item["first_use_frames"] += 1
            else:
                item["repeat_use_frames"] += 1
            comparison = entry["comparison"]
            status, reason = comparison["status"], comparison["reason"]
            item["status"][status] = item["status"].get(status, 0) + 1
            item["reason"][reason] = item["reason"].get(reason, 0) + 1
            for name, value in (comparison["residuals"] or {}).items():
                if value is not None:
                    item["candidate_residuals"].setdefault(name, []).append(value)
            if not entry["scored"]:
                for name, value in (comparison["diagnostic_residuals"] or {}).items():
                    if value is not None:
                        item["diagnostic_residuals"].setdefault(
                            name, []).append(value)
                continue
            name, residual = attached_residual(comparison, entry["purpose"])
            if residual is not None:
                item["attached_candidate"] = name
                item["residual_m"].append(residual)
        return {**variant.summary(), "by_purpose": by_purpose,
                "comparisons": len(variant.comparisons),
                "scored_comparisons": sum(
                    1 for e in variant.comparisons
                    if e["scored"] and e["applicability"] == "applicable"),
                "residual_field_is_scored_only": True,
                "errors": _copy(variant.errors)}

    def _common_set(self):
        """Paired residuals only where every variant produced a residual.

        Restricted to *scored* comparisons: calibration and unscored diagnostic
        frames have no verdict and must not enter a denominator. Pairing is on
        the attached hypothesis for the purpose (its candidate name differs
        between relation, release and post_release), so the two variants are
        always compared on the same claim.

        Paired samples, one-sided coverage and the full set of scheduled
        opportunities are reported separately. Marginal means over different
        surviving subsets are not comparable, so the paired mean is the only
        mean reported over a common denominator, and the one-sided lists say
        exactly which samples each variant alone covered. A paired sample is one
        shared physical reading compared twice, not two independent draws.
        """
        names = [v.name for v in self._variants]
        keyed, keyed_names = {}, {}
        for variant in self._variants:
            for entry in variant.comparisons:
                if entry["applicability"] != "applicable" or not entry["scored"]:
                    continue
                name, residual = attached_residual(
                    entry["comparison"], entry["purpose"])
                if residual is None:
                    continue
                key = (entry["evidence_id"], entry["purpose"])
                keyed.setdefault(key, {})[variant.name] = residual
                keyed_names.setdefault(key, {})[variant.name] = name
        pairs, one_sided = [], {name: [] for name in names}
        for (evidence_id, purpose), residuals in sorted(keyed.items()):
            candidates = keyed_names[(evidence_id, purpose)]
            if len(residuals) == len(names):
                pairs.append({"evidence_id": evidence_id, "purpose": purpose,
                              "residual_m": residuals,
                              "attached_candidate": candidates,
                              "shared_physical_sample": True,
                              "independent_observations": False})
            else:
                for name in residuals:
                    one_sided[name].append(
                        {"evidence_id": evidence_id, "purpose": purpose,
                         "residual_m": residuals[name],
                         "attached_candidate": candidates[name],
                         "absent_for": sorted(set(names) - set(residuals))})
        out = {"variants": names, "paired_frames": len(pairs), "pairs": pairs,
               "paired_mean_residual_m": {},
               "paired_purposes": sorted({p["purpose"] for p in pairs}),
               "paired_hypothesis": _copy(ATTACHED_CANDIDATE),
               "scored_purposes": list(SCORED_PURPOSES),
               "pairs_are_scored_only": True,
               "one_sided_samples": one_sided,
               "one_sided_counts": {n: len(v) for n, v in one_sided.items()},
               "scheduled_opportunities": {},
               "schedule_owner": "broker",
               "schedule_intervention": (
                   "the broker schedules every sample; each variant's own "
                   "request_query gating is preserved and recorded as "
                   "not_requested rather than overridden"),
               "paired_sample_is_one_physical_reading": True,
               "means_not_comparable_across_subsets": True}
        for name in names:
            values = [p["residual_m"][name] for p in pairs]
            out["paired_mean_residual_m"][name] = (
                sum(values) / len(values) if values else None)
        for record in self._shared_measurements:
            item = out["scheduled_opportunities"].setdefault(
                record["purpose"], {
                    "scheduled": 0, "measurement_ok": 0,
                    "scored": record["purpose"] in SCORED_PURPOSES,
                    "evidence_class": {}, "physical_epochs": [],
                    "applicable": {n: 0 for n in names},
                    "scored_applicable": {n: 0 for n in names}})
            item["scheduled"] += 1
            item["measurement_ok"] += int(
                record["primary"].get("status") == "ok")
            klass = record["evidence_class"]
            item["evidence_class"][klass] = item["evidence_class"].get(klass, 0) + 1
            if record["physical_epoch"] not in item["physical_epochs"]:
                item["physical_epochs"].append(record["physical_epoch"])
        for item in out["scheduled_opportunities"].values():
            # Distinct physical epochs bound how many independent samples the
            # purpose can possibly rest on, whatever the frame count says.
            item["distinct_physical_epochs"] = len(item.pop("physical_epochs"))
        for variant in self._variants:
            for entry in variant.comparisons:
                item = out["scheduled_opportunities"].get(entry["purpose"])
                if item and entry["applicability"] == "applicable":
                    item["applicable"][variant.name] += 1
                    if entry["scored"]:
                        item["scored_applicable"][variant.name] += 1
        # Scored coverage a variant adds beyond the paired set, reported
        # separately so it can never be read as an improvement on the
        # like-for-like numbers. Unscored purposes are excluded: extra
        # diagnostics are not extra verification.
        out["added_coverage"] = {
            v.name: sorted({e["purpose"] for e in v.comparisons
                            if e["applicability"] == "applicable" and e["scored"]}
                           - set(out["paired_purposes"]))
            for v in self._variants}
        out["added_unscored_coverage"] = {
            v.name: sorted({e["purpose"] for e in v.comparisons
                            if e["applicability"] == "applicable"
                            and not e["scored"]})
            for v in self._variants}
        out["coverage_gaps"] = {
            v.name: sorted({(e["purpose"], e["applicability"])
                            for e in v.comparisons
                            if e["applicability"] in ("purpose_unsupported",
                                                      "not_requested")})
            for v in self._variants}
        return out

    def close(self, error=None):
        report = {
            "mode": MODE, "schema": SCHEMA, "schedule": self._schedule,
            "tolerance_m": self._tolerance,
            "identity_binding": IDENTITY_BINDING,
            "phase_at_close": self._phase,
            "policy_verify_calls": self._verify_calls,
            "policy_controlled_by_world": False,
            "reference_invalidations": _copy(self._reference_invalidations),
            "shared_measurements": len(self._shared_measurements),
            "scored_measurements": sum(
                1 for m in self._shared_measurements if m["scored"]),
            "calibration_measurements": sum(
                1 for m in self._shared_measurements
                if m["evidence_class"] == "calibration"),
            "unscored_diagnostic_measurements": sum(
                1 for m in self._shared_measurements
                if m["evidence_class"] == "unscored_diagnostic"),
            "measurements_by_evidence_class": _counts(
                m["evidence_class"] for m in self._shared_measurements),
            "distinct_physical_epochs": len(
                {m["physical_epoch"] for m in self._shared_measurements}),
            "synthetic_frames": sum(
                1 for m in self._shared_measurements if m["synthetic_frame"]),
            "post_release_frame_taken": self._post_release_done,
            "post_release_scored": False,
            "post_release_evidence_class": "unscored_diagnostic",
            "added_sensing": {
                "scene_samples": self._scene_samples + self._anchor_scene_samples,
                "entity_readouts": self._entity_readouts + self._anchor_entity_readouts,
                "scheduled_scene_samples": self._scene_samples,
                "anchor_scene_samples": self._anchor_scene_samples,
                "anchor_entity_readouts": self._anchor_entity_readouts,
                "variant_comparisons": self._variant_comparisons,
                "query_used": self._query_used,
                "note": ("totals include initialization anchors; query_used counts "
                         "scheduled checks only. One scene sample yields one readout per requested "
                         "entity and is compared once per variant; the three "
                         "counts are different quantities and none of them may "
                         "be read as an independent-observation count"),
            },
            "variants": [self._variant_report(v) for v in self._variants],
            "common_set": self._common_set(),
            "unidentifiable_conditions": {
                "physical_grasp_support": (
                    "a compatible attached hypothesis is not a grasp label"),
                "stable_placement_support": (
                    "XY proximity to the target does not establish support"),
                "post_release_temporal_stability": (
                    "the policy takes no action after open_gripper, so repeated "
                    "reads at a static gripper cannot separate a settled object "
                    "from a still-supported one"),
            },
            "graspnet_used": False,
        }
        self.manifest["shadow_report"] = report
        _write_json(self.directory / "shadow_report.json", report)
        super().close(error)


# -- config ---------------------------------------------------------------

_CONFIG_KEYS = {
    "schema_version", "mode", "suite", "task", "policy", "policy_sha256",
    "world_program", "world_sha256", "variants", "scene_inventory",
    "scene_inventory_sha256", "observation", "query_budget", "tolerance",
    "max_actions", "max_recovery", "trial_timeout_seconds", "dev_seeds",
    "output_root", "run_name", "env_config", "schedule", "engineering_variant",
}
_VARIANT_KEYS = {"name", "world_program", "world_sha256", SUPPORTED_PURPOSES,
                 "provenance"}


def _digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def validate_shadow_config(config, *, seed):
    """Shared parent/child validation. Refuses caps this round must not have."""
    config.pop("_inventory", None)
    if set(config) != _CONFIG_KEYS:
        missing = sorted(_CONFIG_KEYS - set(config))
        extra = sorted(set(config) - _CONFIG_KEYS)
        raise ValueError(f"shadow config keys missing={missing} extra={extra}")
    if config["schema_version"] != SCHEMA or config["mode"] != MODE:
        raise ValueError("shadow config schema/mode mismatch")
    if config["suite"] != SUITE or config["task"] != TASK:
        raise ValueError("shadow config is bound to the bowl-on-plate task")
    if config["schedule"] != SHADOW_SCHEDULE:
        raise ValueError("shadow schedule must match the frozen schedule")
    if config["engineering_variant"] is not True:
        raise ValueError(
            "this round is an engineering variant, not a native Fix Loop result")
    for field in ("query_budget", "max_actions", "max_recovery"):
        if config[field] != UNLIMITED:
            raise ValueError(f"{field} must be the word {UNLIMITED!r}")
    if abs(float(config["tolerance"]) - 0.03) > 1e-12:
        raise ValueError("the common comparison tolerance stays fixed at 0.03 m")
    if int(config["trial_timeout_seconds"]) != 900:
        raise ValueError("the 900 s process watchdog must be preserved")
    seeds = config["dev_seeds"]
    if list(seeds) != [51, 65]:
        raise ValueError("dev_seeds must be the development range [51, 65]")
    if not (seeds[0] <= int(seed) <= seeds[1]):
        raise ValueError(f"seed {seed} is outside the development range")
    if _digest(config["policy"]) != config["policy_sha256"]:
        raise ValueError("policy digest mismatch")
    if _digest(config["world_program"]) != config["world_sha256"]:
        raise ValueError("base world digest mismatch")
    if _digest(config["scene_inventory"]) != config["scene_inventory_sha256"]:
        raise ValueError("scene inventory digest mismatch")
    names = []
    for spec in config["variants"]:
        if set(spec) != _VARIANT_KEYS:
            raise ValueError(f"variant keys must be exactly {sorted(_VARIANT_KEYS)}")
        if _digest(spec["world_program"]) != spec["world_sha256"]:
            raise ValueError(f"variant {spec['name']} digest mismatch")
        unsupported = set(spec[SUPPORTED_PURPOSES]) - set(PURPOSES)
        if unsupported:
            raise ValueError(f"unknown supported purposes {sorted(unsupported)}")
        if not isinstance(spec["provenance"], dict) or not spec["provenance"].get(
                "origin"):
            raise ValueError("each variant needs a provenance origin")
        names.append(spec["name"])
    if len(names) < 2 or len(set(names)) != len(names):
        raise ValueError("shadow mode needs at least two uniquely named variants")
    root = Path(config["output_root"]).resolve()
    for forbidden in ("world-native-fixloop-abc-opus46-bowl-20260914",
                      "code-world-bowl-opus46-frozen"):
        if forbidden in root.parts:
            raise ValueError(
                "the shadow run root must stay separate from frozen A/B/C ledgers")
    inventory = json.loads(Path(config["scene_inventory"]).read_text())
    roles = [e["role"] for e in inventory]
    if roles.count("manipulated") != 1 or roles.count("target") != 1:
        raise ValueError("inventory needs exactly one manipulated and one target")
    manipulated = next(e for e in inventory if e["role"] == "manipulated")
    target = next(e for e in inventory if e["role"] == "target")
    observation = config["observation"]
    if set(observation) - {"object_id", "segmentation_prompt", "min_score",
                           "target_object_id"}:
        raise ValueError("unsupported observation keys")
    if observation["object_id"] != manipulated["id"]:
        raise ValueError("observation.object_id must be the manipulated entity")
    if observation["segmentation_prompt"] != manipulated["label"]:
        raise ValueError("segmentation_prompt must be that entity's label")
    if observation.get("target_object_id") not in (None, target["id"]):
        raise ValueError("observation.target_object_id must be the target entity")
    config["_inventory"] = inventory
    return config


def load_shadow_config(args):
    """Parent-side load: the config plus the args it must agree with."""
    if not args.replay_code or args.interactive:
        raise ValueError(
            "shadow mode requires --args.replay-code and noninteractive")
    if (args.suite, args.task) != (SUITE, TASK):
        raise ValueError(f"shadow mode is restricted to {SUITE}/{TASK}")
    if type(args.trial) is not int or args.trial < 0:
        raise ValueError("trial seed must be a nonnegative integer")
    config_path = Path(args.world_model_config).expanduser().resolve()
    config = json.loads(config_path.read_text())
    base_dir = config_path.parent
    for key in ("policy", "world_program", "scene_inventory"):
        path = Path(config[key]).expanduser()
        config[key] = str((path if path.is_absolute() else base_dir / path).resolve())
    for spec in config["variants"]:
        path = Path(spec["world_program"]).expanduser()
        spec["world_program"] = str(
            (path if path.is_absolute() else base_dir / path).resolve())
    config = validate_shadow_config(config, seed=args.trial)
    if _digest(args.replay_code) != config["policy_sha256"]:
        raise ValueError("policy source missing or digest mismatch")
    output_root = Path(config["output_root"]).expanduser().resolve()
    ordinary = Path(args.output_dir).expanduser().resolve()
    if (output_root.is_relative_to(ordinary)
            or ordinary.is_relative_to(output_root)):
        raise ValueError("shadow output_root must be separate from replay output")
    config["output_root"] = str(output_root)

    import yaml
    base = yaml.safe_load(Path(args.config).expanduser().read_text())
    cfg = base.get("env", {}).get("cfg", {})
    if (cfg.get("privileged", False)
            or cfg.get("low_level", {}).get("privileged", False)
            or cfg.get("apis") != ["FrankaLiberoApiReducedSkillLibraryTraced"]):
        raise ValueError("shadow replay requires the nonprivileged reduced API")
    return config, base


def prepare(args, config):
    """Write the trial directory; return its child_request.json path."""
    broker_config = dict(config)
    broker_config.pop("_inventory")
    request_path = _prepare_live(args, broker_config)
    directory = request_path.parent
    inventory_dest = directory / "scene_inventory.json"
    inventory_dest.write_bytes(Path(config["scene_inventory"]).read_bytes())
    for index, spec in enumerate(config["variants"]):
        dest = directory / f"variant_{index}_{spec['name']}.py"
        dest.write_bytes(Path(spec["world_program"]).read_bytes())
    request = json.loads(request_path.read_text())
    request["schema_version"] = SCHEMA
    _write_json(request_path, request)

    manifest_path = directory / "live_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest.update(
        schema_version=SCHEMA, mode=MODE, partition="development",
        engineering_variant=True,
        native_fix_loop_result=False,
        schedule=config["schedule"],
        identity_binding=IDENTITY_BINDING,
        execution_caps=UNLIMITED,
        scene_inventory_sha256=_sha256(inventory_dest),
        adapter_sha256=_sha256(Path(__file__)),
        variant_digests={spec["name"]: spec["world_sha256"]
                         for spec in config["variants"]})
    _write_json(manifest_path, manifest)
    return request_path


def run_shadow_condition(args):
    """Parent entry point: prepare, launch the isolated child, validate."""
    config, _ = load_shadow_config(args)
    environment = _child_environment(dict(os.environ))
    request_path = prepare(args, config)
    directory = request_path.parent
    prepared = json.loads((directory / "live_manifest.json").read_text())
    receipt = _launch_child(request_path, environment,
                            config["trial_timeout_seconds"],
                            module="cap.world_model.shadow_condition_broker")
    try:
        manifest = json.loads((directory / "live_manifest.json").read_text())
        valid = (
            manifest.get("status") == "complete"
            and manifest.get("mode") == MODE
            and manifest.get("schema_version") == SCHEMA
            and manifest.get("engineering_variant") is True
            and (manifest.get("suite"), manifest.get("task"),
                 manifest.get("seed")) == (SUITE, TASK, args.trial)
            and manifest.get("adapter_sha256") == prepared["adapter_sha256"]
            == _sha256(Path(__file__))
            and manifest.get("max_actions") == UNLIMITED
            and manifest.get("query_budget") == UNLIMITED
            and isinstance(manifest.get("shadow_report"), dict))
        for field, name in [("policy_sha256", "frozen_policy.py"),
                            ("world_program_sha256", "world_program.py"),
                            ("scene_inventory_sha256", "scene_inventory.json"),
                            ("live_config_sha256", "live_config.json"),
                            ("yaml_sha256", "source_config.yaml"),
                            ("tape_sha256", "live_tape.jsonl")]:
            valid = valid and manifest.get(field) == _sha256(directory / name)
            if field != "tape_sha256":
                valid = valid and manifest.get(field) == prepared.get(field)
    except (OSError, ValueError, TypeError, KeyError):
        valid = False
    receipt["manifest_valid"] = bool(valid)
    _write_json(directory / "child_exit.json", receipt)
    if receipt["status"] != "completed" or not valid:
        raise RuntimeError(
            "shadow trial child failed or evidence incomplete: " + str(directory))
    return directory


def _child_main(request_path):
    directory = request_path.parent
    request = json.loads(request_path.read_text())
    if request.get("schema_version") != SCHEMA:
        raise ValueError("unsupported shadow child schema")
    manifest = json.loads((directory / "live_manifest.json").read_text())
    config = json.loads((directory / "live_config.json").read_text())
    config["scene_inventory"] = str(directory / "scene_inventory.json")
    for index, spec in enumerate(config["variants"]):
        spec["world_program"] = str(
            directory / f"variant_{index}_{spec['name']}.py")
    config["world_program"] = str(directory / "world_program.py")
    config["policy"] = str(directory / "frozen_policy.py")
    config = validate_shadow_config(config, seed=manifest["seed"])
    for field, name in [("policy_sha256", "frozen_policy.py"),
                        ("world_program_sha256", "world_program.py"),
                        ("scene_inventory_sha256", "scene_inventory.json")]:
        if manifest.get(field) != _sha256(directory / name):
            raise ValueError(f"{name} does not match the recorded {field}")

    replay_path = (Path(__file__).resolve().parents[2]
                   / "scripts/libero/replay_trial.py")
    spec = importlib.util.spec_from_file_location("_shadow_replay", replay_path)
    replay = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = replay
    spec.loader.exec_module(replay)
    if os.environ.get("SAM3_SERVICE_URL"):
        from aspire.sim.cap.integrations.vision import sam3
        sam3.SERVICE_URL = os.environ["SAM3_SERVICE_URL"]
    if os.environ.get("PYROKI_SERVICE_URL"):
        from aspire.sim.cap.integrations.motion import pyroki
        pyroki.init_pyroki.__defaults__ = (os.environ["PYROKI_SERVICE_URL"],)

    broker_config = dict(config, scene_inventory=config["_inventory"])
    broker_config.pop("_inventory")
    broker = ShadowConditionBroker(
        directory, broker_config, manifest,
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
        raise RuntimeError("shadow mechanism evidence incomplete")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--child-request", type=Path, required=True)
    _child_main(parser.parse_args().child_request)
