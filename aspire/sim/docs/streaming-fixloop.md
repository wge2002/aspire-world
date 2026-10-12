# SAM2-streaming native Fix Loop: development and held-out interfaces

Profile `sam2-streaming-native-v1` under
[`libero-pro-common-v1`](libero-pro-common-protocol.md). This document covers
the development-stage accounting CLI and the outer held-out runner. It does not authorize starting a
model service, a simulator trial, a campaign or a DLC job. No benchmark result
exists for this profile.

## What the solver authors and what is engineering

| Input | Author | Frozen as |
| --- | --- | --- |
| `cap/world_model/World.py`, `sam2_streaming.py`, `streaming_bridge.py`, `streaming_replay.py`, `control_progress.py`, the LIBERO env hook, `replay_trial.py` | engineering (declared method runtime, **not** solver-generated) | `identity.engineered_world` + part of every bundle's `world` digest |
| SAM2_streaming source tree and checkpoint | external backend | `identity.sam2` + bundle `world` digest |
| policy (`*_code.py`), including relation predicates, checkpoints and optional predictions through the injected World helpers | cell solver (Qwen via native CC) | bundle `policy` |
| `*_streaming_config.json`: `objects` (public SAM3 seed prompts), optional `cadence`, `notes` | cell solver, from this cell's public observations | bundle `world` digest |

No object inventory, strategy MD or historical solution is supplied
(condition C). An authored config cannot set `mode`, `sam2`, `camera` or any
other runtime field. A prompt that cannot seed exactly one public SAM3 mask is a
charged program fault (`world_program_error`). Other startup failures, such as
missing weights, are charged infrastructure errors.

## Accounting (unchanged ledger)

`StreamingFixLoopState` subclasses `NativeWorldState`. Retry admission, initial
freezing, alias-smoke, BLOCKED notes and rejected revisions are unchanged:

* There are three TOTAL charged executions per seed 51–65 (45 per cell).
  Smoke, initial, repair and diagnostic runs are all charged, including timeouts,
  crashes and interrupted runs. An edit or a restart never refunds one.
* The single public scene `snapshot` is uncharged. It runs the framework
  script without the streaming profile.
* The initial bundle (policy + streaming config) is frozen for the whole
  initial batch, and every seed must have initial evidence before any repair.
* There is no global revision limit and no motor, query or recovery cap. Each
  trial keeps the 900 s watchdog and `max_steps=4000`.

Differences declared for this profile:

* **Persisted development deadline.** At `init` the ledger records
  `phase_clock.development = {started_at, watchdog_seconds: 50400, deadline}`.
  A resume reads the same deadline and never refreshes it. Each trial watchdog
  is clipped to the remaining window. After the deadline, nothing more is
  admitted, `select` is closed and the unspent budget stays unspent.
  `check` then reports `terminal`.
* **Program error is never a pass.** A trial counts as a development success
  only if `status == complete`, `sandbox_rc == 0` and the oracle reports
  `task_completed`.
* **No observation fallback.** Only a bundle that actually executed in
  development can be selected.
* Seeds 1–50 are refused before any ledger row is written. The development
  CLI has no held-out subcommand; held-out is a separate outer runner (below).

The identity binds the cell (`method`, `repeat`, `task`), the model, the
service, effort and capacities, the CC version, devices, perception URLs, the
config hash, the protocol sources, the engineering World files and the SAM2
source and weights. If any of these changes, the cell cannot be resumed.

## Service, perception and dependency freeze

`case.model_service` is not trusted as a dict. `server_manifest` names a small
JSON record written when the service was launched (`endpoint`,
`served_model`, `max_model_len`). Every identity computation reads that file,
checks its sha256 against `server_manifest_sha256`, and requires the recorded
endpoint and served model to match the case and `max_model_len` to equal 1M.
The endpoint must be a loopback server root. Model weights are not rehashed per
trial; the fixed manifest binds them.

A formal case (`dependency_evidence: "frozen"`, the default) must also set
`require_runtime_freeze: true`, `runtime_manifest` + `runtime_manifest_sha256`
(the `native_cc_freeze` manifest: sources, dependency links, external files,
executables) and `perception_manifest` + `perception_manifest_sha256`
(recording `sam3_url`, `graspnet_url`, `pyroki_url`). These are re-verified
before and after every development trial and held-out seed. CPU fixtures may
set `dependency_evidence: "cpu-test-fake"`, which lets the runtime and
perception manifests be absent. That value is copied into the ledger, the
stage-1 result and the held-out report, so it is never mistaken for real
evidence. The example case has only placeholders and does not load as a real
cell.

## Finalize: provenance is recorded, not declared

`finalize` keeps three things apart in `native_provenance`:

* `configured`: the case values (model, xhigh, 1M, 64K, endpoint).
* `worker_settings`: the settings file the native launcher actually passed to
  the worker (`--worker-settings`, same shape as
  `native_world_campaign.local_vllm_settings`). It must show `model`,
  `effortLevel=xhigh`, `ANTHROPIC_BASE_URL` = the loopback endpoint,
  `CLAUDE_CODE_EFFORT_LEVEL=xhigh`, `CLAUDE_CODE_ALWAYS_ENABLE_EFFORT=1`,
  `CLAUDE_CODE_MAX_CONTEXT_TOKENS=1000000`,
  `CLAUDE_CODE_MAX_OUTPUT_TOKENS=64000`, and no `apiKeyHelper`.
* `probe`: the recorded native probe of the local route (`--probe`,
  `native_world_campaign.probe` shape). It must have exit 0, the served model,
  context window `[1000000]` and configured xhigh/1M/64K.
* `observed`: from the transcripts. Every served model must be the solver
  model, and the actual context windows must be exactly `[1000000]`. Missing
  windows fail.

As in the legacy probe, Claude Code reports a custom alias's built-in default in
`modelUsage.maxOutputTokens`. So the 64K limit is proven by the effective request
setting, and the reported value stays visible as `reported_max_output_tokens`.
Any missing or mismatched record refuses finalize.

## World-use evidence per trial

The trial directory gets `streaming_world_snapshot.json` (final public state)
and `streaming_world_journal.jsonl`. The journal records:

* seed, reacquire and failed-reacquire events, with frame IDs and timestamps
* each object frame (status per identity, but not geometry values)
* tracking loss
* relation checks
* checkpoint interrupts and errors
* episode resets and the final snapshot
* a summary of counts, including robot updates and continuing checkpoints,
  with the first and last robot update times

The journal contains no simulator state, oracle label or credentials. If the
SAM2 session fails at startup, the replay writes
`streaming_world_startup_error.json` (`authored: true|false`).

## CLI

From `aspire/sim`, with `ASPIRE_NATIVE_CASE` pointing at a fresh case. Copy
`env_configs/libero/streaming_fixloop_case.example.json`. The control directory
must be outside the repo; the cell lives in `<control>/cell`.

```bash
P=scripts/libero/streaming_fixloop_protocol.py
.venv-libero/bin/python3 $P init                       # outer coordinator, once
.venv-libero/bin/python3 $P snapshot                   # uncharged, seed 51, once
.venv-libero/bin/python3 $P trial --phase smoke|initial|repair|diagnostic --seed N \
    --code "$TASK_DIR/initial_code.py" --streaming-config "$TASK_DIR/initial_streaming_config.json"
.venv-libero/bin/python3 $P alias-smoke --seed 51
.venv-libero/bin/python3 $P status
.venv-libero/bin/python3 $P select --reason "<development evidence>"   # fix_code.py + fix_streaming_config.json
.venv-libero/bin/python3 $P check
.venv-libero/bin/python3 $P resolve-interrupted --directory development/initial/seed_N/attempt_K  # outer only
.venv-libero/bin/python3 $P finalize --transcript <native stream-json> \
    --worker-settings <settings the worker ran with> --probe <control>/probe.json  # outer only
```

Each trial runs `replay_trial.py ... --args.replay-code <attempt>/code.py
--args.streaming-world-config <attempt>/streaming_world_config.json`. It never
adds `--args.world-model-config`, `--args.ordinary-budget-config` or
`--args.interactive`. The SAM2 source is prepended to `PYTHONPATH`, and
credential variables are stripped.

## Held-out evaluation (outer coordinator only)

`scripts/libero/streaming_fixloop_heldout.py` evaluates one frozen cell on
seeds 1–50. It is a separate script; the solver never calls it and never sees
its outputs.

```bash
echo "$CELL_ID" > "$CONTROL/OUTER_HELDOUT_AUTHORIZED"   # outer coordinator writes this
.venv-libero/bin/python3 scripts/libero/streaming_fixloop_heldout.py --case "$CASE"
.venv-libero/bin/python3 scripts/libero/streaming_fixloop_heldout.py --case "$CASE" --resume
.venv-libero/bin/python3 scripts/libero/streaming_fixloop_heldout.py --case "$CASE" --resolve-interrupted N
```

**Freeze.** Before the first seed the runner requires the following:

* a complete stage-1 result and development ledger
* a current `identity()` equal to the development ledger's identity
  (engineering World, SAM2 source and checkpoint, model-service, perception
  and runtime manifests, config, protocol, model, task)
* `fix_code.py` and `fix_streaming_config.json` copied once to
  `<control>/heldout/frozen/` and re-digested to exactly the stage-1
  `selected_bundle` and the ledger selection
* at least one completed graded development row (smoke, initial or repair) of
  that bundle

A forged `tested_bundles` entry or a stage-1 flag is not accepted on its own.
`frozen_manifest.json` labels authors as follows:

* cell solver (Qwen): the policy, its relation predicates, the public prompts
  and the cadence
* engineering, not solver-generated: the engineered World
* `object_inventory` and `strategy_md`: `null`

**Per seed.** The runner is serial and holds an exclusive lock. For every seed
it does the following:

1. Re-verify the runtime freeze, cell identity and frozen bundle.
2. Record the row as `running` in `heldout_state.json`.
3. Run `replay_trial.py` with the same frozen `code.py` and the materialized
   streaming config.
4. Re-verify everything.
5. Record the final status.

A change detected after a seed makes that seed `infrastructure_error` and stops
the sweep. A change detected before a seed stops the sweep with the seed
unstarted. Files for a seed with no ledger row also stop the sweep; they are
kept, never overwritten.

**Scoring** follows `native_world_heldout.py`:

| Status | When |
| --- | --- |
| `success` | Normal exit with `sandbox_rc == 0` and the oracle's `task_completed`. |
| `failure` | Normal exit with `sandbox_rc == 0`, but the oracle did not report completion. |
| `crash` | `sandbox_rc != 0`, even when the oracle reported success. An authored seed prompt that cannot seed is also a crash. |
| `infrastructure_error` | Runner error, timeout, nonzero process exit or a verification failure. These are reported separately in `infrastructure_error_seeds`. |

`success_rate = success / 50` always.

**Deadline and recovery.** The first start records
`phase_clock.evaluation = {started_at, watchdog_seconds: 18000, deadline}`. A
resume never resets it. Each seed's watchdog is `min(900, remaining)`. After the
deadline no seed starts, and missing seeds are reported as `not_run` with
`incomplete: true`. No execution result is generated for them.

If the runner dies mid-seed, the row stays `running` and a resume refuses to
continue. `--resolve-interrupted N` settles that row from its `lifecycle.json`
and result directory, and refuses while the process is alive. The settled row
records `recovery.replayed: false`. The consumed run becomes a result and is
never re-run.

`heldout_result.json` records the following:

* identity and frozen manifest
* phase clock and `stop_reason`
* `completed` / `incomplete`
* counts, including `not_run` and `running`
* per-seed statuses
* `not_run_seeds`, `unresolved_running_seeds` and
  `infrastructure_error_seeds`
* durations and failure reasons
* `dependency_evidence`

The exit code is 0 only when all 50 seeds are recorded and none is
infrastructure.

**Not a security sandbox.** The outer marker, the separate `<control>/heldout`
directory and these checks protect experiment integrity only. Generated code
runs with full import access. Isolation of solver from held-out outputs still
depends on the host/container and the guard rules of the campaign
launcher.

## Common environment fix (all new comparisons)

`libero-reset-v2-2026-10-10` (see the common protocol §1) is shared by every new
cell and is not a streaming/SAM2 method factor. `LiberoHandle.reset` now does
`env.seed(seed)` → one `env.reset()` → one `set_init_state(init_states[(seed-1)%N])`
(seed `None` → 0) and returns that call's observation; `FrankaLiberoEnv.reset`
delegates to it and no longer resets again, then runs the existing 10 settling
steps inside the streaming `reset_scope`. CPU tests: `tests/test_libero_reset.py`.
Old frozen runtimes and outputs are not changed.

`libero-pro-common-env-v3-statepack-2026-10-10` replaces the v2 seed mapping (the
v2 reset sequence is kept). The `(seed-1)%50` mapping made development seeds
51-65 reuse test states 1-15. Now a hash-bound registry
(`cap/integrations/libero/initial_states.py`) maps test seeds 1-50 to the original
init-file slots 0-49 (file unchanged) and development seeds 51-65 to 15 separate
development states per task. Formal cases must bind `initial_state_manifest` and
its sha256. The protocol exports them only to the trial process
(`ASPIRE_LIBERO_INITIAL_STATE_MANIFEST`, `..._SHA256`, `..._REQUIRED=1`). Every
reset re-verifies the manifest, the task/suite/seed mapping and the selected
file, and never falls back to modulo. The states are generated once, outside the
source checkout, by the outer-only
`scripts/libero/prepare_common_initial_states.py`. The guard denies the
statepack, the manifest and the generator, and `check_authored` refuses
state/asset access (`get_sim_state`, `set_init_state`, `init_states`, `np.load`,
`.npy`/`.bddl`/`.xml`/... literals). CPU tests:
`tests/test_common_initial_states.py` (fixtures: `tests/statepack_fixture.py`).

## Oracle refusal in authored files

The development gate is the outer oracle, but the policy and its streaming config
cannot query it while they run. `collect_bundle` calls `check_authored`, which
applies the legacy ground-truth check and also the sealed-cell
`development_gate.source_errors` patterns. Those patterns cover
`task_completed`, `compute_reward`, `check_success`, `APIS`, `__self__`,
`low_level_env` and every `handle.` attribute except `task_language`. A refusal
happens before admission, so it charges nothing. Legacy oracle cells keep
`check_policy`'s default.

## Campaign launcher (one cell)

`scripts/libero/streaming_fixloop_campaign.py --case <case.json> [--resume] [--no-heldout] [--render-only]`

**Who runs it.** The outer deployment runs it via `runuser` as the cell's own
unprivileged user. It is never run as root and never with a repurposed HOME.

**Preflight.** Before anything runs, the launcher verifies and records the
following. It does not create any of them.
* The OS user: uid, gid, name and HOME.
* The source runtime is read-only to that user and is not the `/root` checkout.
* The control directory is writable.
* The native binary: path, sha256 and `--version`, which must be 2.1.220.
* The outer task manifest: suite, task, `environment_version`, seed mapping and
  `LIBERO_CONFIG_PATH`, plus the config sha256. It records hashes only, never
  asset content.
* The runtime freeze.
* The model-service and perception manifests.
* Perception reachability. This is a health check only; nothing is started.

**Session records.** Each session writes to `<control>/sessions/NN/`:
* `worker-settings.json`: exactly the `--settings` passed to the worker. It is
  `local_vllm_settings` (every alias resolves to the local Qwen, xhigh, 1M
  context, 64K output, no apiKeyHelper) plus the dedicated guard hook.
* `worker-environment.json`.
* `probe.json`: a tiny native call with no task content. It must actually serve
  the model with a 1M context window.
* `native-command.json`: the argv, with the prompt and settings replaced by sha
  references, and `dangerously_skip_permissions: false`.
* The rendered prompts and their diff against the pristine template.
* The coordinator transcript.

Wire-level request capture is not performed. Only these recorded settings,
the probe and the transcript `modelUsage` are claimed.

**Native invocation.** The legacy `native_command` is used: `--setting-sources ""`,
an empty strict MCP config, `--permission-mode dontAsk` and the legacy tool
allowlist.

**Guard.** `scripts/common/streaming_fixloop_guard.py` is a PreToolUse hook on
Read, Glob, Grep, Write, Edit, MultiEdit, NotebookEdit and Bash.
* Writes are limited to this cell's `initial_/fix_code.py`,
  `initial_/fix_streaming_config.json`, `diagnostic_session.py`, the
  notes/analysis/task_analysis/findings files, `attempts/seed_N_BLOCKED.md`, and
  the `analysis/` and `skills/` directories (cell-local, never shared).
* Reads are refused for: the strategy library, outputs and working codes,
  `docs/experiments`, `docs/logs`, the LIBERO sources and assets, other cells,
  `<control>/heldout` and the outer marker.
* Bash allows one foreground call per command, to the `status`, `check`,
  `snapshot`, `trial`, `alias-smoke` or `select` subcommands of the streaming
  protocol. It refuses `init`, `finalize`, `resolve-interrupted`, `--case`,
  loops, background jobs, raw replay, held-out, the campaign script, and any
  mutation of the runtime, ledger or evidence.
* Any guard failure fails closed. Every decision is audited in
  `<control>/guard-audit.jsonl`.
* The guard is an integrity control, **not a security sandbox**.

**Sequence.**
1. preflight → probe → `init`. This starts the persisted 50,400 s development
   deadline.
2. One coordinator session, with its timeout set to the time left before that
   deadline.
3. Freeze check → `check` → `finalize` with all session transcripts and the
   session's settings and probe.
4. Unless `--no-heldout`: create the outer marker after the solver has exited,
   then run `streaming_fixloop_heldout.py`.

**Exit status.** The worker's actual exit is recorded as it is. A failed
`finalize`, such as missing provenance, ends the cell as `incomplete`.

**Restart and resume.**
* There is no hidden restart: once the ledger exists, a second launch is
  refused.
* `--resume` is a recorded outer action. It opens a new session but never
  re-inits, never refreshes the deadline and never refunds budget.
* After the deadline, no solver is started.

**Scope.**
* One case is one cell. Multi-cell and DLC keys are refused, and nothing here
  schedules 20 cells.
* The control path may not contain a guard-denied component such as `outputs`
  or `heldout`.
* The first formal cell is a new `bowl_sam2streaming_r1`.

## Deployment status (2026-10-10)

The RS deployment has produced and verified the model-service, perception,
runtime-freeze and task manifests, the unprivileged user, the read-only runtime
and the per-cell directory. The launcher verifies these inputs; a new host must
prepare its own inputs rather than reuse another host's paths or credentials.

The first formal `bowl_sam2streaming_r1` cell passed preflight and entered native
development at 2026-10-10 10:14:34 UTC. Its earlier permission failure occurred
before ledger initialization and remains recorded. The original development
deadline and charged-attempt ledger apply to every subsequent continuation.
This startup is not a completed benchmark result. Runtime records, generated
programs, state packs and eventual held-out results remain outside source
synchronization; the running frozen runtime retains its original source hashes.
