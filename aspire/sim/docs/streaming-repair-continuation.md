# Streaming Fix Loop: explicit repair continuation (opt-in)

Schema `aspire-streaming-repair-continuation-v1`, implemented by
`scripts/libero/streaming_fixloop_continuation.py` and used by
`streaming_fixloop_protocol.py` / `streaming_fixloop_campaign.py`. A case
without a `continuation` key behaves exactly as before.

A continuation lets an authorized, incomplete development cell carry on with
**its own** earlier code and evidence under a new, corrected runtime. It is not
an unchanged `--resume` (the runtime and deadline differ) and not a fresh
independent repeat (it starts from its own history). The parent's rows, outcomes
and clock stay immutable history; the new phase has its own identity, ledger and
a development clock recorded once at `init`.

## Case contract

```json
"continuation": {
  "schema": "aspire-streaming-repair-continuation-v1",
  "parent": {
    "cell": "<this case's id>",
    "development_state": "<control>/parent/cell/development_state.json",
    "development_state_sha256": "<sha256 of that copied file>"
  },
  "authorization": "<who authorized continuing this cell's own evidence, when>"
}
```

The importer refuses the contract if any of these fail:

- `parent.cell` must equal the case `id`, and must equal the parent ledger's
  `identity.cell`.
- The copied ledger lives in this cell's own control directory, outside
  `<control>/cell`, and not under a held-out or `/root` path.
- At `init` the copied ledger must have no write bits, must not be
  world-readable, and must be owned by the cell's own user.
- The parent identity must match the new one on harness, profile, method,
  repeat, suite, task, condition and gate. Model, effort, context and output
  limits must match, and so must seeds 51–65, the retry limit, max steps, the
  trial timeout and both watchdogs. The camera, dependency evidence,
  environment version and online-review settings must match too.
- The model service must be the same launch. That means the same endpoint and
  served model, and the same manifest sha256 with the same verified endpoint,
  model and capacity. A same-named model on different weights, engine, argv or
  settings has a different manifest and is refused. The initial-state registry
  must also match: the same manifest sha256, seed mapping and per-state hashes.
  Only the two manifest file paths (`model_service.manifest.path`,
  `initial_states.manifest`) may move. Nothing else is relocatable: a different
  endpoint needs a new launch manifest, which is refused.
- The parent must be incomplete (`stage1_complete: false`) and must have a
  recorded development clock. It must not itself be a continuation.
- No parent row may be `running`, and no row may name a seed outside 51–65 or
  any held-out key.
- No seed may have more than 3 charged rows.
- Every admitted simulator row must spend its retry. Aliases and the snapshot
  must spend none.

The importer reads only that one file. It does not scan sibling cells or
held-out directories. The coordinator checks separately that the parent
terminated and never used held-out seeds.

## Layout and initialization

```
<control>/                      new, owned by the new cell's private user
  parent/cell/                  read-only copy of the parent's own task directory
    development_state.json      bound by sha256 (mode 0400/0440)
    scene_snapshot*.jpg, task_analysis.md, findings.md, *_code.py, *_streaming_config.json,
    attempts/, development/     historical inputs the worker may read
  cell/                         NEW task directory, empty before init
```

1. Copy the parent's own task directory to `<control>/parent/cell`, set it
   read-only, and hash `development_state.json` into the case.
2. Run the ordinary native `init` (the DLC pair's `fresh` mode does this
   through the campaign). It checks the contract and imports the parent once,
   in the ledger's first save, as `continuation.historical`, with
   `historical_sha256`. The parent's identity, clock and rows are kept verbatim.
   It also starts the new 50,400 s development clock.
3. Every later action resumes this ledger. The identity, which includes
   `identity.continuation` with the re-hashed parent, and the deadline must be
   unchanged. A second `init`, a case without the contract, or a changed parent
   is refused.

## Accounting and evidence

- **Retries:** the 3 TOTAL per seed are cumulative. Every parent row with
  `spends_retry: true` counts once, whatever its status: diagnostic, crash,
  timeout, infrastructure error or interrupted. Aliases and the snapshot never
  count. A seed with no attempts left is refused.
- **Snapshot and smoke:** the parent's snapshot is the cell's snapshot. No new
  snapshot is admitted, and no smoke runs once the parent ran its initial batch.
- **Initial coverage:** parent initial coverage is kept. Seeds the parent never
  ran initially are owed initial runs before any repair. Those runs must use the
  parent's own initial policy and authored streaming config, compared by
  digest. The world digest is the corrected runtime's and is never compared with
  the parent's. The first new initial row freezes the new initial bundle as
  usual.
- **Historical successes:** they are reported in
  `continuation.historical_successes` and per-seed `historical.passed`. They
  never count as `passed`. A seed with initial evidence, no pass in this phase
  and attempts left is `seeds_pending_repair`. It is finished only by a passing
  execution in this phase, or by exhaustion plus its blocker note.
- **Selection, freeze and held-out:** these read the new `trials` rows only.
  This covers `tested_bundles`, `final_development_coverage` and the held-out
  freeze. A parent bundle is never a candidate.
- **Reporting:** `status`, `check` and `stage1_result.json` report
  `continuation.execution_totals.historical`, `.new` and the cumulative charged
  attempts separately.
