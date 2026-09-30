# Foundation R2: basic bug repairs

Engineering CC accepted this task but failed its first model request with HTTP403 insufficient quota. Codex verified the current failure after a continuous300-second wait and took over under AGENTS.md. Experiment inference remains local Qwen3.8-Flash-Next.

## Changes

- `cap/world_model/executable_world.py`: offline policies raising SystemExit(None/0) now persist a report with unsupported/unknown, rather than disappearing without a report and stopping the campaign as an infrastructure failure. Nonzero exits are authored program errors. A SystemExit during world module loading is also an authored error and restores the prior world module. KeyboardInterrupt remains an interrupt. No clean process exit is counted as robot success.
- New-study `support/two_task_scope.py`: CC2.1.220 contains a native subagent report-file restriction matching findings.md. Workers now return their full report between explicit markers; the existing coordinator writes it verbatim before the required completion check. The worker still generates/selects its tested program and writes task_analysis.md and per-seed blocker notes. Missing report text goes back to the same worker. No Bash workaround, global guard change, extra trial or strategy is introduced.
- New-study native compatibility fixture additionally exercises Agent report return → coordinator Write findings.md → exact-content verification, alongside the existing images/compaction test. Each job must pass this gate before formal development.

## Validation

First related suite run:83 passed,12 subtests passed; one new test reached successful completion check but had a fixture KeyError when counting attempts. The fixture was corrected to count the ledger's executed rows. All12 new tests then passed, including real offline subprocess classification in both bindings, world-load cleanup, interrupt preservation, rendered worker/coordinator contract, file-boundary checks, and all-failure budget exhaustion through finalize without new attempts. Across the two runs84 distinct tests passed plus12 subtests. No simulator or model was used by these regression tests.

Real task-specific perception preflights and frozen-runtime platform gates are recorded separately at launch. These validations do not establish improved task success or meaningful world use. Existing foundation method, Qwen settings, budgets and held-out boundaries remain unchanged.
