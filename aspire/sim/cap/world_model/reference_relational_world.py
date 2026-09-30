"""CC engineering reference world program for the schema4 relational interface.

AUTHORSHIP: a CC engineering reference, hand-written in this coordinator session
on 2026-09-10. It is *not* the missing artifact of the Opus 4.6 world-generation
protocol: it was never produced by that generation request, must never be
recorded with ``world_program_generator: claude-opus-4-6``, and must never be
admitted through that protocol. The frozen schema3 program under
``docs/experiments/scene-world-bowl-opus46-20260910/frozen/`` remains the only
generated scene artifact. This file exists so the relational mechanisms can be
exercised without any model request or simulator run.

Executed through ``FrozenPythonProgram``: fresh process per call, ``math`` only,
restricted builtins, all persistence through the returned JSON state.

Scope. Implemented here: one shared scene preserving every entity and its
metadata, observed-versus-propagated bookkeeping, a hand-local offset reference
calibrated from an actual measured WXYZ pose, separate attached/free hypotheses
under nonzero offset and rotation, evidence-driven revision of the relation, an
explicit transport condition with named dependencies, and provenance (evidence
IDs, hypothesis IDs, representation version, validity scope).

Explicitly DEFERRED, not implemented and not claimed: online regeneration of the
representation, entity identity merge/split, scene-wide adaptive query
scheduling, placement/support verification at the target, calibrated
probabilities, mass or friction identification, and full geometry recovery.
Mass, hidden geometry and contact stay unknown by construction.
"""

import math

REPRESENTATION_VERSION = "cc-relational-reference-2"
AUTHOR = "cc-engineering-reference-not-the-opus46-generated-artifact"
MOTION_ACTIONS = ("goto_pose", "move_to_joints", "goto_home_joint_position")
OPEN_RELATION = ("unverified", "indistinguishable", "out_of_model")
ROTATION_SCOPE_RAD = 0.15
# A supported relation is only applicable inside the scope it was verified in.
# Every other scope value suspends it until new valid evidence arrives.
APPLICABLE_SCOPE = "within_verified_scope"
SUSPENDED_SCOPE = ("outside_verified_rotation", "invalidated_failed_motion",
                   "invalidated_missing_proprioception")
DEFERRED = ("representation_regeneration", "identity_merge_split",
            "scene_wide_adaptive_scheduling", "placement_support_verification",
            "calibrated_probabilities", "mass_and_friction_identification")
UNCERTAINTY = {"mass": "unknown_not_estimated",
               "hidden_geometry": "unknown_visible_surface_only",
               "contact": "unknown_no_force_or_tactile_evidence",
               "scale_semantics": "uncalibrated_not_a_probability"}

# --- numeric helpers (no imports beyond math, no classes, no globals as state)


def _num(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _vector(value, size):
    if isinstance(value, (list, tuple)) and len(value) == size and all(_num(v) for v in value):
        return [float(v) for v in value]
    return None


def _bounds(value):
    if isinstance(value, (list, tuple)) and len(value) == 2:
        low, high = _vector(value[0], 3), _vector(value[1], 3)
        if low is not None and high is not None:
            return [low, high]
    return None


def _clone(value):
    if isinstance(value, dict):
        return {key: _clone(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clone(item) for item in value]
    return value


def _add(left, right):
    return [left[0] + right[0], left[1] + right[1], left[2] + right[2]]


def _subtract(left, right):
    return [left[0] - right[0], left[1] - right[1], left[2] - right[2]]


def _unit_quaternion(value):
    quaternion = _vector(value, 4)
    if quaternion is None:
        return None
    norm = math.sqrt(sum(c * c for c in quaternion))
    if not math.isfinite(norm) or norm < 1e-9:
        return None
    return [c / norm for c in quaternion]


def _conjugate(quaternion):
    return [quaternion[0], -quaternion[1], -quaternion[2], -quaternion[3]]


def _rotate(quaternion, vector):
    """Rotate by a WXYZ local-hand-to-world quaternion: v + 2w(u x v) + 2u x (u x v)."""
    w, x, y, z = quaternion
    ux, uy, uz = vector
    cx, cy, cz = y * uz - z * uy, z * ux - x * uz, x * uy - y * ux
    dx, dy, dz = y * cz - z * cy, z * cx - x * cz, x * cy - y * cx
    return [ux + 2.0 * (w * cx + dx), uy + 2.0 * (w * cy + dy), uz + 2.0 * (w * cz + dz)]


def _angle_between(first, second):
    dot = abs(sum(a * b for a, b in zip(first, second)))
    return 2.0 * math.acos(max(-1.0, min(1.0, dot)))


def _pose(robot_state):
    """Measured proprioception only; a commanded target is never accepted here."""
    if not isinstance(robot_state, dict):
        return None
    position = _vector(robot_state.get("position"), 3)
    orientation = _unit_quaternion(robot_state.get("orientation_wxyz"))
    if position is None or orientation is None:
        return None
    gripper = robot_state.get("gripper")
    return {"position": position, "orientation_wxyz": orientation,
            "gripper": float(gripper) if _num(gripper) else None}


# --- relation bookkeeping


def _fresh_grasp(attempt_id, closed, reason):
    return {"attempt_id": attempt_id, "attempt_frame": None, "closed": bool(closed),
            "reference": None, "attached_hypothesis_id": None, "free_hypothesis_id": None,
            "relation_status": "no_attempt" if attempt_id is None else "unverified",
            "relation_reason": reason, "relation_evidence_id": None,
            "reference_evidence_id": None, "verified_scope": None,
            "scope_status": "no_verified_scope"}


def _applicable(grasp):
    """Support is usable only while its verified scope still holds. A failed
    motion or an unavailable current pose suspends it; only new valid evidence
    (or a new reference) can restore it, never the mere passing of a step."""
    return (grasp["relation_status"] == "supported"
            and grasp["scope_status"] == APPLICABLE_SCOPE)


def _suspend(state, scope_status, reason):
    """Withdraw applicability of a supported relation without inventing a new
    verdict about contact: the relation becomes unusable, not refuted."""
    grasp = state["grasp"]
    if grasp["relation_status"] != "supported":
        grasp["relation_reason"] = reason
        return False
    grasp["scope_status"] = scope_status
    grasp["relation_reason"] = reason
    record = _object(state)
    if record["attachment"] == "attached":
        record["attachment"] = "candidate"
        record["attachment_reason"] = reason
    if record["position_source"] in ("observed", "propagated_attached"):
        record["position_source"] = "stale_estimate_support_suspended"
        record["uncertainty"]["position"] = "last_value_not_current_support_suspended"
    return True


def _condition(state):
    grasp = state["grasp"]
    status = {"supported": "supported", "refuted": "refuted"}.get(grasp["relation_status"], "unknown")
    if status == "supported" and not _applicable(grasp):
        status = "unknown"
    return {"name": "transport_requires_attached_relation",
            "object_id": state["manipulated_id"],
            "status": status,
            "reason": grasp["relation_reason"],
            "depends_on": {"representation_version": REPRESENTATION_VERSION,
                           "attempt_id": grasp["attempt_id"],
                           "reference_frame": (grasp["reference"] or {}).get("frame"),
                           "reference_evidence_id": grasp["reference_evidence_id"],
                           "relation_evidence_id": grasp["relation_evidence_id"],
                           "attached_hypothesis_id": grasp["attached_hypothesis_id"],
                           "free_hypothesis_id": grasp["free_hypothesis_id"],
                           "scope_status": grasp["scope_status"]},
            "valid_while": ["gripper stays closed since the reference frame",
                            "no new open_gripper or close_gripper",
                            "rigid hand-local offset model holds",
                            "hand rotation stays within the verified scope",
                            "evidence frame is strictly after the reference frame",
                            "evidence answered the query for the frame it is assimilated at"],
            "not_established": ["mass", "contact_force", "hidden_geometry",
                                "placement_support_at_target"]}


def _require_state(state):
    if not isinstance(state, dict) or "objects" not in state or "grasp" not in state:
        raise ValueError("state is not a relational scene state")
    if state.get("representation_version") != REPRESENTATION_VERSION:
        raise ValueError("unexpected representation version")
    return state


def _object(state):
    return state["objects"][state["manipulated_id"]]


def _mark_stale_bounds(record):
    if record["visible_bounds"] is not None and record["position_source"] != "observed":
        record["bounds_status"] = "last_observed_visible_surface_not_current"


def _observation_record(position, frame, bounds, bounds_frame, evidence_id,
                        source, reason, metadata):
    """The last actually measured values, kept beside the working estimate so
    propagation can never overwrite or launder a measurement."""
    return {"position": _clone(position), "frame": frame,
            "visible_bounds": _clone(bounds), "bounds_frame": bounds_frame,
            "evidence_id": evidence_id, "evidence_source": source,
            "measurement_reason": reason,
            # verbatim provider metadata (scores, point counts, coverage notes)
            "measurement_metadata": _clone(metadata),
            "uncertainty": "observed_visible_surface_median_uncalibrated",
            "semantics": "measured_value_at_its_own_frame_never_propagated"}


def _bounds_origin(position, orientation, frame):
    """Geometry is stored with the pose it was measured at, so a later rigid
    transform is applied *relative to that frame* instead of an older centre."""
    if position is None:
        return None
    return {"centre": _clone(position), "hand_orientation_wxyz": _clone(orientation),
            "frame": frame,
            "semantics": "box_measured_at_this_centre_and_hand_orientation"}


def _step_fields(step):
    if not isinstance(step, dict):
        raise ValueError("step must be an object")
    index = step.get("index")
    if not isinstance(index, int) or isinstance(index, bool) or index < 0:
        raise ValueError("step index must be a nonnegative integer")
    action = step.get("action")
    api = action.get("api") if isinstance(action, dict) else None
    budget = step.get("budget")
    remaining = budget.get("remaining") if isinstance(budget, dict) else None
    return {"index": index, "api": api if isinstance(api, str) else None,
            "failed": step.get("action_error") is not None,
            "pose": _pose(step.get("robot_state")),
            "remaining": int(remaining) if isinstance(remaining, int) and not isinstance(remaining, bool) else 0}


# --- initialize


def initialize(context):
    if not isinstance(context, dict):
        raise ValueError("context must be an object")
    entities = context.get("entities")
    if not isinstance(entities, list) or not entities:
        raise ValueError("context must list at least one scene entity")
    anchor_pose = _pose(context.get("robot_state"))
    objects = {}
    order = []
    manipulated = None
    for entity in entities:
        if not isinstance(entity, dict) or not isinstance(entity.get("id"), str):
            raise ValueError("every entity needs a string id")
        identifier = entity["id"]
        if identifier in objects:
            raise ValueError("duplicate entity id: " + identifier)
        measurement = entity.get("measurement")
        measurement = measurement if isinstance(measurement, dict) else {}
        observed = measurement.get("status") == "ok"
        position = _vector(measurement.get("position"), 3) if observed else None
        bounds = _bounds(measurement.get("visible_bounds")) if position is not None else None
        # Everything the measurement carried beyond the fields interpreted here
        # is retained, not discarded: scores, coverage notes, point counts.
        extra = {key: _clone(value) for key, value in measurement.items()
                 if key not in ("position", "visible_bounds", "status", "reason")}
        objects[identifier] = {
            "id": identifier,
            "role": entity.get("role"),
            "label": entity.get("label"),
            # Every non-measurement field of the entity is preserved verbatim,
            # including fields this program does not interpret.
            "metadata": {key: _clone(value) for key, value in entity.items() if key != "measurement"},
            "position": position,
            "position_source": "observed" if position is not None else "unknown",
            "position_frame": 0 if position is not None else None,
            "visible_bounds": bounds,
            "bounds_frame": 0 if bounds is not None else None,
            "bounds_status": "last_observed_visible_surface" if bounds is not None else "unknown",
            # geometry keeps the pose it was measured at
            "bounds_origin": _bounds_origin(
                position, (anchor_pose or {}).get("orientation_wxyz"), 0) if bounds is not None else None,
            "last_observation": _observation_record(
                position, 0 if position is not None else None, bounds,
                0 if bounds is not None else None, None, "scene_anchor",
                measurement.get("reason"), extra) if position is not None else None,
            "measurement_metadata": extra,
            "attachment": "free" if position is not None else "unmodeled",
            "attachment_reason": ("initial_observation_no_interaction" if position is not None
                                  else "position_unknown_at_anchor_frame"),
            "observation_reason": measurement.get("reason"),
            "uncertainty": dict(UNCERTAINTY, position=("observed_visible_surface_median"
                                                      if position is not None else "unknown")),
        }
        order.append(identifier)
        if entity.get("role") == "manipulated":
            if manipulated is not None:
                raise ValueError("exactly one manipulated entity is supported")
            manipulated = identifier
    if manipulated is None:
        raise ValueError("context has no manipulated entity")
    state = {"representation_version": REPRESENTATION_VERSION,
             "author": AUTHOR,
             "deferred": list(DEFERRED),
             "objects": objects,
             "entity_order": order,
             "manipulated_id": manipulated,
             "robot_state": anchor_pose,
             "pose_frame": 0 if anchor_pose is not None else None,
             "proprioception": "measured" if anchor_pose is not None else "unavailable_at_anchor",
             "frame": 0,
             "grasp": _fresh_grasp(None, False, "no_grasp_attempt_yet"),
             "evidence_log": [],
             "evidence_count": 0,
             "next_evidence_id": 1,
             "task": context.get("task")}
    state["conditions"] = [_condition(state)]
    return state


# --- advance


def _attached_estimate(reference, pose):
    return _add(pose["position"], _rotate(pose["orientation_wxyz"], reference["offset_local"]))


def _close_gripper(state, fields):
    record = _object(state)
    if fields["failed"]:
        # A failed close neither binds a reference nor preserves any earlier one.
        state["grasp"] = _fresh_grasp(state["grasp"]["attempt_id"], False, "close_action_failed")
        state["grasp"]["relation_status"] = "no_attempt"
        if record["attachment"] in ("attached", "candidate"):
            record["attachment"] = "unknown"
            record["attachment_reason"] = "close_action_failed_contact_unknown"
        return
    attempt = (state["grasp"]["attempt_id"] or 0) + 1
    grasp = _fresh_grasp(attempt, True, "closed_awaiting_reference_observation")
    grasp["attempt_frame"] = fields["index"]
    state["grasp"] = grasp
    record["attachment"] = "candidate"
    record["attachment_reason"] = "gripper_closed_contact_unknown_relation_unverified"
    if record["position_source"] != "observed":
        # Reclosing discards any released or propagated coordinate: the new
        # attempt must bind a fresh reference from a fresh observation.
        record["position_source"] = "stale_estimate_awaiting_reference"
    _mark_stale_bounds(record)


def _open_gripper(state, fields):
    record = _object(state)
    grasp = state["grasp"]
    if fields["failed"]:
        if record["attachment"] in ("attached", "candidate"):
            record["attachment"] = "unknown"
            record["attachment_reason"] = "open_action_failed_release_unknown"
        reason = "open_action_failed_reference_invalidated"
    else:
        if record["attachment"] in ("attached", "candidate", "unmodeled", "unknown"):
            # Release happens at the object's own estimated position, never at
            # the hand position, and the outcome of the release is unknown.
            record["attachment"] = "released"
            record["attachment_reason"] = "gripper_opened_at_last_object_estimate"
            if record["position_source"] in ("propagated_attached", "stale_estimate_awaiting_reference"):
                record["position_source"] = "released_estimate"
            record["uncertainty"]["release_outcome"] = (
                "unknown_settled_supported_or_falling_not_observed")
        reason = "opening_invalidates_grasp_reference_and_support"
    state["grasp"] = _fresh_grasp(grasp["attempt_id"], False, reason)
    state["grasp"]["relation_status"] = "no_attempt"
    _mark_stale_bounds(record)


def _propagate(state, fields):
    record = _object(state)
    grasp = state["grasp"]
    reference = grasp["reference"]
    pose = fields["pose"]
    if pose is None or not isinstance(reference, dict) or not grasp["closed"]:
        return
    if _applicable(grasp) and record["attachment"] == "attached":
        record["position"] = _attached_estimate(reference, pose)
        record["position_source"] = "propagated_attached"
        record["position_frame"] = fields["index"]
        record["uncertainty"]["position"] = "propagated_from_supported_rigid_offset"
        _mark_stale_bounds(record)
    scope = grasp["verified_scope"]
    if isinstance(scope, dict) and scope.get("orientation_wxyz") is not None:
        angle = _angle_between(scope["orientation_wxyz"], pose["orientation_wxyz"])
        if angle > ROTATION_SCOPE_RAD:
            grasp["scope_status"] = "outside_verified_rotation"
            grasp["relation_reason"] = "hand_rotated_beyond_verified_translation_evidence"


def advance(state, step):
    state = _require_state(state)
    fields = _step_fields(step)
    state["frame"] = fields["index"]
    if fields["pose"] is not None:
        state["robot_state"] = fields["pose"]
        state["pose_frame"] = fields["index"]
        state["proprioception"] = "measured"
    else:
        # The last measured pose is kept for provenance only. It is NOT a
        # current pose, so nothing downstream may treat it as one.
        state["pose_frame"] = None
        state["proprioception"] = "unavailable_last_pose_is_stale_not_current"
        _suspend(state, "invalidated_missing_proprioception",
                 "current_pose_unavailable_support_not_applicable")
    if fields["api"] == "close_gripper":
        _close_gripper(state, fields)
    elif fields["api"] == "open_gripper":
        _open_gripper(state, fields)
    elif fields["api"] in MOTION_ACTIONS and not fields["failed"]:
        _propagate(state, fields)
    elif fields["api"] in MOTION_ACTIONS:
        # A failed motion leaves the true pose reached unknown, so neither the
        # propagated estimate nor the earlier support survives it.
        _suspend(state, "invalidated_failed_motion",
                 "motion_action_failed_no_valid_motion_evidence")
    state["conditions"] = [_condition(state)]
    return state


# --- predict


def _current_pose(state, fields):
    """Only a pose measured *at this frame* counts. A retained earlier pose is
    provenance, never a substitute for a current measurement."""
    if fields["pose"] is not None:
        return fields["pose"]
    if state.get("pose_frame") == fields["index"] and isinstance(state.get("robot_state"), dict):
        return state["robot_state"]
    return None


def _propagated_bounds(record, position, pose):
    """Axis-aligned hull of the last-observed box, transformed from the pose the
    box was actually measured at to the current pose. Geometry travels with its
    own measurement frame, so a newer box is never re-offset by an older
    translation. It approximates a visible-surface box, never full geometry."""
    bounds = record["visible_bounds"]
    origin = record.get("bounds_origin")
    if bounds is None or position is None or not isinstance(origin, dict):
        return None
    reference_orientation = _unit_quaternion(origin.get("hand_orientation_wxyz"))
    centre = _vector(origin.get("centre"), 3)
    if reference_orientation is None or centre is None:
        return None
    low, high = bounds
    corners = []
    for x in (low[0], high[0]):
        for y in (low[1], high[1]):
            for z in (low[2], high[2]):
                local = _rotate(_conjugate(reference_orientation),
                                _subtract([x, y, z], centre))
                corners.append(_add(position, _rotate(pose["orientation_wxyz"], local)))
    return [[min(c[i] for c in corners) for i in (0, 1, 2)],
            [max(c[i] for c in corners) for i in (0, 1, 2)]]


def _hypotheses(state, fields):
    """Attached versus stationary, kept separate and never merged."""
    grasp = state["grasp"]
    record = _object(state)
    reference = grasp["reference"]
    pose = _current_pose(state, fields)
    if isinstance(reference, dict) and pose is None:
        # Abstain rather than evaluate a hand-local offset at an unknown pose.
        return {"reference_frame": None, "attached": None, "free": None,
                "basis": "current_pose_unavailable_no_hypothesis_evaluated"}
    if isinstance(reference, dict):
        return {"reference_frame": reference["frame"],
                "attached": _attached_estimate(reference, pose),
                "free": _clone(reference["object_position"]),
                "basis": "hand_local_offset_versus_stationary_at_reference"}
    if record["position_source"] == "observed":
        return {"reference_frame": None,
                "attached": _clone(record["position"]),
                "free": _clone(record["position"]),
                "basis": "no_reference_bound_both_at_last_observation"}
    return {"reference_frame": None, "attached": None, "free": None,
            "basis": "no_reference_and_no_current_observation"}


def _query_request(state, fields, hypotheses):
    grasp = state["grasp"]
    if fields["remaining"] <= 0:
        return "none", False, "query_budget_exhausted"
    if fields["failed"]:
        return "none", False, "action_failed_no_valid_evidence"
    if fields["api"] == "close_gripper" and grasp["closed"] and grasp["reference"] is None:
        return "reference", True, "calibrate_hand_local_offset_not_grasp_evidence"
    if fields["api"] not in MOTION_ACTIONS or not grasp["closed"]:
        return "none", False, "no_discriminating_motion"
    reference_frame = hypotheses["reference_frame"]
    if reference_frame is None or reference_frame >= fields["index"]:
        return "none", False, "missing_or_non_independent_reference"
    unresolved = grasp["relation_status"] in OPEN_RELATION
    suspended = grasp["scope_status"] in SUSPENDED_SCOPE
    if not unresolved and not suspended:
        return "none", False, "relation_already_resolved_within_scope"
    if unresolved:
        return "relation", True, "discriminate_attached_versus_stationary"
    if grasp["scope_status"] == "outside_verified_rotation":
        return "relation", True, "recheck_outside_verified_scope"
    return "relation", True, "recheck_after_scope_invalidated"


def predict(state, step):
    state = _require_state(state)
    fields = _step_fields(step)
    grasp = state["grasp"]
    manipulated = state["manipulated_id"]
    hypotheses = _hypotheses(state, fields)
    purpose, request, reason = _query_request(state, fields, hypotheses)
    current = _current_pose(state, fields)
    objects = {}
    for identifier in state["entity_order"]:
        record = state["objects"][identifier]
        position = _clone(record["position"])
        source = record["position_source"]
        if identifier == manipulated and _applicable(grasp):
            if isinstance(grasp["reference"], dict) and current is not None:
                position = _attached_estimate(grasp["reference"], current)
                source = "propagated_attached"
        objects[identifier] = {
            "position": position,
            "position_source": source,
            "position_frame": record["position_frame"],
            "attachment": record["attachment"],
            "attachment_reason": record["attachment_reason"],
            # Bounds are never presented as current: they carry the frame they
            # were observed at, and a derived hull is reported separately.
            "visible_bounds_last_observed": _clone(record["visible_bounds"]),
            "bounds_frame": record["bounds_frame"],
            "bounds_status": record["bounds_status"],
            # the measurement itself, never overwritten by propagation
            "last_observation": _clone(record.get("last_observation")),
            "measurement_metadata": _clone(record.get("measurement_metadata")),
            "uncertainty": _clone(record["uncertainty"]),
        }
    record = state["objects"][manipulated]
    if _applicable(grasp) and isinstance(grasp["reference"], dict) and current is not None:
        hull = _propagated_bounds(record, objects[manipulated]["position"], current)
        if hull is not None:
            objects[manipulated]["derived_bounds"] = hull
            objects[manipulated]["derived_bounds_basis"] = (
                "last_observed_visible_box_rotated_from_its_own_measurement_pose_"
                "axis_aligned_hull")
        else:
            objects[manipulated]["derived_bounds"] = None
            objects[manipulated]["derived_bounds_basis"] = (
                "no_geometry_with_a_known_measurement_pose")
    return {"objects": objects,
            "request_query": request,
            "query_purpose": purpose,
            "query_reason": reason,
            "representation_version": REPRESENTATION_VERSION,
            "grasp_check": {"object_id": manipulated,
                            "reference_frame": hypotheses["reference_frame"],
                            "attached_position": hypotheses["attached"],
                            "free_position": hypotheses["free"]},
            "hypotheses": {"attached_id": grasp["attached_hypothesis_id"],
                           "free_id": grasp["free_hypothesis_id"],
                           "basis": hypotheses["basis"],
                           "relation_status": grasp["relation_status"],
                           "relation_reason": grasp["relation_reason"],
                           "scope_status": grasp["scope_status"]},
            "conditions": _clone(state["conditions"]),
            "deferred": list(DEFERRED)}


# --- assimilate


_RELATION_UNKNOWN = {
    "both_inconsistent": ("unmodeled", "out_of_model",
                          "both_hypotheses_inconsistent_model_or_readout_insufficient"),
    "both_consistent": ("candidate", "indistinguishable",
                        "hypotheses_not_separated_by_this_motion"),
    "insufficient_motion": ("candidate", "indistinguishable",
                            "motion_below_separation_gate"),
    "measurement_unavailable": ("candidate", "unverified",
                                "measurement_unavailable_absence_is_not_evidence"),
    "missing_independent_reference": ("candidate", "unverified",
                                     "no_independent_reference_for_this_frame"),
}


def _observe(record, evidence, frame, pose=None, evidence_id=None):
    """Fold a valid measurement in as observed state; absence changes nothing.

    The measurement is *also* stored verbatim in ``last_observation`` so later
    propagation can overwrite the working estimate without ever destroying the
    last thing actually measured, and new geometry is tagged with the pose it
    was measured at rather than inheriting an older reference centre."""
    position = _vector(evidence.get("position"), 3)
    if evidence.get("status") != "ok" or position is None:
        return False
    record["position"] = position
    record["position_source"] = "observed"
    record["position_frame"] = frame
    record["uncertainty"]["position"] = "observed_visible_surface_median"
    bounds = _bounds(evidence.get("visible_bounds"))
    if bounds is not None:
        record["visible_bounds"] = bounds
        record["bounds_frame"] = frame
        record["bounds_status"] = "last_observed_visible_surface"
        record["bounds_origin"] = _bounds_origin(
            position, pose["orientation_wxyz"] if isinstance(pose, dict) else None, frame)
    extra = {key: _clone(value) for key, value in evidence.items()
             if key not in ("position", "visible_bounds", "status", "reason", "comparison",
                            "robot_state", "purpose", "frame", "object_id")}
    record["measurement_metadata"] = extra
    record["last_observation"] = _observation_record(
        position, frame, record["visible_bounds"] if bounds is not None else None,
        frame if bounds is not None else None, evidence_id,
        evidence.get("purpose") or "observation", evidence.get("reason"), extra)
    return True


def _reference_scope_reason(grasp, frame, state_frame):
    """Why reference evidence is out of scope, or None when it is admissible.

    Reference evidence must belong to the current, successful close attempt AND
    to the current frame. This is a synchronous protocol: ``assimilate`` answers
    the query ``predict`` raised for the frame the world is on now. Evidence
    carrying an earlier frame is a *late* answer to a superseded question, and
    the actions taken in between (a failed motion, a lost pose, an open) may have
    already invalidated what it would assert. Such evidence is still logged, but
    it may not calibrate an offset that current control would rely on."""
    if not grasp["closed"] or grasp["attempt_id"] is None:
        return "reference_evidence_outside_current_close_attempt"
    if not isinstance(frame, int) or isinstance(frame, bool):
        return "reference_evidence_frame_not_an_integer"
    attempt_frame = grasp["attempt_frame"]
    if isinstance(attempt_frame, int) and frame < attempt_frame:
        return "reference_evidence_predates_current_close_attempt"
    if frame != state_frame:
        return ("reference_evidence_from_a_future_frame" if frame > state_frame
                else "reference_evidence_stale_not_from_the_current_frame")
    return None


def _bind_reference(state, evidence, frame, evidence_id):
    grasp = state["grasp"]
    record = _object(state)
    pose = _pose(evidence.get("robot_state"))
    position = _vector(evidence.get("position"), 3)
    out_of_scope = _reference_scope_reason(grasp, frame, state["frame"])
    if out_of_scope is not None:
        grasp["relation_reason"] = out_of_scope
        return "reference_rejected_out_of_scope"
    if pose is None or position is None or evidence.get("status") != "ok":
        grasp["reference"] = None
        grasp["reference_evidence_id"] = None
        grasp["relation_status"] = "unverified"
        grasp["relation_reason"] = "reference_observation_unavailable_no_valid_reference"
        if record["attachment"] == "candidate":
            record["attachment"] = "unknown"
            record["attachment_reason"] = "reference_unavailable_relation_unknown"
        return "reference_rejected"
    attempt = grasp["attempt_id"] or 0
    grasp["reference"] = {
        "frame": frame,
        "hand_position": pose["position"],
        "orientation_wxyz": pose["orientation_wxyz"],
        "object_position": position,
        # offset expressed in the hand frame: R_ref^T (p_object - p_hand)
        "offset_local": _rotate(_conjugate(pose["orientation_wxyz"]),
                                _subtract(position, pose["position"])),
        "evidence_id": evidence_id,
        "semantics": "calibration_of_relative_offset_not_evidence_of_grasp",
    }
    grasp["reference_evidence_id"] = evidence_id
    grasp["attached_hypothesis_id"] = "H-attached-" + str(attempt)
    grasp["free_hypothesis_id"] = "H-free-" + str(attempt)
    grasp["relation_status"] = "unverified"
    grasp["relation_reason"] = "reference_bound_awaiting_independent_motion"
    grasp["scope_status"] = "no_verified_scope"
    _observe(record, evidence, frame, pose, evidence_id)
    record["attachment"] = "candidate"
    record["attachment_reason"] = "reference_bound_contact_still_unknown"
    return "reference_bound"


def _distance(left, right):
    return math.sqrt(sum((a - b) * (a - b) for a, b in zip(left, right)))


def _verdict_is_independently_consistent(reference, pose, position, status):
    """Recompute both hypotheses from the reference and this frame's measured
    pose. A comparison field saying SUPPORT is not accepted unless the attached
    hypothesis really is the closer one here; the same for CONTRADICT."""
    attached = _attached_estimate(reference, pose)
    free = reference["object_position"]
    attached_error, free_error = _distance(attached, position), _distance(free, position)
    if status == "SUPPORT":
        return attached_error < free_error
    if status == "CONTRADICT":
        return free_error < attached_error
    return True


def _revise_relation(state, evidence, frame, evidence_id, comparison):
    grasp = state["grasp"]
    record = _object(state)
    reference = grasp["reference"]
    status = comparison.get("status")
    reason = comparison.get("reason")
    pose = _pose(evidence.get("robot_state"))
    position = _vector(evidence.get("position"), 3)
    if not isinstance(reference, dict) or not grasp["closed"]:
        grasp["relation_status"] = "unverified"
        grasp["relation_reason"] = "relation_evidence_without_valid_reference"
        return "relation_rejected_no_reference"
    if (not isinstance(frame, int) or isinstance(frame, bool)
            or frame <= reference["frame"]):
        grasp["relation_reason"] = "relation_evidence_not_after_reference_frame"
        return "relation_rejected_out_of_scope"
    if frame != state["frame"]:
        # Same synchronous rule as the reference scope: a verdict computed for an
        # earlier frame is a late answer, and the steps taken since (a failed
        # motion, an unavailable pose) may already have invalidated it. Logging
        # it is fine; letting it decide *current* applicability is not, because
        # nothing here re-verifies it against the world's present state.
        grasp["relation_reason"] = ("relation_evidence_from_a_future_frame"
                                    if frame > state["frame"]
                                    else "relation_evidence_stale_not_from_the_current_frame")
        return "relation_rejected_out_of_scope"
    if status in ("SUPPORT", "CONTRADICT") and (
            evidence.get("status") != "ok" or position is None or pose is None
            or not _verdict_is_independently_consistent(reference, pose, position, status)):
        # Verification is a separate act from reading a field. Unavailable,
        # pose-less or numerically unsupported evidence cannot decide the
        # relation, and must not refresh an older support either.
        _suspend(state, "invalidated_missing_proprioception" if pose is None
                 else "invalidated_failed_motion",
                 "relation_verdict_not_independently_reproducible")
        if grasp["relation_status"] != "supported":
            grasp["relation_status"] = "unverified"
            grasp["relation_reason"] = "relation_verdict_not_independently_reproducible"
        return "relation_rejected_unverifiable_verdict"
    _observe(record, evidence, frame, pose, evidence_id)
    if status == "SUPPORT":
        record["attachment"] = "attached"
        record["attachment_reason"] = "attached_hypothesis_only_consistent"
        grasp["relation_status"] = "supported"
        grasp["relation_reason"] = "attached_only_consistent_within_tolerance"
        grasp["relation_evidence_id"] = evidence_id
        grasp["verified_scope"] = {
            "reference_frame": reference["frame"], "evidence_frame": frame,
            "orientation_wxyz": pose["orientation_wxyz"],
            "separation_m": comparison.get("separation_m"),
            "covers": "translation_between_reference_and_this_frame"}
        grasp["scope_status"] = APPLICABLE_SCOPE
        return "attachment_supported"
    if status == "CONTRADICT":
        record["attachment"] = "free"
        record["attachment_reason"] = "stationary_hypothesis_only_consistent"
        grasp["reference"] = None
        grasp["attached_hypothesis_id"] = None
        grasp["free_hypothesis_id"] = None
        grasp["reference_evidence_id"] = None
        grasp["verified_scope"] = None
        grasp["scope_status"] = "no_verified_scope"
        grasp["relation_status"] = "refuted"
        grasp["relation_reason"] = "free_only_consistent_attachment_refuted"
        grasp["relation_evidence_id"] = evidence_id
        return "attachment_refuted"
    attachment, relation, note = _RELATION_UNKNOWN.get(
        reason, ("candidate", "unverified", "unknown_comparison_reason"))
    record["attachment"] = attachment
    record["attachment_reason"] = note
    grasp["relation_status"] = relation
    grasp["relation_reason"] = note
    if relation == "out_of_model":
        grasp["verified_scope"] = None
        grasp["scope_status"] = "no_verified_scope"
    return "relation_unresolved"


def assimilate(state, evidence):
    state = _require_state(state)
    if not isinstance(evidence, dict):
        raise ValueError("evidence must be an object")
    grasp = state["grasp"]
    record = _object(state)
    comparison = evidence.get("comparison")
    comparison = comparison if isinstance(comparison, dict) else {}
    frame = evidence.get("frame")
    purpose = evidence.get("purpose")
    evidence_id = state["next_evidence_id"]
    state["next_evidence_id"] = evidence_id + 1
    state["evidence_count"] = state.get("evidence_count", 0) + 1
    reference = grasp["reference"]
    pose = _pose(evidence.get("robot_state"))
    entry = {"evidence_id": evidence_id, "frame": frame, "purpose": purpose,
             "object_id": evidence.get("object_id"),
             "measurement_status": evidence.get("status"),
             "measurement_reason": evidence.get("reason"),
             "comparison_status": comparison.get("status"),
             "comparison_reason": comparison.get("reason"),
             "attached_error_m": comparison.get("attached_error_m"),
             "free_error_m": comparison.get("free_error_m"),
             "separation_m": comparison.get("separation_m"),
             "representation_version": REPRESENTATION_VERSION,
             "attempt_id": grasp["attempt_id"],
             "attached_hypothesis_id": grasp["attached_hypothesis_id"],
             "free_hypothesis_id": grasp["free_hypothesis_id"],
             # pre-update predictions and prior belief are kept, never overwritten
             "pre_update_attachment": record["attachment"],
             "pre_update_relation_status": grasp["relation_status"],
             "pre_update_scope_status": grasp["scope_status"],
             "pre_update_last_observation": _clone(record.get("last_observation")),
             "pre_update_object_position": _clone(record["position"]),
             "pre_update_attached_prediction": (
                 _attached_estimate(reference, pose)
                 if isinstance(reference, dict) and pose is not None else None),
             "pre_update_free_prediction": (_clone(reference["object_position"])
                                            if isinstance(reference, dict) else None),
             "duplicate_of": None,
             "outcome": None}
    for prior in state["evidence_log"]:
        if (prior["frame"] == frame and prior["purpose"] == purpose
                and prior["object_id"] == entry["object_id"]
                and prior["attempt_id"] == grasp["attempt_id"]):
            entry["duplicate_of"] = prior["evidence_id"]
            break
    if evidence.get("object_id") != state["manipulated_id"]:
        # Evidence about another entity updates that entity only, and can never
        # revise or refresh the grasp relation.
        other = state["objects"].get(evidence.get("object_id"))
        entry["outcome"] = ("background_observation_recorded" if other is not None
                            and _observe(other, evidence, frame, pose, evidence_id)
                            else "out_of_scope_ignored")
    elif entry["duplicate_of"] is not None:
        # Repeated evidence is not independent support; fold in the observation
        # but leave the relation verdict exactly as it stands.
        _observe(record, evidence, frame, pose, evidence_id)
        entry["outcome"] = "duplicate_evidence_no_relation_change"
    elif purpose == "reference":
        entry["outcome"] = _bind_reference(state, evidence, frame, evidence_id)
    elif purpose == "relation":
        entry["outcome"] = _revise_relation(state, evidence, frame, evidence_id, comparison)
    else:
        _observe(record, evidence, frame, pose, evidence_id)
        entry["outcome"] = "observation_only_unknown_purpose"
    _mark_stale_bounds(record)
    state["evidence_log"] = (state["evidence_log"] + [entry])[-32:]
    state["conditions"] = [_condition(state)]
    return state




