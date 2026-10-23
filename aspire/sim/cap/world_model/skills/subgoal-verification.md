---
name: libero-fix-loop-subgoal-verification
description: Task-independent contract for confirming every subgoal from observations, retrying differently when a subgoal is not confirmed, and stopping once the instruction holds. Read before writing the initial program of a cell whose prompt includes the subgoal verification contract.
---

# Subgoal verification

A command that returned without an error says only that the arm or gripper was
commanded. It does not say the object moved, was grasped, ended up where you
wanted, or that a part of the scene changed state. Every policy in this cell
checks each subgoal from fresh observations before relying on it.

## 1. Write the subgoals down

From the runtime task language, list the subgoals in order, each as an
observable condition. Typical kinds (use the ones your instruction has):

- an object is held: grasped and lifted clear of its support;
- an object is at a place: above, in or on a region;
- an object is released: the gripper is open and the object rests where intended;
- a part is open or closed: a hinged or sliding part moved to its end position;
- a device is in a state: switched on or off, turned, pressed.

For each one, write in the findings which observation confirms it and what
value or change you expect. Unknown is not confirmed.

## 2. Confirm from observations

Use only the public API (`get_observation`, segmentation, depth, point clouds,
the wrist camera). Generic signals:

- **Before/after comparison.** Keep the image (or a crop of the region you
  acted on) from just before the action and compare it with the one after.
  No change in the region of the action usually means the action did not
  take effect.
- **Appearance change.** A change of colour, brightness or texture of the
  part you acted on, measured in a fixed image region, before versus after.
  Calibrate what "changed" means on a trial whose outcome you know.
- **Displacement.** Object position from segmentation plus depth
  (`mask_to_world_points`, bounding box); compare it with its position before
  the action and with the intended place. A held object rises with the hand.
- **Height and depth.** The height of the object above its support; the depth
  change at a place where something should now be (or no longer be).
- **Gripper opening.** After `close_gripper`, the normalized opening in
  `robot_cartesian_pos[-1]` / `robot_joint_pos[-1]`: fully closed means
  nothing is between the fingers; stopped partly open means something is.
- **Reach.** Compare the reached hand pose (`robot_cartesian_pos[:3]`) with
  the commanded one: a move can stop short.
- **Wrist camera.** A close-range view of the grasp, the opening or the place.

Re-observe after moving the hand out of the way if it hides the object.

## 3. Not confirmed: change the approach

When a check fails, do not continue with the next subgoal on top of an
unconfirmed state. Retry the same subgoal with a different approach:
re-perceive the target, choose another contact point or grasp, approach from
another direction or height, push or pull along another line, use shorter
segmented moves. Bound the retries (for example 2-3 per subgoal) and print
each attempt with what the observation showed, so the trial log explains
itself.

## 4. Stop when the instruction holds

As soon as every subgoal is confirmed, return from the program. Do not keep
moving, tidy up or re-check by acting: extra motion can undo the result. The
episode has a fixed step budget; once it is used up every further action
raises an error, so a program that keeps acting wastes the budget it may need
for a retry.

## 5. World and policy agree

Your world's goal clauses (`done()`) should check the same observable
conditions as the policy's confirmations. Each development trial reports where
the world's final judgment disagreed with the simulator
(`judgment_calibration`): a seed listed under `false_accept` means the
definition accepts a state that is not the goal. Fix the definition from what
that trial's observations showed.
