# Visible held geometry and support placement

Engineering note (2026-10-19, wine repair phase 2). Scope: `cap/world_model/held_geometry.py`,
`rigid_tracking.py` (additive), the new pure-geometry `held_surface.py`, and their exports under
the opt-in checked-execution contract (`streaming_replay.py`). Nothing here reads simulator
state, assets or the evaluator; every threshold is a declared heuristic, not a calibrated
probability. A consistent relation is not grasp, contact, support or task success.

## Why

The previous `HeldGeometry._evaluate` compared the median of the *visible* points in the TCP
frame with the bound median and latched `slip_detected` when it moved. For a dark or
rotationally symmetric object, a view change, partial occlusion or a different visible face
moves that median without any relative motion (phantom slip), while widening the tolerance
(.12 m / 1 rad in one development cell) hid a real 7 cm drop. Contour features also slide along
the silhouette and corrupted the rigid estimate (a 1 rad "rotation" with 1.4 cm centre drift).

## Held relation: what is measured, predicted and unknown

Per synchronized measured frame (object frame and public robot pose within
`cadence.relation_max_skew`):

1. **Correspondences (measured).** Forward/backward-checked optical-flow features *inside the
   mask eroded by `EDGE_MARGIN` = 2 px* (occluding-contour features are not the same physical
   point) are taken from the previous synchronized held frame, expressed in each frame's own TCP
   frame and robustly registered (`rigid_registration`, ≥ 8 inliers, ≥ 70 %). A rigidly held
   object gives the identity. The chained product since the anchor is the relative drift `D`.
   `registration_uncertainty` gives a first-order covariance from the inlier geometry
   (`H = Σ |q|²I − qqᵀ`, noise floor 2 mm), propagated along the chain.
   * A rotation component is **observable** only if its 1-σ ≤ `OBSERVABLE_ANGLE_STD` (0.05 rad);
     e.g. rotation about the line of nearly collinear features is unobservable and never judged.
   * **Violation** (→ `slip_detected`, `cause="rgbd_correspondences"`): an observable rotation
     component > `angle_tolerance + 2σ`, or reference-point drift > `position_tolerance + 2σ`.
   * Otherwise **consistent**; when this chain registered the frame it alone decides, and the
     visible-centre median shift is only the diagnostic `center_drift`/`relation.center_shift`.
2. **Visible surface only (not a measurement of relative motion).** Without a registered chain
   (low texture, occlusion, too few interior features, nothing observable) the relation is
   `partial` when the visible-surface signature (sorted PCA spans) is similar or the visible
   centre lies inside the bound visible extent + tolerance, else `undetermined`. A cylinder
   showing another face has identical spans and a shifted median, so neither similarity nor a
   centre shift ever declares slip or verifies co-motion. The chain re-anchors at this frame.
3. **Unknown.** Occlusion, missing depth, unsynchronized/stale pose, tracking loss, identity
   jump or reseed: `last_measurement.status="unknown"`, nothing fabricated.

Other endings: measured gripper opening increase > 0.1 → `released`; measured closing beyond the
bound opening by > 0.1 → `slip_detected` with `cause="gripper_closed_further"` (object left the
fingers); structure revision → `invalidated`; episode reset clears everything.
**A slip cannot be erased by rebinding:** `world_bind_held` raises for an identity whose binding
ended in `slip_detected` until a measured gripper opening (release) has been observed after it.
An identity interruption breaks the chain permanently for the since-bind relation
(`full_chain_since_bind=False`); later correspondence verification is labelled `*_since_reanchor`
because relative motion during the gap was not measured.

## `world_held_state(identity)` fields (additive; old fields kept)

| field | meaning |
|---|---|
| `status` | old values plus `relation_partial` (fresh, visible-surface-only compatibility) and `relation_unknown` (fresh, undetermined) |
| `co_motion_verified` | CURRENT correspondence-measured consistency after hand excitation (≥ 1 cm or 0.1 rad since binding), fresh and uninterrupted. Never from visible-surface similarity, predictions or robot commands |
| `verified_scope` | `se3` (3 observable rotation axes + translation, chain since bind), `partial_se3` (some rotation unobservable), with suffix `_since_reanchor` after an interruption; `None` when not currently verified |
| `co_motion_verified_historical` | verified at some earlier frame of this binding (history only) |
| `relation_status` | `consistent` / `partial` / `undetermined` / `unknown` (not fresh) |
| `partial_geometry_consistent` | fresh and `consistent` or `partial`; usable visible geometry, NOT rigid evidence |
| `measured_relation` | `layer="observed"`: current TCP-frame features (`visible_center`, `visible_bounds`, `lowest_visible_point`, `long_axis` + status, `visible_points_tcp` ≤ 256 points, `quality`), the judged `relation` (source, per-axis rotation std/drift/observable, `position_drift`/`position_std`, chain epoch/steps/inliers) and evidence IDs; `None` unless fresh |
| `prediction` | `layer="predicted"`: features composed with the current public hand pose; never a measurement |
| `full_chain_since_bind`, `rigid`, `cause`, `previous_binding_status` | chain/ending provenance |

Policies may act on `partial` geometry (e.g. a dark bottle with too few interior features) with
their own measured evidence (closing width, visible lift, re-observation), but must not report it
as rigid or full-SE(3) evidence.

## Support placement helper (`held_surface.py`)

`support_placement_candidates(object_points_tcp, tcp_pose, support, *, body_axis_tcp=None,
radius=None, radius_source=None, view_direction_tcp=None, along=None, rails=None,
target_point=None, tilts=(0.,), clearance=0., workspace=None, min_points=30,
coordinate_frame="robot_base")`

* Frames: object points in the TCP frame (`measured_relation.features_tcp.visible_points_tcp`);
  `tcp_pose` 4×4 robot_base; `support = {"normal", "centroid"}` robot_base with the normal into
  free space (e.g. a plane from `geometry_from_points`). Quaternions WXYZ. Axis sign arbitrary.
* Body axis: caller value, else visible-PCA long axis (`unknown` if ambiguous).
* Cross-section: `cylinder_cross_section(points, axis, radius=None, view_direction=None)` — free
  circle fit accepted only for a visible arc ≥ 60° and rms ≤ 4 mm; a caller radius must name its
  measurement (`radius_source`) and needs a view direction to put the centre behind the visible
  surface. Otherwise `unknown`.
* Height is tangency: `n·lowest = n·support + clearance`, with the extent below the body centre the
  larger of the measured visible points and the cylinder model `r·√(1−(n·a)²) + (L/2)|n·a|`
  (`L` = visible axial span, a lower bound). Two rails parallel to the body that bracket the target
  cradle it at `√(r² − (s/2)²)` above the rail plane; `r ≤ s/2` is rejected.
* Orientation: minimal rotation of the current TCP taking the measured body axis to each
  candidate direction (`along`, rail directions, else the current heading projected on the
  plane), both signs, each requested tilt. Ranked by workspace box flag then rotation.
* Output: `status="candidates"` with `tcp_position`, `tcp_quaternion_wxyz`, `body_center_world`,
  `lowest_point_world`, `extent_source`, `mode`, `workspace_box` (box flag only), all
  `layer="predicted"`, plus `ambiguity` and `caveats`; or `status="unknown"` with a reason.
  Reachability is verified afterwards with `goto_pose_report`/`solve_ik`; contact and success are
  never claimed.

Both functions are exported only under the opt-in `execution_contract` as
`support_placement_candidates` and `cylinder_cross_section`.

## Identity

Unchanged: `measured_motion_association` (forward/backward flow, ≥ 8 matches, ≥ 0.7 coherence,
rigid registration, unique candidate, original gate). No nearest-mask, prediction, command,
segmentation score or contour/ICP similarity makes an identity known. `flow_correspondences`
gained an opt-in `edge_margin` whose default 0 keeps that behaviour.

## Tests

`tests/test_held_geometry.py` (same-feature occlusion/view change without phantom slip,
same-span-other-face counterexample, textured left-behind slip, gripper-closure loss,
textureless translation only partial, identity interruption, rebinding after slip, predictions
never promoted, collinear features unobservable) and `tests/test_held_surface.py`.
