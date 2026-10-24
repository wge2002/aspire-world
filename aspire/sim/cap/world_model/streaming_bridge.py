# SPDX-License-Identifier: Apache-2.0
"""Trusted lifecycle/control integration; the World receives detached data only.

Synchronous, cooperative checkpoints deliberately keep stepping and sensor
acquisition on the simulator's owning thread. Desired simulation-clock cadence
does not promise wall-clock real time. The authored policy owns interruption.
"""
from __future__ import annotations

import copy

from .World import PublicFrame, TrackingError


class StreamingJournal:
    """Public audit trail of World use: acquisition times, statuses and decisions.

    Only World facts derived from public observations and their timestamps are
    written; geometry values, simulator state, evaluator labels and credentials
    are never passed in. Per-step robot updates and continuing checkpoints are
    counted rather than listed; frames, losses, reacquisitions, relation checks
    and interruptions are listed individually.
    """

    def __init__(self):
        self.events = []
        self.counts = {"robot_updates": 0, "object_frames": 0, "checkpoints": 0,
                       "checkpoint_continue": 0, "checkpoint_interrupt": 0,
                       "checkpoint_error": 0, "tracking_lost": 0, "relation_checks": 0,
                       "reacquire_ok": 0, "reacquire_failed": 0}
        self.robot_first_at = self.robot_last_at = None

    def record(self, event, **fields):
        counted = {"structure_revision": "structure_revisions", "motion_report": "motion_reports",
                   "interaction_check": "interaction_checks", "part_evidence_interrupt": "part_evidence_interrupts"}
        if event in counted:
            name = counted[event]
            self.counts[name] = self.counts.get(name, 0) + 1
        self.events.append({"event": event, **fields})

    def robot(self, captured_at):
        self.counts["robot_updates"] += 1
        if self.robot_first_at is None:
            self.robot_first_at = captured_at
        self.robot_last_at = captured_at

    @staticmethod
    def statuses(snapshot):
        return {k: {"status": v.get("status"), "frame_id": v.get("frame_id"),
                    "captured_at": v.get("captured_at"), "reason": v.get("reason")}
                for k, v in snapshot.items()}

    def summary(self):
        return {"event": "summary", "counts": dict(self.counts),
                "robot_first_at": self.robot_first_at, "robot_last_at": self.robot_last_at}


RECOVERY_STATES = ("unseeded", "armed", "tracking_lost", "reacquire_failed",
                   "structure_grounding_failed", "closed")


class StreamingWorldBridge:
    def __init__(self, env, world, *, on_checkpoint=None, verify_relations=None, journal=None):
        if world.clock != "simulation":
            raise ValueError("LIBERO bridge requires its simulation acquisition clock")
        for name in ("get_observation", "get_current_time_s", "add_control_progress_callback",
                     "add_streaming_reset_callback", "add_streaming_close_callback",
                     "add_motion_gate_callback"):
            if not callable(getattr(env, name, None)):
                raise TypeError(f"environment lacks public streaming integration: {name}")
        self._env, self.world = env, world
        self.on_checkpoint, self.verify_relations = on_checkpoint, verify_relations
        self.journal = journal
        self._removers = []
        self._camera_id = 0
        self._armed = False
        self._closed = False
        self._recovery = {"state": "unseeded", "reason": "explicit same-frame seeding has not occurred",
                          "frame_id": None, "captured_at": None, "evidence": {}}
        self.motion_refusals = 0

    def __enter__(self):
        if self._closed or self._removers:
            raise RuntimeError("bridge cannot be entered twice")
        try:
            self._removers.append(self._env.add_motion_gate_callback(self._gate))
            self._removers.append(self._env.add_control_progress_callback(self._progress))
            self._removers.append(self._env.add_streaming_reset_callback(self._reset))
            self._removers.append(self._env.add_streaming_close_callback(self.close))
        except Exception:
            self.close()
            raise
        return self

    # --- recovery state -------------------------------------------------------

    def disarm(self, state, reason, *, frame=None, evidence=None):
        """Withdraw motion permission until an explicit valid reseed succeeds."""
        if state not in RECOVERY_STATES or state in ("armed", "closed"):
            raise ValueError(f"invalid disarmed recovery state {state!r}")
        self._armed = False
        self._recovery = {"state": state, "reason": str(reason),
                          "frame_id": None if frame is None else frame.frame_id,
                          "captured_at": None if frame is None else frame.captured_at,
                          "evidence": copy.deepcopy(dict(evidence or {}))}

    def _armed_after(self, frame, how):
        previous = self._recovery
        self._armed = True
        self._recovery = {"state": "armed", "reason": how, "frame_id": frame.frame_id,
                          "captured_at": frame.captured_at, "evidence": {},
                          "recovered_from": None if previous["state"] in ("armed", "unseeded")
                          else copy.deepcopy(previous)}

    def recovery_status(self):
        """Detached public recovery report; motion_permitted mirrors the pre-step gate."""
        state = copy.deepcopy(self._recovery)
        if self._closed:
            state.update(state="closed", reason="streaming World is closed")
        state["motion_permitted"] = bool(self._armed and not self._closed)
        state["motion_refusals"] = self.motion_refusals
        state["required_action"] = (None if state["motion_permitted"] else
            "none (closed)" if self._closed else "reacquire_world() with valid current public seeds")
        return state

    def _gate(self):
        if self._armed:
            return
        self.motion_refusals += 1
        status = self.recovery_status()
        if self.journal is not None:
            self.journal.counts["motion_refused"] = self.journal.counts.get("motion_refused", 0) + 1
            self.journal.record("motion_refused", state=status["state"], reason=status["reason"],
                                captured_at=self._env.get_current_time_s())
        raise TrackingError(f"motion refused before the next step: streaming World is "
                            f"{status['state']} ({status['reason']}); explicit valid reseeding "
                            "(reacquire_world) is required")

    def capture(self):
        if self._closed:
            raise RuntimeError("bridge is closed")
        # Capture metadata AFTER reading public arrays; no stepping occurs here.
        obs = self._env.get_observation()
        frame = PublicFrame.from_observation(obs, episode_id=self.world.episode_id,
            camera=self.world.camera, frame_id=self._camera_id,
            captured_at=self._env.get_current_time_s(), clock=self.world.clock)
        self._camera_id += 1
        return frame

    def seed(self, frame, masks, **kwargs):
        if self._closed or not self._removers:
            raise RuntimeError("enter the bridge before seeding")
        try:
            self.world.seed(frame, masks, **kwargs)
        except Exception as exc:
            self.disarm("reacquire_failed", f"tracker seeding failed: {type(exc).__name__}: {exc}", frame=frame)
            raise
        self._armed_after(frame, "explicit same-frame public seeding")
        self._log_objects("seed", frame)

    def refresh(self):
        if not self._armed:
            raise TrackingError("explicit same-frame object seeding is required after reset")
        frame = self.capture()
        try:
            self.world.observe_frame(frame)
        except TrackingError as exc:
            self.disarm("tracking_lost", str(exc), frame=frame)
            if self.journal is not None:
                self.journal.counts["tracking_lost"] += 1
                self._log_objects("tracking_lost", frame, error=str(exc))
            raise
        if self.journal is not None:
            self.journal.counts["object_frames"] += 1
            self._log_objects("object_frame", frame)
            if hasattr(self.world, "joint_models"):
                self.journal.record("joint_motion_estimate", frame_id=frame.frame_id,
                    captured_at=frame.captured_at, models=self.world.snapshot(at=frame.captured_at)["articulation"]["models"],
                    part_motion=self.world.part_motion)
        return frame

    def _log_objects(self, event, frame, **extra):
        if self.journal is None:
            return
        snapshot = self.world.snapshot(at=frame.captured_at)
        self.journal.record(event, frame_id=frame.frame_id, captured_at=frame.captured_at,
                            episode_id=frame.episode_id, camera=frame.camera,
                            objects=self.journal.statuses(snapshot["observed"]), **extra)

    def _progress(self, robot, captured_at, frame_id):
        if not self._armed:
            raise TrackingError("seed the streaming world before starting motion")
        self.world.note_motion(captured_at)
        if self.world.due("robot", captured_at):
            self.world.update_robot(robot, frame_id=frame_id, captured_at=captured_at)
            if self.journal is not None:
                self.journal.robot(captured_at)
        if self.world.due("object", captured_at):
            self.refresh()
        if self.world.due("relation", captured_at) or self.world._force_verification:
            if self.verify_relations is not None:
                # Relation cadence always acquires current object evidence, even
                # when its normal cadence would not have requested a frame.
                if self.world._object_at < captured_at:
                    self.refresh()
                # A slower robot period must not supply stale relation evidence.
                if self.world._robot_at < captured_at:
                    self.world.update_robot(robot, frame_id=frame_id, captured_at=captured_at)
                self.verify_relations(self.world, captured_at)
                if self.journal is not None:
                    self.journal.counts["relation_checks"] += 1
                    relations = self.world.snapshot(at=captured_at)["relations"]
                    self.journal.record("relation_check", captured_at=captured_at,
                                        robot_frame_id=frame_id,
                                        relations={k: {"status": v.get("status"),
                                                       "value": v.get("value"),
                                                       "reason": v.get("reason")}
                                                   for k, v in relations.items()})
            self.world.finish_verification(at=captured_at)
        if self.on_checkpoint is not None:
            try:
                decision = self.on_checkpoint(self.world.snapshot(at=captured_at))
            except Exception as exc:
                self._log_checkpoint("checkpoint_error", captured_at, frame_id,
                                     error=f"{type(exc).__name__}: {exc}")
                raise
            self._log_checkpoint("checkpoint_interrupt" if decision is False
                                 else "checkpoint_continue", captured_at, frame_id)
            return decision
        return None

    def _log_checkpoint(self, kind, captured_at, frame_id, **extra):
        if self.journal is None:
            return
        self.journal.counts["checkpoints"] += 1
        self.journal.counts[kind] += 1
        if kind != "checkpoint_continue":
            self.journal.record(kind, captured_at=captured_at, robot_frame_id=frame_id, **extra)

    def _reset(self):
        if self.journal is not None:
            self.journal.record("episode_reset", previous_episode_id=self.world.episode_id)
        self._armed = False
        self._recovery = {"state": "unseeded", "reason": "episode reset; explicit same-frame seeding required",
                          "frame_id": None, "captured_at": None, "evidence": {}}
        self._camera_id = 0
        self.world.reset()

    def close(self):
        if self._closed:
            return
        self._closed = True
        for remove in reversed(self._removers):
            remove()
        self._removers.clear()
        self._armed = False
        self.world.close()

    def __exit__(self, *_):
        self.close()
