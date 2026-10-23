"""CC_TASK_2: the p4 driver stops on any terminal protocol state, and on no progress.

The p4 round's bowldrawer_p4_r1 had spent 45/45 attempts; `check` said
`incomplete` with an unpayable coverage gate, the driver only knew
`seeds_exhausted_without_graded_evidence`, and it restarted an idle coordinator
47 times. Model and simulator boundaries are faked exactly as in
`test_pipeline_driver_terminal.py`; the driver is the p4 copy, run from a
support directory assembled the way `prepare-rerun.py --support-overlay` does.
"""
import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType

import pytest

SIM = Path(__file__).resolve().parents[1]
P3_SUPPORT = SIM / "docs/experiments/code-world-search-control-p3-20261013/support"
P4_SUPPORT = SIM / "docs/experiments/code-world-coverage-p4-20261016/support"


def check_result(exhausted=(), *, ready=False, decision=None, reasons=None, replays=0, errors=None):
    result = {"ready": ready, "errors": [] if ready else list(errors or ["missing report"]),
              "progress": {"seeds_exhausted_without_graded_evidence": list(exhausted),
                           "replay_invocations": replays}}
    if decision is not None:
        result["decision_type"] = decision
    if reasons is not None:
        result["terminal_reasons"] = reasons
    return {"exit_code": 0 if ready else 1, "result": result}


@pytest.fixture
def run_driver(tmp_path, monkeypatch):
    support = tmp_path / "support"
    support.mkdir()
    for source in P3_SUPPORT.iterdir():
        if source.suffix == ".py":
            (support / source.name).symlink_to(source)
    for source in P4_SUPPORT.iterdir():  # the p4 overlay, as the p4n stager freezes it
        if source.suffix == ".py":
            (support / source.name).unlink(missing_ok=True)
            (support / source.name).write_bytes(source.read_bytes())
    monkeypatch.syspath_prepend(str(support))
    # Other studies' support modules of the same names may already be imported.
    for source in support.iterdir():
        monkeypatch.delitem(sys.modules, source.stem, raising=False)
    spec = importlib.util.spec_from_file_location("p4_terminal_driver", support / "run_two_task_cell.py")
    driver = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(driver)
    repo, control = tmp_path / "sim", tmp_path / "control"
    repo.mkdir(); control.mkdir()
    case = {"sim": str(repo), "control": str(control), "id": "synthetic_C",
            "condition": "C", "suite": "synthetic", "task": "synthetic",
            "dev_seeds": list(range(51, 66)), "campaign_timeout": 36000,
            "claude_config_dir": str(control / "config")}
    case_path = tmp_path / "case.json"
    case_path.write_text(json.dumps(case))
    calls = {"native": 0, "finalize": 0, "freeze": 0}

    def atomic(path, data):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(data))

    def prompts(*args):
        worker, coordinator = control / "worker-prompt.md", control / "coordinator-prompt.md"
        worker.write_text("synthetic assignment"); coordinator.write_text("synthetic coordinator")
        return worker, coordinator

    def native(*args, **kwargs):
        calls["native"] += 1
        return 0

    def freeze(*args):
        calls["freeze"] += 1
        raise driver.Blocked("fixture_stop", "test stops before any held-out evaluator")

    campaign = ModuleType("native_world_campaign")
    campaign.atomic_json = atomic
    campaign.verify_runtime = lambda *a: None
    campaign.native_settings = lambda *a: {"hooks": {"PreToolUse": []}}
    campaign.native_environment = lambda *a: {}
    campaign.perception_ready = lambda *a: {}
    campaign.probe = lambda *a: {}
    campaign.write_prompts = prompts
    campaign.native_command = lambda *a, **kw: ["synthetic-never-executed"]
    campaign.worker_agent = lambda *a: {}
    monkeypatch.setitem(sys.modules, "native_world_campaign", campaign)
    importer = ModuleType("foundation_import")
    importer.import_diagnostic = lambda *a: {}
    monkeypatch.setitem(sys.modules, "foundation_import", importer)
    monkeypatch.setattr(driver.scope, "apply", lambda *a: None)
    monkeypatch.setattr(driver.output_ownership, "verify", lambda *a: {})
    monkeypatch.setattr(driver.runtime_limits, "install", lambda settings, case: settings)
    monkeypatch.setattr(driver, "verify_local_provider", lambda *a: {})
    monkeypatch.setattr(driver, "verify_capacity", lambda *a: {})
    monkeypatch.setattr(driver, "bind_child_env", lambda env, case: env)
    monkeypatch.setattr(driver, "fault", lambda *a: None)
    monkeypatch.setattr(driver.fixed_transport, "run_native_cc", native)
    monkeypatch.setattr(driver, "identity", lambda *a: ("same-session", "same-worker"))
    monkeypatch.setattr(driver, "audit_assignment", lambda *a: {"ok": True})
    monkeypatch.setattr(driver, "verify_received_assignment", lambda *a: {"ok": True})
    monkeypatch.setattr(driver, "heldout_manifest", freeze)

    def run(checks):
        results = iter(checks)

        def protocol(case, repo, env, folder, step, command, *args):
            if command == "check":
                return next(results)
            if command == "finalize":
                calls["finalize"] += 1
            return {"exit_code": 0, "result": {}}

        campaign.protocol = protocol
        assert driver.main(["--case", str(case_path)]) == 1
        return json.loads((control / "campaign_state.json").read_text()), calls

    run.driver = driver
    return run


UNREACHABLE = [{"code": "coverage_unreachable", "missing": [51, 52, 53, 61, 62, 63, 64, 65],
                "retries_remaining_total": 0}]


@pytest.mark.parametrize("before_first_turn", [True, False])
def test_unreachable_coverage_stops_without_another_coordinator_round(run_driver, before_first_turn):
    terminal = check_result(decision="terminal", reasons=UNREACHABLE)
    checks = ([] if before_first_turn else [check_result(decision="incomplete", replays=1)]) + [terminal]
    state, calls = run_driver(checks)
    assert calls == {"native": 0 if before_first_turn else 1, "finalize": 0, "freeze": 0}
    assert state["status"] == "blocked"
    detail = state["blocked"]["detail"]
    assert detail["reason_code"] == "coverage_unreachable" and detail["terminal_reasons"] == UNREACHABLE
    assert state["blocked"]["state_preserved"] and not state["blocked"]["automatic_resume"]


def test_exhausted_seeds_still_stop_with_the_legacy_reason(run_driver):
    state, calls = run_driver([check_result([52, 53], decision="terminal")])
    assert calls["native"] == 0
    assert state["blocked"]["detail"]["reason_code"] == "seeds_exhausted_without_graded_evidence"
    assert state["blocked"]["detail"]["seeds"] == [52, 53]


def test_terminal_without_a_structured_reason_is_an_invalid_check(run_driver):
    state, calls = run_driver([check_result(decision="terminal")])
    assert calls["native"] == 0
    assert state["blocked"]["detail"]["reason_code"] == "invalid_protocol_check"


def test_rounds_that_change_nothing_stop_after_stall_turns(run_driver):
    stall = run_driver.driver.STALL_TURNS
    checks = [check_result(decision="incomplete")] + [check_result(decision="incomplete", replays=7)] * (stall + 5)
    state, calls = run_driver(checks)
    assert calls == {"native": stall, "finalize": 0, "freeze": 0}
    assert state["blocked"]["detail"]["reason_code"] == "no_progress"


def test_rounds_that_keep_running_trials_are_not_a_stall(run_driver):
    checks = [check_result(decision="incomplete")]
    checks += [check_result(decision="incomplete", replays=n) for n in range(1, 6)]
    checks += [check_result(ready=True)]
    state, calls = run_driver(checks)
    assert calls == {"native": 6, "finalize": 1, "freeze": 1}
    assert state["blocked"]["phase"] == "fixture_stop"


@pytest.mark.parametrize("result", [None, {}, {"progress": {}},
                                    {"progress": {"seeds_exhausted_without_graded_evidence": "52"}}])
def test_unreadable_check_stops_before_native_call(run_driver, result):
    state, calls = run_driver([{"exit_code": 1, "result": result}])
    assert calls["native"] == calls["finalize"] == calls["freeze"] == 0
    assert state["blocked"]["detail"]["reason_code"] == "invalid_protocol_check"


def test_p4_driver_differs_from_p3_only_in_the_development_stop_and_the_heldout_watchdog():
    """CC_TASK_2 changed the development stop; CC_TASK_5 the held-out deadline and
    the supplement mode. The held-out verifiers themselves stay byte-identical."""
    import ast
    p3_text = (P3_SUPPORT / "run_two_task_cell.py").read_text()
    p4_text = (P4_SUPPORT / "run_two_task_cell.py").read_text()
    p3 = {n.name: ast.get_source_segment(p3_text, n) for n in ast.parse(p3_text).body
          if isinstance(n, ast.FunctionDef)}
    p4 = {n.name: ast.get_source_segment(p4_text, n) for n in ast.parse(p4_text).body
          if isinstance(n, ast.FunctionDef)}
    changed = {name for name in p3 if p4.get(name) != p3[name]}
    assert changed == {"require_development_possible", "main"}
    assert set(p4) - set(p3) == {"check_fingerprint", "heldout_pending", "heldout_deadline",
                                 "run_heldout_supplement"}
    for frozen in ("heldout_manifest", "manifest_row_problems", "check_heldout_rows", "run_heldout",
                   "stop_group", "judge_heldout_seeds"):
        assert p4[frozen] == p3[frozen], frozen
