"""Exercise the repaired outer entry point with model/simulator boundaries faked."""
import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType

import pytest


SUPPORT = (Path(__file__).resolve().parents[1] / "docs/experiments"
           / "code-world-pipeline-repair-20261003/support")


def check_result(exhausted=(), *, ready=False):
    return {"exit_code": 0 if ready else 1,
            "result": {"ready": ready, "errors": [] if ready else ["missing report"],
                       "progress": {"seeds_exhausted_without_graded_evidence": list(exhausted)}}}


@pytest.fixture
def run_driver(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(SUPPORT))
    spec = importlib.util.spec_from_file_location("repaired_terminal_driver", SUPPORT / "run_two_task_cell.py")
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
    ledger = repo / "outputs/libero_fix_loop/synthetic/synthetic/development_state.json"
    ledger.parent.mkdir(parents=True)
    ledger.write_text('{"historical_retries": 45}\n')
    original = ledger.read_bytes()
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
    monkeypatch.setattr(driver, "verify_local_provider", lambda *a: {})
    monkeypatch.setattr(driver, "verify_capacity", lambda *a: {})
    monkeypatch.setattr(driver, "bind_child_env", lambda env, case: env)
    monkeypatch.setattr(driver, "fault", lambda *a: None)
    monkeypatch.setattr(driver.fixed_transport, "run_native_cc", native)
    monkeypatch.setattr(driver, "identity", lambda *a: ("same-session", "same-worker"))
    monkeypatch.setattr(driver, "audit_assignment", lambda *a: {"ok": True})
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
        assert ledger.read_bytes() == original
        return json.loads((control / "campaign_state.json").read_text()), calls

    return run


@pytest.mark.parametrize("before_first_turn", [True, False])
def test_exhaustion_stops_actual_entry_without_another_native_turn(run_driver, before_first_turn):
    checks = ([] if before_first_turn else [check_result()]) + [check_result([52, 53])]
    state, calls = run_driver(checks)
    assert calls == {"native": 0 if before_first_turn else 1, "finalize": 0, "freeze": 0}
    assert state["status"] == "blocked"
    assert state["blocked"]["detail"]["reason_code"] == "seeds_exhausted_without_graded_evidence"
    assert state["blocked"]["detail"]["seeds"] == [52, 53]
    assert state["blocked"]["state_preserved"] and not state["blocked"]["automatic_resume"]


def test_fully_graded_report_gap_can_continue_to_finalize_and_freeze(run_driver):
    state, calls = run_driver([check_result(), check_result(), check_result(ready=True)])
    assert calls == {"native": 2, "finalize": 1, "freeze": 1}
    assert state["blocked"]["phase"] == "fixture_stop"


@pytest.mark.parametrize("result", [None, {}, {"progress": {}},
                                    {"progress": {"seeds_exhausted_without_graded_evidence": "52"}}])
def test_unreadable_check_stops_before_native_call(run_driver, result):
    state, calls = run_driver([{"exit_code": 1, "result": result}])
    assert calls["native"] == calls["finalize"] == calls["freeze"] == 0
    assert state["blocked"]["detail"]["reason_code"] == "invalid_protocol_check"
