# Semantic Streaming World v2

Explicit opt-in profile `sam2-semantic-native-v2`, method `sam2-semantic-v2`,
runtime mode `sam2-semantic-world-v2`. The common LIBERO-Pro conditions remain
unchanged: nonprivileged public RGB-D/proprioception and actual perception,
condition C, fixed local Qwen/native CC settings, development 51–65 with three
TOTAL charged attempts per seed, 900-second trials, 14-hour development and
five-hour frozen evaluation. No previous generated solution, object inventory,
development feedback or held-out feedback is supplied to a fresh cell.

## Authorship and declared differences

The solver independently authors policy and public scene decomposition in
`*_streaming_config.json`. Allowed top-level keys are `objects`, `parts`,
`cadence`, `notes`. Engineering freezes mode, camera, tracker and dependencies.
Each `objects` item has identity, public SAM3 prompt, optional min_seed_score and
optional selector `{axis: "image_x" | "image_y", rank: <nonnegative integer>}`.
The selector refers to the ordering of actual public candidate masks, never
simulator names or an externally provided object list. Each `parts` item has:

| Field | Meaning |
| --- | --- |
| identity | Stable episode-local ID; not a simulator identifier |
| parent | Another part ID or null; cycles and missing parents are refused |
| semantic | A solver-proposed label from public observations |
| tracked | True exactly for IDs in objects; false parents invent no geometry |
| hypotheses | Optional support scores for unknown/fixed/sliding/rotating/free |

Labels, connections and motion types belong to a hypothesis layer. Support
scores and SAM3 scores are engineering heuristics, not calibrated probabilities.
Measured geometry remains the currently visible surface, not full object pose,
hidden joints, contact, mass or evaluator state. A parent's mask excludes its
tracked descendants, preventing double-counted surface motion. Identity jumps
above 0.06 m per acquisition withdraw geometry; public SAM3 reacquisition
uses 3D association with a 0.06 m gate and 0.01 m ambiguity margin. These are
declared heuristics, not controller/action/recovery budget caps.

The method differences are semantic part hypotheses/revisions, identity-aware
tracking/reacquisition, optional focus interruption, public motion reports and
interaction-effect checks. No additional LLM service or replacement agent loop
is introduced: Qwen/native CC authors the scene-specific program/decomposition
and repairs it within the ordinary development loop; the frozen program
proposes runtime structural revisions from public evidence through this API.
This implementation does not claim automatic universal part discovery or
complete 6-DoF articulation estimation.

## Public policy helpers

| Helper | Contract |
| --- | --- |
| world_query(id, layer="hypothesis") | Read candidate meaning/parent/motion support; never measured truth |
| world_set_focus(ids) | Interrupt the next control checkpoint if a focused tracked part is unknown/stale |
| world_refresh() | Acquire another synchronized public RGB-D frame through actual SAM2 |
| goto_pose_report(position, quaternion_wxyz, z_approach=0) | Ordinary goto_pose plus calibrated public arrival verification |
| world_require_relation(name) | Continue only when a fresh authored public relation is known and true |
| world_begin_interaction(id, expected_delta, reference=None, tolerance=.015, hypothesis=None) | Record pre-action geometry and predicted relative visible-surface XYZ change; return interaction ID |
| world_finish_interaction(id, report, contact_relation=None) | Refresh evidence, verify the unchanged recorded action report and the authored contact relation, then compare predicted/observed effect |
| world_revise_structure(parts, objects, reason=..., evidence_ids=..., lineage=None) | Validate and commit a proposed graph after successful same-frame public SAM3/SAM2 initialization |

Existing world_snapshot, observed/predicted/relation queries, checkpoints,
relation verifiers and explicit reacquire_world remain available. Callbacks
and predicates continue to be authored by the policy; a constant-true contact
predicate is not a validated physical contact detector.

Motion report distinguishes public robot_base/panda_hand observation,
world/panda_hand observation and the requested world/TCP. TCP is reconstructed
using the inverse of the existing IK offset. Reports expose requested target,
effective IK target, position clipping, orientation fallback, final Cartesian
and joint errors, convergence and actual acquisition time. Stationarity and
normal RPC return do not establish arrival. Missing calibration or telemetry
returns unknown. The original IK fallbacks, motor iteration limit and API
return values are unchanged. No force/contact sensor is invented.

Execution not reached/unknown or a modified IK target yields execution_unverified.
Fresh valid execution without a fresh contact relation yields contact_unverified.
Missing post-action part observations yields unknown. Only valid execution,
fresh dependent contact evidence and new visual evidence can yield an effect
match/mismatch. A mismatch requests structural review and halves the selected
motion hypothesis's heuristic support; a match increases it by 0.1*(1-support).
Neither outcome automatically renames a part or chooses a different joint.

## Revision, cadence and audit

Structural changes occur while control is paused on the owning simulator
thread. Geometry and relation acquisition retain configured multi-rate cadence;
structural proposals are event-driven. There are no rendering threads, unbounded
camera queues or per-frame LLM calls, and acquisition-clock periods are not
wall-clock FPS promises. All latency remains inside normal watchdogs.

Revision requires an explicit reason and recorded public visual evidence IDs.
Split/merge lineage connects old IDs to new IDs, retired IDs cannot be silently
reused, and validation precedes tracker mutation. First version resets the
entire SAM2 stream on a structural revision. A backend failure leaves the old
graph intact but withdraws observed geometry and requires reacquisition.
Structural revisions clear dependent predictions/relations and leave pending
interaction checks unresolved. Each episode clears graph, evidence and updates.

The saved snapshot/journal records graph versions, lineages, supporting evidence,
identity conflicts, motion reports and interaction checks. Frozen evaluation
pins the update algorithm, authored policy/config and dependencies; runtime
state may change using that algorithm within one seed. No state/structure is
carried to another seed and no program is repaired during held-out evaluation.
New experimental cells use independent fresh output directories and hashes.
Stopped v1 cells retain all charged attempts/deadlines and remain incomplete,
rather than being relabeled as fresh v2 runs.

The initial v2 pilot uses one eight-GPU DLC node with two independent fresh
cells. The pair entry accepts one or two explicitly declared workers and checks
the actual DLC WORLD_SIZE/RANK against that declaration. Per-node Qwen TP4 and
the two perception/simulator assignments are unchanged; node count is recorded
as a deployment factor. This pilot is a subset of the ten-cell method design.
