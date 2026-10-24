# SPDX-License-Identifier: Apache-2.0
"""Pure geometry of currently visible public points (no simulator state).

Inputs are back-projected RGB-D points of one tracked mask in one declared
frame. Outputs describe the VISIBLE surface only: principal axes and visible
spans, dominant planes, upward-facing support candidates and the top slab.
`box_axes` interprets an oriented box's rotation by COLUMNS (Open3D convention).
Hidden geometry, physical extent, contact and task success are unknown here;
geometric proximity between features is never contact or oracle success.
Thresholds are declared heuristics, not calibrated probabilities.
"""
from __future__ import annotations

import numpy as np

MIN_POINTS = 30            # fewer visible points -> unknown
PLANE_THRESHOLD = .004     # m, RANSAC inlier distance
PLANE_MIN_FRACTION = .15   # of the analysed points, per plane
MAX_PLANES = 3
RANSAC_TRIALS = 128
LONG_AXIS_RATIO = 1.3      # std ratio first/second axis below which no long axis is reported
UPWARD_TILT = np.deg2rad(20)
TOP_SLAB = .01             # m below the highest visible point
CAVEATS = ("visible surface only; occluded geometry and physical extents are unknown",
           "geometric proximity of features is not contact, support or task success")


def mask_points(frame, mask):
    """Back-project one binary mask of a PublicFrame into its declared frame."""
    mask = np.asarray(mask, dtype=bool)
    if mask.shape != frame.depth.shape:
        raise ValueError("mask does not belong to this RGB-D frame")
    ys, xs = np.where(mask & np.isfinite(frame.depth) & (frame.depth > 0))
    pixels = np.stack([xs, ys, np.ones(len(xs))], axis=0)
    points = (np.linalg.inv(frame.intrinsics) @ pixels).T * frame.depth[ys, xs, None]
    return points @ frame.camera_to_world[:3, :3].T + frame.camera_to_world[:3, 3]


def _canonical(axis):
    axis = np.asarray(axis, dtype=float)
    return axis if axis[int(np.argmax(np.abs(axis)))] >= 0 else -axis


def principal_axes(points):
    """PCA of visible points: unit axes (rows), visible spans and std per axis."""
    centred = points - points.mean(0)
    _, s, vt = np.linalg.svd(centred, full_matrices=False)
    std = s / np.sqrt(max(len(points) - 1, 1))
    axes = np.array([_canonical(v) for v in vt])
    projected = centred @ axes.T
    spans = projected.max(0) - projected.min(0)
    result = {"axes": axes.tolist(), "visible_spans": spans.tolist(), "std": std.tolist(),
              "long_axis": None, "long_axis_status": "unknown", "long_axis_visible_span": None}
    if std[0] < 1e-6:
        result["reason"] = "visible points are coincident"
        return result
    ratio = float(std[0] / max(std[1], 1e-9))
    result["elongation_ratio"] = ratio
    if ratio >= LONG_AXIS_RATIO:
        result.update(long_axis=axes[0].tolist(), long_axis_status="known",
                      long_axis_visible_span=float(spans[0]),
                      reason="dominant visible spread; axis sign is arbitrary")
    else:
        result.update(long_axis_status="ambiguous",
                      reason=f"first/second std ratio {ratio:.2f} < {LONG_AXIS_RATIO}")
    return result


def axis_tilt_from_up(axis, up=(0., 0., 1.)):
    """Angle in [0, pi/2] between an unsigned axis line and gravity-up.

    0 means the axis is vertical, pi/2 that it is horizontal. The axis sign is
    irrelevant. Classified with the same UPWARD_TILT band as plane facing.
    """
    axis, up = np.asarray(axis, dtype=float), np.asarray(up, dtype=float)
    cosine = abs(float(axis @ up)) / max(np.linalg.norm(axis) * np.linalg.norm(up), 1e-12)
    tilt = float(np.arccos(np.clip(cosine, 0, 1)))
    label = "vertical" if tilt <= UPWARD_TILT else "horizontal" if tilt >= np.pi / 2 - UPWARD_TILT else "oblique"
    return tilt, label


def box_axes(rotation, extent, *, up=(0., 0., 1.), long_axis_ratio=LONG_AXIS_RATIO):
    """Interpret an oriented box whose COLUMNS are its axes (Open3D OBB convention).

    ``rotation[:, i]`` is box axis i and ``extent[i]`` the full box length along
    it, both in the frame of the points the box was fitted to; ``up`` is
    gravity-up in that frame. Rows of ``rotation`` are NOT box axes: reading
    ``rotation[0]`` as the long axis mixes the components of all three axes.
    The long axis is reported only when the largest extent exceeds the second
    by ``long_axis_ratio``; near-equal extents are ``ambiguous`` and the axis is
    None. A box fitted to visible points describes the visible surface only.
    """
    rotation, extent = np.asarray(rotation, dtype=float), np.asarray(extent, dtype=float).reshape(-1)
    up = np.asarray(up, dtype=float)
    result = {"axis_convention": "axes[i] == R[:, i] (columns of R); extent[i] is the full length along axes[i]; "
                                 "axis sign is arbitrary",
              "axes": None, "up": up.tolist(), "long_axis": None, "long_axis_index": None,
              "long_axis_status": "unknown", "long_axis_extent": None, "elongation_ratio": None,
              "long_axis_tilt_from_up": None, "long_axis_orientation": None, "caveats": list(CAVEATS)}
    if (rotation.shape != (3, 3) or extent.shape != (3,) or up.shape != (3,)
            or not (np.isfinite(rotation).all() and np.isfinite(extent).all() and np.isfinite(up).all())
            or np.linalg.norm(up) < 1e-9):
        result["reason"] = "box needs a finite 3x3 rotation, three extents and a nonzero up vector"
        return result
    if not np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-5):
        result["reason"] = "rotation is not orthonormal; no box axes are reported"
        return result
    result["axes"] = rotation.T.tolist()  # rows of this list are the box axes
    order = np.argsort(extent)[::-1]
    first, second = float(extent[order[0]]), float(extent[order[1]])
    if first < 1e-6:
        result["reason"] = "box has no extent"
        return result
    ratio = first / max(second, 1e-9)
    result["elongation_ratio"] = ratio
    if ratio < long_axis_ratio:
        result.update(long_axis_status="ambiguous",
                      reason=f"largest/second extent ratio {ratio:.2f} < {long_axis_ratio}; "
                             "no long axis or lying/upright orientation is implied")
        return result
    index = int(order[0])
    axis = _canonical(rotation[:, index])
    tilt, label = axis_tilt_from_up(axis, up)
    result.update(long_axis=axis.tolist(), long_axis_index=index, long_axis_status="known",
                  long_axis_extent=first, long_axis_tilt_from_up=tilt, long_axis_orientation=label,
                  reason="largest extent of the fitted box; column R[:, long_axis_index]")
    return result


def _fit_plane(points):
    centroid = points.mean(0)
    _, s, vt = np.linalg.svd(points - centroid, full_matrices=False)
    return centroid, _canonical(vt[2]), s


def _planes(points, up, *, threshold, min_count):
    rng = np.random.default_rng(0)
    remaining = np.arange(len(points))
    planes = []
    while len(planes) < MAX_PLANES and len(remaining) >= max(min_count, 3):
        best = None
        pts = points[remaining]
        for _ in range(RANSAC_TRIALS):
            sample = pts[rng.choice(len(pts), 3, replace=False)]
            normal = np.cross(sample[1] - sample[0], sample[2] - sample[0])
            if np.linalg.norm(normal) < 1e-9:
                continue  # collinear sample
            normal /= np.linalg.norm(normal)
            keep = np.abs((pts - sample[0]) @ normal) <= threshold
            if best is None or keep.sum() > best.sum():
                best = keep
        if best is None or best.sum() < min_count:
            break
        inliers = pts[best]
        centroid, normal, s = _fit_plane(inliers)
        if s[1] < 1e-4:
            break  # inliers are collinear: no plane is defined
        if np.dot(normal, up) < 0:
            normal = -normal
        residual = (inliers - centroid) @ normal
        in_plane = np.linalg.svd(inliers - centroid, full_matrices=False)[2][:2]
        projected = (inliers - centroid) @ in_plane.T
        spans = projected.max(0) - projected.min(0)
        tilt = float(np.arccos(np.clip(abs(np.dot(normal, up)), 0, 1)))
        planes.append({"normal": normal.tolist(), "centroid": centroid.tolist(),
                       "inlier_count": int(best.sum()), "inlier_fraction": float(best.sum() / len(points)),
                       "rms": float(np.sqrt(np.mean(residual ** 2))),
                       "in_plane_axes": in_plane.tolist(), "visible_spans": spans.tolist(),
                       "tilt_from_up": tilt,
                       "facing": "up" if tilt <= UPWARD_TILT else "side" if tilt >= np.pi / 2 - UPWARD_TILT else "oblique",
                       "height_along_up": float(np.median(inliers @ up))})
        remaining = remaining[~best]
    return planes


def visible_geometry_summary(points, *, up=(0., 0., 1.), near=None, radius=None,
                             min_points=MIN_POINTS, plane_threshold=PLANE_THRESHOLD):
    """Describe visible public points; insufficient evidence returns status unknown.

    ``up`` is the gravity-up direction expressed in the points' frame (derive
    it from public mounting calibration when needed). ``near``/``radius``
    restrict the analysis to a local candidate region around a point.
    """
    up = np.asarray(up, dtype=float)
    if up.shape != (3,) or not np.isfinite(up).all() or np.linalg.norm(up) < 1e-9:
        raise ValueError("up must be one finite nonzero XYZ vector")
    up = up / np.linalg.norm(up)
    raw = np.asarray(points, dtype=float).reshape(-1, 3) if np.size(points) else np.zeros((0, 3))
    finite = np.isfinite(raw).all(axis=1)
    pts = raw[finite]
    result = {"status": "unknown", "units": "m, rad", "up": up.tolist(), "input_points": int(len(raw)),
              "nonfinite_dropped": int((~finite).sum()), "caveats": list(CAVEATS)}
    if near is not None or radius is not None:
        near = np.asarray(near, dtype=float)
        if (near.shape != (3,) or not np.isfinite(near).all() or isinstance(radius, bool)
                or not isinstance(radius, (int, float)) or not np.isfinite(radius) or radius <= 0):
            raise ValueError("a local region needs one finite XYZ point and a positive radius")
        pts = pts[np.linalg.norm(pts - near, axis=1) <= radius]
        result["region"] = {"near": near.tolist(), "radius": float(radius)}
    result["point_count"] = int(len(pts))
    if len(pts) < min_points:
        result["reason"] = f"{len(pts)} visible points < {min_points}"
        return result
    axes = principal_axes(pts)
    if axes["long_axis_status"] == "unknown":
        result.update(reason=axes["reason"], principal_axes=axes)
        return result
    planes = _planes(pts, up, threshold=plane_threshold,
                     min_count=max(min_points, int(np.ceil(PLANE_MIN_FRACTION * len(pts)))))
    heights = pts @ up
    top = heights.max()
    slab = pts[heights >= top - TOP_SLAB]
    support = [{"source": "plane", "height_along_up": p["height_along_up"], "centroid": p["centroid"],
                "normal": p["normal"], "visible_spans": p["visible_spans"], "in_plane_axes": p["in_plane_axes"],
                "inlier_count": p["inlier_count"], "rms": p["rms"],
                "meaning": "visible upward-facing planar region; candidate, not verified support"}
               for p in planes if p["facing"] == "up"]
    result.update(status="known", reason="visible-surface geometry from current public points",
                  centroid=pts.mean(0).tolist(), median=np.median(pts, axis=0).tolist(),
                  bounds=[pts.min(0).tolist(), pts.max(0).tolist()], principal_axes=axes,
                  planes=planes, support_candidates=support,
                  top_slab={"height_along_up": float(top), "thickness": TOP_SLAB, "point_count": int(len(slab)),
                            "center": slab.mean(0).tolist(),
                            "bounds": [slab.min(0).tolist(), slab.max(0).tolist()]},
                  thresholds={"min_points": min_points, "plane_threshold": plane_threshold,
                              "plane_min_fraction": PLANE_MIN_FRACTION,
                              "long_axis_ratio": LONG_AXIS_RATIO, "upward_tilt": float(UPWARD_TILT)})
    if not planes:
        result["planes_reason"] = "no planar region with enough inliers"
    return result
