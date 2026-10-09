# Development-gate study: oracle vs self-eval vs VLM judge

Date: 2026-10-05. Status: engineering complete and CPU-tested; not staged, not
launched. No simulator or model has run for this study. Nothing in this directory
is a result.

## Question

Can a CaP agent whose belief, goal and failure handling are programs improve
itself **without the simulator's success label**? During development the Fix Loop
normally reads the environment's `task_completed` after every trial and repairs on
it. This study replaces that grade with the agent's own `done(world)` predicate
(`self_eval`) or with an independent same-backbone VLM judge (`vlm_judge`), seals
the real label away from the solver, and compares the three gates on frozen
held-out seeds that the environment grades in every arm.

The headline figure is one plot: held-out success per gate, with the development
gate's false-accept / false-reject rate against the sealed oracle beside it. The
prediction from the self-evaluation literature (FailBench 2609.03611; "When Do
Agent Loops Mistake Stagnation for Progress?" 2607.25152) is that the VLM-judge
arm drifts toward accept-all while a program predicate grounded in fresh public
observations tracks the oracle. If all three arms land together, the result is a
measurement, not a claim.

## Arms

Every cell is the same fresh C / `judgment` / `executable_world_revision=r1` /
`foundation_revision=r1` / `c_arm=full` / `c_lineage=fresh` cell with the same
model (local Qwen3.8-Flash-Next, native CC, xhigh, 1M context, 64K output), the
same retry budget (3 retries per development seed 51-65), the same watchdogs and
the same frozen held-out sweep (seeds 1-50). The only declared difference is
`development_gate`:

| gate | what grades a development trial | what the solver sees |
|---|---|---|
| `oracle` | the environment's `task_completed` (legacy, byte-identical path) | reward and label in dir names, `summary.txt`, ledger, `foundation_calibration.json` |
| `self_eval` | the world's FINAL shadow `done()` (`true` passes; `false`/`unknown` do not) | a `gate` verdict per trial; no reward, no label, no calibration file |
| `vlm_judge` | an independent request to the same served model with 3 agentview keyframes + task text (`success` passes; `failure`/`unsure` do not) | a `gate` verdict with the judge's one-line reason; no reward, no label |

Every arm records the shadow `done()` verdict, so the agreement of the
self-evaluation with the environment is measured in all three arms. The judge
is run post hoc over every arm's held-out trials as well, so judge agreement is
also measured in all three arms.

Cells: 5 tasks x 3 gates x 2 independent development runs = 30 cells.
Tasks (libero_goal_swap): `put_the_bowl_on_the_plate`, `open_the_middle_drawer_of_the_cabinet`,
`open_the_top_drawer_and_put_the_bowl_inside`, `put_the_wine_bottle_on_the_rack`,
`turn_on_the_stove`. The first three have oracle-gate history (bowl R3 40/50,
drawer 43/50, bowldrawer R2 48/50 under earlier pipeline revisions); the last two
are new. Cell ids: `<task>_<gate>_r<repeat>`, e.g. `bowl_selfeval_r1`.

## How the seal works (and what it is not)

Protocol boundary, not a security sandbox (same stance as the rest of ASPIRE):

1. The protocol runs every sealed trial with `ASPIRE_SEALED_OUTCOME=<control>/sealed/<trial>/outcome.json`.
   The replay writes the real outcome there, names the public directory
   `trial_<seed>_sandboxrc_<rc>_sealed`, saves `video.mp4`, omits reward/label/
   terminated from `summary.txt` and the log, and gives the world session a
   redacted `result`. Sealed trials run every code block (no early stop on
   success) so the block count cannot leak the outcome.
2. Generated code sees `env` as a read-only view exposing only
   `env.handle.task_language` (the surface the rehearsal binding already exposed),
   `APIS` is hidden, and admission refuses `task_completed`, `compute_reward`,
   `check_success`, `__self__`, `APIS`, `low_level_env` and any `handle.`
   attribute other than `task_language`. The diagnostic REPL gets the same view
   and no `step()`.
3. The public ledger row carries `gate` instead of `task_completed`; `status`,
   `check`, triage, repair and selection read the gate verdict. `check` never
   emits `foundation_calibration` in a sealed cell.
4. The read guard denies `control/sealed/` and `heldout/` by path and by name.
5. Oracle beside gate goes to `control/sealed/development_outcomes.jsonl` with
   `gate_false_accept`, `gate_false_reject` and the self-eval calibration category.

The solver is told all of this in its prompt, including that held-out is graded
by the environment, so a trivially-true predicate passes development and fails
the evaluation.

## Metrics and how to read them

Per cell (`scripts/libero/gate_study_analysis.py`, summary.md):

- **held-out success** (seeds 1-50, environment-graded): the outcome measure.
- **gate false-accept rate** = of trials the gate passed, the fraction the
  environment failed. **false-reject rate** = of trials the gate did not pass, the
  fraction the environment passed. Zero by construction for `oracle`.
- **self-eval vs environment 2x2** on development (all arms) and on held-out
  (all arms): tp/fp/fn/tn plus undecided (`unknown`), balanced accuracy on the
  decided subset, undecided rate.
- **judge vs environment 2x2** on held-out (all arms, post hoc) and in-loop for
  `vlm_judge`.
- retries spent, development wall-clock, selected bundle's development record.

Aggregates by gate and by task x gate; unit of analysis is the cell. With 2
repeats per task the aggregate is a demonstration, not a significance test; say
so in the paper.

Reading guide:

- `self_eval` held-out close to `oracle` and `vlm_judge` below both, with
  `vlm_judge` false-accept rate clearly above `self_eval`'s: the self-evaluation
  spine stands.
- All three similar: the gate does not matter on these tasks; report the
  agreement numbers as measurements and move the paper's weight to rehearsal.
- `self_eval` far below `oracle` with high false-accept: predicates were loosened
  until they passed; the sealed ledger shows exactly which trials, and the
  held-out 2x2 shows whether the frozen predicate still tracks the environment.
- High `unknown` rate in `self_eval` with low false-accept: the predicate is
  honest but indecisive; the cost shows up in retries and dev time, not in
  held-out success.

## What the tests establish (CPU only)

`tests/test_development_gate.py` (25 tests) and `tests/test_gate_study_support.py` (8):

- an absent or `oracle` gate adds no constraint and renders the legacy prompt
  byte for byte; every incompatible combination is refused before staging;
- in a faked sealed trial the ledger row has no label or reward, progress and
  selection follow the gate verdict, the public task tree has no forbidden token,
  and the sealed ledger has the oracle with the false-accept/false-reject flags;
- the vlm_judge gate grades from the judge, not the world; an unavailable judge
  is `unavailable`/not passed, never an infrastructure error, never a pass;
- a missing sealed outcome file is an infrastructure error (retry stays spent);
- the sealed outcome must agree with the public directory and exit code;
- the sandbox proxy hides the simulator and APIS and exposes only the task
  language; the REPL helpers behave the same;
- frame selection, verdict parsing and bounded requests of the judge;
- the scope gate refuses a render that disagrees with the cell's gate; the read
  guard denies the sealed root by path and in Bash commands; the analysis
  computes identical metrics from both ledger shapes.

All other fix-loop suites pass after the retry rename (276 tests in the targeted
set; the whole unit directory has only pre-existing environment failures: numpy
pins, missing `.venv-libero`, missing `tyro`).

What the tests do NOT establish: a real sealed simulator run. That is the DSW
real-work preflight (`make-preflights.py` generates one runner per cell; a sealed
runner proves the outcome file is written and `public_leaks()` is empty on the
real artifact tree) and the platform preflight, both before any DLC submission.

## Terminology change

"charged attempt" is now "retry" throughout live code, prompts and the ledger
(`spends_retry`, `retries_used`, `retries_remaining`, `RETRY_LIMIT`,
`imported_retries`). Frozen studies keep their historical wording. The pristine
worker template's budget paragraph therefore changes for every cell, including
`oracle`; a test pins that only budget vocabulary differs from the previous
render.

## Launch shape: one shared serial queue (decided 2026-10-05)

The 10/04 closed-loop launch lost 2 of 3 single-cell jobs inside the native Qwen
compat fixture (one coordinator waited on `TaskOutput` after compaction, which
answered "No task found"; one subagent wrote the five swatch colours permuted) and
the third hit the 10 h development watchdog. Two consequences for this study:

1. **Fixture r4** (`support/qwen-native-compat-r2.py`): the coordinator is no
   longer offered `TaskOutput` and is told to wait for the native completion
   notification, as production coordinators are; the colour check is set
   equality on the five words (no production path depends on five-image
   ordering), while the twelve sentinels stay strictly ordered and the report
   handoff, real Agent call, two real compactions and untouched verifier are
   unchanged. The summary still records whether the order matched.
2. **Shared queue** (`support/dlc-supervisor.py --queue`, entry
   `dlc-queue-entry.sh`): every worker of a job starts its node's services once,
   passes the fixture once (3 attempts, fresh session each), then claims cells
   from `<parent>/queue/claims` (atomic `mkdir` on CPFS) and runs the frozen
   per-cell driver one cell after another. A worker that cannot pass the fixture
   simply serves nothing; a cell interrupted mid-run keeps its claim and its
   preserved `campaign_state.json` for manual review and is never re-run blindly.
   The fixture therefore runs per node, not per cell.

GPU rule (`dlc-run`): a job is 4, 8, or any multiple of 8 GPUs, i.e. N workers
x 8. Until 2026-10-05 the wrapper stopped at 24 (3 workers); the user clarified
that the convention always meant "4, 8, or multiples of 8", and the wrapper and
ops docs were widened accordingly. "Eight nodes of 8 GPUs" can therefore be one
64-GPU job (8 workers claiming from the same queue) or eight 8-GPU jobs sharing
the queue; this design supports both. The 2026-10-05 launch used eight 8-GPU
jobs: they were admitted individually within one minute, and a node failure
only loses that node's in-flight cell, whereas an 8-worker job is gang-placed
and a worker failure can end the whole job. The job running-time cap must cover
a worker's share of the queue (about 4 cells per worker with 8 workers: 4 x up
to 16.3 h).

## Launch procedure (after the user's confirmation)

1. `sync-to-remote.sh --apply` (or tar over scp; local rsync is broken): copy
   exactly `SYNC_FILES.txt` into the remote engineering checkout.
2. On rbs-debug: `python prepare-gate-study.py --prepare`, then `--verify`.
   Launch files (supervisor, queue entry, fixture) are pinned in every cell's
   runtime manifest, so a change to any of them means re-staging all cells.
3. `python make-preflights.py` then `run-diagnostics.py` (one real scratch
   trial per cell on DSW GPU 2; sealed runners prove the seal). Source pins only;
   launch-file changes do not invalidate these.
4. `run-platform-preflights.py --entry dlc-queue-entry.sh --preflight-gpu 3`
   (dlc-preflight of the queue entry against every cell's case).
5. `submit-gate-study.py --gpus 24 --max-minutes <cap> --authorization "<user's
   words>"` (one 3-worker job) or `--gpus 8 --jobs N` (N single-node jobs
   sharing the queue). `--plan-only` prints the dlc-run plan first.
6. When the queue drains: `gate_study_analysis.py --parent <experiments parent>
   --out <dir>`.

Default decisions taken here, all reversible before staging: no preflight retry
is imported into cell ledgers (`IMPORT_PREFLIGHT = False`); judge effort `high`,
3 frames, 4096 tokens, 300 s timeout; held-out judging runs inside each cell's
turn after its sweep.
