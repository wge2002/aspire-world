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
import grp
import hashlib
import json
import os
import pwd
import re
import subprocess
import sys
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
TERMINAL = {"stage1_finalized", "complete", "heldout_incomplete", "heldout_refused"}


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


def worker_prompt(case: dict, repo: Path) -> str:
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
tracked nodes' public SAM3 prompts, scores and optional image_x/image_y rank
selector; untracked parent nodes organize the graph without inventing geometry.
Semantic labels and motion hypotheses are uncertain, not measured truth.

Use the injected goto_pose_report to inspect requested TCP arrival and the
effective IK target. It does not prove grasp/contact or task success. Public
robot_cartesian_pos is robot_base/panda_hand; the added robot_hand_pose_world is
world/panda_hand; goto_pose requests world/TCP and applies its existing offset.
Never compare positions from different control points. Motion stagnation is
not arrival. A quaternion argument contains exactly one WXYZ quaternion.

The public helpers world_set_focus, world_refresh, world_require_relation,
world_begin_interaction, world_finish_interaction and world_revise_structure
allow the policy to validate evidence and respond to conflicts. Use fresh
part observations and authored evidence-based relation predicates. Do not
interpret a closed gripper as contact. If execution/contact is unverified,
update that diagnosis before revising the object's structure. When structure
changes, cite recorded visual evidence and preserve IDs/lineage. Reacquisition
rejects conflicting or ambiguous instances. World resets per episode; no other
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
existing events; it cannot invent evidence or force an extra query. Freeze
prompts, model templates, numerical thresholds, request settings and update
rules with the policy/config. Runtime updates reset every episode and never
repair a frozen program, choose a program per seed, or use scoring feedback.
"""
    return text


def coordinator_prompt(case: dict, prompt_path: Path) -> str:
    return legacy.COORDINATOR.format(cell=case["id"], suite=case["suite"], task=case["task"],
                                     prompt_path=prompt_path,
                                     promotion=legacy.PROMOTION_C.strip(), protocol=PROTOCOL)


def write_prompts(case: dict, repo: Path, control: Path) -> tuple[Path, Path]:
    rendered = worker_prompt(case, repo)
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


def run(case_path: Path, *, resume: bool = False, heldout: bool = True, clock=time.time) -> int:
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
                          "started_at": datetime.now(timezone.utc).isoformat()}
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
        if remaining > 0:
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
