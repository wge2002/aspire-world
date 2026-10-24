# Event-driven articulated World v3

Explicit profile `sam2-articulation-native-v3`, method
`sam2-articulation-qwen-v3`, mode `sam2-articulation-world-v3`. This is an
engineering implementation, not a task solution or a benchmark result. V1/v2
frozen runtimes, outputs and ledgers are unchanged. A DLC experiment requires
the user's further approval; implementation/RS validation is authorized.

## Representation and evidence

V3 keeps semantic v2's observed/predicted/relation/hypothesis separation,
SAM3 initialization, SAM2 temporal masks, freshness, identity review,
transactional graph revisions, execution/contact guards and episode resets.
The cell independently authors its scene decomposition from public input.
Optional `joints` entries have `child`, `parent`, `family` with registered
families fixed/sliding/rotating/free/unknown. A root may explicitly have
parent:null with free/unknown only; it has no articulated reference or fitted
joint. An untracked reference parent
provides no geometry and cannot establish a fitted joint.

Public RGB-D correspondences, using texture features inside both temporal
masks, forward/backward optical-flow checks and robust rigid registration,
estimate each part's motion relative to its initial visible geometry. These
are estimates, not full object poses in hidden body coordinates. Parent masks
exclude tracked descendants. Missing depth, insufficient/collinear features,
identity conflicts or failed registration break the motion reference epoch;
no fitting spans the gap. Motion from mask centroids is never a rigid pose.

Synchronized parent-relative transforms feed the fixed model library:

- fixed: relative transform remains constant; stationary observations alone
  cannot establish that a joint is physically fixed.
- sliding: `p(q) = p0 + axis*q`, constant relative orientation.
- rotating: `p(q) = c + R(axis,q)*(p0-c)`, where the component of `c` along
  the axis is unobservable and conventionally zero.
- free/unknown: no constrained prediction; further evidence may be needed.

The numerical engine fits axes/axis points from measured motion. The final
sample validates parameters fitted to preceding samples. At least four
distinct acquisition times and informative excitation are required. Multiple
valid families remain unknown. Qwen's preferred family cannot override a
contradicting numerical fit. Observed displacement/angle ranges are recorded;
they are not physical joint limits. Predictions are a separate layer. Outside
that range they default to unknown; explicit `extrapolate=True` returns an
unvalidated extrapolation, never a measured fact or a joint-limit claim.
Mass, friction, force, latch mechanisms,
complete articulated shape and true contact remain outside this model.

Numerical heuristics (not task/action/query/recovery budgets): 200 texture
features/part, <0.7 pixel forward-backward error, 64 deterministic RANSAC
samples, >=8 noncollinear correspondences and >=70% inliers within 0.006 m;
64-frame motion window; >=0.01 m translation or >=0.1 rad rotation excitation;
sliding requires <=0.01 rad observed relative rotation, so a small visible arc
at a long lever arm is not silently promoted to a slider;
fit/validation <=0.008 m and <=0.035 rad residual. A previously accepted fit
is reviewed when new residual exceeds 0.012 m or 0.06 rad. These thresholds
and implementation are frozen method factors, not calibrated probabilities.

## Qwen online review

The initial public graph/model hypothesis, identity conflict, credible
interaction-effect contradiction and numerical model contradiction queue
review events. Only queued events invoke Qwen, on the owning simulator thread
with control paused. There is no per-frame LLM loop or separate rendering
thread. Geometry/relation cadence remains configurable as in v2.

The trusted reviewer receives the current public image, detached World
snapshot, event evidence and recent public execution reports. It receives no
simulator handle, hidden object list, asset/joint definitions, evaluator output,
held-out feedback, external credentials or previous episode's state. It uses
the same loopback Qwen3.8-Flash-Next service, xhigh, configured 1M/64K, adaptive
thinking and recorded request/usage/duration. Online inference is an explicit
agent-execution difference from v2. Its latency belongs to the 900-second
trial watchdog; development/test clocks and retry accounting stay unchanged.

The response is structured JSON data: defer, retain, select_models, reacquire,
or revise. Only registered fields, existing public evidence, valid graphs and
model families are admitted. Qwen cannot supply executable code or authoritative
numeric geometry. Graph revisions require current SAM3 masks and successful
SAM2 reseeding before commit. Model-family updates remain hypotheses until
numerical validation. Invalid proposal data is recorded with usage/hash and
rejected while an unchanged fresh World remains available; it never counts
as an accepted update. Service outages or invalidated tracking interrupt
control. Focused unknown parts still interrupt at the normal checkpoint.
Rejected proposals are omitted from subsequent inference packets. Defer awaits
further evidence; the policy can inspect the review and obtain observations.

Phase-2 repair (event-local review): the packet carries the complete current
graph, current observed/robot/relation facts, current joint edges/fits and
part motion, the last four motion reports, interaction checks citing the event
evidence, and ONLY registered evidence IDs cited by the events, current facts,
fits and those checks; revision/model/receipt history is omitted. A reply may
cite only these IDs. Request parameters (Qwen3.8-Flash-Next, xhigh, 1M/64K,
900 s) are unchanged and no token/action cap is added. Initial model reviews
with no informative measured motion (>= 0.01 m or >= 0.1 rad) are marked
`deferred_no_call` with journal `world_review_deferred_no_call`
(`qwen_called: false`, no usage/duration) and re-armed
(`world_review_rearmed`) when such motion is registered. Each actual call is
journaled once: `qwen_world_review` with usage/duration/hashes and
`outcome` accepted | action_rejected (valid World unchanged) |
grounding_failed (World disarmed, control interrupted); malformed data is one
`qwen_world_review_rejected` (`data_rejected`); a transport/service failure is
`qwen_world_review_outage` and interrupts control.

Public helpers: `world_query_joint(child)`,
`world_predict_joint(child,q,extrapolate=False)`, `world_review_pending()` plus all v2 helpers.
`world_begin_joint_interaction(child,delta_q)` records a validated model's
expected parent-relative pose; pass its ID to the existing
`world_finish_interaction(id,report,contact_relation=...)`. The check compares
both translation and rotation, so a knob that failed to rotate cannot pass
merely because its visible centre stayed still. Fresh tracked parent/child
motion, unchanged structure, verified execution and fresh authored contact
evidence are required. Only a credible pose mismatch queues joint-effect review.
An accepted graph/model-family change at a motor checkpoint interrupts the
ongoing trajectory before the next step; the policy may reobserve and replan.
`world_revise_structure` additionally accepts optional `joints` in this profile.
Journals/snapshots retain structure/model revisions, reasons, evidence,
request/response/prompt hashes, inference usage and parameter validation.

## Freeze and validation

The native profile freezes authored policy/config, model templates, tracking,
review prompt/schema, numeric thresholds, service identity and dependencies.
Evaluation uses one frozen program on all seeds 1–50. State/parameters may
update through its fixed rules within one episode; code/prompt repair,
per-seed program choice and cross-seed learned structure are forbidden.
The common nonprivileged C inputs, oracle outer gate, seeds51–65/three total
charged trials each, 14h/5h clocks and four outcome classes remain unchanged.

Behavioral tests distinguish registration quality, candidate ambiguity,
temporal prediction validation, false execution attribution, response rejection,
event frequency, model/graph revisions, lifecycle and frozen profile integrity.
Real-service engineering smoke is reported separately from task success and
complete held-out benchmark evidence.
