"""Run one fresh foundation C cell: import its pinned current-study diagnostic exactly once, then native development51-65, freeze and heldout1-50. Native guards, compaction, ownership and accounting remain unchanged from the verified driver.

p4 copy (CC_TASK_2, 2026-10-17) of the p3 search-control support driver; the p3
original stays frozen. Only `require_development_possible` changed: it stops on
the protocol's structured `decision_type: terminal` / `terminal_reasons` (not
just exhausted seeds) and on STALL_TURNS identical post-turn checks.

CC_TASK_5 (2026-10-19): the held-out watchdog is derived from the seeds still to
run (`full_deadlines.heldout_deadline`) unless `--deadline-heldout` overrides it,
and `--heldout-supplement` finishes a preserved, interrupted sweep on its frozen
bundle (`heldout_supplement.py`); development and freeze are unchanged."""
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
import runtime_limits
from infra_guard import bind_child_env, fault, monitor
from lineage import audit_assignment, verify_received_assignment
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
    continuing from them is an operator decision. The one supported
    continuation is a held-out block: `--heldout-supplement` runs only the
    seeds the sweep lacks, on the same frozen bundle.
    """

    def __init__(self, phase, reason, *, detail=None):
        super().__init__(f"{phase}: {reason}")
        self.payload = {"phase": phase, "reason": reason, "state_preserved": True,
                        "automatic_resume": False, "recovery": "manual",
                        "detail": detail}


def now():
    return datetime.now(timezone.utc).isoformat()


#: CC_TASK_2 (p4): this many consecutive post-turn checks with an identical
#: fingerprint (no new trial, rejection, blocker or selection, and the same
#: errors) end development instead of starting another coordinator round. The
#: p4 round's bowldrawer_p4_r1 restarted an idle coordinator 47 times.
STALL_TURNS = 3


def check_fingerprint(result):
    """What a coordinator round can change, from the check's structured fields only."""
    progress = result.get("progress") or {}
    gate = result.get("coverage_gate") or {}
    return json.dumps({"replays": progress.get("replay_invocations"),
                       "rejected": len(progress.get("rejected_revisions") or []),
                       "blockers": len(progress.get("screening_blockers") or []),
                       "selected": (result.get("selected") or {}).get("bundle_sha256"),
                       "reason": (result.get("selected") or {}).get("reason"),
                       "covered": gate.get("covered_seeds"),
                       "errors": sorted(result.get("errors") or [])}, sort_keys=True)


def require_development_possible(check, history=None):
    """Stop as soon as the cell can no longer reach `ready`, or stops moving.

    Read the ledger's structured fields, never model prose or an error string:
    `decision_type == "terminal"` with its `terminal_reasons` (exhausted seeds
    without graded evidence, a coverage gate no candidate can reach, ...), and
    the legacy `seeds_exhausted_without_graded_evidence` list for a protocol
    that predates `terminal_reasons`. The same check runs before the first
    worker and after every completed turn; `history` (post-turn checks only)
    adds the no-progress stop.
    """
    result = check.get("result")
    progress = result.get("progress") if isinstance(result, dict) else None
    exhausted = (progress.get("seeds_exhausted_without_graded_evidence")
                 if isinstance(progress, dict) else None)
    if not isinstance(exhausted, list) or any(type(seed) is not int for seed in exhausted):
        raise Blocked("development_check", "protocol check has no valid evidence-accounting result",
                      detail={"reason_code": "invalid_protocol_check", "check": check})
    decision = result.get("decision_type")
    reasons = result.get("terminal_reasons")
    if decision == "terminal":
        if not reasons:
            # A protocol without `terminal_reasons` only knows this one terminal state.
            reasons = ([{"code": "seeds_exhausted_without_graded_evidence", "seeds": exhausted}]
                       if exhausted else [])
        if not reasons or any(not isinstance(r, dict) or not r.get("code") for r in reasons):
            raise Blocked("development_check", "protocol check is terminal without a structured reason",
                          detail={"reason_code": "invalid_protocol_check", "check": check})
        raise Blocked("development", "the protocol check reports a state from which the cell can no "
                      "longer reach ready; no further coordinator round is started",
                      detail={"reason_code": reasons[0]["code"], "terminal_reasons": reasons,
                              **({"seeds": exhausted} if exhausted else {}), "check": result})
    if exhausted:
        raise Blocked("development", "development seeds exhausted their attempts without graded evidence",
                      detail={"reason_code": "seeds_exhausted_without_graded_evidence",
                              "seeds": exhausted, "check": result})
    if history is not None and not result.get("ready"):
        history.append(check_fingerprint(result))
        if len(history) >= STALL_TURNS and len(set(history[-STALL_TURNS:])) == 1:
            raise Blocked("development", f"{STALL_TURNS} consecutive coordinator rounds changed nothing "
                          "the protocol check can see; no further round is started",
                          detail={"reason_code": "no_progress", "rounds": STALL_TURNS, "check": result})


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
Keep all spent retries spent and the same three-retry cap per seed. Select a tested bundle, complete required reports, and run check before
returning."
Do not create another worker, supply a task strategy, reset budgets or access
another cell or held-out data. Development seeds 51-65 are your whole boundary;
the frozen held-out evaluation on seeds 1-50 is run afterwards by the outer
driver, from the bundle you select, and no held-out outcome is returned to you.
Approximately {int(remaining_seconds // 60)} minutes of wall-clock development time remain in this
job. That is an infrastructure watchdog, not a revision or action limit: it
neither refunds nor adds a retry.
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


def judge_heldout_seeds(case, repo, env, folder, timeout=3 * 3600):
    """Run `gate_study_analysis.judge_heldout` in its own process group."""
    command = [str(repo / ".venv-libero/bin/python3"), "-c",
               "import json, sys; sys.path.insert(0, sys.argv[1]); import gate_study_analysis as a; "
               "print(json.dumps({'judged': a.judge_heldout(sys.argv[2])}))",
               str(repo / "scripts/libero"), str(Path(case["control"]) / "case.json")]
    stdout_path, stderr_path = folder / "heldout_judge.stdout.json", folder / "heldout_judge.stderr.log"
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
    return {"performed": True, "exit_code": code, "timed_out": timed_out, "result": payload,
            "elapsed_seconds": round(time.monotonic() - started, 1),
            "stderr_tail": stderr_path.read_text()[-2000:],
            "output": str(Path(case["control"]) / "heldout/judge")}


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


def heldout_pending(evaluation):
    """Seeds of 1-50 the held-out ledger has no row for yet (all 50 before a sweep)."""
    ledger = evaluation / "heldout_state.json"
    rows = json.loads(ledger.read_text())["seeds"] if ledger.is_file() else {}
    return [seed for seed in HELDOUT_SEEDS if str(seed) not in rows]


def heldout_deadline(args, case, seeds):
    """`--deadline-heldout` if given, else the bound of the seeds about to run."""
    if args.deadline_heldout is not None:
        return args.deadline_heldout
    return full_deadlines.heldout_deadline(len(seeds), case["trial_timeout"])


def run_heldout_supplement(args, case, repo, control, campaign):
    """Finish a preserved, interrupted held-out sweep: only its missing seeds.

    Kept rows stay; seeds whose row is `infrastructure_error` and seeds with no
    row run again from the frozen copies, through the same evaluator entry and
    deadline enforcement as the original sweep. The manifest and the ledger
    identity are re-verified, never rewritten. Ends `full_complete` with all 50
    rows accounted and the supplementary seeds marked in `heldout_result.json`,
    or blocked at phase `heldout` again with everything preserved.
    """
    import heldout_supplement as supplement
    state_path = control / "campaign_state.json"
    state = json.loads(state_path.read_text()) if state_path.is_file() else None
    problems = supplement.eligibility(state)
    if problems:
        raise ValueError(f"cell cannot be supplemented: {problems}")
    task_dir = repo / "outputs/libero_fix_loop" / case["suite"] / case["task"]
    evaluation = control / "heldout"
    started = time.monotonic()
    entry = None

    def save():
        campaign.atomic_json(state_path, state)

    try:
        manifest = heldout_manifest(case, repo, task_dir, evaluation)
        if digest(evaluation / "heldout_manifest.json") != state["heldout_manifest"]["sha256"]:
            raise Blocked("heldout", "the held-out manifest changed since the sweep was frozen",
                          detail={"recorded": state["heldout_manifest"]["sha256"]})
        planned = supplement.plan(evaluation, HELDOUT_SEEDS)
        if planned["problems"] or not planned["seeds_to_run"]:
            raise ValueError(f"nothing to supplement or an unreadable ledger: {planned}")
        attempt = supplement.new_attempt(evaluation)
        env = bind_child_env(campaign.native_environment(
            case, repo, args.case, Path(case["claude_config_dir"])), case)
        record = supplement.prepare(evaluation, planned, attempt, state_path)
        deadline = heldout_deadline(args, case, planned["seeds_to_run"])
        entry = {"attempt": attempt.name, "record": str(attempt / "supplement.json"),
                 "seeds_to_run": record["seeds_to_run"],
                 "rerun_infrastructure_error": record["rerun_infrastructure_error"],
                 "missing": record["missing"], "status": "running", "started_at": now(),
                 "deadline_seconds": deadline, "driver_sha256": digest(Path(__file__)),
                 "prior": {k: state.get(k) for k in ("status", "blocker", "blocked", "heldout",
                                                     "finished_at", "total_seconds")}}
        state.setdefault("heldout_supplements", []).append(entry)
        state.update(status=supplement.RUNNING, blocker=None, blocked=None)
        save()
        heldout_args = ["--case", str(args.case)]
        if (evaluation / "heldout_state.json").is_file():
            heldout_args.append("--resume")  # Kept rows are never re-run.
        step = run_heldout(campaign, repo, env, attempt, heldout_args, deadline)
        entry.update(exit_code=step["exit_code"], timed_out=step["timed_out"],
                     elapsed_seconds=step["elapsed_seconds"])
        heldout = {"performed": True, "evaluate_invoked": True, "exit_code": step["exit_code"],
                   "timed_out": step["timed_out"],
                   "result_path": str(evaluation / "heldout_result.json"),
                   "elapsed_seconds": step["elapsed_seconds"], "deadline_seconds": deadline,
                   "supplement_attempt": attempt.name}
        state["heldout"] = heldout
        if step["timed_out"]:
            raise Blocked("heldout", "the supplementary held-out sweep exceeded its enforced "
                          "wall-clock deadline and its process group was stopped; every per-seed "
                          "row already recorded is preserved", detail=heldout)
        if step["exit_code"]:
            raise Blocked("heldout", f"the supplementary held-out sweep exited {step['exit_code']}",
                          detail={"stderr_tail": step.get("stderr_tail"), **heldout})
        heldout["accounting"] = check_heldout_rows(manifest, step["result"])
        entry["status"] = "complete"
        supplement.finish(attempt, status="complete", exit_code=step["exit_code"],
                          elapsed_seconds=step["elapsed_seconds"], deadline_seconds=deadline)
        report = supplement.annotate(evaluation, state["heldout_supplements"])
        heldout["supplementary_seeds"] = report["supplementary_seeds"]
        state["heldout_manifest"] = {"path": str(evaluation / "heldout_manifest.json"),
                                     "row_count": manifest["row_count"], "seeds": manifest["seeds"],
                                     "selected_bundle": manifest["selected_bundle"],
                                     "sha256": digest(evaluation / "heldout_manifest.json")}
        if "judge" in case:
            state["heldout_judge"] = judge_heldout_seeds(case, repo, env, attempt)
        state["status"] = TERMINAL
    except Blocked as exc:
        if entry is None:
            raise  # Nothing changed yet: the cell keeps its original record.
        state.update(status="blocked", blocked=exc.payload,
                     blocker=f"{exc.payload['phase']}: {exc.payload['reason']}")
    except Exception as exc:
        if entry is None:
            raise
        state.update(status="blocked",
                     blocked={"phase": supplement.RUNNING,
                              "reason": f"{type(exc).__name__}: {exc}", "state_preserved": True,
                              "automatic_resume": False, "recovery": "manual", "detail": None},
                     blocker=f"{type(exc).__name__}: {exc}")
    if entry is not None and entry["status"] != "complete":
        entry["status"] = "blocked"
        supplement.finish(Path(entry["record"]).parent, status="blocked", blocked=state["blocked"])
    state["finished_at"] = now()
    state["supplement_seconds"] = round(time.monotonic() - started, 1)
    save()
    print(json.dumps({k: state.get(k) for k in ("cell", "status", "blocker", "blocked")}), flush=True)
    return int(state["status"] != TERMINAL)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", type=Path, required=True)
    parser.add_argument("--deadline-development", type=int, default=full_deadlines.DEVELOPMENT)
    parser.add_argument("--deadline-heldout", type=int, default=None,
                        help="override; default: full_deadlines.heldout_deadline over the seeds to run")
    parser.add_argument("--startup-session", help="resume only the prelaunch coordinator with no admitted primary/trials")
    parser.add_argument("--native-smoke", action="store_true", help="checkpoint same native worker after one seed51 startup attempt")
    parser.add_argument("--heldout-supplement", action="store_true",
                        help="finish a preserved interrupted held-out sweep: run only its missing seeds")
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
    if args.heldout_supplement:
        return run_heldout_supplement(args, case, repo, control, campaign)
    folder = control / ("campaign-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    folder.mkdir()
    state_path = control / ("native_smoke_state.json" if args.native_smoke else "campaign_state.json")
    if state_path.exists():
        raise ValueError("This cell already has a campaign record; preserve and inspect it")
    prior_path = control / "native_smoke_state.json"
    prior = json.loads(prior_path.read_text()) if not args.native_smoke and prior_path.exists() else None
    if prior and prior.get("status") != "native_smoke_checkpoint":
        raise ValueError("native smoke is incomplete; preserve it for outer recovery")
    transcripts = [Path(p) for p in prior["solver_transcripts"]] if prior else []
    session, worker = (prior["native_session"], prior["native_worker"]) if prior else (None, None)
    guard = Path(__file__).with_name("cell_read_guard.py").resolve()
    guard_hash = digest(guard)
    python = str(repo / ".venv-libero/bin/python3")
    settings = runtime_limits.install(campaign.native_settings(case, repo, args.case), case)
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
            python, str(Path(__file__).with_name("canonical_assignment.py").resolve()), str(args.case),
            str(control / "worker-prompt.md"),
            str(control / "native/assignment-state.json"),
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
                           "heldout_deadline_seconds": (args.deadline_heldout if args.deadline_heldout
                                                        is not None else full_deadlines.HELDOUT)},
             "guard_sha256": guard_hash, "driver_sha256": digest(Path(__file__)),
             "assignment_guard_sha256": digest(assignment_guard),
             "canonical_assignment_sha256": digest(Path(__file__).with_name("canonical_assignment.py")),
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
        state["init"] = campaign.protocol(case, repo, env, folder, "init", "status" if (prior or args.startup_session) else "init")
        if state["init"]["exit_code"]:
            raise Blocked("init", "fresh initialization failed", detail=state["init"])
        if case.get("diagnostic_import"):
            from foundation_import import import_diagnostic
            state["diagnostic_import"] = import_diagnostic(case, task_dir)
        else:
            state["diagnostic_import"] = {"imported": False, "note": "no preflight retry imported; three retries per seed"}
        save()

        state["pre_development_check"] = campaign.protocol(
            case, repo, env, folder, "check-before-solver", "check")
        save()
        require_development_possible(state["pre_development_check"])

        # --- development: wall-clock watchdog, no turn ceiling -----------------
        deadline = started + args.deadline_development
        turn = 0
        stall_history = []
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise Blocked("development",
                              "the development wall-clock deadline expired before the "
                              "protocol check passed; the ledger and every spent "
                              "attempt are intact",
                              detail={"turns": turn,
                                      "deadline_seconds": args.deadline_development,
                                      "last_check": state.get("check")})
            if session:
                prompt = continuation(worker, remaining)
                if prior:
                    prompt += "\nThis SAME worker authored the current cell seed51 native smoke. Reuse its exact initial bundle and completed snapshot. Alias a completed identical smoke as initial51 if eligible; do not re-run it, refund attempts or reinitialize. Continue the remaining original development work and reports.\n"
            else:
                prompt = coordinator_path.read_text() + (
                    "\nIf check reports seeds_pending_repair with original attempts "
                    "remaining, use SendMessage to resume the SAME worker to complete the "
                    "existing protocol. Never reset the ledger or create a replacement "
                    "worker.\n")
            if args.native_smoke:
                prompt = ("STARTUP WORK STAGE: Read the worker assignment and dispatch one background general-purpose primary Agent with prompt set to the literal ASPIRE_FROZEN_PRIMARY_ASSIGNMENT; the native hook supplies the complete frozen file verbatim before the worker starts. Immediately SendMessage to that SAME worker: 'For this first invocation, execute only the public snapshot, fresh task-specific initial policy/world generation, and ONE recorded smoke on seed51. Pause and return a checkpoint after that attempt, before the other development seeds or repairs. This is a startup stage boundary, not an action cap or extra budget. Original p2 runtime perception/state/prediction/decision and p3 search-control requirements apply. Do not select/finalize or write full findings yet.' After its completion notification, report the actual checkpoint and stop. The next invocation resumes the SAME worker and remaining original work. No extra strategy, geometry or old code may be supplied.\n" + prompt)
            (folder / f"turn-{turn:02d}-prompt.md").write_text(prompt)
            transcript = folder / f"turn-{turn:02d}.stdout.jsonl"
            command = campaign.native_command(case, prompt, settings,
                                              agents=campaign.worker_agent(case, prompt_path))
            if session or args.startup_session:
                command.extend(["--resume", session or args.startup_session])
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
            received = verify_received_assignment(config, session, worker, prompt_path)
            campaign.atomic_json(folder / "actual-worker-prompt-receipt.json", received)
            state["actual_worker_prompt_receipt"] = received
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
            if args.native_smoke:
                ledger = json.loads((task_dir / "development_state.json").read_text())
                attempts = [r for r in ledger["trials"] if r.get("spends_retry")]
                if len(attempts) != 1 or attempts[0]["seed"] != 51:
                    raise Blocked("native_smoke", "startup must preserve exactly one charged seed51 attempt", detail=attempts)
                state.update(status="native_smoke_checkpoint", native_session=session, native_worker=worker,
                             solver_transcripts=[str(p) for p in transcripts], smoke_attempt=attempts[0],
                             elapsed_seconds=round(time.monotonic() - started, 1))
                save()
                return 0
            require_development_possible(check, stall_history)
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
        # The sum of the per-seed bounds of the seeds still to run: a legal slow
        # sweep (every seed inside its trial timeout) is never cut short.
        heldout_seconds = heldout_deadline(args, case, heldout_pending(evaluation))
        state["status"] = "heldout_running"
        state["deadlines"]["heldout_deadline_seconds"] = heldout_seconds
        save()
        # Enforced here, not checked afterwards: the evaluator has no timeout of
        # its own, so a stall would otherwise never end.
        step = run_heldout(campaign, repo, env, folder, heldout_args, heldout_seconds)
        state["heldout"] = {"performed": True, "evaluate_invoked": True,
                            "exit_code": step["exit_code"], "timed_out": step["timed_out"],
                            "result_path": str(evaluation / "heldout_result.json"),
                            "elapsed_seconds": step["elapsed_seconds"],
                            "deadline_seconds": heldout_seconds}
        save()
        if step["timed_out"]:
            raise Blocked("heldout", "the held-out sweep exceeded its enforced wall-clock "
                          "deadline and its process group was stopped; every per-seed row "
                          "already recorded is preserved", detail=state["heldout"])
        if step["exit_code"]:
            raise Blocked("heldout", f"the held-out sweep exited {step['exit_code']}",
                          detail={"stderr_tail": step.get("stderr_tail")})
        state["heldout"]["accounting"] = check_heldout_rows(manifest, step["result"])
        # Post hoc, outer-only: the same VLM judge over every graded held-out
        # trial's keyframes, so every arm gets a judge-vs-environment 2x2 on the
        # frozen seeds. Never a grade; a judge failure is recorded, not fatal.
        # A case with no judge configured (the prediction-contract study) skips it.
        if "judge" in case:
            state["status"] = "heldout_judging"
            save()
            state["heldout_judge"] = judge_heldout_seeds(case, repo, env, folder)
        else:
            state["heldout_judge"] = {"performed": False, "reason": "case configures no judge"}
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
