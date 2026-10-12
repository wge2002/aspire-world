# SPDX-License-Identifier: Apache-2.0
"""Motion verification from public robot proprioception and command telemetry."""
from __future__ import annotations

import numpy as np


def rotation_wxyz(quaternion):
    q = np.asarray(quaternion, dtype=float)
    if q.shape != (4,) or not np.isfinite(q).all() or np.linalg.norm(q) < 1e-9:
        raise ValueError("one finite, nonzero WXYZ quaternion is required")
    w, x, y, z = q / np.linalg.norm(q)
    return np.array([[1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w)],
                     [2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w)],
                     [2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y)]])


def motion_report(position, quaternion, robot, *, ik=None, joints=None,
                  tcp_offset=(0., 0., -.1), position_tolerance=.015, angle_tolerance=.15):
    """Check the requested TCP in world frame, accounting for panda_hand offset.

    Missing calibrated public hand pose is unknown. Lack of observed progress
    never establishes arrival. IK fallbacks and workspace clipping stay visible.
    """
    position = np.asarray(position, dtype=float)
    quaternion = np.asarray(quaternion, dtype=float)
    rotation_wxyz(quaternion)
    if position.shape != (3,) or not np.isfinite(position).all():
        raise ValueError("target position must be one finite XYZ vector")
    result = {"requested": {"position": position.tolist(), "quaternion_wxyz": quaternion.tolist()},
              "position_reference": "world/TCP", "reached": None, "status": "unknown",
              "ik": ik, "joint_motion": joints, "target_modified": None,
              "position_tolerance": position_tolerance, "angle_tolerance": angle_tolerance}
    if ik is None:
        result["reason"] = "effective IK target was not recorded"
        return result
    result["target_modified"] = bool(ik.get("position_clipped") or ik.get("orientation_fallback"))
    hand = robot.get("robot_hand_pose_world")
    if hand is None:
        result["reason"] = "public calibrated panda_hand pose is unavailable"
        return result
    hand = np.asarray(hand, dtype=float)
    if hand.shape != (7,) or not np.isfinite(hand).all():
        raise ValueError("public hand pose must be world XYZ/WXYZ")
    actual_tcp = hand[:3] - rotation_wxyz(hand[3:]) @ np.asarray(tcp_offset)
    position_error = float(np.linalg.norm(actual_tcp - position))
    dot = float(abs(np.dot(hand[3:] / np.linalg.norm(hand[3:]), quaternion / np.linalg.norm(quaternion))))
    angle_error = float(2*np.arccos(np.clip(dot, 0, 1)))
    reached = position_error <= position_tolerance and angle_error <= angle_tolerance
    if joints is not None and joints.get("converged") is not True:
        reached = False
    result.update(actual_tcp_position=actual_tcp.tolist(), actual_quaternion_wxyz=hand[3:].tolist(),
                  position_error=position_error, angle_error=angle_error, reached=bool(reached),
                  status="reached" if reached else "not_reached")
    return result
