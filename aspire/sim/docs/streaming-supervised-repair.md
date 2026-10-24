# Streaming Fix Loop: supervised repair study (opt-in)

Schema `aspire-supervised-repair-v1`, implemented by
`scripts/libero/streaming_fixloop_rounds.py`, used by `streaming_fixloop_protocol.py`,
`streaming_fixloop_campaign.py` and `streaming_fixloop_heldout.py`; outer CLI
`scripts/libero/streaming_fixloop_review.py`. A case without
`supervised_repair` behaves exactly as before. It cannot be combined with
`continuation` (docs/streaming-repair-continuation.md) and requires
`streaming.execution_contract` (docs/checked-execution-contract.md).

## What it is

A NEW, separately registered development phase of one cell: new identity (with
the study id and the bound prior-input manifest), new ledger and a 50,400 s
clock recorded once at `init`. It is not a resume and refunds nothing. The
cell's own earlier development inputs are listed by an
`aspire-repair-prior-input-v1` manifest (hash-bound, files re-hashed at `init`)
and are readable by the solver; nothing from them enters the new trials, budget,
candidates or selection.

## Case contract

```json
"supervised_repair": {
  "schema": "aspire-supervised-repair-v1", "study_id": "...", "authorization": "...",
  "prior_input": {"manifest": "/abs/.../manifest.json", "manifest_sha256": "..."},
  "rounds": 3, "round_seeds": [51, ..., 65], "attempts_per_seed_per_round": 1,
  "require_current_selected_success": true, "review_dir": "/abs/.../reviews/<cell>",
  "review_poll_seconds": 30, "boundary_grace_seconds": 1800
}
```

## Rounds and budget

* In round r every seed 51–65 gets at most one charged attempt (smoke, initial,
  repair or diagnostic; timeouts, crashes and infrastructure errors stay
  charged; aliases and the snapshot are free). A failed seed waits for the next
  round; no attempt is borrowed. Total per seed stays 3. Code edits are unlimited.
* A round is complete when all 15 seeds have a resolved attempt of that round
  and nothing is running. The runner stops the native session after
  `boundary_grace_seconds`, settles stale `running` rows (still charged), and
  runs `close-round`: per-seed public summary in
  `<control>/supervised/round_<r>/summary.json` and an `evidence_sha256` over the
  rows and the attempt files.
* Rounds 1 and 2 then wait (non-model file polling) for
  `<review_dir>/round_<r>.review.json`, root-owned and not writable by the cell,
  bound to cell, study, round and evidence digest, with decision
  `continue|freeze|stop` and nonempty analysis and next-round guidance. The
  campaign consumes it once (`consume-review`) and opens a fresh native session
  for the next round with the review paths in its prompt. Round 3 closes
  development without a review. A deadline is never an approval.

## Round closeout

Optional `supervised_repair.closeout` (absent: the boundary-grace stop above
is unchanged). Strictly validated, recorded in the identity with a
`method_difference` note, and any change after `init` is an identity mismatch:

```json
"closeout": {"schema": "aspire-round-closeout-v1", "report_grace_seconds": 1800,
             "session_timeout_seconds": 1200, "max_sessions": 2}
```

Integers only: `report_grace_seconds` 600–7200, `session_timeout_seconds`
300–7200, `max_sessions` 1–3. A round then has two separate states, persisted
in `campaign_state.json` under `supervised.closeout.<r>` and in
`<control>/supervised/round_<r>/closeout.json`:

* **trials complete**: all 15 seeds have their resolved charged attempt of the
  round. With closeout opted in, the ledger refuses every further trial admission,
  including an uncharged `snapshot`, before any simulator action or new row.
  The round session
  may continue writing reports for up to `report_grace_seconds`, then the
  runner stops it (`stopped_at_report_grace`). It is no longer stopped at
  `boundary_grace_seconds`.
* **closeout complete**: the runner (`streaming_fixloop_closeout.verify`, over
  the protocol's own `status`) finds a nonempty `task_analysis.md` and a
  `findings.md` with the four required sections. In rounds after the first
  closed-out round, both files must also differ from that round's accepted
  versions. It also requires `attempts/seed_N_BLOCKED.md` for every exhausted
  seed without a pass. If a bundle of this study has its own normal oracle
  success, a `select` of such a bundle is required, with `fix_code.py` and
  `fix_streaming_config.json` matching it. With no success, any selection is
  an error. The charged count, ledger rows and evidence digests of every round
  so far must equal their values when the trials completed.

Only after closeout completes does the runner run `close-round` (hence the
review and, after round 3, `check`/`finalize`, both unchanged). If closeout is
incomplete, it runs at most `max_sessions` restricted native sessions of the
same cell. They use the same model, settings and guard; each is clipped to
`session_timeout_seconds` and the unchanged deadline. Their prompt lists only
this cell's own task directory, reviews and summaries, plus the problems
found. They may write reports and blocker notes, copy an already tested
successful bundle of this study into the fix files, and `select`. They may not
run trials, author policy or edit attempts. A watcher stops a session whose
ledger gains a running or new row. Their transcripts are ordinary sessions for
`finalize`'s provenance. A changed tested program or charge count is an
integrity violation. Violations, exhausted sessions and the deadline all end
the campaign as `development_incomplete` (`heldout_skipped.json`, no outer
marker). None of them refunds, re-opens or extends anything.

## Gate

`select`, `check` and `finalize` require the selected bundle to have its OWN
normal oracle success (complete, sandbox_rc 0, task_completed; never diagnostic)
in this study. With no new success at all the campaign ends
`development_failed` (`heldout_skipped.json`, no outer marker); `stop` ends
`development_stopped`. The held-out freeze re-derives the closure and the
frozen bundle's own success from the ledger rows before any seed runs.
An outer `freeze` waives only pending repair/initial coverage; seeds without any
graded evidence still block.

## Outer CLI

`queue`, `export-review-request --out`, and root-only `approve-review --round R
--decision D --analysis-file A --guidance-file G --reviewer TEXT`, which writes
the immutable review file and never edits the ledger. The solver's guard refuses
this CLI, the outer protocol subcommands, other cells' review/prior directories,
`EXPERIMENT_RESULTS*.md` and `preparation-state.json`.
