"""Node layout 2026-10-15: units, ports, slots, model argv, queue claims, unit isolation."""
from __future__ import annotations

import json
import multiprocessing
from pathlib import Path
import subprocess
import sys
import threading
import time

import pytest

ROOT = Path(__file__).resolve().parents[1]
LAYOUT = ROOT / "scripts/common/node_layout"
sys.path[:0] = [str(LAYOUT), str(LAYOUT.parent)]
import unit_layout as layout  # noqa: E402
import unit_supervisor as supervisor  # noqa: E402
from native_cc_runtime import Service  # noqa: E402

FROZEN_EAGER = Path("/mnt/home/gewang/experiments/code-world-search-control-p3-20261013"
                    "/launch/reference/model-server.json")


def eager_reference():
    """A synthetic copy of the frozen p3 eager config (paths are placeholders)."""
    return {"argv": ["/py", "/vllm", "serve", "/models/q", "--host", "127.0.0.1", "--port", "8121",
                     "--served-model-name", "qwen3.8-flash-next", "--tensor-parallel-size", "4",
                     "--enable-expert-parallel", "--moe-backend", "triton",
                     "--gpu-memory-utilization", "0.95", "--max-model-len", "1000000",
                     "--max-num-seqs", "4", "--enable-prefix-caching", "--enforce-eager",
                     "--limit-mm-per-prompt", "{\"image\": 600, \"video\": 0}"],
            "env": {"CUDA_VISIBLE_DEVICES": "0,1,2,3", "PYTORCH_CUDA_ALLOC_CONF": "expandable_segments:True",
                    "VLLM_ALLREDUCE_USE_FLASHINFER": "0", "VLLM_ALLOW_LONG_MAX_MODEL_LEN": "1"},
            "cwd": "/w", "endpoint": "http://127.0.0.1:8121", "model": "qwen3.8-flash-next",
            "context_tokens": 1000000}


# ---- GPU -> unit -> ports/slots ----------------------------------------------


def test_four_gpus_make_one_unit_with_default_ports_and_two_slots():
    (unit,) = layout.units("0,1,2,3")
    assert unit.gpus == (0, 1, 2, 3)
    assert unit.ports == {"model": 8121, "sam3": 8114, "graspnet": 8115, "pyroki": 8116}
    assert (unit.card("sam3"), unit.card("graspnet")) == (0, 1)
    assert [(s.name, s.gpu) for s in unit.slots] == [("a", 2), ("b", 3)]


def test_eight_gpus_make_two_numa_aligned_units_with_disjoint_ports():
    first, second = layout.units(list(range(8)))
    assert first.gpus == (0, 1, 2, 3) and second.gpus == (4, 5, 6, 7)
    assert second.ports == {"model": 8131, "sam3": 8124, "graspnet": 8125, "pyroki": 8126}
    assert [(s.unit, s.name, s.gpu) for u in (first, second) for s in u.slots] == [
        (0, "a", 2), (0, "b", 3), (1, "a", 6), (1, "b", 7)]
    assert second.card("sam3") == 4 and second.card("graspnet") == 5
    assert not set(first.ports.values()) & set(second.ports.values())


def test_physical_gpu_lists_ports_and_placement_are_parameters():
    base = layout.parse_mapping("model=8321,sam3=8314,graspnet=8315,pyroki=8316", layout.DEFAULT_PORTS)
    placement = layout.parse_mapping("sam3=1,graspnet=0,a=3,b=2", layout.DEFAULT_PLACEMENT)
    (unit,) = layout.units("0,3,4,7", ports=base, placement=placement)
    assert unit.endpoint == "http://127.0.0.1:8321"
    assert (unit.card("sam3"), unit.card("graspnet")) == (3, 0)
    assert [s.gpu for s in unit.slots] == [7, 4]


@pytest.mark.parametrize("gpus", ["0", "0,1,2", "0,1,2,3,4", "0,1,2,3,4,5", list(range(16)), "0,0,1,2"])
def test_other_gpu_counts_and_duplicates_are_refused(gpus):
    with pytest.raises(layout.LayoutError):
        layout.units(gpus)


def test_bad_placement_and_port_collisions_are_refused():
    with pytest.raises(layout.LayoutError):
        layout.units("0,1,2,3", placement={"sam3": 0, "graspnet": 1, "a": 2, "b": 2})
    with pytest.raises(layout.LayoutError):
        layout.units(list(range(8)), stride=0)
    with pytest.raises(layout.LayoutError):
        layout.parse_mapping("model=1,sam3=2", layout.DEFAULT_PORTS)


def test_slot_binding_carries_every_wiring_field():
    unit = layout.units(list(range(8)))[1]
    binding = layout.slot_binding(unit, unit.slots[1])
    assert binding["inference_endpoint"] == "http://127.0.0.1:8131"
    assert (binding["gpu"], binding["cuda_visible_devices"], binding["egl_device_id"]) == (7, "7", 7)
    assert binding["service_ports"] == [8124, 8125, 8126]
    assert binding["sam3_url"] == "http://127.0.0.1:8124"
    assert binding["model_gpus"] == [4, 5, 6, 7] and binding["pyroki_device"] == "cpu"
    case = layout.bind_case({"id": "c", "gpu": 6, "sam3_url": "http://127.0.0.1:8114", "model": "m"}, binding)
    assert all(case[key] == binding[key] for key in layout.SLOT_FIELDS)
    assert case["slot_binding"] == binding and case["model"] == "m"


def test_runtime_family_ignores_slot_wiring_but_not_the_model():
    unit0, unit1 = layout.units(list(range(8)))
    base = {"model": "q", "context_tokens": 1000000, "effort": "xhigh", "prediction_contract": "p3-search"}
    a = layout.bind_case(base, layout.slot_binding(unit0, unit0.slots[0]))
    b = layout.bind_case(base, layout.slot_binding(unit1, unit1.slots[1]))
    assert layout.same_runtime_family(a, b) == []
    assert layout.same_runtime_family(a, dict(b, context_tokens=262144)) == ["context_tokens"]
    assert not set(layout.SLOT_FIELDS) & set(layout.FAMILY_KEYS)


# ---- model server argv ---------------------------------------------------------


def test_reference_differs_from_eager_in_exactly_the_three_design_changes():
    eager = eager_reference()
    reference = layout.reference_from_eager(eager, 0.82)
    removed = [a for a in eager["argv"] if a not in reference["argv"]]
    added = [a for a in reference["argv"] if a not in eager["argv"]]
    assert sorted(removed) == ["--enforce-eager", "0.95"]
    assert added == ["0.82", "--compilation-config", json.dumps(layout.COMPILATION_CONFIG)]
    assert set(eager["env"]) - set(reference["env"]) == {"PYTORCH_CUDA_ALLOC_CONF"}
    assert {k: v for k, v in eager["env"].items() if k != "PYTORCH_CUDA_ALLOC_CONF"} == reference["env"]


def test_model_server_sets_port_cards_and_util_per_unit():
    reference = layout.reference_from_eager(eager_reference(), 0.82)
    unit = layout.units(list(range(8)))[1]
    server = layout.model_server(reference, unit, 0.80)
    argv = server["argv"]
    assert argv[argv.index("--port") + 1] == "8131"
    assert argv[argv.index("--gpu-memory-utilization") + 1] == "0.80"
    assert server["env"]["CUDA_VISIBLE_DEVICES"] == "4,5,6,7"
    assert "--enforce-eager" not in argv and "expandable_segments" not in json.dumps(server["env"])
    assert json.loads(argv[argv.index("--compilation-config") + 1]) == {
        "mode": 0, "cudagraph_mode": "FULL_DECODE_ONLY", "cudagraph_capture_sizes": [1, 2, 4]}
    for bad in (0.70, 0.99):
        with pytest.raises(layout.LayoutError):
            layout.model_server(reference, unit, bad)


@pytest.mark.parametrize("mutate", [
    lambda r: r["argv"].append("--enforce-eager"),
    lambda r: r["env"].update(PYTORCH_CUDA_ALLOC_CONF="expandable_segments:True"),
    lambda r: r["argv"].append("--disable-custom-all-reduce"),
    lambda r: r["argv"].__setitem__(r["argv"].index("--compilation-config") + 1, "{}"),
])
def test_reference_check_refuses_old_logic(mutate):
    reference = layout.reference_from_eager(eager_reference(), 0.82)
    mutate(reference)
    with pytest.raises(layout.LayoutError):
        layout.check_reference(reference)


def test_shipped_reference_and_deploy_example_follow_design_section_1():
    shipped = json.loads((LAYOUT / "reference/model-server.json").read_text())
    example = json.loads((ROOT.parents[1] / "deploy/qwen/model-server.example.json").read_text())
    for config in (shipped, example):
        layout.check_reference(config)
        assert config["argv"][config["argv"].index("--gpu-memory-utilization") + 1] == "0.82"
    if FROZEN_EAGER.is_file():
        frozen = json.loads(FROZEN_EAGER.read_text())
        derived = layout.reference_from_eager(frozen, 0.82)
        assert (derived["argv"], derived["env"]) == (shipped["argv"], shipped["env"])


# ---- queue claims ----------------------------------------------------------------


def make_cells(root, names):
    cells = []
    for name in names:
        (root / name).mkdir(parents=True)
        (root / name / "case.json").write_text(json.dumps({"id": name}))
        cells.append(root / name)
    return cells


def test_two_slots_claiming_concurrently_never_share_a_cell(tmp_path):
    cells = make_cells(tmp_path, [f"cell{i:02d}" for i in range(24)])
    unit = layout.units("0,1,2,3")[0]
    claimed, barrier = {s.name: [] for s in unit.slots}, threading.Barrier(2)

    def slot_loop(slot):
        barrier.wait()
        while (cell := layout.claim_next(tmp_path, cells, f"w-{slot.name}",
                                         layout.slot_binding(unit, slot))) is not None:
            claimed[slot.name].append(cell.name)

    threads = [threading.Thread(target=slot_loop, args=(s,)) for s in unit.slots]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    every = claimed["a"] + claimed["b"]
    assert sorted(every) == sorted(c.name for c in cells) and len(set(every)) == len(every)
    for name in every:
        record = json.loads((tmp_path / "queue/claims" / name / "claim.json").read_text())
        slot = "a" if name in claimed["a"] else "b"
        assert record["slot_binding"]["slot"] == slot


def _claim_in_process(root, names, worker, out):
    cells = [Path(root) / n for n in names]
    unit = layout.units("0,1,2,3")[0]
    got = []
    while (cell := layout.claim_next(Path(root), cells, worker,
                                     layout.slot_binding(unit, unit.slots[0]))) is not None:
        got.append(cell.name)
    Path(out).write_text(json.dumps(got))


def test_claims_from_separate_processes_are_exclusive(tmp_path):
    names = [f"cell{i:02d}" for i in range(30)]
    make_cells(tmp_path, names)
    outs = [tmp_path / f"got{i}.json" for i in range(4)]
    procs = [multiprocessing.get_context("fork").Process(
        target=_claim_in_process, args=(str(tmp_path), names, f"w{i}", str(outs[i]))) for i in range(4)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(30)
    every = [n for out in outs for n in json.loads(out.read_text())]
    assert sorted(every) == names


def test_started_or_claimed_cells_are_never_claimed(tmp_path):
    cells = make_cells(tmp_path, ["done", "interrupted", "smoked", "free"])
    (tmp_path / "done/campaign_state.json").write_text("{}")
    (tmp_path / "smoked/native_smoke_state.json").write_text("{}")
    (tmp_path / "queue/claims/interrupted").mkdir(parents=True)
    unit = layout.units("0,1,2,3")[0]
    binding = layout.slot_binding(unit, unit.slots[0])
    assert layout.claim_next(tmp_path, cells, "w", binding).name == "free"
    assert layout.claim_next(tmp_path, cells, "w", binding) is None


# ---- node: unit isolation and run records ------------------------------------------


class FakeService(Service):
    def ready(self):
        return True


class FakeHooks:
    """Real child processes (sleep) stand in for services and cell drivers."""
    service_startup = 30

    def __init__(self, tmp, *, fail_unit=None, driver_seconds=1.0):
        self.tmp, self.fail_unit, self.driver_seconds = tmp, fail_unit, driver_seconds
        self.services = {}
        self.spawned = []
        self.health = []

    def _sleep(self, seconds, log):
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("w") as out:
            return subprocess.Popen([sys.executable, "-c", f"import time; time.sleep({seconds})"],
                                    stdout=out, stderr=subprocess.STDOUT, start_new_session=True)

    def start_services(self, unit):
        logs = self.tmp / f"unit{unit.unit.index}"
        services = []
        for name in ("qwen", "sam3", "contact_graspnet", "pyroki"):
            process = self._sleep(120, logs / f"{name}.log")
            unit.processes.append(process)
            services.append(FakeService(name, process, logs / f"{name}.log", "http://unused"))
        self.services[unit.unit.index] = services
        return services

    def startup_checks(self, unit):
        pass

    def boundary_check(self, unit):
        self.health.append(unit.unit.index)

    def verify_cell(self, cell):
        return json.loads((cell / "case.json").read_text())

    def spawn_driver(self, unit, slot, bound, bound_path, cell):
        self.spawned.append((unit.unit.index, slot.name, cell.name, bound["inference_endpoint"]))
        if unit.unit.index == self.fail_unit:
            # Kill this unit's SAM3 while its first cell runs.
            threading.Timer(1.0, self.services[unit.unit.index][1].process.kill).start()
            return self._sleep(60, self.tmp / f"drv-{cell.name}.log")
        process = self._sleep(self.driver_seconds, self.tmp / f"drv-{cell.name}.log")
        (cell / "campaign_state.json").write_text(json.dumps({"status": "ok"}))
        return process

    def accept(self, cell, code, smoke_stop=None):
        assert code == 0 and smoke_stop is None
        return {"status": "ok"}


def run_node(tmp_path, gpus, names, hooks):
    root = tmp_path / "root"
    cells = make_cells(root, names)
    node = supervisor.Node(root=root, cells=cells, units=layout.units(gpus), hooks=hooks,
                           worker="worker-node0", status_path=root / "status.json",
                           node_stop_at=time.monotonic() + 3600, cell_timeout=60, mode="full")
    node.terminal = "ok"
    return root, node, node.run()


def test_two_units_four_slots_drain_the_queue_and_record_bindings(tmp_path):
    hooks = FakeHooks(tmp_path)
    names = [f"cell{i}" for i in range(6)]
    root, node, code = run_node(tmp_path, list(range(8)), names, hooks)
    assert code == 0
    assert sorted(r["cell"] for r in node.records) == names
    assert {(u, s) for u, s, _, _ in hooks.spawned} <= {(0, "a"), (0, "b"), (1, "a"), (1, "b")}
    for name in names:
        record = json.loads((root / name / "queue-result.json").read_text())
        bound = json.loads((root / name / "slot-case.json").read_text())
        claim = json.loads((root / "queue/claims" / name / "claim.json").read_text())
        assert record["state"] == "ok"
        assert record["slot_binding"] == bound["slot_binding"] == claim["slot_binding"]
        assert bound["inference_endpoint"] == record["slot_binding"]["inference_endpoint"]
        assert record["slot_binding"]["unit"] == record["unit"]
        expected = layout.units(list(range(8)))[record["unit"]]
        assert bound["gpu"] == expected.card(record["slot"])
    status = json.loads((root / "status.json").read_text())
    assert status["state"] == "node_complete" and len(status["units"]) == 2


def test_failed_unit_marks_only_its_cells_infrastructure_and_the_other_unit_continues(tmp_path):
    hooks = FakeHooks(tmp_path, fail_unit=0)
    names = [f"cell{i}" for i in range(8)]
    root, node, code = run_node(tmp_path, list(range(8)), names, hooks)
    assert code == 1
    by_unit = {}
    for record in node.records:
        by_unit.setdefault(record["unit"], []).append(record)
    assert by_unit[0] and all(r["state"] == "infrastructure" for r in by_unit[0])
    assert all("unit 0 down" in r["error"] for r in by_unit[0])
    assert by_unit[1] and all(r["state"] == "ok" for r in by_unit[1])
    # Unit 1 drained everything unit 0 did not take; nothing ran twice.
    assert sorted(r["cell"] for r in node.records) == names
    assert len(by_unit[0]) <= 2
    status = json.loads((root / "status.json").read_text())
    assert status["failed_units"] == [0]
    assert [u["state"] for u in status["units"]] == ["failed", "stopped"]
    for record in by_unit[0]:
        assert record["process_cleanup"]["verified_no_live_work"] is True


def test_unit_whose_startup_fails_takes_no_cell(tmp_path):
    class StartupFails(FakeHooks):
        def startup_checks(self, unit):
            if unit.unit.index == 1:
                raise RuntimeError("native compat failed 3x")

    hooks = StartupFails(tmp_path)
    names = [f"cell{i}" for i in range(4)]
    root, node, code = run_node(tmp_path, list(range(8)), names, hooks)
    assert code == 1
    assert {r["unit"] for r in node.records} == {0}
    assert all(r["state"] == "ok" for r in node.records) and len(node.records) == 4
    assert json.loads((root / "status.json").read_text())["failed_units"] == [1]


# ---- native smoke: the supervisor enforces the stop -----------------------------------

#: A cell driver whose solver ignores the smoke pause: snapshot, one charged
#: smoke on seed 51, then initial 52, 53, ... until stopped. Trials and the
#: "solver" are real child processes; ledger rows follow
#: native_world_fixloop_state (row written "running", then rewritten in place).
FAKE_DRIVER = r"""
import json, os, signal, subprocess, sys, time
from pathlib import Path
ledger, smoke_seconds, smoke_rc, later_seconds = (Path(sys.argv[1]), float(sys.argv[2]),
                                                  int(sys.argv[3]), float(sys.argv[4]))
self_stop = sys.argv[5] == "self-stop"
data = {"trials": [], "rejected": []}
children = [subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])]  # the solver
def save():
    ledger.parent.mkdir(parents=True, exist_ok=True)
    tmp = ledger.with_suffix(".tmp")
    tmp.write_text(json.dumps(data))
    os.replace(tmp, ledger)
def begin(phase, seed, charged):
    row = {"phase": phase, "seed": seed, "attempt": 1, "status": "running", "spends_retry": charged,
           "executed": True, "directory": f"development/{phase}/seed_{seed}/attempt_1",
           "begun_at": time.time()}
    data["trials"].append(row)
    return row
def trial(seconds, rc=0):
    child = subprocess.Popen([sys.executable, "-c", f"import sys,time; time.sleep({seconds}); sys.exit({rc})"])
    children.append(child)
    return child.wait()
def finish(row, rc):
    row.update(status="complete" if rc == 0 else "infrastructure_error", process_exit_code=rc,
               reward=1.0 if rc == 0 else None, trial_dir=f"/trials/{row['seed']}", finished_at=time.time())
def interrupted(*_):
    for row in data["trials"]:
        if row["status"] == "running":
            row.update(status="infrastructure_error", process_exit_code=-15,
                       error="TrialInterrupted: trial interrupted by signal 15")
    save()
    for child in children:
        child.terminate()
    sys.exit(143)
signal.signal(signal.SIGTERM, interrupted)
finish(begin("snapshot", 51, False), 0); save()
row = begin("smoke", 51, True); save()
finish(row, trial(smoke_seconds, smoke_rc))
if self_stop:
    save()
    Path(sys.argv[6]).write_text(json.dumps({"status": "native_smoke_checkpoint", "smoke_attempt": row}))
    sys.exit(0)
seed = 52
row = begin("initial", seed, True)
save()  # The fast solver: smoke result and the next trial land in one write.
while True:
    finish(row, trial(later_seconds)); seed += 1
    row = begin("initial", seed, True); save()
"""


class SmokeHooks(FakeHooks):
    """FakeHooks whose cell driver is FAKE_DRIVER and whose acceptance is the real one."""

    def __init__(self, tmp, *, smoke_seconds=2.0, smoke_rc=0, later_seconds=30.0, self_stop=False,
                 ledger_allowed=True):
        super().__init__(tmp)
        self.smoke_seconds, self.smoke_rc, self.later_seconds = smoke_seconds, smoke_rc, later_seconds
        self.self_stop, self.ledger_allowed = self_stop, ledger_allowed
        self.driver_script = tmp / "fake_driver.py"
        self.driver_script.write_text(FAKE_DRIVER)

    def smoke_ledger(self, cell, bound):
        assert self.ledger_allowed, "full mode must never watch the ledger"
        return cell / "outputs/development_state.json"

    def spawn_driver(self, unit, slot, bound, bound_path, cell):
        self.spawned.append((unit.unit.index, slot.name, cell.name, bound["inference_endpoint"]))
        log = (self.tmp / f"drv-{cell.name}.log").open("w")
        return subprocess.Popen([sys.executable, str(self.driver_script),
                                 str(cell / "outputs/development_state.json"), str(self.smoke_seconds),
                                 str(self.smoke_rc), str(self.later_seconds),
                                 "self-stop" if self.self_stop else "continue",
                                 str(cell / "native_smoke_state.json")],
                                stdout=log, stderr=subprocess.STDOUT, start_new_session=True)

    def accept(self, cell, code, smoke_stop=None):
        state = supervisor.read_ledger(cell / "native_smoke_state.json")
        return supervisor.accept_smoke(state, code, smoke_stop)


def run_smoke_node(tmp_path, hooks, names=("stove", "drawer"), gpus="0,1,2,3", mode="native_smoke"):
    root = tmp_path / "root"
    cells = make_cells(root, list(names))
    node = supervisor.Node(root=root, cells=cells, units=layout.units(gpus), hooks=hooks,
                           worker="worker-node0", status_path=root / "status.json",
                           node_stop_at=time.monotonic() + 3600, cell_timeout=120, mode=mode)
    return root, node, node.run()


def assert_no_live_pids(cleanup):
    import psutil
    assert cleanup["verified_no_live_work"] is True
    for pid in cleanup["tracked_pids"]:
        try:
            assert psutil.Process(pid).status() == psutil.STATUS_ZOMBIE
        except psutil.NoSuchProcess:
            pass


def test_smoke_stop_point_waits_for_the_first_charged_row_to_be_terminal():
    snapshot = {"phase": "snapshot", "seed": 51, "status": "complete", "spends_retry": False}
    running = {"phase": "smoke", "seed": 51, "status": "running", "spends_retry": True}
    assert supervisor.smoke_stop_point(None) is None
    assert supervisor.smoke_stop_point({"trials": [snapshot]}) is None
    assert supervisor.smoke_stop_point({"trials": [snapshot, running]}) is None
    done = dict(running, status="complete", reward=0.0)
    later = {"phase": "initial", "seed": 52, "status": "running", "spends_retry": True}
    assert supervisor.smoke_stop_point({"trials": [snapshot, done, later]}) == (1, done)


def test_solver_that_keeps_going_is_stopped_after_the_first_charged_terminal_row(tmp_path):
    hooks = SmokeHooks(tmp_path)
    root, node, code = run_smoke_node(tmp_path, hooks)
    assert code == 0
    assert {r["cell"] for r in node.records} == {"stove", "drawer"}
    for name in ("stove", "drawer"):
        record = json.loads((root / name / "queue-result.json").read_text())
        assert record["state"] == "native_smoke_complete"
        summary, stop = record["summary"], record["smoke_stop"]
        assert summary["status"] == "native_smoke_passed"
        assert summary["stop_reason"] == stop["reason"] == "supervisor_smoke_stop"
        attempt = summary["smoke_attempt"]
        assert (attempt["phase"], attempt["seed"], attempt["status"], attempt["reward"]) == (
            "smoke", 51, "complete", 1.0)
        assert attempt["trial_dir"] == "/trials/51" and attempt["process_exit_code"] == 0
        # The trial the solver began next was interrupted by the stop and is not counted.
        assert [(a["seed"], a["ledger_status"], a["recorded_as"], a["counted"])
                for a in summary["after_stop"]] == [(52, "infrastructure_error", "infrastructure_error", False)]
        assert "smoke stop" in summary["after_stop"][0]["note"]
        ledger = json.loads((root / name / "outputs/development_state.json").read_text())
        assert [r["seed"] for r in ledger["trials"]] == [51, 51, 52]  # No seed 53 ever began.
        assert record["driver_exit"] == summary["driver_exit"] != 0
        # Driver, solver and trial processes were all tracked and none survived.
        assert len(record["process_cleanup"]["tracked_pids"]) >= 3
        assert_no_live_pids(record["process_cleanup"])
    status = json.loads((root / "status.json").read_text())
    assert status["state"] == "node_complete" and sorted(status["completed"]) == ["drawer", "stove"]


def test_running_smoke_trial_is_never_interrupted(tmp_path):
    hooks = SmokeHooks(tmp_path, smoke_seconds=4.0)
    root, node, code = run_smoke_node(tmp_path, hooks, names=("bowl",))
    assert code == 0
    record = json.loads((root / "bowl/queue-result.json").read_text())
    ledger = json.loads((root / "bowl/outputs/development_state.json").read_text())
    smoke = ledger["trials"][1]
    assert smoke["status"] == "complete" and smoke["process_exit_code"] == 0
    assert smoke["finished_at"] - smoke["begun_at"] >= 4.0
    signals = record["process_cleanup"]["signals"]
    assert signals and record["smoke_stop"]["observed_at"] >= smoke["finished_at"]


def test_smoke_attempt_ending_in_infrastructure_error_fails_the_smoke(tmp_path):
    hooks = SmokeHooks(tmp_path, smoke_rc=3)
    root, node, code = run_smoke_node(tmp_path, hooks, names=("bowl",))
    assert code == 1
    record = json.loads((root / "bowl/queue-result.json").read_text())
    assert record["state"] == "failed" and "infrastructure_error" in record["error"]
    assert record["smoke_stop"]["smoke_attempt"]["status"] == "infrastructure_error"
    assert_no_live_pids(record["process_cleanup"])


def test_driver_that_pauses_by_itself_is_still_accepted(tmp_path):
    hooks = SmokeHooks(tmp_path, smoke_seconds=0.2, self_stop=True)
    root, node, code = run_smoke_node(tmp_path, hooks, names=("bowl",))
    assert code == 0
    record = json.loads((root / "bowl/queue-result.json").read_text())
    assert record["state"] == "native_smoke_complete"
    assert record["summary"]["status"] in ("native_smoke_checkpoint", "native_smoke_passed")


def test_two_units_four_smoke_cells_each_stop_on_their_own_ledger(tmp_path):
    hooks = SmokeHooks(tmp_path)
    names = ("bowl", "stove", "drawer", "plate")
    root, node, code = run_smoke_node(tmp_path, hooks, names=names, gpus=list(range(8)))
    assert code == 0
    assert {(u, s) for u, s, _, _ in hooks.spawned} == {(0, "a"), (0, "b"), (1, "a"), (1, "b")}
    for name in names:
        record = json.loads((root / name / "queue-result.json").read_text())
        assert record["state"] == "native_smoke_complete"
        assert record["smoke_ledger"] == str(root / name / "outputs/development_state.json")
        assert record["slot_binding"]["inference_endpoint"] == (
            "http://127.0.0.1:8121" if record["unit"] == 0 else "http://127.0.0.1:8131")


def test_full_mode_never_watches_the_ledger(tmp_path):
    hooks = SmokeHooks(tmp_path, smoke_seconds=0.2, self_stop=True, ledger_allowed=False)
    hooks.accept = lambda cell, code, smoke_stop=None: (
        {"status": "ok"} if code == 0 and smoke_stop is None else pytest.fail("smoke stop in full mode"))
    root, node, code = run_smoke_node(tmp_path, hooks, names=("bowl",), mode="full")
    assert code == 0
    record = json.loads((root / "bowl/queue-result.json").read_text())
    assert record["state"] == "full_complete" and "smoke_stop" not in record
    assert "smoke_ledger" not in record and record["driver_exit"] == 0


def test_accept_smoke_refuses_unfinished_or_foreign_stops():
    attempt = {"phase": "smoke", "seed": 51, "status": "complete", "reward": 0.0}
    stop = {"reason": "supervisor_smoke_stop", "smoke_attempt": attempt, "after_stop": []}
    assert supervisor.accept_smoke({}, -2, stop)["status"] == "native_smoke_passed"
    for bad in (dict(stop, reason="manual"),
                dict(stop, smoke_attempt=dict(attempt, status="running")),
                dict(stop, smoke_attempt=dict(attempt, status="infrastructure_error"))):
        with pytest.raises(RuntimeError):
            supervisor.accept_smoke({}, -2, bad)
    with pytest.raises(RuntimeError):
        supervisor.accept_smoke({"status": "solver_running"}, -2)


# ---- entry and DLC plan -------------------------------------------------------------


@pytest.mark.parametrize("gpus", ["0,1,2", "0,1,2,3,4,5", "0,1,2,3,4,5,6,7,8,9,10,11"])
def test_entry_refuses_other_gpu_counts(gpus):
    if not Path("/mnt/home/gewang/.config/rbs-dlc/dlc_entry_prelude.sh").is_file():
        pytest.skip("DLC prelude is host-specific")
    proc = subprocess.run(["bash", str(LAYOUT / "unit-entry.sh"), "--node-id", "0", "--node-count", "1",
                           "--node-window-minutes", "120", "--gpus", gpus],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 2 and "4 or 8 GPUs" in proc.stderr


def test_dlc_plan_requests_four_or_eight_gpus_only(tmp_path):
    argv = layout.dlc_plan_argv("study", 1, 2, tmp_path, 600, 4)
    assert argv[argv.index("--gpus") + 1] == "4" and argv[1] == "plan"
    assert argv[-6:] == ["--node-id", "1", "--node-count", "2", "--node-window-minutes", "590"]
    assert str(tmp_path / "launch/unit-entry.sh") in argv
    for gpus in (2, 6, 16):
        with pytest.raises(layout.LayoutError):
            layout.dlc_plan_argv("study", 0, 1, tmp_path, 600, gpus)


def test_freeze_launch_pins_every_file(tmp_path):
    compat = tmp_path / "compat.py"
    compat.write_text("# compat\n")
    manifest = layout.freeze_launch(tmp_path / "launch", {"mode": "full"}, compat=compat)
    for name, sha in manifest["files"].items():
        assert supervisor.digest(tmp_path / "launch" / name) == sha
    assert {"unit_supervisor.py", "unit_layout.py", "native_cc_runtime.py", "qwen-native-compat.py",
            "reference/model-server.json", "study.json", "unit-entry.sh"} <= set(manifest["files"])
    with pytest.raises(layout.LayoutError):
        layout.freeze_launch(tmp_path / "launch", {"mode": "full"}, compat=compat)


def test_supervisor_plan_mode_prints_units_without_starting_anything(tmp_path, capsys):
    assert supervisor.main(["--root", str(tmp_path), "--gpus", "0,1,2,3,4,5,6,7", "--plan"]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert [u["gpus"] for u in printed["units"]] == [[0, 1, 2, 3], [4, 5, 6, 7]]
    assert printed["units"][1]["model_server"]["env"]["CUDA_VISIBLE_DEVICES"] == "4,5,6,7"
    assert [s["gpu"] for u in printed["units"] for s in u["slots"]] == [2, 3, 6, 7]
    assert not (tmp_path / "queue").exists()


# ---- rendered prompt points at the cell's own unit ----------------------------------


def test_worker_prompt_health_check_uses_the_cells_service_ports(monkeypatch):
    sys.path.insert(0, str(ROOT / "scripts/libero"))
    import native_world_campaign as campaign
    line = ("for p in 8114 8115 8116; do echo \"port $p: $(curl -s -o /dev/null -w "
            "'%{http_code}' --max-time 3 http://127.0.0.1:$p/health)\"; done")
    monkeypatch.setattr(campaign, "is_simple", lambda case: False)
    monkeypatch.setattr(campaign, "pristine_template", lambda repo: line)
    monkeypatch.setattr(campaign, "_rewrite_commands", lambda text, case, task_dir: text)
    case = {"suite": "s", "task": "t", "gpu": 6}
    default = campaign.worker_prompt(case, Path("."))
    assert default.startswith(line) and "never restart" in default
    assert campaign.worker_prompt(dict(case, service_ports=[8114, 8115, 8116]), Path(".")) == default
    unit1 = campaign.worker_prompt(dict(case, service_ports=[8124, 8125, 8126]), Path("."))
    assert unit1 == default.replace("8114 8115 8116", "8124 8125 8126")
