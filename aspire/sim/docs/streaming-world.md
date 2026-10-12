# Multi-rate SAM2 streaming World

New comparative experiments on this line follow the
[common LIBERO-Pro protocol](libero-pro-common-protocol.md) and its
[machine-readable constraints](libero-pro-common-protocol.json). The generic
engineered World and SAM2 perception are declared method factors; shared C
inputs, task conditions, solver, budgets, oracle gate and scoring remain fixed.

`cap/world_model/World.py` is an optional engineering state model. It is separate
from historical generated seven-function worlds and frozen p1/ordinary profiles.
There is no new benchmark result or task solution here.

## Update and control path

The trusted LIBERO simulator publishes detached public robot observations after
each movement or gripper control step. `StreamingWorldBridge` updates the World
at three configured acquisition-clock cadences:

1. Robot proprioception: every checkpoint by default, without camera processing.
2. Objects: a synchronized public RGB-D frame goes through the actual
   `SAM2_streaming` camera predictor, retaining its temporal memory; masks are
   projected using that frame's depth, intrinsics and camera-to-world transform.
3. Relations: an authored verifier receives fresh synchronized robot/object
   facts on its cadence or after `request_world_verification()`.

After these updates the policy's checkpoint receives a detached JSON snapshot.
Returning `False` raises `MotionInterrupted` before the next control step;
`None` or `True` continues. An exception also stops further execution. This does
not establish that the task completed. The code executor records an uncaught
interruption as an error; a policy may explicitly catch it and perform its own
observation/reacquisition handling.

This implementation is **cooperative and synchronous**. Physics stepping,
observation acquisition and tracker inference remain on one owning thread.
Inference therefore adds wall-clock latency, and requested simulation-clock
periods are not a real-time FPS guarantee. No rendering thread races or unbounded
frame queues are introduced. Async inference and an independently timed actuator
controller require a separate design and validation.

## Evidence and lifecycle

- One World/tracker belongs to one episode and camera. Reset clears all facts,
  advances the episode identity, clears video memory and resets SAM2's frame
  counter. Reset settlement suspends checkpoints; explicit seeding is required
  before subsequent motion. Context exit and environment close remove callbacks
  and close the tracker, including after failure.
- Public frames are detached, read-only RGB/depth/calibration snapshots with a
  camera, episode, sequence ID and acquisition-clock timestamp. Duplicate,
  delayed, cross-camera and previous-episode frames are rejected.
- Visible-surface geometry carries acquisition time, processing time, stable
  identity, evidence IDs and age limits. Robot and camera evidence use distinct
  identifiers. Readouts are copied, including nested arrays converted to JSON.
- Empty/lost masks, insufficient valid depth or overlapping ambiguous tracks
  replace geometry with unknown. Backend failure withdraws all object facts and
  requires explicit reacquisition. There is no static-segmentation fallback.
- `visible_center` and `visible_bounds` describe measured visible surfaces, not
  object center of mass, full shape, contact or full 6-DoF object pose.
- Predictions have their own layer and no measurement evidence IDs. They never
  replace observations. Relation predicates require current synchronized
  post-motion facts; new dependency measurements invalidate an old verdict.
  Numeric/phase values cannot stand in for a boolean predicate. Measured `False`
  is a valid known fact; missing evidence is unknown.
- Stable SAM2 object IDs prevent silent renaming but do not prove that tracking
  has stayed on the correct physical instance. The mask IoU and point-count
  thresholds are configurable engineering heuristics, not calibrated confidence.
  Task-dependent contact/placement rules and robust semantic reassociation remain
  the policy/world author's responsibility.

## Saved-policy replay

Use a dedicated compatible environment with SAM2_streaming, its checkpoint,
LIBERO and the normal SAM3/GraspNet/PyRoKi services already prepared according to
the suite README. This documentation does not authorize starting a campaign.
Keep weights, logs and outputs outside Git. The selected source is checked;
an already-imported different `sam2` package is rejected.

Copy `env_configs/libero/sam2_streaming_world.example.json` outside the repository,
fill its external paths and public object descriptions, and choose a fresh output
directory. SAM3 initializes/reacquires each identity on the exact public frame
that SAM2 receives; missing or multiple candidates above the declared score
threshold are rejected. Refine the prompt from actual observations. A SAM3 score
is not a calibrated identity probability.

From `aspire/sim`, a replay command after normal experiment preflight is:

```bash
PYTHONPATH=/path/to/SAM2_streaming:/path/to/aspire-world \
  .venv-libero/bin/python3 scripts/libero/replay_trial.py \
  --args.suite libero_goal_swap --args.task put_the_bowl_on_the_plate \
  --args.trial 51 --args.replay-code scripts/libero/streaming_world_policy_example.py \
  --args.streaming-world-config /path/to/streaming-config.json \
  --args.config env_configs/libero/franka_libero_traced.yaml \
  --args.output-dir /path/to/fresh-output
```

The example demonstrates state updates during a home movement, not a solved bowl
task. No LLM generation occurs on this saved-policy route. It injects:

| Helper | Meaning |
| --- | --- |
| `world_query(name, layer="observed")` | Fresh read using current simulation time; use `layer="relation"` for a verdict |
| `world_snapshot()` | Detached current JSON state |
| `set_world_checkpoint(fn)` | Authored per-control-step response to updated state |
| `set_world_relation_verifier(fn)` | Authored relation logic, called with `(world, capture_time)` |
| `request_world_verification()` | Request new evidence and relation checking at the next checkpoint |
| `reacquire_world()` | Explicit same-frame SAM3 initialization of the original identities |
| `streaming_world` | The World object; no simulator handle is stored in it |

`streaming_world_snapshot.json` records the final public state beside the trial.
Use distinct output directories and existing seed/replay accounting for any
future experiment. The streaming config cannot be combined with frozen world or
ordinary budget configs, or silently enabled for fresh model generation.

For trusted custom integration, construct a `World(SAM2Streaming(...), ...)`,
enter `StreamingWorldBridge(env, world, ...)`, call `bridge.capture()` and
`bridge.seed(frame, identity_to_same_frame_mask)`, then execute ordinary public
movement APIs. The bridge owns simulator access; the World owns data only.

## Validation

CPU behavioral regressions are in `tests/test_streaming_world.py`: calibrated
moving-object geometry, missing/depth/identity failures, timestamps, separate
predictions, relation freshness, real production motor-loop checkpoint cadence,
interruption and episode cleanup. They use synthetic public frames and a fake
temporal predictor and do not establish robot task success. The real backend
supports `build_sam2_camera_predictor`, `load_first_frame`, `add_new_mask`,
`track`, `reset_state`; weights are not included.

## Proposed two-cell concurrency on an eight-GPU DLC node

Status (2026-10-10): resource and source inspection only; no two-cell benchmark
or DLC job has been started. Actual DLC execution requires the user's explicit
confirmation. This is a deployment proposal, not a validated throughput result.

Share one fixed Qwen service. Prefer two separate perception/IK service stacks,
native CC sessions, simulators, World objects and SAM2 temporal predictors. This
uses all eight GPUs and avoids cross-cell waiting on the perception semaphores.
The proposed layout preserves the current Qwen GPU assignment:

| GPU | Role |
| --- | --- |
| 2–5 | Shared Qwen, TP=4; unchanged model, xhigh, 1M context and 64K output |
| 0 | Cell A SAM3 and GraspNet |
| 1 | Cell A simulator and its own SAM2 predictor |
| 6 | Cell B SAM3 and GraspNet |
| 7 | Cell B simulator and its own SAM2 predictor |
| CPU | Separate Pyroki IK services and per-cell agent processes |

The deployed Qwen startup log reports 2,212,451 KV-cache tokens and an estimated
maximum concurrency of 2.21 at 1,000,000 tokens per request. Its configured
`max_num_seqs=4` is a scheduler setting, not a guarantee that four maximum-length
requests fit or that two cells finish twice as fast. The two workers' requests
and their coordinators compete for that same cache and compute capacity.

SAM3 and GraspNet each serialize inference with a service-local semaphore; these
two semaphores do not serialize SAM3 against GraspNet. An alternative shares
both services on GPU 6 and places the two simulators/SAM2 instances on GPUs 0
and 7, leaving GPU 1 spare. That seven-GPU layout introduces cross-cell request
waiting and is not the preferred first validation. Never share a SAM2 predictor
or World between cells: both retain episode state. Both layouts are proposals.

Use distinct OS users, HOME/Claude config directories, output directories,
ledgers, selections and frozen bundles. Each cell independently keeps seeds
51–65, three TOTAL charged executions per seed, the 900-second trial watchdog,
14-hour development deadline and five-hour evaluation deadline. Shared services
must remain alive until both cells finish; one cell's failure must not terminate
its peer. Record concurrency as a deployment factor for comparisons.

The current DLC entry is still under engineering acceptance for one cell. A
node-level supervisor that owns the shared services and two independent native
campaigns must be implemented and verified before adopting this layout. Check
model queueing/preemptions, peak GPU/CPU memory, shared perception latency and
per-cell trial/phase timeouts in a declared concurrency validation. Program
executions in that validation are charged to their cells; they do not provide
free task attempts or permit changes to the common model or budget settings.
