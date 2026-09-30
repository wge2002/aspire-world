# Coordinator acceptance checklist

## Scope and implementation

- Items 1/2/3/8 have executable support and behavioral tests, not just new prose.
- State read-after-write, overwrite, missingness and observed/predicted/rehearsal separation work.
- Current object facts cannot silently reuse initialization defaults after a missing measurement. Historical reference measurements remain explicitly historical; legitimate pre/post comparisons must not require every reference to be newer than the last action.
- Predicate true/false/unknown combinations are explicit. A reliable negative fact can disprove a conjunction; absent facts alone cannot establish false or true. Invalid/future/command evidence is rejected.
- Goal audit ties verdicts and task labels to one exact development bundle and excludes historical held-out data. Runtime helpers do not receive evaluator labels.
- Usage audit distinguishes logging from actual consumption; a warning is not a proof. No new autonomous recovery loop is imposed in this phase.
- Old outputs/runtimes remain unchanged. New profile/version and changed runtime hashes are documented.

## Three-task preflight

- Three cases resolve to the declared distinct tasks and nonprivileged traced API, no strategy MD, same Qwen native model settings.
- Local model and gated perception weights exist; live perception and a real task unit produce artifacts. Service probes alone do not establish the work loop.
- Preflight diagnostics, if any, enter each task's own development ledger exactly once. Snapshot, charged trial, offline check and infra failure remain distinct. No old imports or tombstones leak into fresh cells.
- Original per-seed budgets, native worker assignment/read guards, tested-bundle selection and immutable held-out 1–50 manifests remain enforced.
- Launch metadata pins support/runtime sources and unique output ownership. Entry point switches UID/GID before project writes; one worker per 8-GPU job.
- Three submission receipts maximum; use exclusive claim records and reconcile ambiguous outcomes before any further submission.

## Handoff

Report actual DLC IDs and platform/application state, confirmed changed features, exact tests/preflight evidence, remaining blockers and ETA. Submission is not successful experiment completion. Final result review must independently reconcile all 50 rows per task and report self-evaluation confusion/unknown coverage separately from task success.
