# SPDX-License-Identifier: Apache-2.0
"""Explicit saved-policy streaming profile; never enabled by historical studies."""
from __future__ import annotations

import json
import copy
from pathlib import Path

import numpy as np

from .World import Cadence, World
from .streaming_bridge import StreamingJournal, StreamingWorldBridge
from .sam2_streaming import SAM2Streaming
from .semantic_world import SEMANTIC_MODE, SemanticWorld, choose_seed, validate_parts
from .motion_feedback import motion_report, rotation_wxyz
from .control_progress import MotionInterrupted
from .articulated_world import ARTICULATION_MODE, ArticulatedWorld
from .qwen_world_review import QwenWorldReviewer, ReviewRejected


class StreamingSeedError(ValueError):
    """The authored public object prompts did not yield one same-frame seed each."""


class StreamingReplay:
    def __init__(self, env, config_path, *, episode_id):
        config = json.loads(Path(config_path).read_text())
        if config.get("mode") not in ("sam2-streaming-world-v1", SEMANTIC_MODE, ARTICULATION_MODE):
            raise ValueError("expected an explicit streaming World v1 or semantic v2 mode")
        self.articulation = config["mode"] == ARTICULATION_MODE
        self.semantic = config["mode"] in (SEMANTIC_MODE, ARTICULATION_MODE)
        self.reviewer = QwenWorldReviewer(config["review"]) if self.articulation else None
        self.definitions = copy.deepcopy(config["objects"])
        self.parts = copy.deepcopy(config.get("parts", [
            {"identity": item["identity"], "semantic": item["prompt"], "tracked": True}
            for item in self.definitions]))
        self._focus, self._policy_checkpoint, self._reports = (), None, {}
        self.config = config
        self.env = env
        if getattr(env.cfg, "privileged", True) or getattr(env.low_level_env, "privileged", True):
            raise ValueError("streaming world requires a nonprivileged environment")
        self._globals = env._exec_globals
        names = ("streaming_world", "world_snapshot", "world_query", "set_world_checkpoint",
                 "set_world_relation_verifier", "request_world_verification", "reacquire_world")
        if self.semantic:
            names += ("world_revise_structure", "world_refresh", "world_set_focus",
                      "world_begin_interaction", "world_finish_interaction",
                      "goto_pose_report", "world_require_relation")
        if self.articulation:
            names += ("world_query_joint", "world_predict_joint", "world_review_pending", "world_begin_joint_interaction")
        if any(name in self._globals for name in names):
            raise ValueError("streaming policy helper name already exists")
        self._names = names
        self.world = None
        self.bridge = None
        self.final_snapshot = None
        self.journal = StreamingJournal()
        self._closed = False
        tracker = SAM2Streaming.from_checkpoint(**config["sam2"])
        try:
            world_class = ArticulatedWorld if self.articulation else SemanticWorld if self.semantic else World
            self.world = world_class(tracker, episode_id=episode_id, camera=config.get("camera", "agentview"),
                               cadence=Cadence(**config.get("cadence", {})))
            self.bridge = StreamingWorldBridge(env.low_level_env, self.world,
                                               journal=self.journal).__enter__()
            if self.semantic:
                self.bridge._removers.append(env.low_level_env.add_streaming_reset_callback(self._reset_semantic))
            self.reacquire()
            now = env.low_level_env.get_current_time_s
            self._globals.update(streaming_world=self.world,
                world_snapshot=lambda: self.world.snapshot(at=now()),
                world_query=lambda name, **kw: self.world.query(name, at=now(), **kw),
                set_world_checkpoint=self.set_checkpoint,
                set_world_relation_verifier=self.set_verifier,
                request_world_verification=self.world.request_verification,
                reacquire_world=self.reacquire)
            if self.semantic:
                self.bridge.on_checkpoint = self._semantic_checkpoint
                self._globals.update(world_revise_structure=self.revise_structure,
                    world_refresh=self.bridge.refresh, world_set_focus=self.set_focus,
                    world_begin_interaction=lambda identity, expected_delta, **kw:
                        self.world.begin_interaction(identity, expected_delta, at=now(), **kw),
                    world_finish_interaction=self.finish_interaction,
                    goto_pose_report=self.goto_pose_report,
                    world_require_relation=self.require_relation)
            if self.articulation:
                self._globals.update(world_query_joint=lambda child: self.world.joint_query(child, at=now()),
                    world_predict_joint=lambda child, q, **kw: self.world.predict_joint(child, q, at=now(), **kw),
                    world_begin_joint_interaction=lambda child, delta_q, **kw:
                        self.world.begin_joint_interaction(child,delta_q,at=now(),**kw),
                    world_review_pending=self.process_reviews)
                self.process_reviews()
        except Exception:
            if self.bridge is not None:
                self.bridge.close()
            else:
                tracker.close()
            raise

    def reacquire(self):
        """Same-frame public SAM3 initialization; ambiguous identities are refused."""
        frame = self.bridge.capture()
        definitions = self.definitions
        identities = [item["identity"] for item in definitions]
        if not definitions or len(set(identities)) != len(identities):
            raise ValueError("objects must have unique public identities")
        segment = self._globals["segment_sam3_text_prompt"]
        masks = {}
        for item in definitions:
            threshold = float(item.get("min_seed_score", 0.5))
            if not 0 <= threshold <= 1:
                raise ValueError("min_seed_score must lie between zero and one")
            candidates = [m for m in segment(frame.rgb, item["prompt"])
                          if float(m.get("score", 0)) >= threshold]
            if self.semantic:
                old = self.world._last_good.get(item["identity"])
                try:
                    masks[item["identity"]] = choose_seed(frame, candidates, item,
                        old["center"] if old is not None else None,
                        max_distance=self.world.max_identity_jump)
                except ValueError as exc:
                    self.world.invalidate_objects(frame, reason=str(exc))
                    self.bridge._armed = False
                    self.journal.counts["reacquire_failed"] += 1
                    self.journal.record("reacquire_failed", frame_id=frame.frame_id,
                        captured_at=frame.captured_at, identity=item["identity"], reason=str(exc))
                    raise StreamingSeedError(str(exc)) from exc
            elif len(candidates) != 1:
                self.world.invalidate_objects(frame, reason="ambiguous or missing public reacquisition")
                self.bridge._armed = False
                self.journal.counts["reacquire_failed"] += 1
                self.journal.record("reacquire_failed", frame_id=frame.frame_id,
                                    captured_at=frame.captured_at, identity=item["identity"],
                                    candidates_above_threshold=len(candidates))
                raise StreamingSeedError(f"ambiguous or missing public seed for {item['identity']}; refine its prompt")
            else:
                masks[item["identity"]] = candidates[0]["mask"]
        if self.semantic and not self.world.parts:
            self.world.revise_structure(frame, masks, self.parts, reason="initial public scene decomposition")
            self.bridge._armed = True
            self.journal.record("structure_revision", revision=self.world.structure_revision,
                frame_id=frame.frame_id, captured_at=frame.captured_at, identities=sorted(masks))
            if self.articulation:
                joints = self.config.get("joints", [{"child": k, "parent": p["parent"], "family": "unknown"}
                    for k, p in self.world.parts.items() if p["parent"] is not None])
                ids = sorted(self.world._evidence)
                if ids:
                    self.world.configure_joints(joints, reason="initial public scene joint hypotheses", evidence_ids=ids)
                    self.world._request_review("initial_model_review", None, ids)
        else:
            if self.semantic:
                self.world.tracker.reset()
            self.bridge.seed(frame, masks)
        self.journal.counts["reacquire_ok"] += 1
        self.journal.record("reacquire_ok", frame_id=frame.frame_id, captured_at=frame.captured_at,
                            identities=sorted(masks))

    def set_checkpoint(self, callback):
        if callback is not None and not callable(callback):
            raise TypeError("checkpoint must be callable")
        if self.semantic:
            self._policy_checkpoint = callback
        else:
            self.bridge.on_checkpoint = callback

    def set_focus(self, identities):
        identities = tuple(identities)
        if any(k not in self.world._identities for k in identities):
            raise ValueError("focus requires tracked part identities")
        self._focus = identities
        self.journal.record("focus_changed", identities=list(identities))

    def _semantic_checkpoint(self, snapshot):
        if self.articulation:
            before = (self.world.structure_revision,self.world.model_revision)
            self.process_reviews()
            if before != (self.world.structure_revision,self.world.model_revision):
                self.journal.record("representation_motion_interrupt",before=list(before),
                    after=[self.world.structure_revision,self.world.model_revision])
                return False  # the ongoing trajectory used the earlier representation
            snapshot = self.world.snapshot(at=self.env.low_level_env.get_current_time_s())
        if any(snapshot["observed"].get(k, {}).get("status") != "known" for k in self._focus):
            self.journal.record("part_evidence_interrupt", identities=list(self._focus))
            return False
        if self._policy_checkpoint is not None:
            return self._policy_checkpoint(snapshot)
        return True

    def revise_structure(self, parts, objects, *, reason, evidence_ids, lineage=None, joints=None):
        """Policy-proposed scene decomposition, with fresh public segmentation."""
        validate_parts(parts, {item["identity"] for item in objects})
        if self.articulation:
            from .articulated_world import validate_joints
            nodes = validate_parts(parts, {item["identity"] for item in objects})
            joints = joints if joints is not None else [{"child": k, "parent": p["parent"], "family": "unknown"}
                for k, p in nodes.items() if p["parent"] is not None]
            validate_joints(joints, nodes)
        frame = self.bridge.capture()
        segment = self._globals["segment_sam3_text_prompt"]
        masks = {}
        try:
            for item in objects:
                old = self.world._last_good.get(item["identity"])
                masks[item["identity"]] = choose_seed(frame, segment(frame.rgb, item["prompt"]), item,
                    old["center"] if old is not None else None, max_distance=self.world.max_identity_jump)
        except Exception:
            self.world.invalidate_objects(frame, reason="structural proposal could not be grounded in current public masks")
            self.bridge._armed = False
            raise
        self.bridge._armed = False
        revision = self.world.revise_structure(frame, masks, parts, reason=reason,
                                               evidence_ids=evidence_ids, lineage=lineage)
        self.parts, self.definitions = copy.deepcopy(parts), copy.deepcopy(objects)
        self._focus = tuple(k for k in self._focus if k in masks)
        self.bridge._armed = True
        if self.articulation:
            self.world.configure_joints(joints, reason=reason, evidence_ids=evidence_ids)
        self.journal.record("structure_revision", revision=revision, reason=reason,
            evidence_ids=list(evidence_ids), frame_id=frame.frame_id,
            captured_at=frame.captured_at, identities=sorted(masks), lineage=lineage or {})
        return revision

    def process_reviews(self):
        """Only queued public events call Qwen; control is paused on this thread."""
        if not self.articulation:
            return []
        pending = [r for r in self.world.review_requests if r["status"] == "pending"]
        if not pending:
            return []
        now = self.env.low_level_env.get_current_time_s()
        snapshot = self.world.snapshot(at=now)
        # Do not feed arbitrary rejected proposals or nested previous receipts
        # back into the model. Current graph, fits and evidence suffice.
        snapshot["articulation"].pop("reviews", None)
        # Only registered observed IDs; predictions cannot authorize a revision.
        ids = sorted(self.world._evidence)
        packet = {"episode_id": self.world.episode_id, "events": copy.deepcopy(pending),
            "evidence_ids": ids, "objects": copy.deepcopy(self.definitions), "snapshot": snapshot,
            "recent_motion_reports": list(self._reports.values())[-4:]}
        try:
            reply, receipt = self.reviewer.review(packet,
                rgb=self.world.latest_frame.rgb if self.world.latest_frame is not None else None)
            if reply["action"] == "revise":
                self.revise_structure(reply["parts"], reply["objects"], reason=reply["reason"],
                    evidence_ids=reply["evidence_ids"], lineage=reply.get("lineage"), joints=reply["joints"])
            elif reply["action"] == "select_models":
                self.world.configure_joints(reply["joints"], reason=reply["reason"], evidence_ids=reply["evidence_ids"])
            elif reply["action"] == "reacquire":
                self.reacquire()
            for event in pending:
                event["status"] = "awaiting_evidence" if reply["action"] == "defer" else "reviewed"
            receipt.update(episode_id=self.world.episode_id, captured_at=now,
                structure_revision=self.world.structure_revision, model_revision=self.world.model_revision,
                event_reasons=[r["reason"] for r in pending])
            self.world.review_history.append(copy.deepcopy(receipt))
            self.journal.counts["qwen_reviews"] = self.journal.counts.get("qwen_reviews", 0) + 1
            self.journal.record("qwen_world_review", **receipt)
            return [receipt]
        except Exception as exc:
            if isinstance(exc, ValueError):
                receipt = copy.deepcopy(getattr(exc, "receipt", {}))
                receipt.update(status="rejected", reason=str(exc), error_type=type(exc).__name__,
                    episode_id=self.world.episode_id, captured_at=now,
                    structure_revision=self.world.structure_revision, model_revision=self.world.model_revision)
                self.world.review_history.append(receipt)
                for event in pending:
                    event["status"] = "rejected"
                self.journal.counts["qwen_review_rejections"] = self.journal.counts.get("qwen_review_rejections", 0)+1
                self.journal.record("qwen_world_review_rejected", **receipt)
                if self.bridge._armed:
                    return [receipt]  # unchanged, fresh World remains usable
            self.journal.record("qwen_world_review_rejected", error_type=type(exc).__name__,
                episode_id=self.world.episode_id, event_reasons=[r["reason"] for r in pending])
            raise MotionInterrupted("online World review failed or its proposal was rejected") from exc

    def _reset_semantic(self):
        self.definitions = copy.deepcopy(self.config["objects"])
        self.parts = copy.deepcopy(self.config.get("parts", [
            {"identity": item["identity"], "semantic": item["prompt"], "tracked": True}
            for item in self.definitions]))
        self._focus, self._reports = (), {}

    def goto_pose_report(self, position, quaternion_wxyz, z_approach=0.0):
        """Execute ordinary goto_pose and measure requested TCP arrival separately.

        Returns a public report with reached, actual error and effective IK target.
        It never reports contact, grasping or task success. Control errors raise.
        """
        rotation_wxyz(quaternion_wxyz)
        pos = np.asarray(position, dtype=float)
        if pos.shape != (3,) or not np.isfinite(pos).all():
            raise ValueError("one finite XYZ position is required")
        apis = getattr(self.env, "_apis", {})
        api = next((a for a in apis.values() if hasattr(a, "_TCP_OFFSET")), None)
        if api is not None:
            api._last_ik_report = None
        low = self.env.low_level_env
        low._last_motion_report = None
        self._globals["goto_pose"](position, quaternion_wxyz, z_approach=z_approach)
        now = low.get_current_time_s()
        report = motion_report(position, quaternion_wxyz, low.get_robot_observation(),
            ik=getattr(api, "_last_ik_report", None), joints=getattr(low, "_last_motion_report", None))
        report.update(action_id=f"motion-{len(self._reports)+1}", captured_at=now,
                      episode_id=self.world.episode_id)
        self._reports[report["action_id"]] = copy.deepcopy(report)
        self.journal.record("motion_report", **report)
        return copy.deepcopy(report)

    def finish_interaction(self, key, report, *, contact_relation=None):
        actual = self._reports.get(report.get("action_id"))
        if actual is None or actual != report or actual["episode_id"] != self.world.episode_id:
            raise ValueError("interaction execution must cite an unchanged recorded motion report")
        self.bridge.refresh()
        now = self.env.low_level_env.get_current_time_s()
        robot = self.env.low_level_env.get_robot_observation()
        # Robot IDs belong to the simulator control stream, not the camera stream.
        robot_id = getattr(self.env.low_level_env, "_sim_step_count", -1)
        if robot_id > self.world._robot_id:
            self.world.update_robot(robot, frame_id=robot_id, captured_at=now)
        if self.bridge.verify_relations is not None:
            self.bridge.verify_relations(self.world, now)
        finish = (self.world.finish_joint_interaction if self.articulation and key in self.world._joint_pending
                  else self.world.finish_interaction)
        result = finish(key, {**actual, "contact_relation": contact_relation}, at=now)
        self.journal.record("interaction_check", **result)
        return result

    def require_relation(self, name):
        answer = self.world.query(name, at=self.env.low_level_env.get_current_time_s(), layer="relation")
        if answer.get("status") != "known" or answer.get("value") is not True:
            raise MotionInterrupted(f"required public relation {name!r} is false or unknown")
        return answer

    def set_verifier(self, callback):
        if callback is not None and not callable(callback):
            raise TypeError("relation verifier must be callable")
        self.bridge.verify_relations = callback
        self.world.request_verification()

    def close(self):
        if self._closed:
            return
        self._closed = True
        for name in self._names:
            self._globals.pop(name, None)
        try:
            at = self.env.low_level_env.get_current_time_s()
            self.final_snapshot = self.world.snapshot(at=at)
            self.journal.record("final_snapshot", captured_at=at,
                                objects=self.journal.statuses(self.final_snapshot["observed"]),
                                relations=self.journal.statuses(self.final_snapshot["relations"]))
        finally:
            self.journal.record(**self.journal.summary())
            self.bridge.close()

    def write_journal(self, path):
        """JSONL of public World-use events; created exclusively beside the trial."""
        with Path(path).open("x") as out:
            for event in self.journal.events:
                out.write(json.dumps(event, allow_nan=False) + "\n")

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
