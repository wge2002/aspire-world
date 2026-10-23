---
name: libero-policy-api-revision-m4
description: What changes in the policy API under revision m4-r1 (solve_ik reports, segmented goto_pose, deterministic grasp planning and subsampling, step-budget scoring). Read when the cell prompt names API revision m4-r1.
---

# Policy API revision m4-r1

The function names and return values are the ones in
`.claude/libero/api-reference.md`; this revision only adds reporting, one
keyword argument and determinism. Nothing here reads simulator state.

## solve_ik(position, quaternion_wxyz) -> joints (7,)

- The target is clipped to the reachable workspace
  x in [-0.1, 0.75], y in [-0.5, 0.5], z in [0.005, 0.9].
- If IK fails for the requested orientation, top-down, 45-tilt and
  side-approach orientations are tried, in that order.
- Whenever the target was clipped or another orientation was used, one line is
  printed:

      [solve_ik] target clipped to the workspace: requested [...] -> used [...]; orientation substituted: requested wxyz [...] -> used top-down [...]

  The joints then reach the USED pose, not the requested one. Read the trial
  log for these lines before concluding that a place is reachable.
- The joints are a best-effort solution: after moving, check the reached pose
  with `get_observation()["robot_cartesian_pos"][:3]`.

## goto_pose(position, quaternion_wxyz, z_approach=0.0, max_step=None)

- `max_step=None` (default) is the single move it always was. A single blocking
  move of more than about 0.07 m can stop short of its target.
- `max_step=0.05` (metres, at least 0.005) performs a segmented move: the path
  from the current hand position to each target (the approach pose first, when
  `z_approach > 0`) is split into straight-line steps of at most `max_step`,
  each solved and executed in turn. If the hand is still more than 0.01 m from
  the final target, the final target is issued once more. One line reports it:

      [goto_pose] segmented move: 6 step(s) of <= 0.050 m; remaining distance to target 0.0042 m

- Use it for any transport longer than a few centimetres instead of writing
  your own interpolation loop.

## plan_grasp / plan_grasp_from_point_clouds / subsample_point_cloud

The same request returns the same result: the grasp service draws its
randomness from a generator seeded by the request content, and
`subsample_point_cloud` seeds its draw from the point cloud. A changed input
(another depth image, mask or point cloud) can still give different grasps, so
a policy should not depend on one particular grasp being returned.

## Step budget and scoring (when the cell prompt names success rule libero_latch)

The episode has a fixed budget of simulator steps. A trial counts as a success
if the task was achieved at ANY step within the budget (the standard LIBERO
rule), even if a later motion undid it. When the budget is used up, every
further action raises `ValueError: executing action in terminated episode`.
This is no longer recorded as a program error: the trial ends and is scored
by whether success was reached. A program should still stop acting once the
instruction holds (see `subgoal-verification.md`).
