# SPDX-License-Identifier: Apache-2.0
"""Semantic v3: observed relative motion, fitted joints and event review."""
from __future__ import annotations

import copy
import numpy as np

from .World import _finite
from .semantic_world import SemanticWorld, ancestors
from .joint_models import FAMILIES, evaluate, fit_models, residual, rotvec, transform
from .rigid_tracking import PublicRigidTracker

ARTICULATION_MODE = "sam2-articulation-world-v3"


def validate_joints(joints, parts):
    if not isinstance(joints, list):
        raise ValueError("joints must be a list of parent-child motion hypotheses")
    result = {}
    for joint in joints:
        if not isinstance(joint, dict) or set(joint)-{"child", "parent", "family"}:
            raise ValueError("joint fields are child, parent and family")
        child, parent, family = joint.get("child"), joint.get("parent"), joint.get("family", "unknown")
        root_free = parent is None and family in ("free", "unknown")
        if (child not in parts or (parent not in parts and not root_free) or child in result
                or parts[child].get("parent") != parent or family not in FAMILIES):
            raise ValueError("joint must describe one existing parent-child edge and registered family")
        result[child] = {"child": child, "parent": parent, "family": family}
    return result


class ArticulatedWorld(SemanticWorld):
    def _clear(self):
        super()._clear()
        self.rigid_tracker = PublicRigidTracker()
        self.part_motion, self.joints, self.joint_samples, self.joint_models = {}, {}, {}, {}
        self._joint_epochs = {}
        self.model_revision, self.model_history, self.review_history = 0, [], []
        self.latest_frame = None
        self._joint_pending, self.joint_checks = {}, []

    def configure_joints(self, joints, *, reason, evidence_ids):
        proposed = validate_joints(joints, self.parts)
        if not reason or not evidence_ids or not set(evidence_ids) <= self._evidence:
            raise ValueError("joint proposal requires recorded public evidence and reason")
        before = copy.deepcopy(self.joints)
        histories = {k: self.joint_samples.get(k, []) if before.get(k, {}).get("parent") == v["parent"] else [] for k, v in proposed.items()}
        fits = {k: fit_models(histories[k], families=FAMILIES, preferred=v["family"] if v["family"] != "unknown" else None) for k, v in proposed.items()}
        if any("proposed family contradicts" in f["reason"] for f in fits.values()):
            raise ValueError("joint family proposal contradicts validated public motion")
        self.joints = proposed
        self.joint_samples, self.joint_models = histories, fits
        if proposed == before:
            return
        self._predictions.clear()
        self._relations.clear()
        self.model_revision += 1
        self.model_history.append({"revision": self.model_revision, "before": before,
            "after": copy.deepcopy(proposed), "reason": reason, "evidence_ids": list(evidence_ids),
            "meaning": "proposed families; numerical parameters need motion validation"})

    def _request_review(self, reason, identity, evidence_ids):
        # Reconsider a later contradiction after a model change, without calling
        # the LLM again on every frame of the same unresolved event.
        key = (reason, identity, self.structure_revision, self.model_revision)
        if any((r["reason"], r["identity"], r["structure_revision"], r.get("model_revision")) == key for r in self.review_requests):
            return
        self.review_requests.append({"reason": reason, "identity": identity,
            "structure_revision": self.structure_revision, "model_revision": self.model_revision,
            "evidence_ids": list(evidence_ids), "status": "pending"})

    def _fit(self, child):
        family = self.joints[child]["family"]
        return fit_models(self.joint_samples.get(child, []), families=FAMILIES, preferred=family if family != "unknown" else None)

    def _accept(self, frame, masks):
        super()._accept(frame, masks)
        # A captured ambiguous/low-depth image is still visual evidence for
        # reassociation, while its geometry and motion remain unknown.
        for key, fact in self._objects.items():
            if masks.get(key) is not None:
                self._evidence.update(fact["evidence_ids"])
        nodes = self._candidate_parts or self.parts
        exclusive = {k: None if m is None else np.asarray(m, dtype=bool).copy() for k, m in masks.items()}
        for child, mask in masks.items():
            if mask is not None:
                for parent in ancestors(nodes, child):
                    if exclusive.get(parent) is not None:
                        exclusive[parent] &= ~np.asarray(mask, dtype=bool)
        for key, fact in self._objects.items():
            if fact["status"] != "known":
                exclusive[key] = None
        if self._candidate_parts is not None:
            # Every structural reseed starts a new observed geometry reference.
            self.rigid_tracker.reset()
            self.joint_samples, self.joint_models, self._joint_epochs = {}, {}, {}
        self.part_motion = self.rigid_tracker.update(frame, exclusive)
        self.latest_frame = frame
        self._update_joints(frame)

    def _update_joints(self, frame):
        for child, joint in self.joints.items():
            parent = joint["parent"]
            if parent is None:
                self.joint_models[child] = {"status": "unknown", "selected": None,
                    "reason": "free or unknown root motion has no articulation reference", "sample_count": 0}
                continue
            a, b = self.part_motion.get(parent, {}), self.part_motion.get(child, {})
            epoch = (a.get("epoch"), b.get("epoch"))
            if epoch != self._joint_epochs.get(child):
                self.joint_samples[child] = []
                self._joint_epochs[child] = epoch
            history = self.joint_samples.setdefault(child, [])
            usable = a.get("status") == b.get("status") == "estimated"
            if usable and (not history or frame.captured_at > history[-1]["captured_at"]):
                relative = np.linalg.inv(transform(a["motion_transform"])) @ transform(b["motion_transform"])
                evidence = sorted({e for k in (parent, child) for e in self._objects[k]["evidence_ids"]})
                history.append({"relative_transform": relative.tolist(), "captured_at": frame.captured_at,
                                "frame_id": frame.frame_id, "evidence_ids": evidence})
                # Numerical sliding window, not a task/action/review budget.
                del history[:-64]
                previous = self.joint_models.get(child, {}).get("selected")
                if previous is not None:
                    current = relative @ np.linalg.inv(transform(previous["origin_transform"]))
                    error = residual(previous, [current])
                    if error["position_rmse"] > .012 or error["angle_rmse"] > .06:
                        self._request_review("joint_model_contradiction", child, evidence)
                self.joint_models[child] = self._fit(child)
                if self.joint_models[child]["status"] == "fitted" and previous is None:
                    if any(r["status"] == "awaiting_evidence" for r in self.review_requests):
                        self._request_review("joint_motion_evidence", child, evidence)
                if "proposed family contradicts" in self.joint_models[child]["reason"]:
                    self._request_review("joint_model_contradiction", child, evidence)
            elif not usable:
                self.joint_models[child] = {"status": "unknown", "selected": None,
                    "reason": "fresh parent and child rigid motion estimates are required", "sample_count": len(history)}

    def joint_query(self, child, *, at):
        result = copy.deepcopy(self.joint_models.get(child, {"status": "unknown", "selected": None, "reason": "no joint hypothesis"}))
        edge = self.joints.get(child)
        if edge and any(self.query(k, at=at)["status"] != "known" for k in (child, edge["parent"]) if k is not None):
            result.update(status="unknown", selected=None, reason="joint's public geometry is unavailable or expired")
        result.update(layer="kinematic_hypothesis", model_revision=self.model_revision,
                      structure_revision=self.structure_revision)
        return _finite(result)

    def predict_joint(self, child, q, *, at, extrapolate=False):
        fit = self.joint_query(child, at=at)
        if fit["status"] != "fitted":
            return {"status": "unknown", "reason": fit["reason"], "layer": "predicted"}
        model = fit["selected"]
        low, high = model["observed_range"]
        if type(extrapolate) is not bool:
            raise ValueError("extrapolate must be an explicit boolean")
        if isinstance(q, bool) or not isinstance(q, (int, float)) or not np.isfinite(q):
            return {"status": "unknown", "reason": "finite joint coordinate required", "layer": "predicted"}
        within = low <= q <= high
        if not within and not extrapolate:
            return {"status": "unknown", "reason": "prediction coordinate outside observed range", "layer": "predicted"}
        relative = evaluate(model, q) @ transform(model["origin_transform"])
        return {"status": "predicted", "relative_transform": relative.tolist(),
                "q": q, "family": model["family"], "layer": "predicted",
                "validation_scope": "observed_range" if within else "unvalidated_extrapolation",
                "physical_joint_limits": "unknown",
                "evidence_ids": fit["evidence_ids"], "parameter_frame": fit["parameter_frame"]}

    def begin_joint_interaction(self, child, delta_q, *, at, position_tolerance=.015, angle_tolerance=.035):
        """Predict part-relative pose change, including rotation about its centre."""
        for value in (delta_q, position_tolerance, angle_tolerance):
            if isinstance(value, bool) or not isinstance(value, (int,float)) or not np.isfinite(value):
                raise ValueError("joint change and tolerances must be finite")
        if position_tolerance < 0 or angle_tolerance < 0:
            raise ValueError("joint tolerances must be nonnegative")
        fit = self.joint_query(child, at=at)
        if fit["status"] != "fitted":
            raise ValueError("joint interaction requires validated public motion")
        parent = self.joints[child]["parent"]
        before = {k:self.query(k,at=at) for k in (parent,child)}
        motion = {k:copy.deepcopy(self.part_motion[k]) for k in (parent,child)}
        if any(m["status"] != "estimated" for m in motion.values()):
            raise ValueError("fresh rigid-part estimates are required")
        model = fit["selected"]
        expected = evaluate(model,model["q"]+delta_q) @ transform(model["origin_transform"])
        key = f"joint-interaction-{len(self.joint_checks)+len(self._joint_pending)+1}"
        self._joint_pending[key] = {"id":key,"child":child,"parent":parent,"at":at,
            "before":before,"motion":motion,"family":model["family"],"delta_q":delta_q,
            "expected_relative_transform":expected.tolist(),"position_tolerance":position_tolerance,
            "angle_tolerance":angle_tolerance,"structure_revision":self.structure_revision,
            "model_revision":self.model_revision}
        return key

    def finish_joint_interaction(self, key, report, *, at):
        p = self._joint_pending.pop(key)
        row = {k:copy.deepcopy(v) for k,v in p.items() if k not in ("before","motion")}
        row.update(action_report=_finite(report),finished_at=at,status="unresolved",diagnosis="observation_missing")
        action_at = report.get("captured_at")
        valid_action = (isinstance(action_at,(int,float)) and not isinstance(action_at,bool)
            and np.isfinite(action_at) and p["at"] < action_at <= at
            and report.get("reached") is True and report.get("target_modified") is False)
        parts = (p["parent"],p["child"])
        after = {k:self.query(k,at=at) for k in parts}
        motion = {k:self.part_motion.get(k,{}) for k in parts}
        fresh = all(after[k]["status"] == "known" and after[k]["frame_id"] > p["before"][k]["frame_id"]
            and after[k]["captured_at"] > p["at"] and motion[k].get("status") == "estimated"
            and motion[k].get("epoch") == p["motion"][k]["epoch"] for k in parts)
        contact = self.query(report.get("contact_relation",""),at=at,layer="relation")
        if not valid_action:
            row["diagnosis"] = "execution_unverified"
        elif self.structure_revision != p["structure_revision"]:
            row["diagnosis"] = "structure_changed"
        elif fresh:
            if contact.get("status") != "known" or contact.get("value") is not True or p["child"] not in contact.get("dependencies",[]):
                row["diagnosis"] = "contact_unverified"
            else:
                measured = np.linalg.inv(transform(motion[p["parent"]]["motion_transform"])) @ transform(motion[p["child"]]["motion_transform"])
                error = np.linalg.inv(transform(p["expected_relative_transform"])) @ measured
                distance, angle = float(np.linalg.norm(error[:3,3])), float(np.linalg.norm(rotvec(error[:3,:3])))
                matched = distance <= p["position_tolerance"] and angle <= p["angle_tolerance"]
                ids = sorted({e for fact in [*p["before"].values(),*after.values(),contact] for e in fact.get("evidence_ids",[])})
                row.update(status="match" if matched else "mismatch",diagnosis="joint_effect_verified" if matched else "joint_effect_contradiction",
                    position_error=distance,angle_error=angle,measured_relative_transform=measured.tolist(),evidence_ids=ids)
                if not matched and self.model_revision == p["model_revision"]:
                    self._request_review("joint_effect_contradiction",p["child"],ids)
        self.joint_checks.append(row)
        return copy.deepcopy(row)

    def snapshot(self, *, at):
        result = super().snapshot(at=at)
        result["articulation"] = {"part_motion": copy.deepcopy(self.part_motion),
            "joints": copy.deepcopy(self.joints), "models": {k: self.joint_query(k, at=at) for k in self.joints},
            "model_revision": self.model_revision, "model_history": copy.deepcopy(self.model_history),
            "joint_interaction_checks":copy.deepcopy(self.joint_checks),
            "reviews": copy.deepcopy(self.review_history)}
        return _finite(result)
