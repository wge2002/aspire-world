# Opt-in p2-runtime: executable observed state and scoped prediction feedback

This interface applies only to a NEW C judgment cell with
`executable_world_revision: r1` and `prediction_contract: p2-runtime`.
Off and p1 keep their original contracts and defaults. No experiment is enabled
by installing this code. This document lives under `cap/` so the existing source
freeze inventory hashes it with the runtime, profile and public API source.

The generated module defines `update`, `query`, `snapshot`, `predict`, `observe`,
`simulate`, `done`, and declares `STATE_TEMPLATE_REVISION = "p2-runtime"`.
Declare `FOUNDATION_REVISION = "r1"` as well in foundation cells. It gets only
public call/response values and evidence bookkeeping; no environment, simulator,
perception-service handles, evaluator label, future tape or credentials.
Adapters can process raw public arrays. The policy's control facts are reads of
one observed template, rather than another authoritative scene dict in policy.
The author owns associations, predicates, dynamics, numerical bounds and
development provenance. This fixed template supplies none of those semantics.

## Authoritative observed template

`state = WorldState()` returns the same episode-local state on repeated calls.
It separates measured layers (`observed`, or explicitly `rehearsal` in that
binding) from `predicted`. Commands and predicted coordinates cannot fill
measured slots. `get_observation` evidence IDs are real public-call IDs, never
motor receipts. All evidence in a current fact must postdate the last motor call.

```python
state.entity(entity_id, label=public_label, evidence_ids=[obs_id],
             source={"api": "segment_sam3_text_prompt", "method": association_rule},
             uncertainty={"status": "estimated", "reason": association_reason})
state.set_fact(entity_id, "position", measured_position,
               kind="geometry", frame="world", unit="m", evidence_ids=[obs_id],
               source={"api": "mask_to_world_points", "method": geometry_estimator},
               uncertainty={"status": "estimated", "reason": measurement_quality})
```

This is schema pseudocode, not a supplied detector or task recipe. Entity IDs
are stable authored public associations, not indices in an incomplete detection
list. Register only identities supported by public evidence. Occlusion,
ambiguity and changed association must yield unknown and never silently switch
the target. Record an unknown using `invalidate_fact(entity, fact, frame=...,
unit=..., evidence_ids=..., source=..., reason=...)`; it replaces the old slot.
Refresh the entity association from current public evidence before writing a
current geometry or relation. Unknown/stale association and changed entity labels
cannot become known geometry merely by assigning the old ID to a new measurement.

Each slot contains entity, fact, kind (`fact`, `geometry`, `relation`), value,
status, frame, unit, related_entities, source.api/method, uncertainty.status
(`measured`, `estimated`, `unknown`), observation IDs, layer, revision, validity
and freshness. `unit="1"` and `frame="semantic"` are suitable explicit names
for dimensionless symbolic facts, if that is their authored meaning. Frames and
units must match the actual public API conventions. Relations declare their
other stable entity IDs in `related_entities`, in authored role order.
Use distinct fact names for distinct relations on one entity.

All facts become stale after a motor call. Optionally `through_call` declares an
earlier API-call validity cutoff. Prediction horizons do not extend measured
fact validity. Evidence provenance alone does not establish semantic accuracy.

Policy reads:

```python
fact = world.query("state", entity=entity_id, fact="position", frame="world", unit="m")
# fact['status'], fact['value'], provenance, freshness and read_id
```

Optional expected frame/unit filters downgrade a different frame/unit to unknown.
`world.query("world_state")` returns the template for inspection. Inside world,
`state.get_fact(entity, fact, frame=..., unit=..., fresh=True)` reads the same
slots. `fresh=False` is an explicit historical read for authored comparisons,
never evidence of current control/goal state. `state.snapshot()` is finite JSON.
`state.set()` is refused in p2 because it would omit required reference metadata.
The substrate's `state.predicate(name, authored_test, [(entity, fact), ...],
references=[(entity, reference_fact), ...])` and
`state.observed_only((entity, fact))` remain available using explicit scoped keys.
The predicate semantics, thresholds and reference interpretation are authored.

An `observe(event)` gets `{id, kind, binding, call, result, error, evidence_id}`
with the complete public return. For policy-derived perception, pass public
derived quantities to `world.update({"evidence_ids": [world.evidence_id()],
"values": ...}, last_action)`; the authored update adapter writes template
slots. Finish the actual segmentation/geometry/update pipeline before requesting
prediction feedback. A command target is never a measured object position.

## Prediction lifecycle

`predict(call)` runs before the real public call, where call is
`{id, function, args, kwargs}`. Return a nonempty finite-JSON list or raise
`Unsupported(reason)` honestly for unmodelled dynamics, math or sensing:

```python
return {"predictions": [{
    "entity": tracked_entity, "fact": modelled_fact,
    "frame": actual_frame, "unit": actual_unit,
    "kind": "fact", "related_entities": [],
    "expected": modelled_effect,
    "tolerance": fitted_bound,
    "target": {"kind": "next_observation"},
    "valid_for_calls": pipeline_horizon,
    "intervening_motion": "supersede",
}]}
```

Expected cannot be None; unavailable semantics are Unsupported. Numeric
comparisons use maximum absolute residual and an authored nonnegative tolerance
(default 1e-6 if omitted). Symbolic comparisons use equality. Malformed batches
commit no partial predictions and are recorded, rather than inventing effects.
`kind` defaults to fact. Relation predictions require related_entities and check
their identity/role order as well as the primary entity.

Targets:

* `next_observation`: the FIRST successful `get_observation` after the source
  call, including derived perception writes citing exactly that observation.
* `same_call`: only on a source `get_observation`; its actual response can
  resolve the prediction with the SAME evidence ID. No future return is shown
  to predict. Not available for a command receipt or pure math result.
* `checkpoint`: `{"kind": "checkpoint", "name": label}` targets the latest
  fresh post-source observation when the policy calls `world.checkpoint(label)`.
  It reads completed template facts; missing or stale facts become unknown.

`valid_for_calls` is a required, author-chosen API-call horizon, inclusive at
source ID + horizon. It must cover the intended perception pipeline; it is not
an action/query cap. The next call beyond it marks pending predictions expired.
Before ANY later motor call, pending predictions are superseded by default,
even when that later action has an Unsupported model. Thus a series of goto_pose
calls followed by goto_home cannot all be compared against the final home pose.
Explicit `intervening_motion="retain"` allows an authored multi-action model,
usually with a checkpoint, to retain its pending prediction inside its horizon.
It does not extend the horizon or relax fresh evidence checks.

New predictions for the same entity/fact supersede the old pending window.
Entity identity, frame, unit, fact kind and relation identities must match; a
bad association/reference produces unknown rather than a physical mismatch.
Stale or mixed observation IDs cannot resolve a prediction. The exact target
window remains open for derived adapter updates; feedback query, a new
observation, a named checkpoint or episode end closes a missing target as unknown.
An observation never received before episode end is unresolved. Superseded and
expired are distinct from mismatch. Historical resolved results also become
expired in current feedback after later motion or horizon expiry.
An association change or revised measurement interpretation after a check also
expires that historical result in current feedback; it cannot recycle a residual
computed from an earlier template revision.

## Runtime policy interface

```python
feedback = world.query("prediction_feedback", entity=entity_id, fact=fact_name)
# {'contract': 'p2-runtime', 'at_call': ..., 'read_id': ..., 'results': [...]}
```

Optional filters are entity, fact, source_call, checkpoint. The query returns
the latest source window for each relevant entity/fact, not the last mismatch
from any point in episode history. Rows include source call/function, expected,
observed, target, horizon, metadata, evidence ID, residual and status:
match/mismatch/unknown/pending/superseded/expired/unresolved. Unsupported and
malformed calls remain in historical checks/counts; they never masquerade as
task-relevant supported predictions. `prediction_checks` with the same filters
and `prediction_summary` without arguments are diagnostic history/counters.

At meaningful manipulation checkpoints, author a policy branch which reads
current world state and corresponding prediction feedback, and uses them to
choose the next call or its arguments. The branch may continue, obtain evidence,
relocalize or revise the action. No per-action mechanical querying is required;
neither query counts nor prediction accuracy are rewarded. An end-of-program
print is logging, not use. A mismatch may diagnose dynamics, perception,
association or execution, so do not equate it automatically with task failure.

```python
# Branch semantics and next_call are AUTHOR-WRITTEN, not framework routing.
branch, next_call = choose_from_world(fact, feedback)
world.record_decision(branch, feedback=feedback, facts=[fact], reason=authored_reason)
next_call()
```

`record_decision` accepts exact current state/feedback query answers and records
their read IDs. Modifying or reusing answers across a public call is refused.
Changes to the template or prediction outcomes after a read also require a new
query. It neither executes nor changes an action. The next completed public API call
is linked to that record, including sensing chosen to resolve unknown evidence.
The decision API is for auditable policy-authored flow, not a harness recovery.
Declarations alone are not causal proof; fixtures with controlled feedback can
test actual branching for that fixture only.

Read validity uses the entire observed-template revision for both state AND
feedback tokens, plus the outcome revision for feedback. Thus an old resolved
match cannot be paired with a new state answer after same-frame reinterpretation
or association updates. This is conservative: even an unrelated template update
requires fresh query answers. The fixed store may contain authored relations and
uncertainty dependencies not expressible by a shallow entity/fact token, so this
revision avoids silently guessing a partial dependency graph. Complete perception
updates first, then read the state/feedback pair and author the decision. This
adds no motor calls, query quotas or replay budget, and freshly reread answers
remain usable even when the current feedback correctly reports expired.

Old observational-only instructions concern `done()` GOAL self-evaluation.
They do not prohibit prediction residuals from influencing ordinary policy
control. This opt-in profile by itself keeps done observational. Authored goal
clauses still need current observed evidence and `state.all_of` can combine
them; predicted state is not goal evidence. No change to development gates,
native CC tools, initial generation, dev 51–65 (three total attempts each),
selection/freeze, heldout 1–50, model or budgets.

## Audit and compatibility

Events: state_write, prediction_committed, prediction_check, world_query,
world_checkpoint, policy_decision and policy_decision_action with the following
ordinary public_api receipt. Manifests carry prediction_checks and
runtime_mechanism only in p2. Trial feedback includes runtime_mechanism even
without the older world_use_revision switch. The selected-pair report includes
the pair's own trial audits. Mechanism findings never gate task success,
admission, retries or freeze selection.

Classification: no_query; logging_only if static syntax and recorded queries
show only prints/discards; inconclusive for untracked/helper flow or an unlinked
declaration; action_evidence for exact answers linked through a policy-authored
decision record to a completed subsequent public call and scoped state/check
evidence. Static supported_use_candidate remains a separate advisory status.
None is a claim of causal real-task benefit. A preflight with every prediction
Unsupported has zero supported_predictions and no action chain; it cannot pass
mechanism acceptance. A completed rehearsal reports model consistency, not
actual perception or task success.

The profile, CLI, cache identity, trial config and heldout identity preserve the
revision string; offline rehearsal/replay use the same runtime. The normal
source freeze captures this document and the new modules. Never change an
active/frozen off or p1 cell into p2. Use fresh outputs and a fresh explicit case.
