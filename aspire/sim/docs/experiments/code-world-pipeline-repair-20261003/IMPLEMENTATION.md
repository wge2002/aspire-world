# Code World pipeline repair — 2026-10-03

> The repair-only phase below is complete. A subsequent user request authorized a fresh drawer_C DLC restart; see [RUN_RECORD.json](RUN_RECORD.json) and [STATUS.md](STATUS.md) for that separate execution.

## Scope

Repair the pipeline failure found in the R3 drawer development run. CPU regression tests only. No new model inference experiments, simulator trials or DLC jobs; no changes to task policies, held-out seeds or historical frozen runtimes/results.

## Failure chain

1. A valid NumPy boolean scalar produced by a predicate was rejected by the strict Python-bool check.
2. Once the initial bundle had really executed, subsequent offline screening rejected that same frozen bundle. The protocol also required the same initial bundle across all development seeds before repairs, so development could not progress.
3. Diagnostic trials could consume every attempt without producing the required graded initial evidence.
4. The outer driver treated an impossible ledger like a repairable missing report and repeatedly invoked the solver until its wall-clock timeout.

The R3 drawer run charged 45 attempts: 44 diagnostics and one initial trial. Seeds 52–65 had no graded evidence. This is a pipeline failure; it is not a measured held-out success rate.

## Repair boundaries

- Accept genuine NumPy boolean scalars and normalize them; continue rejecting numeric/string/array truthiness.
- Validate trial admission without mutations before expensive screening. Keep seed partitions, original attempt limits, frozen-initial identity and unresolved-attempt checks.
- Reserve the last available attempt for graded evidence when a seed has none.
- Permit only the identical, previously executed frozen initial bundle to collect real initial failures on the remaining development seeds. Preserve screening failures and an explicit exception record; infrastructure failures must still block. The exception also recognizes a real graded smoke backing an initial alias. Mixed infrastructure reports are not reusable cache entries, even if their aggregate status is rejected. New candidates and repairs do not get this exception.
- Report an exhausted-without-graded ledger as terminal, including when solution/report files are missing. Keep correctable report gaps recoverable.
- Check ledger viability at the outer entry point before the first solver turn and after each completed turn. Preserve ledger bytes, report the structured reason and stop before another solver turn or held-out freeze.

## Integration

The repair package contains a fresh copy of R3 support code. Only its outer driver is changed. `prepare-foundation.py` points to the new package, new runtime/result roots and the repaired canonical overlays. No real runtime has been staged. A future authorized rerun requires its normal fresh preflight and staging; old frozen R3 packages must not be patched or resumed as if they contained this fix.

## Validation

Final status: **implemented and CPU-verified**. No experiment was launched.

- `158 passed, 12 subtests passed in 9.08s` across seven related suites: new core regressions, actual outer entry-point tests, native Fix Loop, world foundation, R2 repairs, Qwen full repair, and executable-world screening.
- Tests run the actual protocol/driver logic with model and simulator boundaries mocked. The NumPy tests use the real installed NumPy. This is engineering verification, not an end-to-end Qwen/simulator result.
- Regression coverage includes a real failed initial result, a legitimate smoke alias, first/changed/repair candidate refusal, unexecuted and infrastructure-only records, mixed and malformed screening, stale mixed caches, missing solution files, final-attempt reservation, historical exhausted ledgers, and recoverable report gaps. The existing all-crashed-but-documented fallback still finalizes.
- Historical three-diagnostic tests retain their assertions using explicitly serialized old ledgers; the new admission guard now prevents constructing that bad state through normal calls. Fixture callbacks, valid candidate hashes and explicit fresh lineage were updated to match their real interfaces.
- **61 old R3 source/support files** match their stored hashes, including the relevant frozen runtime overlays. Existing experiment outputs were not modified.
- **30 local delivery files** exactly match the remote sources used for verification.
- The staging smoke froze and verified all 16 runtime support files in a temporary directory, including the repaired outer driver. It created no real experiment runtime.

Command (engineering sim checkout):

```sh
.venv-libero/bin/python3 -m pytest -q \
  tests/test_pipeline_repair_20261003.py tests/test_pipeline_driver_terminal.py \
  tests/test_native_world_fixloop.py tests/test_world_foundation.py \
  tests/test_foundation_r2_repairs.py tests/test_qwen_full_repair.py \
  tests/test_executable_world.py
```

Evidence:

- `artifacts/regression-final.log`
- `artifacts/verified-source-sha256.json`
- `artifacts/frozen-integrity.json`
- `artifacts/staging-smoke.json`
- `artifacts/core-reviewed.patch`
- `artifacts/baseline-bool-reproduction.json` and `artifacts/baseline-reproduction.json`

The prior drawer run remains failed/incomplete. This patch does not refund attempts, rewrite its status or produce a held-out result. A later authorized rerun must use the new package and a fresh output root.
