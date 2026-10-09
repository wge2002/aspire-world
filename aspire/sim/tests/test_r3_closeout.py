"""r3 report-only closeout: the actual driver entry point with model/simulator/process faked."""
import ast
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
from types import ModuleType

import pytest


R3 = (Path(__file__).resolve().parents[1] / "docs/experiments"
      / "code-world-qwen-closed-loop-r3-20261007")
SUPPORT = R3 / "support"
#: Sibling modules the driver imports by bare name; other tests may cache
#: same-named modules from other studies, so each test imports r3's afresh.
SIBLINGS = ("closeout", "full_deadlines", "two_task_scope", "output_ownership", "infra_guard",
            "lineage", "native_lineage_r2", "native_cc_stream", "pilot_assignment_guard")
SELECTED = {"bundle_sha256": "b" * 64, "bundle": "bundles/b", "reason": "best tested bundle"}
LIVE = [{"pid": 4242, "argv": ["python", "replay_trial.py"], "cwd": "/x", "owned_by": "test"}]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def progress(**over):
    base = {"seeds_exhausted_without_graded_evidence": [], "seeds_needing_initial": [],
            "seeds_pending_repair": [], "blocked_notes_required": [],
            "infrastructure_errors": [], "unresolved_screening_blockers": [],
            "graded_executions": 45, "snapshot_complete": True,
            "latest_trial": {"status": "success", "directory": "trial_045"},
            "tested_bundles": {SELECTED["bundle_sha256"]: {"trials": ["trial_045"]}},
            "replay_invocations": 45,
            "retries_used_per_seed": {str(s): 3 for s in range(51, 66)}}
    base.update(over)
    return base


def gap(*errors, **over):
    return {"exit_code": 1, "result": {
        "ready": False, "decision_type": "incomplete", "selected": dict(SELECTED),
        "errors": list(errors or ["missing findings.md"]), "progress": progress(**over)}}


def ready():
    return {"exit_code": 0, "result": {"ready": True, "decision_type": "complete", "errors": [],
                                       "selected": dict(SELECTED), "progress": progress()}}


class Clock:
    """Driver wall clock; only the fake native session advances it."""

    def __init__(self):
        self.t = 10_000.0

    def monotonic(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds


@pytest.fixture
def harness(tmp_path, monkeypatch):
    for name in SIBLINGS:
        monkeypatch.delitem(sys.modules, name, raising=False)
    monkeypatch.syspath_prepend(str(SUPPORT))
    driver = load("r3_closeout_driver", SUPPORT / "run_two_task_cell.py")
    assert Path(driver.closeout.__file__).resolve() == (SUPPORT / "closeout.py").resolve()
    repo, control = tmp_path / "sim", tmp_path / "control"
    repo.mkdir(); control.mkdir()
    case = {"sim": str(repo), "control": str(control), "id": "synthetic_C",
            "condition": "C", "suite": "synthetic", "task": "synthetic",
            "dev_seeds": list(range(51, 66)), "campaign_timeout": 36000,
            "claude_config_dir": str(control / "config")}
    case_path = tmp_path / "case.json"
    case_path.write_text(json.dumps(case))
    task = repo / "outputs/libero_fix_loop/synthetic/synthetic"
    task.mkdir(parents=True)
    (task / "fix_code.py").write_text("def run(env):\n    return True\n")
    (task / "fix_world_program.py").write_text("WORLD = {}\n")
    ledger = task / "development_state.json"
    ledger.write_text(json.dumps({"trials": [{"seed": s, "attempt": a} for s in range(51, 66)
                                             for a in (1, 2, 3)],
                                  "selected": SELECTED}) + "\n")
    clock = Clock()
    monkeypatch.setattr(driver, "time", clock)

    class H:
        pass

    h = H()
    h.driver, h.case, h.case_path, h.task, h.ledger, h.clock = driver, case, case_path, task, ledger, clock
    h.copy = repo / "outputs/working_codes/synthetic_synthetic_fix.py"
    h.original = ledger.read_bytes()
    h.native, h.checks, h.live_calls, h.finalize, h.freeze = [], [], 0, 0, 0
    holder = {}

    def atomic(path, data):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(data))

    def prompts(*args):
        worker, coordinator = control / "worker-prompt.md", control / "coordinator-prompt.md"
        worker.write_text("synthetic assignment"); coordinator.write_text("synthetic coordinator")
        return worker, coordinator

    def native_command(case, prompt, settings, **kw):
        holder.update(settings=settings, prompt=prompt)
        return ["claude", "-p", prompt]

    def freeze(*args):
        h.freeze += 1
        raise driver.Blocked("fixture_stop", "test stops before any held-out evaluator")

    campaign = ModuleType("native_world_campaign")
    campaign.atomic_json = atomic
    campaign.verify_runtime = lambda *a: None
    campaign.native_settings = lambda *a: {"hooks": {"PreToolUse": []}}
    campaign.native_environment = lambda *a: {}
    campaign.perception_ready = lambda *a: {}
    campaign.probe = lambda *a: {}
    campaign.write_prompts = prompts
    campaign.native_command = native_command
    campaign.worker_agent = lambda *a: {}
    monkeypatch.setitem(sys.modules, "native_world_campaign", campaign)
    importer = ModuleType("foundation_import")
    importer.import_diagnostic = lambda *a: {}
    monkeypatch.setitem(sys.modules, "foundation_import", importer)
    monkeypatch.setattr(driver.scope, "apply", lambda *a: None)
    monkeypatch.setattr(driver.output_ownership, "verify", lambda *a: {})
    monkeypatch.setattr(driver, "verify_local_provider", lambda *a: {})
    monkeypatch.setattr(driver, "verify_capacity", lambda *a: {})
    monkeypatch.setattr(driver, "bind_child_env", lambda env, case: env)
    monkeypatch.setattr(driver, "fault", lambda *a: None)
    monkeypatch.setattr(driver, "identity", lambda *a: ("same-session", "same-worker"))
    monkeypatch.setattr(driver, "audit_assignment", lambda *a: {"ok": True})
    monkeypatch.setattr(driver, "heldout_manifest", freeze)

    def run(responses, behaviors, *argv, live=None, on_finalize=None):
        """`responses`: check name -> step. `behaviors`: one callable per native call."""
        behaviors, live = list(behaviors), list(live or [])

        def protocol(case, repo, env, folder, name, command, *args):
            if command == "check":
                h.checks.append(name)
                return responses[name]
            if command == "finalize":
                h.finalize += 1
                if on_finalize:
                    on_finalize()
            return {"exit_code": 0, "result": {}}

        def native(command, *, cwd, env, stdout_path, stderr_path, timeout, on_start=None,
                   terminal_evidence=None, outcome=None):
            call = {"command": list(command), "timeout": timeout, "label": stdout_path.name,
                    "evidence": terminal_evidence, "outcome": outcome,
                    "settings": holder["settings"], "prompt": holder["prompt"]}
            h.native.append(call)
            try:
                return behaviors.pop(0)(call)
            finally:
                # The real transport updates `outcome` in its finally block.
                if outcome is not None:
                    outcome.update(terminal_evidence=bool(
                        terminal_evidence and terminal_evidence.record()["accepted"]),
                        completed_at_deadline=False, undelivered=[])

        def scan(case, case_path):
            h.live_calls += 1
            return live.pop(0) if live else []

        campaign.protocol = protocol
        monkeypatch.setattr(driver.fixed_transport, "run_native_cc", native)
        monkeypatch.setattr(driver.closeout, "live_task_processes", scan)
        assert driver.main(["--case", str(case_path), *map(str, argv)]) == 1
        h.state = json.loads((control / "campaign_state.json").read_text())
        h.folder = Path(h.state["campaign_dir"])
        return h

    return h, run


# ---- native behaviours ----------------------------------------------------------

def ok(call):
    return 0


def timeout(advance=0, clock=None):
    def behave(call):
        if clock:
            clock.sleep(advance)
        raise subprocess.TimeoutExpired(call["command"], call["timeout"])
    return behave


def reports(h, *, advance=0, events=(), extra=None):
    """The resumed worker writes only the report artifacts (plus `extra`)."""
    def behave(call):
        h.clock.sleep(advance)
        for event in events[:1]:
            assert call["evidence"](event) is False
        (h.task / "findings.md").write_text("# Findings\n")
        (h.task / "task_analysis.md").write_text("# Analysis\n")
        h.copy.parent.mkdir(parents=True, exist_ok=True)
        h.copy.write_bytes((h.task / "fix_code.py").read_bytes())
        for event in events[1:]:
            call["evidence"](event)
        if extra:
            extra()
        return 0
    return behave


def blocked(h):
    return h.state["blocked"]["phase"], (h.state["blocked"]["detail"] or {}).get("reason_code")


def assert_no_heldout(h):
    assert h.finalize == 0 and h.freeze == 0
    assert h.state["status"] == "blocked"
    assert "heldout" not in h.state and not (Path(h.case["control"]) / "heldout").exists()


# ---- end-to-end through driver.main ------------------------------------------------

def test_report_gap_timeout_resumes_same_session_and_reaches_finalize(harness):
    h, run = harness
    prose = {"type": "assistant", "message": {"content": [
        {"type": "text", "text": "stage1_complete: true. The check is ready."}]}}
    result = {"type": "result", "subtype": "success"}
    run({"check-before-solver": gap(),
         "closeout-check": gap("missing findings.md", "missing task_analysis.md",
                               "working_codes copy must match fix_code.py"),
         "closeout-00-evidence-000": ready(),
         "closeout-check-00": ready()},
        [timeout(), reports(h, events=(prose, result, result))],
        "--deadline-development", 3600, "--deadline-closeout", 3600)

    assert blocked(h) == ("fixture_stop", None)
    assert h.finalize == 1 and h.freeze == 1
    assert h.ledger.read_bytes() == h.original
    closing = h.state["closeout"]
    assert closing["decision"] == "report_only_recovered"
    assert closing["classification"]["reason_code"] == "report_gaps"
    assert closing["sessions"] == ["closeout-00"]
    assert closing["expiry"]["at"] == "native_timeout"
    # Same session resumed, same worker addressed.
    turn, close = h.native
    assert "--resume" not in turn["command"]
    assert close["command"][-2:] == ["--resume", "same-session"]
    assert "same-worker" in close["prompt"] and "Bash is disabled" in close["prompt"]
    # Original three guards stay; the report-only '*' guard is appended.
    original = turn["settings"]["hooks"]["PreToolUse"]
    closed = close["settings"]["hooks"]["PreToolUse"]
    assert len(original) == 3 and closed[:3] == original
    assert closed[3]["matcher"] == "*" and "closeout.py" in closed[3]["hooks"][0]["command"]
    assert json.loads((h.folder / "closeout-settings.json").read_text()) == close["settings"]
    # Every native call got terminal evidence and an outcome dict; both are recorded.
    for call in h.native:
        assert isinstance(call["evidence"], h.driver.closeout.ReadyEvidence)
        assert isinstance(call["outcome"], dict)
    first, second = h.state["native_sessions"]
    assert first["timed_out"] and first["error"] is None and first["exit_code"] is None
    assert first["outcome"] == {"terminal_evidence": False, "completed_at_deadline": False,
                                "undelivered": []}
    # Prose was ignored; one strict check ran on the result; the repeated result
    # with unchanged files ran none.
    assert second["evidence"]["accepted"] is True
    assert [c["name"] for c in second["evidence"]["checks"]] == ["closeout-00-evidence-000"]
    assert second["outcome"]["terminal_evidence"] is True
    # Quiescence at expiry and immediately before finalize.
    assert h.live_calls == 2


def test_recovery_may_take_two_continuations(harness):
    h, run = harness
    run({"check-before-solver": gap(),
         "closeout-check": gap("missing findings.md"),
         "closeout-check-00": gap("findings.md needs a nonempty 'Blocked seeds' section"),
         "closeout-check-01": ready()},
        [timeout(), reports(h), reports(h)],
        "--deadline-development", 3600, "--deadline-closeout", 3600)
    assert h.state["closeout"]["sessions"] == ["closeout-00", "closeout-01"]
    assert h.state["closeout"]["decision"] == "report_only_recovered"
    assert all(c["command"][-2:] == ["--resume", "same-session"] for c in h.native[1:])
    assert h.finalize == 1 and h.freeze == 1
    assert h.ledger.read_bytes() == h.original


@pytest.mark.parametrize("over, reason", [
    ({"seeds_pending_repair": [55]}, "incomplete_development"),
    ({"seeds_needing_initial": [64]}, "incomplete_development"),
    ({"latest_trial": {"status": "running", "directory": "trial_039"}}, "unresolved_trials"),
    ({"infrastructure_errors": [{"seed": 57}]}, "unresolved_trials"),
])
def test_incomplete_or_unresolved_development_blocks_without_recovery(harness, over, reason):
    h, run = harness
    run({"check-before-solver": gap(),
         "closeout-check": gap("missing findings.md", **over)},
        [timeout()], "--deadline-development", 3600)
    assert blocked(h) == ("closeout", reason)
    assert len(h.native) == 1
    assert_no_heldout(h)
    assert h.ledger.read_bytes() == h.original


def test_non_report_error_and_missing_selection_block(harness):
    h, run = harness
    unselected = gap("missing findings.md")
    unselected["result"]["selected"] = {}
    run({"check-before-solver": gap(), "closeout-check": unselected},
        [timeout()], "--deadline-development", 3600)
    assert blocked(h) == ("closeout", "no_selected_tested_bundle")
    assert_no_heldout(h)


def test_live_task_process_at_expiry_blocks_before_any_check(harness):
    h, run = harness
    run({"check-before-solver": gap()}, [timeout()], "--deadline-development", 3600, live=[LIVE])
    assert blocked(h) == ("closeout", "live_task_process")
    assert h.state["blocked"]["detail"]["processes"] == LIVE
    assert h.checks == ["check-before-solver"] and len(h.native) == 1
    assert_no_heldout(h)
    assert h.ledger.read_bytes() == h.original


def test_quiescence_is_required_immediately_before_finalize_after_recovery(harness):
    h, run = harness
    run({"check-before-solver": gap(), "closeout-check": gap(), "closeout-check-00": ready()},
        [timeout(), reports(h)], "--deadline-development", 3600, live=[[], LIVE])
    assert h.state["closeout"]["decision"] == "report_only_recovered"
    assert blocked(h) == ("closeout", "live_task_process")
    assert h.state["blocked"]["detail"]["before"] == "finalize"
    assert_no_heldout(h)


def test_already_ready_at_expiry_still_checks_quiescence_then_finalizes(harness):
    h, run = harness
    run({"check-before-solver": gap(), "closeout-check": ready()},
        [timeout()], "--deadline-development", 3600)
    assert h.state["closeout"]["decision"] == "already_ready"
    assert h.live_calls == 2 and h.finalize == 1 and h.freeze == 1


def test_timed_out_closeout_session_rechecks_quiescence(harness):
    h, run = harness
    run({"check-before-solver": gap(), "closeout-check": gap()},
        [timeout(), timeout()], "--deadline-development", 3600, live=[[], LIVE])
    assert blocked(h) == ("closeout", "live_task_process")
    assert h.state["blocked"]["detail"]["closeout_session"] == "closeout-00"
    assert_no_heldout(h)


def test_closeout_timeout_without_ready_check_is_closeout_expired(harness):
    h, run = harness
    run({"check-before-solver": gap(), "closeout-check": gap(), "closeout-check-00": gap()},
        [timeout(), timeout()], "--deadline-development", 3600)
    assert blocked(h) == ("closeout", "closeout_expired")
    assert_no_heldout(h)
    assert h.ledger.read_bytes() == h.original


def test_generic_transport_exception_stays_a_failure(harness):
    h, run = harness

    def boom(call):
        raise RuntimeError("native CC input transport failed: boom")

    run({"check-before-solver": gap()}, [boom], "--deadline-development", 3600)
    assert h.state["status"] == "blocked"
    assert "RuntimeError: native CC input transport failed: boom" in h.state["blocker"]
    (record,) = h.state["native_sessions"]
    assert record["error"].startswith("RuntimeError") and not record["timed_out"]
    assert record["outcome"] == {"terminal_evidence": False, "completed_at_deadline": False,
                                 "undelivered": []}
    assert "closeout" not in h.state and h.live_calls == 0
    assert_no_heldout(h)


def test_nonzero_exit_without_timeout_is_still_a_solver_failure(harness):
    h, run = harness
    run({"check-before-solver": gap()}, [lambda call: 3], "--deadline-development", 3600)
    assert h.state["blocked"]["phase"] == "solver"
    assert "closeout" not in h.state
    assert_no_heldout(h)


@pytest.mark.parametrize("mutate", ["bundle", "ledger"])
def test_bundle_or_ledger_mutation_during_recovery_is_rejected(harness, mutate):
    h, run = harness

    def tamper():
        if mutate == "bundle":
            with (h.task / "fix_code.py").open("a") as out:
                out.write("# edited during closeout\n")
        else:
            ledger = json.loads(h.ledger.read_text())
            ledger["trials"].pop()
            h.ledger.write_text(json.dumps(ledger))

    run({"check-before-solver": gap(), "closeout-check": gap(), "closeout-check-00": ready()},
        [timeout(), reports(h, extra=tamper)], "--deadline-development", 3600)
    assert blocked(h) == ("closeout", "selected_bundle_changed")
    changes = h.state["blocked"]["detail"]["changes"]
    assert ("files" in changes) if mutate == "bundle" else ("trial_rows" in changes)
    assert_no_heldout(h)


def test_bundle_change_across_finalize_is_rejected(harness):
    h, run = harness

    def tamper():
        (h.task / "fix_code.py").write_text("def run(env):\n    return False\n")

    run({"check-before-solver": gap(), "closeout-check": gap(), "closeout-check-00": ready()},
        [timeout(), reports(h)], "--deadline-development", 3600, on_finalize=tamper)
    assert h.state["blocked"]["phase"] == "finalize"
    assert "files" in h.state["blocked"]["detail"]["changes"]
    assert h.freeze == 0


def test_closeout_reserve_below_first_wait_starts_no_native_call(harness):
    h, run = harness
    run({"check-before-solver": gap(), "closeout-check": gap()},
        [timeout()], "--deadline-development", 3600, "--deadline-closeout", 299)
    assert blocked(h) == ("closeout", "closeout_reserve_short")
    detail = h.state["blocked"]["detail"]
    assert detail["remaining_seconds"] == 299.0 and detail["first_wait_seconds"] == 300
    assert detail["sessions"] == [] and len(h.native) == 1
    assert_no_heldout(h)


def test_second_continuation_is_refused_when_reserve_falls_below_first_wait(harness):
    h, run = harness
    run({"check-before-solver": gap(), "closeout-check": gap(),
         "closeout-check-00": gap("findings.md needs a nonempty 'Blocked seeds' section")},
        [timeout(), reports(h, advance=450)],
        "--deadline-development", 3600, "--deadline-closeout", 700)
    assert blocked(h) == ("closeout", "closeout_reserve_short")
    detail = h.state["blocked"]["detail"]
    assert detail["remaining_seconds"] == 250.0 and detail["sessions"] == ["closeout-00"]
    assert h.native[1]["timeout"] == 700 and len(h.native) == 2
    assert_no_heldout(h)


def test_development_turn_is_not_started_below_first_wait(harness):
    h, run = harness
    run({"check-before-solver": gap(), "check-00": gap(), "closeout-check": gap(),
         "closeout-check-00": ready()},
        [lambda call: h.clock.sleep(800) or 0, reports(h)],
        "--deadline-development", 1000, "--deadline-closeout", 3600)
    assert [c["label"] for c in h.native] == ["turn-00.stdout.jsonl", "closeout-00.stdout.jsonl"]
    assert all(c["timeout"] >= 300 for c in h.native)
    expiry = h.state["closeout"]["expiry"]
    assert expiry["at"] == expiry["reason_code"] == "development_reserve_short"
    assert expiry["remaining_seconds"] == 200.0 and expiry["first_wait_seconds"] == 300
    assert h.state["closeout"]["decision"] == "report_only_recovered"
    assert h.finalize == 1 and h.freeze == 1


@pytest.mark.parametrize("deadline, at", [(0, "between_turns"),
                                          (299, "development_reserve_short")])
def test_no_worker_to_resume_when_no_turn_could_start(harness, deadline, at):
    h, run = harness
    run({"check-before-solver": gap(), "closeout-check": gap()}, [],
        "--deadline-development", deadline)
    assert h.native == []
    assert h.state["closeout"]["expiry"]["at"] == at
    assert h.state["blocked"]["phase"] == "solver_lineage"
    assert_no_heldout(h)


# ---- closeout helper units ----------------------------------------------------------

@pytest.fixture
def closeout(monkeypatch):
    return load("r3_closeout_unit", SUPPORT / "closeout.py")


@pytest.fixture
def unit_case(tmp_path):
    case = {"sim": str(tmp_path / "sim"), "control": str(tmp_path / "control"),
            "suite": "s", "task": "t"}
    task = tmp_path / "sim/outputs/libero_fix_loop/s/t"
    task.mkdir(parents=True)
    (task / "development_state.json").write_text("{}")
    (task / "fix_code.py").write_text("x = 1\n")
    return case, task


def test_ready_evidence_runs_strict_check_only_on_gated_changed_boundaries(closeout, unit_case):
    case, task = unit_case
    calls, steps = [], iter([gap(), ready()])

    def run_check(name):
        calls.append(name)
        return next(steps)

    evidence = closeout.ReadyEvidence(run_check, case, "e")
    prose = {"type": "assistant", "message": {"content": [{"type": "text", "text": "ready"}]}}
    for event in (prose, {"type": "stream_event", "event": {"delta": "stage1_complete"}},
                  {"type": "user", "stage1_complete": True}, {"type": "system", "subtype": "init"}):
        assert evidence(event) is False
    assert calls == []
    assert evidence({"type": "result"}) is False          # first boundary: check runs, not ready
    assert evidence({"type": "result"}) is False          # nothing changed: no check
    assert calls == ["e-000"]
    (task / "findings.md").write_text("# Findings\n")
    assert evidence({"type": "system", "subtype": "task_notification", "task_id": "1"}) is True
    assert calls == ["e-000", "e-001"]
    assert evidence.record()["accepted"] is True


def test_ready_evidence_check_exception_is_a_recorded_refusal(closeout, unit_case):
    case, _ = unit_case

    def run_check(name):
        raise OSError("protocol unavailable")

    evidence = closeout.ReadyEvidence(run_check, case, "e")
    assert evidence({"type": "result"}) is False
    assert evidence.record() == {"checks": [{"name": "e-000", "ready": False,
                                             "error": "OSError: protocol unavailable"}],
                                 "accepted": False}
    for bad in ({"exit_code": 0, "result": {"ready": "true"}}, {"exit_code": 1, "result": {"ready": True}},
                {"exit_code": 0, "result": None}, None):
        assert closeout.strict_ready(bad) is False


@pytest.mark.parametrize("command", [
    "python scripts/libero/native_world_protocol.py trial --seed 51",
    "python scripts/libero/replay_trial.py --seed 51",
    "python -c 'print(1)'",
    "python - <<'PY'\nprint(1)\nPY",
    "cat findings.md",
])
def test_report_guard_denies_every_shell_call(closeout, unit_case, command):
    case, _ = unit_case
    assert closeout.report_only_denial({"tool_name": "Bash", "tool_input": {"command": command}},
                                       case)


def test_report_guard_allows_only_reads_messages_and_report_writes(closeout, unit_case):
    case, task = unit_case
    deny = closeout.report_only_denial
    for tool in ("Read", "Glob", "Grep", "SendMessage", "TaskOutput"):
        assert deny({"tool_name": tool, "tool_input": {}}, case) is None
    for tool in ("Agent", "Task", "NotebookEdit", "WebFetch", None):
        assert deny({"tool_name": tool, "tool_input": {}}, case)
    allowed = [task / "findings.md", task / "task_analysis.md", closeout.working_copy(case),
               "outputs/libero_fix_loop/s/t/findings.md"]
    for path in allowed:
        for tool in ("Write", "Edit", "MultiEdit"):
            assert deny({"tool_name": tool, "cwd": case["sim"],
                         "tool_input": {"file_path": str(path)}}, case) is None
    denied = [task / "fix_code.py", task / "fix_world_program.py", task / "fix_inventory.json",
              task / "development_state.json", task / "attempts/seed_51_BLOCKED.md",
              "outputs/libero_fix_loop/s/t/../t/fix_code.py", ""]
    for path in denied:
        assert deny({"tool_name": "Write", "cwd": case["sim"],
                     "tool_input": {"file_path": str(path)}}, case)


def test_report_guard_hook_main_denies_on_malformed_input(closeout, unit_case, tmp_path, monkeypatch):
    case, task = unit_case
    case_path, audit = tmp_path / "case.json", tmp_path / "audit.jsonl"
    case_path.write_text(json.dumps(case))
    argv = ["--case", str(case_path), "--audit", str(audit)]
    monkeypatch.setattr(sys, "stdin", io.StringIO("{not json"))
    assert closeout.main(argv) == 2
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"tool_name": "Read"})))
    assert closeout.main(argv) == 0
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(
        {"tool_name": "Bash", "tool_input": {"command": "python replay_trial.py"}})))
    assert closeout.main(argv) == 2
    rows = [json.loads(line) for line in audit.read_text().splitlines()]
    assert [(r["tool"], r["denied"]) for r in rows] == [("Read", False), ("Bash", True)]
    assert closeout.main(["--case", str(tmp_path / "missing.json"), "--audit", str(audit)]) == 2


def test_scan_reads_fake_proc_and_finds_only_cell_owned_work(closeout, tmp_path):
    case = {"sim": str(tmp_path / "sim"), "control": str(tmp_path / "control")}
    for root in (case["sim"], case["control"]):
        Path(root).mkdir()
    case_path = tmp_path / "case.json"
    proc = tmp_path / "proc"

    def entry(pid, argv, cwd, environ=b""):
        d = proc / str(pid)
        d.mkdir(parents=True)
        (d / "cmdline").write_bytes(b"\0".join(a.encode() for a in argv))
        (d / "environ").write_bytes(environ)
        os.symlink(cwd, d / "cwd")

    entry(101, ["python", "worker.py"], str(tmp_path),
          b"PATH=/bin\0ASPIRE_NATIVE_CASE=" + str(case_path).encode())
    entry(102, ["python", "scripts/libero/replay_trial.py"], case["sim"])
    entry(103, ["python", "scripts/libero/replay_trial.py"], str(tmp_path))   # another cell
    entry(104, [], case["sim"])                                              # kernel thread
    entry(105, ["vllm", "serve"], case["sim"])                                # shared service
    found = closeout.scan_task_processes(case, case_path, proc=proc)
    assert sorted(p["pid"] for p in found) == [101, 102]
    assert closeout.live_task_processes(case, case_path, settle_seconds=0, proc=proc) == found


# ---- wiring ----------------------------------------------------------------------

def test_supervisor_timeout_manifests_and_submission_use_full_deadlines(monkeypatch):
    for name in SIBLINGS:
        monkeypatch.delitem(sys.modules, name, raising=False)
    deadlines = load("r3_full_deadlines", SUPPORT / "full_deadlines.py")
    runtime = ModuleType("native_cc_runtime")
    for name in ("Service", "ServiceWatch", "atomic_json", "isolated_jit_env",
                 "new_attempt", "stop_processes"):
        setattr(runtime, name, object())
    monkeypatch.setitem(sys.modules, "native_cc_runtime", runtime)
    supervisor = load("r3_supervisor", SUPPORT / "dlc-supervisor.py")
    expected = (deadlines.DEVELOPMENT + deadlines.CLOSEOUT + deadlines.HELDOUT
                + deadlines.FINALIZE_MARGIN)
    assert supervisor.driver_timeout(deadlines) == expected == 133200
    assert "closeout.py" in supervisor.REQUIRED_SUPPORT
    source = (SUPPORT / "dlc-supervisor.py").read_text()
    assert '"--deadline-closeout", full_deadlines.CLOSEOUT' in source

    prepare = (R3 / "prepare-foundation.py").read_text()
    support_files = next(
        node.value for node in ast.walk(ast.parse(prepare)) if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Attribute) and t.attr == "SUPPORT_FILES" for t in node.targets))
    assert "closeout.py" in ast.literal_eval(support_files)
    assert '"revision": "closed-loop-r3-20261007"' in prepare
    assert "closed-loop-r2-20261005" not in prepare

    submit = (R3 / "submit-foundation.py").read_text()
    assert '"--max-minutes", str(full_deadlines.DLC_MAX_RUNNING_MINUTES)' in submit
    assert deadlines.DLC_MAX_RUNNING_MINUTES == -(-deadlines.TOTAL // 60) == 2292
