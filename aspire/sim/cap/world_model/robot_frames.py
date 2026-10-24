# SPDX-License-Identifier: Apache-2.0
"""Shared coordinate contract for public LIBERO robot proprioception.

Frames (see docs/robot-frame-contract.md):

* ``robot_base`` is the policy/action frame. Camera ``pose_mat`` maps camera
  optical coordinates into it, so World object geometry, ``solve_ik`` and
  ``goto_pose`` targets and motion reports share it.
* ``world`` is the simulator frame. ``robot_hand_pose_world`` remains
  accurately labeled there for compatibility; it is never compared with a
  robot_base target.

Poses are ``[x, y, z, qw, qx, qy, qz]`` of the calibrated ``panda_hand`` link.
The IK TCP is ``hand = tcp + R(hand) @ tcp_offset_hand``. Gripper opening is a
named normalized scalar (0 closed, 1 open), never a quaternion index.
Only public proprioception is read; no simulator object or evaluator state.
"""
from __future__ import annotations

from collections.abc import Mapping

import numpy as np

POLICY_FRAME = "robot_base"
SIMULATOR_FRAME = "world"
FRAMES = (POLICY_FRAME, SIMULATOR_FRAME)
HAND_LINK = "panda_hand"
CAMERA_POSE_CONVENTION = "camera_to_robot_base; OpenCV optical axes (+x right, +y down, +z forward)"
CARTESIAN_LAYOUT = "xyz[0:3], quaternion_wxyz[3:7], gripper_opening[7]"
GRIPPER_OPENING_MEANING = "normalized finger opening: 0 closed, 1 open"
CONSISTENCY_POSITION_TOLERANCE = 1e-4
CONSISTENCY_ANGLE_TOLERANCE = 1e-3


class FrameContractError(ValueError):
    """Public pose metadata is malformed, mislabeled or mutually inconsistent."""


def rotation_wxyz(quaternion):
    q = np.asarray(quaternion, dtype=float)
    if q.shape != (4,) or not np.isfinite(q).all() or np.linalg.norm(q) < 1e-9:
        raise ValueError("one finite, nonzero WXYZ quaternion is required")
    w, x, y, z = q / np.linalg.norm(q)
    return np.array([[1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w)],
                     [2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w)],
                     [2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y)]])


def quaternion_from_rotation(R):
    R = np.asarray(R, dtype=float)
    trace = np.trace(R)
    if trace > 0:
        s = 2 * np.sqrt(trace + 1)
        q = [s / 4, (R[2, 1]-R[1, 2]) / s, (R[0, 2]-R[2, 0]) / s, (R[1, 0]-R[0, 1]) / s]
    else:
        i = int(np.argmax(np.diag(R)))
        j, k = (i + 1) % 3, (i + 2) % 3
        s = 2 * np.sqrt(1 + R[i, i] - R[j, j] - R[k, k])
        q = np.zeros(4)
        q[0] = (R[k, j] - R[j, k]) / s
        q[1 + i] = s / 4
        q[1 + j] = (R[j, i] + R[i, j]) / s
        q[1 + k] = (R[k, i] + R[i, k]) / s
    q = np.asarray(q, dtype=float)
    return q / np.linalg.norm(q)


def quaternion_angle(a, b):
    a = np.asarray(a, dtype=float) / np.linalg.norm(a)
    b = np.asarray(b, dtype=float) / np.linalg.norm(b)
    return float(2 * np.arccos(np.clip(abs(float(np.dot(a, b))), 0, 1)))


def pose_vector(value, *, name="pose"):
    """Validate one ``[xyz, wxyz]`` pose (7 values); return a float copy."""
    pose = np.asarray(value, dtype=float)
    if pose.shape != (7,) or not np.isfinite(pose).all() or np.linalg.norm(pose[3:]) < 1e-9:
        raise FrameContractError(f"{name} must be one finite XYZ/WXYZ pose of 7 values")
    pose = pose.copy()
    pose[3:] /= np.linalg.norm(pose[3:])
    return pose


def pose_matrix(pose):
    pose = pose_vector(pose)
    T = np.eye(4)
    T[:3, :3], T[:3, 3] = rotation_wxyz(pose[3:]), pose[:3]
    return T


def matrix_pose(T):
    T = np.asarray(T, dtype=float)
    return np.concatenate([T[:3, 3], quaternion_from_rotation(T[:3, :3])])


def parse_reference(reference, *, point=None):
    """``"robot_base/TCP"`` -> ``("robot_base", "TCP")``; unknown frames fail."""
    if not isinstance(reference, str) or reference.count("/") != 1:
        raise FrameContractError(f"pose reference must be '<frame>/<point>', got {reference!r}")
    frame, where = reference.split("/")
    if frame not in FRAMES or not where:
        raise FrameContractError(f"unsupported pose reference {reference!r}; frames are {FRAMES}")
    if point is not None and where != point:
        raise FrameContractError(f"pose reference {reference!r} does not describe {point}")
    return frame, where


def _labeled(robot, field, label_field, *, expected=None, width=7):
    if robot.get(field) is None:
        return None, None
    raw = np.asarray(robot[field], dtype=float)
    if raw.ndim != 1 or raw.shape[0] != width:
        raise FrameContractError(f"{field} must have exactly {width} values")
    label = robot.get(label_field)
    if label is None:
        if expected is None:
            return None, None  # unlabeled legacy layout: frame unknown, never guessed
        label = expected
    frame, point = parse_reference(label)
    if expected is not None and label != expected:
        raise FrameContractError(f"{label_field}={label!r} contradicts {field} ({expected})")
    if point != HAND_LINK:
        raise FrameContractError(f"{label_field} must describe {HAND_LINK}, got {label!r}")
    return frame, pose_vector(raw[:7], name=field)


def base_pose_world(robot):
    """Public robot mounting calibration (world/robot_base), if published."""
    if robot.get("robot_base_pose_world") is None:
        return None
    label = robot.get("robot_base_pose_world_reference", f"{SIMULATOR_FRAME}/robot_base")
    if label != f"{SIMULATOR_FRAME}/robot_base":
        raise FrameContractError(f"robot_base_pose_world_reference must be world/robot_base, got {label!r}")
    return pose_vector(robot["robot_base_pose_world"], name="robot_base_pose_world")


def _direct_hand_poses(robot):
    found = {}
    sources = (("robot_hand_pose_base", "robot_hand_pose_base_reference", f"{POLICY_FRAME}/{HAND_LINK}", 7),
               ("robot_hand_pose_world", "robot_hand_pose_reference", f"{SIMULATOR_FRAME}/{HAND_LINK}", 7),
               ("robot_cartesian_pos", "robot_cartesian_reference", None, 8))
    for field, label, expected, width in sources:
        frame, pose = _labeled(robot, field, label, expected=expected, width=width)
        if pose is None:
            continue
        if frame in found:
            previous_field, previous = found[frame]
            if (np.linalg.norm(previous[:3] - pose[:3]) > CONSISTENCY_POSITION_TOLERANCE
                    or quaternion_angle(previous[3:], pose[3:]) > CONSISTENCY_ANGLE_TOLERANCE):
                raise FrameContractError(f"{field} disagrees with {previous_field} in frame {frame}")
            continue
        found[frame] = (field, pose)
    return found


def check_consistency(robot):
    """Raise if published base, world and robot_base hand poses disagree."""
    if not isinstance(robot, Mapping):
        raise FrameContractError("robot observation must be a mapping")
    found = _direct_hand_poses(robot)
    base = base_pose_world(robot)
    if base is not None and POLICY_FRAME in found and SIMULATOR_FRAME in found:
        composed = matrix_pose(pose_matrix(base) @ pose_matrix(found[POLICY_FRAME][1]))
        world = found[SIMULATOR_FRAME][1]
        if (np.linalg.norm(composed[:3] - world[:3]) > CONSISTENCY_POSITION_TOLERANCE
                or quaternion_angle(composed[3:], world[3:]) > CONSISTENCY_ANGLE_TOLERANCE):
            raise FrameContractError("world/robot_base o robot_base/panda_hand != world/panda_hand")
    return found, base


def hand_pose(robot, frame=POLICY_FRAME):
    """Calibrated panda_hand pose in ``frame``, or None when not published.

    A missing direct pose is derived through the published base mounting pose;
    labels are never guessed and contradictions raise FrameContractError.
    """
    if frame not in FRAMES:
        raise FrameContractError(f"unsupported frame {frame!r}; frames are {FRAMES}")
    found, base = check_consistency(robot)
    if frame in found:
        return found[frame][1], found[frame][0]
    if base is not None:
        if frame == POLICY_FRAME and SIMULATOR_FRAME in found:
            T = np.linalg.inv(pose_matrix(base)) @ pose_matrix(found[SIMULATOR_FRAME][1])
            return matrix_pose(T), f"derived:{found[SIMULATOR_FRAME][0]}+robot_base_pose_world"
        if frame == SIMULATOR_FRAME and POLICY_FRAME in found:
            T = pose_matrix(base) @ pose_matrix(found[POLICY_FRAME][1])
            return matrix_pose(T), f"derived:{found[POLICY_FRAME][0]}+robot_base_pose_world"
    return None, None


def gripper_opening(robot):
    """Named normalized opening; legacy index 7 only with a labeled length-8 layout."""
    if not isinstance(robot, Mapping):
        raise FrameContractError("robot observation must be a mapping")
    named = robot.get("robot_gripper_opening")
    legacy = None
    cartesian = robot.get("robot_cartesian_pos")
    if cartesian is not None:
        cartesian = np.asarray(cartesian, dtype=float)
        if cartesian.ndim == 1 and cartesian.shape[0] == 8 and robot.get("robot_cartesian_reference") is not None:
            legacy = float(cartesian[7])
    if named is not None:
        value = np.asarray(named, dtype=float)
        if value.size != 1 or not np.isfinite(value).all():
            raise FrameContractError("robot_gripper_opening must be one finite scalar")
        value = float(value.reshape(()))
        if legacy is not None and abs(legacy - value) > 1e-6:
            raise FrameContractError("robot_gripper_opening disagrees with robot_cartesian_pos[7]")
        return value
    if legacy is not None and np.isfinite(legacy):
        return legacy
    return None


def tcp_from_hand(hand, tcp_offset):
    hand = pose_vector(hand, name="hand pose")
    offset = np.asarray(tcp_offset, dtype=float)
    if offset.shape != (3,) or not np.isfinite(offset).all():
        raise FrameContractError("tcp_offset_hand must be one finite XYZ vector")
    return hand[:3] - rotation_wxyz(hand[3:]) @ offset


def robot_pose_view(robot, *, frame=POLICY_FRAME, tcp_offset=None):
    """Unambiguous named robot view in one frame for predicates and reports."""
    hand, source = hand_pose(robot, frame)
    view = {"frame": frame, "status": "unknown", "hand_reference": f"{frame}/{HAND_LINK}",
            "gripper_opening": gripper_opening(robot), "gripper_opening_meaning": GRIPPER_OPENING_MEANING}
    if hand is None:
        view["reason"] = f"no labeled {HAND_LINK} pose in {frame} is published"
        return view
    view.update(status="known", hand_source=source, hand_position=hand[:3].tolist(),
                hand_quaternion_wxyz=hand[3:].tolist())
    if tcp_offset is not None:
        view.update(tcp_reference=f"{frame}/TCP", tcp_offset_hand=np.asarray(tcp_offset, dtype=float).tolist(),
                    tcp_position=tcp_from_hand(hand, tcp_offset).tolist())
    return view
