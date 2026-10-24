"""CPU tests for the RS v3 user-requested short pilot adapter/supervisor.

No model, native CC, simulator, perception or tmux is started. The development
ledger and held-out runner are the real modules driven by the existing synthetic
fixtures (`run_replay` faked); the campaign launcher uses the existing
process-boundary harness; the root supervisor runs against a fake Host. The
only real processes are `sleep` children this test owns, used to prove that the
watcher signals exactly its own native process group.
"""
from __future__ import annotations

import fcntl
import io
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

SIM = Path(__file__).resolve().parents[1]
OPS = SIM / "scripts/ops/rs_articulation_v3_20261012"
for extra in (SIM.parents[1], SIM / "scripts/common", SIM / "scripts/libero", SIM / "tests", OPS):
    sys.path.insert(0, str(extra))

import native_world_fixloop_state as ledger_mod  # noqa: E402
import pilot_runner as pr  # noqa: E402
import streaming_fixloop_campaign as camp  # noqa: E402
import streaming_fixloop_guard as guard  # noqa: E402
import streaming_fixloop_heldout as sh  # noqa: E402
import streaming_fixloop_protocol as sp  # noqa: E402
import test_streaming_fixloop_campaign as tcamp  # noqa: E402
from test_streaming_fixloop import cell, cli, started, task_dir, trial  # noqa: E402,F401
from test_streaming_fixloop_heldout import finalized, heldout_calls  # noqa: E402

RT = SimpleNamespace(sim=SIM, campaign=camp, sp=sp, heldout=sh, ledger=ledger_mod)
CANONICAL = tcamp.TRIAL  # the launcher's own rendered trial call (\\-continued, | tail)


def plain(fixture):
    """The undecorated body of an existing fixture (reused under another name)."""
    if hasattr(fixture, "_get_wrapped_function"):
        return fixture._get_wrapped_function()
    if hasattr(fixture, "__pytest_wrapped__"):
        return fixture.__pytest_wrapped__.obj
    return fixture


@pytest.fixture
def ccell(tmp_path):
    return plain(tcamp.cell)(tmp_path)


@pytest.fixture
def charness(ccell, monkeypatch):
    return plain(tcamp.harness)(ccell, monkeypatch)


def call(seed, phase="initial", sub="trial"):
    return (f".venv-libero/bin/python3 scripts/libero/{pr.PROTOCOL_SCRIPT} {sub} --phase {phase} "
            f"--seed {seed} --code \"$TASK_DIR/initial_code.py\" "
            f"--streaming-config \"$TASK_DIR/initial_streaming_config.json\"")


def ledger(c) -> dict:
    return json.loads((task_dir(c) / "development_state.json").read_text())


def bind(case_path: Path, case: dict) -> Path:
    pilot = Path(case["control"]).resolve() / "pilot"
    pr.bind_identity(pilot, pr.pilot_identity(case_path, case), resume=False)
    return pilot


def hook(case_path: Path, command: str) -> int:
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
    return pr.gate_hook(case_path, io.StringIO(payload))


def row(seed, phase="initial", status="complete", rc=0, done=0, t=0.0, attempt=1):
    r = {"phase": phase, "seed": seed, "attempt": attempt, "status": status,
         "directory": f"development/{phase}/seed_{seed}/attempt_{attempt}",
         "spends_retry": phase in pr.RETRY_PHASES, "executed": True,
         "started_at": t, "finished_at": t + 10}
    if status in pr.SETTLED:
        r.update(sandbox_rc=rc, task_completed=done)
    return r


# --- the adapter mirrors the frozen runtime's definitions ------------------------------


def test_adapter_constants_match_runtime_and_modules_come_from_the_case_runtime():
    assert pr.SETTLED == ledger_mod.RESOLVED_STATUSES and pr.RETRY_LIMIT == ledger_mod.RETRY_LIMIT
    assert set(pr.RETRY_PHASES) == set(ledger_mod.RETRY_PHASES)
    assert pr.GRADED_PHASES == sh.GRADED_PHASES and pr.HELDOUT_SEEDS == sh.HELDOUT_SEEDS
    assert pr.HELDOUT_FINAL == sh.FINAL_STATUSES and guard.PROTOCOL == pr.PROTOCOL_SCRIPT
    assert all(sp.TASKS[k] == v for k, v in pr.TASKS.items())
    rt = pr.runtime_modules(SIM)
    assert Path(rt.campaign.__file__).resolve().is_relative_to(SIM)


# --- the first-three screen on the real ledger -----------------------------------------


def test_no_pass_screen_blocks_seed54_and_all_execution_without_touching_budgets(cell):
    started(cell)
    pilot = bind(cell.path, cell.case)
    cell.fake.outcomes.update({51: (0, "", 0, 0), 52: (0, "", 1, 1), 53: (0, "", 0, 0)})
    assert trial(cell, "initial", 51)[0] == 0
    # Undecided: order 51, 52, 53 and nothing beyond them.
    assert "in order" in pr.admission_denial(call(53), ledger(cell))
    assert "not admitted" in pr.admission_denial(call(54), ledger(cell))
    assert "repairs" in pr.admission_denial(call(51, "repair"), ledger(cell))
    assert pr.admission_denial(call(52), ledger(cell)) is None
    assert hook(cell.path, call(54)) == 2 and hook(cell.path, call(52)) == 0
    trial(cell, "initial", 52)  # crash with the oracle reporting completion
    trial(cell, "initial", 53)
    before = (task_dir(cell) / "development_state.json").read_bytes()
    gate = pr.screen(ledger(cell))
    assert gate["decision"] == "no_pass" and gate["passing_seeds"] == []
    assert gate["seeds"]["52"]["program_crashes"] and not gate["seeds"]["52"]["passed"]
    for command in (call(54), call(51, "repair"), call(52, "diagnostic"), "ls", "python3 x.py"):
        assert pr.admission_denial(command, ledger(cell)) == pr.CLOSED
        assert hook(cell.path, command) == 2
    for sub in ("status", "check"):
        assert hook(cell.path, f".venv-libero/bin/python3 scripts/libero/{pr.PROTOCOL_SCRIPT} {sub}") == 0
    # The hook reads only: ledger, budgets and deadline are byte-identical.
    assert (task_dir(cell) / "development_state.json").read_bytes() == before
    assert {s: cli(cell, "status")[1]["retries_used_per_seed"][s] for s in ("51", "52", "53", "54")} \
        == {"51": 1, "52": 1, "53": 1, "54": 0}
    audit = pr.read_jsonl(pilot / "gate-audit.jsonl")
    assert any(a["denied"] and a["decision"] == "no_pass" for a in audit)
    assert any(not a["denied"] and a["decision"] == "undecided" for a in audit)
    assert pr.classify("stage1_incomplete", gate, None)["outcome"] == "early_stopped"


def test_genuine_pass_permits_the_original_development_flow(cell):
    started(cell)
    bind(cell.path, cell.case)
    cell.fake.outcomes.update({51: (0, "", 0, 0), 52: (0, "", 0, 1), 53: (0, "", 0, 0)})
    for seed in (51, 52, 53):
        trial(cell, "initial", seed)
    gate = pr.screen(ledger(cell))
    assert gate["decision"] == "pass" and gate["passing_seeds"] == [52]
    for command in (call(54), call(51, "repair"), "ls", f"python3 -c 'print(1)'"):
        assert pr.admission_denial(command, ledger(cell)) is None
    assert hook(cell.path, call(54)) == 0
    assert trial(cell, "initial", 54)[0] == 0  # the original protocol and budgets apply
    gate = pr.screen(ledger(cell))
    assert gate["screen_violations"] == [] and gate["executions_beyond_screen"]
    # Protected outer paths stay refused even after a pass.
    assert hook(cell.path, f"cat {Path(cell.case['control']).resolve() / 'pilot'}/x") == 2


def test_crash_with_oracle_true_and_world_errors_are_never_success():
    assert not pr.passed({"status": "complete", "sandbox_rc": 1, "task_completed": 1})
    assert not pr.passed({"status": "world_program_error", "sandbox_rc": 1, "task_completed": 1})
    assert not pr.passed({"status": "infrastructure_error", "task_completed": 1})
    assert pr.passed({"status": "complete", "sandbox_rc": 0, "task_completed": 1})
    data = {"trials": [row(51, rc=1, done=1), row(52, status="world_program_error", rc=1, done=1),
                       row(53, rc=1, done=1)]}
    assert pr.screen(data)["decision"] == "no_pass"


def test_infrastructure_and_missing_evidence_stay_distinct(cell):
    started(cell)
    cell.fake.outcomes.update({51: (None, "OSError: no GPU", 0, 0), 52: (0, "", 0, 0),
                               53: (0, "", 0, 0)})
    trial(cell, "initial", 51)  # infrastructure, charged, budget remains: still open
    gate = pr.screen(ledger(cell))
    assert gate["seeds"]["51"]["state"] == "open" and gate["seeds"]["51"]["infrastructure_rows"]
    for _ in range(2):
        trial(cell, "initial", 51)
    trial(cell, "initial", 52)
    trial(cell, "initial", 53)
    gate = pr.screen(ledger(cell))
    assert gate["seeds"]["51"]["state"] == "missing" and gate["decision"] == "missing_evidence"
    verdict = pr.classify("stage1_incomplete", gate, None)
    assert verdict["outcome"] == "halted_screen_missing_evidence" and not verdict["permits_next_cell"]
    assert pr.admission_denial(call(54), ledger(cell)) == pr.CLOSED
    # An in-flight attempt keeps the screen open and halts, never a success.
    running = {"trials": [row(51, status="running"), row(52), row(53)]}
    gate = pr.screen(running)
    assert gate["decision"] == "undecided" and gate["running_anywhere"]
    assert pr.classify("stage1_incomplete", gate, None)["outcome"] == "halted_unresolved_running"


def test_screen_violation_halts_even_after_a_pass():
    data = {"trials": [row(51, t=0), row(54, t=5), row(52, done=1, t=20), row(53, t=40)]}
    gate = pr.screen(data)
    assert gate["decision"] == "pass" and gate["screen_violations"] == [data["trials"][1]["directory"]]
    assert pr.classify("stage1_finalized", gate, None)["outcome"] == "halted_screen_violation"


@pytest.mark.parametrize("command,expected", [
    (CANONICAL, ("trial", "initial", 52)),
    ('"/mnt/rt/aspire/sim/.venv-libero/bin/python3" "scripts/libero/streaming_fixloop_protocol.py" '
     "trial --phase 'smoke' --seed \"51\" --code \"$TASK_DIR/initial_code.py\" "
     "--streaming-config \"$TASK_DIR/initial_streaming_config.json\" 2>&1 | tail -5",
     ("trial", "smoke", 51)),
    ("cd /x && .venv-libero/bin/python3 scripts/libero/streaming_fixloop_protocol.py trial "
     "--phase=initial --seed=53 --code a.py --streaming-config b.json", ("trial", "initial", 53)),
    (".venv-libero/bin/python3 scripts/libero/streaming_fixloop_protocol.py trial --pha initial "
     "--se 54 --code a --streaming-config b", ("trial", "initial", 54)),
    (".venv-libero/bin/python3 scripts/libero/streaming_fixloop_protocol.py status", ("status", None, None)),
])
def test_protocol_call_parses_quoted_and_canonical_calls(command, expected):
    parsed = pr.protocol_call(command)
    assert "error" not in parsed and (parsed["sub"], parsed["phase"], parsed["seed"]) == expected


@pytest.mark.parametrize("command", [
    call("$SEED"), call("5$(echo 4)"),
    call(51) + " --seed 54", call(51) + " --see 54", call(51) + " --phase repair",
    '"$ASPIRE_ROOT/scripts/libero/streaming_fixloop_protocol.py" trial --phase initial --seed 51',
    "python3 -c \"import runpy; runpy.run_path('scripts/libero/streaming_fixloop_protocol.py')\"",
    ".venv-libero/bin/python3 scripts/libero/streaming_fixloop_protocol.py trial --phase initial "
    "--seed 51 'unterminated",
    ".venv-libero/bin/python3 scripts/libero/streaming_fixloop_protocol.py trial --seed 51",
])
def test_dynamic_or_ambiguous_protocol_calls_fail_closed_during_the_screen(command):
    assert pr.protocol_call(command).get("error")
    assert pr.admission_denial(command, {"trials": []})


def test_gate_hook_fails_closed_without_a_bound_identity(cell):
    started(cell)
    assert hook(cell.path, call(51)) == 2
    bind(cell.path, cell.case)
    assert hook(cell.path, call(51)) == 0


# --- the watcher: only its own idle native group --------------------------------------


def no_pass_ledger(task: Path, extra=()):
    task.mkdir(parents=True, exist_ok=True)
    (task / "development_state.json").write_text(json.dumps(
        {"trials": [row(51), row(52, rc=1, done=1), row(53), *extra]}))


def session_leader():
    return subprocess.Popen(["sleep", "60"], start_new_session=True)


def test_watcher_never_stops_during_a_trial_then_stops_only_its_group(tmp_path):
    task, pilot = tmp_path / "cell", tmp_path / "pilot"
    pilot.mkdir()
    no_pass_ledger(task, [row(51, phase="diagnostic", status="running", attempt=2)])
    proc = session_leader()
    try:
        w = pr.NativeStopWatcher(proc, task, pilot, poll=0.01, grace=5)
        assert w.check_once() == "watching" and proc.poll() is None  # running row
        no_pass_ledger(task)
        with (task / ".trial.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            assert w.check_once() == "watching" and proc.poll() is None  # trial lock held
        assert w.check_once() == "watching"  # first quiet poll only
        assert w.check_once() == "stopped" and proc.wait(timeout=10) is not None
        events = [r["event"] for r in pr.read_jsonl(pilot / "operator_stop.jsonl")]
        assert events == ["gate_decided", "native_stop_requested", "native_stop_result"]
        assert pr.read_jsonl(pilot / "operator_stop.jsonl")[1]["pgid"] == proc.pid
    finally:
        proc.kill()
        proc.wait()


def test_watcher_skips_a_process_that_is_not_its_own_group_leader_and_ignores_a_pass(tmp_path):
    task, pilot = tmp_path / "cell", tmp_path / "pilot"
    pilot.mkdir()
    no_pass_ledger(task)
    proc = subprocess.Popen(["sleep", "60"])  # shares this test's process group
    try:
        w = pr.NativeStopWatcher(proc, task, pilot, poll=0.01, grace=1, confirmations=1)
        assert w.check_once() == "stopped" and w.result == {"signalled": False}
        assert proc.poll() is None  # neither it nor this test's group was signalled
        (task / "development_state.json").write_text(json.dumps(
            {"trials": [row(51), row(52, done=1), row(53)]}))
        assert pr.NativeStopWatcher(proc, task, pilot, poll=0.01).check_once() == "pass"
        assert proc.poll() is None
    finally:
        proc.kill()
        proc.wait()


# --- one cell through the original launcher ------------------------------------------


def wire_solver(monkeypatch, charness, rows, *, native=False):
    """The harness solver, plus ledger rows written as the native session would leave them."""
    solver = camp.run_native_cc

    def fake(command, *, cwd, env, stdout_path, stderr_path, timeout, on_start=None, **_):
        code = solver(command, cwd=cwd, env=env, stdout_path=stdout_path,
                      stderr_path=stderr_path, timeout=timeout)
        task = Path(env["ASPIRE_NATIVE_CASE"]).parent / "campaigns/bowl_sam2streaming_r1/cell"
        data = json.loads((task / "development_state.json").read_text())
        data["trials"] = rows
        (task / "development_state.json").write_text(json.dumps(data))
        if not native:
            return code
        proc = session_leader()
        on_start(proc)
        return proc.wait(timeout=30)
    monkeypatch.setattr(camp, "run_native_cc", fake)


def test_no_pass_cell_early_stops_with_receipts_hooks_and_prompts(ccell, charness, monkeypatch):
    (ccell.control / "claude-config").mkdir()
    (ccell.control / "prelaunch-route-probe-20261012").mkdir()
    (ccell.control / "prelaunch-route-probe-20261012/probe.json").write_text("{}")
    charness.codes["check"] = 1
    wire_solver(monkeypatch, charness, [row(51), row(52, rc=1, done=1), row(53)], native=True)
    assert pr.run_cell(RT, ccell.path, resume=False, launch_id="L1", clock=charness.clock,
                       poll=0.02, grace=5) == 0
    control, pilot = ccell.control, ccell.control / "pilot"
    outcome = json.loads((pilot / "pilot_outcome.json").read_text())
    assert outcome["outcome"] == "early_stopped" and outcome["permits_next_cell"]
    assert outcome["campaign_status"] == "stage1_incomplete" and outcome["launch_id"] == "L1"
    assert "finalize" not in charness.events and "heldout" not in charness.events
    stops = [r["event"] for r in pr.read_jsonl(pilot / "operator_stop.jsonl")]
    assert stops == ["gate_decided", "native_stop_requested", "native_stop_result", "early_stopped"]
    assert (control / "prelaunch-route-probe-20261012/probe.json").read_text() == "{}"
    start = pr.read_jsonl(pilot / "pilot_events.jsonl")[0]
    assert start["control_kind"] == "prep_only"
    assert start["preserved_prep_entries"] == ["claude-config", "prelaunch-route-probe-20261012"]
    # Original guard first, plus the extra gate hook; override in worker AND coordinator.
    session = control / "sessions/01"
    hooks = json.loads((session / "worker-settings.json").read_text())["hooks"]["PreToolUse"]
    assert "streaming_fixloop_guard.py" in hooks[0]["hooks"][0]["command"]
    assert hooks[1]["matcher"] == "Bash" and "gate-hook" in hooks[1]["hooks"][0]["command"]
    assert str(pr.ADAPTER) in hooks[1]["hooks"][0]["command"]
    for name in ("worker-prompt.md", "coordinator-prompt.md"):
        assert (session / name).read_text().count(pr.PILOT_MARK) == 1
    record = json.loads((session / "pilot-prompt-override.json").read_text())
    assert set(record["files"]) == {"worker", "coordinator"}
    assert record["override_sha256"] == pr.sha256_text(pr.PILOT_OVERRIDE)
    command = charness.sessions[0]["command"]
    agents = json.loads(command[command.index("--agents") + 1])
    assert pr.PILOT_MARK in agents["fix-loop-worker"]["prompt"]
    assert any(pr.PILOT_MARK in arg for i, arg in enumerate(command)
               if i != command.index("--agents") + 1)  # the coordinator prompt itself
    # Ledger and deadline are exactly what the session left; a resume re-runs nothing.
    deadline = json.loads((control / "cell/development_state.json").read_text())["phase_clock"]
    events = list(charness.events)
    assert pr.run_cell(RT, ccell.path, resume=True, launch_id="L2", clock=charness.clock) == 0
    assert charness.events == events and len(list((control / "sessions").iterdir())) == 1
    assert json.loads((control / "cell/development_state.json").read_text())["phase_clock"] == deadline
    with pytest.raises(pr.Refused, match="--resume"):
        pr.run_cell(RT, ccell.path, resume=False)


def test_pass_cell_finalizes_normally_then_runs_outer_heldout_after_solver_exit(
        ccell, charness, monkeypatch):
    rows = [row(51, t=0), row(52, done=1, t=20), row(53, t=40), row(54, t=60)]
    wire_solver(monkeypatch, charness, rows)
    step = camp.outer_step
    seen = {}

    def outer(case_path, repo, env, control, name, script, *args):
        if name == "heldout":
            seen.update(script=script, args=args, marker=(control / guard.OUTER_MARKER).is_file())
            (control / "heldout").mkdir()
            (control / "heldout/pilot_receipt.json").write_text(json.dumps(
                {"pilot_complete": True, "requested_infrastructure_error_seeds": []}))
            charness.codes["heldout"] = 1  # the original runner: three of 50 -> incomplete
        return step(case_path, repo, env, control, name, script, *args)
    monkeypatch.setattr(camp, "outer_step", outer)
    assert pr.run_cell(RT, ccell.path, resume=False, launch_id="L", clock=charness.clock) == 0
    assert charness.events.index("solver") < charness.events.index("finalize") \
        < charness.events.index("heldout")
    assert seen == {"script": str(pr.ADAPTER), "args": ("heldout",), "marker": True}
    outcome = json.loads((ccell.control / "pilot/pilot_outcome.json").read_text())
    assert outcome["outcome"] == "heldout_pilot_complete"
    assert outcome["campaign_status"] == "heldout_incomplete"


@pytest.mark.parametrize("entry", ["cell/development_state.json", "campaign_state.json", "junk"])
def test_unknown_or_ledger_artifacts_are_never_adopted(ccell, charness, entry):
    path = ccell.control / entry
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}")
    for resume in (False, True):
        with pytest.raises(pr.Refused, match="neither known prelaunch prep"):
            pr.run_cell(RT, ccell.path, resume=resume)
    assert not (ccell.control / "pilot").exists() and charness.events == []


def test_resume_requires_same_adapter_settings(ccell, monkeypatch):
    pilot = bind(ccell.path, ccell.case)
    monkeypatch.setattr(pr, "PILOT_SETTINGS", {**pr.PILOT_SETTINGS, "screen_seeds": [51, 52]})
    with pytest.raises(pr.Refused, match="settings_sha256"):
        pr.bind_identity(pilot, pr.pilot_identity(ccell.path, ccell.case), resume=True)


# --- held-out: the original runner, stopped before seed 4 ------------------------------


def test_heldout_pilot_stops_before_seed4_and_keeps_47_not_run(cell, monkeypatch):
    finalized(cell, monkeypatch)
    bind(cell.path, cell.case)
    cell.fake.outcomes.update({1: (0, "", 0, 1), 2: (0, "", 1, 1), 3: (0, "", 0, 0)})
    original = sh.evaluate_seed
    result, receipt = pr.run_heldout_pilot(RT, cell.path, resume=False, clock=cell.clock)
    assert sh.evaluate_seed is original and sh.HELDOUT_SEEDS == tuple(range(1, 51))
    assert [Path(c["directory"]).name for c in heldout_calls(cell)] == ["seed_01", "seed_02", "seed_03"]
    evaluation = Path(cell.case["control"]).resolve() / "heldout"
    report = json.loads((evaluation / "heldout_result.json").read_text())
    assert report["incomplete"] and report["success_rate_denominator"] == 50
    assert report["not_run_seeds"] == list(range(4, 51)) and report["counts"]["not_run"] == 47
    assert report["stop_reason"] == pr.PILOT_STOP_REASON and report["counts"]["success"] == 1
    assert receipt["pilot_complete"] and receipt["requested_evaluation_seeds"] == [1, 2, 3]
    assert receipt["requested_seed_status"] == {"1": "success", "2": "crash", "3": "failure"}
    assert receipt["frozen_bundle_sha256"] == report["identity"]["bundle_sha256"]
    assert receipt["evaluation_seed_universe"] == list(range(1, 51))
    assert receipt["official_report_sha256"] == pr.file_sha256(evaluation / "heldout_result.json")
    state = json.loads((evaluation / "heldout_state.json").read_text())
    assert set(state["seeds"]) == {"1", "2", "3"}
    deadline = state["phase_clock"]["evaluation"]["deadline"]
    assert pr.classify("heldout_incomplete", {"decision": "pass", "running_anywhere": []},
                       receipt)["outcome"] == "heldout_pilot_complete"
    # Resume: nothing re-run, deadline kept, exit code of the original runner (incomplete -> 1).
    cell.clock.now += 100
    assert pr.heldout_main(RT, cell.path, resume=True, resolve=None, clock=cell.clock) == 1
    assert len(heldout_calls(cell)) == 3
    state = json.loads((evaluation / "heldout_state.json").read_text())
    assert state["phase_clock"]["evaluation"]["deadline"] == deadline
    # The frozen identity is still checked: a changed frozen policy is refused.
    frozen = evaluation / "frozen/fix_code.py"
    frozen.write_text(frozen.read_text() + "# changed\n")
    assert pr.heldout_main(RT, cell.path, resume=True, resolve=None, clock=cell.clock) == 2
    assert len(heldout_calls(cell)) == 3


def test_heldout_requires_a_bound_pilot(cell, monkeypatch):
    finalized(cell, monkeypatch)
    assert pr.heldout_main(RT, cell.path, resume=False, resolve=None, clock=cell.clock) == 2
    assert heldout_calls(cell) == []


# --- root serial supervisor -------------------------------------------------------------


USERS = {t: (f"aspire-rs-articulation-{t}-r1", 30101 + i) for i, t in enumerate(pr.QUEUE_ORDER)}


class FakeHost(pr.Host):
    def __init__(self, deploy, outcomes):
        self.environ = {"LANG": "C.UTF-8", "HOME": "/root", "CODEX_HOME": "/root/.codex",
                        "OPENAI_API_KEY": "x", "PATH": "/root/bin"}
        self.deploy, self.outcomes = deploy, outcomes
        self.runs, self.launches, self.live = [], [], 0
        self.users = {u: SimpleNamespace(pw_uid=uid, pw_dir=str(deploy.homes / t))
                      for t, (u, uid) in USERS.items()}

    def geteuid(self):
        return 0

    def getpwnam(self, name):
        return self.users[name]

    def stat(self, path):
        for t, (user, uid) in USERS.items():
            if Path(path) == self.deploy.controls[t]:
                return SimpleNamespace(st_uid=uid, st_gid=uid, st_mode=0o40700)
        return SimpleNamespace(st_uid=0, st_gid=999, st_mode=0o40750)

    def run(self, argv, **kwargs):
        assert self.live == 0
        self.runs.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, "{}", "")

    def popen(self, argv, **kwargs):
        assert self.live == 0, "only one cell at a time"
        self.live += 1
        self.launches.append((argv, kwargs))
        host = self
        case = json.loads(Path(argv[argv.index("--case") + 1]).read_text())

        class Child:
            pid = 4242

            def wait(self):
                host.live -= 1
                pilot = Path(case["control"]) / "pilot"
                pilot.mkdir(exist_ok=True)
                (pilot / "pilot_identity.json").write_text("{}")
                outcome = host.outcomes.get(case["id"], "early_stopped")
                (pilot / "pilot_outcome.json").write_text(json.dumps({
                    "outcome": outcome, "permits_next_cell": outcome in pr.PERMITS_NEXT,
                    "launch_id": argv[argv.index("--launch-id") + 1],
                    "adapter_sha256": pr.file_sha256(Path(argv[6]))}))
                return 0 if outcome in pr.PERMITS_NEXT else 3
        return Child()

    def start_ticks(self, pid):
        return "1"

    def alive(self, pid, ticks):
        return False


@pytest.fixture
def deploy(tmp_path):
    d = SimpleNamespace(root=tmp_path, queue_dir=tmp_path / "deployment", runs=tmp_path / "runs",
                        homes=tmp_path / "users", operator=tmp_path / "operator",
                        sim=tmp_path / "runtime/aspire/sim", controls={})
    for path in (d.queue_dir, d.runs, d.homes, d.operator / "adapter"):
        path.mkdir(parents=True)
    d.adapter = d.operator / "adapter/pilot_runner.py"
    shutil.copy(pr.ADAPTER, d.adapter)
    cells = []
    for t in pr.QUEUE_ORDER:
        user, uid = USERS[t]
        control = d.runs / f"{t}_sam2articulation_r1"
        control.mkdir()
        d.controls[t] = control
        case = {"id": control.name, "task": pr.TASKS[t], "control": str(control), "sim": str(d.sim),
                "runtime_manifest_sha256": "rt", "claude_config_dir": str(control / "claude-config"),
                "model_service": {"server_manifest": str(d.queue_dir / "model.json")},
                "libero_config_path": "/frozen/libero-config"}
        (d.queue_dir / f"{t}.case.json").write_text(json.dumps(case))
        cells.append({"task": t, "cell": control.name, "case": str(d.queue_dir / f"{t}.case.json"),
                      "user": user, "uid": uid, "home": str(d.homes / t), "control": str(control),
                      "status": "pending"})
    (d.queue_dir / "model.json").write_text(json.dumps({"env": {"LD_LIBRARY_PATH": "/compat"}}))
    d.queue = d.queue_dir / "queue.json"
    d.queue.write_text(json.dumps({
        "sim": str(d.sim), "runtime_manifest_sha256": "rt", "order": list(pr.QUEUE_ORDER),
        "concurrency": 1, "cells": cells, "full_comparison": False, "dlc_allocation": False,
        "no_generated_code_feedback_or_skills_shared": True}))
    return d


def supervise(deploy, host, resume=False):
    return pr.supervise(deploy.queue, deploy.operator, resume=resume, host=host, adapter=deploy.adapter)


def test_supervisor_runs_drawer_stove_wine_serially_as_isolated_users(deploy):
    (deploy.controls["drawer"] / "claude-config").mkdir()
    (deploy.controls["drawer"] / "prelaunch-route-probe-20261012").mkdir()
    host = FakeHost(deploy, {})
    assert supervise(deploy, host) == 0
    users = [argv[2] for argv, _ in host.launches]
    assert users == [USERS[t][0] for t in pr.QUEUE_ORDER]
    caches = set()
    for (argv, kw), t in zip(host.launches, pr.QUEUE_ORDER):
        assert argv[:7] == [pr.RUNUSER, "-u", USERS[t][0], "--", pr.CELL_PYTHON, "-B", str(deploy.adapter)]
        assert argv[argv.index("--case") + 1] == str(deploy.queue_dir / f"{t}.case.json")
        assert "--resume" not in argv and kw["start_new_session"] and kw["cwd"] == str(deploy.sim)
        env = kw["env"]
        assert not {"HOME", "CODEX_HOME", "OPENAI_API_KEY"} & set(env)
        assert env["NUMBA_CACHE_DIR"] == str(deploy.homes / t / ".cache/aspire-numba")
        assert env["LD_LIBRARY_PATH"] == "/compat" and env["PYTHONDONTWRITEBYTECODE"] == "1"
        caches.add(env["NUMBA_CACHE_DIR"])
    assert len(caches) == 3
    # Preflight before every launch, as the same cell user.
    assert [a[2] for a, _ in host.runs] == users and all(a[-1] == "preflight" for a, _ in host.runs)
    status = json.loads((deploy.operator / "supervisor/status.json").read_text())
    assert status["state"] == "finished"
    assert all(c["state"] == "permitted" for c in status["cells"].values())
    assert (deploy.controls["drawer"] / "prelaunch-route-probe-20261012").is_dir()
    for t in pr.QUEUE_ORDER:
        assert (deploy.operator / f"supervisor/logs/{t}_sam2articulation_r1/01.stdout.log").is_file()


def test_supervisor_halts_on_unexpected_outcome_and_resume_is_explicit(deploy):
    host = FakeHost(deploy, {"stove_sam2articulation_r1": "halted_screen_missing_evidence"})
    assert supervise(deploy, host) == 1
    assert [argv[2] for argv, _ in host.launches] == [USERS["drawer"][0], USERS["stove"][0]]
    status = json.loads((deploy.operator / "supervisor/status.json").read_text())
    assert status["state"] == "halted" and "missing_evidence" in status["reason"]
    with pytest.raises(pr.Refused, match="--resume"):
        supervise(deploy, host)
    host.outcomes.clear()
    assert supervise(deploy, host, resume=True) == 0
    stove = [argv for argv, _ in host.launches if argv[2] == USERS["stove"][0]]
    assert len(stove) == 2 and "--resume" in stove[1]  # resumed, never reinitialized
    assert sum(argv[2] == USERS["drawer"][0] for argv, _ in host.launches) == 1


def test_supervisor_refuses_unknown_control_artifacts(deploy):
    (deploy.controls["stove"] / "cell").mkdir()
    host = FakeHost(deploy, {})
    assert supervise(deploy, host) == 1
    assert [argv[2] for argv, _ in host.launches] == [USERS["drawer"][0]]
    assert "unrecognized" in json.loads((deploy.operator / "supervisor/status.json").read_text())["reason"]


def test_duplicate_live_supervisor_is_refused(deploy):
    host = FakeHost(deploy, {})
    directory = deploy.operator / "supervisor"
    directory.mkdir()
    with pr.supervisor_lock(directory):
        with pytest.raises(pr.Refused, match="another live supervisor"):
            supervise(deploy, host)
        with pytest.raises(pr.Refused, match="another live supervisor"):
            pr.start_tmux(deploy.queue, deploy.operator, resume=False, session="rs-v3-pilot",
                          host=host)
    assert host.launches == [] and host.runs == []


def test_queue_and_deployment_validation(deploy):
    host = FakeHost(deploy, {})
    queue = json.loads(deploy.queue.read_text())
    queue["cells"][0]["user"] = "bad user;rm"
    deploy.queue.write_text(json.dumps(queue))
    with pytest.raises(pr.Refused, match="invalid user name"):
        pr.validate_queue(host, deploy.queue)
    queue["cells"][0]["user"] = USERS["drawer"][0]
    queue["order"] = ["stove", "drawer", "wine"]
    deploy.queue.write_text(json.dumps(queue))
    with pytest.raises(pr.Refused, match="queue order"):
        pr.validate_queue(host, deploy.queue)
    queue["order"] = list(pr.QUEUE_ORDER)
    deploy.queue.write_text(json.dumps(queue))
    good = pr.validate_queue(host, deploy.queue)
    assert pr.deployment_errors(host, deploy.adapter, deploy.operator, good, deploy.queue) == []
    writable = FakeHost(deploy, {})
    writable.stat = lambda path: SimpleNamespace(st_uid=0, st_gid=999, st_mode=0o40775)
    assert pr.deployment_errors(writable, deploy.adapter, deploy.operator, good, deploy.queue)
    host.users[USERS["wine"][0]].pw_dir = "/elsewhere"
    with pytest.raises(pr.Refused, match="passwd HOME"):
        pr.validate_queue(host, deploy.queue)


def test_numba_cache_check_is_pure_and_refuses_frozen_trees(tmp_path):
    repo = tmp_path / "runtime/aspire/sim"
    repo.mkdir(parents=True)
    home = tmp_path / "home"
    home.mkdir()
    out = pr.numba_cache_check(str(home / ".cache/aspire-numba"), repo, Path(sys.executable))
    assert out["exists"] is False and out["writable_ancestor"] == str(home)
    assert not (home / ".cache").exists()  # nothing written by the check
    with pytest.raises(pr.Refused, match="immutable"):
        pr.numba_cache_check(str(repo / "cache"), repo, Path(sys.executable))
    with pytest.raises(pr.Refused, match="absolute"):
        pr.numba_cache_check(None, repo, Path(sys.executable))
