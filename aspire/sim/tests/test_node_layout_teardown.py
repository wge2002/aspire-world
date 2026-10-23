"""CC_TASK_2: a failed unit must leave nothing running, and the node must exit.

p4 node1 (2026-10-17): Qwen TP2 hit an NCCL/CUDA fault, the supervisor wrote
`node_finished_with_failures` at 02:18:12, and the DLC job stayed Running for
8.5 h. The APIServer (the group leader the supervisor owned) exited cleanly; its
TP workers, orphaned when EngineCore was SIGKILLed and blocked in a collective
whose peer was gone, never logged a shutdown. The old teardown sent one SIGTERM
per group and escalated to SIGKILL only if the LEADER did not exit. Here a
TERM-ignoring orphan in the service's session stands in for such a worker.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

import psutil
import pytest

ROOT = Path(__file__).resolve().parents[1]
LAYOUT = ROOT / "scripts/common/node_layout"
sys.path[:0] = [str(LAYOUT), str(LAYOUT.parent), str(ROOT / "tests")]
import unit_layout as layout  # noqa: E402
import unit_supervisor as supervisor  # noqa: E402
from native_cc_runtime import stop_processes, stop_sessions  # noqa: E402
from test_node_layout import FakeHooks, FakeService, make_cells  # noqa: E402

#: A leader that starts a TERM-ignoring child, writes its pid, then exits on
#: TERM itself — the child is orphaned and still in the leader's session.
STUBBORN_LEADER = r"""
import os, signal, subprocess, sys, time
signal.signal(signal.SIGTERM, lambda *_: os._exit(0))
# The child reports its pid only once it ignores TERM, so the fixture is ready.
subprocess.Popen([sys.executable, "-c",
    "import os,signal,sys,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
    "open(sys.argv[1], 'w').write(str(os.getpid())); time.sleep(600)", sys.argv[1]])
time.sleep(600)
"""


def stubborn_service(tmp_path, name="qwen"):
    pidfile = tmp_path / f"{name}.child"
    process = subprocess.Popen([sys.executable, "-c", STUBBORN_LEADER, str(pidfile)],
                               start_new_session=True)
    deadline = time.monotonic() + 20
    while not (pidfile.exists() and pidfile.read_text()):
        assert time.monotonic() < deadline
        time.sleep(.05)
    return process, int(pidfile.read_text())


def alive(pid):
    try:
        return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


def test_old_group_term_leaves_an_orphaned_worker_running(tmp_path):
    process, child = stubborn_service(tmp_path)
    try:
        stop_processes([process])
        process.wait(timeout=15)  # The leader exits: the old code saw nothing wrong.
        time.sleep(1)
        assert alive(child)
    finally:
        os.killpg(process.pid, 9)


def test_session_teardown_kills_the_orphan_and_verifies_empty(tmp_path):
    process, child = stubborn_service(tmp_path)
    started = time.monotonic()
    report = stop_sessions([process], grace=2, kill_wait=5)
    assert time.monotonic() - started < 20
    assert not alive(child) and process.poll() is not None
    assert report["verified_empty"] and report["survivors"] == []
    assert child in [p["pid"] for p in report["sigkilled"]]


def test_session_teardown_ignores_processes_outside_its_sessions(tmp_path):
    outsider = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                                start_new_session=True)
    try:
        process, child = stubborn_service(tmp_path)
        stop_sessions([process], grace=1, kill_wait=5)
        assert alive(outsider.pid) and not alive(child)
    finally:
        outsider.kill()
        outsider.wait()


class StubbornHooks(FakeHooks):
    """Unit 0's qwen leaves a TERM-ignoring orphan; its SAM3 dies while a cell runs."""

    def __init__(self, tmp, **kw):
        super().__init__(tmp, fail_unit=0, **kw)
        self.orphans = []

    def start_services(self, unit):
        services = super().start_services(unit)
        if unit.unit.index == 0:
            (self.tmp / "stub").mkdir(exist_ok=True)
            process, child = stubborn_service(self.tmp / "stub", "qwen")
            self.orphans.append(child)
            unit.processes.append(process)
        return services

    def requeue(self, root, cell, record):
        return layout.infra_requeue(root, cell, record)


def run(tmp_path, hooks, gpus="0,1,2,3", names=("c0", "c1"), grace=None):
    root = tmp_path / "root"
    cells = make_cells(root, list(names))
    node = supervisor.Node(root=root, cells=cells, units=layout.units(gpus), hooks=hooks,
                           worker="worker-node0", status_path=root / "status.json",
                           node_stop_at=time.monotonic() + 3600, cell_timeout=60, mode="full")
    node.terminal = "ok"
    if grace is not None:
        node.unit_stop_grace = grace
    return root, node, node.run()


def test_a_failed_unit_leaves_no_process_and_records_its_teardown(tmp_path):
    hooks = StubbornHooks(tmp_path)
    root, node, code = run(tmp_path, hooks)
    assert code == 1
    assert hooks.orphans and not any(alive(pid) for pid in hooks.orphans)
    status = json.loads((root / "status.json").read_text())
    assert status["state"] == "node_finished_with_failures"
    teardown = status["units"][0]["teardown"]
    assert teardown["verified_empty"] and teardown["survivors"] == []
    assert hooks.orphans[0] in [p["pid"] for p in teardown["sigkilled"]]


def test_node_exits_a_bounded_time_after_a_unit_whose_thread_hangs(tmp_path):
    class HangsAfterFailure(FakeHooks):
        def boundary_check(self, unit):
            unit.fail("service failure: synthetic")
            time.sleep(3600)  # Never returns: the unit thread is stuck.

    root, node, code = run(tmp_path, HangsAfterFailure(tmp_path), grace=2)
    assert code == 1
    status = json.loads((root / "status.json").read_text())
    assert status["abandoned_units"] == [0] and status["failed_units"] == [0]


def test_final_sweep_kills_every_session_the_node_started(tmp_path):
    process, child = stubborn_service(tmp_path)
    result = supervisor.final_sweep([process])
    assert result["verified_empty"] and not alive(child) and not alive(process.pid)


def test_supervisor_process_exits_even_with_a_non_daemon_thread_left(tmp_path):
    script = tmp_path / "entry.py"
    script.write_text(
        "import sys, threading, time\n"
        f"sys.path[:0] = [{str(LAYOUT)!r}, {str(LAYOUT.parent)!r}]\n"
        "import unit_supervisor as s\n"
        "s.main = lambda argv=None: (threading.Thread(target=time.sleep, args=(3600,)).start(), 1)[1]\n"
        "s.run_and_exit()\n")
    started = time.monotonic()
    proc = subprocess.run([sys.executable, str(script)], timeout=60)
    assert proc.returncode == 1 and time.monotonic() - started < 30


# ---- infrastructure requeue ------------------------------------------------------


def staged_cell(root, name):
    cell = root / name
    for relative in layout.STAGED_EMPTY_DIRS:
        (cell / relative).mkdir(parents=True)
    for staged in ("case.json", "worker-prompt.md", "runtime-manifest.json"):
        (cell / staged).write_text(json.dumps({"id": name}))
    return cell


def interrupted(root, cell):
    """What an interrupted full cell leaves: run state, outputs, a ledger, a claim."""
    (root / "queue/claims" / cell.name).mkdir(parents=True)
    (root / "queue/claims" / cell.name / "claim.json").write_text("{}")
    (root / "queue/claims" / cell.name / "result.json").write_text("{}")
    for name in ("campaign_state.json", "queue-result.json", "slot-case.json", "probe.json"):
        (cell / name).write_text("{}")
    ledger = cell / "outputs/libero_fix_loop/suite/task/development_state.json"
    ledger.parent.mkdir(parents=True)
    ledger.write_text(json.dumps({"trials": [{"seed": 51, "status": "complete"}]}))
    (cell / "native/config/settings.json").write_text("{}")


def test_infra_requeue_archives_the_attempt_and_releases_the_claim(tmp_path):
    cell = staged_cell(tmp_path, "stove")
    interrupted(tmp_path, cell)
    result = layout.infra_requeue(tmp_path, cell, {"state": "infrastructure"})
    assert result["requeued"] and result["attempt"] == 1
    archive = Path(result["archive"])
    # Nothing deleted, nothing of the interrupted attempt left in the cell.
    assert (archive / "cell/outputs/libero_fix_loop/suite/task/development_state.json").is_file()
    assert (archive / "claim/claim.json").is_file()
    assert not layout.started(cell) and not (tmp_path / "queue/claims/stove").exists()
    assert sorted(p.name for p in cell.iterdir()) == ["case.json", "native", "outputs",
                                                      "runtime-manifest.json", "worker-prompt.md"]
    assert list((cell / "outputs").iterdir()) == [cell / "outputs/working_codes"]
    assert list((cell / "native/config").iterdir()) == []
    # The archive is outside every cell: no later read of the cell reaches it.
    assert not archive.resolve().is_relative_to(cell.resolve())
    # Claimable again, and claimed fresh.
    unit = layout.units("0,1,2,3")[0]
    assert layout.claim_next(tmp_path, [cell], "w", layout.slot_binding(unit, unit.slots[0])) == cell


def test_infra_requeue_is_capped(tmp_path):
    cell = staged_cell(tmp_path, "stove")
    for attempt in range(layout.INFRA_REQUEUE_CAP):
        interrupted(tmp_path, cell)
        assert layout.infra_requeue(tmp_path, cell, {})["requeued"]
    interrupted(tmp_path, cell)
    result = layout.infra_requeue(tmp_path, cell, {})
    assert not result["requeued"] and "cap" in result["reason"]
    assert (tmp_path / "queue/claims/stove").is_dir() and layout.started(cell)


def test_a_cell_interrupted_by_its_unit_is_rerun_fresh_by_another_slot(tmp_path):
    # Unit 0 dies under its first cells while unit 1's cells still run (the p4
    # node2 shape: one slot busy, the queue otherwise empty). Unit 1's slots then
    # pick up the requeued cells, which run once more from a fresh cell directory.
    hooks = StubbornHooks(tmp_path, driver_seconds=8)
    names = [f"cell{i}" for i in range(4)]
    root, node, code = run(tmp_path, hooks, gpus=list(range(8)), names=names)
    infra = [r for r in node.records if r["state"] == "infrastructure"]
    assert infra and all(r["requeue"]["requeued"] for r in infra)
    ok = {r["cell"] for r in node.records if r["state"] == "ok"}
    assert ok == set(names)
    for record in infra:
        archive = root / "queue/infra-archive" / record["cell"] / "1"
        assert (archive / "requeue.json").is_file() and (archive / "claim/claim.json").is_file()
    status = json.loads((root / "status.json").read_text())
    assert set(status["requeued"]) == {r["cell"] for r in infra}
    assert not any(alive(pid) for pid in hooks.orphans)


def test_requeue_is_refused_when_cell_cleanup_was_not_verified(tmp_path):
    root = tmp_path / "root"
    (cell,) = make_cells(root, ["c0"])
    node = supervisor.Node(root=root, cells=[cell], units=layout.units("0,1,2,3"), hooks=FakeHooks(tmp_path),
                           worker="w", status_path=root / "s.json", node_stop_at=time.monotonic() + 60,
                           cell_timeout=10)
    result = node.requeue(cell, {"process_cleanup": {"verified_no_live_work": False}})
    assert not result["requeued"] and "not verified" in result["reason"]


def test_an_idle_slot_waits_for_a_requeue_only_while_its_sibling_runs(tmp_path):
    root = tmp_path / "root"
    a, b = make_cells(root, ["a", "b"])
    unit_layout = layout.units("0,1,2,3")[0]
    node = supervisor.Node(root=root, cells=[a, b], units=[unit_layout], hooks=FakeHooks(tmp_path),
                           worker="w", status_path=root / "s.json", node_stop_at=time.monotonic() + 3600,
                           cell_timeout=10)
    node.requeue_poll = .1
    unit = node.units[0]
    (root / "queue/claims/a").mkdir(parents=True)  # in flight elsewhere, no result yet
    (b / "campaign_state.json").write_text("{}")    # done
    slot_a, slot_b = unit_layout.slots
    # Sibling idle: nothing would keep this unit up, so the slot drains at once.
    assert node.wait_for_requeue(unit, slot_a) is False
    # Sibling running: wait, and claim as soon as the in-flight cell is requeued.
    unit.slots["b"] = {"state": "cell_running", "gpu": 3}
    got = []
    waiter = threading.Thread(target=lambda: got.append(node.wait_for_requeue(unit, slot_a)))
    waiter.start()
    time.sleep(.5)
    assert waiter.is_alive() and unit.slots["a"]["state"] == "waiting_for_requeue"
    (root / "queue/claims/a").rmdir()
    waiter.join(5)
    assert got == [True]
