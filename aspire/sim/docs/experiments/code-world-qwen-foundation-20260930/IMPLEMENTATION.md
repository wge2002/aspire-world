# Foundation 1/2/3/8 implementation

Engineering CC accepted the task after a clear, but its first model request returned HTTP403 quota exhausted before tool execution. Its session stayed idle and its prior files were preserved. Codex took over under AGENTS.md's explicit model-API-failure exception. Experiment inference remains local Qwen; no provider substitution.

## Implemented

- `cap/world_model/evidence_state.py`: one authoritative state with observed/predicted/rehearsal layers, copied finite values, per-fact observation identity/freshness, missingness invalidation, explicit historical references, and three-valued conjunction. Framework contains no task geometry or goal thresholds.
- `cap/world_model/executable_world.py`: inject the helper, record query caller sites, validate each foundation goal clause independently before final conjunction. Old nonfoundation worlds retain their previous verdict contract.
- `cap/world_model/foundation_audit.py` and `native_world_protocol.py`: post-process development outcomes only, compare final judgment to final label, exclude diagnostics/crashes, verify exact policy/world/config/ledger identities, persist false-positive/false-negative/unknown categories and group by bundle.
- `world_use_audit.py`: executable-world config identity now supported; existing bounded dataflow analysis is enabled, with real query caller sites. It reports logging-only, corroborated use candidates and inconclusive cases without changing admission or task scores.
- New foundation interface and opt-in prompt: task-independent state adapter and uncertainty examples, meaningful use instruction, semantic goal decomposition and development-only calibration. No new done-driven recovery or stopping controller. The existing source/MD removal, perception APIs, offline support semantics and native Agent/compaction workflow remain.

The engineering profile was rebased on its newer remote version (which already supports fresh C lineage and a case-specific interface path); the old local file and remote originals were preserved under reference/. This preserves existing remote repairs rather than reverting them.

## Verification

Remote pytest: 77 passed, 12 subtests passed. Covered state read-after-write/overwrite, unknown invalidation, copy isolation, identity mismatch, command-vs-observation IDs, stale-per-fact goals, historical references, three-valued AND, executed query-use evidence, final-version calibration, held-out rejection, diagnostic import idempotence/non-candidate accounting, original Fix Loop regressions, offline replay/rehearsal boundaries, and single-submission receipts.

Real nonprivileged DSW preflight passed for each of the three tasks. Constructed API verified as FrankaLiberoApiReducedSkillLibraryTraced; observation → SAM3 → GraspNet → IK actually called. The new state helper additionally passed real-observation write/read/snapshot/missingness assertions inside ExecutableSession. Each run was seed51 attempt1/3 of its own cell. These are infrastructure diagnostics, not task performance evidence.

## Frozen experiment

Three new independent C/full cells: bowl_C, bowldrawer_C, drawer_C. Each uses Native CC2.1.220, Qwen3.8-Flash-Next xhigh, 1M context and configured64K output. Each starts from its own public scene with no old program or held-out input. The sole pre-executed input is its pinned current-study infrastructure diagnostic, imported once after fresh init. Seed51 has two remaining attempts;52–65 have three. Original initial sweep, per-failure repair, tested-bundle selection and frozen outer evaluation1–50 remain.

The outer development watchdog is10h (previous driver8h), heldout5h, startup40min, native-compat32min, finalize30min; DLC maximum1002min. These are process watchdogs, not new action/revision limits. Expected8–16h per task after scheduling. Frozen supervisor and case manifests pin all support and runtime changes.

Selected work remains1/2/3/8. Items4/5/6/7/9/10 are recorded in PLAN.md and not implemented. New tests prove framework semantics, not task predicate correctness or improved robot success; those require the launched Fix Loop results.
