# Remaining supervised rounds with autonomous review

This opt-in phase continues the same supervised study after an explicitly
authorized repair. It imports the immutable completed first-round ledger
(15 charged attempts), preserves its expired clock as parent evidence, and
records one new repair-phase development clock. Only repair attempts in rounds
2/3 are admitted: 30 additional, 45 cumulative. Ordinary resume cannot reset
either clock or budget. Parent executions cannot qualify a current candidate.

The case adds paired `supervised_repair.continuation` and `autonomous_review`
objects. Their exact schemas are validated in `streaming_fixloop_autonomous.py`.
The continuation binds cell/study, parent ledger and campaign-state hashes,
parent runtime hash/clock, and corrected starting policy/config/guidance hashes.
Parent inputs are root-owned, private, and read-only to the experiment user.
Separate input copies and OS users preserve cell isolation.

Each DLC slot launches its own root controller. The ordinary campaign and an
independent review session run as that slot's unprivileged user. The reviewer
uses the same native CC version, local Qwen model, effort, context and output
settings. It has no tools, agents or MCP servers. Root assembles only that
cell's closed-round development evidence, including per-seed outcomes, distinct
executed code and bounded log excerpts, with full-file hashes. No held-out or
cross-cell evidence is supplied.

The reviewer returns analysis and next-round guidance as data. Root verifies
model/context, exact identity/round/evidence binding and substantive content,
then publishes the existing immutable review format. The campaign validates
and consumes it to open the next round. Only a fatal evidence/runtime problem
permits `stop`; early freeze is refused. Reviews have a persisted, bounded
attempt allowance and a timeout clipped to the development deadline. Failure
terminates that campaign, stops its children and records
`development_review_failed`; it cannot wait indefinitely for chat intervention.
The other slot and the pair's service health checks run independently.

New cases can add the optional `supervised_repair.closeout` block
(`aspire-round-closeout-v1`; docs/streaming-supervised-repair.md#round-closeout)
to the remaining-round or independent-start branches. Then a round's
independent review is requested only after its closeout is complete:
reports, exhausted-seed notes, and a selection exactly when an own success
exists. Missing material is completed by bounded restricted native sessions
of the same cell, with no trial. Cases without the block, including the
Oct15–Oct17 cases, keep the earlier boundary stop.

Round 3 closes automatically. A selected bundle must have its own normal
oracle-successful execution in the new runtime, complete the normal development
handling, and pass normal provenance/freeze checks before held-out seeds 1–50.
Zero qualified successes skip evaluation. Trial/development/evaluation
watchdogs remain 900s/14h/5h. Engineering corrections and automated Qwen review
are declared method differences, not an independent baseline or human review.

The separately authorized Oct17 r2 repeats use `independent_start=true` without
`continuation`. Their SHA-bound `aspire-independent-round-start-v1` manifest
has null parent identifiers, zero historical attempts and an empty file list;
historical inputs are refused. They generate their initial program from their
own public scene and run rounds1–3 (at most45 attempts), each with the same
unprivileged native solver and independent automatic reviewer. Their identity
records the independent start and exact review configuration. Review phase IDs
bind to the new study ID; payloads contain only their own executed programs and
logs, with no corrected-parent starting policy or historical guidance. They do
not use the remaining-round ledger importer. All ordinary gates and clocks
remain unchanged. Report their fresh starting condition separately from r1's
history-based continuation.
