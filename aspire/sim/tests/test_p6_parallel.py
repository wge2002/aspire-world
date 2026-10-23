"""CC_TASK_7 (p6): several held-out / sweep seeds of one cell at once, equivalent to serial.

Groups:
  P. process layer — `run_replay(interrupt=...)` from a worker thread ends like a
     signalled serial replay; `ReplayBatch` caps concurrency, owns SIGTERM/SIGINT,
     and caps CPU threads without touching the slot's device wiring;
  H. held-out — N=1 is byte-identical to the p5-frozen evaluator; N>1 writes the
     same ledger and report; resume, the orphan-directory refusal, a timeout or
     infrastructure row, `heldout_stop_on_infra`, a SIGTERM, a SIGKILL plus the
     supplement path, and the single ledger writer;
  S. batch sweep — opt-in only; the same rows as single-seed sweeps whatever N;
     admission of every seed before any runs; stops after an infrastructure row;
     in-flight rows are not "lost evidence"; prompt text and Bash envelope;
  C. cleanup — the supervisor's per-cell process tree removes every parallel
     replay, also after the evaluator was SIGKILLed;
  D. deadlines — the p6 overlay's held-out bound and the driver that uses it.

The held-out tests run the REAL evaluator and the REAL `run_replay`; only the
simulator is a small fake replay script. The sweep tests use the p5 budget
harness (model and simulator faked as in test_search_control.py).
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest

SIM = Path(__file__).resolve().parents[1]
for p in (SIM.parents[1], SIM, SIM / "scripts/libero", SIM / "scripts/common", SIM / "tests",
          SIM / "scripts/common/node_layout"):
    sys.path.insert(0, str(p))

import executable_world_profile as profile  # noqa: E402
import native_cc_trial_process as trial_process  # noqa: E402
import native_world_fixloop_state as ledger  # noqa: E402
import native_world_heldout as heldout  # noqa: E402
import native_world_protocol as protocol  # noqa: E402
from test_p5_budget import BudgetHarness, SEEDS  # noqa: E402

FIXTURES = SIM / "tests/fixtures/p6_parallel"
P3_SUPPORT = SIM / "docs/experiments/code-world-search-control-p3-20261013/support"
P4_SUPPORT = SIM / "docs/experiments/code-world-coverage-p4-20261016/support"
P5_SUPPORT = SIM / "docs/experiments/code-world-budget-p5-20261019/support"
P6_SUPPORT = SIM / "docs/experiments/code-world-budget-p5-20261019/parallel/support"
HELDOUT = list(range(1, 51))
ENV_KEYS = (*trial_process.THREAD_VARIABLES, "CUDA_VISIBLE_DEVICES", "MUJOCO_EGL_DEVICE_ID",
            "__EGL_VENDOR_LIBRARY_FILENAMES", "SAM3_SERVICE_URL", "GRASPNET_SERVICE_URL",
            "PYROKI_SERVICE_URL")


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- the fake simulator -----------------------------------------------------------

FAKE_REPLAY = r'''
import json, os, sys, time
from pathlib import Path
args = sys.argv[1:]
seed = int(args[args.index("--args.trial") + 1])
out = Path(args[args.index("--args.output-dir") + 1])
plan = json.loads(Path(os.environ["FAKE_PLAN"]).read_text()).get(str(seed), {})
def note(**fields):
    line = json.dumps({"seed": seed, "pid": os.getpid(), "time": time.time(), **fields}) + "\n"
    fd = os.open(os.environ["FAKE_LOG"], os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    os.write(fd, line.encode())
    os.close(fd)
note(event="start", sid=os.getsid(0), env={k: os.environ.get(k) for k in KEYS})
time.sleep(plan.get("sleep", 0.05))
outcome = plan.get("outcome", "success")
if outcome == "hang":
    time.sleep(3600)
note(event="end")
if outcome != "noresult":
    rc, ok = int(outcome == "crash"), int(outcome == "success")
    (out / "suite/task/model/run" / f"trial_{seed:02d}_sandboxrc_{rc}_reward_{ok:.3f}_taskcompleted_{ok}").mkdir(parents=True)
print(f"fake replay seed {seed} {outcome}")
sys.exit(plan.get("exit", 0))
'''.replace("KEYS", repr(ENV_KEYS))


class Cell:
    """A frozen condition-A cell whose replay is FAKE_REPLAY, evaluated by the real code."""

    def __init__(self, root: Path, plan: dict | None = None, **case):
        self.root, self.repo, self.control = root, root / "sim", root / "control"
        (self.repo / ".venv-libero/bin").mkdir(parents=True)
        (self.repo / ".venv-libero/bin/python3").symlink_to(sys.executable)
        for name in ("scripts/libero/replay_trial.py", "scripts/libero/runtime/replay_trial.py"):
            (self.repo / name).parent.mkdir(parents=True, exist_ok=True)
            (self.repo / name).write_text(FAKE_REPLAY)
        (self.repo / "env_configs/libero").mkdir(parents=True)
        (self.repo / "env_configs/libero/franka_libero_traced.yaml").write_text("suite: libero\n")
        self.case = {"id": "bowl_p6_t", "condition": "A", "sim": str(self.repo),
                     "control": str(self.control), "suite": "libero_goal_swap",
                     "task": "put_the_bowl_on_the_plate", "dev_seeds": list(range(51, 66)),
                     "max_steps": 4000, "trial_timeout": 30, "model": "qwen3.8-flash-next",
                     "env_config": "env_configs/libero/franka_libero_traced.yaml",
                     "cuda_visible_devices": "6", "egl_device_id": 6,
                     "egl_vendor_config": str(root / "egl.json"),
                     "sam3_url": "http://127.0.0.1:8114", "graspnet_url": "http://127.0.0.1:8115",
                     "pyroki_url": "http://127.0.0.1:8116", **case}
        task_dir = self.repo / "outputs/libero_fix_loop" / self.case["suite"] / self.case["task"]
        task_dir.mkdir(parents=True)
        policy = "get_observation()\nopen_gripper()\n"
        (task_dir / "fix_code.py").write_text(policy)
        bundle = {"policy": ledger.code_hash(policy)}
        digest = ledger.bundle_identity(bundle)
        (task_dir / "stage1_result.json").write_text(json.dumps(
            {"stage1_complete": True, "selected_bundle": bundle, "tested_bundles": {digest: {}},
             "selection": {"bundle_sha256": digest}}))
        self.control.mkdir()
        self.case_path = root / "case.json"
        self.case_path.write_text(json.dumps(self.case))
        self.plan_path, self.log = root / "plan.json", root / "fake.log"
        self.set_plan(plan or {})

    def set_plan(self, plan):
        self.plan_path.write_text(json.dumps({str(k): v for k, v in plan.items()}))

    def update(self, **case):
        self.case.update(case)
        self.case_path.write_text(json.dumps(self.case))

    @property
    def evaluation(self):
        return self.control / "heldout"

    def ledger(self):
        return json.loads((self.evaluation / "heldout_state.json").read_text())

    def report(self):
        return json.loads((self.evaluation / "heldout_result.json").read_text())

    def events(self, kind=None):
        if not self.log.exists():
            return []
        rows = [json.loads(line) for line in self.log.read_text().splitlines()]
        return [r for r in rows if kind is None or r["event"] == kind]

    def env(self):
        return {"FAKE_PLAN": str(self.plan_path), "FAKE_LOG": str(self.log)}

    def run(self, module=heldout, *, resume=False, entry=None):
        """The evaluator in this process (main thread), as the driver would start it."""
        argv = ["--case", str(self.case_path)] + (["--resume"] if resume else [])
        out = io.StringIO()
        with patch.dict(os.environ, self.env()), contextlib.redirect_stdout(out):
            if entry is not None:
                code = entry(argv)
            else:
                with patch.object(sys, "argv", ["native_world_heldout.py", *argv]):
                    code = module.main()
        return code, out.getvalue()

    def spawn(self, *, resume=False):
        """The evaluator as its own session, the way `run_heldout` starts it."""
        code = ("import sys; sys.path[:0] = [sys.argv[1], sys.argv[2]]; import native_world_heldout as h; "
                "sys.argv = ['native_world_heldout.py', '--case', sys.argv[3]] + sys.argv[4:]; "
                "raise SystemExit(h.main())")
        command = [sys.executable, "-c", code, str(SIM / "scripts/libero"), str(SIM / "scripts/common"),
                   str(self.case_path)] + (["--resume"] if resume else [])
        return subprocess.Popen(command, env={**os.environ, **self.env()}, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, start_new_session=True, text=True)


def normalized(cell: Cell, data):
    """Ledger/report text with the cell root and the finish times taken out."""
    text = json.dumps(data, indent=2, sort_keys=False).replace(str(cell.root), "<root>")
    data = json.loads(text)
    for row in (data.get("seeds") or {}).values():
        row["finished_at"] = "<t>"
    return data


def p5_evaluator():
    """The p5-frozen evaluator, running on the p5-frozen `run_replay`."""
    old_process = load(FIXTURES / "native_cc_trial_process_p5.py", "native_cc_trial_process_p5")
    module = load(FIXTURES / "native_world_heldout_p5.py", "native_world_heldout_p5")
    module.run_replay = old_process.run_replay
    return module


def overlap(events):
    """Most replays alive at once, from the fake's start/end notes."""
    moments = sorted([(e["time"], 1) for e in events if e["event"] == "start"]
                     + [(e["time"], -1) for e in events if e["event"] == "end"])
    alive = peak = 0
    for _, step in moments:
        alive += step
        peak = max(peak, alive)
    return peak


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:
        with open(f"/proc/{pid}/stat") as stat:
            return stat.read().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return False


def wait_for(predicate, timeout=60, step=0.05):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(step)
    return False


#: Seed-dependent outcomes and run times, so a parallel sweep finishes out of seed order.
MIXED = {s: {"sleep": round(0.02 * ((s * 7) % 5), 2),
             "outcome": "failure" if s % 7 == 0 else "crash" if s % 11 == 0 else "success"}
         for s in HELDOUT}


# --- P. process layer -----------------------------------------------------------------


def sleeper(tmp_path, name, seconds):
    directory = tmp_path / name
    directory.mkdir()
    return [sys.executable, "-c", f"import time; time.sleep({seconds})"], directory


def test_a_thread_replay_times_out_with_the_serial_error_text(tmp_path):
    command, serial_dir = sleeper(tmp_path, "serial", 30)
    serial = trial_process.run_replay(command, repo=tmp_path, env=dict(os.environ),
                                      directory=serial_dir, timeout=1)
    _, thread_dir = sleeper(tmp_path, "thread", 30)
    box = {}
    worker = threading.Thread(target=lambda: box.update(out=trial_process.run_replay(
        command, repo=tmp_path, env=dict(os.environ), directory=thread_dir, timeout=1,
        interrupt=trial_process.BatchInterrupt())))
    worker.start()
    worker.join(30)
    assert box["out"][1] == serial[1] and serial[1].startswith("TimeoutExpired: Command")
    assert box["out"][0] == serial[0] == -signal.SIGTERM
    for directory in (serial_dir, thread_dir):
        assert json.loads((directory / "lifecycle.json").read_text())["error"] == serial[1]


def test_without_interrupt_run_replay_is_the_p5_one(tmp_path, monkeypatch):
    old = load(FIXTURES / "native_cc_trial_process_p5.py", "native_cc_trial_process_p5_cmp")
    for module, name in ((old, "old"), (trial_process, "new")):
        command, directory = sleeper(tmp_path, name, 0)
        installed = []
        real = signal.signal
        monkeypatch.setattr(signal, "signal", lambda sig, h: (installed.append(sig), real(sig, h))[1])
        code, error = module.run_replay(command, repo=tmp_path, env=dict(os.environ),
                                        directory=directory, timeout=30)
        monkeypatch.setattr(signal, "signal", real)
        assert (code, error) == (0, "")
        assert installed == [signal.SIGTERM, signal.SIGINT, signal.SIGTERM, signal.SIGINT,
                             signal.SIGTERM, signal.SIGINT], name
        record = json.loads((directory / "lifecycle.json").read_text())
        assert sorted(record) == ["command", "error", "finished_at", "host", "pid", "proc_start_ticks",
                                  "process_exit_code", "started_at", "stdin"]


def test_a_signal_ends_every_batch_replay_like_a_serial_one(tmp_path):
    previous = signal.getsignal(signal.SIGTERM)
    pids, results = [], []
    with trial_process.ReplayBatch(3) as batch:
        assert signal.getsignal(signal.SIGTERM) is not previous
        for n in range(3):
            command, directory = sleeper(tmp_path, f"s{n}", 60)
            batch.submit(n, trial_process.run_replay, command, repo=tmp_path, env=dict(os.environ),
                         directory=directory, timeout=120)
        assert batch.free() == 0
        assert wait_for(lambda: all((tmp_path / f"s{n}/lifecycle.json").exists() for n in range(3)))
        pids = [json.loads((tmp_path / f"s{n}/lifecycle.json").read_text())["pid"] for n in range(3)]
        started = time.monotonic()
        os.kill(os.getpid(), signal.SIGTERM)
        while batch.running():
            results += batch.wait()
        assert time.monotonic() - started < 20
        assert batch.stopping and batch.free() == 0
    assert signal.getsignal(signal.SIGTERM) is previous
    assert sorted(k for k, _ in results) == [0, 1, 2]
    for _, (code, error) in results:
        assert code == -signal.SIGTERM
        assert error == ("TrialInterrupted: trial interrupted by signal 15; same frozen trial "
                         "requires infrastructure recovery")
    assert not any(alive(pid) for pid in pids)


def test_the_batch_never_runs_more_than_its_limit(tmp_path):
    log = tmp_path / "log"
    script = ("import os, sys, time; fd = os.open(sys.argv[1], os.O_WRONLY | os.O_APPEND | os.O_CREAT); "
              "os.write(fd, f'{time.time()} 1\\n'.encode()); time.sleep(0.3); "
              "os.write(fd, f'{time.time()} -1\\n'.encode())")
    pending, done = list(range(7)), []
    with trial_process.ReplayBatch(3) as batch:
        while pending or batch.running():
            while pending and batch.free():
                n = pending.pop(0)
                (tmp_path / f"r{n}").mkdir()
                batch.submit(n, trial_process.run_replay, [sys.executable, "-c", script, str(log)],
                             repo=tmp_path, env=dict(os.environ), directory=tmp_path / f"r{n}", timeout=30)
                assert batch.running() <= 3
            done += batch.wait()
    assert sorted(k for k, _ in done) == list(range(7)) and all(o == (0, "") for _, o in done)
    alive_now = peak = 0
    for line in sorted(log.read_text().split("\n")[:-1], key=lambda s: float(s.split()[0])):
        alive_now += int(line.split()[1])
        peak = max(peak, alive_now)
    assert 2 <= peak <= 3


def test_parallel_env_caps_threads_and_keeps_the_device_wiring():
    env = {"CUDA_VISIBLE_DEVICES": "6", "MUJOCO_EGL_DEVICE_ID": "6", "OMP_NUM_THREADS": "64", "X": "y"}
    capped = trial_process.parallel_env(env, 3)
    assert {k: capped[k] for k in trial_process.THREAD_VARIABLES} == dict.fromkeys(
        trial_process.THREAD_VARIABLES, "3")
    assert {k: v for k, v in capped.items() if k not in trial_process.THREAD_VARIABLES} == {
        "CUDA_VISIBLE_DEVICES": "6", "MUJOCO_EGL_DEVICE_ID": "6", "X": "y"}
    assert env["OMP_NUM_THREADS"] == "64"


@pytest.mark.parametrize("case, message", [
    ({"heldout_parallel": 0}, "heldout_parallel must be an integer 1..8"),
    ({"heldout_parallel": 9}, "heldout_parallel must be an integer 1..8"),
    ({"heldout_parallel": "4"}, "heldout_parallel must be an integer"),
    ({"sweep_parallel": 2.0, "prediction_contract": "p3-search"}, "sweep_parallel must be an integer"),
    ({"trial_threads": 0}, "trial_threads must be an integer 1..32"),
    ({"sweep_parallel": 4, "prediction_contract": "p2-runtime"}, "exists only under p3-search")])
def test_parallel_settings_are_validated(case, message):
    assert any(message in p for p in profile.parallel_errors(case))
    with pytest.raises(ledger.ProtocolError, match=message.split("..")[0]):
        protocol.parallel_settings(case, "heldout")


def test_absent_keys_are_the_serial_defaults():
    assert profile.parallel_errors({}) == []
    assert protocol.parallel_settings({}, "heldout") == (1, profile.DEFAULT_TRIAL_THREADS)
    assert protocol.parallel_settings({"sweep_parallel": 4, "trial_threads": 3,
                                       "prediction_contract": "p3-search"}, "sweep") == (4, 3)


# --- H. held-out ------------------------------------------------------------------------


def test_n1_is_byte_identical_to_the_p5_frozen_evaluator(tmp_path):
    old, new = Cell(tmp_path / "old", MIXED), Cell(tmp_path / "new", MIXED)
    code_old, out_old = old.run(p5_evaluator())
    code_new, out_new = new.run()
    assert code_old == code_new == 0
    assert normalized(old, old.ledger()) == normalized(new, new.ledger())
    assert (old.evaluation / "heldout_result.json").read_text().replace(str(old.root), "<root>") == \
        (new.evaluation / "heldout_result.json").read_text().replace(str(new.root), "<root>")
    assert out_old.replace(str(old.root), "<root>") == out_new.replace(str(new.root), "<root>")
    assert "execution" not in new.report() and "execution" not in new.ledger()
    for seed in HELDOUT:
        for name in ("command.json", "code.py", "replay.log"):
            a = (old.evaluation / f"seed_{seed:02d}" / name).read_text().replace(str(old.root), "<root>")
            b = (new.evaluation / f"seed_{seed:02d}" / name).read_text().replace(str(new.root), "<root>")
            assert a == b, (seed, name)
        assert sorted(json.loads((new.evaluation / f"seed_{seed:02d}/lifecycle.json").read_text())) == \
            sorted(json.loads((old.evaluation / f"seed_{seed:02d}/lifecycle.json").read_text()))
    # Serial: one replay at a time, and the child environment carries no thread cap.
    assert overlap(new.events()) == 1
    parent = {k: os.environ.get(k) for k in trial_process.THREAD_VARIABLES}
    for event in new.events("start"):
        assert {k: event["env"][k] for k in trial_process.THREAD_VARIABLES} == parent
    assert new.report()["counts"] == {"success": 39, "failure": 7, "crash": 4, "infrastructure_error": 0}


def test_n4_writes_the_serial_ledger_and_report(tmp_path):
    serial, parallel = Cell(tmp_path / "serial", MIXED), Cell(tmp_path / "parallel", MIXED,
                                                             heldout_parallel=4, trial_threads=3)
    assert serial.run()[0] == 0 and parallel.run()[0] == 0
    a, b = normalized(serial, serial.ledger()), normalized(parallel, parallel.ledger())
    assert a == b and list(b["seeds"]) == [str(s) for s in HELDOUT]
    report = parallel.report()
    assert report.pop("execution") == {"heldout_parallel": 4, "trial_threads": 3}
    assert json.loads(json.dumps(report).replace(str(parallel.root), "<root>")) == \
        json.loads(json.dumps(serial.report()).replace(str(serial.root), "<root>"))
    assert list(report["per_seed"]) == [str(s) for s in HELDOUT]
    # Really parallel, never above N, finished out of seed order.
    assert 2 <= overlap(parallel.events()) <= 4
    ends = [e["seed"] for e in parallel.events("end")]
    assert sorted(ends) == HELDOUT and ends != HELDOUT
    # Every replay: its own session, the slot's card, threads capped.
    starts = parallel.events("start")
    assert len({e["sid"] for e in starts}) == 50 and all(e["sid"] == e["pid"] for e in starts)
    for event in starts:
        assert {k: event["env"][k] for k in trial_process.THREAD_VARIABLES} == dict.fromkeys(
            trial_process.THREAD_VARIABLES, "3")
        assert (event["env"]["CUDA_VISIBLE_DEVICES"], event["env"]["MUJOCO_EGL_DEVICE_ID"]) == ("6", "6")
        assert event["env"]["__EGL_VENDOR_LIBRARY_FILENAMES"] == str(parallel.root / "egl.json")
        assert event["env"]["SAM3_SERVICE_URL"] == "http://127.0.0.1:8114"


def test_only_the_main_thread_writes_the_ledger_and_every_write_is_whole(tmp_path, monkeypatch):
    cell = Cell(tmp_path, MIXED, heldout_parallel=4)
    writes, real = [], heldout.atomic_json

    def recorded(path, data):
        real(path, data)
        writes.append((threading.current_thread() is threading.main_thread(),
                       json.loads(Path(path).read_text())))

    monkeypatch.setattr(heldout, "atomic_json", recorded)
    assert cell.run()[0] == 0
    assert len(writes) == 50 and all(main for main, _ in writes)
    sizes = [len(data["seeds"]) for _, data in writes]
    assert sizes == list(range(1, 51))
    for _, data in writes:
        assert list(data["seeds"]) == sorted(data["seeds"], key=int)
        assert set(data) == {"identity", "seeds"}


def test_resume_at_another_n_runs_only_the_missing_seeds(tmp_path):
    cell = Cell(tmp_path, MIXED)
    first = {s: MIXED[s] for s in range(1, 21)}
    cell.set_plan({**first, **{s: {"outcome": "noresult"} for s in range(21, 51)}})
    # A partial serial ledger: the first 20 rows, as an interrupted sweep leaves it.
    assert cell.run()[0] == 1
    data = cell.ledger()
    data["seeds"] = {k: v for k, v in data["seeds"].items() if int(k) <= 20}
    (cell.evaluation / "heldout_state.json").write_text(json.dumps(data))
    for seed in range(21, 51):
        shutil.rmtree(cell.evaluation / f"seed_{seed:02d}")
    cell.set_plan(MIXED)
    cell.log.unlink()
    with pytest.raises(SystemExit, match="pass --resume"):
        cell.run()
    cell.update(heldout_parallel=4)
    assert cell.run(resume=True)[0] == 0
    assert sorted({e["seed"] for e in cell.events("start")}) == list(range(21, 51))
    reference = Cell(tmp_path / "ref", MIXED)
    assert reference.run()[0] == 0
    assert normalized(cell, cell.ledger()) == normalized(reference, reference.ledger())


@pytest.mark.parametrize("parallel", [1, 4])
def test_a_seed_directory_without_a_row_is_refused_before_anything_runs(tmp_path, parallel):
    cell = Cell(tmp_path, MIXED, heldout_parallel=parallel)
    (cell.evaluation).mkdir()
    (cell.evaluation / "seed_37").mkdir()
    with pytest.raises(SystemExit, match="seed 37 has artifacts"):
        cell.run()
    # Serial runs the seeds before 37 first (unchanged); the batch checks every pending seed first.
    started = {e["seed"] for e in cell.events("start")}
    assert started == (set(range(1, 37)) if parallel == 1 else set())


@pytest.mark.parametrize("outcome, error", [("hang", "TimeoutExpired: Command"), ("noresult", "")])
def test_one_timed_out_or_lost_seed_is_an_infrastructure_row_and_the_rest_run(tmp_path, outcome, error):
    cell = Cell(tmp_path, {**MIXED, 9: {"outcome": outcome}}, heldout_parallel=4, trial_timeout=2)
    started = time.monotonic()
    assert cell.run()[0] == 1  # The plain evaluator continues, exactly as the serial one does.
    assert time.monotonic() - started < 60
    rows = cell.ledger()["seeds"]
    assert len(rows) == 50 and rows["9"]["status"] == "infrastructure_error"
    assert rows["9"]["error"].startswith(error)
    assert cell.report()["unusable_seeds"] == [9]
    assert all(rows[str(s)]["status"] != "infrastructure_error" for s in HELDOUT if s != 9)


@pytest.mark.parametrize("parallel", [1, 4])
def test_stop_on_infra_stops_new_seeds_and_records_the_running_ones(tmp_path, monkeypatch, parallel):
    stop = load(P3_SUPPORT / "heldout_stop_on_infra.py", f"p3_stop_on_infra_{parallel}")
    monkeypatch.setattr(heldout, "atomic_json", heldout.atomic_json)
    monkeypatch.setattr(heldout, "run_replay", heldout.run_replay)
    plan = {s: {"sleep": 0.6} for s in HELDOUT} | {6: {"sleep": 0.05, "outcome": "noresult"}}
    cell = Cell(tmp_path, plan, heldout_parallel=parallel)
    code, out = cell.run(entry=stop.main)
    report = json.loads(out)
    assert code == 1 and report["stopped_after_infrastructure_error"] is True
    assert report["stopped_on"]["seed"] == 6
    rows = cell.ledger()["seeds"]
    started = sorted({e["seed"] for e in cell.events("start")})
    # Every replay that started has its row; nothing after the stop started.
    assert sorted(int(k) for k in rows) == started
    assert all((cell.evaluation / f"seed_{s:02d}").exists() == (s in started) for s in HELDOUT)
    if parallel == 1:
        assert started == list(range(1, 7))
    else:
        # Seed 6 started in the second wave; at most the three seeds beside it were running.
        assert started[:6] == list(range(1, 7)) and len(started) <= 9
    assert [s for s, r in rows.items() if r["status"] == "infrastructure_error"] == ["6"]
    # The runtime entry's command rewrite reached every parallel replay too.
    for seed in started:
        command = json.loads((cell.evaluation / f"seed_{seed:02d}/lifecycle.json").read_text())["command"]
        assert "scripts/libero/runtime/replay_trial.py" in command


def test_sigterm_records_every_running_seed_and_leaves_no_orphan(tmp_path):
    cell = Cell(tmp_path, {s: {"sleep": 0.05 if s <= 8 else 30} for s in HELDOUT}, heldout_parallel=4)
    process = cell.spawn()
    assert wait_for(lambda: len(cell.events("start")) >= 12)
    time.sleep(0.5)
    started = time.monotonic()
    os.killpg(process.pid, signal.SIGTERM)  # What the driver's stop_group sends first.
    out, err = process.communicate(timeout=45)
    assert time.monotonic() - started < 30  # Inside the driver's 45 s GROUP_STOP_GRACE.
    assert process.returncode != 0 and "held-out sweep interrupted" in err
    rows = cell.ledger()["seeds"]
    running = sorted({e["seed"] for e in cell.events("start")} - set(range(1, 9)))
    assert len(running) == 4 and sorted(int(k) for k in rows) == list(range(1, 9)) + running
    for seed in running:
        assert rows[str(seed)]["status"] == "infrastructure_error"
        assert rows[str(seed)]["error"].startswith("TrialInterrupted: trial interrupted by signal 15")
    assert not any(alive(e["pid"]) for e in cell.events("start"))
    # No directory without a row: the plain resume continues with the rest.
    cell.set_plan({s: {"sleep": 0.02} for s in HELDOUT})
    assert cell.run(resume=True)[0] == 1  # The interrupted rows stay unusable, as serially.
    assert len(cell.ledger()["seeds"]) == 50


def test_sigkill_leaves_orphans_that_resume_refuses_and_the_supplement_finishes(tmp_path):
    import unit_supervisor as supervisor
    supplement = load(P4_SUPPORT / "heldout_supplement.py", "p4_heldout_supplement_p6")
    cell = Cell(tmp_path, {s: {"sleep": 0.05 if s <= 8 else 60} for s in HELDOUT}, heldout_parallel=4)
    process = cell.spawn()
    tree = supervisor.CellProcessTree(process)
    assert wait_for(lambda: len(cell.events("start")) >= 12)
    time.sleep(0.5)
    tree.collect()  # The supervisor polls this every 0.5 s while the cell runs.
    running = sorted({e["seed"] for e in cell.events("start")} - set(range(1, 9)))
    os.kill(process.pid, signal.SIGKILL)
    process.wait(timeout=10)
    pids = [e["pid"] for e in cell.events("start") if e["seed"] in running]
    assert all(alive(pid) for pid in pids)  # Own sessions: a SIGKILL of the parent orphans them.
    cleanup = tree.cleanup(grace=5)
    assert cleanup["verified_no_live_work"] and not any(alive(pid) for pid in pids)
    assert set(pids) <= set(cleanup["tracked_pids"])
    # Orphan directories without rows: the plain resume refuses at either N.
    for parallel in (1, 4):
        cell.update(heldout_parallel=parallel)
        with pytest.raises(SystemExit, match=f"seed {running[0]} has artifacts"):
            cell.run(resume=True)
    planned = supplement.plan(cell.evaluation)
    assert planned["orphan_dirs"] == running and planned["missing"] == list(range(9, 51))
    attempt = supplement.new_attempt(cell.evaluation)
    supplement.prepare(cell.evaluation, planned, attempt, tmp_path / "no-campaign-state.json")
    cell.set_plan({s: {"sleep": 0.02} for s in HELDOUT})
    cell.update(heldout_parallel=4)
    assert cell.run(resume=True)[0] == 0
    assert cell.report()["all_seeds_accounted"] and cell.report()["counts"]["success"] == 50
    assert sorted(p.name for p in (attempt / "superseded").iterdir()) == [f"seed_{s:02d}" for s in running]


def test_cleanup_reaches_every_parallel_replay_of_a_live_evaluator(tmp_path):
    import unit_supervisor as supervisor
    cell = Cell(tmp_path, {s: {"outcome": "hang"} for s in HELDOUT}, heldout_parallel=4)
    process = cell.spawn()
    tree = supervisor.CellProcessTree(process)
    assert wait_for(lambda: len(cell.events("start")) >= 4)
    tree.collect()
    pids = [e["pid"] for e in cell.events("start")]
    cleanup = tree.cleanup(grace=40)
    assert cleanup["verified_no_live_work"] and not any(alive(pid) for pid in pids)
    # The evaluator itself ended them (TERM to the driver root), recording each row.
    rows = cell.ledger()["seeds"]
    assert sorted(int(k) for k in rows) == sorted(e["seed"] for e in cell.events("start"))
    assert all(r["error"].startswith("TrialInterrupted") for r in rows.values())


# --- S. batch sweep -----------------------------------------------------------------------


#: Harnesses alive in one test, by repo: the patched `run_replay` is module-wide.
HARNESSES: dict = {}


def dispatch(command, *, repo, **kwargs):
    return HARNESSES[str(repo)].thread_replay(command, repo=repo, **kwargs)


class SweepHarness(BudgetHarness):
    """The p5 budget harness with a thread-safe replay that notes overlap and order."""

    def __init__(self, stack, sweep_parallel=None, delays=None, **kw):
        super().__init__(stack, **kw)
        if sweep_parallel is not None:
            self.case["sweep_parallel"] = sweep_parallel
            self.case["trial_threads"] = 3
        self.delays, self.notes, self.lock = delays or {}, [], threading.Lock()
        self.failing_infra = set()
        HARNESSES[str(self.repo)] = self
        stack.enter_context(patch.object(protocol, "run_replay", dispatch))

    def thread_replay(self, command, *, repo, env, directory, timeout, stdin_path=None, interrupt=None):
        seed = int(command[command.index("--args.trial") + 1])
        with self.lock:
            self.notes.append(("start", seed, time.monotonic(), Path(directory).parts[-3],
                               {k: env.get(k) for k in ("OMP_NUM_THREADS", "CUDA_VISIBLE_DEVICES")}))
        time.sleep(self.delays.get(seed, 0))
        with self.lock:
            out = self.fake_replay(command, repo=repo, env=env, directory=directory, timeout=timeout,
                                   stdin_path=stdin_path)
            self.notes.append(("end", seed, time.monotonic(), Path(directory).parts[-3], None))
        if seed in self.failing_infra:
            return None, "TimeoutExpired: synthetic"
        return out

    def batch(self, code, seeds="all"):
        self.current = Path(code).name
        return protocol.run_sweep_batch(self.case, self.repo, self.state, seeds, Path(code),
                                        self.task_dir / "initial_world_program.py", None)

    def rows(self):
        text = json.dumps(self.state.data["trials"]).replace(str(self.task_dir.parents[4]), "<root>")
        return json.loads(text)


@pytest.fixture
def sweeping():
    with contextlib.ExitStack() as stack:
        yield lambda **kw: SweepHarness(stack, **kw)


#: Later seeds finish first inside a wave.
DELAYS = {s: 0.02 * (66 - s) for s in SEEDS}


def history(h):
    v0 = h.to_phase_b(passing_v0=(51, 53, 57, 60, 61))
    h.crashing["fix0.py"] = {55, 64}
    return v0


def test_the_batch_is_an_opt_in_of_the_case(sweeping):
    h = sweeping()
    v0 = history(h)
    with pytest.raises(ledger.ProtocolError, match="opt-in of the case"):
        h.batch(v0)
    assert [r["phase"] for r in h.state.data["trials"]].count("sweep") == 0


@pytest.mark.parametrize("parallel", [1, 4])
def test_the_batch_writes_the_rows_of_single_seed_sweeps(sweeping, parallel):
    serial, batch = sweeping(), sweeping(sweep_parallel=parallel, delays=DELAYS)
    for h in (serial, batch):
        v0 = history(h)
    serial.sweep(serial.task_dir / "fix0.py")
    result = batch.batch(batch.task_dir / "fix0.py")
    assert result["status"] == "complete" and result["stopped"] is None and result["not_run"] == []
    assert result["seeds"] == list(range(52, 66)) and result["coverage"]["complete"]
    assert serial.rows() == batch.rows()
    # Then a version, swept by batch, still matches its single-seed twin (budget, modes, reports).
    for h in (serial, batch):
        code, record = h.version("v1.py", "drop", [52, 54], passing=(51, 52, 54), sweep=False)
    serial.sweep(serial.task_dir / "v1.py")
    assert batch.batch(batch.task_dir / "v1.py")["status"] == "complete"
    assert serial.rows() == batch.rows()
    assert json.dumps(serial.state.budget_status()).replace(str(serial.task_dir), "<task>") == \
        json.dumps(batch.state.budget_status()).replace(str(batch.task_dir), "<task>")
    starts = [n for n in batch.notes if n[0] == "start" and n[3] == "sweep"]
    peak = alive_now = 0
    for kind, *_ in sorted(batch.notes, key=lambda n: n[2]):
        alive_now += 1 if kind == "start" else -1
        peak = max(peak, alive_now)
    assert peak == 1 if parallel == 1 else 2 <= peak <= parallel
    if parallel > 1:
        assert {n[4]["OMP_NUM_THREADS"] for n in starts} == {"3"}
        ends = [n[1] for n in batch.notes if n[0] == "end" and n[3] == "sweep"]
        assert len(ends) == 28 and ends[:14] != sorted(ends[:14])  # Finished out of order...
        settled = [r["seed"] for r in batch.state.records("sweep")]
        assert settled[:14] == sorted(settled[:14])  # ...and settled in seed order.
    assert {n[4]["CUDA_VISIBLE_DEVICES"] for n in starts} == {batch.case["cuda_visible_devices"]}


def test_every_seed_is_admission_checked_before_any_runs(sweeping):
    h = sweeping(sweep_parallel=4)
    v0 = history(h)
    h.sweep(v0, seeds=[54])
    before = len(h.state.data["trials"])
    with pytest.raises(ledger.ProtocolError, match="seed 54 is already covered"):
        h.batch(v0, seeds=[52, 53, 54, 55])
    assert len(h.state.data["trials"]) == before
    with pytest.raises(ledger.ProtocolError, match="untested bytes cannot be swept"):
        h.batch(h.policy("untested.py", "pull", "u"))
    # `all` is exactly the candidate's sweepable seeds.
    result = h.batch(v0)
    assert result["seeds"] == [s for s in range(52, 66) if s != 54]


def test_an_infrastructure_row_stops_new_seeds_and_settles_the_running_ones(sweeping):
    h = sweeping(sweep_parallel=4, delays={s: 0.05 for s in SEEDS})
    v0 = history(h)
    h.failing_infra = {54}
    result = h.batch(v0)
    assert result["status"] == "incomplete" and "infrastructure error on seed 54" in result["stopped"]
    ran = [r["seed"] for r in result["records"]]
    assert ran == list(range(52, 52 + len(ran))) and 54 in ran and len(ran) <= 4 + 2
    assert result["not_run"] == [s for s in range(52, 66) if s not in ran]
    statuses = {r["seed"]: r["status"] for r in h.state.records("sweep")}
    assert statuses[54] == "infrastructure_error"
    assert all(v != "running" for v in statuses.values())
    # The next batch re-runs the lost seed with the rest (it executed nothing usable).
    h.failing_infra = set()
    again = h.batch(v0)
    assert again["status"] == "complete" and again["seeds"][0] == 54 and again["coverage"]["complete"]


def test_rows_in_flight_are_not_lost_evidence_but_other_running_rows_are(sweeping):
    h = sweeping(sweep_parallel=4)
    v0 = history(h)
    digest = h.digest("fix0.py")
    bundle = {"policy": ledger.code_hash(v0.read_text()),
              "world": ledger.code_hash((h.task_dir / "initial_world_program.py").read_text())}
    row = h.state.begin_trial("sweep", 52, bundle, {})
    with pytest.raises(ledger.ProtocolError, match="unresolved infrastructure evidence"):
        h.state.validate_admission("sweep", 53, digest)
    h.state.validate_admission("sweep", 53, digest, in_flight=frozenset({row["directory"]}))
    other = h.state.begin_trial("sweep", 53, bundle, {}, in_flight=frozenset({row["directory"]}))
    with pytest.raises(ledger.ProtocolError, match="unresolved infrastructure evidence"):
        h.state.validate_admission("sweep", 54, digest, in_flight=frozenset({row["directory"]}))
    assert other["status"] == "running"


def test_changed_candidate_files_stop_the_batch(sweeping):
    h = sweeping(sweep_parallel=1)
    v0 = history(h)
    real = h.thread_replay

    def editing(command, **kwargs):
        out = real(command, **kwargs)
        v0.write_text(v0.read_text() + "# edited mid-batch\n")
        return out

    with patch.object(h, "thread_replay", editing):
        result = h.batch(v0)
    assert result["stopped"] == "the candidate files changed during the batch"
    assert [r["seed"] for r in result["records"]] == [52]


def test_batch_cli_flags(tmp_path, sweeping, capsys):
    h = sweeping(sweep_parallel=2)
    history(h)
    h.case_path.write_text(json.dumps(h.case))
    base = ["native_world_protocol.py", "--case", str(h.case_path), "trial", "--phase"]
    for argv, message in ((base + ["repair", "--seeds", "all"], "only `--phase sweep`"),
                          (base + ["sweep", "--seeds", "52,x"], "comma-separated seeds")):
        with patch.object(sys, "argv", argv):
            assert protocol.main() == 2
        assert message in capsys.readouterr().out
    for argv, message in ((base + ["sweep"], "the following arguments are required: --seed"),
                          (base + ["sweep", "--seed", "52", "--seeds", "all"], "pass one of them")):
        with patch.object(sys, "argv", argv), pytest.raises(SystemExit):
            protocol.main()
        assert message in capsys.readouterr().err
    with patch.object(sys, "argv", base + ["sweep", "--seeds", "52,53", "--code",
                                           str(h.task_dir / "fix0.py"), "--world-program",
                                           str(h.task_dir / "initial_world_program.py")]):
        h.current = "fix0.py"
        assert protocol.main() == 0
    out = json.loads(capsys.readouterr().out)
    assert out["batch"] and out["seeds"] == [52, 53] and out["sweep_parallel"] == 2


def test_the_batch_paragraph_is_rendered_only_with_the_opt_in():
    from test_prediction_contract import executable_case
    case = {"condition": "C", "profile": "judgment", "dev_seeds": SEEDS,
            **executable_case(prediction_contract="p3-search"), "development_budget": "aspire-v1"}
    plain = profile.section(case)
    batched = profile.section({**case, "sweep_parallel": 4})
    paragraph = profile.batch_sweep_contract({**case, "sweep_parallel": 4})
    assert "--seeds all" not in plain and batched.count(paragraph) == 1
    assert batched.replace(paragraph, "") == plain
    assert "up to 4 seeds at once" in paragraph and "4 trial lengths" in paragraph


def test_the_p6_bash_envelope_covers_a_whole_batch():
    p3 = load(P3_SUPPORT / "runtime_limits.py", "p3_runtime_limits")
    p6 = load(P6_SUPPORT / "runtime_limits.py", "p6_runtime_limits")
    case = {"trial_timeout": 900, "dev_seeds": SEEDS}
    assert p6.command_seconds(case) == p3.command_seconds(case) == 2520
    assert p6.command_seconds({**case, "sweep_parallel": 4}) == 1200 + 4 * 1020 + 420
    assert p6.command_seconds({**case, "sweep_parallel": 1}) == 1200 + 14 * 1020 + 420
    assert p6.command_seconds({**case, "sweep_parallel": 8}) == 1200 + 2 * 1020 + 420
    error = p6.trial_timeout_error({"tool_name": "Bash", "tool_input": {
        "command": "python native_world_protocol.py trial --phase sweep --seeds all",
        "timeout": 2520 * 1000}}, {**case, "sweep_parallel": 4})
    assert error and "5700s envelope" in error


# --- D. deadlines ---------------------------------------------------------------------------


def test_the_heldout_bound_is_taken_over_parallel_slots():
    p5 = load(P5_SUPPORT / "full_deadlines.py", "p5_full_deadlines_p6")
    p6 = load(P6_SUPPORT / "full_deadlines.py", "p6_full_deadlines")
    for seeds in (0, 1, 6, 49, 50):
        assert p6.heldout_deadline(seeds, 900) == p5.heldout_deadline(seeds, 900)
        assert p6.heldout_deadline(seeds, 900, 1) == p5.heldout_deadline(seeds, 900)
    assert p6.heldout_deadline(50, 900, 2) == 120 + 25 * 1020 == 25620
    assert p6.heldout_deadline(50, 900, 4) == 120 + 13 * 1020 == 13380
    assert p6.heldout_deadline(6, 900, 4) == 120 + 2 * 1020
    for bad in ((-1, 900, 1), (5, 0, 1), (5, 900, 0)):
        with pytest.raises(ValueError):
            p6.heldout_deadline(*bad)
    assert p6.HELDOUT == p6.heldout_deadline(50, 900, p6.HELDOUT_PARALLEL)
    assert p6.TOTAL == (p6.SERVICE_STARTUP + p6.NATIVE_COMPAT + p6.DEVELOPMENT + p6.HELDOUT
                        + p6.FINALIZE_MARGIN)
    for name in ("TRIAL_TIMEOUT", "SERVICE_STARTUP", "NATIVE_COMPAT", "DEVELOPMENT", "FINALIZE_MARGIN",
                 "HELDOUT_SEED_MARGIN", "HELDOUT_SWEEP_OVERHEAD"):
        assert getattr(p5, name) == getattr(p6, name), name
    assert p6.check_case({"heldout_parallel": p6.HELDOUT_PARALLEL}) == []
    assert p6.check_case({}) and p6.check_case({"heldout_parallel": 2})


def test_list_scheduling_never_exceeds_the_bound():
    """Simulate the evaluator's greedy start rule with adversarial per-seed times <= T."""
    import random
    p6 = load(P6_SUPPORT / "full_deadlines.py", "p6_full_deadlines_sim")
    rng = random.Random(7)
    for _ in range(500):
        seeds, parallel, bound = rng.randint(1, 50), rng.randint(1, 8), 1020
        times = [rng.choice([bound, rng.uniform(0, bound)]) for _ in range(seeds)]
        slots = [0.0] * parallel
        for duration in times:
            slot = min(range(parallel), key=slots.__getitem__)
            slots[slot] += duration
        assert max(slots) <= -(-seeds // parallel) * bound + 1e-6
        assert max(slots) + p6.HELDOUT_SWEEP_OVERHEAD <= p6.heldout_deadline(seeds, 900, parallel)


@pytest.fixture
def p6_driver(tmp_path, monkeypatch):
    """The p6 driver from a support dir overlaid p3 <- p4 <- p5 <- p6."""
    support = tmp_path / "support"
    support.mkdir()
    for layer in (P3_SUPPORT, P4_SUPPORT, P5_SUPPORT, P6_SUPPORT):
        for source in layer.iterdir():
            if source.suffix == ".py":
                (support / source.name).unlink(missing_ok=True)
                (support / source.name).write_bytes(source.read_bytes())
    monkeypatch.syspath_prepend(str(support))
    for source in support.iterdir():
        monkeypatch.delitem(sys.modules, source.stem, raising=False)
    return load(support / "run_two_task_cell.py", "p6_driver")


def test_the_p6_driver_derives_the_bound_at_the_case_parallelism(p6_driver):
    args = SimpleNamespace(deadline_heldout=None)
    deadlines = p6_driver.full_deadlines
    assert p6_driver.heldout_deadline(args, {"trial_timeout": 900}, HELDOUT) == 51120
    assert p6_driver.heldout_deadline(args, {"trial_timeout": 900, "heldout_parallel": 4}, HELDOUT) == \
        deadlines.heldout_deadline(50, 900, 4) == deadlines.HELDOUT
    assert p6_driver.heldout_deadline(args, {"trial_timeout": 900, "heldout_parallel": 4},
                                      list(range(45, 51))) == 120 + 2 * 1020
    assert p6_driver.heldout_deadline(SimpleNamespace(deadline_heldout=77), {"trial_timeout": 900}, HELDOUT) == 77


def test_the_p6_driver_preserves_heldout_helpers_and_declares_closeout_changes():
    """Task 8 changes development orchestration; held-out/supplement helpers stay pinned."""
    import ast
    def functions(path):
        text = path.read_text()
        return {n.name: ast.get_source_segment(text, n) for n in ast.parse(text).body
                if isinstance(n, ast.FunctionDef)}
    p5 = functions(P5_SUPPORT / "run_two_task_cell.py")
    p6 = functions(P6_SUPPORT / "run_two_task_cell.py")
    assert set(p5) == set(p6)
    assert {name for name in p5 if p5[name] != p6[name]} == {
        "heldout_deadline", "main", "require_development_possible"}
    assert 'case.get("heldout_parallel", 1)' in p6['heldout_deadline']
    assert 'full_deadlines.check_case(case)' in p6['main']
