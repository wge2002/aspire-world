# SPDX-License-Identifier: Apache-2.0
"""Episode-local, revisable part hypotheses backed by public observations.

Semantic labels and kinematics remain hypotheses. Neither segmentation scores,
visible centers, closed fingers nor a successful RPC establish physical contact.
The policy proposes decompositions; this runtime validates and records revisions.
"""
from __future__ import annotations

import copy
import math

import numpy as np

from .World import FrameError, TrackingError, World, _finite
from .identity_evidence import SeedSelectionError, deduplicate, measured_motion_association


SEMANTIC_MODE = "sam2-semantic-world-v2"
PART_KEYS = {"identity", "parent", "semantic", "tracked", "hypotheses"}
JOINTS = {"unknown", "fixed", "sliding", "rotating", "free"}


def validate_parts(parts, tracked):
    if not isinstance(parts, list) or not parts:
        raise ValueError("parts must be a nonempty list of scene-derived hypotheses")
    nodes = {}
    for part in parts:
        if not isinstance(part, dict) or set(part) - PART_KEYS:
            raise ValueError(f"part fields must be from {sorted(PART_KEYS)}")
        key = part.get("identity")
        if not isinstance(key, str) or not key or key == "robot" or key in nodes:
            raise ValueError("part identities must be nonempty, unique and different from robot")
        if not isinstance(part.get("semantic", key), str):
            raise ValueError("semantic is a hypothesized text label")
        parent = part.get("parent")
        if parent is not None and (not isinstance(parent, str) or not parent):
            raise ValueError("parent must be a part identity or null")
        measured = part.get("tracked", key in tracked)
        if type(measured) is not bool or measured != (key in tracked):
            raise ValueError("tracked parts must match the same-frame seed identities")
        hypotheses = part.get("hypotheses", {"unknown": 1.0})
        if not isinstance(hypotheses, dict) or not hypotheses or set(hypotheses) - JOINTS:
            raise ValueError(f"motion hypotheses must use {sorted(JOINTS)}")
        for support in hypotheses.values():
            if isinstance(support, bool) or not isinstance(support, (int, float)) or not math.isfinite(support) or not 0 <= support <= 1:
                raise ValueError("hypothesis support must be finite in [0,1]; it is not calibrated probability")
        nodes[key] = {"identity": key, "parent": parent, "semantic": part.get("semantic", key),
                      "tracked": measured, "hypotheses": _finite(hypotheses), "layer": "hypothesis"}
    if set(tracked) - set(nodes):
        raise ValueError("every tracked identity must have a part node")
    for key, node in nodes.items():
        seen, cursor = {key}, node["parent"]
        while cursor is not None:
            if cursor not in nodes or cursor in seen:
                raise ValueError("part hierarchy has a cycle or a missing parent")
            seen.add(cursor)
            cursor = nodes[cursor]["parent"]
    return nodes


def ancestors(nodes, identity):
    result, cursor = set(), nodes.get(identity, {}).get("parent")
    while cursor is not None:
        result.add(cursor)
        cursor = nodes[cursor]["parent"]
    return result


def visible_geometry(frame, mask, min_points=10):
    mask = np.asarray(mask)
    if mask.shape != frame.depth.shape or not np.isin(mask, [0, 1]).all():
        raise FrameError("part mask must be binary and belong to this RGB-D frame")
    ys, xs = np.where(mask.astype(bool) & np.isfinite(frame.depth) & (frame.depth > 0))
    if len(xs) < min_points:
        return None
    pixels = np.stack([xs, ys, np.ones(len(xs))], axis=0)
    points = (np.linalg.inv(frame.intrinsics) @ pixels).T * frame.depth[ys, xs, None]
    points = points @ frame.camera_to_world[:3, :3].T + frame.camera_to_world[:3, 3]
    return {"visible_center": np.median(points, axis=0).tolist(),
            "visible_bounds": [points.min(axis=0).tolist(), points.max(axis=0).tolist()]}


def choose_seed(frame, candidates, definition, previous=None, *, max_distance=.06, **kwargs):
    """Associate actual SAM3 candidates; scores alone never prove instance identity."""
    return select_seed(frame, candidates, definition, previous, max_distance=max_distance, **kwargs)[0]


# Two distinct candidates whose mean image coordinates differ by less than
# this many pixels have no well-defined public rank order (declared heuristic).
RANK_MARGIN_PIXELS = 1.0


def select_seed(frame, candidates, definition, previous=None, *, max_distance=.06,
                motion_previous=None, max_age=None):
    """Return (mask, evidence) or raise SeedSelectionError with structured evidence.

    Near-duplicate masks are merged deterministically before counting/ranking.
    With ``previous`` (reacquisition), the original 3D gate/ambiguity margin
    applies; only if it fails may CURRENT measured RGB-D motion from
    ``motion_previous`` (the identity's last accepted surface) associate a
    moved instance, and only uniquely. Otherwise the identity stays unknown.
    """
    threshold = definition.get("min_seed_score", .5)
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not 0 <= threshold <= 1:
        raise ValueError("invalid min_seed_score")
    above = [c for c in candidates if float(c.get("score", 0)) >= threshold]
    distinct, merged = deduplicate(above)
    options = []
    for candidate in distinct:
        geometry = visible_geometry(frame, candidate["mask"])
        if geometry is not None:
            options.append((candidate, geometry))
    evidence = {"identity": definition.get("identity"), "prompt": definition.get("prompt"),
                "frame_id": frame.frame_id, "captured_at": frame.captured_at,
                "min_seed_score": threshold, "candidates_total": len(candidates),
                "above_threshold": len(above), "duplicates_merged": merged,
                "distinct": len(distinct), "with_depth": len(options)}
    if previous is not None:
        previous = np.asarray(previous, dtype=float)
        ordered = sorted((float(np.linalg.norm(np.asarray(g["visible_center"]) - previous)), i)
                         for i, (_, g) in enumerate(options))
        evidence.update(previous_center=previous.tolist(), distances=[d for d, _ in ordered],
                        gate=max_distance)
        tied = len(ordered) > 1 and ordered[1][0] - ordered[0][0] < .01
        strict = ordered[0][1] if ordered and ordered[0][0] <= max_distance and not tied else None
        motion = None
        if motion_previous is not None and options and max_age is not None:
            motion = measured_motion_association(motion_previous, frame,
                [{"mask": c["mask"], "visible_center": g["visible_center"]} for c, g in options],
                gate=max_distance, max_age=max_age)
            evidence["measured_motion"] = motion
        if strict is not None:
            if motion is not None and motion["status"] == "verified" and motion["candidate"] != strict:
                # Coherent measured motion says the identity moved to ANOTHER
                # candidate: the nearby one is a different instance.
                raise SeedSelectionError("identity_conflict", "a nearby candidate conflicts with coherent "
                                         "measured motion of the previous identity", evidence)
            return options[strict][0]["mask"], {**evidence, "association": "strict_gate",
                                                "distance": ordered[0][0]}
        if motion is not None and motion["status"] == "verified":
            return options[motion["candidate"]][0]["mask"], {**evidence, "association": "measured_motion"}
        kind = ("missing" if not options else
                "ambiguous" if tied and ordered[0][0] <= max_distance
                or evidence.get("measured_motion", {}).get("status") == "ambiguous" else "identity_conflict")
        raise SeedSelectionError(kind, "public reacquisition is missing, ambiguous or conflicts "
                                 f"with the previous identity ({kind})", evidence)
    selector = definition.get("selector")
    if selector is not None:
        if not isinstance(selector, dict) or set(selector) != {"axis", "rank"} or selector["axis"] not in ("image_x", "image_y") or type(selector["rank"]) is not int or selector["rank"] < 0:
            raise ValueError("selector requires image_x/image_y axis and a nonnegative rank")
        axis = 1 if selector["axis"] == "image_x" else 0
        keyed = sorted((float(np.mean(np.where(np.asarray(c["mask"], dtype=bool))[axis])), i)
                       for i, (c, _) in enumerate(options))
        rank = selector["rank"]
        evidence.update(selector=dict(selector), rank_coordinates=[k for k, _ in keyed])
        if rank >= len(keyed):
            raise SeedSelectionError("rank_unavailable", f"public candidate rank {rank} is unavailable: "
                                     f"{len(keyed)} distinct candidate(s) with depth", evidence)
        neighbours = [keyed[j][0] for j in (rank - 1, rank + 1) if 0 <= j < len(keyed)]
        if any(abs(keyed[rank][0] - n) < RANK_MARGIN_PIXELS for n in neighbours):
            raise SeedSelectionError("ambiguous", f"public candidate rank {rank} has no well-defined "
                                     "image order", evidence)
        return options[keyed[rank][1]][0]["mask"], {**evidence, "association": "initial_rank"}
    if len(options) != 1:
        kind = "missing" if not options else "ambiguous"
        raise SeedSelectionError(kind, f"initial public part seed is {kind}: {len(options)} distinct "
                                 "candidate(s) with depth", evidence)
    return options[0][0]["mask"], {**evidence, "association": "initial_unique"}


def _association_summary(evidence):
    keep = ("association", "prompt_used", "distance", "duplicates_merged", "frame_id")
    summary = {k: evidence[k] for k in keep if k in evidence}
    motion = evidence.get("measured_motion")
    if summary.get("association") == "measured_motion" and motion is not None:
        summary["measured_motion"] = motion
    return summary


class SemanticWorld(World):
    def __init__(self, *args, max_identity_jump=.06, **kwargs):
        if isinstance(max_identity_jump, bool) or not math.isfinite(max_identity_jump) or max_identity_jump <= 0:
            raise ValueError("max_identity_jump must be positive and finite")
        self.max_identity_jump = max_identity_jump
        super().__init__(*args, **kwargs)

    def _clear(self):
        super()._clear()
        self.parts, self.structure_history, self.interactions = {}, [], []
        self.structure_revision = 0
        self.belief_revision = 0
        self._evidence, self._used_ids, self._pending = set(), set(), {}
        self.review_requests = []
        self._last_good = {}
        self._candidate_parts = None
        self._seed_association = {}

    def seed(self, frame, masks, *, association=None):
        """Explicit (re)seeding. ``association`` (select_seed evidence) is recorded
        as a diagnostic ``claimed_association`` only; identity across a jump
        beyond max_identity_jump is always recomputed from this frame's mask
        and the previous measured surface, else geometry is withdrawn."""
        with self._lock:
            reseed = self._candidate_parts is None and bool(self._identities)
            self._seed_association = dict(association or {})
            try:
                super().seed(frame, masks)
            finally:
                self._seed_association = {}
            if reseed:
                # Pre-loss/pre-reseed interaction baselines cannot verify an effect.
                for pending in self._pending.values():
                    self.interactions.append({"id": pending["id"], "status": "unresolved",
                        "reason": "objects explicitly reseeded before interaction verification"})
                self._pending.clear()

    def _accept(self, frame, masks):
        nodes = self._candidate_parts or self.parts
        # Subtract descendants from a parent's observed surface. A containing
        # mask is not an independent rigid part and must not fake co-motion.
        exclusive = {key: None if value is None else np.asarray(value, dtype=bool).copy()
                     for key, value in masks.items()}
        for child, mask in masks.items():
            if mask is None:
                continue
            for parent in ancestors(nodes, child):
                if exclusive.get(parent) is not None:
                    exclusive[parent] &= ~np.asarray(mask, dtype=bool)
        super()._accept(frame, exclusive)
        for key, fact in self._objects.items():
            old = self._last_good.get(key)
            if fact["status"] == "known" and old is not None and self._candidate_parts is None:
                center = np.asarray(fact["value"]["visible_center"])
                jump = np.linalg.norm(center - old["center"])
                if key in self._seed_association:
                    # Caller-supplied association is diagnostic only; it never
                    # authorizes identity. A jump is re-verified below from the
                    # current mask/frame and the previous measured surface.
                    fact["claimed_association"] = _association_summary(self._seed_association[key])
                if jump > self.max_identity_jump:
                    # Only CURRENT coherent RGB-D motion may explain a large jump;
                    # otherwise the original strict rejection applies.
                    motion = measured_motion_association(old, frame,
                        [{"mask": exclusive[key], "visible_center": center.tolist()}],
                        gate=self.max_identity_jump, max_age=self.cadence.object_max_age)
                    fact["identity_association"] = {"association": "measured_motion", **motion}
                    if motion["status"] != "verified":
                        fact.update(status="unknown", value=None,
                                    reason="part identity jump; explicit public reacquisition required "
                                           f"({motion['reason']})")
                        self._request_review("identity_conflict", key, fact["evidence_ids"])
            if fact["status"] == "known":
                self._last_good[key] = {"center": np.asarray(fact["value"]["visible_center"]),
                                       "at": frame.captured_at, "frame": frame,
                                       "mask": np.asarray(exclusive[key], dtype=bool).copy()}
                self._evidence.update(fact["evidence_ids"])

    def _request_review(self, reason, identity, evidence_ids):
        key = (reason, identity, self.structure_revision)
        if any((x["reason"], x["identity"], x["structure_revision"]) == key for x in self.review_requests):
            return
        self.review_requests.append({"reason": reason, "identity": identity,
                                     "structure_revision": self.structure_revision,
                                     "evidence_ids": list(evidence_ids), "status": "pending"})

    def revise_structure(self, frame, masks, parts, *, reason, evidence_ids=(), lineage=None):
        """Atomic graph commit after successful same-frame tracker reinitialization."""
        with self._lock:
            self._validate_frame(frame)
            if not isinstance(reason, str) or not reason.strip():
                raise ValueError("structure revision requires an explicit reason")
            if self.parts and (not evidence_ids or not set(evidence_ids) <= self._evidence):
                raise ValueError("structure revision must cite recorded public visual evidence")
            nodes = validate_parts(parts, set(masks))
            if any(key in self._used_ids and key not in self.parts for key in nodes):
                raise ValueError("retired part IDs cannot be silently reused")
            lineage = _finite(lineage or {})
            for old, children in lineage.items():
                if old not in self.parts or not isinstance(children, list) or not children or any(c not in nodes for c in children):
                    raise ValueError("lineage must connect existing parts to nodes in the new structure")
            for mask in masks.values():
                value = np.asarray(mask)
                if value.shape != frame.depth.shape or not np.isin(value, [0, 1]).all() or not value.any():
                    raise FrameError("revision requires nonempty same-frame binary masks")
            old_ids, before = self._identities, copy.deepcopy(self.parts)
            self._candidate_parts = nodes
            try:
                self.tracker.reset()
                self._identities = tuple(masks)
                self.seed(frame, masks)
            except Exception:
                self._identities = old_ids
                self._objects = {key: self._fact(None, frame_id=frame.frame_id, at=frame.captured_at,
                    identity=key, reason="structure tracker reinitialization failed") for key in old_ids}
                self._seeded = False
                raise
            finally:
                self._candidate_parts = None
            self.parts = nodes
            self._used_ids.update(nodes)
            self._last_good = {k: v for k, v in self._last_good.items() if k in masks}
            self.structure_revision += 1
            self.structure_history.append({"revision": self.structure_revision, "before": before,
                "after": copy.deepcopy(nodes), "reason": reason, "evidence_ids": list(evidence_ids),
                "frame_id": frame.frame_id, "captured_at": frame.captured_at, "lineage": lineage})
            for pending in self._pending.values():
                self.interactions.append({"id": pending["id"], "status": "unresolved",
                                          "reason": "structure revised before interaction verification"})
            self._pending.clear()
            for request in self.review_requests:
                request["status"] = "superseded_by_revision"
            return self.structure_revision

    def begin_interaction(self, identity, expected_delta, *, at, reference=None, tolerance=.015, hypothesis=None):
        """Predict measured relative visible-surface displacement, not hidden joint state."""
        with self._lock:
            delta = np.asarray(expected_delta, dtype=float)
            if delta.shape != (3,) or not np.isfinite(delta).all() or isinstance(tolerance, bool) or not math.isfinite(tolerance) or tolerance < 0:
                raise ValueError("expected_delta is finite XYZ; tolerance is finite and nonnegative")
            if identity not in self.parts or (reference is not None and reference not in self.parts):
                raise ValueError("interaction parts must be in the current structure")
            if hypothesis is not None and hypothesis not in self.parts[identity]["hypotheses"]:
                raise ValueError("interaction hypothesis must name an existing motion candidate")
            ids = [identity] + ([reference] if reference is not None else [])
            before = {k: self.query(k, at=at) for k in ids}
            if any(f["status"] != "known" for f in before.values()):
                raise ValueError("interaction requires fresh measured part geometry")
            key = f"interaction-{len(self.interactions) + len(self._pending) + 1}"
            self._pending[key] = {"id": key, "identity": identity, "reference": reference,
                "before": before, "at": at, "expected_delta": delta.tolist(), "tolerance": tolerance,
                "structure_revision": self.structure_revision, "hypothesis": hypothesis}
            return key

    def finish_interaction(self, key, report, *, at):
        """Verify execution first; contradictions request review, never auto-relabel parts."""
        with self._lock:
            pending = self._pending.pop(key)
            report = _finite(report)
            row = {k: copy.deepcopy(v) for k, v in pending.items() if k != "before"}
            row.update(finished_at=at, action_report=report, status="unknown", diagnosis="observation_missing")
            after = {k: self.query(k, at=at) for k in pending["before"]}
            fresh = all(f["status"] == "known" and f["frame_id"] > pending["before"][k]["frame_id"]
                        and f["captured_at"] > pending["at"] for k, f in after.items())
            action_at = report.get("captured_at")
            action_fresh = (isinstance(action_at, (int, float)) and not isinstance(action_at, bool)
                            and math.isfinite(action_at) and pending["at"] < action_at <= at)
            if report.get("reached") is not True or report.get("target_modified") is not False or not action_fresh:
                row.update(status="unresolved", diagnosis="execution_unverified")
            elif fresh:
                # Contact itself remains unproven from gripper closure. Require
                # the policy's separately verified public relation and its IDs.
                contact = self.query(report.get("contact_relation", ""), at=at, layer="relation")
                if contact.get("status") != "known" or contact.get("value") is not True or pending["identity"] not in contact.get("dependencies", []):
                    row.update(status="unresolved", diagnosis="contact_unverified")
                else:
                    identity, reference = pending["identity"], pending["reference"]
                    delta = np.asarray(after[identity]["value"]["visible_center"]) - pending["before"][identity]["value"]["visible_center"]
                    if reference is not None:
                        delta -= np.asarray(after[reference]["value"]["visible_center"]) - pending["before"][reference]["value"]["visible_center"]
                    residual = float(np.linalg.norm(delta - pending["expected_delta"]))
                    row.update(status="match" if residual <= pending["tolerance"] else "mismatch",
                        diagnosis="effect_verified" if residual <= pending["tolerance"] else "effect_contradiction",
                        measured_delta=delta.tolist(), residual=residual,
                        evidence_ids=sorted({i for f in [*after.values(), *pending["before"].values(), contact] for i in f["evidence_ids"]}))
                    if row["status"] == "mismatch":
                        self._request_review("effect_contradiction", identity, row["evidence_ids"])
                    candidate = pending["hypothesis"]
                    if candidate is not None:
                        support = self.parts[identity]["hypotheses"][candidate]
                        updated = support * .5 if row["status"] == "mismatch" else support + .1*(1-support)
                        self.parts[identity]["hypotheses"][candidate] = updated
                        self.belief_revision += 1
                        row["hypothesis_update"] = {"candidate": candidate, "before": support,
                            "after": updated, "meaning": "heuristic evidence support, not calibrated probability"}
            self.interactions.append(row)
            return copy.deepcopy(row)

    def query(self, name, *, at, layer="observed"):
        if layer == "hypothesis":
            self._time(at)
            with self._lock:
                self._open()
                return {"status": "hypothesis" if name in self.parts else "unknown",
                        "value": copy.deepcopy(self.parts.get(name)), "layer": layer,
                        "structure_revision": self.structure_revision}
        return super().query(name, at=at, layer=layer)

    def snapshot(self, *, at):
        with self._lock:
            result = super().snapshot(at=at)
            result.update(semantic={"structure_revision": self.structure_revision, "belief_revision": self.belief_revision,
                "parts": copy.deepcopy(self.parts), "revisions": copy.deepcopy(self.structure_history),
                "interaction_checks": copy.deepcopy(self.interactions),
                "pending_reviews": copy.deepcopy(self.review_requests)})
            return _finite(result)
