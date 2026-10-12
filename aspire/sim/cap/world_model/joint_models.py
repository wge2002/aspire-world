# SPDX-License-Identifier: Apache-2.0
"""Fixed kinematic templates fitted to public, parent-relative rigid motion.

The reference is the parent's initial observed geometry frame, not a hidden
asset/body frame. Fits are hypotheses; a stationary sequence cannot establish
a fixed joint or its limits. The last sample validates a fit to earlier samples.
"""
from __future__ import annotations

import math
import numpy as np

FAMILIES = ("fixed", "sliding", "rotating", "free", "unknown")


def transform(value):
    t = np.asarray(value, dtype=float)
    if (t.shape != (4, 4) or not np.isfinite(t).all()
            or not np.allclose(t[3], [0, 0, 0, 1])
            or not np.allclose(t[:3, :3].T @ t[:3, :3], np.eye(3), atol=1e-5)
            or not np.isclose(np.linalg.det(t[:3, :3]), 1, atol=1e-5)):
        raise ValueError("a finite rigid 4x4 transform is required")
    return t


def rotation(axis, angle):
    a = np.asarray(axis, dtype=float)
    if a.shape != (3,) or not np.isfinite(a).all() or np.linalg.norm(a) < 1e-10:
        raise ValueError("finite nonzero rotation axis required")
    if isinstance(angle, bool) or not math.isfinite(angle):
        raise ValueError("finite angle required")
    a = a / np.linalg.norm(a)
    x, y, z = a
    k = np.array([[0, -z, y], [z, 0, -x], [-y, x, 0]])
    return np.eye(3) + math.sin(angle)*k + (1-math.cos(angle))*(k @ k)


def rotvec(r):
    # Eigenvector remains defined near pi, unlike dividing by sin(angle).
    angle = math.acos(float(np.clip((np.trace(r)-1)/2, -1, 1)))
    if angle < 1e-7:
        return np.zeros(3)
    vals, vecs = np.linalg.eig(r)
    axis = np.real(vecs[:, np.argmin(np.abs(vals-1))])
    axis /= np.linalg.norm(axis)
    skew = np.array([r[2, 1]-r[1, 2], r[0, 2]-r[2, 0], r[1, 0]-r[0, 1]])
    if np.dot(axis, skew) < 0:
        axis = -axis
    return axis * angle


def evaluate(model, q):
    """Relative motion from the first observation; metres or radians."""
    if isinstance(q, bool) or not isinstance(q, (int, float)) or not math.isfinite(q):
        raise ValueError("finite joint coordinate required")
    t = np.eye(4)
    if model["family"] == "sliding":
        t[:3, 3] = np.asarray(model["axis"]) * q
    elif model["family"] == "rotating":
        t[:3, :3] = rotation(model["axis"], q)
        c = np.asarray(model["axis_point"])
        t[:3, 3] = c - t[:3, :3] @ c
    elif model["family"] != "fixed":
        raise ValueError("free/unknown has no constrained prediction")
    return t


def coordinate(model, t):
    if model["family"] == "sliding":
        return float(np.dot(model["axis"], t[:3, 3]))
    if model["family"] == "rotating":
        return float(np.dot(model["axis"], rotvec(t[:3, :3])))
    return 0.0


def residual(model, poses):
    distances, angles = [], []
    for t in poses:
        pred = evaluate(model, coordinate(model, t))
        distances.append(float(np.linalg.norm(t[:3, 3]-pred[:3, 3])))
        angles.append(float(np.linalg.norm(rotvec(pred[:3, :3].T @ t[:3, :3]))))
    return {"position_rmse": float(np.sqrt(np.mean(np.square(distances)))),
            "angle_rmse": float(np.sqrt(np.mean(np.square(angles))))}


def _fit(family, poses):
    if family == "fixed":
        return {"family": family}
    translations = np.array([t[:3, 3] for t in poses])
    if family == "sliding":
        if np.max(np.linalg.norm(translations, axis=1)) < .01:
            return None
        # A small observed arc can look like a line at a long lever arm. Do not
        # promote sliding while rotation is present but insufficient to fit an axis.
        if max(np.linalg.norm(rotvec(t[:3, :3])) for t in poses) > .01:
            return None
        _, _, vt = np.linalg.svd(translations, full_matrices=False)
        axis = vt[0]
        if np.dot(axis, translations[np.argmax(np.linalg.norm(translations, axis=1))]) < 0:
            axis = -axis
        return {"family": family, "axis": axis.tolist(), "unit": "metres"}
    if family == "rotating":
        vectors = np.array([rotvec(t[:3, :3]) for t in poses])
        if np.max(np.linalg.norm(vectors, axis=1)) < .1:
            return None
        _, _, vt = np.linalg.svd(vectors, full_matrices=False)
        axis = vt[0]
        if np.dot(axis, vectors[np.argmax(np.linalg.norm(vectors, axis=1))]) < 0:
            axis = -axis
        a = np.vstack([np.eye(3)-t[:3, :3] for t in poses])
        b = translations.reshape(-1)
        if np.linalg.matrix_rank(a, tol=1e-5) < 2:
            return None
        # The axis point's component along the axis is unobservable; pick zero.
        c = np.linalg.lstsq(np.vstack([a, axis]), np.r_[b, 0.], rcond=None)[0]
        return {"family": family, "axis": axis.tolist(), "axis_point": c.tolist(), "unit": "radians"}
    return None


def fit_models(samples, families=("fixed", "sliding", "rotating"), *, preferred=None):
    """Do not promote Qwen's choice when numerical validation disagrees."""
    if not families or set(families)-set(FAMILIES) or preferred not in (*families, None):
        raise ValueError("invalid candidate joint family")
    result = {"status": "unknown", "reason": "insufficient independent public motion samples",
              "selected": None, "candidates": [], "sample_count": len(samples),
              "parameter_frame": "parent_initial_observed_geometry",
              "evidence_ids": sorted({e for s in samples for e in s["evidence_ids"]})}
    if len(samples) < 4:
        return result
    times = [s["captured_at"] for s in samples]
    if any(not math.isfinite(t) for t in times) or any(b <= a for a, b in zip(times, times[1:])):
        raise ValueError("motion samples need strictly increasing acquisition times")
    observed = [transform(s["relative_transform"]) for s in samples]
    origin = observed[0]
    poses = [t @ np.linalg.inv(origin) for t in observed]
    for family in families:
        model = _fit(family, poses[:-1])
        if model is None:
            continue
        check = residual(model, poses[-1:])
        train = residual(model, poses[:-1])
        good = max(check["position_rmse"], train["position_rmse"]) <= .008 and max(check["angle_rmse"], train["angle_rmse"]) <= .035
        row = {**model, "training_error": train, "validation_error": check,
               "valid": good, "identifiable": family != "fixed"}
        if good:
            model = _fit(family, poses)
            row.update(model)
            qs = [coordinate(model, t) for t in poses]
            row.update(q=qs[-1], observed_range=[min(qs), max(qs)],
                       origin_transform=origin.tolist())
        result["candidates"].append(row)
    valid = [m for m in result["candidates"] if m["valid"] and m["identifiable"]]
    if preferred in ("free", "unknown"):
        result["reason"] = "unconstrained proposal requires further evidence; no constrained prediction"
    elif len(valid) == 1 and preferred in (None, valid[0]["family"]):
        result.update(status="fitted", selected=valid[0], reason="motion template passed temporal validation")
    elif len(valid) > 1:
        result["reason"] = "multiple model families still explain the observations"
    elif preferred and any(m["valid"] and m["identifiable"] for m in result["candidates"]):
        result["reason"] = "proposed family contradicts numerically validated motion"
    elif any(m["valid"] and m["family"] == "fixed" for m in result["candidates"]):
        result["reason"] = "stationary evidence cannot distinguish a fixed joint from an unexcited joint"
    else:
        result["reason"] = "no constrained template explains the motion"
    return result
