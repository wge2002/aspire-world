"""Independent coordinator acceptance; synthetic fixtures, zero simulation/inference."""
import code
from contextlib import ExitStack
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

SIM = Path(__file__).resolve().parents[3]
for path in (SIM / "tests", SIM / "scripts/libero", SIM / "scripts/common", SIM.parents[1]):
    sys.path.insert(0, str(path))
import executable_world_profile as profile
import native_world_protocol as protocol
import replay_trial
import test_native_world_fixloop as fixtures
from test_executable_world import WORLD
from aspire.sim.cap.world_model import executable_world


def test_imports_are_current_checkout():
    for module in (profile, protocol, replay_trial, executable_world):
        assert Path(module.__file__).resolve().is_relative_to(SIM)


def test_actual_subprocess_world_load_failure_has_authored_report(tmp_path):
    policy = tmp_path / "policy.py"
    world = tmp_path / "world.py"
    policy.write_text("get_observation()\n")
    world.write_text(WORLD + '\nraise RuntimeError("acceptance load error")\n')
    env = {k: os.environ[k] for k in ("PATH", "HOME", "LANG") if k in os.environ}
    env["PYTHONPATH"] = str(SIM.parents[1])
    report = profile.run_mode({"c_arm": "full", "task": "synthetic"}, SIM, env,
                              tmp_path, {"policy": policy, "world": world},
                              "rehearsal", None, 60)
    assert report["status"] == "program_error"
    assert report["stage"] == "world_load"
    assert report["api_calls"] == 0
    assert report["conclusion"] == "authored_error"
    assert report["retryable"] is False
    assert "acceptance load error" in report["reason"]
    assert profile.screening_status([report]) == "rejected"


@pytest.mark.parametrize("payload", [None, "{", "[]", '{"status":"unrecognized"}'])
def test_unusable_subprocess_report_is_retryable(tmp_path, monkeypatch, payload):
    def run(*args, **kwargs):
        output = tmp_path / "rehearsal"
        output.mkdir()
        if payload is not None:
            (output / "offline_result.json").write_text(payload)
        return SimpleNamespace(returncode=1)
    monkeypatch.setattr(profile.subprocess, "run", run)
    report = profile.run_mode({"c_arm": "full", "task": "synthetic"}, SIM, {}, tmp_path,
                              {"policy": "p", "world": "w"}, "rehearsal", None, 1)
    assert report["status"] == "infrastructure_error"
    assert report["retryable"] is True
    assert report["conclusion"] == "unknown"


def test_failed_subprocess_launch_is_retryable(tmp_path, monkeypatch):
    def run(*args, **kwargs):
        raise FileNotFoundError("synthetic missing interpreter")
    monkeypatch.setattr(profile.subprocess, "run", run)
    report = profile.run_mode({"c_arm": "full", "task": "synthetic"}, SIM, {}, tmp_path,
                              {"policy": "p", "world": "w"}, "rehearsal", None, 1)
    assert report["status"] == "infrastructure_error" and report["retryable"]


def test_actual_repl_exceptions_are_persisted(tmp_path):
    console = replay_trial._diagnostic_console(code)({"env": object()})
    console.push("env.handle")
    console.push("plate_c")
    replay_trial._write_diagnostic_session(
        SimpleNamespace(output_dir=str(tmp_path), suite="synthetic", task="synthetic", trial=51), console)
    report = json.loads((tmp_path / "diagnostic_session.json").read_text())
    assert report["error_count"] == 2
    assert [e["type"] for e in report["errors"]] == ["AttributeError", "NameError"]
    assert [e["statement_index"] for e in report["errors"]] == [1, 2]


@pytest.mark.parametrize("session,expected", [
    ({"errors": [{"type": "NameError"}], "error_count": 1}, "diagnostic_program_error"),
    (None, "infrastructure_error"),
    ("{", "infrastructure_error"),
    ([], "infrastructure_error"),
    ({"errors": [], "error_count": 0}, "complete"),
])
def test_diagnostic_session_overrides_stray_success(tmp_path, session, expected):
    with ExitStack() as stack:
        h = fixtures.Harness(stack)
        h.snapshot()
        h.write("diagnostic_session.py", "print('synthetic')\n")
        h.outcome(51, success=True)
        def replay(command, **kwargs):
            output = Path(command[command.index("--args.output-dir") + 1])
            output.mkdir(parents=True, exist_ok=True)
            if session is not None:
                (output / "diagnostic_session.json").write_text(
                    session if isinstance(session, str) else json.dumps(session))
            return 0, ""
        h.replay.side_effect = replay
        row = h.trial("diagnostic", 51)
        assert row["status"] == expected
        assert row["charged"] is True
        assert row.get("reward", 0) == 0 and row.get("task_completed", 0) == 0
        assert h.state.attempts_used(51) == 1
        assert h.state.progress()["graded_executions"] == 0


def test_blocked_admission_retries_same_bundle_without_spending(tmp_path, monkeypatch):
    with ExitStack() as stack:
        h = fixtures.Harness(stack)
        h.snapshot()
        # Use the ordinary real admission and ledger; only the offline process
        # boundary is fault-injected. No simulator or model is invoked.
        h.case["executable_world_revision"] = "r1"
        identity = {"policy": "a" * 64, "world": "b" * 64, "tape": None}
        verdict = {"status": "blocked", "directory": "synthetic", "identity": identity,
                   "conclusions": ["unknown"], "retryable": True, "reports": [], "cached": False}
        monkeypatch.setattr(profile, "checks", lambda *a, **k: dict(verdict))
        before = h.replay.call_count
        blocked = h.trial("initial", 51)
        assert blocked["status"] == "screening_blocked"
        assert h.state.attempts_used(51) == 0
        assert h.replay.call_count == before
        assert len(h.state.unresolved_blockers()) == 1
        verdict.update(status="checked", retryable=False)
        h.outcome(51, success=False)
        admitted = h.trial("initial", 51)
        assert admitted["status"] == "complete"
        assert h.state.attempts_used(51) == 1
        assert h.replay.call_count == before + 1
        assert not h.state.unresolved_blockers()
        history = h.state.data["blockers"]
        assert len(history) == 1 and history[0]["resolved"] is True
        assert history[0]["detail"]["identity"] == identity
