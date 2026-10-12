# SAM2-streaming DLC entries (2 workers x 8 L20Z)

These entries handle operational orchestration only. Profile is `sam2-streaming-native-v1`
under [`libero-pro-common-v1`](libero-pro-common-protocol.md). Each cell is
still run by the frozen runtime's own
`scripts/libero/streaming_fixloop_campaign.py` (see
[streaming-fixloop.md](streaming-fixloop.md)), as that cell's unprivileged user.
Generation, repair, selection, freeze and held-out stay there. The entries have
no LLM client and no agent loop. Nothing here submits a DLC job. A failure is
recorded and the entry exits; it never resubmits, refunds budget or forges a pass.

## RS validation before DLC (2026-10-11)

The user requires only a few real generation, execution, feedback and repair
rounds on RS before moving the work to DLC. Completing all development seeds or
all 50 held-out seeds on RS is **not** an admission requirement. The common
protocol's budgets, isolation, frozen evaluation and reporting requirements still
apply to the complete experiment on DLC. Existing native RS development evidence
may establish this operational check; do not add another full RS campaign merely
to repeat it. Model weights remain fixed during these code-repair iterations.

Keep existing cells and infrastructure failures intact. A finalized RS cell may
continue its frozen evaluation; it must not restart development. The current
four-cell allocation uses an independent bowl repeat 2 rather than restarting
the finalized bowl repeat 1. No code or feedback is shared between repeats.

| File | Role |
| --- | --- |
| `scripts/common/streaming_dlc_entry.py` | Shared pieces: case/protocol checks, POSIX identity checks, asset/weight/version checks, service specs from the recorded manifests, synthetic readiness requests, cell-user probes (identity, CUDA, NVIDIA EGL render, EGL→CUDA mapping), process lifecycle. It also keeps the earlier single-node one-cell entry. |
| `scripts/common/streaming_dlc_pair.py` | The authorized job shape: 2 workers, each with one shared Qwen and two independent cells. |
| `docs/streaming-dlc-pair.example.json` | Real known paths and hashes, with field notes. The four case sha256 values are placeholders until the outer side writes the cases. |

## Layout per node (fixed)

| | Qwen (one, shared) | SAM3 + GraspNet | simulator / SAM2 / EGL | Pyroki | ports |
| --- | --- | --- | --- | --- | --- |
| slot A | GPUs 2,3,4,5, `:8121` | 0 | 1 | CPU | 8214 / 8215 / 8216 |
| slot B | (same Qwen) | 6 | 7 | CPU | 8114 / 8115 / 8116 |

| DLC `RANK` | slot A | slot B |
| --- | --- | --- |
| 0 | new `bowldrawer_sam2streaming_r1` (uid/gid 30010) | new independent `bowl_sam2streaming_r2` (30013; GPU 7/EGL 7, perception 6, 8114-8116, model 8121) |
| 1 | new `drawer_sam2streaming_r1` (30011) | new `wine_sam2streaming_r1` (30012) |

There is never more than one Qwen per node, because two TP4 engines would take
the perception GPUs. The Qwen engine argv is the recorded
`qwen-model-service-manifest.json` argv byte for byte: TP4, 1M max-model-len,
`max-num-seqs 4`, parsers, image limit, RoPE overrides. Its env is unchanged
too: CUDA 13 compat/toolkit and `VLLM_PLE_CPU_OFFLOAD=1`. Only these are
adapted, and each is recorded:

* cwd, rebased from the RS `/root` checkout to the frozen runtime;
* a node-local copy of the prebuilt FlashInfer workspace;
* offline per-node cache variables.

Capacity was not lowered to pass preflight. Whether one engine with 4 sequences
and about 2.21x full-1M KV can sustain two cells has **not** been measured.
No dual-cell speed claim is made.

The earlier repeat-1 migration plan is historical: that solver has finalized.
Its original ledger, deadlines and frozen files remain on RS. The repeat-2 cell
has a separate user, HOME, control directory and fresh generation context.

On 2026-10-11, after bowl repeat 2 completed, the user authorized a separate
8-GPU DLC node for `stove_sam2streaming_r1` (`turn_on_the_stove`). The original
two-node job remains untouched. Use the single-cell entry, UID/GID 30014 and a
separate HOME/control directory; never reuse bowl's generated code, feedback or
learned skills. The new node keeps Qwen on GPUs 2–5, perception on 6 and
simulator/SAM2/EGL on 7. It serves one cell instead of two, a recorded deployment
and concurrency difference; model/request parameters and common experimental
conditions remain fixed. Its 21-hour job envelope covers startup plus the full
14-hour development and 5-hour evaluation watchdogs. Total assigned DLC GPUs
become 24 when the additional node is allocated.

Perception services use the recorded start manifest. Slot A changes only the
GPU and the listen port, and both changes are recorded as `adaptations`.
Pyroki points `ROBOT_DESCRIPTIONS_CACHE` at the verified CPFS clone, so there is
no Git fetch and no proxy. A slot-A case binds its own perception manifest. That
manifest must equal the common descriptor in every field except the three URLs
and `startup_manifest`, and the entry checks this. The original model and
perception descriptors are never rewritten. Every DLC start is recorded
separately per attempt in `services.json`: argv, env, PID/PGID, host, ready
time, adaptations and a reference to the original descriptor with its sha256.

## Sequence on each rank

1. Record `WORLD_SIZE/RANK/NODE_RANK/LOCAL_RANK/MASTER_*`, and refuse unless
   WORLD_SIZE=2 and RANK is 0 or 1. Every service and campaign starts from an
   allowlisted env (`LANG/LC_*/TZ/TERM` plus its manifest env), so no
   distributed, NCCL, PAI, proxy or credential variable reaches it.
2. Preflight each slot independently. If one slot fails, only that slot is
   skipped. The checks:
   * case sha, the runtime's own campaign `load`, the fixed protocol values,
     the initial-state manifest sha `1668dd6b…`;
   * slot GPU/ports, and the shared Qwen descriptor;
   * mode preconditions on the ledger: clock, counts and status only. Migration
     preflight permits the old worker to remain active while services warm up;
     the final handoff check requires it to be quiescent before resume;
   * POSIX mode-bit identity: no read/traverse of other cells' private
     paths, read access to every frozen file, no write to shared inputs;
   * perception weights, versions, the runtime freeze, the task manifest and
    the initial-state registry.
   The real cell-user permission probe receives the complete file inventory
   through stdin. A frozen inventory can exceed Linux's per-argument limit;
   passing it as one command-line argument caused the 2026-10-11 startup
   failure before any task execution. The transport fix preserves every read,
   write and peer-isolation check. Regression coverage includes a real child
   with a large inventory and an unreadable final entry.
3. Node checks: every Qwen weight sha256, vllm/torch versions, nvcc sha, eight
   idle L20Z GPUs, every recorded interpreter/CUDA path present, and no target
   port already listening.
4. Minimal root setup per viable slot:
   * `groupadd`/`useradd` only for missing exact ids; any UID/GID or name
     conflict is refused;
   * membership in the existing read group 999;
   * creation of the 0700 HOME/control leaf. An existing one must already be
     0700 and owned by the cell user.

   There is no recursive chmod or chown. Then, as the cell user, run the
   identity probe and the device probe. The device probe checks EGL device →
   CUDA ordinal (`EGL_CUDA_DEVICE_NV`), then CUDA on the simulator GPU and an
   NVIDIA (not Mesa/llvmpipe) EGL render. No CPU or privileged fallback is used.
5. Start Qwen and each slot's three services, then wait for synthetic requests:
   `/v1/models` must report exactly the model at 1M, `/segment` takes a
   synthetic PNG, `/plan` a synthetic depth/mask, `/ik` a numerical pose. When
   all are ready, write `entry_root/rank<N>/services_ready.json`.
6. Publish service failures before teardown and update `health_checked_at`
   after every service check. An outer migration coordinator must require a
   recent check as well as the initial readiness file before quiescing RS.
   Only then start campaigns. `fresh` runs the campaign as is; its `init` starts
   the 14 h clock. `resume` and `migrate` use `--resume`. The entry waits on the
   process (polling exit codes only, with no model polling). It records the ledger
   summary before and after, and whether the clock was kept and no rows were
   removed.
7. Teardown signals only the recorded process trees started by this entry
   (descendants found by ppid and start time, with child subreaper). It never
   kills by name or user. Qwen is stopped only after both campaigns have ended.

Failure scope:

| Failure | Effect |
| --- | --- |
| A slot's perception service | Stops that slot's services and campaign only. |
| Shared Qwen | Stops both campaigns. |
| SIGTERM/node loss | Stops everything this entry started; ledgers are untouched. |
| Unclosed attempt on the same rank | Refuses a second entry. |

Under every row, charged ledgers are never edited and resumes keep the identity,
ledger and deadline.

## Continuing bowl r1 from RS

* Ledger `…/bowl_sam2streaming_r1/cell/development_state.json`; clock
  `started_at 1791627274.3236666`, `deadline 1791677674.3236666` (50,400 s).
  The deployment declares this clock and the ledger must match it exactly. The
  entry reads only identity, phase_clock, trial counts and status, never
  generated code, solver history, feedback or held-out results.
* Order: after rank 0's `services_ready.json` exists, the outer coordinator
  quiesces the RS worker between trials and writes
  `<control>/dlc_migration_handoff.json`:
  `{cell, quiesced: true, old_host, old_pids, ledger_sha256,
  development_started_at, development_deadline, quiesced_at}`.
  The entry waits up to `handoff_wait_seconds`, then refuses B only.
* Before starting `--resume`, the entry refuses if any of these holds:
  * the handoff's ledger sha differs from the current ledger (the old worker
    is still writing);
  * any trial row is `running`;
  * `campaign_state.status` is `running` or terminal (stage 1 finalized:
    held-out is an outer resume, so no solver is started);
  * the three clocks differ;
  * the old PIDs are alive on the same host.
* The entry never stops RS processes, never runs `init`, never edits the case,
  ledger or identity, and never creates a replacement r1.

## Blockers recorded, not worked around

1. **r1's private directories must stay 0700.** At the start of this stage
   (about 20:00), `/mnt/workspace/aspire-world-users/bowl-r1` was 0750, and the
   r1 control directory and `cell/` were 0755. Group 999, the public read group
   fresh users join, could then read them. By the RS validate-only run at about
   23:15, the outer side had made HOME, control and `cell/` 0700. The entry does
   not rely on that being kept. Static identity refuses every other cell's
   slot if any other cell's private path is readable or traversable by its uid
   and groups, and migration refuses an r1 HOME/control that is not 0700. The
   entry itself never changes modes on existing paths.
2. **The read group is r1's own primary group (999 `aspire-bowl-r1`).** Fresh users
   join it to read the runtime, so any file r1 creates with group-read is
   visible to them. Only the mode-bit checks above stand between cells. A
   dedicated runtime read group would be cleaner, but it would change frozen
   asset group ownership and is left to the outer side.
3. The campaign's protocol `identity()` includes `devices`, perception URLs and
   the model-service manifest sha. Slot A cells therefore get their own
   perception manifest, and bowl r1 must stay in slot B with the unchanged
   descriptors. Moving r1 anywhere else would change its identity and is
   refused by the protocol itself.
4. The campaign records `runtime_user()`: HOME must equal the passwd HOME. The
   entry sets `HOME/USER/LOGNAME` from the declared identity, so the DLC
   container's own root HOME is not inherited.

5. **Quiescing requires the outer campaign to finish its bookkeeping.** After
   target services are ready, verify that no program trial is running and stop
   only the recorded native coordinator process tree. Leave the outer campaign
   alive so it records the solver exit and its development check. A SIGTERM to
   the outer campaign would leave `status: running`, which both resume gates
   refuse. Confirm a nonterminal, non-running campaign status and a stable
   ledger before writing the handoff. This entry never edits campaign state.
   If selection/freeze has already completed, use the outer held-out workflow;
   never start a solver session against a finalized cell.

## Validate-only (DSW pre-submit)

```bash
/mnt/workspace/aspire-world-envs/libero/bin/python3 -I -B \
  scripts/common/streaming_dlc_pair.py --deployment <pair.json> --validate-only \
  [--skip-weight-rehash]
```

It checks structure, sources, assets and safety boundaries. It starts no
service, creates no user or directory, writes no file, takes no task action and
executes no program. `--skip-weight-rehash` checks Qwen weight sizes instead of
sha256 and records that. The full launch always rehashes. Validate-only is a
pre-submit check, not a reason to start a paid 16-GPU job.

The launch command per worker, which the DLC job must start exactly once per
rank, is:

```bash
/mnt/workspace/aspire-world-envs/libero/bin/python3 -I -B \
  <hash-recorded tools directory>/streaming_dlc_pair.py \
  --deployment <pair.json>
```

The entry is new source and is not in the r1 runtime's frozen inventory. The
outer side decides where to place the entry, either a separate tools path or a
new frozen runtime, and records its sha. Every attempt records `entry_sha256`
and `pair_sha256`.

## Not verified on a target node

* DLC actually exporting WORLD_SIZE=2/RANK, and `/sz` mounted as `/mnt/workspace`.
* Users and groups inside the image (useradd, runuser), and the NVIDIA EGL ICD at
  `/usr/lib/x86_64-linux-gnu/libEGL_nvidia.so.550.127.08`.
* That the driver matches the CUDA 13 compat path.
* Real service readiness, GPU placement and memory headroom of two perception
  sets plus TP4 on one node.
* Throughput of one Qwen shared by two cells.

CPU tests: `tests/test_streaming_dlc_pair.py`, `tests/test_streaming_dlc_entry.py`.
The independent entry acceptance run passed 46 tests, including an active-worker
handoff and adding an evaluation clock without changing the development clock.
The outer migration watcher and entry acceptance run together passed 94 tests,
including stale health metadata and failure publication while the other cell runs.
This does not establish target-node service readiness or two-cell throughput.
