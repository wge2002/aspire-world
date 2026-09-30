#!/usr/bin/env python3
"""Launch one native-CC Fix Loop cell and its frozen held-out evaluation.

Only Claude Code runs the model/tool loop and context compaction. This process
starts owned services, checks artifacts, and launches deterministic evaluation.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
import urllib.request

from native_cc_protocol import runtime_env, identity
from fix_loop_state import Stage1State

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "common"))
from native_cc_stream import run_native_cc, stream_command
from native_cc_permissions import skill_write_denials
from native_cc_runtime import (Service, ServiceWatch, atomic_json, isolated_jit_env,
                               new_attempt, stop_processes)
from native_cc_freeze import verify_runtime, REQUIRED_FILES
from native_cc_guard import settings as guard_settings


def clean_env() -> dict[str, str]:
    return {k: os.environ[k] for k in ("PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "LC_ALL", "TERM", "TMPDIR", "NVIDIA_VISIBLE_DEVICES", "NVIDIA_DRIVER_CAPABILITIES") if k in os.environ}


def worker_prompt(case: dict, repo: Path) -> str:
    text = (repo / ".claude/libero/fix-loop/subagent-prompt.md").read_text()
    text = text.split("TEMPLATE START ==================== -->", 1)[1].split("<!-- ==================== TEMPLATE END", 1)[0]
    text = re.sub(r"SUITE: <[^\n]+", f"SUITE: {case['suite']}", text)
    text = re.sub(r"TASK:  <[^\n]+", f"TASK: {case['task']}", text)
    text = re.sub(r"GPU:   <[^\n]+", f"GPU: {case['gpu']}", text)
    cli = ".venv-libero/bin/python3 scripts/libero/native_cc_protocol.py"
    text = text.replace("Run every replay with `CUDA_VISIBLE_DEVICES=$GPU`.", "Run every replay through the recorded protocol CLI below; it sets the approved CUDA/EGL mapping.")
    text = text.replace("8114 8115 8116", "8214 8215 8216")
    text = text.replace("Follow `.claude/libero/fix-loop/skills/task-exploration.md`, then read", f"Run `{cli} trial --phase snapshot --seed 51` exactly once. Read its replay.log for TASK_LANGUAGE and Read both scene_snapshot.jpg and scene_snapshot_wrist.jpg. Follow the analysis format in `.claude/libero/fix-loop/skills/task-exploration.md` (its raw replay command is replaced by the recorded CLI), then read")
    text = text.replace("Smoke-test seed 51 first and fix any crash before continuing.", f"Smoke-test with `{cli} trial --phase smoke --seed 51 --code \"$TASK_DIR/initial_code.py\"`. Stop smoke after the first non-crashing run; at most three smoke invocations are permitted. If all three crash, freeze the current initial program and continue to the initial batch.")
    text = re.sub(r"for trial in \$\(seq 51 65\); do\n.*?\ndone", f'{cli} trial --phase initial --seed 51 --code "$TASK_DIR/initial_code.py"\n# After this ONE call completes, issue a separate Bash call for seed 52, then 53, through 65.\n# Never wrap the seeds in a shell or Python loop.', text, count=1, flags=re.S)
    start = text.index("Write a fix based on your diagnosis and test it on the failed seed:")
    end = text.index("Check reward in output dir name:", start)
    text = text[:start] + f'Write each candidate inside $TASK_DIR/attempts/ and test it on the failed seed:\n  {cli} trial --phase repair --seed <N> --code "$TASK_DIR/attempts/fix_attempt.py"\n\n' + text[end:]
    start = text.index("**REPL for live inspection**")
    end = text.index("**Hard limit:", start)
    text = text[:start] + "Read recorded traces, summaries, and keyframes. Additional interactive simulator calls are not part of this fixed-budget run.\n\n" + text[end:]
    contract = f"""# Approved native CC cell
This is the complete assignment for one independent cell. The user has approved this task and its fixed budgets. Services and the development ledger have already been initialized. Do not ask for another preflight approval or choose other tasks.
ASPIRE_NATIVE_CASE={case['control']}/case.json
Working directory: {repo}
All simulator calls MUST use `{cli} trial`. Do not invoke replay_trial.py directly, start another simulator, run held-out evaluation, modify framework/configuration/ledger files, or start/stop services. This CLI provides accounting only; you are the native Claude Code solver.
The CLI records snapshot/smoke/initial/repair separately, rejects held-out seeds, freezes initial_code.py at the initial batch, and enforces three repairs for EACH initially failed seed. A failure on seed 51 does not use any other seed's repair budget. Run seeds serially within this task. An infrastructure_error is a blocker to report, never a model score or permission to reset the ledger.
One trial per Bash tool call, with timeout=1000000 milliseconds. If native CC backgrounds the call, wait with TaskOutput/native notification until that single trial finishes before the next seed. Never put multiple seeds into one Bash/Python loop: long native background Bash tasks can be killed independently of the campaign timeout. The per-trial infrastructure watchdog is 900 seconds.
Use `{cli} status` to recover after native compaction; keep concise notes in $TASK_DIR/notes.md. Preserve all previous artifacts. A completed failed replay is ordinary development evidence: continue to the next allowed attempt or next seed.
Before returning, run `{cli} check` and resolve every error. The final program must match a program actually tested within the permitted budget; synthesize and test while a repair slot remains, or select an already tested candidate. Do not request extra replays after the budget. Apply the documented single-observation fallback only if every candidate crashed.
Use Read for images. Read only evidence from THIS cell and the initial checked-in API/skill templates. No historical experiment outputs, learned-skill branches, external baselines, simulator assets, or other cells.
For large traces/logs, use Python via Bash to select relevant fields, or Read with a bounded line range (about 120 lines at a time). Keep durable summaries in notes.md; avoid repeatedly loading entire large trace files, including after native compaction.

"""
    return (contract + text).replace(".claude/libero/skills", case.get("skill_library_dir", ".claude/libero/skills"))


def development_readiness(case: dict, repo: Path) -> dict:
    verify_runtime(case, repo)
    task = repo / "outputs/libero_fix_loop" / case["suite"] / case["task"]
    state = Stage1State(task, identity(case, repo), resume=True)
    working = repo / "outputs/working_codes" / f"{case['suite']}_{case['task']}_fix.py"
    errors = state.completion_errors(working_code=working)
    return {"ready": not errors, "errors": errors, "progress": state.progress()}


def native_final_report(transcript: Path) -> dict:
    result = {}
    for line in transcript.read_text().splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("type") == "result":
            result = {k: record.get(k) for k in ("is_error", "subtype", "result")}
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", type=Path, required=True)
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--resume-session", help="Resume this recorded native CC session without resetting trials")
    parser.add_argument("--resume-worker", help="Existing native worker agent ID to resume")
    parser.add_argument("--attempt-id", help="Unique launch attempt; never reuse an existing ID")
    parser.add_argument("--recover-trial", help="Explicit interrupted ledger directory to complete with its frozen code")
    args = parser.parse_args()
    if bool(args.resume_session) != bool(args.resume_worker):
        parser.error("--resume-session and --resume-worker must be used together")
    if args.recover_trial and not args.resume_session:
        parser.error("--recover-trial requires an explicit session resume")
    case = json.loads(args.case.read_text())
    repo, control = Path(case["sim"]), Path(case["control"])
    env = runtime_env(case, repo, clean_env())
    env["ASPIRE_NATIVE_CASE"] = str(args.case.resolve())
    py = str(repo / ".venv-libero/bin/python3")
    cli = [py, "scripts/libero/native_cc_protocol.py", "--case", str(args.case.resolve())]
    owned: list[subprocess.Popen] = []
    invocation_dir = None if args.preflight else new_attempt(control, args.attempt_id)
    logs = invocation_dir / "logs" if invocation_dir is not None else None
    watch = None

    def status(state: str, **extra):
        record = {"case": case["id"], "state": state, "updated_at": time.time(),
                  "supervisor_pid": os.getpid(), "host": os.uname().nodename,
                  "owned_pids": [p.pid for p in owned],
                  "attempt_dir": str(invocation_dir) if invocation_dir else None, **extra}
        if invocation_dir is not None:
            atomic_json(invocation_dir / "status.json", record)
            atomic_json(control / "status.json", record)
        print(json.dumps(record), flush=True)

    def spawn(command, name, run_env):
        with (logs / name).open("x") as log:
            p = subprocess.Popen(command, cwd=repo, env=run_env, stdin=subprocess.DEVNULL,
                                 stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        owned.append(p)
        return p

    def run(command, **kwargs):
        return subprocess.run(command, cwd=repo, env=env, check=True, **kwargs)

    def interrupted(signum, frame):
        raise RuntimeError(f"campaign received signal {signum}; preserve partial evidence")

    handlers = {sig: signal.signal(sig, interrupted) for sig in (signal.SIGTERM, signal.SIGINT)}
    try:
        for path in (repo / ".venv-libero/bin/python3", Path(case["claude_bin"]),
                     Path(case["model_server_config"]), Path("/mnt/home/gewang/.cache/aspire/sam3/sam3.pt"),
                     *(repo / p for p in REQUIRED_FILES)):
            if not path.is_file():
                raise RuntimeError(f"missing preflight dependency: {path}")
        verify_runtime(case, repo)
        if case.get("skill_permission_preflight") and not case.get("native_preflight_on_node"):
            proof = json.loads(Path(case["skill_permission_preflight"]).read_text())
            if not (proof["passed"] and proof["pristine_skills_restored"] and
                    proof["case"] == case["id"] and proof["models"] == [case["model"]]):
                raise RuntimeError("this case's real native skill Write/Edit preflight is missing or failed")
        run([py, "-c", "import os,getpass,torch,mujoco; assert os.access('.',os.R_OK); assert getpass.getuser(); assert not any(k in os.environ for k in ('RANK','WORLD_SIZE','MASTER_ADDR')); c=mujoco.GLContext(96,96); c.make_current(); c.free(); print('NATIVE_CC_DLC_PREFLIGHT_OK',torch.__version__)"])
        # PyRoKi imports Robosuite, whose legacy EGL check also validates that
        # the EGL id occurs in CUDA_VISIBLE_DEVICES. Exercise that full import.
        run([py, "cap/serving/launch_pyroki_server.py", "--help"], stdout=subprocess.DEVNULL)
        if args.preflight:
            return 0
        if args.resume_session:
            session_files = list((control / "campaign-cc/projects").glob(f"*/{args.resume_session}.jsonl"))
            if len(session_files) != 1 or not (session_files[0].with_suffix("") / "subagents" / f"agent-{args.resume_worker}.jsonl").is_file():
                raise RuntimeError("resume requires this cell's existing native session and worker transcripts")
        elif (control / "campaign-cc").exists():
            raise RuntimeError("campaign CC state already exists; inspect/resume it explicitly, do not overwrite")
        status("starting_services")
        services = []
        if case["execution"] == "dlc":
            config = json.loads(Path(case["model_server_config"]).read_text())
            cache_env = isolated_jit_env(case["id"])
            atomic_json(invocation_dir / "jit-cache.json", cache_env)
            process = spawn(config["argv"], "model.log", clean_env() | config["env"] | cache_env)
            services.append(Service("model", process, logs / "model.log", case["endpoint"] + "/health"))
            # Keep the preflighted CUDA/EGL pair together (e.g. 4,0 / 0).
            service_env = env | {"SAM3_CHECKPOINT_PATH": "/mnt/home/gewang/.cache/aspire/sam3/sam3.pt", "HF_HUB_OFFLINE": "1"}
            for script, port, extra in (("sam3", 8214, ["--device", "cuda"]), ("contact_graspnet", 8215, []), ("pyroki", 8216, [])):
                process = spawn([py, "-u", f"cap/serving/launch_{script}_server.py", "--host", "127.0.0.1", "--port", str(port), *extra], script + ".log", service_env)
                services.append(Service(script, process, logs / (script + ".log"),
                                        f"http://127.0.0.1:{port}/health", allow_404=True))
        else:
            urls = [("model", case["endpoint"]), *[(k, env[k]) for k in
                    ("SAM3_SERVICE_URL", "GRASPNET_SERVICE_URL", "PYROKI_SERVICE_URL")]]
            services = [Service(name, None, logs / (name + ".log"), url + "/health",
                                allow_404=name != "model") for name, url in urls]
        watch = ServiceWatch(services, lambda: stop_processes(owned))
        watch.start()
        watch.wait_ready(timeout=int(case.get("service_startup_timeout_seconds", 1800)))
        with urllib.request.urlopen(case["endpoint"] + "/v1/models", timeout=10) as response:
            models = json.load(response)["data"]
        if not any(m["id"] == case["model"] and m["max_model_len"] == case["context_tokens"] for m in models):
            raise RuntimeError(f"served model/context mismatch: {models}")
        if case.get("native_preflight_on_node"):
            status("toolchain_preflight")
            probe_dir = invocation_dir / "toolchain-preflight"
            with (logs / "toolchain-preflight.log").open("x") as log:
                subprocess.run([py, "scripts/libero/replay_trial.py", "--args.suite", case["suite"],
                    "--args.task", case["task"], "--args.trial", "51", "--args.model", "infrastructure-rehearsal",
                    "--args.replay-code", "scripts/libero/native_cc_toolchain_probe.py",
                    "--args.config", "env_configs/libero/franka_libero_traced.yaml",
                    "--args.output-dir", str(probe_dir / "results")], cwd=repo,
                    env=env | {"ASPIRE_TOOLCHAIN_PROBE_DIR": str(probe_dir),
                               "ASPIRE_TOOLCHAIN_PROBE_OBJECT": case["toolchain_probe_object"]},
                    stdout=log, stderr=subprocess.STDOUT, check=True, timeout=900)
            proof = json.loads((probe_dir / "toolchain.json").read_text())
            if not proof["passed"]:
                raise RuntimeError("actual observation/segmentation/grasp/IK preflight failed")
            status("native_skill_preflight")
            proof_path = Path(case["skill_permission_preflight"])
            if not proof_path.exists():
                run([py, "scripts/common/native_cc_skill_probe.py", "--case", str(args.case),
                     "--output", str(proof_path.parent)], timeout=1000)
            proof = json.loads(proof_path.read_text())
            if not (proof["passed"] and proof["pristine_skills_restored"] and
                    proof["case"] == case["id"] and proof["models"] == [case["model"]]):
                raise RuntimeError("native skill preflight failed; no formal trial started")
            verify_runtime(case, repo)
        status("compatibility_check", models=models)
        compat = control / case.get("compat_output", "compat-01")
        if not compat.exists():
            run([py, "scripts/common/native_cc_compat.py", "--case", str(args.case), "--output", str(compat)],
                timeout=int(case.get("compat_timeout_seconds", 900)) + 100)
        summary = json.loads((compat / "summary.json").read_text())
        if not summary["passed"]:
            raise RuntimeError("native CC compatibility check failed; no scored trial started")
        if args.recover_trial:
            status("recovering_interrupted_trial", trial=args.recover_trial)
            run([py, "scripts/libero/native_cc_recover_trial.py", "--case", str(args.case),
                 "--directory", args.recover_trial, "--attempt-dir", str(invocation_dir)],
                timeout=int(case.get("trial_timeout", 900)) + 60)
        run(cli + ["status" if args.resume_session else "init"])
        task_dir = f"outputs/libero_fix_loop/{case['suite']}/{case['task']}"
        prompt = worker_prompt(case, repo)
        (invocation_dir / "worker-prompt.md").write_text(prompt)
        agents = {"fix-loop-worker": {"description": "Execute this cell's approved Stage 0 and Stage 1 Fix Loop protocol.", "prompt": prompt, "model": "inherit"}}
        coordinator = f"""Run the approved ASPIRE native Claude Code cell {case['id']} in {repo}.
Model: {case['model']}. Task: {case['suite']}/{case['task']}. Owned simulator GPU: {case['gpu']} (mapping {case['cuda_visible_devices']}, EGL {case['egl_device_id']}). User approval and preflight are complete. Services are ready. There is exactly ONE task in this checkout.
You are the coordinator. Read CLAUDE.md and .claude/libero/CLAUDE.md. Use the native Agent tool to launch exactly one fix-loop-worker with run_in_background=true. Its full protocol is preloaded in the agent definition; pass a short task assignment and ASPIRE_NATIVE_CASE={args.case}. Do not solve or debug the robot task yourself. Do not read traces, replay code, keyframes, historical outputs, or other cells.
Wait for completion using native background task notifications/TaskOutput. Do not run simulator trials yourself. After the worker returns, run `.venv-libero/bin/python3 scripts/libero/native_cc_protocol.py check`. If incomplete, resume the SAME worker with those errors and the durable status, within the original remaining budgets. Never reset the ledger or invent completion.
When check reports ready, perform the canonical skill promotion using Stage 1 evidence only: run `.venv-libero/bin/python3 scripts/libero/record_skill_promotion.py begin --suite {case['suite']} --task {case['task']}`, read {task_dir}/findings.md (and skim fix_code.py only to extract demonstrated generalizable snippets), update .claude/libero/skills/*.md if supported, then run the same promotion script with finish and verify. If nothing generalizes, finish with --reason and a concise evidence-based explanation, keeping skills unchanged.
The deterministic outer coordinator will freeze provenance and launch the canonical held-out validation script after you exit successfully. Do not run seeds 1–50 or start that script yourself. Return NATIVE_CC_STAGE1_READY only after the worker's check and skill-promotion verify pass. Never edit framework scripts, configuration, ledger, or past outputs; never install dependencies or start/stop services. Do not request another approval or dispatch unrelated tasks.
"""
        skills_rel = case.get("skill_library_dir", ".claude/libero/skills")
        promotion_cli = [py, "scripts/libero/record_skill_promotion.py", "--skills-dir", skills_rel]
        coordinator = coordinator.replace(".claude/libero/skills", skills_rel).replace(
            "record_skill_promotion.py ", f"record_skill_promotion.py --skills-dir {skills_rel} ")
        coordinator += "\nA denied skill Write/Edit is an infrastructure blocker. Never claim it as a successful no-op or work around it using Bash. Use native Write/Edit for skill changes.\n"
        if args.resume_session:
            coordinator = coordinator.replace(
                "Use the native Agent tool to launch exactly one fix-loop-worker with run_in_background=true.",
                f"Use the native Agent tool to resume the EXISTING fix-loop-worker with resume={args.resume_worker} and run_in_background=true. Do not create a new worker. The previous DLC was terminated after another cell's model service failed. Its infrastructure has been repaired. This is continuation of the same task, with the same ledger and remaining budgets. Tell the worker to read protocol status and its durable notes, skip completed snapshot/smoke/initial seeds, and run only missing initial seeds before repairs. A prior interrupted replay may have been completed by the infrastructure coordinator with its exact frozen program; inspect status for that recorded result."
            )
        (invocation_dir / "coordinator-prompt.md").write_text(coordinator)
        cc_env = env | {"CC_LOCAL_CONFIG_DIR": str(control / "campaign-cc"), "CC_LOCAL_CONTEXT_TOKENS": str(case["context_tokens"]),
                       "CC_LOCAL_MAX_OUTPUT_TOKENS": str(case["max_output_tokens"]), "CC_LOCAL_EFFORT": case["effort"],
                       "CC_LOCAL_CLAUDE_BIN": case["claude_bin"], "CLAUDE_CODE_AUTO_CONNECT_IDE": "false", "API_TIMEOUT_MS": "300000"}
        command = ["bash", "scripts/common/claude_with_local_model.sh", case["endpoint"], case["model"], "-p", coordinator,
                   "--agents", json.dumps(agents), "--effort", case["effort"], "--output-format", "stream-json", "--verbose",
                   "--forward-subagent-text", "--setting-sources", "", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
                   "--no-chrome", "--permission-mode", "dontAsk", "--tools", "default", "--allowedTools", "Read", "Write", "Edit", "Glob", "Grep", "Bash", "Agent", "TaskOutput", "SendMessage"]
        if case.get("require_runtime_freeze"):
            command.extend(["--settings", json.dumps(guard_settings(args.case, repo))])
        cc_env.update(BASH_MAX_TIMEOUT_MS="1200000", BASH_DEFAULT_TIMEOUT_MS="1000000")
        if args.resume_session:
            command.extend(["--resume", args.resume_session])
        argv, stdin_prompt = stream_command(command)
        (invocation_dir / "cc-invocation.json").write_text(json.dumps({"argv": argv, "stdin_prompt": stdin_prompt,
            "environment": cc_env, "transport": "native-notification-stream"}, indent=2))

        def cc_started(process):
            owned.append(process)
            status("stage1_running", cc_pid=process.pid)

        cc_returncode = run_native_cc(command, cwd=repo, env=cc_env,
            stdout_path=logs / "cc.stdout.jsonl",
            stderr_path=logs / "cc.stderr.log", timeout=int(case.get("stage1_timeout_seconds", 43200)), on_start=cc_started)
        watch.check()
        if cc_returncode:
            raise RuntimeError(f"native CC exited {cc_returncode}; retain partial evidence")
        readiness = development_readiness(case, repo)
        readiness["native_final_report"] = native_final_report(logs / "cc.stdout.jsonl")
        atomic_json(invocation_dir / "development-readiness.json", readiness)
        if not readiness["ready"]:
            raise RuntimeError("native development is incomplete: " + "; ".join(readiness["errors"]))
        if readiness["native_final_report"].get("is_error"):
            raise RuntimeError("native CLI reported an error; inspect development-readiness.json")
        transcripts = [*(control / "logs").glob("cc*.stdout.jsonl"),
                       *(control / "attempts").glob("*/logs/cc*.stdout.jsonl"),
                       *(control / "campaign-cc/projects").rglob("*.jsonl")]
        denials = skill_write_denials(transcripts, repo, skills_rel)
        if denials:
            atomic_json(invocation_dir / "skill-permission-failure.json", {"denials": denials})
            raise RuntimeError("native skill writes were denied; promotion cannot be accepted as a no-op")
        run(promotion_cli + ["verify", "--suite", case["suite"], "--task", case["task"]])
        run(cli + ["finalize", *[a for p in transcripts for a in ("--transcript", str(p))]])
        status("heldout_running")
        validation = [py, "scripts/libero/run_fix_loop_validation.py", "--suite", case["suite"], "--task", case["task"],
                      "--gpu", case["gpu"], "--cuda-visible-devices", case["cuda_visible_devices"], "--egl-device-id", str(case["egl_device_id"]),
                      "--fix-code", task_dir + "/fix_code.py", "--output-dir", "outputs/libero_fix_loop_eval", "--seeds", *map(str, case["heldout_seeds"]), "--resume"]
        with (logs / "heldout.log").open("x") as log:
            run(validation, stdout=log, stderr=subprocess.STDOUT, timeout=int(case.get("heldout_timeout_seconds", 14400)))
        watch.check()
        result = json.loads((repo / task_dir / "validation_result.json").read_text())
        if result["trials"] != 50 or result["seeds"] != list(range(1, 51)):
            raise RuntimeError("required held-out manifest is incomplete")
        status("complete", validation=result)
        return 0
    except Exception as exc:
        if watch is not None and watch.failure is not None:
            exc = watch.failure
        status("blocked", error=f"{type(exc).__name__}: {exc}")
        return 1
    finally:
        for sig, handler in handlers.items():
            signal.signal(sig, handler)
        if watch is not None:
            watch.close()
        stop_processes(owned)
        for p in reversed(owned):
            if p.poll() is None:
                try:
                    p.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    os.killpg(p.pid, signal.SIGKILL)


if __name__ == "__main__":
    raise SystemExit(main())
