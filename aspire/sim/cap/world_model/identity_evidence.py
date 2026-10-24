# SPDX-License-Identifier: Apache-2.0
"""Public candidate handling and measured-motion identity association.

Only current SAM3 masks, RGB-D frames and forward/backward-checked texture
correspondences are used. Segmentation scores, predictions, solver/policy
assertions and robot commands never authorize an observed identity. Missing,
ambiguous or unverifiable evidence is reported with structured reasons and
stays unknown; nothing here chooses a fallback instance.
"""
from __future__ import annotations

import numpy as np

from .World import FrameError
from .rigid_tracking import flow_correspondences, rigid_registration
from .robot_frames import FRAMES

# A mask that overlaps an earlier candidate this much is the same image region
# reported twice (e.g. two SAM3 queries/heads), not a second instance. Equal to
# the existing Cadence.max_mask_iou ambiguity heuristic.
DUPLICATE_IOU = .85
# Fraction of coherent flowed features that must land in the associated mask;
# equal to rigid_registration's inlier fraction.
MIN_COHERENCE = .7
MIN_MATCHES = 8


class SeedSelectionError(FrameError):
    """Typed public seeding failure: kind is missing|ambiguous|rank_unavailable|identity_conflict."""

    KINDS = ("missing", "ambiguous", "rank_unavailable", "identity_conflict")

    def __init__(self, kind, message, evidence):
        if kind not in self.KINDS:
            raise ValueError(f"unregistered seed failure kind {kind!r}")
        self.kind, self.evidence = kind, {"kind": kind, **evidence}
        super().__init__(message)


def _iou(a, b):
    union = np.logical_or(a, b).sum()
    return float(np.logical_and(a, b).sum() / union) if union else 0.


def deduplicate(candidates, *, iou=DUPLICATE_IOU):
    """Merge near-identical candidate masks deterministically.

    Candidates are visited by descending score, then original index; a mask
    whose IoU with an already kept mask is >= iou is merged into it. Returns
    (kept candidates in original order, merged-count). Duplicates never add
    an identity or shift a rank.
    """
    order = sorted(range(len(candidates)), key=lambda i: (-float(candidates[i].get("score", 0)), i))
    kept, merged = [], 0
    for index in order:
        mask = np.asarray(candidates[index]["mask"], dtype=bool)
        if any(_iou(mask, np.asarray(candidates[k]["mask"], dtype=bool)) >= iou for k in kept):
            merged += 1
            continue
        kept.append(index)
    return [candidates[i] for i in sorted(kept)], merged


def measured_motion_association(previous, frame, candidates, *, gate, max_age):
    """Associate a moved instance using CURRENT RGB-D correspondences.

    previous: {"frame", "mask", "center", "at"} of the identity's last accepted
    measured surface. candidates: list of {"mask", "visible_center"} in frame.
    A candidate is verified only if (1) the previous evidence is fresh
    (0 < age <= max_age) and from the same episode/camera/clock and the same
    DECLARED supported coordinate frame (undeclared frames never qualify),
    (2) >= MIN_MATCHES forward/backward-consistent correspondences exist,
    (3) >= MIN_COHERENCE of them land inside the candidate's current mask,
    (4) robust rigid registration of those correspondences succeeds, and
    (5) the registered motion carries the previous visible centre to the
    candidate's visible centre within the ORIGINAL identity gate. Exactly
    one candidate may pass; otherwise the association is unverified.
    """
    result = {"status": "unverified", "candidate": None, "candidates": [], "gate": gate}
    if not previous or previous.get("frame") is None or previous.get("mask") is None:
        return {**result, "reason": "no previous measured surface for correspondence"}
    old = previous["frame"]
    age = frame.captured_at - previous["at"]
    result.update(previous_frame_id=old.frame_id, age=age)
    if old.coordinate_frame not in FRAMES or frame.coordinate_frame not in FRAMES:
        # Undeclared (None) or unsupported calibration labels cannot support a
        # measured cross-frame identity, even when both sides are undeclared.
        return {**result, "reason": "camera pose frame is undeclared or unsupported"}
    if (old.episode_id, old.camera, old.clock, old.coordinate_frame) != (
            frame.episode_id, frame.camera, frame.clock, frame.coordinate_frame):
        return {**result, "reason": "previous evidence belongs to an incompatible frame/stream"}
    if not (0 < age <= max_age) or frame.frame_id <= old.frame_id:
        return {**result, "reason": "previous measured surface is stale or not earlier than this frame"}
    flow = flow_correspondences(old, previous["mask"], frame)
    if flow is None or int(flow["valid"].sum()) < MIN_MATCHES:
        return {**result, "reason": "insufficient consistent RGB-D correspondences"}
    valid = flow["valid"]
    total = int(valid.sum())
    result["correspondences"] = total
    centre = np.asarray(previous["center"], dtype=float)
    passing = []
    for index, candidate in enumerate(candidates):
        inside = valid & flow["inside"](candidate["mask"])
        row = {"index": index, "inside": int(inside.sum()), "coherence": float(inside.sum() / total)}
        registration = None
        if row["inside"] >= MIN_MATCHES:
            registration = rigid_registration(flow["points0"][inside], flow["points1"][inside])
        if registration is not None:
            T = registration["transform"]
            moved = T[:3, :3] @ centre + T[:3, 3]
            row.update(inliers=registration["inliers"], rmse=registration["rmse"],
                       motion_translation=float(np.linalg.norm(moved - centre)),
                       residual=float(np.linalg.norm(moved - np.asarray(candidate["visible_center"]))))
        row["verified"] = bool(registration is not None and row["coherence"] >= MIN_COHERENCE
                               and row["residual"] <= gate)
        if row["verified"]:
            passing.append(index)
        result["candidates"].append(row)
    if len(passing) == 1:
        return {**result, "status": "verified", "candidate": passing[0],
                "reason": "coherent RGB-D correspondences register the previous surface onto this mask"}
    if passing:
        return {**result, "status": "ambiguous", "reason": "measured motion supports more than one candidate"}
    return {**result, "reason": "no candidate is explained by coherent measured motion"}


def valid_alternative_prompts(value, prompt):
    """Validate an author-declared ordered list of alternative public prompts."""
    if value is None:
        return []
    if (not isinstance(value, list) or not value
            or any(not isinstance(p, str) or not p.strip() for p in value)
            or len(set(value)) != len(value) or prompt in value):
        raise ValueError("alternative_prompts must be a nonempty list of distinct nonempty "
                         "public prompts different from prompt")
    return list(value)
