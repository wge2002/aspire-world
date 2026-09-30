# Executable C world r1

This is the C arm of a fresh two-task A/C comparison; each C cell authors its
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
Do not replace the predicate with phase=complete. Use `world.done()` in policy
to decide checking/recovery/stopping when self-evaluation is enabled. The runtime
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
