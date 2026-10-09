# ASPIRE Agent Guide

This is the model-neutral entry point for repository-aware coding agents. Read it before installing dependencies, starting services, launching experiments, editing code, or accessing runtime artifacts.

## Shared source and publication scope (2026-10-09)

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

When Codex coordinates implementation, follow [CC_USAGE.md](CC_USAGE.md). Substantial
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
