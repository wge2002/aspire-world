#!/usr/bin/env python3
"""Formal native campaign launcher for ONE SAM2-streaming Fix Loop cell.

Profile `sam2-streaming-native-v1` under `libero-pro-common-v1`. Native Claude
Code 2.1.220 (the fixed local Qwen3.8-Flash-Next solver, xhigh, 1M context, 64K
output) owns generation, tools and context; this file has no LLM client or agent
loop. It reuses the legacy native_world_campaign pieces unchanged: the pristine
`.claude/libero/fix-loop/subagent-prompt.md` worker template with the complete
initial-15 / repair / selection workflow, `local_vllm_settings`,
`native_environment`, `native_command` and `probe`. Only mechanical edits differ
from the legacy C rendering:

* every trial goes through `streaming_fixloop_protocol.py` with this cell's
  authored policy plus `--streaming-config` (no world program, no inventory);
* the task directory is `<control>/cell`, outside the source runtime, and the
  `outputs/working_codes` copy is dropped;
* the legacy world-adapter section is replaced by the streaming authoring
  contract; condition C keeps no strategy MD and no promotion step;
* the dedicated `scripts/common/streaming_fixloop_guard.py` hook covers Read,
  Glob, Grep, Write, Edit, MultiEdit, NotebookEdit and Bash.

No 15-version, action, query or recovery cap is added. Sequence, all outer:
preflight (OS user, read-only runtime, native binary, task/model/perception
manifests, runtime freeze) -> perception reachability -> tiny native route probe
(no task content, no robot policy) -> `init` (starts the persisted 50,400 s
development deadline) -> one native coordinator session clipped to that deadline
-> `check` -> `finalize` with the actual worker settings and probe -> optional
outer held-out run of the frozen bundle. A worker exit is finalized as it
actually ended: nothing is restarted, and `--resume` is an explicit, recorded
outer action that never re-inits, never refreshes the deadline and never refunds
budget. One case file is one cell; there is no multi-cell or DLC entry here.

Isolation is the outer deployment's job: a separate unprivileged OS user (run
via runuser, HOME untouched), a read-only source runtime under /mnt/workspace
and a writable control directory per cell. This launcher only verifies and
records those facts and refuses to start without them; it never creates users or
starts services. The guard is an integrity control, not a security sandbox.
"""
from __future__ import annotations

import argparse
import difflib
import fcntl
import grp
import hashlib
import json
import os
import pwd
import re
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "common"))
import native_world_campaign as legacy  # noqa: E402
import streaming_fixloop_guard as guard  # noqa: E402
import streaming_fixloop_protocol as sp  # noqa: E402
import streaming_fixloop_closeout as closeout  # noqa: E402
from native_cc_freeze import RuntimeChanged, verify_runtime  # noqa: E402
from native_cc_runtime import ServiceFailure, atomic_json  # noqa: E402
from native_cc_stream import run_native_cc  # noqa: E402

PROTOCOL = "scripts/libero/streaming_fixloop_protocol.py"
HELDOUT = "scripts/libero/streaming_fixloop_heldout.py"
CLAUDE_VERSION = "2.1.220"
# Common environment revision shared by every method of this line: single reset
# + set_init_state + 10 settling steps, and the hash-bound initial-state registry
# (original 50 test states, 15 separate development states, no modulo).
ENVIRONMENT_VERSION = sp.ist.ENVIRONMENT_VERSION
SEED_MAPPING = sp.ist.SEED_MAPPING
CAMPAIGN_KEYS = ("claude_bin", "claude_config_dir", "libero_config_path",
                 "task_manifest", "task_manifest_sha256")
TERMINAL = {"stage1_finalized", "complete", "heldout_incomplete", "heldout_refused",
            # Supervised repair study: development ended without a held-out run.
            "development_failed", "development_stopped", "development_incomplete", "development_review_failed"}


class LaunchRefused(RuntimeError):
    """A precondition failed before anything ran; nothing is recorded as a trial."""


def load(case_path: Path) -> tuple[dict, Path, Path]:
    case, repo, task_dir = sp.load_case(case_path)
    errors = [f"campaign case requires {k}" for k in CAMPAIGN_KEYS if not case.get(k)]
    if case.get("dependency_evidence", "frozen") != "frozen":
        errors.append("a formal campaign requires dependency_evidence: frozen")
    if case.get("claude_version") != CLAUDE_VERSION:
        errors.append(f"claude_version must be {CLAUDE_VERSION}")
    for key in ("cells", "tasks", "dlc"):
        if key in case:
            errors.append(f"one case is one cell; `{key}` is not accepted")
    control = Path(case["control"]).resolve()
    if (any(re.search(p, str(control)) for p in guard.READ_DENY) or "heldout" in str(control)
            or guard.OUTER_MARKER in str(control)):
        errors.append("control path collides with a guard-denied path component "
                      "(outputs, heldout, skills, assets); use a fresh campaign directory")
    if case.get("claude_config_dir"):
        config = Path(case["claude_config_dir"]).resolve()
        if config.is_relative_to(repo) or not config.is_relative_to(control):
            errors.append("claude_config_dir must be this cell's own directory under control")
    if errors:
        raise LaunchRefused("; ".join(errors))
    return case, repo, task_dir


def shim(case: dict, repo: Path) -> dict:
    """The legacy local-vllm case shape, filled from this profile's case."""
    return {"id": case["id"], "suite": case["suite"], "task": case["task"], "condition": "C",
            "gpu": case["cuda_visible_devices"], "sim": str(repo),
            "python_root": str(repo.parents[1]), "claude_bin": case["claude_bin"],
            "model_provider": legacy.LOCAL_VLLM_PROVIDER,
            "inference_endpoint": case["model_service"]["endpoint"],
            "model_tag": case["model"], "expected_served_model": case["model"],
            "effort": case["effort"], "context_tokens": case["context_tokens"],
            "max_output_tokens": case["max_output_tokens"],
            "trial_timeout": case["trial_timeout"],
            **({"egl_vendor_config": case["egl_vendor_config"]}
               if case.get("egl_vendor_config") else {})}


# --- preflight: verify and record, never create ---------------------------------


def runtime_user() -> dict:
    uid, gid = os.geteuid(), os.getegid()
    entry = pwd.getpwuid(uid)
    record = {"uid": uid, "gid": gid, "user": entry.pw_name, "group": grp.getgrgid(gid).gr_name,
              "passwd_home": entry.pw_dir, "env_home": os.environ.get("HOME")}
    if uid == 0:
        raise LaunchRefused("refusing to run as root: the outer deployment runs this cell as its "
                            "own unprivileged user (runuser)")
    if record["env_home"] != entry.pw_dir:
        raise LaunchRefused("HOME differs from the run user's passwd home; HOME is not "
                            "repurposed for isolation")
    return record


def writable(path: Path) -> bool:
    return os.access(path, os.W_OK)


def isolation(case: dict, repo: Path) -> dict:
    control = Path(case["control"]).resolve()
    problems = []
    if writable(repo):
        problems.append(f"source runtime {repo} is writable by the run user; it must be read-only")
    if repo.is_relative_to("/root"):
        problems.append("the engineering checkout under /root is never handed to the solver")
    if not writable(control):
        problems.append(f"control directory {control} is not writable by the run user")
    if problems:
        raise LaunchRefused("; ".join(problems))
    return {"runtime": str(repo), "runtime_writable": False, "control": str(control),
            "task_dir": str(control / "cell"), "claude_config_dir": case["claude_config_dir"]}


def native_binary(case: dict) -> dict:
    path = Path(case["claude_bin"]).resolve()
    proc = subprocess.run([str(path), "--version"], capture_output=True, text=True, timeout=60,
                          env={"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "")})
    record = {"path": str(path), "sha256": sp.file_sha256(path),
              "version_output": proc.stdout.strip(), "version_exit": proc.returncode}
    if proc.returncode or not proc.stdout.strip().startswith(CLAUDE_VERSION):
        raise LaunchRefused(f"native Claude Code must be {CLAUDE_VERSION}: {record}")
    return record


def task_metadata(case: dict) -> dict:
    """The outer-generated task manifest: hashes and versions only, no asset content."""
    config = Path(case["libero_config_path"])
    config_file = config / "config.yaml" if config.is_dir() else config
    if not config_file.is_file():
        raise LaunchRefused(f"declared LIBERO_CONFIG_PATH has no config: {config}")
    try:
        manifest = sp.verified_manifest(
            case["task_manifest"], case["task_manifest_sha256"],
            {"suite": case["suite"], "task": case["task"],
             "environment_version": ENVIRONMENT_VERSION,
             "seed_to_init_state_index": SEED_MAPPING,
             "libero_config_path": case["libero_config_path"]}, "task")
        # Formal campaigns are frozen, so the registry is required (validate_case)
        # and verified here: manifest hash, task entry, original test file hash.
        initial_states = sp.initial_state_identity(case)
    except RuntimeChanged as exc:
        raise LaunchRefused(str(exc)) from exc
    if initial_states.get("evidence") != "frozen":
        raise LaunchRefused("a formal campaign requires the common initial-state manifest")
    return {"manifest": manifest, "libero_config_path": case["libero_config_path"],
            "libero_config_sha256": sp.file_sha256(config_file),
            "initial_states": initial_states}


def perception_ready(case: dict) -> dict:
    """Reachability only. Services are already running; never start or restart one."""
    status = {}
    for key in ("sam3_url", "graspnet_url", "pyroki_url"):
        try:
            with urllib.request.urlopen(f"{case[key].rstrip('/')}/health", timeout=5) as r:
                status[key] = r.status
        except urllib.error.HTTPError as exc:
            status[key] = exc.code
        except OSError as exc:
            status[key] = f"unreachable: {exc}"
    if any(isinstance(v, str) for v in status.values()):
        raise ServiceFailure({"unreachable_perception": status})
    return status


# --- native wiring --------------------------------------------------------------


def worker_settings(case: dict, repo: Path, case_path: Path) -> dict:
    settings = legacy.local_vllm_settings(shim(case, repo))
    settings.update(guard.settings(case_path, repo))
    return settings


def native_env(case: dict, repo: Path, case_path: Path) -> dict:
    env = legacy.native_environment(shim(case, repo), repo, case_path,
                                    Path(case["claude_config_dir"]))
    env.update(LIBERO_CONFIG_PATH=str(case["libero_config_path"]),
               MUJOCO_EGL_DEVICE_ID=str(case["egl_device_id"]),
               ASPIRE_NATIVE_CASE=str(case_path.resolve()))
    return env


def command_record(command: list[str], prompt_path: Path, settings_path: Path) -> list:
    """The argv actually executed, with the prompt and settings replaced by references."""
    out, skip = [], None
    for i, arg in enumerate(command):
        if skip == i:
            continue
        out.append(arg)
        if arg in {"-p", "--settings", "--agents"} and i + 1 < len(command):
            ref = {"-p": prompt_path, "--settings": settings_path}.get(arg)
            value = command[i + 1]
            out.append({"file": str(ref) if ref else None,
                        "sha256": hashlib.sha256(value.encode()).hexdigest()})
            skip = i + 1
    return out


# --- prompts --------------------------------------------------------------------


STREAMING_SECTION = """
---

## Streaming world contract (this cell)

This cell's method runtime is the engineering SAM2-streaming World
(`cap/world_model/World.py` and its bridge), declared and frozen by the
coordinator. It is NOT yours to edit and it is not something you learned. You
author, from this cell's own public observations only:

- the policy (`initial_code.py`, later `fix_code.py`), including any relation
  predicates, checkpoint responses and predictions through the injected World
  helpers described in `docs/streaming-world.md`;
- a streaming object configuration (`initial_streaming_config.json`, later
  `fix_streaming_config.json`): JSON with `objects` (a list of
  `{"identity", "prompt", optional "min_seed_score"}` public SAM3 text prompts),
  optional `cadence` and optional `notes`. Runtime fields (`mode`, `sam2`,
  `camera`) are frozen and refused.

A policy and its streaming config are ONE candidate bundle; pass both on every
trial. No object inventory is provided. Neither file may read the task outcome:
`task_completed`, `compute_reward`, `check_success`, the `APIS` mapping, bound
method owners and every `handle` attribute except `env.handle.task_language` are
refused before a trial is admitted, as are simulator ground-truth APIs. The
outcome reaches you only through the recorded trial result.
"""


CONTINUATION_SECTION = """
---

## Repair continuation of this cell (explicitly authorized)

This cell continues ITS OWN earlier, incomplete development phase under a
corrected engineering runtime. It is not a fresh repeat and not an unchanged
resume. The earlier phase's rows, outcomes and clock are immutable history; this
phase has its own ledger, identity and once-recorded 50,400 s deadline.

Historical inputs (read-only, this cell's own; nothing from any other cell):

- `{parent_task_dir}/` — the earlier task directory: its scene snapshot
  images, `task_analysis.md`, `findings.md`, initial and fix code/configs,
  `attempts/` notes and `development/` attempt evidence.
- `{parent_ledger}` — the earlier ledger, bound by sha256. Its rows are imported
  once as `continuation.historical`; never edit or copy it.

Run `status` first. It reports, per seed, the cumulative charged attempts
(`retries_used`, split into `historical.retries_used` and `new_retries_used`),
`continuation.owed_initial_seeds` and `continuation.historical_successes`.

- The 3 TOTAL charged attempts per seed are cumulative across both phases. Every
  earlier charged execution counts once: diagnostics, crashes, timeouts,
  infrastructure errors and interrupted runs are not refunded. Aliases and the
  scene snapshot never counted. A seed with no attempts left cannot run again.
- The earlier scene snapshot is this cell's snapshot; no second one runs.
- Seeds the earlier phase never ran initially are owed their initial run before
  any repair. Run the earlier initial program unchanged with `--phase initial`.
  The exact files that ran are `code.py` and `authored_streaming_config.json` in
  `{parent_task_dir}/<continuation.initial_anchor.directory>/`. Copy them to
  `$TASK_DIR/initial_code.py` and `$TASK_DIR/initial_streaming_config.json`. Any
  change belongs to `repair`.
- Earlier successes were measured under the previous runtime. They are history,
  not evidence: a seed counts as passing only after a passing execution in THIS
  phase, so every seed with initial evidence and attempts left needs a repair
  trial in this phase, or exhaustion with its `attempts/seed_N_BLOCKED.md` note.
- `select` accepts only a bundle executed in THIS phase. Earlier bundles,
  passing or not, are not candidates. Write this phase's own
  `task_analysis.md`, `findings.md` and blocker notes in `$TASK_DIR`.

The earlier code was written against the previous runtime. Read the corrected
contracts before reusing it: `docs/robot-frame-contract.md` (which frame and
control point each pose uses), and the recovery gate, under which a motor or
gripper command is refused BEFORE it executes while `world_recovery_status()`
does not permit motion. Earlier traces may show behaviour the corrected runtime
no longer has. Decide from this phase's own executions.
"""


def continuation_section(case: dict) -> str:
    ledger = sp.continuation.parent_path(case)
    return CONTINUATION_SECTION.format(parent_task_dir=ledger.parent, parent_ledger=ledger)


SUPERVISED_SECTION = """
---

## Supervised repair study (explicitly authorized, outer-reviewed rounds)

Study `{study_id}`. This is a NEW development phase of this cell with its own
ledger, identity and once-recorded 50,400 s deadline. It is not a resume of the
earlier study and refunds nothing; the earlier study's 45 attempts stay its own.

This session is ROUND {round} of 3. In every round each development seed 51-65
gets exactly ONE charged attempt (smoke, initial, repair or diagnostic;
timeouts, crashes and infrastructure errors stay charged; aliases and the
scene snapshot are free). The protocol refuses a second attempt on a seed in
the same round: a failed seed waits for the next round, and its next attempt
cannot be borrowed early. A round is complete when all 15 seeds have a
resolved attempt of this round. Run `status` first; `supervised.round_seeds_remaining`
lists the seeds still owed this round. There is no limit on how often you edit
code between attempts.

{round_rule}

When the round is complete, before you end: update `task_analysis.md` and
`findings.md`, write `attempts/seed_N_BLOCKED.md` for every seed that has no
attempts left without a pass, and record your current best tested bundle with
`select --reason` (a bundle needs its OWN oracle success in THIS study to be
frozen; another bundle's or an earlier study's success never counts). Then end
the session. If no bundle in this study has a normal oracle success yet, leave
the selection empty, record that fact in findings, and end the session without
retrying selection or attempting extra seeds. The outer runner stops the session at the round boundary
({grace} s grace), closes the round, and an outer coordinator reviews this
cell's own development evidence before any next round opens. Nothing you write
is an approval; reviews are written only by the outer coordinator.

Read-only inputs of THIS cell only (never another cell, never held-out):

- `{prior_dir}/` — your own earlier development inputs, listed in
  `{prior_manifest}` (ledger, code, configs, findings, task analysis,
  replay log, lifecycle and the initial outer analysis). History for reading
  and reuse only: its rows, successes and bundles are not candidates here.
{reviews}
"""

ROUND_ONE = ("Round 1 follows the original workflow: scene snapshot, smoke on seed 51 "
             "(alias it as initial when it completed), then the initial program on the other "
             "seeds. You may base the initial program on your own earlier code.")
ROUND_LATER = ("Rounds 2 and 3 continue the same ledger: no new snapshot or smoke. A seed "
               "still owed initial coverage runs `--phase initial` with the frozen initial "
               "program first; every other seed runs `repair` (or `diagnostic`). Read the "
               "outer reviews listed below before you change code.")
SUPERVISED_COORDINATOR = """
This is a supervised repair study. The worker covers ONE round per session:
one charged attempt per development seed, then notes and `select`, then it
returns. Do not ask it to continue past the round; the outer runner stops this
session at the round boundary and opens a new one after outer review.
"""


def supervised_section(case: dict, context: dict | None) -> str:
    context = context or {"round": 1, "reviews": [], "summaries": []}
    manifest = Path(case["supervised_repair"]["prior_input"]["manifest"]).resolve()
    lines = [f"- `{p}` — outer review of round {i + 1} (decision, analysis, guidance)"
             for i, p in enumerate(context["reviews"])]
    lines += [f"- `{p}` — runner summary of a closed round" for p in context["summaries"]]
    if sp.autonomous.enabled(case):
        c = sp.autonomous.config(case)
        return f"""
## Explicit remaining-round continuation — overrides initial/smoke/snapshot steps above

Study `{case['supervised_repair']['study_id']}`, repair phase `{c['phase_id']}`.
The parent completed round 1 and spent 15 attempts (one per seed51–65), then
expired while awaiting review. Its ledger and clock are immutable. This new
authorized phase imports those attempts, with only rounds2/3 left: at most30
additional attempts, at most45 cumulative. Infra errors/crashes stay charged.
Historical rows and successes are never corrected-runtime validation.

This native session is ROUND {context['round']} of 3. Run `status` first and
cover ONLY `supervised.round_seeds_remaining`. Use `trial --phase repair` for
every remaining seed51–65, ONCE per seed this round. Even the parent's timed-out
seed51 may now use a repair attempt; do not replay its old initial or smoke.
No new snapshot, initial batch, or diagnostic trial. Edits have no revision cap.

The outer initializer copied this cell's engineering-assisted, UNVALIDATED
starting policy/config into `fix_code.py` and `fix_streaming_config.json`.
Read them and the guidance at `{c['seed_input']['guidance']['path']}` before
repairing. Your own immutable parent development directory is
`{Path(c['parent']['ledger']).parent}`. Do not read any other cell or held-out data.
After current failures, use your own observed geometry, code and logs to improve
the program; all existing nonprivileged/checked-motion/World gates remain.

Read these independent local-Qwen reviews and development summaries:
{chr(10).join(lines)}

Complete all15 attempts of this round even if a candidate succeeds early.
Then update task_analysis.md, findings.md (Root causes observed; What fixed
them; Generalizable patterns; Blocked seeds), and attempts/seed_N_BLOCKED.md
for every exhausted seed without a current-phase pass. If a current-phase
bundle has a normal oracle success, copy that exact tested policy/config into
fix_code.py/fix_streaming_config.json and `select --reason` it. A crashed
task_completed=1 is NOT a pass. With no normal success leave selection empty.
End this session after the round; do not loop over rejected extra trials.

The DLC outer controller independently reviews round2 and opens round3 without
waiting for the chat. You cannot approve or consume reviews. Round3 is the last
round; after it the outer runner checks all handling/selection/freeze conditions.
Only the selected same bundle's normal success in this new phase can qualify
held-out1–50. With no qualifying bundle, held-out is skipped. The new phase's
14-hour clock is recorded once; ordinary resume never resets it or the counts.
"""
    if sp.autonomous.repair_start(case):
        return (sp.autonomous.repair_start_section(case, context, lines)
                + "\nRead `docs/visible-held-placement.md` for the generic public geometry API.\n")
    if case["supervised_repair"].get("independent_start"):
        return f"""
## Fresh independent development with automatic round reviews

Study `{case['supervised_repair']['study_id']}`, this cell only. This is a fresh
independent repeat: no parent ledger, code, task configuration, feedback or
historical successes are supplied. Generate your own program from the public
initial scene and allowed API documentation. Never read any other repeat.
The bound start manifest `{manifest}` records zero prior attempts and no files.

This session is ROUND {context['round']} of 3. Each round allows ONE charged
attempt per seed51–65, at most45 total. Crashes, timeouts and infrastructure
errors stay charged. Run `status` first and cover only the remaining seeds.
{ROUND_ONE.replace('You may base the initial program on your own earlier code.', 'Generate the initial program independently.') if context['round'] == 1 else ROUND_LATER}

After all15 attempts, update task_analysis.md, findings.md and exhausted-seed
BLOCKED notes, then end this session. Select only an exact tested bundle with
its OWN normal oracle success; a crash after task_completed=1 is not success.
With no such bundle leave selection empty. The outer controller reviews your
own development evidence with an independent same-local-Qwen session and
opens the next round automatically. You cannot write or consume approvals.
Read the resulting reviews and summaries before revising your own program:
{chr(10).join(lines) or '(none yet: round1)'}

After round3, the normal selection, handling and freeze checks still apply.
Only a qualifying selected bundle can enter held-out1–50; otherwise skip it.
The 14-hour development clock starts once and resume never resets budgets.
"""
    section = SUPERVISED_SECTION.format(
        study_id=case["supervised_repair"]["study_id"], round=context["round"],
        round_rule=ROUND_ONE if context["round"] == 1 else ROUND_LATER,
        grace=(closeout.config(case)["report_grace_seconds"] if closeout.config(case)
               else sp.rounds.grace_seconds(case)), prior_dir=manifest.parent, prior_manifest=manifest,
        reviews="\n".join(lines) if lines else "- (no outer review yet: this is round 1)")
    return section


def worker_prompt(case: dict, repo: Path, rounds_context: dict | None = None) -> str:
    s = shim(case, repo)
    text = legacy.worker_prompt(s, repo)
    task_dir = str(Path(case["control"]).resolve() / "cell")
    legacy_world = legacy.world_section(s)
    if legacy_world not in text:
        raise LaunchRefused("legacy template drifted: world section not found")
    text = text.replace(legacy_world, STREAMING_SECTION)
    for stem in ("initial", "fix"):
        text = text.replace(
            f' \\\n    --world-program "$TASK_DIR/{stem}_world_program.py"'
            f' \\\n    --inventory "$TASK_DIR/{stem}_inventory.json"',
            f' \\\n    --streaming-config "$TASK_DIR/{stem}_streaming_config.json"')
    text = text.replace(
        ' \\\n    --code "$TASK_DIR/diagnostic_session.py"\n',
        ' \\\n    --code "$TASK_DIR/diagnostic_session.py"'
        ' \\\n    --streaming-config "$TASK_DIR/initial_streaming_config.json"\n')
    text = text.replace("  $TASK_DIR/fix_world_program.py, $TASK_DIR/fix_inventory.json"
                        "   ← frozen with the program, part of its identity\n",
                        "  $TASK_DIR/fix_streaming_config.json"
                        "                  ← frozen with the policy, part of its identity\n")
    text = text.replace("Save to TWO locations (create `outputs/working_codes` first if missing):",
                        "Save to this cell's task directory:")
    text = text.replace("  outputs/working_codes/${SUITE}_${TASK}_fix.py  ← named copy\n", "")
    text = text.replace(legacy.PROTOCOL, PROTOCOL)
    text = text.replace(f"outputs/libero_fix_loop/{case['suite']}/{case['task']}", task_dir)
    text += (f"\nCell-local skills you write go to `{task_dir}/skills/` and are never shared "
             "with another cell.\n")
    if case["profile"] in (sp.SEMANTIC_PROFILE, sp.ARTICULATION_PROFILE):
        text = text.replace("optional `cadence` and optional `notes`. Runtime fields",
                            "required scene-derived `parts`, optional `cadence` and `notes`. Runtime fields")
        text += """

This cell explicitly uses sam2-semantic-native-v2. Read the public interface
`docs/semantic-streaming-world.md`. The engineered part graph, identity
association, action reports and structure revision rules are method factors.
No task solution or scene inventory is supplied. Derive your own decomposition
from this cell's actual public observations. Include `parts` in the authored
streaming config, with stable identity, hypothesized semantic, parent (or null),
tracked (boolean) and optional motion hypotheses. `objects` specifies only the
tracked nodes' public SAM3 prompts, scores, optional image_x/image_y rank
selector and optional `alternative_prompts` (distinct public prompts tried in
order only when earlier prompts find no candidate; they never break ambiguity);
untracked parent nodes organize the graph without inventing geometry.
Near-duplicate candidate masks (IoU >= 0.85) count as one candidate. Seeding
failures are typed missing/ambiguous/rank_unavailable/identity_conflict with
public candidate evidence; nothing is chosen arbitrarily.
Semantic labels and motion hypotheses are uncertain, not measured truth.

Use the injected goto_pose_report to inspect requested TCP arrival and the
effective IK target. It does not prove grasp/contact or task success. Read the
generic frame contract `docs/robot-frame-contract.md`. Camera pose_mat, World
object geometry, solve_ik/goto_pose targets and motion reports all use the
robot_base frame; goto_pose requests a robot_base TCP and applies its existing
panda_hand offset. robot_cartesian_pos is robot_base/panda_hand [XYZ, WXYZ,
gripper opening at index 7]; robot_hand_pose_base and robot_gripper_opening name
the same values. robot_hand_pose_world is the simulator world/panda_hand, not
comparable with robot_base positions. World robot facts include frame_view with
robot_base hand/TCP pose and gripper opening. Never compare positions from
different frames or control points. Motion stagnation is not arrival. A
quaternion argument contains exactly one WXYZ quaternion.

The public helpers world_set_focus, world_refresh, world_require_relation,
world_begin_interaction, world_finish_interaction and world_revise_structure
allow the policy to validate evidence and respond to conflicts. Use fresh
part observations and authored evidence-based relation predicates. Do not
interpret a closed gripper as contact. If execution/contact is unverified,
update that diagnosis before revising the object's structure. When structure
changes, cite recorded visual evidence and preserve IDs/lineage. Reacquisition
rejects conflicting or ambiguous instances; a moved instance beyond the 0.06 m
gate is accepted only with unique coherent current RGB-D correspondences.
world_recovery_status() reports state (armed/unseeded/tracking_lost/
reacquire_failed/structure_grounding_failed/closed), reason, evidence and
motion_permitted. While motion is not permitted, or a focused part is unknown
or stale, the next motor or gripper step is refused BEFORE it executes; catch
the error, inspect the status and call reacquire_world() (or world_refresh()
for stale focus) before commanding motion again. World resets per episode; no other
cell or evaluation seed supplies generated code, feedback or learned structure.
All executions, including diagnostics, still consume the original ledger.
"""
    if case["profile"] == sp.ARTICULATION_PROFILE:
        text = text.replace("sam2-semantic-native-v2", sp.ARTICULATION_PROFILE)
        text += """
This cell additionally uses the explicitly declared sam2-articulation-qwen-v3
method. Read docs/articulation-streaming-world.md. Public RGB-D correspondences
estimate rigid part motion; fixed/sliding/rotating templates fit synchronized
parent-relative trajectories. Qwen reviews initial hypotheses and queued public
identity/effect/model contradictions on the owning control thread. This adds
event-driven inference calls to the same fixed local Qwen service. No task
solution, historical feedback, hidden joint parameters or scene inventory is
supplied. Optional authored `joints` has child,parent,family; families are
fixed/sliding/rotating/free/unknown. Track the reference parent as well as the
child when motion fitting is needed. Untracked parents remain uncertain.
world_query_joint(child) returns fitted hypotheses or unknown, and
world_begin_joint_interaction(child,delta_q) plus world_finish_interaction
checks expected relative translation AND rotation with fresh execution/contact
evidence; unchanged visible centre does not prove a rotation succeeded.
world_predict_joint(child,q) predicts within the observed range by default;
explicit extrapolate=True marks further predictions as unvalidated. Neither
reports joint limits or task completion. world_review_pending() only handles
existing events; it cannot invent evidence or force an extra query. Initial
model reviews without informative measured motion are deferred without a
Qwen call and re-armed when such motion is registered. Freeze
prompts, model templates, numerical thresholds, request settings and update
rules with the policy/config. Runtime updates reset every episode and never
repair a frozen program, choose a program per seed, or use scoring feedback.
"""
    if "execution_contract" in case["streaming"]:
        # Engineering opt-in only; absent leaves every earlier prompt unchanged.
        text += """
This cell additionally runs the engineering checked-execution contract
checked-motion-v1. Read docs/checked-execution-contract.md for exact helper
signatures, frames and units. A gripper change is refused BEFORE it takes
effect unless the arm has not moved this episode or the last arm motion was a
goto_pose_report with reached=true and target_modified=false: reach first,
then grasp or release. A clipped or orientation-fallback target is not
executed unless execute_modified=True, and even then never permits a gripper
change. motion_workspace(), motion_contract_status(), world_visible_geometry,
geometry_from_points, world_bind_held/world_held_state/world_release_held and
tcp_target_for_feature describe public visible geometry and held relations
only; unknown stays unknown and nothing proves contact or success. The
contract is a method factor, not a task strategy: derive targets from this
cell's own public observations.
"""
    if "continuation" in case:
        text += continuation_section(case)
    if "supervised_repair" in case:
        text += supervised_section(case, rounds_context)
        if closeout.config(case):
            # Opt-in only; absent leaves every earlier prompt unchanged.
            text += closeout.ROUND_SECTION.format(schema=closeout.config(case)["schema"],
                                                  grace=closeout.config(case)["report_grace_seconds"])
    return text


def coordinator_prompt(case: dict, prompt_path: Path) -> str:
    text = legacy.COORDINATOR.format(cell=case["id"], suite=case["suite"], task=case["task"],
                                     prompt_path=prompt_path,
                                     promotion=legacy.PROMOTION_C.strip(), protocol=PROTOCOL)
    return text + SUPERVISED_COORDINATOR if "supervised_repair" in case else text


def write_prompts(case: dict, repo: Path, control: Path,
                  rounds_context: dict | None = None) -> tuple[Path, Path]:
    rendered = worker_prompt(case, repo, rounds_context)
    prompt_path = control / "worker-prompt.md"
    prompt_path.write_text(rendered)
    (control / "worker-prompt.diff").write_text("".join(difflib.unified_diff(
        legacy.pristine_template(repo).splitlines(keepends=True),
        rendered.splitlines(keepends=True),
        fromfile=".claude/libero/fix-loop/subagent-prompt.md (pristine template)",
        tofile=f"{case['id']} rendered streaming worker prompt")))
    coordinator = control / "coordinator-prompt.md"
    coordinator.write_text(coordinator_prompt(case, prompt_path))
    return prompt_path, coordinator


# --- outer steps ----------------------------------------------------------------


def outer_step(case_path: Path, repo: Path, env: dict, control: Path, name: str,
               script: str, *args: str) -> dict:
    command = [str(repo / ".venv-libero/bin/python3"), script, "--case", str(case_path), *args]
    proc = subprocess.run(command, cwd=repo, env=env, capture_output=True, text=True)
    stdout_path = control / f"outer_{name}.stdout.log"
    stderr_path = control / f"outer_{name}.stderr.log"
    stdout_path.write_text(proc.stdout)
    stderr_path.write_text(proc.stderr)
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        payload = None
        lines = proc.stdout.splitlines(keepends=True)
        for index, line in enumerate(lines):
            if not line.lstrip().startswith("{"):
                continue
            try:
                payload = json.loads("".join(lines[index:]))
                break
            except json.JSONDecodeError:
                continue
    step = {"step": name, "command": command, "exit_code": proc.returncode,
            "result": payload, "stderr_tail": proc.stderr[-4000:],
            "stdout_path": str(stdout_path), "stderr_path": str(stderr_path)}
    atomic_json(control / f"outer_{name}.json", step)
    return step


def development_deadline(task_dir: Path) -> float:
    ledger = json.loads((task_dir / "development_state.json").read_text())
    return float(ledger["phase_clock"]["development"]["deadline"])


def read_json_file(path: Path) -> dict:
    try:
        value = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


# --- supervised repair study (opt-in; docs/streaming-supervised-repair.md) ----------


def read_ledger(task_dir: Path) -> dict:
    return json.loads((task_dir / "development_state.json").read_text())


def trial_lock_free(task_dir: Path) -> bool:
    with (task_dir / ".trial.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        fcntl.flock(lock, fcntl.LOCK_UN)
    return True


def settle_running(case_path, repo, env, control, task_dir, record) -> None:
    """After a session ended: settle rows whose trial process is gone (still charged)."""
    running = [r["directory"] for r in read_ledger(task_dir)["trials"] if r["status"] == "running"]
    if not running or not trial_lock_free(task_dir):
        return
    settled = record["supervised"].setdefault("settled_interrupted", [])
    for directory in running:
        step = outer_step(case_path, repo, env, control, f"resolve_{len(settled) + 1:02d}",
                          PROTOCOL, "resolve-interrupted", "--directory", directory)
        settled.append({"directory": directory, "exit_code": step["exit_code"]})


def boundary_watch(task_dir: Path, round_: int, grace: float, info: dict, poll: float = 15.0,
                   *, report_grace: bool = False):
    """on_start hook: stop the native session `grace` seconds after its round completed.

    With the opt-in closeout, `grace` is the closeout report grace: the trials are
    complete, the session may still be writing its reports, and a stop at the
    limit is recorded as such; closeout completion is verified afterwards.
    """
    def on_start(process):
        def watch():
            reached = None
            while process.poll() is None:
                time.sleep(poll)
                try:
                    complete = sp.rounds.round_complete(read_ledger(task_dir), round_)
                except (OSError, ValueError, KeyError):
                    continue
                if not complete:
                    continue
                if reached is None:
                    reached = time.monotonic()
                    info["round_boundary_reached_at"] = time.time()
                    if report_grace:
                        info["round_state"] = "trials_complete"
                if time.monotonic() - reached >= grace:
                    info["stopped_at_report_grace" if report_grace else "stopped_at_round_boundary"] = True
                    try:
                        os.killpg(process.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                    return
        threading.Thread(target=watch, daemon=True).start()
    return on_start


def rounds_context(case: dict, data: dict) -> dict:
    sup, control = data["supervised"], Path(case["control"]).resolve()
    closed = [r["round"] for r in sup["rounds"] if r.get("evidence_sha256")]
    return {"round": sup["current_round"],
            "reviews": [r["review"]["path"] for r in sup["rounds"] if r.get("review")],
            "summaries": [str(control / "supervised" / f"round_{n}" / "summary.json") for n in closed]}


def round_session(case, case_path, repo, env, control, task_dir, record, state_path, info,
                  session, settings, clock) -> None:
    """One fresh native CC session for the current round, stopped at its boundary."""
    data = read_ledger(task_dir)
    r = data["supervised"]["current_round"]
    if session is None:  # every later session gets its own append-only directory
        used = [int(d.name) for d in (control / "sessions").glob("[0-9]*") if d.name.isdigit()]
        session = control / "sessions" / f"{max(used, default=0) + 1:02d}"
        session.mkdir(parents=True, exist_ok=False)
        info = {"session": str(session), "resume": True, "started_at": clock(),
                "continues_round": r}
        record["sessions"].append(info)
        atomic_json(session / "worker-settings.json", settings)
    info["supervised_round"] = r
    prompt_path, coordinator_path = write_prompts(case, repo, session, rounds_context(case, data))
    remaining = int(development_deadline(task_dir) - clock())
    info["solver_timeout"] = max(0, remaining)
    record["status"] = "running"
    atomic_json(state_path, record)
    s = shim(case, repo)
    command = legacy.native_command(s, coordinator_path.read_text(), settings,
                                    agents=legacy.worker_agent(s, prompt_path))
    atomic_json(session / "native-command.json",
                {"argv": command_record(command, coordinator_path, session / "worker-settings.json"),
                 "cwd": str(repo), "dangerously_skip_permissions": False, "supervised_round": r})
    plan = closeout.config(case)
    grace = plan["report_grace_seconds"] if plan else sp.rounds.grace_seconds(case)
    info["solver_exit_code"] = run_native_cc(
        command, cwd=repo, env=env, stdout_path=session / "coordinator.stdout.jsonl",
        stderr_path=session / "coordinator.stderr.log", timeout=max(1, remaining),
        on_start=boundary_watch(task_dir, r, grace, info, report_grace=plan is not None))
    info["finished_at"] = clock()
    verify_runtime(case, repo)
    atomic_json(state_path, record)


def await_review(case, case_path, repo, env, control, task_dir, record, state_path, round_,
                 clock, sleep) -> bool:
    """Short non-model file wait for the root-owned review; False at the deadline."""
    path = sp.rounds.review_path(case, round_)
    sup = record["supervised"]
    rejected = sup.setdefault("rejected_reviews", [])
    seen = {x["sha256"] for x in rejected if x["round"] == round_}
    deadline = development_deadline(task_dir)
    summary = control / "supervised" / f"round_{round_}" / "summary.json"
    sup["awaiting"] = {"round": round_, "review_path": str(path), "summary": str(summary),
                       "evidence_sha256": read_ledger(task_dir)["supervised"]["rounds"][round_ - 1][
                           "evidence_sha256"], "since": clock(), "deadline": deadline}
    record.update(status="awaiting_review", blocker=None)
    atomic_json(state_path, record)
    while clock() < deadline:
        try:
            sha = sp.file_sha256(path) if path.is_file() else None
        except OSError as exc:
            sha = f"unreadable: {exc}"
        if sha and sha not in seen:
            step = outer_step(case_path, repo, env, control, f"consume_review_r{round_}_{len(rejected) + 1:02d}",
                              PROTOCOL, "consume-review", "--review", str(path))
            if step["exit_code"] == 0:
                sup["consumed"] = sup.get("consumed", []) + [{"round": round_, "sha256": sha,
                                                              "at": clock()}]
                sup.pop("awaiting", None)
                atomic_json(state_path, record)
                return True
            seen.add(sha)
            rejected.append({"round": round_, "sha256": sha, "at": clock(),
                             "error": (step.get("result") or {}).get("error") or step["stderr_tail"]})
            atomic_json(state_path, record)
        sleep(max(1, min(sp.rounds.poll_seconds(case), deadline - clock())))
    return False


def closeout_store(record: dict, round_: int) -> dict:
    """Persisted closeout state of one round; a resume continues it, never resets it."""
    return record["supervised"].setdefault("closeout", {}).setdefault(
        str(round_), {"round": round_, "state": "trials_complete", "sessions": [], "verifications": []})


def previous_reports(record: dict, round_: int) -> dict | None:
    done = [v for k, v in (record["supervised"].get("closeout") or {}).items()
            if int(k) < round_ and v.get("state") == "complete"]
    return max(done, key=lambda v: v["round"])["reports"] if done else None


def closeout_session(case, repo, env, control, task_dir, record, state_path, settings, clock,
                     result, store, timeout) -> None:
    """One restricted native closeout session of this cell: reports and select only."""
    used = [int(d.name) for d in (control / "sessions").glob("[0-9]*") if d.name.isdigit()]
    session = control / "sessions" / f"{max(used, default=0) + 1:02d}"
    session.mkdir(parents=True, exist_ok=False)
    info = {"session": str(session), "kind": "closeout", "closeout_round": result["round"],
            "started_at": clock(), "solver_timeout": timeout}
    record["sessions"].append(info)
    store["sessions"].append(str(session))
    store["state"] = "closeout_session"
    atomic_json(session / "worker-settings.json", settings)
    context = rounds_context(case, read_ledger(task_dir))
    prompt = closeout.session_prompt(case, task_dir, result, context["reviews"] + context["summaries"])
    (session / "closeout-prompt.md").write_text(prompt)
    command = legacy.native_command(shim(case, repo), prompt, settings)
    atomic_json(session / "native-command.json",
                {"argv": command_record(command, session / "closeout-prompt.md",
                                        session / "worker-settings.json"),
                 "cwd": str(repo), "dangerously_skip_permissions": False,
                 "closeout_round": result["round"]})
    atomic_json(state_path, record)
    info["solver_exit_code"] = run_native_cc(
        command, cwd=repo, env=env, stdout_path=session / "coordinator.stdout.jsonl",
        stderr_path=session / "coordinator.stderr.log", timeout=max(1, timeout),
        on_start=closeout.session_watch(task_dir, store["boundary"], info))
    info["finished_at"] = clock()
    verify_runtime(case, repo)
    atomic_json(state_path, record)


def round_closeout(case, case_path, repo, env, control, task_dir, record, state_path, settings,
                   clock) -> tuple[str, str] | None:
    """Opt-in: None once the complete round's closeout is verified, else a terminal end.

    Bounded by max_sessions restricted sessions and the unchanged deadline; no
    trial, refund or clock change. A failed verification never closes the round.
    """
    plan, data = closeout.config(case), read_ledger(task_dir)
    r = data["supervised"]["current_round"]
    store = closeout_store(record, r)
    if store["state"] == "complete":
        return None
    store.setdefault("trials_complete_at", clock())
    store.setdefault("boundary", closeout.boundary(data, task_dir, r))
    out = control / "supervised" / f"round_{r}"
    out.mkdir(parents=True, exist_ok=True)
    while True:
        step = outer_step(case_path, repo, env, control,
                          f"closeout_status_r{r}_{len(store['verifications']) + 1:02d}", PROTOCOL, "status")
        if step["exit_code"] or not isinstance(step["result"], dict):
            store["state"] = "incomplete"
            atomic_json(out / "closeout.json", store)
            return ("development_incomplete",
                    f"round {r} closeout: protocol status failed: {step['result'] or step['stderr_tail']}")
        result = closeout.verify(read_ledger(task_dir), step["result"], task_dir, r, store["boundary"],
                                 previous_reports(record, r))
        store["verifications"].append({**{k: result[k] for k in (
            "complete", "errors", "violations", "reports", "selected", "fix_bundle_sha256")},
            "at": clock()})
        if result["complete"]:
            store.update(state="complete", completed_at=clock(), reports=result["reports"])
        elif result["violations"]:
            store["state"] = "violated"
        remaining = development_deadline(task_dir) - clock()
        exhausted = len(store["sessions"]) >= plan["max_sessions"] or remaining <= 0
        if not result["complete"] and not result["violations"] and exhausted:
            store["state"] = "incomplete"
        atomic_json(out / "closeout.json", store)
        atomic_json(state_path, record)
        if result["complete"]:
            return None
        if result["violations"]:
            return ("development_incomplete",
                    f"round {r} closeout integrity violation: {'; '.join(result['violations'])}")
        if exhausted:
            return ("development_incomplete",
                    f"round {r} trials are complete but its closeout is not, after "
                    f"{len(store['sessions'])} restricted session(s)"
                    f"{' and at the development deadline' if remaining <= 0 else ''}: "
                    f"{'; '.join(result['errors'])}")
        closeout_session(case, repo, env, control, task_dir, record, state_path, settings, clock,
                         result, store, int(min(plan["session_timeout_seconds"], remaining)))


def supervised_end(case, control, record, data, status: str, reason: str) -> str:
    """A terminal development outcome without held-out: recorded, no OUTER marker."""
    record.update(status=status, heldout="skipped", blocker=reason)
    atomic_json(control / "supervised" / "heldout_skipped.json", {
        "cell": case["id"], "study_id": case["supervised_repair"]["study_id"], "status": status,
        "reason": reason, "supervised": data.get("supervised"),
        "study_successes": sp.rounds.study_successes(data),
        "charged_attempts_used": sum(1 for r in data["trials"] if r.get("spends_retry")),
        "heldout": "skipped: no outer marker was created and no held-out seed ran"})
    return status


def supervised_development(case, case_path, repo, env, control, task_dir, record, state_path,
                           info, session, settings, clock, sleep) -> str | None:
    """Rounds, boundaries and reviews. None means: closed with a new success, go finalize."""
    record.setdefault("supervised", {})
    (control / "supervised").mkdir(exist_ok=True)
    first = (info, session)
    while True:
        settle_running(case_path, repo, env, control, task_dir, record)
        data = read_ledger(task_dir)
        sup = data["supervised"]
        r = sup["current_round"]
        if sup["state"] == "closed":
            break
        if clock() >= development_deadline(task_dir):
            if (closeout.config(case) and sup["state"] == "open" and sp.rounds.round_complete(data, r)
                    and closeout_store(record, r)["state"] != "complete"):
                return supervised_end(case, control, record, data, "development_incomplete",
                                      f"development deadline passed during round {r} closeout "
                                      f"({closeout_store(record, r)['state']}); the round was not closed")
            if not sp.rounds.study_successes(data):
                return supervised_end(case, control, record, data, "development_failed",
                                      "development deadline passed with no new oracle success")
            return supervised_end(case, control, record, data, "development_incomplete",
                                  f"development deadline passed in round {r} ({sup['state']}); "
                                  "a timeout is never an outer review")
        if sup["state"] == "awaiting_review":
            if not await_review(case, case_path, repo, env, control, task_dir, record, state_path,
                                r, clock, sleep):
                continue  # deadline reached: handled above
            continue
        if sp.rounds.round_complete(data, r):
            if closeout.config(case):
                # Opt-in: trials complete != closeout complete; close only after the latter.
                ended = round_closeout(case, case_path, repo, env, control, task_dir, record,
                                       state_path, settings, clock)
                if ended:
                    return supervised_end(case, control, record, read_ledger(task_dir), *ended)
            step = outer_step(case_path, repo, env, control, f"close_round_{r}", PROTOCOL,
                              "close-round")
            if step["exit_code"]:
                raise LaunchRefused(f"close-round {r} failed: {step['result'] or step['stderr_tail']}")
            continue
        before = len(sp.rounds.round_rows(data, r))
        round_session(case, case_path, repo, env, control, task_dir, record, state_path,
                      *(first if first[1] is not None else ({}, None)), settings, clock)
        first = ({}, None)
        settle_running(case_path, repo, env, control, task_dir, record)
        after = read_ledger(task_dir)
        if not sp.rounds.round_complete(after, r) and len(sp.rounds.round_rows(after, r)) == before:
            # Visible and resumable (--resume); never silently re-launched in a loop.
            owed = sorted(set(after["identity"]["dev_seeds"])
                          - {row["seed"] for row in sp.rounds.round_rows(after, r)})
            record.update(status="stage1_incomplete",
                          blocker=f"the round {r} session ended without a new attempt; seeds "
                                  f"{owed} remain owed this round (resume explicitly)")
            return "stage1_incomplete"
    closure = sup.get("closure") or {}
    if not sp.rounds.study_successes(data):
        return supervised_end(case, control, record, data, "development_failed",
                              f"no normal oracle success in this study ({closure.get('reason')}); "
                              "held-out is skipped")
    if closure.get("reason") == "outer_stop":
        return supervised_end(case, control, record, data, "development_stopped",
                              f"the outer review of round {closure.get('round')} stopped the study")
    record["supervised"]["closure"] = closure
    return None


def run(case_path: Path, *, resume: bool = False, heldout: bool = True, clock=time.time,
        sleep=time.sleep) -> int:
    case_path = case_path.resolve()
    case, repo, task_dir = load(case_path)
    control = Path(case["control"]).resolve()
    state_path = control / "campaign_state.json"
    previous = json.loads(state_path.read_text()) if state_path.is_file() else None
    # Launched means the ledger (and with it the development deadline) exists.
    launched = (task_dir / "development_state.json").is_file()
    if launched and not resume:
        raise LaunchRefused("this cell was already launched; there is no hidden restart. "
                            "Use --resume as a recorded outer action")
    if resume and not launched:
        raise LaunchRefused("nothing to resume: the ledger was never initialized")
    if resume and (not previous or previous.get("status") in TERMINAL
                   or previous.get("status") == "running"):
        raise LaunchRefused(f"cannot resume from status {previous and previous.get('status')}")
    # Who runs this and on what runtime are checked before anything is written.
    user, isolated = runtime_user(), isolation(case, repo)
    record = previous or {"cell": case["id"], "profile": case["profile"], "sessions": [],
                          "started_at": datetime.now(timezone.utc).isoformat(),
                          # Opt-in repair continuation: `init` imports this bound parent.
                          **({"continuation": case["continuation"]} if "continuation" in case else {})}
    # Sessions are append-only: a new index after every directory already on disk.
    used = [int(d.name) for d in (control / "sessions").glob("[0-9]*") if d.name.isdigit()]
    session = control / "sessions" / f"{max(used, default=0) + 1:02d}"
    session.mkdir(parents=True, exist_ok=False)
    info = {"session": str(session), "resume": resume, "started_at": clock()}
    record["sessions"].append(info)
    record.update(status="preflight", blocker=None)
    try:
        info["user"], info["isolation"] = user, isolated
        info["native_binary"] = native_binary(case)
        info["task"] = task_metadata(case)
        info["runtime_manifest_sha256"] = verify_runtime(case, repo)
        info["model_service"] = sp.model_service_identity(case)
        info["perception_manifest"] = sp.perception_identity(case)
        info["perception"] = perception_ready(case)
        Path(case["claude_config_dir"]).mkdir(parents=True, exist_ok=True)
        settings = worker_settings(case, repo, case_path)
        settings_path = session / "worker-settings.json"
        atomic_json(settings_path, settings)
        env = native_env(case, repo, case_path)
        atomic_json(session / "worker-environment.json", {
            "keys": sorted(env), "declared": {k: env.get(k) for k in (
                "CLAUDE_CONFIG_DIR", "ASPIRE_NATIVE_CASE", "ASPIRE_ROOT", "PYTHONPATH",
                "CUDA_VISIBLE_DEVICES", "MUJOCO_EGL_DEVICE_ID", "MUJOCO_GL", "LIBERO_CONFIG_PATH",
                "HOME")}})
        atomic_json(state_path, record)
        s = shim(case, repo)
        # A tiny native call with no task content: route, model and capacity only.
        info["probe"] = legacy.probe(s, session, env, settings)
        if info["probe"].get("context_windows") != [case["context_tokens"]]:
            raise ServiceFailure({"probe_context_window": info["probe"].get("context_windows")})
        prompt_path, coordinator_path = write_prompts(case, repo, session)
        if not launched:
            record["init"] = outer_step(case_path, repo, env, control, "init", PROTOCOL, "init")
            if record["init"]["exit_code"]:
                raise LaunchRefused(f"protocol init failed: {record['init']['stderr_tail']}")
        deadline = development_deadline(task_dir)
        remaining = int(deadline - clock())
        info.update(development_deadline=deadline, solver_timeout=max(0, remaining))
        record["status"] = "running"
        atomic_json(state_path, record)
        transcript = session / "coordinator.stdout.jsonl"
        if "supervised_repair" in case:
            # Opt-in: one native session per round, outer review between rounds.
            ended = supervised_development(case, case_path, repo, env, control, task_dir, record,
                                           state_path, info, session, settings, clock, sleep)
            if ended:
                return finish(record, state_path)
        elif remaining > 0:
            command = legacy.native_command(s, coordinator_path.read_text(), settings,
                                            agents=legacy.worker_agent(s, prompt_path))
            atomic_json(session / "native-command.json",
                        {"argv": command_record(command, coordinator_path, settings_path),
                         "cwd": str(repo), "dangerously_skip_permissions": False})
            info["solver_exit_code"] = run_native_cc(
                command, cwd=repo, env=env, stdout_path=transcript,
                stderr_path=session / "coordinator.stderr.log", timeout=remaining)
        else:
            info["solver_exit_code"] = None
            info["solver_skipped"] = "development deadline already expired"
        info["finished_at"] = clock()
        verify_runtime(case, repo)
        record["status"] = "solver_exited"
        atomic_json(state_path, record)
        record["check"] = outer_step(case_path, repo, env, control, "check", PROTOCOL, "check")
        if record["check"]["exit_code"]:
            record.update(status="stage1_incomplete", blocker="stage 1 check is not ready")
            return finish(record, state_path)
        transcripts = [str(Path(i["session"]) / "coordinator.stdout.jsonl")
                       for i in record["sessions"]
                       if (Path(i["session"]) / "coordinator.stdout.jsonl").is_file()]
        args = [a for t in transcripts for a in ("--transcript", t)]
        record["finalize"] = outer_step(case_path, repo, env, control, "finalize", PROTOCOL,
                                        "finalize", *args, "--worker-settings", str(settings_path),
                                        "--probe", str(session / "probe.json"))
        if record["finalize"]["exit_code"]:
            record.update(status="incomplete",
                          blocker=f"finalize refused: {record['finalize']['stderr_tail']}"
                                  f"{json.dumps(record['finalize']['result'])}")
            return finish(record, state_path)
        record["status"] = "stage1_finalized"
        if heldout and "supervised_repair" in case and (read_json_file(
                task_dir / "stage1_result.json").get("supervised_repair") or {}).get(
                "heldout_gate") != "passed":
            # finalize enforces the gate; the outer marker is never written without it.
            record.update(status="development_failed", heldout="skipped",
                          blocker="stage-1 result records no passed supervised held-out gate")
            return finish(record, state_path)
        if heldout:
            # The solver session has exited; only now is the outer marker created.
            atomic_json(control / "outer_heldout_authorization.json",
                        {"created_by": "streaming_fixloop_campaign", "at": clock(),
                         "cell": case["id"], "solver_exited": True})
            (control / guard.OUTER_MARKER).write_text(case["id"] + "\n")
            record["heldout"] = outer_step(case_path, repo, env, control, "heldout", HELDOUT)
            code = record["heldout"]["exit_code"]
            record["status"] = {0: "complete", 1: "heldout_incomplete"}.get(code, "heldout_refused")
    except LaunchRefused as exc:
        record.update(status="refused", blocker=str(exc))
    except (ServiceFailure, RuntimeChanged, OSError, ValueError, subprocess.SubprocessError) as exc:
        record.update(status="blocked", blocker=f"{type(exc).__name__}: {exc}")
    return finish(record, state_path)


def finish(record: dict, state_path: Path) -> int:
    record["finished_at"] = datetime.now(timezone.utc).isoformat()
    atomic_json(state_path, record)
    print(json.dumps(record, ensure_ascii=False, indent=2, default=str))
    if record["status"] == "complete" or (record["status"] == "stage1_finalized"):
        return 0
    return 2 if record["status"] == "refused" else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--case", type=Path, required=True)
    parser.add_argument("--resume", action="store_true",
                        help="explicit outer resume: same ledger, same deadline, no new budget")
    parser.add_argument("--no-heldout", action="store_true",
                        help="stop after stage-1 finalize; held-out runs later from the outer side")
    parser.add_argument("--render-only", action="store_true",
                        help="write the prompts and diff into <control>/render, run nothing")
    args = parser.parse_args(argv)
    try:
        if args.render_only:
            case, repo, _ = load(args.case)
            out = Path(case["control"]) / "render"
            out.mkdir(parents=True, exist_ok=True)
            prompt, coordinator = write_prompts(case, repo, out)
            print(json.dumps({"worker_prompt": str(prompt), "coordinator": str(coordinator)}))
            return 0
        return run(args.case, resume=args.resume, heldout=not args.no_heldout)
    except (LaunchRefused, sp.ProtocolError, OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc), "type": type(exc).__name__}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
