"""Checks based only on camera masks and robot end-effector poses."""

import numpy as np


def mask_view_quality(mask):
    """Describe whether a nonempty 2-D mask is complete and centrally visible."""
    mask = np.asarray(mask, dtype=bool)
    if mask.ndim != 2 or not mask.any():
        return {"complete": False, "centered": False, "bbox": None, "pixels": 0}
    ys, xs = np.nonzero(mask)
    h, w = mask.shape
    bbox = [int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())]
    # A border-touching mask cannot establish that the entire object is visible.
    complete = bbox[0] > 0 and bbox[1] > 0 and bbox[2] < w - 1 and bbox[3] < h - 1
    cx, cy = (bbox[0] + bbox[2]) / (2 * w), (bbox[1] + bbox[3]) / (2 * h)
    centered = 0.1 <= cx <= 0.9 and 0.1 <= cy <= 0.9
    return {"complete": complete, "centered": centered, "bbox": bbox,
            "pixels": int(mask.sum()), "center_fraction": [cx, cy]}


def eef_pose_error(target_pose, actual_pose):
    """Return translation (m) and quaternion geodesic error (rad), XYZW inputs."""
    target_pos, target_quat = (np.asarray(x, dtype=float) for x in target_pose)
    actual_pos, actual_quat = (np.asarray(x, dtype=float) for x in actual_pose)
    if (target_pos.shape != (3,) or actual_pos.shape != (3,)
            or target_quat.shape != (4,) or actual_quat.shape != (4,)
            or not all(np.isfinite(x).all() for x in [target_pos, actual_pos, target_quat, actual_quat])
            or np.linalg.norm(target_quat) == 0 or np.linalg.norm(actual_quat) == 0):
        return float("inf"), float("inf")
    dot = abs(float(np.dot(target_quat / np.linalg.norm(target_quat),
                           actual_quat / np.linalg.norm(actual_quat))))
    return float(np.linalg.norm(target_pos - actual_pos)), float(2 * np.arccos(np.clip(dot, 0, 1)))
