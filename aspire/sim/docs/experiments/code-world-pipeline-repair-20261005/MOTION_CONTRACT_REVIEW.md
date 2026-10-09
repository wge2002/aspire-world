# Bounded motion-contract review

Read-only engineering finding, not proof of the cause of every failed grasp.

The prior frozen bowldrawer runtime and engineering checkout contain identical bytes for:

- `cap/integrations/franka/libero_reduced.py`: `fee6d84b7d5dbb0a7cf36b9d58d6dd52db606256302f539ab9105bd1912ef17b`
- `cap/envs/simulators/libero.py`: `690c31e355d0d287262d0c316f73e8391f6e468299a790ef4a84206089d508ac`
- `cap/serving/launch_pyroki_server.py`: `1f8de94143a1d743af257f0c0c0013faf96dff6f21be3c824496d9c97a7e2741`

`solve_ik` documents the target as `panda_hand`, but transforms the requested position by `R(quaternion) @ [0, 0, -0.1]` before passing it to the IK service targeting `panda_hand`. `goto_pose` uses this function. Meanwhile, `get_observation` documents `robot_cartesian_pos` as `panda_hand`; its implementation derives a transformed frame from `gripper0_eef` using a -0.107 m translation and a pi/2 Z rotation. Consequently, comparing an unqualified requested grasp position directly against the observed hand position is not established as a comparison of the same reference point. The documented target-point contract needs verification/clarification before calling every such residual a control failure.

Also, `move_to_joints_blocking` exits after the existing max_steps even if joint tolerance is not reached, and returns None; `solve_ik_with_convergence` checks joint-iterate stability, not Cartesian residual. These are limits of existing feedback, not independently demonstrated causes of the old failure. Do not change motion semantics, add hardcoded offsets to generated policies, or claim that a static source review proves the 0.149 m residual is fully explained.

Within phase 1's existing API-contract review scope, CC should choose the smallest evidence-backed generic clarification/fix and test it, or record the remaining uncertainty. Preserve nonprivileged observations and avoid task-specific guidance or old policy inputs to fresh solvers. The current priority remains the diagnostic execution patch and real-Qwen compatibility completion; this note must not trigger broad robotics refactoring.
