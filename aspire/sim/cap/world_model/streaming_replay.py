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
from .semantic_world import SEMANTIC_MODE, SemanticWorld, _association_summary, select_seed, validate_parts
from .identity_evidence import SeedSelectionError, valid_alternative_prompts
from .motion_feedback import motion_report
from .robot_frames import POLICY_FRAME, base_pose_world, rotation_wxyz
from .execution_contract import (CONTRACT_VERSION, CheckedMotionContract, ExecutionContractError,
                                 validate_contract_config, workspace_diagnostics)
from .geometry_queries import mask_points, visible_geometry_summary
from .held_geometry import HeldGeometry, tcp_target_for_feature
from .held_surface import cylinder_cross_section, support_placement_candidates
from .control_progress import MotionInterrupted
from .articulated_world import ARTICULATION_MODE, ArticulatedWorld
from .qwen_world_review import QwenWorldReviewer, ReviewRejected


class StreamingSeedError(ValueError):
    """The authored public object prompts did not yield one same-frame seed each."""

    def __init__(self, message, evidence=None):
        self.evidence = evidence or {}
        super().__init__(message)


class StreamingReplay:
    def __init__(self, env, config_path, *, episode_id):
        config = json.loads(Path(config_path).read_text())
        if config.get("mode") not in ("sam2-streaming-world-v1", SEMANTIC_MODE, ARTICULATION_MODE):
            raise ValueError("expected an explicit streaming World v1 or semantic v2 mode")
        self.articulation = config["mode"] == ARTICULATION_MODE
        self.semantic = config["mode"] in (SEMANTIC_MODE, ARTICULATION_MODE)
        self.reviewer = QwenWorldReviewer(config["review"]) if self.articulation else None
        # Opt-in checked-execution contract (docs/checked-execution-contract.md);
        # absent keeps every legacy profile's behaviour unchanged.
        self.execution_contract = validate_contract_config(config.get("execution_contract"))
        if self.execution_contract is not None and not self.semantic:
            raise ValueError("execution_contract requires the semantic v2 or articulation v3 mode")
        self.contract = None
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
                 "set_world_relation_verifier", "request_world_verification", "reacquire_world",
                 "world_recovery_status")
        if self.semantic:
            names += ("world_revise_structure", "world_refresh", "world_set_focus",
                      "world_begin_interaction", "world_finish_interaction",
                      "goto_pose_report", "world_require_relation")
        if self.articulation:
            names += ("world_query_joint", "world_predict_joint", "world_review_pending", "world_begin_joint_interaction")
        if self.execution_contract is not None:
            names += ("motion_workspace", "motion_contract_status", "world_bind_held", "world_held_state",
                      "world_release_held", "world_visible_geometry", "geometry_from_points",
                      "tcp_target_for_feature", "support_placement_candidates", "cylinder_cross_section")
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
            # Explicit production frame contract: camera geometry, robot facts
            # and goto_pose targets all use robot_base (robot_frames.py).
            api = self._motion_api()
            self.world = world_class(tracker, episode_id=episode_id, camera=config.get("camera", "agentview"),
                               cadence=Cadence(**config.get("cadence", {})), coordinate_frame=POLICY_FRAME,
                               tcp_offset=None if api is None else api._TCP_OFFSET)
            self.bridge = StreamingWorldBridge(env.low_level_env, self.world,
                                               journal=self.journal).__enter__()
            if self.execution_contract is not None:
                self._install_contract(api)
            if self.semantic:
                self.bridge._removers.append(env.low_level_env.add_streaming_reset_callback(self._reset_semantic))
                # Pre-step: a focused part already known to be unknown/stale in
                # the cached World refuses the next commanded step, even if the
                # policy caught the earlier post-step interrupt.
                self.bridge._removers.append(env.low_level_env.add_motion_gate_callback(self._focus_gate))
            self.reacquire()
            now = env.low_level_env.get_current_time_s
            self._globals.update(streaming_world=self.world,
                world_snapshot=lambda: self.world.snapshot(at=now()),
                world_query=lambda name, **kw: self.world.query(name, at=now(), **kw),
                set_world_checkpoint=self.set_checkpoint,
                set_world_relation_verifier=self.set_verifier,
                request_world_verification=self.world.request_verification,
                reacquire_world=self.reacquire,
                world_recovery_status=self.recovery_status)
            if self.semantic:
                self.bridge.on_checkpoint = self._semantic_checkpoint
                self._globals.update(world_revise_structure=self.revise_structure,
                    world_refresh=self.bridge.refresh, world_set_focus=self.set_focus,
                    world_begin_interaction=lambda identity, expected_delta, **kw:
                        self.world.begin_interaction(identity, expected_delta, at=now(), **kw),
                    world_finish_interaction=self.finish_interaction,
                    goto_pose_report=self.goto_pose_report,
                    world_require_relation=self.require_relation)
            if self.execution_contract is not None:
                self._globals.update(motion_workspace=self.motion_workspace,
                    motion_contract_status=self.contract_status,
                    world_bind_held=lambda identity, **kw: self.world.held.bind(identity, at=now(), **kw),
                    world_held_state=lambda identity: self.world.held.state(identity, at=now()),
                    world_release_held=self.release_held,
                    world_visible_geometry=self.visible_geometry,
                    geometry_from_points=lambda points, **kw: visible_geometry_summary(points, **{"up": self._up, **kw}),
                    tcp_target_for_feature=tcp_target_for_feature,
                    support_placement_candidates=support_placement_candidates,
                    cylinder_cross_section=cylinder_cross_section)
            if self.articulation:
                self._globals.update(world_query_joint=lambda child: self.world.joint_query(child, at=now()),
                    world_predict_joint=lambda child, q, **kw: self.world.predict_joint(child, q, at=now(), **kw),
                    world_begin_joint_interaction=lambda child, delta_q, **kw:
                        self.world.begin_joint_interaction(child,delta_q,at=now(),**kw),
                    world_review_pending=self.process_reviews)
                self.process_reviews()
        except Exception:
            # No helper name existed before (checked above): leave none behind.
            for name in names:
                self._globals.pop(name, None)
            if self.bridge is not None:
                self.bridge.close()
            else:
                tracker.close()
            raise

    def _ground(self, frame, item):
        """Ground one identity in current public SAM3 candidates.

        The authored prompt is tried first; author-declared alternative_prompts
        are tried in order ONLY while every earlier prompt found no distinct
        candidate (kind "missing"). Ambiguous, rank-unavailable and conflicting
        results stop immediately; no instance is ever chosen arbitrarily.
        """
        segment = self._globals["segment_sam3_text_prompt"]
        old = self.world._last_good.get(item["identity"])
        prompts = [item["prompt"], *valid_alternative_prompts(item.get("alternative_prompts"), item["prompt"])]
        attempts = []
        for prompt in prompts:
            try:
                mask, evidence = select_seed(frame, segment(frame.rgb, prompt), {**item, "prompt": prompt},
                    old["center"] if old is not None else None, max_distance=self.world.max_identity_jump,
                    motion_previous=old, max_age=self.world.cadence.object_max_age)
            except SeedSelectionError as exc:
                attempts.append(exc.evidence)
                if exc.kind != "missing":
                    break
                continue
            return mask, {**evidence, "prompt_used": prompt, "earlier_attempts": attempts}
        kind = attempts[-1]["kind"]
        raise SeedSelectionError(kind, f"{kind} public seed for {item['identity']!r} after "
            f"{len(attempts)} declared prompt(s); refine the public prompt/selector or reobserve",
            {"identity": item["identity"], "attempts": attempts})

    def _reacquire_failed(self, frame, identity, reason, evidence):
        self.world.invalidate_objects(frame, reason=reason)
        self.bridge.disarm("reacquire_failed", reason, frame=frame, evidence=evidence)
        self.journal.counts["reacquire_failed"] += 1
        self.journal.record("reacquire_failed", frame_id=frame.frame_id, captured_at=frame.captured_at,
                            identity=identity, reason=reason, evidence=evidence)

    def reacquire(self):
        """Same-frame public SAM3 initialization; ambiguous identities are refused."""
        frame = self.bridge.capture()
        definitions = self.definitions
        identities = [item["identity"] for item in definitions]
        if not definitions or len(set(identities)) != len(identities):
            raise ValueError("objects must have unique public identities")
        segment = self._globals["segment_sam3_text_prompt"]
        masks, associations = {}, {}
        for item in definitions:
            threshold = float(item.get("min_seed_score", 0.5))
            if not 0 <= threshold <= 1:
                raise ValueError("min_seed_score must lie between zero and one")
            if self.semantic:
                try:
                    masks[item["identity"]], associations[item["identity"]] = self._ground(frame, item)
                except ValueError as exc:
                    evidence = getattr(exc, "evidence", {"kind": "invalid_definition"})
                    self._reacquire_failed(frame, item["identity"], str(exc), evidence)
                    raise StreamingSeedError(str(exc), evidence) from exc
                continue
            candidates = [m for m in segment(frame.rgb, item["prompt"])
                          if float(m.get("score", 0)) >= threshold]
            if len(candidates) != 1:
                evidence = {"kind": "missing" if not candidates else "ambiguous",
                            "candidates_above_threshold": len(candidates)}
                self._reacquire_failed(frame, item["identity"],
                                       "ambiguous or missing public reacquisition", evidence)
                raise StreamingSeedError(f"ambiguous or missing public seed for {item['identity']}; "
                                         "refine its prompt", evidence)
            masks[item["identity"]] = candidates[0]["mask"]
        if self.semantic and not self.world.parts:
            self.world.revise_structure(frame, masks, self.parts, reason="initial public scene decomposition")
            self.bridge._armed_after(frame, "initial public scene decomposition")
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
            try:
                if self.semantic:
                    self.world.tracker.reset()
                    self.bridge.seed(frame, masks, association=associations)
                else:
                    self.bridge.seed(frame, masks)
            except Exception as exc:
                # bridge.seed already disarmed (reacquire_failed); geometry withdrawn.
                self.journal.counts["reacquire_failed"] += 1
                self.journal.record("reacquire_failed", frame_id=frame.frame_id, captured_at=frame.captured_at,
                                    identity=None, reason=f"tracker seeding failed: {type(exc).__name__}: {exc}",
                                    evidence={"kind": "tracker_seed_failed"})
                raise
        self.journal.counts["reacquire_ok"] += 1
        self.journal.record("reacquire_ok", frame_id=frame.frame_id, captured_at=frame.captured_at,
                            identities=sorted(masks),
                            associations={k: _association_summary(v) for k, v in associations.items()})

    def recovery_status(self):
        """Public recovery report for policies: state, reason, evidence, permission.

        state is armed|unseeded|tracking_lost|reacquire_failed|
        structure_grounding_failed|closed. When motion_permitted is false the
        next actual motor/gripper step is refused before it executes, until
        reacquire_world() (or a successful structure revision) reseeds every
        identity from valid current public masks.
        """
        status = self.bridge.recovery_status()
        if not self.bridge._closed:
            at = self.env.low_level_env.get_current_time_s()
            observed = self.world.snapshot(at=at)["observed"]
            status["objects"] = {k: {"status": v.get("status"), "reason": v.get("reason"),
                                     "frame_id": v.get("frame_id")} for k, v in observed.items()}
        return copy.deepcopy(status)

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

    def _focus_gate(self):
        if not self._focus or not self.bridge._armed:
            return  # the bridge's own gate handles a disarmed World
        at = self.env.low_level_env.get_current_time_s()
        facts = {k: self.world.query(k, at=at) for k in self._focus}
        missing = {k: v.get("reason") for k, v in facts.items() if v.get("status") != "known"}
        if missing:
            self._count("focus_motion_refused")
            self.journal.record("focus_motion_refused", captured_at=at, identities=sorted(missing),
                                reasons=missing)
            raise MotionInterrupted(f"focused part evidence is unknown or stale {sorted(missing)}; "
                                    "refresh/reacquire or change focus before commanding motion")

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
        masks = {}
        try:
            for item in objects:
                masks[item["identity"]] = self._ground(frame, item)[0]
        except Exception as exc:
            reason = "structural proposal could not be grounded in current public masks"
            self.world.invalidate_objects(frame, reason=reason)
            self.bridge.disarm("structure_grounding_failed", f"{reason}: {exc}", frame=frame,
                               evidence=getattr(exc, "evidence", {"error_type": type(exc).__name__}))
            raise
        was_armed = self.bridge._armed
        try:
            revision = self.world.revise_structure(frame, masks, parts, reason=reason,
                                                   evidence_ids=evidence_ids, lineage=lineage)
        except Exception as exc:
            # Validation precedes tracker mutation: an invalid proposal leaves the
            # seeded World (and its arming) unchanged. A tracker reinitialization
            # failure withdraws geometry and disarms.
            if not (isinstance(exc, ValueError) and self.world._seeded and was_armed):
                self.bridge.disarm("structure_grounding_failed",
                                   f"structure reseed failed: {type(exc).__name__}: {exc}", frame=frame)
            raise
        self.parts, self.definitions = copy.deepcopy(parts), copy.deepcopy(objects)
        self._focus = tuple(k for k in self._focus if k in masks)
        self.bridge._armed_after(frame, "structure revision reseeded every identity")
        if self.articulation:
            self.world.configure_joints(joints, reason=reason, evidence_ids=evidence_ids)
        self.journal.record("structure_revision", revision=revision, reason=reason,
            evidence_ids=list(evidence_ids), frame_id=frame.frame_id,
            captured_at=frame.captured_at, identities=sorted(masks), lineage=lineage or {})
        return revision

    def _count(self, name):
        self.journal.counts[name] = self.journal.counts.get(name, 0) + 1

    def _review_packet(self, pending, now):
        """Detached event-local packet: complete current graph, current facts,
        fits and motion, plus only the registered evidence IDs they cite.

        Accumulated history (earlier revisions, model history, receipts,
        unrelated interaction checks, every past frame ID) is omitted.
        """
        snap = self.world.snapshot(at=now)
        semantic, articulation = snap["semantic"], snap["articulation"]
        event_ids = {i for event in pending for i in event["evidence_ids"]}
        checks = [c for c in semantic["interaction_checks"] + articulation["joint_interaction_checks"]
                  if event_ids & set(c.get("evidence_ids", []))]
        cited = set(event_ids)
        cited.update(i for fact in snap["observed"].values() for i in fact.get("evidence_ids", []))
        cited.update(i for model in articulation["models"].values() for i in model.get("evidence_ids", []))
        cited.update(i for check in checks for i in check.get("evidence_ids", []))
        # Only registered observed IDs; predictions cannot authorize a revision.
        ids = sorted(cited & self.world._evidence)
        return {"episode_id": self.world.episode_id, "events": copy.deepcopy(pending),
            "evidence_ids": ids, "objects": copy.deepcopy(self.definitions),
            "snapshot": {"captured_at": now, "coordinate_frame": snap["coordinate_frame"],
                "robot": snap["robot"], "observed": snap["observed"], "relations": snap["relations"],
                "semantic": {k: semantic[k] for k in ("structure_revision", "belief_revision", "parts")},
                "articulation": {k: articulation[k] for k in ("model_revision", "joints", "models", "part_motion")}},
            "relevant_interaction_checks": checks,
            "recent_motion_reports": list(self._reports.values())[-4:]}

    def _rearm_deferred(self, now):
        deferred = [r for r in self.world.review_requests if r["status"] == "deferred_no_call"]
        if not deferred:
            return
        informative = self.world.informative_motion()
        if not informative:
            return
        for request in deferred:
            request.update(status="pending", rearmed_at=now,
                           evidence_ids=sorted(set(request["evidence_ids"]) | set(informative)))
        self.journal.record("world_review_rearmed", captured_at=now, event_reasons=[r["reason"] for r in deferred],
                            evidence_ids=informative)

    def process_reviews(self):
        """Only queued public events call Qwen; control is paused on this thread.

        Initial model reviews without informative measured motion are deferred
        WITHOUT a model call (journal ``world_review_deferred_no_call``) and are
        re-armed when informative public motion is registered. Each actual call
        is journaled once with its inference receipt and the action outcome.
        """
        if not self.articulation:
            return []
        now = self.env.low_level_env.get_current_time_s()
        self._rearm_deferred(now)
        pending = [r for r in self.world.review_requests if r["status"] == "pending"]
        if not pending:
            return []
        reasons = [r["reason"] for r in pending]
        if all(r == "initial_model_review" for r in reasons) and not self.world.informative_motion():
            for request in pending:
                request.update(status="deferred_no_call", deferred_at=now)
            record = {"status": "deferred_no_call", "qwen_called": False, "episode_id": self.world.episode_id,
                      "captured_at": now, "event_reasons": reasons,
                      "reason": "no informative public motion yet; review waits for new measured evidence"}
            self._count("world_review_deferred_no_call")
            self.journal.record("world_review_deferred_no_call", **record)
            return [record]
        packet = self._review_packet(pending, now)
        base = {"episode_id": self.world.episode_id, "captured_at": now, "event_reasons": reasons,
                "packet_evidence_ids": len(packet["evidence_ids"])}
        try:
            reply, receipt = self.reviewer.review(packet,
                rgb=self.world.latest_frame.rgb if self.world.latest_frame is not None else None)
        except ReviewRejected as exc:
            # Malformed/invalid reply data: inference happened; World unchanged.
            receipt = copy.deepcopy(exc.receipt)
            receipt.update(base, qwen_called=True, status="rejected", outcome="data_rejected", reason=str(exc),
                structure_revision=self.world.structure_revision, model_revision=self.world.model_revision)
            for event in pending:
                event["status"] = "rejected"
            self.world.review_history.append(copy.deepcopy(receipt))
            self._count("qwen_review_rejections")
            self.journal.record("qwen_world_review_rejected", **receipt)
            if self.bridge._armed:
                return [receipt]  # unchanged, fresh World remains usable
            raise MotionInterrupted("online World review proposal was rejected while the World is disarmed") from exc
        except Exception as exc:
            # Service outage/transport failure: no valid inference receipt exists.
            for event in pending:
                event["status"] = "outage"
            self._count("qwen_review_outages")
            self.journal.record("qwen_world_review_outage", **base, qwen_called="unknown",
                                outcome="service_outage", error_type=type(exc).__name__, error=str(exc))
            raise MotionInterrupted("online World review service failed") from exc
        receipt = copy.deepcopy(receipt)
        receipt.update(base, qwen_called=True)
        action, error = reply["action"], None
        outcome = {"action": action, "status": "applied"}
        try:
            if action == "revise":
                self.revise_structure(reply["parts"], reply["objects"], reason=reply["reason"],
                    evidence_ids=reply["evidence_ids"], lineage=reply.get("lineage"), joints=reply["joints"])
            elif action == "select_models":
                self.world.configure_joints(reply["joints"], reason=reply["reason"], evidence_ids=reply["evidence_ids"])
            elif action == "reacquire":
                self.reacquire()
        except Exception as exc:
            error = exc
            outcome.update(status="grounding_failed" if not self.bridge._armed else "action_rejected",
                           error_type=type(exc).__name__, reason=str(exc),
                           recovery_state=self.bridge.recovery_status()["state"])
        for event in pending:
            event["status"] = ("action_failed" if error is not None else
                               "awaiting_evidence" if action == "defer" else "reviewed")
        receipt.update(outcome="accepted" if error is None else outcome["status"], action_outcome=outcome,
            structure_revision=self.world.structure_revision, model_revision=self.world.model_revision)
        self.world.review_history.append(copy.deepcopy(receipt))
        self._count("qwen_reviews")
        if error is not None:
            self._count("qwen_review_action_failures")
        self.journal.record("qwen_world_review", **receipt)
        if error is None:
            return [receipt]
        if not self.bridge._armed:
            raise MotionInterrupted("accepted World review action could not be grounded; "
                                    "the World is disarmed until valid reseeding") from error
        if isinstance(error, ValueError):
            return [receipt]  # validation refused the action; unchanged valid World
        raise error

    def _reset_semantic(self):
        self.definitions = copy.deepcopy(self.config["objects"])
        self.parts = copy.deepcopy(self.config.get("parts", [
            {"identity": item["identity"], "semantic": item["prompt"], "tracked": True}
            for item in self.definitions]))
        self._focus, self._reports = (), {}
        if self.contract is not None:
            self.contract.reset()

    def _motion_api(self):
        apis = getattr(self.env, "_apis", None) or {}
        return next((a for a in apis.values() if hasattr(a, "_TCP_OFFSET")), None)

    # --- opt-in checked-execution contract ---------------------------------

    def _install_contract(self, api):
        low = self.env.low_level_env
        if api is None or not callable(getattr(api, "workspace_contract", None)):
            raise ValueError("execution_contract requires the motion API's public workspace contract")
        if not callable(getattr(low, "add_gripper_gate_callback", None)):
            raise TypeError("execution_contract requires a driver gripper gate")
        self.contract = CheckedMotionContract(lambda: getattr(low, "arm_motion_count", 0))
        self.bridge._removers.append(low.add_gripper_gate_callback(self._gripper_gate))
        # Gravity-up in robot_base from the public mounting calibration.
        base = base_pose_world(low.get_robot_observation())
        if base is not None:
            self._up, self._up_source = (rotation_wxyz(base[3:]).T @ [0., 0., 1.]).tolist(), "robot_base_pose_world"
        else:
            self._up, self._up_source = [0., 0., 1.], "assumed robot_base +z (mounting calibration unpublished)"
        self.world.held = HeldGeometry(self.world, up=self._up)

    def _gripper_gate(self, requested, current):
        try:
            self.contract.check_gripper(requested, current)
        except ExecutionContractError as exc:
            self._count("gripper_refused")
            self.journal.record("gripper_refused", requested=requested, current=current,
                                captured_at=self.env.low_level_env.get_current_time_s(),
                                contract=self.contract.permission(), error=str(exc))
            raise

    def motion_workspace(self):
        """Public target semantics: frames, TCP, software clip, fallbacks, tolerances."""
        return {**self._motion_api().workspace_contract(), "contract": CONTRACT_VERSION,
                "position_tolerance": .015, "angle_tolerance": .15,
                "checked_motion": ("goto_pose_report refuses to execute a clipped or fallback target unless "
                                   "execute_modified=True; a gripper change is refused unless the last arm "
                                   "motion was a checked report with reached=true and target_modified=false, "
                                   "or the arm has not moved since the episode started")}

    def contract_status(self):
        return self.contract.permission()

    def release_held(self, identity, reason="policy release"):
        self.world.held.end(identity, "released", str(reason), at=self.env.low_level_env.get_current_time_s())
        return self.world.held.state(identity, at=self.env.low_level_env.get_current_time_s())

    def visible_geometry(self, identity, *, near=None, radius=None):
        """Visible-surface geometry of a tracked part from its current mask; else unknown."""
        at = self.env.low_level_env.get_current_time_s()
        base = {"identity": identity, "layer": "observed_geometry", "status": "unknown",
                "coordinate_frame": self.world.coordinate_frame, "up_source": self._up_source}
        part = self.world.parts.get(identity)
        if part is None:
            raise ValueError("geometry query requires a part identity in the current structure")
        if not part["tracked"]:
            return {**base, "reason": "tracked:false part has no public mask; geometry and joint parameters unknown"}
        fact = self.world.query(identity, at=at)
        good = self.world._last_good.get(identity)
        if fact["status"] != "known" or good is None or good["frame"].frame_id != fact["frame_id"]:
            return {**base, "reason": fact.get("reason") or "no current measured mask"}
        summary = visible_geometry_summary(mask_points(good["frame"], good["mask"]), up=self._up,
                                           near=near, radius=radius)
        return {**summary, **{k: v for k, v in base.items() if k != "status"},
                "frame_id": fact["frame_id"], "captured_at": fact["captured_at"],
                "evidence_ids": fact["evidence_ids"]}

    def _leg(self, leg, target, quaternion_wxyz, api, low):
        api._last_ik_report = None
        low._last_motion_report = None
        diagnostics = workspace_diagnostics(target, api._WORKSPACE_LOW, api._WORKSPACE_HIGH)
        def measure(joints):
            report = motion_report(target, quaternion_wxyz, low.get_robot_observation(),
                ik=getattr(api, "_last_ik_report", None), joints=joints, frame=POLICY_FRAME,
                tcp_offset=api._TCP_OFFSET)
            report.update(leg=leg, workspace=diagnostics)
            return report
        try:
            joints = self._globals["solve_ik"](target, quaternion_wxyz)
        except RuntimeError as exc:
            report = measure(None)
            report.update(status="ik_failed", reached=False, executed=False,
                          reason=f"IK failed for the requested and fallback orientations: {exc}")
            return report
        return joints, measure

    def _checked_goto(self, position, quaternion_wxyz, z_approach, execute_modified):
        api, low = self._motion_api(), self.env.low_level_env
        action_id = f"motion-{len(self._reports)+1}"
        self.contract.begin(action_id)  # an exception below leaves this action in flight
        targets = [("final", np.asarray(position, dtype=float))]
        if z_approach > 0.0:
            approach = targets[0][1].copy()
            approach[2] += z_approach
            targets.insert(0, ("approach", approach))
        legs = []
        for leg, target in targets:
            planned = self._leg(leg, target, quaternion_wxyz, api, low)
            if isinstance(planned, dict):
                report = planned
            else:
                joints, measure = planned
                ik = api._last_ik_report or {}
                if (ik.get("position_clipped") or ik.get("orientation_fallback")) and not execute_modified:
                    report = measure(None)
                    report.update(status="refused_target_modified", reached=False, executed=False,
                                  reason="IK changed the requested target (workspace clip or orientation "
                                         "fallback); not executed. Replan, or pass execute_modified=True "
                                         "(arrival stays unverified)")
                else:
                    self._globals["move_to_joints"](joints)
                    report = measure(low._last_motion_report)
                    report["executed"] = True
            legs.append({k: report.get(k) for k in ("leg", "status", "reached", "target_modified", "executed",
                                                    "position_error", "angle_error", "reason")})
            if report.get("reached") is not True or report.get("target_modified") is not False:
                break
        report.update(final_requested=targets[-1][1].tolist(), legs=legs, z_approach=float(z_approach),
                      execute_modified=bool(execute_modified))
        return report

    def goto_pose_report(self, position, quaternion_wxyz, z_approach=0.0, *, execute_modified=False):
        """Execute ordinary goto_pose and measure requested TCP arrival separately.

        Returns a public report with reached, actual error and effective IK target.
        It never reports contact, grasping or task success. Control errors raise.
        With the opt-in execution contract, each leg (approach, final) is solved
        and checked separately; a clipped/fallback target is not executed unless
        ``execute_modified=True``; later legs stop after a failed leg; the report
        carries the contract's gripper permission.
        """
        rotation_wxyz(quaternion_wxyz)
        pos = np.asarray(position, dtype=float)
        if pos.shape != (3,) or not np.isfinite(pos).all():
            raise ValueError("one finite XYZ position is required")
        if self.contract is not None:
            report = self._checked_goto(pos, quaternion_wxyz, float(z_approach), execute_modified)
            now = self.env.low_level_env.get_current_time_s()
            report.update(action_id=f"motion-{len(self._reports)+1}", captured_at=now,
                          episode_id=self.world.episode_id)
            self.contract.record(report)
            report["contract"] = self.contract.permission()
            self._reports[report["action_id"]] = copy.deepcopy(report)
            self.journal.record("motion_report", **report)
            return copy.deepcopy(report)
        if execute_modified:
            raise ValueError("execute_modified exists only under the opt-in execution contract")
        api = self._motion_api()
        if api is not None:
            api._last_ik_report = None
        low = self.env.low_level_env
        low._last_motion_report = None
        self._globals["goto_pose"](position, quaternion_wxyz, z_approach=z_approach)
        now = low.get_current_time_s()
        # Target, effective IK target and measured hand all in robot_base.
        report = motion_report(position, quaternion_wxyz, low.get_robot_observation(),
            ik=getattr(api, "_last_ik_report", None), joints=getattr(low, "_last_motion_report", None),
            frame=POLICY_FRAME, tcp_offset=None if api is None else api._TCP_OFFSET)
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
