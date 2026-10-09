# Executable C world r1

This is a fresh three-task C foundation study; each independent C cell authors its
first world and policy for its own task, with no prior C bundle. Use public
LIBERO API observations, numpy and Python, without simulator ground truth,
assets, environment/API handles in the world, service credentials, or high-level
strategy MD. Read the factual API reference and public API source for exact
signatures and reference conventions.

The module `world` is loaded once per episode from the exact saved world bytes.
Required functions (all are authored by you):

```python
def predict(call): ...   # called BEFORE the public API executes
def observe(event): ...  # called automatically with its actual public return
def simulate(call): ... # executable effect/return for the SAME API in rehearsal
def update(obs, last_action): ...  # optional policy-derived evidence updates
def query(name, **kwargs): ...   # policy consumes finite JSON answers
def snapshot(): ...      # pure finite JSON dict, no image arrays
def done(): ...          # finite JSON tri-state goal, see below
```

`call` is `{id: int, function: str, args: tuple, kwargs: dict}`. `predict` may
return your finite/array-valued prediction or raise the injected `Unsupported`
exception; it cannot see the future response. Prediction is logged before the
real call. Keep commands, predictions, observed state and uncertainty separate.

`observe` receives `{id, kind, binding, call, result, error, evidence_id}`. It is
invoked automatically for ALL public API calls; `get_observation` includes its
full public arrays, not just the log summary. `kind` is `measurement` for that
API, `command_receipt` for motor calls, otherwise `derived_public_result`.
Successful `get_observation` calls get an evidence ID equal to the API call ID.
Motor commands NEVER get a measurement ID. A returned pose command/None does not
measure where an object or end effector went. No evaluator truth enters observe.

For policy-computed quantities (e.g. from public point clouds), use
`world.update({"evidence_ids": [world.evidence_id()], "values": {...}}, last_action)`.
`world.evidence_id()` returns the most recent actual observation ID or None.
Missing/invalid IDs are rejected. Preserve the meaning of the evidence: citing a
real frame cannot make a commanded location an object observation. Implement
your numerical/relational inference in world using these inputs; unknown or
occluded measurements remain unknown, not zero or success. Include fitted
parameter values and development provenance in the frozen world source; API
constants are allowed according to their actual documented/source semantics.

`done()` returns `{"verdict": "true"|"false"|"unknown", "evidence_ids": [...],
"reason": "..."}`. It must evaluate your generated task goal from world state and
public evidence. The runtime downgrades definite judgments to unknown when the
IDs are invalid or none is newer than the last motor command. Freshness is only
an evidence check, not a guarantee that your predicate is semantically correct.
Do not replace the predicate with phase=complete. This study keeps done() observational; do not add a new goal-driven recovery or
termination controller. That optimization is deferred. The runtime
also records a final judgment separately from the evaluator's task label.

`world.self_eval_enabled` is the frozen arm switch. In no_self_eval, done() is
disabled and never evaluated by the runtime. Keep grounded low-level state,
ordinary action completion and useful world queries; do not reintroduce the
same goal as a differently named self-evaluation function.

## Same policy, three API bindings

In real/shadow mode, the real API executes; your prediction is recorded before
it, and observe gets its actual response. Normal public return values and policy
source are unchanged. The world never invokes sensing or motion itself.

In rehearsal, the SAME policy executes with public calls bound to simulate.
Pure math helpers are extracted unchanged from the factual API source. A previous
development tape, when available, supplies only its initial public-call prefix
BEFORE its first motor command. That prefix describes a recorded scenario, not
the unseen current seed. After it, simulate must implement effects and predicted
returns. It may raise `Unsupported("reason")` for unavailable semantics; never
pretend old future images are measurements of a new motion. With no tape, your
model can report unsupported initial scene and the first real trial proceeds.
Rehearsal goal outputs are labelled model_goal, not real self-evaluation labels.

In replay, API calls and exact arguments must match the prior development tape
in order. At the FIRST changed call, replay stops without releasing any old
future response. An exact matched prefix can test logic and world interpretation
of those calls only. Unsupported/divergent history does not predict task failure.

Do not branch the policy on the execution binding, read tape files from the
generated program, or add hidden simulator clients. World.simulate receives no
live API handles. The framework does not supply task-specific geometry,
thresholds, recovery policies or successful predicates. You author and revise
the semantics using development evidence.

The offline check command records candidate hashes, scenario tape identity,
errors/unsupported outcomes and elapsed compute separately from live attempts.
Inspect it before spending a live trial. Python errors in supported execution
reject admission without charging a simulator attempt; unknown/unsupported do
not prevent obtaining missing evidence in a real trial. No_rehearsal disables
this path entirely. Both ablations still use the same observation contract.

Artifacts beside each real trial: `judgment_world/events.jsonl`, `manifest.json`,
and lossless public `public_tape.jsonl` plus `arrays/`. Offline reports live in
the task's `attempts/offline/`. Keep simulator trials within seeds51–65 and the
same three TOTAL attempts per seed. Only the outer evaluator runs frozen seeds1–50.


## Foundation revision r1: one store, explicit uncertainty, auditable goals

Declare `FOUNDATION_REVISION = "r1"` at module top level. The runtime injects
`WorldState` before executing your module. Instantiate ONE `state = WorldState()`;
use it as the authoritative store for observations read by update/query/snapshot/done.
Do not maintain a second cache or a default geometry that queries silently read.
Retain parameter provenance and other metadata separately, but not duplicate facts.
The helper provides no task predicates, thresholds, object inventory or actions.

- `state.set(name, value, evidence_ids=[id], valid=True, reason="...", identity=None,
  layer="observed", reference_evidence_ids=[])` stores a finite JSON value.
  Empty/ambiguous masks, missing depth, uncertain object identity and failed
  measurement must set value=None or valid=False WITH the real observation ID.
  That update replaces the old value with unknown; it does not preserve stale success.
  A reliable measured False or zero can be known. Absence of a detection cannot by
  itself justify False or an invented zero coordinate. Document validity criteria.
- `state.query(name, fresh=True, identity=None, layer="observed")` returns a copy:
  status known/unknown, value, evidence_ids, reference_evidence_ids, identity,
  layer and reason. Unmeasured, stale or mismatched identity gives unknown/None.
  Queries, snapshot and predicates all read this store. Motion invalidates current
  claims until a new observation supports the particular fact; an unrelated new
  image must not refresh old object geometry.
- `state.snapshot()` uses the same freshness-aware accessors. Predicted and
  rehearsal values occupy separate layers and cannot overwrite observed facts.
  The framework puts rehearsal updates in the rehearsal layer automatically.
  Unsupported prediction/simulation is honest; do not invent outputs to boost counts.
- `state.predicate(name, test, keys, references=(), identity=None)` makes a clause
  from fresh current keys and optional historical reference keys. It calls test
  only when all those inputs are known. test receives their values and returns a
  Python bool or NumPy boolean scalar; the latter is normalized to Python bool.
  Numeric values, strings and arrays (including zero-dimensional arrays) are
  not predicate results. References may predate the action (e.g. a displacement origin),
  but the current measurement must follow it. Each clause records its own IDs.
- `state.all_of(clauses)` returns the goal's true/false/unknown verdict, clauses,
  IDs and reason. All true means true; one reliable false means false; otherwise
  unknown. Define the task's full goal as explicit semantic clauses. A detected
  object alone is not a completed placement or drawer operation. Do not use phase,
  command receipt, elapsed time, or lack of Python errors as completion evidence.
  The runtime validates each clause independently; fresh evidence for one clause
  does not rescue stale evidence for another. It does not certify your semantics.

Task-independent data-flow example (illustration only, not a task solution):

```python
FOUNDATION_REVISION = "r1"
state = WorldState()
def update(obs, last_action=None):
    for key, fact in obs["facts"].items():
        state.set(key, fact.get("value"), evidence_ids=obs["evidence_ids"],
                  valid=fact.get("valid", False), reason=fact.get("reason", ""),
                  identity=fact.get("identity"))
def query(name, **kwargs):
    return state.query(name, **kwargs)
def snapshot():
    return state.snapshot()
# A policy passes REAL measurements, never its intended action target:
# world.update({"evidence_ids": [world.evidence_id()], "facts": measured_facts})
# answer = world.query("measurement_name", identity=tracked_identity)
# Consume known values in your existing numeric/control computation; explicitly
# handle unknown using admissible sensing or the policy's ordinary error handling.
# print(answer) alone is logging, not use. Do not replace geometry with made-up
# fallback coordinates when unknown. No new self-eval recovery controller here.
```

Implement all seven required functions, including observe, predict, simulate and
done; the snippet above is only the state adapter. Let observe handle automatic
public returns where useful, and update handle derived measurements. Do not
pass full image arrays into the JSON fact store: derive compact measurements
from the actual public arrays. Validate read-after-write, overwrites, unknown
invalidation, object identity, per-fact freshness and old-reference/new-current
comparisons using task-independent CPU assertions before spending simulator time.

For each development trial, inspect `foundation_calibration.json` and
`world_use_audit.json`. Calibration compares the FINAL live judgment with the
FINAL evaluator outcome, grouped by exact policy/world version: false positives,
false negatives and unknown are separate. Earlier judgments lack simultaneous
labels and are not calibration samples. Labels are produced after the process
exits and never enter the runtime world. Use only this cell's development
feedback to correct semantics/parameters; record fitted values and provenance.
A usage audit distinguishes logging-only, supported-use candidates and
inconclusive dynamic Python. It is advisory and never grounds a success claim,
rejects a legal policy, or rewards raw call counts. Report inconclusive honestly.

Keep the original Fix Loop, action APIs, replay budget and frozen evaluation.
Do not add a new world-driven recovery controller, geometry migration mandate,
physical predictor, larger rehearsal library or a new selection/budget scheme.

### Trial admission and failure accounting (pipeline repair, 2026-10-03)

The original three-attempt budget per development seed is unchanged. A diagnostic
session grades nothing; it cannot consume the final available attempt on a seed
that has no graded evidence. Use that remaining attempt for a real graded trial.
This reservation does not restrict diagnostics after graded evidence exists.

The initial policy/world bundle remains identical across the initial batch.
After that bundle has really executed (including a real smoke explicitly aliased
as initial), an offline reproduction of its authored error does not prevent
initial grading on the remaining development seeds. The trial is charged, keeps
the actual failure, and records `screening_baseline_exception` alongside the
original rejected screening. This is not a screening pass. It cannot admit a new
or changed bundle, a repair, or an infrastructure/malformed screening result.

`check` reports `decision_type`: `ready`, `incomplete`, or `terminal`. A seed whose
attempts are exhausted without graded evidence makes the batch terminal even if
solution files or reports are missing. Stop and report the blocker; do not reset
its ledger, relabel diagnostics as task results, or repeatedly ask another solver
turn to manufacture the missing evidence. Missing reports alone remain fixable.
