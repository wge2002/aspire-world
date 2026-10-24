# SPDX-License-Identifier: Apache-2.0
"""Motion verification from public robot proprioception and command telemetry.

The requested TCP and the measured panda_hand are compared in ONE declared
frame (see robot_frames.py). The production IK report declares
``robot_base/TCP``; a target whose frame is undeclared is never reached=true.
"""
from __future__ import annotations

from collections.abc import Mapping

import numpy as np

from .robot_frames import (FRAMES, HAND_LINK, FrameContractError, gripper_opening, hand_pose,
                           parse_reference, quaternion_angle, rotation_wxyz, tcp_from_hand)

__all__ = ["DEFAULT_TCP_OFFSET", "FrameContractError", "motion_report", "rotation_wxyz"]

# FrankaLiberoApiReduced._TCP_OFFSET; used only when telemetry omits the offset.
DEFAULT_TCP_OFFSET = (0., 0., -.1)


def _target_frame(frame, ik):
    declared = None
    if isinstance(ik, Mapping) and ik.get("position_reference") is not None:
        declared, _ = parse_reference(ik["position_reference"], point="TCP")
    if frame is not None:
        if frame not in FRAMES:
            raise FrameContractError(f"unsupported target frame {frame!r}; frames are {FRAMES}")
        if declared is not None and declared != frame:
            raise FrameContractError(f"IK telemetry target frame {declared!r} contradicts {frame!r}")
        return frame
    return declared


def _offset(tcp_offset, ik):
    recorded = ik.get("tcp_offset_hand") if isinstance(ik, Mapping) else None
    if recorded is not None:
        recorded = np.asarray(recorded, dtype=float)
        if recorded.shape != (3,) or not np.isfinite(recorded).all():
            raise FrameContractError("IK telemetry tcp_offset_hand must be one finite XYZ vector")
    if tcp_offset is not None:
        tcp_offset = np.asarray(tcp_offset, dtype=float)
        if recorded is not None and not np.allclose(recorded, tcp_offset, atol=1e-9):
            raise FrameContractError("tcp_offset contradicts the offset recorded by IK telemetry")
        return tcp_offset, "argument"
    if recorded is not None:
        return recorded, "ik_telemetry"
    return np.asarray(DEFAULT_TCP_OFFSET, dtype=float), "default"


def motion_report(position, quaternion, robot, *, ik=None, joints=None, frame=None,
                  tcp_offset=None, position_tolerance=.015, angle_tolerance=.15):
    """Check the requested TCP against the measured hand in the target's frame.

    ``frame`` defaults to the IK telemetry's ``position_reference`` frame.
    Missing frame declaration, calibrated hand pose or IK telemetry is unknown.
    Malformed or contradictory frame metadata raises FrameContractError.
    Lack of observed progress never establishes arrival. ``reached`` means the
    measured TCP is within the unchanged tolerances of the REQUESTED pose and
    the joint motion converged; joint nonconvergence forces reached=false.
    Workspace clipping/orientation fallback set ``target_modified=true``; they
    do not by themselves force reached=false (a clip smaller than the
    tolerance can still arrive at the requested pose), but interaction checks
    require ``target_modified is False`` and treat such actions as unverified.
    """
    position = np.asarray(position, dtype=float)
    quaternion = np.asarray(quaternion, dtype=float)
    rotation_wxyz(quaternion)
    if position.shape != (3,) or not np.isfinite(position).all():
        raise ValueError("target position must be one finite XYZ vector")
    target_frame = _target_frame(frame, ik)
    result = {"requested": {"position": position.tolist(), "quaternion_wxyz": quaternion.tolist()},
              "coordinate_frame": target_frame,
              "position_reference": f"{target_frame}/TCP" if target_frame else None,
              "reached": None, "status": "unknown",
              "ik": ik, "joint_motion": joints, "target_modified": None,
              "position_tolerance": position_tolerance, "angle_tolerance": angle_tolerance}
    if ik is None:
        result["reason"] = "effective IK target was not recorded"
        return result
    result["target_modified"] = bool(ik.get("position_clipped") or ik.get("orientation_fallback"))
    if target_frame is None:
        result["reason"] = "target coordinate frame was not declared"
        return result
    offset, source = _offset(tcp_offset, ik)
    result.update(tcp_offset_hand=offset.tolist(), tcp_offset_source=source)
    if not isinstance(robot, Mapping):
        result["reason"] = "public robot observation is unavailable"
        return result
    hand, hand_source = hand_pose(robot, target_frame)
    result["measured_gripper_opening"] = gripper_opening(robot)
    if hand is None:
        result["reason"] = f"public calibrated {HAND_LINK} pose in {target_frame} is unavailable"
        return result
    actual_tcp = tcp_from_hand(hand, offset)
    position_error = float(np.linalg.norm(actual_tcp - position))
    angle_error = quaternion_angle(hand[3:], quaternion)
    reached = position_error <= position_tolerance and angle_error <= angle_tolerance
    if joints is not None and joints.get("converged") is not True:
        reached = False
    result.update(hand_reference=f"{target_frame}/{HAND_LINK}", hand_source=hand_source,
                  actual_hand_position=hand[:3].tolist(),
                  actual_tcp_position=actual_tcp.tolist(), actual_quaternion_wxyz=hand[3:].tolist(),
                  position_error=position_error, angle_error=angle_error, reached=bool(reached),
                  status="reached" if reached else "not_reached")
    effective = ik.get("effective_tcp_position")
    effective_q = ik.get("effective_quaternion_wxyz")
    if effective is not None and effective_q is not None:
        # Diagnostic only: arrival at a clipped/fallback target is not arrival
        # at the requested target.
        result.update(effective_position_error=float(np.linalg.norm(actual_tcp - np.asarray(effective, dtype=float))),
                      effective_angle_error=quaternion_angle(hand[3:], effective_q))
    return result
