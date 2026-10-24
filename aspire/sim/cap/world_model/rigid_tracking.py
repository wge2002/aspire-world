# SPDX-License-Identifier: Apache-2.0
"""Public RGB-D correspondences, never mask-centroid pose estimation.

Feature tracking uses forward/backward optical-flow checks within both SAM2
masks. Robust rigid registration rejects insufficient/collinear correspondences
and depth disagreement. Missing evidence breaks the accumulated pose epoch.
`registration_uncertainty` reports how well the inlier geometry constrains
each rotation axis and the translation (a first-order least-squares model with
a declared noise floor, not a calibrated probability).
"""
from __future__ import annotations

import numpy as np

NOISE_FLOOR = .002  # m, per-correspondence 1-sigma floor (depth/flow quantization heuristic)


def registration_uncertainty(points, rmse, *, noise_floor=NOISE_FLOOR):
    """First-order 1-sigma uncertainty of a rigid fit to these inlier points.

    For residual r = w x q + t (q centred), the rotation information is
    H = sum(|q|^2 I - q q^T) / sigma^2. Rotation about an axis along which the
    points are (nearly) collinear has tiny information and huge std: it is not
    observable from these correspondences. Returns rotation covariance (3x3,
    rad^2, in the points' frame), translation covariance at the centroid (m^2),
    the per-axis rotation std and the centroid.
    """
    q = np.asarray(points, dtype=float)
    sigma = max(float(rmse), noise_floor)
    centroid = q.mean(0)
    q = q - centroid
    H = (q * q).sum() * np.eye(3) - q.T @ q
    values, vectors = np.linalg.eigh(H)
    values = np.maximum(values, 1e-12)
    rotation_cov = sigma ** 2 * (vectors / values) @ vectors.T
    return {"rotation_cov": rotation_cov, "translation_cov": sigma ** 2 / len(q) * np.eye(3),
            "rotation_axes": vectors.T, "rotation_std": sigma / np.sqrt(values),
            "centroid": centroid, "sigma": sigma, "count": int(len(q))}


def rigid_registration(before, after, *, threshold=.006, trials=64):
    a, b = np.asarray(before, dtype=float), np.asarray(after, dtype=float)
    if a.shape != b.shape or a.ndim != 2 or a.shape[1] != 3 or len(a) < 8 or not np.isfinite([a, b]).all():
        return None

    def solve(x, y):
        if np.linalg.svd(x-x.mean(0), compute_uv=False)[1] < 1e-4:
            return None
        u, _, vt = np.linalg.svd((x-x.mean(0)).T @ (y-y.mean(0)))
        r = vt.T @ np.diag([1, 1, np.linalg.det(vt.T @ u.T)]) @ u.T
        t = np.eye(4)
        t[:3, :3], t[:3, 3] = r, y.mean(0)-r @ x.mean(0)
        return t

    rng, best = np.random.default_rng(0), None
    for _ in range(trials):
        ids = rng.choice(len(a), 3, replace=False)
        t = solve(a[ids], b[ids])
        if t is None:
            continue
        errors = np.linalg.norm(a @ t[:3, :3].T+t[:3, 3]-b, axis=1)
        keep = errors <= threshold
        if best is None or keep.sum() > best.sum():
            best = keep
    if best is None or best.sum() < max(8, int(.7*len(a))):
        return None
    t = solve(a[best], b[best])
    if t is None:
        return None
    error = np.linalg.norm(a[best] @ t[:3, :3].T+t[:3, 3]-b[best], axis=1)
    rmse = float(np.sqrt(np.mean(error**2)))
    return {"transform": t, "inliers": int(best.sum()), "matches": len(a), "rmse": rmse,
            "uncertainty": registration_uncertainty(b[best], rmse)}


def _points(frame, xy):
    rounded = np.rint(xy).astype(int)
    xs, ys = rounded[:, 0], rounded[:, 1]
    good = (xs >= 0) & (ys >= 0) & (xs < frame.depth.shape[1]) & (ys < frame.depth.shape[0])
    z = np.zeros(len(xy))
    z[good] = frame.depth[ys[good], xs[good]]
    good &= np.isfinite(z) & (z > 0)
    rays = np.c_[xy, np.ones(len(xy))] @ np.linalg.inv(frame.intrinsics).T
    xyz = rays*z[:, None]
    xyz = xyz @ frame.camera_to_world[:3, :3].T + frame.camera_to_world[:3, 3]
    return xyz, good


def _erode(mask, margin):
    mask = np.asarray(mask, dtype=bool)
    if margin <= 0:
        return mask
    import cv2
    kernel = np.ones((2 * margin + 1, 2 * margin + 1), np.uint8)
    return cv2.erode(mask.astype(np.uint8), kernel, borderType=cv2.BORDER_CONSTANT, borderValue=0).astype(bool)


def flow_correspondences(previous, old_mask, frame, *, edge_margin=0):
    """Forward/backward-checked texture correspondences from one mask to a later frame.

    Returns None without enough trackable features; otherwise per-feature
    3D points in both frames, the coherent (flow + depth) flag and a function
    testing which flowed features land inside a candidate current mask.
    ``edge_margin`` > 0 (pixels) restricts features to the mask interior and
    ``inside(mask)`` to the eroded candidate mask: features on an occluding
    contour slide along the silhouette as the view changes and are not the
    same physical surface point. The default 0 keeps the historical behaviour.
    """
    import cv2
    old_gray = cv2.cvtColor(previous.rgb, cv2.COLOR_RGB2GRAY)
    gray = cv2.cvtColor(frame.rgb, cv2.COLOR_RGB2GRAY)
    features = cv2.goodFeaturesToTrack(old_gray, maxCorners=200, qualityLevel=.01,
                                       minDistance=3, mask=_erode(old_mask, edge_margin).astype(np.uint8)*255)
    if features is None or len(features) < 8:
        return None
    moved, ok, _ = cv2.calcOpticalFlowPyrLK(old_gray, gray, features, None)
    if moved is None:
        return None
    back, back_ok, _ = cv2.calcOpticalFlowPyrLK(gray, old_gray, moved, None)
    if back is None:
        return None
    xy0, xy1 = features[:, 0], moved[:, 0]
    points0, good0 = _points(previous, xy0)
    points1, good1 = _points(frame, xy1)
    valid = good0 & good1 & (ok[:, 0] == 1) & (back_ok[:, 0] == 1)
    valid &= np.linalg.norm(features[:, 0]-back[:, 0], axis=1) < .7
    loc = np.rint(xy1).astype(int)

    def inside(mask):
        result = np.zeros(len(loc), dtype=bool)
        result[good1] = _erode(mask, edge_margin)[loc[good1, 1], loc[good1, 0]]
        return result
    return {"points0": points0, "points1": points1, "valid": valid, "inside": inside,
            "features": len(features)}


class PublicRigidTracker:
    def __init__(self):
        self.reset()

    def reset(self):
        self.previous, self.masks, self.poses, self.epochs = None, {}, {}, {}

    def reanchor(self, identities):
        """Start a new motion epoch for identities after an explicit reseed."""
        for identity in identities:
            self.masks.pop(identity, None)
            self.poses.pop(identity, None)

    def update(self, frame, masks):
        results, next_poses = {}, {}
        for identity, mask in masks.items():
            old_mask = self.masks.get(identity)
            registration = None
            if (mask is not None and np.asarray(mask).any() and old_mask is not None
                    and identity in self.poses and self.previous is not None):
                flow = flow_correspondences(self.previous, old_mask, frame)
                if flow is not None:
                    valid = flow["valid"] & flow["inside"](mask)
                    registration = rigid_registration(flow["points0"][valid], flow["points1"][valid])
            epoch = self.epochs.get(identity, 0)
            if registration is not None:
                pose = registration["transform"] @ self.poses[identity]
                next_poses[identity] = pose
                results[identity] = {"status": "estimated", "motion_transform": pose.tolist(),
                    "epoch": epoch, "inliers": registration["inliers"], "rmse": registration["rmse"]}
            elif mask is not None and np.asarray(mask).any():
                # Reanchor; this frame alone supplies no articulated motion.
                epoch += 1
                self.epochs[identity] = epoch
                next_poses[identity] = np.eye(4)
                results[identity] = {"status": "reference_only", "epoch": epoch,
                    "reason": "new reference or insufficient reliable RGB-D correspondences"}
            else:
                results[identity] = {"status": "unknown", "epoch": epoch,
                    "reason": "tracked part unavailable"}
            results[identity].update(frame_id=frame.frame_id, captured_at=frame.captured_at)
        self.previous, self.masks, self.poses = frame, {k: None if m is None else np.asarray(m).copy() for k, m in masks.items()}, next_poses
        return results
