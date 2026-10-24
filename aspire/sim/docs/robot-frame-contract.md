# Public robot frame, TCP and gripper contract (LIBERO driver)

Engineering interface contract, not a task strategy. Implementation:
`cap/world_model/robot_frames.py`; producers `cap/envs/simulators/libero.py`
(`get_observation`, `get_robot_observation`) and
`cap/integrations/franka/libero_reduced.py` (`solve_ik`, `goto_pose`).
Only public proprioception and camera calibration are used; no simulator
object state, asset geometry or evaluator predicate is exposed.

## Frames

| Frame | Meaning | Used by |
| --- | --- | --- |
| `robot_base` | Policy/action frame | camera `pose_mat`, back-projected points, World object geometry, `solve_ik`/`goto_pose` targets, IK telemetry, motion reports, World robot `frame_view` |
| `world` | Simulator world frame | `robot_hand_pose_world`, `robot_base_pose_world` only |

Older API text called the policy frame "world". That frame is `robot_base`;
the base is generally translated (and may be rotated) from simulator `world`,
so values from the two frames must never be compared directly. Do not hardcode
a base offset: use `robot_base_pose_world` when a conversion is really needed.

## Camera observation

`obs[camera]["pose_mat"]` maps OpenCV optical coordinates (+x right, +y down,
+z forward) into `robot_base`; `obs[camera]["pose_frame"] == "robot_base"` and
`pose_convention` states this. `PublicFrame.from_observation` records
`pose_frame` as `coordinate_frame`.

## Robot observation

| Field | Shape | Content |
| --- | --- | --- |
| `robot_cartesian_pos` | (8,) | robot_base panda_hand `[x, y, z, qw, qx, qy, qz, gripper_opening]`; index 6 is quaternion z, index 7 is the opening |
| `robot_cartesian_reference` | str | `robot_base/panda_hand` |
| `robot_cartesian_layout` | str | `xyz[0:3], quaternion_wxyz[3:7], gripper_opening[7]` |
| `robot_hand_pose_base` (+`_reference`) | (7,) | same pose as `robot_cartesian_pos[:7]`, named |
| `robot_gripper_opening` | float | finger opening normalized 0 (closed) to 1 (open) |
| `robot_hand_pose_world` (`robot_hand_pose_reference`) | (7,) | `world/panda_hand`, unchanged legacy field |
| `robot_base_pose_world` (+`_reference`) | (7,) | `world/robot_base` mounting calibration |
| `robot_joint_pos` | (8,) | 7 arm joints (rad) + gripper opening |

The public panda_hand is the simulator gripper site rotated by Rz(pi/2) and
offset -0.107 m along its Z axis. `robot_frames.check_consistency` verifies
`world/robot_base o robot_base/panda_hand == world/panda_hand`; contradictory
labels, shapes or poses raise `FrameContractError`. Unlabeled legacy layouts
are never assigned a guessed frame.

## TCP

`solve_ik(position, quaternion_wxyz)` and `goto_pose` take a robot_base TCP
target. The API offset `_TCP_OFFSET = [0, 0, -0.1]` (hand frame) gives the IK
panda_hand target `hand = tcp + R(q) @ offset`, so measured
`tcp = hand - R(hand_q) @ offset`. IK telemetry (`_last_ik_report`) records
`position_reference: robot_base/TCP`, `hand_position_reference:
robot_base/panda_hand`, `tcp_offset_hand`, requested/effective TCP, effective
hand target, `position_clipped`, `orientation_fallback` and the chosen
orientation. Workspace clipping and orientation fallback are unchanged.

## Motion reports

`motion_report` compares the requested TCP with the measured hand converted to
TCP in the declared target frame (argument `frame`, or the telemetry's
`position_reference`). Tolerances remain 0.015 m and 0.15 rad. Arrival requires
both; joint nonconvergence (`converged` not true) forces `reached=false`.
`target_modified` is true after clipping or orientation fallback, and
`effective_position_error`/`effective_angle_error` are diagnostics only.
Clipping/fallback does not itself force `reached=false`: `reached` measures the
requested pose, so a clip smaller than the tolerance can report
`reached=true, target_modified=true`. Interaction checks require
`reached is True and target_modified is False`, so such an action stays
`execution_unverified`; a clip beyond tolerance is simply not reached.
An undeclared target frame, missing IK telemetry or missing calibrated hand
pose yields `reached=None`/`status=unknown`; contradictory frames or offsets
raise. Reports never establish grasp, contact or task success.
`goto_pose_report` passes `frame="robot_base"` and the actual API offset.

## World

Production `StreamingReplay` builds the World with
`coordinate_frame="robot_base"` and the API TCP offset. Every camera frame must
then declare `robot_base`; object facts carry `coordinate_frame`. Robot facts
keep all legacy fields and add `frame_view`: `frame`, `status`,
`hand_position`, `hand_quaternion_wxyz`, `tcp_position`, `tcp_offset_hand`,
`gripper_opening`. Authored relation predicates should compare object
`visible_center` with `robot["frame_view"]["tcp_position"]` and read the opening
from `frame_view["gripper_opening"]`. A relation whose dependencies declare
different frames raises `FrameError`. Callers that construct `World` without
`coordinate_frame` keep the legacy undeclared behaviour.

## Runtime identity

`robot_frames.py` and this document are in
`streaming_fixloop_protocol.ENGINEERED_WORLD_FILES`. Any change to these, the
driver, IK telemetry or World alters the engineered runtime hashes: future
experiments need a new runtime freeze and fresh cells. Results produced before
this contract (e.g. the 2026-10-12 v3 campaign) remain historical records with
their original frozen identity; their motion reports used the earlier,
inconsistent comparison.
