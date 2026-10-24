# SPDX-License-Identifier: Apache-2.0
"""Pure geometry: candidate TCP poses that rest a held body on a visible support.

Inputs are public measurements the caller already has: visible object points
in the TCP frame (e.g. ``world_held_state(...)["measured_relation"]
["features_tcp"]["visible_points_tcp"]``), the current TCP pose, and a visible
support plane (``normal``, ``centroid``) in the robot_base frame, optionally
with rail lines on that support. Nothing here reads simulator state, assets or
the evaluator. Every output is a labelled prediction: not contact, support,
stability, reachability or task success. Reachability is checked afterwards
through the public checked-motion / workspace / IK interfaces.

Conventions (docs/visible-held-placement.md):

* positions in metres, robot_base frame; quaternions WXYZ; the TCP frame has
  the panda_hand rotation and the API TCP origin;
* axis vectors are unit and their sign is arbitrary unless stated: a body axis
  from visible-surface PCA has no head/tail, so both signs are candidates;
* the support ``normal`` points from the support into free space;
* visible surfaces are not whole shapes: the body radius comes from a circle
  fit of the visible cross-section (or an explicitly labelled caller value),
  the visible length is a lower bound, and hidden extents stay unknown.

Insufficient evidence returns ``status="unknown"`` with a reason; nothing is
filled in with fixed offsets.
"""
from __future__ import annotations

import numpy as np

from .geometry_queries import principal_axes
from .robot_frames import quaternion_from_rotation

MIN_POINTS = 30
MIN_ARC = np.deg2rad(60)   # visible cross-section arc needed for a free circle fit
MAX_FIT_RMS = .004         # m, circle-fit residual above which the radius is unknown
CAVEATS = ("prediction only: not contact, support, stability, reachability or task success",
           "visible surface only: hidden extents and the body's far side are modelled, not measured",
           "verify reachability with solve_ik/goto_pose_report and re-observe after release")


def _unit(v, name):
    v = np.asarray(v, dtype=float)
    if v.shape != (3,) or not np.isfinite(v).all() or np.linalg.norm(v) < 1e-9:
        raise ValueError(f"{name} must be one finite nonzero XYZ vector")
    return v / np.linalg.norm(v)


def _align(u, v):
    """Minimal rotation matrix taking unit u onto unit v."""
    w, c = np.cross(u, v), float(np.clip(u @ v, -1, 1))
    if c < -1 + 1e-9:
        helper = np.eye(3)[int(np.argmin(np.abs(u)))]
        k = np.cross(u, helper)
        k /= np.linalg.norm(k)
        return 2 * np.outer(k, k) - np.eye(3)
    K = np.array([[0, -w[2], w[1]], [w[2], 0, -w[0]], [-w[1], w[0], 0]])
    return np.eye(3) + K + K @ K / (1 + c)


def _basis(axis):
    helper = np.eye(3)[int(np.argmin(np.abs(axis)))]
    e1 = np.cross(axis, helper)
    e1 /= np.linalg.norm(e1)
    return e1, np.cross(axis, e1)


def cylinder_cross_section(points, axis, *, radius=None, view_direction=None, min_points=MIN_POINTS):
    """Fit the circular cross-section of visible points around a given axis.

    ``points`` and the unit ``axis`` share one frame. Without ``radius`` a
    free (Kasa) circle fit is accepted only if the visible arc spans at least
    60 deg with rms <= 4 mm. With a caller ``radius`` (labelled by the caller)
    the centre is fitted with that radius from an initial guess behind the
    visible surface along ``view_direction`` (camera -> object, same frame);
    without a view direction a fixed-radius fit is ambiguous (two centres).
    Returns the centreline point at the middle of the VISIBLE axial span.
    """
    pts = np.asarray(points, dtype=float).reshape(-1, 3)
    pts = pts[np.isfinite(pts).all(axis=1)]
    axis = _unit(axis, "axis")
    result = {"status": "unknown", "point_count": int(len(pts)), "axis": axis.tolist()}
    if len(pts) < min_points:
        return {**result, "reason": f"{len(pts)} visible points < {min_points}"}
    e1, e2 = _basis(axis)
    xy = np.c_[pts @ e1, pts @ e2]
    along = pts @ axis
    if radius is None:
        A = np.c_[2 * xy, np.ones(len(xy))]
        b = (xy ** 2).sum(1)
        sol, *_ = np.linalg.lstsq(A, b, rcond=None)
        centre = sol[:2]
        r = float(np.sqrt(max(sol[2] + centre @ centre, 0.)))
        source = "free circle fit of the visible cross-section"
    else:
        if isinstance(radius, bool) or not np.isfinite(radius) or radius <= 0:
            raise ValueError("radius must be positive and finite")
        if view_direction is None:
            return {**result, "reason": "fixed-radius fit needs a view direction to choose the hidden side"}
        v = _unit(view_direction, "view_direction")
        v2 = np.array([v @ e1, v @ e2])
        if np.linalg.norm(v2) < 1e-6:
            return {**result, "reason": "view direction is along the axis; the cross-section is not visible"}
        centre = xy.mean(0) + radius * v2 / np.linalg.norm(v2)
        for _ in range(20):  # Gauss-Newton for the centre with a fixed radius
            d = xy - centre
            dist = np.linalg.norm(d, axis=1)
            dist[dist < 1e-9] = 1e-9
            J = -d / dist[:, None]
            step, *_ = np.linalg.lstsq(J, -(dist - radius), rcond=None)
            centre = centre + step
            if np.linalg.norm(step) < 1e-7:
                break
        r = float(radius)
        source = "caller radius; centre fitted behind the visible surface"
    d = xy - centre
    dist = np.linalg.norm(d, axis=1)
    rms = float(np.sqrt(np.mean((dist - r) ** 2)))
    angles = np.sort(np.arctan2(d[:, 1], d[:, 0]))
    gaps = np.diff(np.r_[angles, angles[0] + 2 * np.pi])
    arc = float(2 * np.pi - gaps.max())
    result.update(radius=r, rms=rms, arc=arc, radius_source=source,
                  visible_axial_span=float(along.max() - along.min()))
    if not np.isfinite(r) or r <= 0 or rms > MAX_FIT_RMS or (radius is None and arc < MIN_ARC):
        return {**result, "status": "unknown",
                "reason": f"cross-section not determined (arc {np.degrees(arc):.0f} deg, rms {rms:.4f} m)"}
    mid = .5 * (along.max() + along.min())
    centre3 = centre[0] * e1 + centre[1] * e2 + mid * axis
    return {**result, "status": "known", "centerline_point": centre3.tolist(),
            "reason": "visible cross-section circle; hidden far side and axial ends are modelled, not measured"}


def _rails_cradle(rails, direction, target, normal, centroid, radius):
    """Two rails parallel to the body direction that bracket the target: the
    body lies in the groove; its axis sits sqrt(r^2-(s/2)^2) above the rail line plane."""
    parallel = []
    for rail in rails:
        d = _unit(rail["direction"], "rail direction")
        p = np.asarray(rail["point"], dtype=float)
        if abs(d @ direction) >= np.cos(np.deg2rad(10)):
            parallel.append((p, d))
    lateral = np.cross(normal, direction)
    offsets = sorted(((p - target) @ lateral, p) for p, _ in parallel)
    left = [o for o in offsets if o[0] < 0]
    right = [o for o in offsets if o[0] > 0]
    if not left or not right:
        return None
    (a, pa), (b, pb) = left[-1], right[0]
    spacing = float(b - a)
    if radius <= spacing / 2:
        return {"status": "falls_between", "spacing": spacing}
    rail_height = float(.5 * ((pa - centroid) @ normal + (pb - centroid) @ normal))
    middle = target + (.5 * (a + b)) * lateral
    middle = middle - ((middle - centroid) @ normal - rail_height) * normal
    return {"status": "cradled", "spacing": spacing, "point": middle,
            "axis_height": float(np.sqrt(radius ** 2 - (spacing / 2) ** 2))}


def support_placement_candidates(object_points_tcp, tcp_pose, support, *, body_axis_tcp=None,
                                 radius=None, radius_source=None, view_direction_tcp=None,
                                 along=None, rails=None, target_point=None, tilts=(0.,),
                                 clearance=0., workspace=None, min_points=MIN_POINTS,
                                 coordinate_frame="robot_base"):
    """Candidate TCP poses resting a held body tangent to a visible support plane.

    object_points_tcp: N x 3 measured visible points of the held body in the
      CURRENT TCP frame (rigid-held assumption: they move with the TCP).
    tcp_pose: 4 x 4 TCP pose (robot_base) at which those points were measured.
    support: {"normal", "centroid"} of a visible support plane (robot_base);
      the normal points into free space.
    body_axis_tcp: unit body axis in the TCP frame; default: visible-PCA long
      axis of the points (``unknown`` if ambiguous). Sign is arbitrary.
    radius/radius_source: optional caller radius with its measurement source;
      otherwise a free circle fit of the visible cross-section.
    along: candidate in-plane directions for the body axis (robot_base); rail
      directions are added; default: the current axis projected on the plane.
    rails: optional [{"point", "direction"}] rail lines on the support; two
      parallel rails bracketing the target cradle a body lying along them.
    target_point: desired placement point (projected onto the support);
      default the support centroid.
    tilts: body-axis elevations (rad) above the support plane to evaluate.
    workspace: optional (low, high) TCP box; a box flag only, not IK.

    Height is tangency: n . lowest = n . support + clearance, where the
    extent below the body centre is the larger of the measured visible points
    and the fitted cylinder model ``r*sqrt(1-(n.a)^2) + (L/2)|n.a|``.
    """
    pts = np.asarray(object_points_tcp, dtype=float).reshape(-1, 3) if np.size(object_points_tcp) else np.zeros((0, 3))
    pts = pts[np.isfinite(pts).all(axis=1)]
    T = np.asarray(tcp_pose, dtype=float)
    if T.shape != (4, 4) or not np.isfinite(T).all() or not np.allclose(T[:3, :3].T @ T[:3, :3], np.eye(3), atol=1e-5):
        raise ValueError("tcp_pose must be a finite 4x4 rigid transform")
    n = _unit(support.get("normal"), "support normal")
    c = np.asarray(support.get("centroid"), dtype=float)
    if c.shape != (3,) or not np.isfinite(c).all():
        raise ValueError("support centroid must be one finite XYZ point")
    if isinstance(clearance, bool) or not np.isfinite(clearance) or clearance < 0:
        raise ValueError("clearance must be finite and nonnegative")
    base = {"status": "unknown", "layer": "predicted", "coordinate_frame": coordinate_frame,
            "conventions": {"positions": "m, robot_base", "quaternion": "WXYZ", "tcp_frame":
                            "panda_hand rotation at the API TCP origin", "axis_sign": "arbitrary unless stated",
                            "support_normal": "from the support into free space"},
            "support": {"normal": n.tolist(), "centroid": c.tolist()}, "point_count": int(len(pts)),
            "caveats": list(CAVEATS)}
    if len(pts) < min_points:
        return {**base, "reason": f"{len(pts)} visible held points < {min_points}"}
    if body_axis_tcp is None:
        axes = principal_axes(pts)
        if axes["long_axis_status"] != "known":
            return {**base, "reason": "body axis unknown: visible held points have no dominant axis "
                    f"({axes.get('reason')}); pass a measured body_axis_tcp"}
        a_t, axis_source = np.asarray(axes["long_axis"], dtype=float), "visible-surface PCA (sign arbitrary)"
    else:
        a_t, axis_source = _unit(body_axis_tcp, "body_axis_tcp"), "caller"
    section = cylinder_cross_section(pts, a_t, radius=radius, view_direction=view_direction_tcp,
                                     min_points=min_points)
    if radius is not None:
        if not isinstance(radius_source, str) or not radius_source.strip():
            raise ValueError("a caller radius needs radius_source naming its measurement")
        section["radius_source"] = f"caller: {radius_source}"
    base["cross_section"] = section
    base["body_axis_tcp"] = {"axis": a_t.tolist(), "source": axis_source}
    if section["status"] != "known":
        return {**base, "reason": f"body radius/centreline unknown: {section['reason']}"}
    r, m_t = section["radius"], np.asarray(section["centerline_point"])
    L = section["visible_axial_span"]
    R_now = T[:3, :3]
    target = c if target_point is None else np.asarray(target_point, dtype=float)
    if target.shape != (3,) or not np.isfinite(target).all():
        raise ValueError("target_point must be one finite XYZ point")
    target = target - ((target - c) @ n) * n
    directions = []
    for d in list(along or []) + [rail["direction"] for rail in (rails or [])]:
        d = _unit(d, "along direction")
        d = d - (d @ n) * n
        if np.linalg.norm(d) < 1e-6:
            continue
        d /= np.linalg.norm(d)
        if not any(abs(d @ e) > np.cos(np.deg2rad(5)) for e, _ in directions):
            directions.append((d, "caller/rail direction"))
    if not directions:
        current = R_now @ a_t
        d = current - (current @ n) * n
        if np.linalg.norm(d) < 1e-6:
            return {**base, "reason": "no along direction given and the current body axis is along the "
                    "support normal; the in-plane heading is ambiguous"}
        directions.append((d / np.linalg.norm(d), "current axis heading projected on the support"))
    candidates = []
    for d, d_source in directions:
        for sign in (1., -1.):
            for tilt in tilts:
                if isinstance(tilt, bool) or not np.isfinite(tilt) or abs(tilt) >= np.pi / 2:
                    raise ValueError("tilts must be finite and within (-pi/2, pi/2)")
                a_w = np.cos(tilt) * sign * d + np.sin(tilt) * n
                R = _align(R_now @ a_t, a_w) @ R_now
                n_t = R.T @ n
                cos_na = float(abs(n_t @ a_t))
                e_model = r * np.sqrt(max(1 - cos_na ** 2, 0.)) + .5 * L * cos_na
                e_visible = float(np.max(-(pts - m_t) @ n_t))
                extent, source = (e_model, "cylinder model") if e_model >= e_visible else (e_visible, "measured visible points")
                point, mode = target, "plane tangency"
                row = {"layer": "predicted", "direction_source": d_source, "sign": sign, "tilt": float(tilt),
                       "axis_world": a_w.tolist()}
                if rails and abs(tilt) < 1e-9:
                    cradle = _rails_cradle(rails, d, target, n, c, r)
                    if cradle is not None and cradle["status"] == "falls_between":
                        candidates.append({**row, "status": "rejected", "reason":
                                           f"radius {r:.3f} <= half rail spacing {cradle['spacing'] / 2:.3f}"})
                        continue
                    if cradle is not None:
                        point, mode, extent, source = (cradle["point"], f"cradled between rails (spacing "
                                                       f"{cradle['spacing']:.3f} m)", cradle["axis_height"],
                                                       "cylinder radius over rail spacing")
                m_w = point + (extent + clearance) * n
                tcp = m_w - R @ m_t
                rotation = float(np.arccos(np.clip((np.trace(R_now.T @ R) - 1) / 2, -1, 1)))
                row.update(status="candidate", mode=mode, tcp_position=tcp.tolist(),
                           tcp_quaternion_wxyz=quaternion_from_rotation(R).tolist(),
                           body_center_world=m_w.tolist(), lowest_point_world=(m_w - extent * n).tolist(),
                           extent_below_center=float(extent), extent_source=source, clearance=float(clearance),
                           rotation_from_current=rotation)
                if workspace is not None:
                    low, high = (np.asarray(v, dtype=float) for v in workspace)
                    row["workspace_box"] = "inside" if np.all(tcp >= low) and np.all(tcp <= high) else "outside"
                else:
                    row["workspace_box"] = "unchecked"
                candidates.append(row)
    ranked = sorted((c for c in candidates if c["status"] == "candidate"),
                    key=lambda c: (c["workspace_box"] == "outside", c["rotation_from_current"]))
    rejected = [c for c in candidates if c["status"] != "candidate"]
    if not ranked:
        return {**base, "reason": "every candidate was geometrically rejected", "rejected": rejected}
    ambiguity = ["body axis sign is arbitrary: both signs listed"]
    if len(directions) == 1 and directions[0][1].startswith("current"):
        ambiguity.append("no support direction given: heading kept from the current body axis")
    return {**base, "status": "candidates", "candidates": ranked, "rejected": rejected,
            "ambiguity": ambiguity, "reason": "tangent placement candidates from measured visible geometry"}
