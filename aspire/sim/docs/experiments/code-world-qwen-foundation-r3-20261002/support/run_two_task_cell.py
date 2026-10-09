"""Run one fresh foundation C cell: import its pinned current-study diagnostic exactly once, then native development51-65, freeze and heldout1-50. Native guards, compaction, ownership and accounting remain unchanged from the verified driver."""
import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import time
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
import full_deadlines
import two_task_scope as scope
import output_ownership
from infra_guard import bind_child_env, fault, monitor
from lineage import audit_assignment
import native_cc_stream as fixed_transport
import pilot_assignment_guard


STUDY_MODEL = "qwen3.8-flash-next"
LOOPBACK = {"127.0.0.1", "localhost", "::1", "[::1]"}
HELDOUT_SEEDS = tuple(scope.HELDOUT_SEEDS)
TERMINAL = scope.TERMINAL

#: The only statuses `native_world_heldout` writes for an evaluated seed.
HELDOUT_STATUSES = ("success", "failure", "crash", "infrastructure_error")

#: Grace between SIGTERM and SIGKILL for a held-out sweep that overran. It must
#: exceed `run_replay`'s own 15s TERM->KILL cleanup of the detached replay
#: session, or the evaluator dies mid-cleanup and orphans that child.
GROUP_STOP_GRACE = 45


class Blocked(RuntimeError):
    """A structured stop: the phase, the reason, and what survives it.

    There is deliberately no `resumable` flag. The driver refuses to run against
    an existing `campaign_state.json`, so claiming a blocked run can be resumed
    would be a misreport. What every blocker does guarantee is stated instead:
    the ledger and all recorded per-seed evidence are preserved untouched, and
    continuing from them is an operator decision.
    """

    def __init__(self, phase, reason, *, detail=None):
        super().__init__(f"{phase}: {reason}")
        self.payload = {"phase": phase, "reason": reason, "state_preserved": True,
                        "automatic_resume": False, "recovery": "manual",
                        "detail": detail}


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def identity(transcripts, assignment):
    audit = audit_assignment(transcripts, assignment)
    if not audit["ok"] or len(audit["sessions"]) != 1:
        raise Blocked("solver_lineage", "ambiguous native worker lineage",
                      detail=audit)
    return audit["sessions"][0]["session_id"], audit["primary_worker"]


def model_fields(case):
    return {key: value for key, value in case.items()
            if isinstance(value, str) and "model" in key and key != "model_provider"}


def verify_local_provider(case, settings):
    """Fail closed on the provider contract; never fall back to a provider."""
    if case.get("model_provider") != "local-vllm":
        raise ValueError(f"This study requires model_provider=local-vllm, got {case.get('model_provider')!r}")
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
    if len(models) != 3 or set(models.values()) != {STUDY_MODEL}:
        raise ValueError(f"Case model fields are not three x {STUDY_MODEL}: {json.dumps(models, sort_keys=True)}")
    return {"model_provider": case["model_provider"], "endpoint": endpoint,
            "api_key_helper": False, "models": models}


def verify_capacity(case, settings, probe):
    """Frozen v4's capacity rule, applied at the driver.

    `modelUsage.maxOutputTokens` reports the alias default for a custom local
    model, so only the served context window can be compared directly; the
    request setting is validated against the settings this driver installed.
    """
    problems = []
    if probe["context_windows"] != [case["context_tokens"]]:
        problems.append(f"served context windows {probe['context_windows']} "
                        f"!= configured {case['context_tokens']}")
    configured = settings.get("env", {}).get("CLAUDE_CODE_MAX_OUTPUT_TOKENS")
    if case.get("model_provider") == "local-vllm":
        if configured != str(case["max_output_tokens"]):
            problems.append(f"request setting CLAUDE_CODE_MAX_OUTPUT_TOKENS={configured!r} "
                            f"!= configured {case['max_output_tokens']}")
    elif probe["max_output_tokens"] != [case["max_output_tokens"]]:
        problems.append(f"reported max output {probe['max_output_tokens']} "
                        f"!= configured {case['max_output_tokens']}")
    if problems:
        raise Blocked("capacity_probe", "native capacity differs from the frozen case",
                      detail={"problems": problems, "probe": probe,
                              "configured_request_max_output": configured})
    return {"context_windows": probe["context_windows"],
            "reported_max_output_tokens": probe["max_output_tokens"],
            "configured_request_max_output": configured,
            "checked_request_setting": case.get("model_provider") == "local-vllm"}


def continuation(worker, remaining_seconds):
    """Resume the SAME worker. No budget is granted, reset or refunded here."""
    return f"""Continue this same cell under its original protocol. Use SendMessage
to resume the existing worker {worker}, with this protocol-only message:
"The protocol check is not ready. Read your own ledger status and complete the
pending development work on seeds 51-65 using the remaining original attempts.
Keep all existing attempts charged and the same three-total-attempt cap per
seed. Select a tested bundle, complete required reports, and run check before
returning."
Do not create another worker, supply a task strategy, reset budgets or access
another cell or held-out data. Development seeds 51-65 are your whole boundary;
the frozen held-out evaluation on seeds 1-50 is run afterwards by the outer
driver, from the bundle you select, and no held-out outcome is returned to you.
Approximately {int(remaining_seconds // 60)} minutes of wall-clock development time remain in this
job. That is an infrastructure watchdog, not a revision or action limit: it
neither refunds nor adds a charged attempt.
After its native completion notification, run the original protocol check and
report its actual result.
"""


def heldout_manifest(case, repo, task_dir, evaluation):
    """The immutable 50-row manifest, written once from the frozen selection.

    Refuses an incomplete, duplicated or missing seed set, and refuses to
    rewrite an existing manifest whose identity differs: the sweep that already
    started is the one that must finish.
    """
    stage1_path = task_dir / "stage1_result.json"
    if not stage1_path.is_file():
        raise Blocked("freeze", "development finalization wrote no stage1_result.json",
                      detail={"expected": str(stage1_path)})
    stage1 = json.loads(stage1_path.read_text())
    if not stage1.get("stage1_complete"):
        raise Blocked("freeze", "stage 1 is not complete; the development ledger is not finalized",
                      detail={"stage1_complete": stage1.get("stage1_complete")})
    bundle = stage1.get("selected_bundle")
    if not bundle:
        raise Blocked("freeze", "no selected bundle to freeze",
                      detail={"stage1_keys": sorted(stage1)})
    seeds = list(HELDOUT_SEEDS)
    # The frozen sweep is seeds 1-50 exactly. "50 unique sorted integers" would
    # accept a shifted or resampled set, which is a different experiment.
    if seeds != list(range(1, 51)):
        raise Blocked("freeze", "held-out seed set is not exactly seeds 1-50",
                      detail={"seeds": seeds})
    rows = [{"row": index, "seed": seed, "status": "pending"}
            for index, seed in enumerate(seeds, start=1)]
    manifest = {"schema_version": 1, "cell": case["id"], "condition": case["condition"],
                "suite": case["suite"], "task": case["task"],
                "seeds": seeds, "rows": rows, "row_count": len(rows),
                "selected_bundle": bundle,
                "selected_bundle_sha256": stage1.get("selection", {}).get("bundle_sha256"),
                "tested_bundle_digests": sorted(stage1.get("tested_bundles", {})),
                "config_sha256": digest(repo / case["env_config"]),
                "trial_timeout": case["trial_timeout"], "max_steps": case["max_steps"],
                "frozen_at": now(), "immutable": True,
                "note": ("Outer-only evaluation. Every seed is attempted, every result "
                         "stays visible, and no held-out outcome is returned to a solver.")}
    path = evaluation / "heldout_manifest.json"
    if path.is_file():
        existing = json.loads(path.read_text())
        comparable = ("cell", "condition", "suite", "task", "seeds", "row_count",
                      "selected_bundle", "selected_bundle_sha256", "config_sha256",
                      "trial_timeout", "max_steps")
        differing = {k: (existing.get(k), manifest[k]) for k in comparable
                     if existing.get(k) != manifest[k]}
        if differing:
            raise Blocked("freeze", "an immutable held-out manifest already exists with a "
                          "different identity; inspect it rather than overwriting",
                          detail={"path": str(path), "differing": differing})
        # An existing manifest is trusted for the sweep, so its rows are checked
        # too, not just the scalar identity fields beside them.
        row_problems = manifest_row_problems(existing)
        if row_problems:
            raise Blocked("freeze", "the existing held-out manifest's rows are not the frozen "
                          "1-50 sweep; inspect it rather than evaluating against it",
                          detail={"path": str(path), "problems": row_problems})
        return existing
    evaluation.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def manifest_row_problems(manifest):
    """Ways a manifest's `rows` could stop describing the frozen 1-50 sweep."""
    rows = manifest.get("rows")
    problems = []
    if not isinstance(rows, list):
        return [f"rows is {type(rows).__name__}, not a list"]
    if len(rows) != manifest.get("row_count"):
        problems.append(f"{len(rows)} rows against row_count {manifest.get('row_count')}")
    seeds = [row.get("seed") for row in rows if isinstance(row, dict)]
    if len(seeds) != len(rows):
        problems.append("some rows are not objects")
    if seeds != list(range(1, 51)):
        problems.append(f"row seeds are not exactly 1-50: {seeds}")
    if list(manifest.get("seeds") or []) != seeds:
        problems.append("the `seeds` list and the row seeds disagree")
    numbering = [row.get("row") for row in rows if isinstance(row, dict)]
    if numbering != list(range(1, len(rows) + 1)):
        problems.append("rows are not numbered 1..N in order")
    return problems


def check_heldout_rows(manifest, report):
    """Verify the sweep against the manifest, recomputing what it reports.

    Key coverage alone is not enough: a report can name every seed and still be
    the wrong evaluation. So the frozen identity (cell/condition/suite/task and,
    above all, the selected bundle and its digest) must match the manifest, every
    per-seed status must be one the evaluator actually writes, and `counts`,
    `seeds_evaluated`, `success_rate` and `unusable_seeds` are recomputed from
    the per-seed statuses rather than believed.
    """
    report = report or {}
    per_seed = report.get("per_seed") or {}
    expected = [str(seed) for seed in manifest["seeds"]]
    missing = [seed for seed in expected if seed not in per_seed]
    extra = sorted(set(per_seed) - set(expected))
    problems = {}
    if missing:
        problems["missing_seeds"] = missing
    if extra:
        problems["unexpected_seeds"] = extra
    if len(per_seed) != manifest["row_count"]:
        problems["row_count"] = {"manifest": manifest["row_count"], "reported": len(per_seed)}
    invalid = {seed: status for seed, status in sorted(per_seed.items())
               if status not in HELDOUT_STATUSES}
    if invalid:
        problems["invalid_statuses"] = invalid

    identity = report.get("identity") or {}
    identity_problems = {key: {"manifest": manifest.get(key), "report": identity.get(key)}
                         for key in ("cell", "condition", "suite", "task")
                         if identity.get(key) != manifest.get(key)}
    if identity.get("bundle") != manifest.get("selected_bundle"):
        identity_problems["bundle"] = {"manifest": manifest.get("selected_bundle"),
                                       "report": identity.get("bundle")}
    if identity.get("bundle_sha256") != manifest.get("selected_bundle_sha256"):
        identity_problems["bundle_sha256"] = {"manifest": manifest.get("selected_bundle_sha256"),
                                              "report": identity.get("bundle_sha256")}
    if identity_problems:
        problems["identity"] = identity_problems

    counts = {status: sum(1 for value in per_seed.values() if value == status)
              for status in HELDOUT_STATUSES}
    if report.get("counts") != counts:
        problems["counts"] = {"recomputed": counts, "reported": report.get("counts")}
    if report.get("seeds_evaluated") != len(per_seed):
        problems["seeds_evaluated"] = {"recomputed": len(per_seed),
                                       "reported": report.get("seeds_evaluated")}
    rate = counts["success"] / manifest["row_count"] if manifest["row_count"] else 0.0
    if not isinstance(report.get("success_rate"), (int, float)) or \
            abs(report["success_rate"] - rate) > 1e-9:
        problems["success_rate"] = {"recomputed": rate, "reported": report.get("success_rate")}
    unusable = sorted(int(seed) for seed, status in per_seed.items()
                      if status == "infrastructure_error")
    if sorted(report.get("unusable_seeds") or []) != unusable:
        problems["unusable_seeds_report"] = {"recomputed": unusable,
                                             "reported": report.get("unusable_seeds")}
    if unusable:
        problems["unusable_seeds"] = unusable
    if not report.get("all_seeds_accounted"):
        problems["all_seeds_accounted"] = False
    if problems:
        raise Blocked("heldout", "the held-out sweep does not account for the manifest exactly",
                      detail=problems)
    return {"rows_accounted": len(per_seed), "counts": counts, "success_rate": rate,
            "identity_verified": True, "unusable_seeds": unusable}


def stop_group(process):
    """SIGTERM the sweep's own process group, then SIGKILL what survives.

    The sweep is spawned with `start_new_session=True`, so its group contains the
    evaluator and every simulator child it launched and nothing of this driver's.
    """
    for signal_number, grace in ((signal.SIGTERM, GROUP_STOP_GRACE), (signal.SIGKILL, 10)):
        try:
            os.killpg(os.getpgid(process.pid), signal_number)
        except (ProcessLookupError, PermissionError):
            break
        try:
            return process.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            continue
    return process.poll()


def run_heldout(campaign, repo, env, folder, argv, timeout):
    """Run the outer held-out sweep under an actually enforced deadline.

    `campaign.protocol` runs its step through `subprocess.run` with no timeout,
    so a stalled sweep would hang forever and an elapsed-time check *after* it
    returned could never fire. The sweep is therefore spawned here instead, in
    its own process group, and an expiry stops that group. Per-seed rows already
    written to `heldout_state.json` are preserved exactly as they are: this
    function never edits a ledger, refunds an attempt or re-runs a recorded seed.
    The recorded step keeps `campaign.protocol`'s shape so downstream readers and
    `outer_heldout.json` are unchanged.
    """
    # The evaluator is entered through `heldout_stop_on_infra`, which ends the
    # sweep one atomic ledger write after an infrastructure row instead of
    # starting another replay in its own session while this stop is in progress.
    command = [str(repo / ".venv-libero/bin/python3"),
               str(Path(__file__).with_name("heldout_stop_on_infra.py")), *argv]
    stdout_path, stderr_path = folder / "heldout.stdout.json", folder / "heldout.stderr.log"
    started = time.monotonic()
    with stdout_path.open("wb") as out, stderr_path.open("wb") as err:
        process = subprocess.Popen(command, cwd=repo, env=env, stdin=subprocess.DEVNULL,
                                   stdout=out, stderr=err, start_new_session=True)
        try:
            code, timed_out = process.wait(timeout=timeout), False
        except subprocess.TimeoutExpired:
            code, timed_out = stop_group(process), True
    try:
        payload = json.loads(stdout_path.read_text())
    except (json.JSONDecodeError, OSError):
        payload = None
    step = {"step": "heldout", "command": command, "exit_code": code, "result": payload,
            "stderr_tail": stderr_path.read_text()[-4000:], "timed_out": timed_out,
            "timeout_seconds": timeout, "elapsed_seconds": round(time.monotonic() - started, 1),
            "stdout_path": str(stdout_path)}
    campaign.atomic_json(folder / "outer_heldout.json", step)
    return step


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", type=Path, required=True)
    parser.add_argument("--deadline-development", type=int, default=full_deadlines.DEVELOPMENT)
    parser.add_argument("--deadline-heldout", type=int, default=full_deadlines.HELDOUT)
    args = parser.parse_args(argv)
    case = json.loads(args.case.read_text())
    repo, control = Path(case["sim"]), Path(case["control"])
    sys.path.insert(0, str(repo / "scripts/libero"))
    import native_world_campaign as campaign
    # Corrected turn-aware transport lives outside the frozen baseline source.
    campaign.run_native_cc = fixed_transport.run_native_cc
    scope.apply(campaign, case)
    lock = (control / "recovery.lock").open("a")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    campaign.verify_runtime(case, repo)
    folder = control / ("campaign-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    folder.mkdir()
    state_path = control / "campaign_state.json"
    if state_path.exists():
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
    task_dir = repo / "outputs/libero_fix_loop" / case["suite"] / case["task"]
    evaluation = control / "heldout"
    started = time.monotonic()
    state = {"cell": case["id"], "condition": case["condition"], "started_at": now(),
             "status": "preflight", "blocker": None, "blocked": None,
             "campaign_dir": str(folder), "mode": "full_study", "provider": provider,
             "dev_seeds": sorted(int(s) for s in case["dev_seeds"]),
             "heldout_seeds": list(HELDOUT_SEEDS),
             "deadlines": {**full_deadlines.summary(),
                           "development_deadline_seconds": args.deadline_development,
                           "heldout_deadline_seconds": args.deadline_heldout},
             "guard_sha256": guard_hash, "driver_sha256": digest(Path(__file__)),
             "assignment_guard_sha256": digest(assignment_guard),
             "ownership_sha256": digest(Path(__file__).with_name("output_ownership.py")),
             "scope_sha256": digest(Path(__file__).with_name("two_task_scope.py")),
             "turns": []}

    def save():
        campaign.atomic_json(state_path, state)
        campaign.atomic_json(folder / "state.json", state)

    save()
    try:
        # Ownership first: before a model, a service or a simulator exists.
        state["output_ownership"] = output_ownership.verify(case, repo)
        save()
        state["perception"] = campaign.perception_ready(case)
        state["probe"] = campaign.probe(case, control, env, settings)
        state["capacity"] = verify_capacity(case, settings, state["probe"])
        prompt_path, coordinator_path = campaign.write_prompts(case, repo, control)
        state["init"] = campaign.protocol(case, repo, env, folder, "init", "init")
        if state["init"]["exit_code"]:
            raise Blocked("init", "fresh initialization failed", detail=state["init"])
        from foundation_import import import_diagnostic
        state["diagnostic_import"] = import_diagnostic(case, task_dir)
        save()

        # --- development: wall-clock watchdog, no turn ceiling -----------------
        deadline = started + args.deadline_development
        turn = 0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise Blocked("development",
                              "the development wall-clock deadline expired before the "
                              "protocol check passed; the ledger and every charged "
                              "attempt are intact",
                              detail={"turns": turn,
                                      "deadline_seconds": args.deadline_development,
                                      "last_check": state.get("check")})
            if session:
                prompt = continuation(worker, remaining)
            else:
                prompt = coordinator_path.read_text() + (
                    "\nIf check reports seeds_pending_repair with original attempts "
                    "remaining, use SendMessage to resume the SAME worker to complete the "
                    "existing protocol. Never reset the ledger or create a replacement "
                    "worker.\n")
            (folder / f"turn-{turn:02d}-prompt.md").write_text(prompt)
            transcript = folder / f"turn-{turn:02d}.stdout.jsonl"
            command = campaign.native_command(case, prompt, settings,
                                              agents=campaign.worker_agent(case, prompt_path))
            if session:
                command.extend(["--resume", session])
            state.update(status="solver_running", turn=turn)
            save()
            code = campaign.run_native_cc(
                command, cwd=repo, env=env, stdout_path=transcript,
                stderr_path=folder / f"turn-{turn:02d}.stderr.log",
                # The solver turn may not outlive the development phase itself.
                timeout=min(case["campaign_timeout"], max(1, int(deadline - time.monotonic()))),
                on_start=lambda p: monitor(p, case, folder / "infra-stop.json"))
            transcripts.append(transcript)
            state["coordinator_exit_code"] = code
            blocker = fault(case)
            if blocker:
                # Stops before any further trial spending and before held-out.
                raise Blocked("infrastructure", "an infrastructure failure is recorded in a "
                              "ledger; stopping without resetting attempts and without "
                              "starting the held-out sweep", detail=blocker)
            if code:
                raise Blocked("solver", f"native coordinator exited {code}",
                              detail={"turn": turn, "transcript": str(transcript)})
            session, worker = identity(transcripts, prompt_path.read_text())
            campaign.atomic_json(folder / "lineage.json",
                                 audit_assignment(transcripts, prompt_path.read_text()))
            campaign.verify_runtime(case, repo)
            if digest(guard) != guard_hash:
                raise Blocked("guard", "the read guard changed during the run")
            output_ownership.verify(case, repo)
            check = campaign.protocol(case, repo, env, folder, f"check-{turn:02d}", "check")
            state["check"] = check
            state["turns"].append({"turn": turn, "exit_code": code,
                                   "check_exit_code": check["exit_code"],
                                   "elapsed_seconds": round(time.monotonic() - started, 1)})
            save()
            if check["exit_code"] == 0:
                break
            turn += 1

        final_args = ["finalize"]
        for transcript in transcripts:
            final_args.extend(["--transcript", str(transcript)])
        state["finalize"] = campaign.protocol(case, repo, env, folder, "finalize", *final_args)
        if state["finalize"]["exit_code"]:
            raise Blocked("finalize", "development finalization failed; inspect the recorded "
                          "outer result", detail=state["finalize"])
        state.update(solver_transcripts=[str(p) for p in transcripts],
                     status="development_complete",
                     development_seconds=round(time.monotonic() - started, 1))
        save()

        # --- freeze ------------------------------------------------------------
        manifest = heldout_manifest(case, repo, task_dir, evaluation)
        state["heldout_manifest"] = {"path": str(evaluation / "heldout_manifest.json"),
                                     "row_count": manifest["row_count"],
                                     "seeds": manifest["seeds"],
                                     "selected_bundle": manifest["selected_bundle"],
                                     "sha256": digest(evaluation / "heldout_manifest.json")}
        state["status"] = "frozen"
        save()

        # --- held-out: outer-only, seeds 1-50 ----------------------------------
        heldout_args = ["--case", str(args.case)]
        if (evaluation / "heldout_state.json").is_file():
            heldout_args.append("--resume")  # Recorded seeds are never re-run.
        state["status"] = "heldout_running"
        save()
        # Enforced here, not checked afterwards: the evaluator has no timeout of
        # its own, so a stall would otherwise never end.
        step = run_heldout(campaign, repo, env, folder, heldout_args, args.deadline_heldout)
        state["heldout"] = {"performed": True, "evaluate_invoked": True,
                            "exit_code": step["exit_code"], "timed_out": step["timed_out"],
                            "result_path": str(evaluation / "heldout_result.json"),
                            "elapsed_seconds": step["elapsed_seconds"],
                            "deadline_seconds": args.deadline_heldout}
        save()
        if step["timed_out"]:
            raise Blocked("heldout", "the held-out sweep exceeded its enforced wall-clock "
                          "deadline and its process group was stopped; every per-seed row "
                          "already recorded is preserved", detail=state["heldout"])
        if step["exit_code"]:
            raise Blocked("heldout", f"the held-out sweep exited {step['exit_code']}",
                          detail={"stderr_tail": step.get("stderr_tail")})
        state["heldout"]["accounting"] = check_heldout_rows(manifest, step["result"])
        state["status"] = TERMINAL
    except Blocked as exc:
        state.update(status="blocked", blocked=exc.payload,
                     blocker=f"{exc.payload['phase']}: {exc.payload['reason']}")
    except Exception as exc:
        state.update(status="blocked",
                     blocked={"phase": state.get("status", "unknown"),
                              "reason": f"{type(exc).__name__}: {exc}",
                              "state_preserved": True, "automatic_resume": False,
                              "recovery": "manual", "detail": None},
                     blocker=f"{type(exc).__name__}: {exc}")
    state["finished_at"] = now()
    state["total_seconds"] = round(time.monotonic() - started, 1)
    save()
    print(json.dumps({k: state[k] for k in
                      ("cell", "status", "blocker", "blocked", "campaign_dir")}), flush=True)
    return int(state["status"] != TERMINAL)


if __name__ == "__main__":
    raise SystemExit(main())
