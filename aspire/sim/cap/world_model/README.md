# Optional numeric world programs

`replay_trial.py` activates this package only when
`--args.world-model-config PATH` is explicitly supplied. Omitting the flag
retains the existing replay/generation behavior, configuration and lifecycle.

The JSON selects a separate protocol:

- `mode: capture`, schema1: the original passive Alphabet-soup P1 protocol,
  including its open-weights model gate. See
  [P1 instructions](../../.claude/libero/code-world/INSTRUCTIONS.md).
- `mode: opus46-diagnostic`, schema2: the explicitly user-authorized bowl-on-plate
  development pilot. It requires a frozen policy and numerical world program,
  exact diagnostic task/profile and seeds51–65. This does not relax the P1 gate.
  See the [pilot protocol](../../docs/experiments/code-world-opus46-bowl-pilot.md)
  and [frozen example](../../docs/experiments/code-world-bowl-opus46-frozen/live_config.example.json).
- `mode: opus46-scene-diagnostic`, schema3: the frozen multi-object Opus 4.6
  scene program evaluated once on each bowl development seed51–65. The adapter
  obtains current scene anchors from a frozen semantic catalog, records full
  predictions after motor calls, and meters bowl evidence at policy verification
  gates. The world and action policy remain byte-identical to the prior generated
  artifacts. This single-arm diagnostic does not reproduce the full image-inventory
  pipeline or establish a paired efficacy gain. See the
  [scene experiment](../../docs/experiments/scene-world-bowl-opus46-20260910/README.md).

For the live profile, the broker reads measured robot state after an exposed
motor call, executes `advance` and `predict`, commits the prediction, and only
then runs an approved object query. The comparison receives only requested
axes, precedes `assimilate`, and returns support, contradiction or unknown.
One anchor is accounted separately; additional query attempts, including
unavailable measurements, consume the configured cap. Denied queries do not.
Public `get_observation` also returns images, so sparse numeric queries are
not a claim of sparse camera acquisition.

The generated policy can call `world_verify`, `recovery_available` and
`use_recovery`. Its first two-arm pilot uses the identical policy source in
both arms and one common recovery routine. Numeric world workers receive JSON
and no simulator handle, images, evaluator results or provider credentials.
They have restricted imports and process timeouts for reliability; this is
not a hardened security boundary.

`live_tape.jsonl` records committed predictions, selected evidence,
comparisons, actual verification reads and recovery requests.
`live_manifest.json` separates task results from mechanism status and records
sensor counts/durations and source/evidence hashes. The campaign's independent
`scripts/libero/paired_bowl_supervisor.py` preserves all30 planned rows, refuses
to retry attempted rows, checks terminal artifacts and caps each episode at900s.

The current frozen world uses translation and fixed XYZ readouts. Its thresholds
are engineering choices, its visual median is not a material-point identity,
and its successful verification does not prove full task completion. The
pilot establishes whether numerical evidence can change execution; it does
not establish automatic representation discovery or held-out generalization.

## Relational engineering reference

[`reference_relational_world.py`](reference_relational_world.py) is the CC-authored
engineering reference for the schema4 relational interface. It implements
`initialize`, `advance`, `predict`, and `assimilate` through `FrozenPythonProgram`
using JSON state, fresh worker processes, and `math` only. It is exercised by
offline contracts and broker integration tests. It is not an Opus 4.6 generated
experiment candidate; the `opus46-relational-scene-diagnostic` provenance gate
remains unchanged, and this source must not be relabeled to pass that gate.

The reference keeps all supplied entities and metadata in one scene. The last
measurement stays separate from the propagated state. A grasp reference
calibrates a nonzero hand-local offset from measured position and a normalized
WXYZ quaternion; it does not establish attachment. Subsequent evidence compares
the attached and free hypotheses before assimilation. Visible bounds retain
their own measurement frame and center so propagation does not apply the same
translation twice. Release preserves the object's current estimate instead of
moving it to the hand.

The transport condition records evidence and hypothesis IDs, representation
version, dependencies, and its validity scope. Failed motion or missing current
proprioception suspends applicability. Reference and relation evidence must
answer a query for the current frame; delayed evidence is logged but cannot
restore support invalidated by intervening actions. The broker also drops stale
references on new grasp attempts, release, failed steps, capture gaps, and
contradiction. Wrong-object observations cannot relocate or support the requested
object. Query attempts, including unavailable evidence, consume the cap; repeated
verification and denied queries are cached per frame.

Mass, contact, and hidden geometry remain unknown. Online representation
regeneration, entity identity merge/split, scene-wide adaptive query scheduling,
target placement/support verification, and calibrated probabilities are outside
this increment. These offline tests provide no new task success-rate result.

Run the reference and real-worker broker contracts from `aspire/sim`:

```sh
PYTHONPATH=tests python3 -m unittest test_reference_relational_world test_relational_scene_diagnostic
```

The separate `GeneratedWorldContracts` suite still requires the actual generated
candidate via `ASPIRE_RELATIONAL_TEST_SOURCE`; do not point it at the reference
to hide a missing candidate. See the
[revision and verification log](../../docs/logs/2026-09-10-world-code-revision.md)
for the complete regression command and results.
