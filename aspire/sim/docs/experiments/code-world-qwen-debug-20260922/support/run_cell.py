"""Run an isolated cell with original native same-worker continuation.

Development pilot variant: the native worker is served by a loopback local-vLLM
provider, the scoped pilot surface is applied to the campaign module, an
assignment guard hook is added, the prior diagnostic is imported into the cell
ledger, and the run stops at the pilot boundary without any held-out evaluation.
Frozen runtime, solver interface and trial accounting are unchanged. No task
solution or other cell result is supplied.
"""
import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import sys
from urllib.parse import urlsplit
from infra_guard import bind_child_env, fault, monitor
from lineage import audit_assignment
import native_cc_stream as fixed_transport
import pilot_assignment_guard
import pilot_import
import pilot_scope


PILOT_MODEL = "qwen3.8-flash-next"
LOOPBACK = {"127.0.0.1", "localhost", "::1", "[::1]"}


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def identity(transcripts, assignment):
    audit = audit_assignment(transcripts, assignment)
    if not audit["ok"] or len(audit["sessions"]) != 1:
        raise ValueError("Ambiguous native worker lineage: " + json.dumps(audit))
    return audit["sessions"][0]["session_id"], audit["primary_worker"]


def model_fields(case):
    return {key: value for key, value in case.items()
            if isinstance(value, str) and "model" in key and key != "model_provider"}


def verify_local_provider(case, settings):
    """Fail closed on the pilot provider contract; never fall back to a provider."""
    if case.get("model_provider") != "local-vllm":
        raise ValueError(f"Pilot requires model_provider=local-vllm, got {case.get('model_provider')!r}")
    if "apiKeyHelper" in settings:
        raise ValueError("Native settings carry apiKeyHelper; the local provider takes no key helper")
    endpoint = settings.get("env", {}).get("ANTHROPIC_BASE_URL")
    if not endpoint:
        raise ValueError("Native settings carry no ANTHROPIC_BASE_URL for the local provider")
    if urlsplit(endpoint).hostname not in LOOPBACK:
        raise ValueError(f"Inference endpoint is not loopback: {endpoint}")
    if "inference_endpoint" in case and case["inference_endpoint"] != endpoint:
        raise ValueError("Case inference_endpoint differs from the native settings endpoint")
    models = model_fields(case)
    if len(models) != 3 or set(models.values()) != {PILOT_MODEL}:
        raise ValueError(f"Case model fields are not three x {PILOT_MODEL}: {json.dumps(models, sort_keys=True)}")
    return {"model_provider": case["model_provider"], "endpoint": endpoint,
            "api_key_helper": False, "models": models}


def continuation(worker):
    return f"""Continue this same cell under its original protocol. Use SendMessage
to resume the existing worker {worker}, with this protocol-only message:
"The protocol check is not ready. Read your own ledger status and complete the
pending repair work on debug seeds 51, 52 and 53 using the remaining original
attempts. Keep all existing attempts charged and the same three-total-attempt
cap. Select a tested bundle, complete required reports, and run check before
returning."
Do not create another worker, supply a task strategy, reset budgets or access
another cell or held-out data. This run is a development pilot: debug seeds
51/52/53 are the whole boundary and no held-out evaluation follows it.
After its native completion notification, run the original protocol check and
report its actual result.
"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", type=Path, required=True)
    args = parser.parse_args()
    case = json.loads(args.case.read_text())
    repo, control = Path(case["sim"]), Path(case["control"])
    sys.path.insert(0, str(repo / "scripts/libero"))
    import native_world_campaign as campaign
    # Corrected turn-aware transport lives outside the frozen baseline source.
    campaign.run_native_cc = fixed_transport.run_native_cc
    pilot_scope.apply(campaign, case)
    lock = (control / "recovery.lock").open("a")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    campaign.verify_runtime(case, repo)
    folder = control / ("recovery-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    folder.mkdir()
    state_path = control / "campaign_state.json"
    prior = json.loads(state_path.read_text()) if state_path.exists() else None
    if prior:
        raise ValueError("This cell already has a campaign record; preserve and inspect it")
    transcripts, session, worker = [], None, None
    guard = Path(__file__).with_name("cell_read_guard.py").resolve()
    guard_hash = digest(guard)
    python = str(repo / ".venv-libero/bin/python3")
    settings = campaign.native_settings(case, repo, args.case)
    provider = verify_local_provider(case, settings)
    hook = shlex.join([python, str(guard),
                       "--case", str(args.case), "--audit", str(folder / "read-guard-audit.jsonl")])
    settings["hooks"]["PreToolUse"].append({
        "matcher": "Read|Glob|Grep|Write|Edit|MultiEdit|Bash",
        "hooks": [{"type": "command", "command": hook, "timeout": 60}]})
    settings["hooks"]["PreToolUse"].append({
        "matcher": "Bash", "hooks": [{"type": "command", "timeout": 60,
        "command": shlex.join([python,
                               str(Path(__file__).with_name("infra_guard.py")),
                               "--case", str(args.case)])}]})
    assignment_guard = Path(__file__).with_name("pilot_assignment_guard.py").resolve()
    settings["hooks"]["PreToolUse"].append({
        "matcher": "Agent|Task|Bash", "hooks": [{"type": "command", "timeout": 60,
        "command": shlex.join(pilot_assignment_guard.hook_command(
            python, str(assignment_guard), str(args.case),
            str(control / "worker-prompt.md"),
            str(folder / "assignment-state.json"),
            str(folder / "assignment-audit.jsonl")))}]})
    config = Path(case["claude_config_dir"])
    campaign.atomic_json(config / "settings.json", settings)
    env = bind_child_env(campaign.native_environment(case, repo, args.case, config), case)
    state = {"cell": case["id"], "condition": case["condition"], "started_at": now(),
             "status": "preflight", "blocker": None, "recovery_dir": str(folder),
             "mode": "fresh_cell", "provider": provider,
             "guard_sha256": guard_hash, "driver_sha256": digest(Path(__file__)),
             "assignment_guard_sha256": digest(assignment_guard),
             "prior_state": None}
    def save():
        campaign.atomic_json(state_path, state)
        campaign.atomic_json(folder / "state.json", state)
    save()
    try:
        state["perception"] = campaign.perception_ready(case)
        prompt_path = control / "worker-prompt.md"
        state["probe"] = campaign.probe(case, control, env, settings)
        if (state["probe"]["context_windows"] != [case["context_tokens"]]
                or state["probe"]["configured"]["max_output_tokens"] != case["max_output_tokens"]):
            raise ValueError("Native capacity probe differs from frozen case")
        prompt_path, coordinator_path = campaign.write_prompts(case, repo, control)
        state["init"] = campaign.protocol(case, repo, env, folder, "init", "init")
        if state["init"]["exit_code"]:
            raise ValueError("Fresh initialization failed")
        ledger_dir = repo / "outputs/libero_fix_loop" / case["suite"] / case["task"]
        source = repo / pilot_import.SOURCE_REL
        ledger = pilot_import.open_state(ledger_dir)
        record = pilot_import.import_diagnostic(ledger, source)
        summary = dict(record) if isinstance(record, dict) else {"record": record}
        summary.update(ledger_dir=str(ledger_dir), source=str(source))
        state["diagnostic_import"] = summary
        save()
        for turn in range(8):
            if session:
                prompt = continuation(worker)
            else:
                prompt = (control / "coordinator-prompt.md").read_text() + "\nIf check reports seeds_pending_repair with original attempts remaining, use SendMessage to resume the SAME worker to complete the existing protocol. Never reset the ledger or create a replacement worker.\n"
            (folder / f"turn-{turn:02d}-prompt.md").write_text(prompt)
            transcript = folder / f"turn-{turn:02d}.stdout.jsonl"
            command = campaign.native_command(case, prompt, settings,
                                             agents=campaign.worker_agent(case, prompt_path))
            if session:
                command.extend(["--resume", session])
            state.update(status="solver_running", turn=turn)
            save()
            code = campaign.run_native_cc(command, cwd=repo, env=env, stdout_path=transcript,
                                          stderr_path=folder / f"turn-{turn:02d}.stderr.log",
                                          timeout=case["campaign_timeout"],
                                          on_start=lambda p: monitor(p, case, folder / "infra-stop.json"))
            transcripts.append(transcript)
            state["coordinator_exit_code"] = code
            if fault(case):
                raise ValueError("Infrastructure failure recorded; stopped without resetting attempts")
            if code:
                raise ValueError(f"Native coordinator exited {code}")
            session, worker = identity(transcripts, prompt_path.read_text())
            campaign.atomic_json(folder / "lineage.json", audit_assignment(transcripts, prompt_path.read_text()))
            campaign.verify_runtime(case, repo)
            if digest(guard) != guard_hash:
                raise ValueError("Read guard changed during the run")
            check = campaign.protocol(case, repo, env, folder, f"check-{turn:02d}", "check")
            state["check"] = check
            save()
            if check["exit_code"] == 0:
                break
            if not session:
                session, worker = identity(transcripts, prompt_path.read_text())
        else:
            raise ValueError("Stage1 still incomplete after bounded same-worker continuation")
        final_args = ["finalize"]
        for transcript in transcripts:
            final_args.extend(["--transcript", str(transcript)])
        state["finalize"] = campaign.protocol(case, repo, env, folder, "finalize", *final_args)
        if state["finalize"]["exit_code"]:
            raise ValueError("Finalization failed; inspect recorded outer result")
        state.update(solver_transcripts=[str(p) for p in transcripts])
        state["heldout"] = {"performed": False, "evaluate_invoked": False,
                            "reason": "development pilot only"}
        state["status"] = "pilot_complete"
    except Exception as exc:
        state.update(status="recovery_blocked", blocker=f"{type(exc).__name__}: {exc}")
    state["finished_at"] = now()
    save()
    print(json.dumps({k: state[k] for k in ("cell", "status", "blocker", "recovery_dir")}), flush=True)
    return int(state["status"] != "pilot_complete")


if __name__ == "__main__":
    raise SystemExit(main())
