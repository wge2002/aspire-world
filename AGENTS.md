# Personal ASPIRE Agent Guide

Read README.md and docs/REPOSITORY_SCOPE.md first. This is a curated personal simulation source repository derived from NVlabs/ASPIRE. The original checkout and experiment outputs are external and must not be deleted or overwritten.

- Use aspire/sim as the simulation working root; read its README.md, CLAUDE.md, .claude/README.md and selected runbook before executing.
- Current research documentation is indexed by docs/LATEST_EXPERIMENTS.md. Historical authorization, job IDs, status, deadlines and budgets quoted in copied documents describe their original campaigns; they do not authorize new launches in this repository.
- Clear user execution requests authorize ordinary implementation and verification in scope. Questions and tentative discussion do not authorize changes or new experiments. Do not push without explicit user instruction.
- Keep original development 51–65 and held-out 1–50 boundaries and per-seed replay accounting. Do not import held-out feedback into solvers. Preserve frozen bundles and existing outputs.
- CaP-RL and associated robot-feedback/world-code runs use nonprivileged APIs and actual perception. Verify constructed classes and live calls; never silently switch to privileged APIs.
- Preserve explicitly selected models, providers and effort. The latest recorded Qwen solver profile is local qwen3.8-flash-next, xhigh, 1M context and 64K configured output; see docs/QWEN.md. Engineering CC settings are separate.
- Keep logs, results, generated trajectories/media, checkpoints, weights, credentials and local settings outside Git. New experiment files require explicit entries in aspire/sim/docs/experiments/.gitignore. Do not force-add excluded artifacts.
- Keep LICENSE, NOTICE, LICENSES and source copyright headers. A personal remote does not change upstream authorship or component licenses.
- Follow CC_USAGE.md for substantial implementation handoff. Do not restart or advance historical experiments solely because their source is present. No physical robot code is included; simulation work never authorizes physical hardware access.
- Verify results with tools and report concrete limitations. Existing host-specific staging scripts and missing external references are documented; do not claim fresh-machine reproduction without validating them.
