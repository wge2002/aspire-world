# SPDX-License-Identifier: Apache-2.0
"""Held-object relative geometry from public RGB-D and public robot pose.

A binding records, from ONE synchronized measurement (object frame and robot
fact within ``relation_max_skew``), the tracked object's visible features in
the robot_base TCP frame (rotation = panda_hand rotation, origin = API TCP).
Later measured frames test whether that relation persists while the hand
moves. Nothing here reads simulator state, and the hand command is never an
object measurement:

* measured: a fresh tracked mask with depth, synchronized robot pose;
* predicted: the bound relation composed with the CURRENT public hand pose
  (labelled prediction, never a measurement);
* unknown: occlusion, missing depth, stale/unsynchronized evidence, identity
  interruption (tracking loss, identity jump, reseed) until a new measurement.

How a measured frame is judged (docs/visible-held-placement.md):

1. Held-relative correspondences. Interior (not occluding-contour) texture
   features are flowed forward/backward from the previous synchronized held
   frame, expressed in each frame's own TCP frame and rigidly registered. A
   rigidly held object gives the identity; the chained product since the
   anchor is the measured relative drift, with a first-order covariance. Only
   rotation axes whose 1-sigma is below ``OBSERVABLE_ANGLE_STD`` are judged;
   e.g. rotation about the line of a nearly collinear feature set stays
   unobservable. When this chain registered the frame it alone decides; the
   visible-centre median is then only a diagnostic.
2. Without a registered chain (low texture, occlusion, contour-only
   features, symmetric surface) nothing about relative motion is measured:
   a similar visible-surface signature (sorted PCA spans) or a visible centre
   inside the bound visible extent gives ``partial`` geometric consistency,
   otherwise ``undetermined``. A cylinder showing another face has the same
   spans but a shifted median, so neither similarity nor centre shift ever
   declares slip or verifies co-motion. "Did not see a change" is not a
   verified relation; the policy may still use the labelled partial geometry.

Correspondence-measured violation (beyond tolerance plus ``SIGNIFICANCE``
sigma on an observable component) ends the
binding as ``slip_detected``; so does a measured gripper closing beyond the
bound opening (object left the fingers). Measured opening ends it as
``released``; structure revision as ``invalidated``. A slip cannot be erased
by rebinding: a new binding of that identity requires a measured gripper
opening after the slip (an actual release/regrasp). A consistent relation is
not grasp or contact proof. Thresholds are declared heuristics.
"""
from __future__ import annotations

import copy

import numpy as np

from .geometry_queries import mask_points, principal_axes
from .rigid_tracking import flow_correspondences, rigid_registration
from .robot_frames import quaternion_from_rotation, rotation_wxyz

POSITION_TOLERANCE = .02   # m, default relative drift tolerance (heuristic)
ANGLE_TOLERANCE = .15      # rad, rigid relative rotation tolerance (heuristic)
EXCITATION = (.01, .1)     # m / rad hand motion that makes co-motion informative
OPENING_RELEASE = .1       # measured normalized opening increase that ends a hold
OPENING_LOSS = .1          # measured normalized closing beyond the bound opening: object left the fingers
MIN_FEATURE_POINTS = 10
EDGE_MARGIN = 2            # px; contour features are excluded from held correspondences
MIN_CORRESPONDENCES = 8    # equal to rigid_registration's minimum
OBSERVABLE_ANGLE_STD = .05     # rad, 1-sigma above which a rotation component is unobservable
OBSERVABLE_POSITION_STD = .02  # m, 1-sigma above which the reference translation is unobservable
SIGNIFICANCE = 2.0         # sigma multiplier added to tolerances before a violation is declared
SHAPE_TOLERANCE = (.006, .08)  # m / fraction: sorted visible spans of the "same visible surface"
POINTS_KEPT = 256          # deterministic subsample of TCP-frame visible points kept per measurement


def _tcp_frame(robot_fact):
    """Return (4x4 robot_base->TCP-frame pose, opening) or None from a World robot fact."""
    if robot_fact is None or robot_fact.get("status") != "known":
        return None
    view = (robot_fact.get("value") or {}).get("frame_view") or {}
    if view.get("status") != "known" or view.get("tcp_position") is None:
        return None
    T = np.eye(4)
    T[:3, :3] = rotation_wxyz(view["hand_quaternion_wxyz"])
    T[:3, 3] = view["tcp_position"]
    return T, view.get("gripper_opening")


def _to_local(T, points):
    return (np.asarray(points, dtype=float) - T[:3, 3]) @ T[:3, :3]


def _angle(R):
    return float(np.arccos(np.clip((np.trace(R) - 1) / 2, -1, 1)))


def _rotvec(R):
    angle = _angle(R)
    if angle < 1e-9:
        return np.zeros(3)
    if angle > np.pi - 1e-6:
        values, vectors = np.linalg.eigh((R + np.eye(3)) / 2)
        return vectors[:, -1] * angle
    w = np.array([R[2, 1] - R[1, 2], R[0, 2] - R[2, 0], R[1, 0] - R[0, 1]])
    return w / (2 * np.sin(angle)) * angle


def _skew(v):
    return np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])


def _features(points, T, up):
    local = _to_local(T, points)
    heights = points @ up
    lowest = points[int(np.argmin(heights))]
    axes = principal_axes(points) if len(points) >= 3 else {"long_axis_status": "unknown", "long_axis": None}
    long_axis = None
    if axes["long_axis_status"] == "known":
        long_axis = (np.asarray(axes["long_axis"]) @ T[:3, :3]).tolist()
    keep = np.unique(np.linspace(0, len(local) - 1, min(len(local), POINTS_KEPT)).astype(int))
    spans = sorted(axes.get("visible_spans", [0., 0., 0.]), reverse=True)
    return {"visible_center": np.median(local, axis=0).tolist(),
            "visible_bounds": [local.min(0).tolist(), local.max(0).tolist()],
            "lowest_visible_point": _to_local(T, lowest[None])[0].tolist(),
            "long_axis": long_axis, "long_axis_status": axes["long_axis_status"],
            "point_count": int(len(points)),
            "visible_points_tcp": local[keep].tolist(),
            "quality": {"point_count": int(len(points)), "visible_spans_sorted": [float(s) for s in spans],
                        "elongation_ratio": axes.get("elongation_ratio"),
                        "meaning": "visible surface only; PCA axis sign arbitrary; hidden extent unknown"}}


def _same_surface(a, b):
    a, b = np.asarray(a["quality"]["visible_spans_sorted"]), np.asarray(b["quality"]["visible_spans_sorted"])
    limit = np.maximum(SHAPE_TOLERANCE[0], SHAPE_TOLERANCE[1] * np.maximum(a, b))
    return bool(np.all(np.abs(a - b) <= limit))


def tcp_target_for_feature(feature_tcp, goal_point, quaternion_wxyz):
    """TCP position placing a TCP-frame feature at goal_point for a TCP orientation.

    Pure geometry under the assumption that the held relation stays rigid;
    the result is a prediction, not a reachability, contact or success claim.
    """
    feature, goal = np.asarray(feature_tcp, dtype=float), np.asarray(goal_point, dtype=float)
    if feature.shape != (3,) or goal.shape != (3,) or not np.isfinite([feature, goal]).all():
        raise ValueError("feature_tcp and goal_point must be finite XYZ vectors")
    return {"tcp_position": (goal - rotation_wxyz(quaternion_wxyz) @ feature).tolist(),
            "quaternion_wxyz": np.asarray(quaternion_wxyz, dtype=float).tolist(),
            "layer": "predicted", "assumption": "rigid held relation; verify by reobservation"}


class HeldGeometry:
    """Episode-local held-object bindings attached to a SemanticWorld."""

    def __init__(self, world, *, up=(0., 0., 1.)):
        self.world = world
        up = np.asarray(up, dtype=float)
        self.up = up / np.linalg.norm(up)
        self.reset()

    def reset(self):
        self.bindings, self.history = {}, []
        # Correspondence chains hold frames/masks; they are never copied into states.
        self._chains, self._epochs = {}, {}

    # --- binding lifecycle ------------------------------------------------

    def _points(self, identity, fact):
        good = self.world._last_good.get(identity)
        if good is None or good["frame"].frame_id != fact["frame_id"]:
            return None
        points = mask_points(good["frame"], good["mask"])
        return points if len(points) >= MIN_FEATURE_POINTS else None

    def _synchronized(self, fact):
        robot = self.world._robot
        pose = _tcp_frame(robot)
        if pose is None:
            return None, "public robot TCP pose is unavailable"
        if abs(robot["captured_at"] - fact["captured_at"]) > self.world.cadence.relation_max_skew:
            return None, "robot pose and object frame are not synchronized"
        return (robot, *pose), None

    def _anchor(self, identity, fact, T, *, since_bind, features):
        """Start a new correspondence chain at this measured, synchronized frame."""
        good = self.world._last_good[identity]
        epoch = self._epochs.get(identity, 0) + 1
        self._epochs[identity] = epoch
        self._chains[identity] = {"frame": good["frame"], "mask": good["mask"].copy(), "T": T.copy(),
                                  "D": np.eye(4), "rot_cov": np.zeros((3, 3)), "pos_cov": np.zeros((3, 3)),
                                  "reference": np.asarray(features["visible_center"], dtype=float),
                                  "epoch": epoch, "since_bind": since_bind, "steps": 0,
                                  "anchor_frame_id": fact["frame_id"], "min_inliers": None}
        return {"status": "anchored", "epoch": epoch, "anchor_frame_id": fact["frame_id"],
                "since_bind": since_bind, "source": "held-relative RGB-D correspondences (TCP frame)"}

    def _break_chain(self, identity):
        self._chains.pop(identity, None)

    def bind(self, identity, *, at, position_tolerance=POSITION_TOLERANCE, angle_tolerance=ANGLE_TOLERANCE):
        world = self.world
        for value in (position_tolerance, angle_tolerance):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not np.isfinite(value) or value <= 0:
                raise ValueError("held tolerances must be positive and finite")
        if identity not in world.parts or not world.parts[identity]["tracked"]:
            raise ValueError("a held binding needs a tracked part; tracked:false parts have no public geometry")
        previous = self.bindings.get(identity)
        if previous is not None and previous["status"] == "slip_detected" and not previous.get("regrasp_observed"):
            self._watch_regrasp(previous)
            if not previous.get("regrasp_observed"):
                raise ValueError("this identity's binding ended in slip_detected; rebinding cannot erase it. "
                                 "A new binding needs a measured gripper opening (release) and re-grasp first")
        fact = world.query(identity, at=at)
        if fact["status"] != "known":
            raise ValueError(f"held binding needs a fresh measured object ({fact.get('reason')})")
        sync, reason = self._synchronized(fact)
        if sync is None:
            raise ValueError(f"held binding needs a synchronized measurement: {reason}")
        robot, T, opening = sync
        points = self._points(identity, fact)
        if points is None:
            raise ValueError("held binding needs enough visible depth points in the current mask")
        features = _features(points, T, self.up)
        rigid = self._anchor(identity, fact, T, since_bind=True, features=features)
        binding = {"identity": identity, "status": "bound_unverified", "valid": True,
                   "coordinate_frame": world.coordinate_frame, "reference": f"{world.coordinate_frame}/TCP",
                   "units": "m, rad", "bound_at": fact["captured_at"], "frame_id": fact["frame_id"],
                   "robot_frame_id": robot["frame_id"],
                   "evidence_ids": sorted(set(fact["evidence_ids"]) | set(robot["evidence_ids"])),
                   "structure_revision": world.structure_revision, "gripper_opening": opening,
                   "tcp_pose": T.tolist(), "features_tcp": features,
                   "position_tolerance": float(position_tolerance), "angle_tolerance": float(angle_tolerance),
                   "rigid": rigid, "co_motion_verified": False, "verified_scope": None,
                   "identity_interrupted": False, "full_chain_since_bind": True,
                   "last_measurement": None, "reason": "bound from one synchronized measurement; "
                   "co-motion not yet observed",
                   "previous_binding_status": None if previous is None else previous["status"]}
        if previous is not None and previous["valid"]:
            self.history.append({"event": "superseded", "identity": identity, "at": fact["captured_at"]})
        self.bindings[identity] = binding
        self.history.append({"event": "bound", "identity": identity, "at": fact["captured_at"]})
        return self.state(identity, at=at)

    def end(self, identity, status, reason, *, at=None, **detail):
        binding = self.bindings.get(identity)
        if binding is None or not binding["valid"]:
            return
        binding.update(valid=False, status=status, reason=reason, ended_at=at, **detail)
        if status == "slip_detected":
            binding["regrasp_observed"] = False
            binding["opening_at_end"] = self._opening()
        self._break_chain(identity)
        self.history.append({"event": status, "identity": identity, "at": at, "reason": reason, **detail})

    def _opening(self):
        pose = _tcp_frame(self.world._robot)
        return None if pose is None else pose[1]

    def _watch_regrasp(self, binding):
        """A later measured opening above the post-slip opening is an actual release."""
        opening, base = self._opening(), binding.get("opening_at_end")
        if opening is None:
            return
        if base is None:
            binding["opening_at_end"] = opening
        elif opening - base > OPENING_RELEASE:
            binding["regrasp_observed"] = True
        else:
            binding["opening_at_end"] = min(base, opening)

    # --- World events -------------------------------------------------------

    def on_event(self, kind, frame=None):
        at = None if frame is None else frame.captured_at
        for identity, binding in self.bindings.items():
            if not binding["valid"]:
                if binding["status"] == "slip_detected" and not binding.get("regrasp_observed"):
                    self._watch_regrasp(binding)
                continue
            if binding["structure_revision"] != self.world.structure_revision:
                self.end(identity, "invalidated", "scene structure was revised", at=at)
            elif kind in ("seed", "tracking_failed", "invalidated"):
                # Identity continuity is interrupted: the correspondence chain and
                # any full since-bind rigid relation are lost; measurement is
                # unknown until a later measured frame is compared again.
                self._interrupt(binding, identity)
                binding["last_measurement"] = {"status": "unknown", "frame_id": getattr(frame, "frame_id", None),
                                               "captured_at": at, "reason": f"identity interrupted ({kind})"}
                if kind == "seed":
                    self._evaluate(identity, binding, frame)
            elif kind == "frame":
                self._evaluate(identity, binding, frame)

    def _interrupt(self, binding, identity):
        binding.update(identity_interrupted=True, rigid=None, full_chain_since_bind=False)
        self._break_chain(identity)

    def _step(self, identity, binding, fact, T):
        """Extend the correspondence chain to this frame; None if it cannot."""
        chain = self._chains.get(identity)
        good = self.world._last_good[identity]
        if chain is None:
            return None, "no correspondence anchor"
        flow = flow_correspondences(chain["frame"], chain["mask"], good["frame"], edge_margin=EDGE_MARGIN)
        if flow is None:
            return None, "fewer than 8 trackable interior texture features"
        valid = flow["valid"] & flow["inside"](good["mask"])
        if int(valid.sum()) < MIN_CORRESPONDENCES:
            return None, f"{int(valid.sum())} coherent interior correspondences < {MIN_CORRESPONDENCES}"
        registration = rigid_registration(_to_local(chain["T"], flow["points0"][valid]),
                                          _to_local(T, flow["points1"][valid]))
        if registration is None:
            return None, "held-relative correspondences do not register rigidly"
        S, u = registration["transform"], registration["uncertainty"]
        R = S[:3, :3]
        lever = _skew(chain["D"][:3, :3] @ chain["reference"] + chain["D"][:3, 3] - u["centroid"])
        chain["rot_cov"] = R @ chain["rot_cov"] @ R.T + u["rotation_cov"]
        chain["pos_cov"] = (R @ chain["pos_cov"] @ R.T + u["translation_cov"]
                            + lever @ u["rotation_cov"] @ lever.T)
        chain["D"] = S @ chain["D"]
        chain["steps"] += 1
        chain["min_inliers"] = (registration["inliers"] if chain["min_inliers"] is None
                                else min(chain["min_inliers"], registration["inliers"]))
        chain.update(frame=good["frame"], mask=good["mask"].copy(), T=T.copy())
        return {"inliers": registration["inliers"], "rmse": registration["rmse"],
                "correspondences": int(valid.sum())}, None

    def _rigid_judgement(self, binding, chain):
        D = chain["D"]
        omega = _rotvec(D[:3, :3])
        values, vectors = np.linalg.eigh(chain["rot_cov"])
        axes, violated, observable = [], False, 0
        for value, axis in zip(values, vectors.T):
            std = float(np.sqrt(max(value, 0.)))
            component = float(abs(omega @ axis))
            row = {"axis_tcp": axis.tolist(), "std": std, "drift": component,
                   "observable": std <= OBSERVABLE_ANGLE_STD}
            if row["observable"]:
                observable += 1
                row["violated"] = component > binding["angle_tolerance"] + SIGNIFICANCE * std
                violated |= row["violated"]
            axes.append(row)
        reference = chain["reference"]
        drift = D[:3, :3] @ reference + D[:3, 3] - reference
        position_std = float(np.sqrt(max(np.linalg.eigvalsh(chain["pos_cov"]).max(), 0.)))
        position_observable = position_std <= OBSERVABLE_POSITION_STD
        position_violated = bool(position_observable and np.linalg.norm(drift)
                                 > binding["position_tolerance"] + SIGNIFICANCE * position_std)
        return {"rotation_drift_tcp": omega.tolist(), "rotation_axes": axes,
                "observable_rotation_axes": observable,
                "position_drift": float(np.linalg.norm(drift)), "position_drift_tcp": drift.tolist(),
                "position_std": position_std, "position_observable": position_observable,
                "violated": bool(violated or position_violated),
                "relative_transform": D.tolist()}

    def _evaluate(self, identity, binding, frame):
        fact = self.world._objects.get(identity)
        row = {"status": "unknown", "frame_id": frame.frame_id, "captured_at": frame.captured_at}
        if fact is None or fact["status"] != "known":
            reason = (fact or {}).get("reason", "not observed")
            if "identity jump" in reason:
                self._interrupt(binding, identity)
            binding["last_measurement"] = {**row, "reason": reason}
            return
        sync, reason = self._synchronized(fact)
        points = self._points(identity, fact) if sync is not None else None
        if sync is None or points is None:
            binding["last_measurement"] = {**row, "reason": reason or "insufficient visible depth points"}
            return
        robot, T, opening = sync
        bound_opening = binding["gripper_opening"]
        if opening is not None and bound_opening is not None:
            if opening - bound_opening > OPENING_RELEASE:
                self.end(identity, "released", "measured gripper opening increased", at=frame.captured_at)
                return
            if bound_opening - opening > OPENING_LOSS:
                self.end(identity, "slip_detected", "measured gripper closed beyond the bound opening "
                         f"({bound_opening:.3f} -> {opening:.3f}); the object left the fingers",
                         at=frame.captured_at, cause="gripper_closed_further")
                return
        features = _features(points, T, self.up)
        bound = binding["features_tcp"]
        drift = float(np.linalg.norm(np.subtract(features["visible_center"], bound["visible_center"])))
        T0 = np.asarray(binding["tcp_pose"])
        hand_motion = (float(np.linalg.norm(T[:3, 3] - T0[:3, 3])), _angle(T0[:3, :3].T @ T[:3, :3]))
        row.update(status="measured", evidence_ids=sorted(set(fact["evidence_ids"]) | set(robot["evidence_ids"])),
                   center_drift=drift, hand_motion_since_bind=list(hand_motion), features_tcp=features)
        step, chain_reason = self._step(identity, binding, fact, T)
        chain = self._chains.get(identity)
        judged = None
        if step is not None:
            judged = self._rigid_judgement(binding, chain)
            if judged["observable_rotation_axes"] == 0 and not judged["position_observable"]:
                step, chain_reason = None, "registered correspondences constrain no rotation axis or translation"
        relation = {"center_shift": drift, "center_shift_meaning": "visible-centre median shift in the TCP frame; "
                    "diagnostic only, not relative motion of a matched physical feature"}
        if step is not None:
            row["rigid_drift"] = {"point": judged["position_drift"], "angle": float(np.linalg.norm(
                judged["rotation_drift_tcp"])), "epoch": chain["epoch"], "relative_transform": judged["relative_transform"]}
            full = judged["observable_rotation_axes"] == 3 and judged["position_observable"]
            scope = ("se3" if full else "partial_se3") + ("" if chain["since_bind"] else "_since_reanchor")
            relation.update(judged, source="rgbd_correspondences", status="violated" if judged["violated"] else "consistent",
                            verified_scope=None if judged["violated"] else scope,
                            chain={"epoch": chain["epoch"], "since_bind": chain["since_bind"], "steps": chain["steps"],
                                   "anchor_frame_id": chain["anchor_frame_id"], "min_inliers": chain["min_inliers"],
                                   **step})
            if not chain["since_bind"]:
                relation["caveat"] = ("correspondence chain re-anchored after an interruption; relative motion "
                                      "during the gap since binding is unmeasured (use measured_relation features)")
        else:
            row["rigid_drift"] = {"status": "unknown", "reason": chain_reason}
            # The chain cannot bridge this frame: re-anchor here (a new epoch).
            rigid = self._anchor(identity, fact, T, since_bind=False, features=features)
            if binding.get("rigid") is not None and binding["rigid"].get("since_bind"):
                binding["full_chain_since_bind"] = False
            binding["rigid"] = rigid
            relation.update(chain={"status": "unknown", "reason": chain_reason})
            similar = _same_surface(features, bound)
            lo, hi = (np.asarray(b) for b in bound["visible_bounds"])
            tol = binding["position_tolerance"]
            centre = np.asarray(features["visible_center"])
            inside = bool(np.all(centre >= lo - tol) and np.all(centre <= hi + tol))
            relation.update(source="visible_surface_only", status="partial" if similar or inside else "undetermined",
                            verified_scope=None, visible_signature_similar=similar,
                            visible_center_within_bound_extent=inside,
                            rotation="unknown", translation="unknown",
                            reason="no reliable held-relative correspondences; visible-surface similarity and centre "
                                   "shift do not measure relative motion (another face of a symmetric object has the "
                                   "same spans); geometry is " + ("partially consistent" if similar or inside
                                                                  else "undetermined"))
        row["relation"] = relation
        binding["last_measurement"] = row
        if relation["status"] == "violated":
            self.end(identity, "slip_detected", "measured object features moved relative to the TCP "
                     f"({relation['source']}; centre shift {drift:.3f} m)", at=frame.captured_at,
                     cause=relation["source"])
            return
        binding["identity_interrupted"] = False
        if relation["status"] != "consistent":
            binding["status"] = "relation_partial" if relation["status"] == "partial" else "relation_unknown"
            binding["reason"] = relation["reason"]
            return
        if hand_motion[0] >= EXCITATION[0] or hand_motion[1] >= EXCITATION[1]:
            binding["co_motion_verified"] = True
            binding["verified_scope"] = relation["verified_scope"]
        binding["status"] = "co_moving_verified" if binding["co_motion_verified"] else "consistent_stationary"
        binding["reason"] = (f"measured features follow the TCP after hand motion ({relation['source']}, "
                             f"scope {relation['verified_scope']})" if binding["co_motion_verified"]
                             else "measured relation unchanged; hand has not moved enough to test co-motion")

    # --- policy query -------------------------------------------------------

    def state(self, identity, *, at):
        binding = self.bindings.get(identity)
        if binding is None:
            return {"identity": identity, "status": "unbound", "valid": False, "layer": "none",
                    "reason": "no held binding; call world_bind_held after a synchronized observation"}
        if binding["valid"] and binding["structure_revision"] != self.world.structure_revision:
            self.end(identity, "invalidated", "scene structure was revised", at=at)
        if not binding["valid"] and binding["status"] == "slip_detected" and not binding.get("regrasp_observed"):
            self._watch_regrasp(binding)
        result = copy.deepcopy(binding)
        last = result.get("last_measurement")
        fact = self.world.query(identity, at=at)
        fresh = (last is not None and last.get("status") == "measured" and fact["status"] == "known"
                 and fact["frame_id"] == last["frame_id"])
        result["measurement_status"] = "fresh" if fresh else "unknown"
        result["measurement_age"] = None if last is None or last.get("captured_at") is None else at - last["captured_at"]
        relation = (last or {}).get("relation") or {}
        result["relation_status"] = relation.get("status", "unknown") if fresh else "unknown"
        # The binding keeps the EVER-verified flag as history. Current verification
        # needs the fresh, uninterrupted, CONSISTENT measurement of the current
        # object frame; after occlusion, staleness, unsynchronized pose, an
        # identity interruption or an undetermined relation it is not current.
        historical = bool(binding["co_motion_verified"])
        result["co_motion_verified_historical"] = historical
        result["co_motion_verified"] = bool(binding["valid"] and fresh and historical
                                            and not binding["identity_interrupted"]
                                            and result["relation_status"] == "consistent")
        result["verified_scope"] = binding.get("verified_scope") if result["co_motion_verified"] else None
        # Field meanings (docs/visible-held-placement.md): co_motion_verified is
        # CURRENT correspondence-measured consistency after excitation (scope
        # in verified_scope; partial_se3 leaves some rotation unobservable);
        # partial_geometry_consistent is only visible-surface compatibility and
        # never measures relative motion.
        result["partial_geometry_consistent"] = bool(binding["valid"] and fresh
                                                     and result["relation_status"] in ("consistent", "partial"))
        result["measured_relation"] = None
        if fresh and binding["valid"]:
            result["measured_relation"] = {
                "layer": "observed", "reference": binding["reference"], "frame_id": last["frame_id"],
                "captured_at": last["captured_at"], "evidence_ids": last["evidence_ids"],
                "features_tcp": last["features_tcp"], "relation": relation,
                "source": relation.get("source"),
                "meaning": "measured visible features in the current TCP frame and their judged relation to the "
                           "binding; visible surface only"}
        if binding["valid"] and not fresh and binding["status"] in ("co_moving_verified", "consistent_stationary",
                                                                     "relation_partial", "relation_unknown"):
            result["status"] = "measurement_unknown"
            result["reason"] = ("no fresh uninterrupted measurement of the held relation ("
                                + str((last or {}).get("reason", "not observed")) + "); "
                                + ("co-motion was verified earlier (historical only)" if historical
                                   else "co-motion was never verified"))
        if binding["valid"]:
            robot = self.world.query("robot", at=at)
            pose = _tcp_frame(robot)
            if pose is not None:
                T, opening = pose
                if opening is not None and binding["gripper_opening"] is not None:
                    if opening - binding["gripper_opening"] > OPENING_RELEASE:
                        self.end(identity, "released", "measured gripper opening increased", at=at)
                        return self.state(identity, at=at)
                    if binding["gripper_opening"] - opening > OPENING_LOSS:
                        self.end(identity, "slip_detected", "measured gripper closed beyond the bound opening; "
                                 "the object left the fingers", at=at, cause="gripper_closed_further")
                        return self.state(identity, at=at)
                features = (last or {}).get("features_tcp") if fresh else binding["features_tcp"]
                center = T[:3, :3] @ np.asarray(features["visible_center"]) + T[:3, 3]
                result["prediction"] = {"layer": "predicted", "visible_center": center.tolist(),
                    "tcp_pose": T.tolist(), "tcp_quaternion_wxyz": quaternion_from_rotation(T[:3, :3]).tolist(),
                    "robot_captured_at": robot["captured_at"],
                    "basis": "bound TCP-frame features composed with the current public hand pose; not a measurement"}
            else:
                result["prediction"] = {"layer": "predicted", "status": "unknown",
                                        "reason": "current public robot pose is unavailable or stale"}
        return result
