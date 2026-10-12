# SPDX-License-Identifier: Apache-2.0
"""Public-observation world state for optional cooperative streaming control.

This module owns no simulator, camera, actuator, evaluator, or model credentials.
Coordinates describe observed visible surfaces, not hidden geometry or contact.
Legacy executable-world contracts are deliberately independent of this class.
"""
from __future__ import annotations

import copy
import json
import math
import threading
import time
from dataclasses import dataclass
from typing import Mapping

import numpy as np


class FrameError(ValueError):
    """A frame cannot be associated with the active public observation stream."""


class TrackingError(RuntimeError):
    """Temporal tracking failed; measured object facts have been withdrawn."""


def _finite(value):
    def convert(v):
        if isinstance(v, np.ndarray):
            return v.tolist()
        if isinstance(v, np.generic):
            return v.item()
        raise TypeError(f"not a JSON fact: {type(v).__name__}")
    return json.loads(json.dumps(value, default=convert, allow_nan=False))


@dataclass(frozen=True)
class Cadence:
    """Requested acquisition-clock periods/ages, in seconds; not FPS promises."""

    robot_period: float = 0.0  # every control checkpoint
    object_period: float = 0.2
    relation_period: float = 0.5
    robot_max_age: float = 0.2
    object_max_age: float = 0.6
    relation_max_age: float = 0.6
    relation_max_skew: float = 0.05
    min_depth_points: int = 10
    max_mask_iou: float = 0.85  # explicit ambiguity heuristic, not calibrated probability

    def __post_init__(self):
        for name in ("robot_period", "object_period", "relation_period",
                     "robot_max_age", "object_max_age", "relation_max_age", "relation_max_skew"):
            value = getattr(self, name)
            if isinstance(value, bool) or not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and nonnegative")
        if type(self.min_depth_points) is not int or self.min_depth_points < 1:
            raise ValueError("min_depth_points must be a positive integer")
        if not 0 <= self.max_mask_iou <= 1:
            raise ValueError("max_mask_iou must lie between zero and one")


@dataclass(frozen=True)
class PublicFrame:
    episode_id: str
    camera: str
    frame_id: int
    captured_at: float
    rgb: np.ndarray
    depth: np.ndarray
    intrinsics: np.ndarray
    camera_to_world: np.ndarray
    clock: str = "simulation"

    def __post_init__(self):
        if not self.episode_id or not self.camera or not self.clock:
            raise FrameError("episode, camera and acquisition clock are required")
        if type(self.frame_id) is not int or self.frame_id < 0:
            raise FrameError("frame_id must be a nonnegative integer")
        if not math.isfinite(self.captured_at) or self.captured_at < 0:
            raise FrameError("invalid capture time")
        rgb = np.asarray(self.rgb)
        depth = np.asarray(self.depth, dtype=float)
        if depth.ndim == 3 and depth.shape[-1] == 1:
            depth = depth[..., 0]
        K, T = np.asarray(self.intrinsics, dtype=float), np.asarray(self.camera_to_world, dtype=float)
        if rgb.ndim != 3 or rgb.shape[-1] != 3 or rgb.dtype != np.uint8:
            raise FrameError("RGB must have shape H,W,3 and dtype uint8")
        if depth.shape != rgb.shape[:2] or K.shape != (3, 3) or T.shape != (4, 4):
            raise FrameError("RGB/depth/calibration shapes disagree")
        if (not np.isfinite(K).all() or not np.isfinite(T).all()
                or K[0, 0] <= 0 or K[1, 1] <= 0 or abs(np.linalg.det(K)) < 1e-12
                or not np.allclose(T[3], [0, 0, 0, 1])
                or not np.allclose(T[:3, :3].T @ T[:3, :3], np.eye(3), atol=1e-5)
                or not np.isclose(np.linalg.det(T[:3, :3]), 1, atol=1e-5)):
            raise FrameError("invalid camera calibration")
        for name, value in (("rgb", rgb), ("depth", depth), ("intrinsics", K), ("camera_to_world", T)):
            detached = value.copy()
            detached.flags.writeable = False
            object.__setattr__(self, name, detached)

    @classmethod
    def from_observation(cls, obs, *, episode_id, camera, frame_id, captured_at, clock="simulation"):
        view = obs[camera]
        return cls(episode_id, camera, frame_id, captured_at, view["images"]["rgb"],
                   view["images"]["depth"], view["intrinsics"], view["pose_mat"], clock)


class World:
    """One episode/camera's measured facts, predictions, and authored relations.

    tracker implements initialize(rgb, masks), track(rgb), reset(), close().
    All masks are keyed by stable public identity strings. Calls are serialized;
    the control bridge does not create a background simulator/rendering thread.
    """

    def __init__(self, tracker, *, episode_id: str, camera="agentview", clock="simulation", cadence=None):
        if not episode_id or not camera or not clock:
            raise ValueError("episode, camera and clock are required")
        self.tracker = tracker
        self.episode_id, self.camera, self.clock = episode_id, camera, clock
        self._base_episode_id = episode_id
        self._episode_generation = 0
        self.cadence = cadence or Cadence()
        self._lock = threading.RLock()
        self._closed = False
        self._clear()

    def _clear(self):
        self._objects, self._predictions, self._relations = {}, {}, {}
        self._robot = None
        self._identities = ()
        self._frame_id, self._robot_id = -1, -1
        self._object_at = self._robot_at = self._relation_at = -math.inf
        self._motion_at = -math.inf
        self._force_verification = False
        self._seeded = False
        self.revision = 0

    def _open(self):
        if self._closed:
            raise RuntimeError("world is closed")

    @staticmethod
    def _time(at):
        if isinstance(at, bool) or not math.isfinite(at) or at < 0:
            raise ValueError("acquisition time must be finite and nonnegative")

    def due(self, kind, at):
        self._time(at)
        with self._lock:
            self._open()
            period = getattr(self.cadence, f"{kind}_period")
            previous = getattr(self, f"_{kind}_at")
            return ((kind == "object" and self._force_verification)
                    or at - previous + 1e-12 >= period)

    def request_verification(self):
        """Force acquisition and authored relation checking at the next checkpoint."""
        with self._lock:
            self._open()
            self._force_verification = True

    def note_motion(self, at):
        self._time(at)
        with self._lock:
            self._open()
            self._motion_at = max(self._motion_at, at)

    def _fact(self, value, *, frame_id, at, layer="observed", reason="", identity=None):
        self.revision += 1
        return {"status": "known" if value is not None else "unknown", "value": _finite(value),
                "identity": identity, "layer": layer, "frame_id": frame_id,
                "captured_at": at, "processed_at_monotonic": time.monotonic(),
                "evidence_ids": [f"{self.episode_id}:{'robot' if identity == 'robot' else self.camera}:{frame_id}"]
                    if layer == "observed" else [],
                "episode_id": self.episode_id, "camera": self.camera, "clock": self.clock,
                "revision": self.revision, "reason": reason}

    def update_robot(self, values, *, frame_id, captured_at):
        self._time(captured_at)
        with self._lock:
            self._open()
            if type(frame_id) is not int or frame_id <= self._robot_id or captured_at < self._robot_at:
                raise FrameError("duplicate or out-of-order robot observation")
            fact = self._fact(values, frame_id=frame_id, at=captured_at, identity="robot")
            self._robot = fact
            self._robot_id, self._robot_at = frame_id, captured_at

    def _validate_frame(self, frame):
        self._open()
        if (frame.episode_id, frame.camera, frame.clock) != (self.episode_id, self.camera, self.clock):
            raise FrameError("episode, camera, or clock mismatch")
        if frame.frame_id <= self._frame_id or frame.captured_at < self._object_at:
            raise FrameError("duplicate or out-of-order RGB-D frame")

    def seed(self, frame: PublicFrame, masks: Mapping[str, np.ndarray]):
        """Explicit acquisition/reacquisition; no implicit identity switching."""
        with self._lock:
            self._validate_frame(frame)
            if not masks or any(not isinstance(k, str) or not k or k == "robot" for k in masks):
                raise ValueError("nonempty public object identities are required")
            if self._identities and set(masks) != set(self._identities):
                raise FrameError("reacquisition must preserve the episode's object identities")
            seeds = {}
            for identity, mask in masks.items():
                mask = np.asarray(mask)
                if mask.shape != frame.depth.shape or not np.isin(mask, [0, 1]).all() or not mask.any():
                    raise FrameError("seed must be a nonempty same-frame binary mask")
                seeds[identity] = mask.astype(bool).copy()
            self._identities = tuple(masks)
            try:
                self.tracker.initialize(frame.rgb, seeds)
                self._accept(frame, seeds)
                self._relations.clear()
                self._predictions.clear()
                self._seeded = True
            except Exception as exc:
                self._tracking_failed(frame, exc)

    def observe_frame(self, frame: PublicFrame):
        with self._lock:
            self._validate_frame(frame)
            if not self._seeded:
                raise TrackingError("seed or explicitly reacquire objects before tracking")
            try:
                masks = self.tracker.track(frame.rgb)
                self._accept(frame, masks)
            except Exception as exc:
                self._tracking_failed(frame, exc)

    def _tracking_failed(self, frame, exc):
        self._seeded = False
        self._frame_id, self._object_at = frame.frame_id, frame.captured_at
        for identity in self._identities:
            self._objects[identity] = self._fact(None, frame_id=frame.frame_id,
                at=frame.captured_at, identity=identity, reason=f"tracking failed: {type(exc).__name__}")
        raise TrackingError("SAM2 streaming failed; explicit reacquisition required") from exc

    def _accept(self, frame, masks):
        if not isinstance(masks, Mapping) or set(masks) - set(self._identities):
            raise FrameError("tracker returned an unknown object identity")
        objects = {}
        ambiguous = set()
        present = [(identity, np.asarray(m)) for identity, m in masks.items() if m is not None]
        for index, (identity, first) in enumerate(present):
            if first.shape != frame.depth.shape or not np.isin(first, [0, 1]).all():
                raise FrameError("tracked mask does not match its RGB-D frame")
            for other, second in present[index + 1:]:
                if second.shape != first.shape or not np.isin(second, [0, 1]).all():
                    raise FrameError("tracked mask does not match its RGB-D frame")
                union = np.logical_or(first, second).sum()
                if union and np.logical_and(first, second).sum() / union > self.cadence.max_mask_iou:
                    ambiguous.update((identity, other))
        for identity in self._identities:
            value, reason = None, "object missing or occluded"
            mask = masks.get(identity)
            if identity in ambiguous:
                reason = "ambiguous overlapping object tracks; geometry unknown"
            elif mask is not None:
                mask = np.asarray(mask)
                if mask.shape != frame.depth.shape or not np.isin(mask, [0, 1]).all():
                    raise FrameError("tracked mask does not match its RGB-D frame")
                ys, xs = np.where(mask.astype(bool) & np.isfinite(frame.depth) & (frame.depth > 0))
                if len(xs) >= self.cadence.min_depth_points:
                    pixels = np.stack([xs, ys, np.ones(len(xs))], axis=0)
                    points = (np.linalg.inv(frame.intrinsics) @ pixels).T * frame.depth[ys, xs, None]
                    points = points @ frame.camera_to_world[:3, :3].T + frame.camera_to_world[:3, 3]
                    value = {"visible_center": np.median(points, axis=0).tolist(),
                             "visible_bounds": [points.min(axis=0).tolist(), points.max(axis=0).tolist()],
                             "depth_points": len(xs), "mask_pixels": int(mask.sum())}
                    reason = "SAM2 tracked visible surface projected from this RGB-D frame"
                elif mask.any():
                    reason = "insufficient valid depth; geometry unknown"
            objects[identity] = self._fact(value, frame_id=frame.frame_id, at=frame.captured_at,
                                           identity=identity, reason=reason)
        self._objects = objects
        self._frame_id, self._object_at = frame.frame_id, frame.captured_at

    def invalidate_objects(self, frame, *, reason):
        """Withdraw geometry after a failed public reacquisition or measurement."""
        with self._lock:
            self._validate_frame(frame)
            self._seeded = False
            self._frame_id, self._object_at = frame.frame_id, frame.captured_at
            for identity in self._identities:
                self._objects[identity] = self._fact(None, frame_id=frame.frame_id,
                    at=frame.captured_at, identity=identity, reason=str(reason))

    def set_prediction(self, identity, value, *, at):
        self._time(at)
        with self._lock:
            self._open()
            if identity not in self._identities:
                raise FrameError("prediction for unregistered identity")
            self._predictions[identity] = self._fact(value, frame_id=self._frame_id, at=at,
                identity=identity, layer="predicted", reason="authored prediction, not a measurement")

    def query(self, name, *, at, layer="observed"):
        self._time(at)
        with self._lock:
            self._open()
            if layer not in ("observed", "predicted", "relation"):
                raise ValueError("unknown world layer")
            if layer == "predicted":
                fact, age = self._predictions.get(name), self.cadence.object_max_age
            elif layer == "relation":
                fact, age = self._relations.get(name), self.cadence.relation_max_age
            elif name == "robot":
                fact, age = self._robot, self.cadence.robot_max_age
            else:
                fact, age = self._objects.get(name), self.cadence.object_max_age
            if fact is None:
                return {"status": "unknown", "value": None, "layer": layer, "reason": "not observed"}
            result = copy.deepcopy(fact)
            stale = at < fact["captured_at"] or at - fact["captured_at"] > age
            if layer == "relation" and fact["captured_at"] < self._motion_at:
                stale = True
            if layer == "relation":
                for dependency, revision in fact.get("dependency_revisions", {}).items():
                    current = self._robot if dependency == "robot" else self._objects.get(dependency)
                    if current is None or current["revision"] != revision:
                        stale = True
            if stale:
                result.update(status="unknown", value=None, reason="observation expired or belongs to a future time")
            return result

    def verify_relation(self, name, identities, predicate, *, at):
        """Evaluate an authored predicate only on synchronized post-motion facts."""
        self._time(at)
        with self._lock:
            self._open()
            if not identities:
                raise ValueError("relation must name its measured dependencies")
            facts = [self.query(identity, at=at) for identity in identities]
            ready = all(f["status"] == "known" and f["captured_at"] >= self._motion_at for f in facts)
            times = [f.get("captured_at", -math.inf) for f in facts]
            ready = ready and max(times) - min(times) <= self.cadence.relation_max_skew
            value = None
            if ready:
                value = predicate(*[copy.deepcopy(f["value"]) for f in facts])
                if not isinstance(value, (bool, np.bool_)):
                    raise TypeError("relation predicate must return a boolean")
                value = bool(value)
            fact = self._fact(value, frame_id=self._frame_id, at=min(times) if ready else at,
                             reason="authored relation on measured facts" if ready else "missing synchronized post-motion evidence")
            fact["dependencies"] = list(identities)
            fact["dependency_revisions"] = {identity: f.get("revision") for identity, f in zip(identities, facts)}
            fact["evidence_ids"] = sorted({i for f in facts for i in f.get("evidence_ids", [])})
            self._relations[name] = fact
            return self.query(name, at=at, layer="relation")

    def finish_verification(self, *, at):
        self._time(at)
        with self._lock:
            self._open()
            self._relation_at = at
            self._force_verification = False

    def snapshot(self, *, at):
        with self._lock:
            return {"episode_id": self.episode_id, "camera": self.camera, "clock": self.clock,
                    "revision": self.revision, "robot": self.query("robot", at=at),
                    "observed": {k: self.query(k, at=at) for k in self._objects},
                    "predicted": {k: self.query(k, at=at, layer="predicted") for k in self._predictions},
                    "relations": {k: self.query(k, at=at, layer="relation") for k in self._relations}}

    def reset(self, *, episode_id=None):
        with self._lock:
            self._open()
            if episode_id is not None and (not episode_id or episode_id == self.episode_id):
                raise ValueError("reset requires a new nonempty episode identity")
            try:
                self.tracker.reset()
            finally:
                self._episode_generation += 1
                self.episode_id = episode_id or f"{self._base_episode_id}:reset/{self._episode_generation}"
                self._clear()

    def close(self):
        with self._lock:
            if not self._closed:
                try:
                    self.tracker.close()
                finally:
                    self._clear()
                    self._closed = True
