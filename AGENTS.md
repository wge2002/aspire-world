# ASPIRE Agent Guide

This is the model-neutral entry point for repository-aware coding agents. Read it before installing dependencies, starting services, launching experiments, editing code, or accessing runtime artifacts.

## Shared source and publication scope (2026-10-09)

Source publication (2026-10-24): the user authorized importing the Oct19 RS
wine-repair source snapshot into local `SZ-DEV` and pushing this publication.
Latest saved operator status is `stopped_by_user`: job `dlcghv217zqk880a`
stopped Oct19 at 14:42 Asia/Shanghai after 18/45 development attempts, zero
normal oracle successes and no held-out evaluation. Preserve attempts, clocks
and frozen files; historical launch authorization does not authorize resuming
or replacing the stopped job. The source snapshot is now under
`/mnt/data/aspire-world-deployment-20261019-wine-repair`; the old engineering
checkout `/root/github/aspire-world` was absent at this synchronization.
See [docs/SYNC_SCOPE.md](docs/SYNC_SCOPE.md) for publication provenance.

Latest authorization (2026-10-19): repair wine and launch ONE new engineering-assisted
wine repair study in Zhu Ziyue DLC workspace1493863/quotaquotal96jnu33mnl, preserving
all ended campaigns and records. Use wine r1's OWN development-only inputs; no other
repeat solutions or held-out feedback. Record a new source/runtime/study identity
and separate 3x15 development allowance (old45 remain in the old ledger). Keep local
Qwen/nativeCC/model settings, ordinary900s/14h/5h clocks, autonomous independent
round review and bounded same-cell closeout. Only current selected-bundle normal
oracle success plus ordinary freeze checks authorize held-out1-50. One eight-GPU
worker for this single wine cell; no DSW model/solver services. Preparation is not
execution. See [docs/DLC_WINE_REPAIR_20261019.md](docs/DLC_WINE_REPAIR_20261019.md).

Current RS handoff (2026-10-12): read [docs/RS_HANDOFF_20261012.md](docs/RS_HANDOFF_20261012.md).
Engineering checkout is `rs:/root/github/aspire-world`, simulation root
`/root/github/aspire-world/aspire/sim`, publication branch `SZ-DEV`.
The older Mac/rbs-debug paths below are historical. Do not edit frozen runtime
trees or infer that prepared cases are already running. The 2026-10-12 run in
workspace 1493863 stopped at its five-hour limit; see
[docs/DLC_RUN_20261012.md](docs/DLC_RUN_20261012.md). The latest 2026-10-13 request
authorizes a repair continuation of the same four cells on two DLC nodes (16
GPUs) in DEFAULT workspace 1000309, quota quotaabjvnuk8san. The user removed
this new round's overall five-hour limit; keep normal protocol watchdogs.
Preserve each cell's own parent evidence and cumulative three attempts per seed,
record a new corrected-runtime identity and repair-phase clock, and do not treat
historical successes as corrected-runtime validation. See
[docs/DLC_REPAIR_CONTINUATION_20261013.md](docs/DLC_REPAIR_CONTINUATION_20261013.md).
On 2026-10-14 the user explicitly paused this campaign for cause analysis.
Job `dlc6eu0wbbqxatvx` and both pods are confirmed Stopped; retain all ledgers,
clocks, frozen files and partial outcomes. Do not resume experiments or launch
replacement jobs until the user subsequently authorizes them.
The subsequent 2026-10-14 user request authorizes a NEW stove/wine repair study
on DEFAULT DLC workspace 1000309 after engineering fixes. Preserve the stopped
four-cell campaign. The new study has three rounds over development seeds 51–65
(one charged attempt per seed per round, at most 45 per task), coordinator
development-only review between rounds, and no 50-seed evaluation without a
current-runtime oracle-successful selected program and the normal freeze checks.
Record the new study identity and the supervised-repair method differences;
do not reset any old ledger. See [docs/DLC_SUPERVISED_REPAIR_20261014.md](docs/DLC_SUPERVISED_REPAIR_20261014.md).
2026-10-15 status: job `dlcxa3n63xgkmj21` exited after both cells completed only
round 1 and waited for an outer review that was never supplied. The 14-hour
development deadline expired at 04:29:58 Asia/Shanghai; rounds 2/3 did not run,
and held-out evaluation was skipped. Preserve the expired clocks and all evidence;
unused attempt allowance does not authorize a silent deadline reset or re-init.
The later 2026-10-15 user request explicitly authorizes fixing autonomous round
review and stove/wine policies, then continuing ONLY rounds 2/3 in DEFAULT DLC.
Keep the 15 spent attempts per task (30 additional maximum, 45 cumulative),
preserve the expired parent phase, and bind a separately named corrected-runtime
repair phase with one new 14-hour development clock. Round review must execute
autonomously on DLC with independent same-local-Qwen sessions and auditable,
root-validated approvals; it must not depend on a chat wakeup. New-phase normal
selected-bundle oracle success and normal freeze checks remain mandatory before
held-out 1–50. See [docs/DLC_AUTONOMOUS_CONTINUATION_20261015.md](docs/DLC_AUTONOMOUS_CONTINUATION_20261015.md).
Later on 2026-10-15 the user cancelled this queued/running continuation and
requested inspection/integration of a main-branch speed fix only. Job
`dlc5zw1sbv3vzijn` is confirmed Stopped (StoppedByUser), with zero allocated
nodes and no new development ledger or attempts. Preserve the prepared frozen
runtime and the 15 spent parent attempts per task. Do not resume or submit a
replacement experiment without subsequent user authorization.
When experiments move to DLC, stop their local DSW solvers and model/perception
services to release resources, preserving outputs, ledger identities and clocks.
Do not stop unrelated projects' processes or restart historical DLC jobs.

Latest authorization (2026-10-16): after asking for local testing, the user
explicitly switched to ONE four-GPU DLC worker in DEFAULT workspace 1000309
to continue stove/wine rounds 2/3. Preserve 15 spent parent attempts per task,
at most 30 additional, the unchanged expired parent clock, and the cancelled
Oct15 preparation. Use decode CUDA graphs and the validated colocated four-GPU
layout with two isolated cells and shared stateless services; bind a new runtime
identity and one new 14-hour phase clock only on actual init. This is a remaining
rounds continuation, not a fresh study or permission to resume historical jobs.
See [docs/DLC_UNIT4_CONTINUATION_20261016.md](docs/DLC_UNIT4_CONTINUATION_20261016.md).
Job `dlcg4a7t7ff5ox1g` was submitted at 23:29 Asia/Shanghai, Oct16 and cancelled
by the user's Oct17 migration request; confirmed Stopped / StoppedByUser before
any allocation, entry, new ledger, clock or attempt. Parent evidence hashes are unchanged. Runtime manifest SHA256
`3cc1c8bbe8c31143def1bf995c1c57cc43c67bd885b66aa3c32b4bf62f9ab93a`.
Preparation/submission is not execution; record actual start separately.

Latest authorization (2026-10-17): migrate to Zhu Ziyue workspace 1493863,
quota quotal96jnu33mnl, ONE eight-GPU worker for stove/wine x two repeats.
User confirmed the two original r1 cells continue ONLY rounds2/3 with their
15 spent attempts each (at most30 additional each); two NEW r2 cells start
independently with no historical code/config/feedback and at most3x15 attempts
each. Keep automatic independent same-local-Qwen round reviews for all four,
normal watchdogs and current-selected-bundle oracle/freeze gates. Do not restart
the cancelled default-workspace job or historical drawer campaign. See
[docs/DLC_UNIT8_FOUR_20261017.md](docs/DLC_UNIT8_FOUR_20261017.md).
Job `dlc12rui2zglkj7r` reached model/perception readiness but all four campaigns
failed before initialization at Oct17 11:32:08: root imports had created each
HOME/.cache root-owned, preventing the unprivileged Numba cache fallback.
No ledger, phase clock or new attempt was created. Preserve its completed
node attempt and archived operator logs. Private cache/config directories now
belong to their respective cell UIDs; all four actual-UID campaign imports pass.
Same cases/runtime/budgets are continued by retry job `dlc7fct8r6v95slf`, submitted
Oct17 11:42:19 Asia/Shanghai; Running since 11:43:36. Node attempt02
actually started 11:44:21. Shared services and post-load capacity checks passed;
all four ledgers initialized at 11:58:57, registered as R08/V07. All four campaigns
were running at the Oct17 14:32 snapshot. Each 14-hour development phase expires
Oct18 01:58:57 Asia/Shanghai; preserve these clocks on any ordinary resume.
Both r1 cells consumed autonomous round2 reviews and are developing in round3;
r2 cells consumed round1 reviews and are independently developing in round2.
At Oct17 16:41, only the two r2 cells remained active. Stove r1 exhausted its
30 additional attempts and exited development_failed; held-out was skipped.
Wine r1 closed round3 and exited
stage1_incomplete at 15:01:39 because eleven exhausted failed seeds lack their
required attempts/seed_N_BLOCKED.md notes. Preserve its selected bundle,
evidence and spent45 attempts; do not skip the normal check or reset clocks.
Final status checked Oct17 23:14: both r2 cells completed development, passed
the ordinary current-selected-bundle success/freeze checks, and completed
held-out seeds1-50. Wine r2 finished 19:44:05 and stove r2 20:12:21 Asia/Shanghai.
All four cells have exited; node teardown finished 20:12:40 and stopped Qwen
and the shared perception services. A/B exit1 and C/D exit0 make the aggregate
cloud job Failed; preserve the two complete r2 results and the two untested r1
records separately. Do not restart this ended job or alter clocks/budgets/frozen
files during status checks. Final scores and full per-cell evidence stay in
coordinator-private records.
This is a pre-init infrastructure retry, not a new study or budget reset.
The accepted runtime manifest is `runtime-freeze-v2.json`, SHA256
`e47624c88c4b6d19afd2f72db6ab76b4c2407d3a14b9faaa747db06b7e453662`.
The initial unlaunched manifest is retained; private independent-start inputs
are bound per case, excluded from the common readable runtime inventory.

Core code and protocols are synchronized across the remote engineering checkout,
local ASPIRE checkout, and `wge2002/aspire-world`; see [docs/SYNC_SCOPE.md](docs/SYNC_SCOPE.md).
Current source navigation is [docs/LATEST_EXPERIMENTS.md](docs/LATEST_EXPERIMENTS.md).
Historical priorities and authorizations below describe their original campaigns;
source synchronization does not start or resume any experiment.
Keep logs, raw results, media, weights, local credentials and frozen runtime trees
outside this source synchronization. The personal Git repository excludes the
real-robot workspace. For CaP-RL and associated robot-feedback/world-code runs,
use nonprivileged APIs with actual perception; never silently fall back to
privileged APIs. Preserve the user's explicit model, seed and budget choices.

## Codex Coordination

### Experiment result record (user request, 2026-10-14)

The outer coordinator maintains [docs/EXPERIMENT_RESULTS.md](docs/EXPERIMENT_RESULTS.md)
as a matrix: task names are columns, Git/source versions and repeats are rows,
and cells contain final success rates only (use a dash when no final result exists).
Keep short linked Git labels; separate runs with different frozen sources or repeats.
Preserve provenance and one record per campaign/task/method/repeat in
[docs/EXPERIMENT_RESULTS_DETAILS.md](docs/EXPERIMENT_RESULTS_DETAILS.md).
Add the records when an experiment actually starts;
record the launch-time full Git HEAD, branch, source modifications, runtime manifest
SHA256, job/workspace, outputs and parent phase if applicable. Update the row at
user-facing status reports and completion/cancellation; record final frozen seed
coverage, success rate and four outcome counts from actual held-out artifacts.
Missing historical Git metadata stays explicitly missing. A repair phase gets a
new record; ordinary resume/migration keeps the same record and budgets.
Both documents contain cross-cell and held-out results: coordinator/user access
only, keep the engineering copies root-private, exclude both from solver-readable
runtime snapshots, and preflight actual experiment-UID denial of the documents
and any copies. Never provide the table, details or summaries to experiment solvers.

When Codex coordinates implementation, follow [CC_USAGE.md](CC_USAGE.md). The CC
handoff rules apply on the RS server only; local implementation and verification
are handled directly by Codex (user clarification, 2026-10-10). On RS, substantial
code changes normally belong to Claude Code (CC). If the matching project session
is absent or CC has exited, start a dedicated CC in tmux in the confirmed project
checkout, reusing that project's previous settings, and hand off the work. Codex
may directly handle small changes for which a handoff is unnecessary. If CC has
actually started the task and its built-in model API (such as Opus) fails to
connect, Codex should preserve CC's work and take over implementation and
verification within the existing authorization without asking again. This API
failure exception also permits substantial code changes; a missing session alone
does not. Avoid simultaneous edits by CC and Codex during the handoff.

## Request Routing

### Current RS Streaming World line and shared comparison protocol (2026-10-10)

For this development line, follow
[the common LIBERO-Pro protocol](aspire/sim/docs/libero-pro-common-protocol.md)
and its [machine-readable constraints](aspire/sim/docs/libero-pro-common-protocol.json).
It fixes five `libero_goal_swap` tasks, C inputs without high-level strategy MD,
local Qwen3.8-Flash-Next/native CC 2.1.220/xhigh/1M/64K, nonprivileged actual
perception, oracle development gate, seeds 51–65 with three TOTAL charged
attempts per seed, and frozen evaluation 1–50. Trial/development/evaluation
watchdogs are 900 seconds/14 hours/5 hours; resuming never resets budgets.
Each method has five tasks x two independent fresh repeats (10 cells).
Declare method differences and keep cell isolation, frozen hashes, four outcome
classes and incomplete cells visible. These current explicit constraints
supersede historical defaults for this line only. Writing or synchronizing this
protocol does not authorize launching an entire comparison or a DLC allocation.

### Default for the ongoing world-code study

Current priority (2026-09-20): the user stopped further A work and requested
direct C repair, optimization and ablations of the Claude-discussed self-evaluation
and rehearsal/replay mechanisms. Do not launch or resume A to fill a comparison.
Drawer A2 was cancelled with its three charged attempts retained. Continue in
`aspire/sim/docs/experiments/code-world-c-opt-ablation-20260920/`; this C repair
study may use the same declared prior C program and development-only evidence
as the starting point for each arm. Label it a repair/continuation study, preserve
arm isolation after that common input, and never supply A solutions or held-out
trajectories to C solvers. The earlier A/B/C request below is historical scope;
the original Fix Loop budgets, native model settings and seed boundaries remain.

API preference clarified by the user on 2026-09-20: world-code experiment
generation/repair continues through jkwl (`https://jkwl.dmxapi.cn`). Temporary
insufficient credit means waiting for jkwl to recover, not switching experiments
or inference probes to RBS. The user's RBS question was a routing check, not a
provider-change authorization. Report engineering CC and experiment solver
routes separately; keep frozen simulator-only evaluations running normally.

On 2026-09-14 the user superseded the earlier scripted 1+15+50 default:
"对的，现在完全改回去，原本fix loop的基本上settings。30次之类的也完全没必要。
然后跑三个完整实验看看。"

Use the original LIBERO Fix Loop and native Claude Code agent/tool workflow for
new world-code experiments: inspect an initial public scene, generate initial
code, evaluate development seeds 51–65, diagnose each failed seed, repair and
retest within the original per-seed replay budget, select a generalizable program
using development evidence, then freeze and evaluate every seed 1–50. Record
replay accounting explicitly; three replay attempts per development seed is an
execution budget, not a requirement to generate 45 different programs. Do not
retain the scripted global 15-revision ceiling or automatic one-version/next-seed
schedule. Preserve original prompts, API references, native tools/context and
model settings except for the declared MD/world condition differences.

Remove the diagnostic-specific 30-motor-call, one-recovery and four-extra-world-
query caps from this new profile; do not replace them with other arbitrary action
or recovery limits. Retain normal simulator/process watchdogs, allowed-API rules,
credential separation and development/held-out boundaries. World is an opt-in
addition to the ordinary workflow, not a replacement debugging pipeline.

The requested fresh bowl conditions are A (original MD, no world), B (same MD,
world), and C (no high-level MD, world), one complete independent experiment per
condition with Opus 4.6. Do not share generated code, feedback or newly learned
skills between conditions. Preserve all existing frozen runtimes and outputs;
their historical 1+15+50 identities remain valid records, not the new default.

### Experiment authorization and preflight

Follow the user's global experiment-authorization rule in
`/Users/wge/.codex/AGENTS.md`: requests such as "新跑一轮", "继续实验", and
"跑起来" authorize ordinary experiment work that is reasonably within the
user's expectations. Exact task/seed/model/resource enumeration is not required;
resolve routine details from the full conversation and project context, report
material assumptions, and proceed. Keep preflight checks. The confirmation
language below does not require a second go-ahead for work already covered by
that request. Ask only for a consequential unresolved choice, a material change
beyond reasonable expectations, or an actual enforced restriction. Preserve
explicit constraints, held-out boundaries, frozen-run integrity and old outputs.

### Canonical LIBERO-Pro Quick Start

The canonical request is:

```text
Run the complete ASPIRE LIBERO-Pro Goal-Swap Quick Start for all ten tasks
in the libero_goal_swap suite.
```

For this request, read and follow:

1. [`aspire/sim/README.md`](aspire/sim/README.md)
2. [`aspire/sim/CLAUDE.md`](aspire/sim/CLAUDE.md)
3. [`aspire/sim/.claude/libero/CLAUDE.md`](aspire/sim/.claude/libero/CLAUDE.md)
4. [`aspire/sim/.claude/libero/fix-loop/QUICKSTART.md`](aspire/sim/.claude/libero/fix-loop/QUICKSTART.md)

Before any setup, service start, subagent dispatch, replay, or evaluation, provide a preflight report covering the host, GPU mapping, credentials and gated weights, required services, seed partitions, expected runtime, and output paths. Proceed when this is reasonably within the user's requested work under the experiment-authorization rule above. Obtain clarification only for consequential unresolved choices, material departures from reasonable expectations, or actual enforced restrictions.

### Canonical BEHAVIOR-1K ASPIRE Protocol

Canonical requests are:

```text
Follow the protocol and run BEHAVIOR-1K Soda Can ASPIRE experiments.
```

```text
Follow the protocol and run BEHAVIOR-1K Radio ASPIRE experiments.
```

For either request, read and follow:

1. [`aspire/sim/README.md`](aspire/sim/README.md)
2. [`aspire/sim/CLAUDE.md`](aspire/sim/CLAUDE.md)
3. [`aspire/sim/.claude/behavior/CLAUDE.md`](aspire/sim/.claude/behavior/CLAUDE.md)
4. [`aspire/sim/.claude/behavior/fix-loop/SKILL.md`](aspire/sim/.claude/behavior/fix-loop/SKILL.md)
5. [`aspire/sim/.claude/behavior/fix-loop/INSTRUCTIONS.md`](aspire/sim/.claude/behavior/fix-loop/INSTRUCTIONS.md)

The named task resolves the task choice, but it does not waive preflight. Before
installing or changing dependencies, starting services, dispatching agents, or
running a trial, report the protocol commit, host and GPU, environment status,
model, fixed per-seed budgets, seed partitions, expected runtime, and fresh
campaign output path. When the run is reasonably within the user's requested
work, the coordinator may execute the complete protocol autonomously and resume
it from its campaign state file without a second confirmation. Ask only for a
consequential unresolved choice, a material departure from reasonable expectations,
or an actual enforced restriction.

### Other simulation experiments

Use `aspire/sim` as the working root. Read [`aspire/sim/.claude/README.md`](aspire/sim/.claude/README.md), the selected suite constitution, and the experiment's `INSTRUCTIONS.md` and `SKILL.md`.

Resolve the suite, experiment and ordinary settings from the full conversation,
current goal, registry and prior rounds; the user need not name every parameter.
State material assumptions and proceed when they are reasonably within the
requested work. If genuinely different research objectives or consequential
choices remain unresolved, present the relevant registry choices and ask only
about that missing decision. Before a paper-scale run, report its scope, expected
trial count and runtime, compute, credentials, services, and outputs. Request
additional confirmation only when that run materially exceeds the user's
reasonable expectations or an actual enforced restriction requires it.

### Real-robot work

Use `aspire/real` as the working root and read [`aspire/real/AGENTS.md`](aspire/real/AGENTS.md). A simulation or documentation request never authorizes starting robot services, opening cameras, contacting follower processes, enabling motion, or accessing physical hardware.

## Repository Rules

- DLC jobs must request a total of 4, 8, or a multiple of 8 GPUs (N workers x 8;
  widened from 4/8/16/24 on 2026-10-05). When convenient, consolidate independent
  experiments into one multi-worker job to reduce job count, with explicit worker
  assignments (or a shared work queue) and separate outputs. Do not
  request 5 GPUs or infer other counts from process concurrency. Follow
  `rbs-debug:/mnt/home/gewang/DSW_DLC_WORKFLOW.md` for node and GPU mapping.
- Do not push to any remote unless the user explicitly requests it.
- Do not delete, overwrite, or mix existing experiment outputs without explicit confirmation and the applicable clean-slate procedure.
- Preserve development and held-out seed boundaries exactly.
- Do not substitute external baseline code or outputs when a runbook forbids them.
- Keep credentials in approved environment variables or protected files and out of generated-code processes, logs, prompts, YAML, Markdown, and committed files.
- Treat generated Python as untrusted. Trial isolation and watchdogs are reliability mechanisms, not a hardened security sandbox.
- Do not expose simulator ground truth or other forbidden APIs to generated programs. The selected suite constitution is authoritative.
- Report commands, outputs, artifact paths, blockers, and deviations precisely. Never claim an experiment completed when required manifests or trials are missing.

## Workspace Boundaries

- Project overview and navigation: repository root
- Simulation setup and execution: `aspire/sim`
- Real-robot setup and execution: `aspire/real`

Keep simulator dependencies, coordinates, APIs, and artifacts separate from physical-robot workflows.
