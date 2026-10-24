# Checked execution contract `checked-motion-v1`

Engineering-authored, opt-in runtime contract for the semantic v2
(`sam2-semantic-native-v2`) and articulation v3 (`sam2-articulation-qwen-v3`)
streaming World profiles. It is a method factor frozen with every bundle
(`ENGINEERED_WORLD_FILES` in `scripts/libero/streaming_fixloop_protocol.py`),
not a task strategy library: it contains no task geometry, no object
inventory, no grasp/placement recipe and no solution from any other cell.

Sources: `cap/world_model/execution_contract.py`,
`cap/world_model/geometry_queries.py`, `cap/world_model/held_geometry.py`,
`cap/world_model/held_surface.py`,
`cap/world_model/streaming_replay.py`, driver hooks in
`cap/envs/simulators/libero.py` (`arm_motion_count`,
`add_gripper_gate_callback`) and `FrankaLiberoApiReduced.workspace_contract`
in `cap/integrations/franka/libero_reduced.py`.

## Enabling it (engineering only)

The case file opts in; the authored streaming config cannot:

```json
"streaming": {"execution_contract": {"version": "checked-motion-v1"}, ...}
```

* `validate_case` accepts exactly `{"version": "checked-motion-v1"}` and only
  with the semantic v2 or articulation v3 profile.
* `materialize_config` copies the case value into
  `streaming_world_config.json`; the authored config may set only
  `objects`, `cadence`, `notes` (+ `parts`, `joints` per profile), so an
  authored `execution_contract` key (including `null`) is rejected by
  `streaming_config_errors` and is never read by materialization.
* `identity()` gains an `execution_contract` field only when opted in, so
  every earlier case identity, ledger and frozen bundle reproduces exactly.
* Absent key: no new helper is injected, no gripper gate is installed,
  `goto_pose_report` keeps its legacy behaviour and `execute_modified=True`
  raises `ValueError`.

## What it enforces

A gripper change (grasp or release) depends on where the arm is, so it must
not follow a pose action whose requested arrival failed, was modified (software
workspace clip or orientation fallback), is unknown, or was interrupted.

Trusted facts only: the driver's monotonic `arm_motion_count` (incremented by
every commanded arm motion, whichever API issued it), the report of the
contract's own checked motion, and a driver gripper gate called *before* the
commanded gripper fraction changes. The gate:

* allows a command equal to the current commanded fraction (no change);
* allows any change while the arm has not moved since the episode started
  (state `initial`);
* allows a change after a checked `goto_pose_report` with `reached=true` and
  `target_modified=false` **and** no arm motion since (state `verified`);
* otherwise raises `ExecutionContractError` (a `RuntimeError`) before the
  gripper moves, journals `gripper_refused` and counts a refusal.

Any arm motion outside `goto_pose_report` (plain `goto_pose`,
`move_to_joints`, `goto_home_joint_position`, ...) after verification turns
the state into `unchecked_motion`. The contract never widens workspace bounds
or tolerances, never accepts a fallback target as arrival, and never asserts
grasp, contact or task success. It is cleared on every episode reset.

Required order: **reach first, then grasp/release** — call
`goto_pose_report(...)`, confirm `reached is True and target_modified is
False`, then `close_gripper()` / `open_gripper()`. On refusal, replan the pose
and re-check; do not retry the gripper blindly.

States (`CONTRACT_STATES`): `initial`, `in_flight` (an exception interrupted
the checked motion), `verified`, `not_reached`, `target_modified`,
`refused_target_modified`, `ik_failed`, `unknown`, `unchecked_motion`.

## Policy API (injected only when opted in)

All positions are robot_base metres; quaternions are one WXYZ quaternion;
angles are radians (see `docs/robot-frame-contract.md`). Targets name the API
TCP, not `panda_hand`.

### `goto_pose_report(position, quaternion_wxyz, z_approach=0.0, *, execute_modified=False) -> dict`

Changed behaviour under the contract:

* legs `approach` (only if `z_approach > 0`, target = position + z_approach
  along robot_base +z) then `final`; each leg is solved and measured
  separately, and a later leg is not executed after a failed leg;
* a leg whose IK clipped the position or used an orientation fallback is
  **not executed** (`status="refused_target_modified"`, `executed=False`)
  unless `execute_modified=True`; executing it still leaves
  `target_modified=true`, which never permits a gripper change;
* IK failure gives `status="ik_failed"`, `reached=False`, `executed=False`;
* the report keeps the legacy `motion_report` fields (`requested`,
  `reached`, `status`, `target_modified`, `ik`, `position_error`,
  `angle_error`, tolerances 0.015 m / 0.15 rad, ...) plus `leg`,
  `workspace` (per-axis clip diagnostics of the last leg), `legs` (summary of
  each attempted leg), `final_requested`, `z_approach`, `execute_modified`,
  `action_id`, `captured_at`, `episode_id` and `contract` (the
  `motion_contract_status()` value after this motion);
* control errors (e.g. a World motion refusal) propagate; the contract then
  stays `in_flight`, so earlier verified evidence is discarded.

### `motion_workspace() -> dict`

`FrankaLiberoApiReduced.workspace_contract()` (target reference, units, TCP
offset/definition, `workspace_low`/`workspace_high`, orientation fallbacks
and their meaning) plus `contract`, `position_tolerance=0.015`,
`angle_tolerance=0.15` and a `checked_motion` description. The workspace box
is a software clip in `solve_ik`, not a measured reach limit.

### `motion_contract_status() -> dict`

`{state, action_id, reason, version, gripper_permitted, refusals,
required_action[, motion_count]}`; `required_action` is `None` when a gripper
change is permitted.

### `world_visible_geometry(identity, *, near=None, radius=None) -> dict`

Visible-surface geometry of a **tracked** part from its current measured mask
(back-projected public RGB-D in robot_base). Raises `ValueError` for an
identity not in the current structure. Returns `status="unknown"` with a
`reason` for a `tracked:false` part, or when the part is not freshly known in
the current frame. Otherwise the `geometry_from_points` summary plus
`identity`, `layer="observed_geometry"`, `coordinate_frame`, `up_source`,
`frame_id`, `captured_at`, `evidence_ids`. `near` (XYZ) with `radius` (m)
restricts analysis to a local region.

### `geometry_from_points(points, *, up=<robot_base gravity-up>, near=None, radius=None, min_points=30, plane_threshold=0.004) -> dict`

Pure geometry of N×3 points in one frame (for example `mask_to_world_points`
output). Non-finite rows are dropped. Fewer than `min_points` points or
coincident points give `status="unknown"`. Known results contain `centroid`,
`median`, `bounds`, `principal_axes` (`long_axis` reported only when the
first/second std ratio ≥ 1.3, sign arbitrary; else `ambiguous`), up to 3
RANSAC `planes` (`normal` oriented toward `up`, `facing` up/side/oblique,
`height_along_up`, `visible_spans`), `support_candidates` (upward planes;
candidates, not verified support), `top_slab` (points within 0.01 m of the
highest visible point), `thresholds` and `caveats`. The default `up` is
gravity-up in robot_base from the public mounting calibration, or robot_base
+z when that calibration is unpublished (`up_source` says which). Visible
surface only: hidden extents, contact and success are unknown.

### `get_oriented_bounding_box_from_3d_points(points, up=(0, 0, 1)) -> dict` (base API, axis convention)

Not part of the contract switch; documented here because it is the usual
source of box axes. `center`, `extent` and `R` are returned unchanged from
Open3D. **The COLUMNS of `R` are the box axes**: `R[:, i]` pairs with
`extent[i]`; a row such as `R[0]` is not an axis and mixes all three. Added
keys (pure math in `geometry_queries.box_axes`): `axes` (rows, `axes[i] ==
R[:, i]`), `axis_convention`, `coordinate_frame` (the frame of `points`, e.g.
robot_base for `mask_to_world_points`), `up` (gravity-up in that frame; +z in
robot_base), `long_axis` (canonical-sign unit column of the largest extent),
`long_axis_index`, `long_axis_extent`, `elongation_ratio`,
`long_axis_status` (`known` only when largest/second extent ≥ 1.3; near-equal
extents are `ambiguous` with `long_axis` None; a degenerate box is `unknown`),
`long_axis_tilt_from_up` (rad, 0 = along `up`) and `long_axis_orientation`
(`vertical` ≤ 20°, `horizontal` ≥ 70°, else `oblique`; None unless known),
`reason` and `caveats`. A box fitted to visible-surface points spans only what
was visible: its extents and long axis are not the complete object's, and a
lying/upright label is a geometric reading of those points, not ground truth.

### `world_bind_held(identity, *, position_tolerance=0.02, angle_tolerance=0.15) -> dict`

Records, from ONE synchronized measurement (object frame and public robot
pose within `cadence.relation_max_skew`), the tracked part's visible features
in the TCP frame (rotation = panda_hand rotation, origin = API TCP; metres).
Raises `ValueError` for untracked/unknown parts, a stale or unknown object,
unsynchronized or missing robot pose, fewer than 10 depth points, or
non-positive tolerances. Returns `world_held_state(identity)`; initial status
`bound_unverified`. It anchors a held-relative RGB-D correspondence chain;
binding alone proves neither grasp nor co-motion. A previous measured slip
cannot be cleared by rebinding without a later measured gripper opening.

### `world_held_state(identity) -> dict`

`status` is one of `unbound`, `bound_unverified`, `consistent_stationary`,
`co_moving_verified`, `relation_partial`, `relation_unknown`,
`measurement_unknown`, `slip_detected`, `released`,
`invalidated`, with `valid`, `reason`, `features_tcp` (`visible_center`,
`visible_bounds`, `lowest_visible_point`, `long_axis`, `point_count`),
`last_measurement` (`measured` with `center_drift`, `hand_motion_since_bind`,
optional `rigid_drift`, or `unknown` with a reason), `measurement_status`
(`fresh` only when the last measurement is the current known object frame),
`measurement_age`, `co_motion_verified`, `co_motion_verified_historical`,
`identity_interrupted`, `relation_status`, `verified_scope`,
`partial_geometry_consistent`, `measured_relation` and, while valid,
`prediction` (bound or freshest
features composed with the CURRENT public hand pose; `layer="predicted"`,
never a measurement).

`co_motion_verified` is CURRENT verification: true only while the binding is
valid, `measurement_status` is `fresh`, identity is uninterrupted, and
current held-relative RGB-D correspondences verify consistency after hand
motion. Read `verified_scope`: `partial_se3` leaves components unobservable;
`*_since_reanchor` leaves relative motion during a tracking gap unmeasured.
`measured_relation` contains fresh visible points in the current TCP frame,
evidence, source and component uncertainties; it is absent without a fresh
measurement. `partial_geometry_consistent` means visible-surface compatibility,
and cannot establish rigid co-motion or contact.
`co_motion_verified_historical` records that co-motion was verified at some
earlier measured frame of this binding; it is history, not current evidence.
A valid binding that was `consistent_stationary` or `co_moving_verified` but
whose latest measurement is not fresh (occlusion, stale evidence,
unsynchronized depth/pose, identity interruption) reports `measurement_unknown`
with the reason; the labelled `prediction` remains. A later measured frame
with reliable matched physical features can verify current relative motion
again. Similar visible dimensions or a shifted median alone cannot do so.

Invalidation rules:

* held-relative correspondence drift on an observable component beyond its
  tolerance plus two estimated standard deviations → `slip_detected`;
  visible median shifts alone remain diagnostics, including equal-span
  symmetric surfaces viewed from another side;
* measured gripper opening increase > 0.1 → `released`;
* measured closing beyond the bound opening by > 0.1 → `slip_detected`;
* scene structure revision → `invalidated`;
* tracking loss, identity jump, reseed/reacquisition → `identity_interrupted`,
  rigid relation dropped, measurement `unknown` until a later measured frame
  is compared with the bound relation; the old measurement is never reused;
* occlusion, missing depth or unsynchronized pose → measurement `unknown`;
* episode reset clears all bindings.

`co_moving_verified` requires measured consistency after hand motion of at
least 0.01 m or 0.1 rad since binding. A consistent relation is not grasp or
contact proof.

Full field meanings and low-texture, ambiguity and uncertainty rules are in
[Visible held geometry and placement](visible-held-placement.md).

### `world_release_held(identity, reason="policy release") -> dict`

Ends a valid binding as `released` and returns its state.

### `tcp_target_for_feature(feature_tcp, goal_point, quaternion_wxyz) -> dict`

Pure geometry: the TCP position that places a TCP-frame feature (e.g.
`features_tcp["lowest_visible_point"]`) at `goal_point` (robot_base) for the
given TCP orientation: `{tcp_position, quaternion_wxyz, layer="predicted",
assumption}`. It is not a reachability, contact or success claim; pass the
result to `goto_pose_report` and re-observe.

### `support_placement_candidates(...)` and `cylinder_cross_section(...)`

Generic pure geometry over measured visible points, exposed when this
contract is enabled. They fit a circular body cross-section and compose
candidate TCP poses using the support normal, body offset and optional rails.
Candidates are predictions; insufficient geometry returns unknown. Neither
helper establishes reachability, physical contact, stability or oracle success.
See [signatures and evidence requirements](visible-held-placement.md).

## Limits

* Not a security sandbox: the gate guards the driver path the policy uses; the
  protocol's authored-code checks forbid reaching env internals.
* Thresholds are declared heuristics, not calibrated probabilities.
* The contract does not decide when to grasp, where to place or whether the
  task succeeded; the authored policy remains responsible for its strategy.
