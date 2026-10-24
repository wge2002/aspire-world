# repair_start: new engineering-assisted repair study

`supervised_repair.repair_start = true` is the third, mutually exclusive way to
start a supervised repair study (`aspire-supervised-repair-v1`, see
[streaming-supervised-repair.md](streaming-supervised-repair.md)), next to the
remaining-round `continuation` and the fresh `independent_start`
([streaming-autonomous-continuation.md](streaming-autonomous-continuation.md)).

It is a NEW study of one cell. The lineage's own declared earlier development
(exactly 45 charged attempts) and an engineering-assisted, unvalidated seed are
bound read-only by the standard own-prior manifest. Nothing is imported: the
new ledger starts with zero trials, round 1 and a fresh 50,400 s development
clock; the new budget is 3 rounds x 15 seeds, one charged attempt per seed per
round (at most 3 per seed, 45 in total). Historical rows, bundles, selections
and successes are never candidates or current evidence.

## Case schema

```json
"supervised_repair": {
  "schema": "aspire-supervised-repair-v1", "study_id": "...", "authorization": "...",
  "repair_start": true,
  "prior_input": {"manifest": "/abs/priors/<cell>/manifest.json", "manifest_sha256": "<sha256>"},
  "rounds": 3, "round_seeds": [51, ..., 65], "attempts_per_seed_per_round": 1,
  "require_current_selected_success": true, "review_dir": "/abs/reviews/<cell>",
  "autonomous_review": {"schema": "aspire-autonomous-native-review-v1",
    "operator_dir": "/abs/operator/<cell>", "timeout_seconds": 60..3600, "max_attempts": 1..3,
    "model": ..., "effort": ..., "context_tokens": ..., "max_output_tokens": ..., "claude_version": ...},
  "closeout": {...}            // optional, docs/streaming-supervised-repair.md#round-closeout
}
```

Refused (no ledger written):

* `repair_start` not exactly `true`; together with `continuation` or `independent_start`;
* missing or inexact `autonomous_review`, a different schema, reviewer
  model/effort/context/output/harness differing from the solver's, timeout or
  attempt count out of range;
* `operator_dir` not absolute, inside the cell control, under a held-out /
  `EXPERIMENT_RESULTS` path, or overlapping the solver-readable prior or review
  directory.

## Own-prior manifest

`aspire-repair-prior-input-v1`, checked by `rounds.read_prior` exactly as for
any supervised study (manifest sha256, exact fields, `historical_attempts: 45`,
`new_budget_separate`, `no_heldout_inputs`, relative non-held-out paths, and at
`init` every listed file re-hashed). `repair_start` adds
(`autonomous.repair_start_prior_errors`):

* the manifest lists `repaired-seed/seed_code.py`,
  `repaired-seed/seed_streaming_config.json` and the historical
  `development_state.json` (`repaired-seed/changes.md` optional notes);
* at `init`, the historical ledger belongs to `parent_cell`, has the same
  suite/task, a different study id than the new study, no running rows, only
  seeds 51-65, and exactly 45 charged rows, 3 per seed.

The prior directory is root-owned and solver read-only in deployment; the guard
allows reading it and denies writing. No code path writes it.

## Identity

`supervised_repair` identity gains (`autonomous.repair_start_provenance`):
`kind` (new engineering-assisted repair study), `repair_start` (`schema
aspire-repair-start-v1`, `parent_cell`, `historical_attempts 45`,
`historical_role "read-only input"`, `imported_trials 0`,
`historical_successes_are_candidates false`, `historical_ledger_sha256`,
`engineering_seed` paths + sha256, `engineering_seed_validated false`,
`new_budget`, `first_round 1`, `phase_clock new`) and the full
`autonomous_review` block. A resume must reproduce the identity exactly, so a
changed review setting, manifest or seed is refused; `init` on an existing
ledger is refused (no refund, no clock reset).

## Automatic review

The root controller (`streaming_fixloop_autonomous.py --case --user`) runs the
campaign as the cell user and, when a round awaits review, one tool-free
same-local-Qwen native session. `review_payload` contains only this cell's
current-round charged rows (paths resolved under its own task directory,
inherited rows refused), and `repair_start` references: prior manifest path and
bound sha256 (re-verified by root), parent cell, 45 historical attempts as
read-only input, `imported_trials 0`, engineering seed paths + sha256,
`engineering_seed_validated false`. It never sets `independent_start` and never
includes historical contents. The reviewer may only `continue` or fatally
`stop`; freeze stays the ordinary post-round-3 selection/check/finalize gate.

## Native prompt

`autonomous.repair_start_section` (called by `campaign.supervised_section`):
new study, not independent and not a continuation; history read-only; seed
UNVALIDATED; run `status` first and only `round_seeds_remaining`; round 1 is
snapshot + smoke + initial, rounds 2/3 repair; write reports/BLOCKED notes;
select only an exact bundle with its own normal oracle success in this study;
reviews open the next round automatically; held-out only after a normal freeze.
