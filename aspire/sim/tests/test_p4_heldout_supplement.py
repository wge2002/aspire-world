"""CC_TASK_5: a legal slow held-out sweep is not cut, and an interrupted one can be finished.

p4n's spatialbetween_p4n_r1 (~400 s/seed) and twomoka_p4n_r1 (~440 s/seed) were
stopped by the fixed 5 h held-out watchdog at seeds 45 and 41, every seed inside
its own 900 s trial timeout. The watchdog is now derived from the seeds still to
run, and `run_two_task_cell.py --heldout-supplement` re-runs only the missing
seeds (the interrupted one included) on the same frozen bundle.

The supplement tests drive the real p4 driver and the REAL held-out evaluator
(`native_world_heldout.main` through `heldout_stop_on_infra.main`, identity and
resume rules included); only `evaluate_seed` (the simulator) is replaced. The
replay test runs on a read-only copy of spatialbetween_p4n_r1's held-out state.
"""
from __future__ import annotations

import contextlib
import gzip
import importlib.util
import io
import json
from pathlib import Path
import shutil
import sys
from types import ModuleType, SimpleNamespace

import pytest

SIM = Path(__file__).resolve().parents[1]
P3_SUPPORT = SIM / "docs/experiments/code-world-search-control-p3-20261013/support"
P4_SUPPORT = SIM / "docs/experiments/code-world-coverage-p4-20261016/support"
FIXTURES = Path(__file__).resolve().parent / "fixtures/p4_heldout_supplement"
LAYOUT = SIM / "scripts/common/node_layout"
sys.path[:0] = [str(LAYOUT), str(LAYOUT.parent), str(SIM / "scripts/libero")]
import unit_layout as layout  # noqa: E402
import unit_supervisor as supervisor  # noqa: E402
import native_world_heldout  # noqa: E402
from native_world_protocol import CLEANUP_MARGIN_SECONDS  # noqa: E402


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


deadlines = load(P4_SUPPORT / "full_deadlines.py", "p4_full_deadlines")
supplement = load(P4_SUPPORT / "heldout_supplement.py", "p4_heldout_supplement")


# ---- fix 1: the held-out watchdog ------------------------------------------------


def test_heldout_deadline_is_the_sum_of_the_per_seed_bounds():
    per_seed = 900 + deadlines.HELDOUT_SEED_MARGIN
    assert deadlines.heldout_deadline(50, 900) == deadlines.HELDOUT_SWEEP_OVERHEAD + 50 * per_seed
    assert deadlines.HELDOUT == deadlines.heldout_deadline(50) == 51120
    assert deadlines.heldout_deadline(6, 900) == 120 + 6 * 1020
    assert deadlines.heldout_deadline(0, 900) == deadlines.HELDOUT_SWEEP_OVERHEAD
    assert deadlines.heldout_deadline(10, 300) < deadlines.heldout_deadline(10, 900)
    for bad in ((-1, 900), (5, 0)):
        with pytest.raises(ValueError):
            deadlines.heldout_deadline(*bad)


def test_a_sweep_of_seeds_each_inside_its_own_bound_always_fits():
    # What run_replay can take per seed: trial timeout, adapter grace, TERM->KILL.
    assert deadlines.HELDOUT_SEED_MARGIN >= CLEANUP_MARGIN_SECONDS + 15 + 30
    worst_legal_seed = 900 + CLEANUP_MARGIN_SECONDS + 15 + 2  # +2 s observed between seeds
    assert 50 * worst_legal_seed < deadlines.HELDOUT
    # The real cells it cut: 18000 s was below 50 x their mean seed time.
    for name, mean_seconds in (("spatialbetween_p4n_r1", 399), ("twomoka_p4n_r1", 438)):
        assert 50 * mean_seconds > 5 * 3600, name
        assert 50 * 641 < deadlines.HELDOUT, name  # 641 s = slowest observed seed


def test_job_window_is_consistent_with_the_cell_allowance():
    cell_timeout = deadlines.DEVELOPMENT + deadlines.HELDOUT + deadlines.FINALIZE_MARGIN
    assert cell_timeout == 103320
    minimum = supervisor.minimum_window_minutes(cell_timeout)
    assert minimum == 1769
    # The p4fix spec (1240 min) is now refused by the supervisor's own window check.
    assert 1240 < minimum
    assert deadlines.DLC_MAX_RUNNING_MINUTES == 1794 >= minimum
    assert deadlines.TOTAL == (deadlines.SERVICE_STARTUP + deadlines.NATIVE_COMPAT + cell_timeout)
    assert deadlines.summary()["heldout_deadline_seconds"] == deadlines.HELDOUT


def test_p4_deadlines_differ_from_p3_only_in_the_heldout_watchdog():
    p3 = load(P3_SUPPORT / "full_deadlines.py", "p3_full_deadlines")
    for name in ("TRIAL_TIMEOUT", "SERVICE_STARTUP", "NATIVE_COMPAT", "DEVELOPMENT", "FINALIZE_MARGIN"):
        assert getattr(p3, name) == getattr(deadlines, name), name
    assert p3.HELDOUT == 5 * 3600 < deadlines.HELDOUT


def hooks(mode="full"):
    real = supervisor.RealHooks.__new__(supervisor.RealHooks)
    real.study = {"mode": mode, "smoke_deadline_seconds": 1800}
    real.deadlines = deadlines
    return real


def test_supervisor_leaves_the_heldout_deadline_to_the_driver():
    assert hooks().driver_args("b.json") == ["--case", "b.json", "--deadline-development",
                                             deadlines.DEVELOPMENT]
    assert hooks().driver_args("b.json", True) == ["--case", "b.json", "--heldout-supplement"]
    assert hooks("native_smoke").driver_args("b.json") == [
        "--case", "b.json", "--deadline-development", 1800, "--deadline-heldout", 60, "--native-smoke"]


# ---- driver harness --------------------------------------------------------------


@pytest.fixture
def driver(tmp_path, monkeypatch):
    """The p4 driver from a support dir assembled the way the p4n stager overlays it."""
    support = tmp_path / "support"
    support.mkdir()
    for source in P3_SUPPORT.iterdir():
        if source.suffix == ".py":
            (support / source.name).symlink_to(source)
    for source in P4_SUPPORT.iterdir():
        if source.suffix == ".py":
            (support / source.name).unlink(missing_ok=True)
            (support / source.name).write_bytes(source.read_bytes())
    monkeypatch.syspath_prepend(str(support))
    for source in support.iterdir():
        monkeypatch.delitem(sys.modules, source.stem, raising=False)
    module = load(support / "run_two_task_cell.py", "p4_supplement_driver")

    def atomic(path, data):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(data, indent=2))

    campaign = ModuleType("native_world_campaign")
    campaign.atomic_json = atomic
    campaign.verify_runtime = lambda *a: None
    campaign.native_environment = lambda *a: {}
    monkeypatch.setitem(sys.modules, "native_world_campaign", campaign)
    monkeypatch.setattr(module.scope, "apply", lambda *a: None)
    monkeypatch.setattr(module, "bind_child_env", lambda env, case: env)
    module.campaign_stub = campaign
    module.support = support
    return module


@pytest.fixture
def evaluator(driver, monkeypatch):
    """Run the real stop-on-infra entry + real evaluator in process; fake only the simulator."""
    stop = load(driver.support / "heldout_stop_on_infra.py", "p4_stop_on_infra")
    # guard()/runtime_replay rebind module attributes: restore them after each test.
    monkeypatch.setattr(native_world_heldout, "atomic_json", native_world_heldout.atomic_json)
    monkeypatch.setattr(native_world_heldout, "run_replay", native_world_heldout.run_replay)
    calls = SimpleNamespace(seeds=[], runs=[], outcome={})

    def evaluate_seed(case, repo, evaluation, bundle, kept, seed):
        calls.seeds.append(seed)
        directory = evaluation / f"seed_{seed:02d}"
        directory.mkdir(parents=True)
        status = calls.outcome.get(seed, "failure")
        return {"seed": seed, "directory": str(directory), "exit_code": 0 if status != "infrastructure_error" else -15,
                "error": "" if status != "infrastructure_error" else "TrialInterrupted: fixture",
                "result": None if status == "infrastructure_error" else
                {"sandbox_rc": 0, "reward": float(status == "success"),
                 "task_completed": int(status == "success"), "trial_dir": str(directory)},
                "finished_at": "2026-10-19T00:00:00+00:00", "status": status}

    monkeypatch.setattr(native_world_heldout, "evaluate_seed", evaluate_seed)

    def run_heldout(campaign, repo, env, folder, argv, timeout):
        calls.runs.append({"argv": list(argv), "timeout": timeout, "folder": str(folder)})
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = stop.main(list(argv))
        step = {"step": "heldout", "exit_code": code, "result": json.loads(out.getvalue()),
                "stderr_tail": "", "timed_out": False, "timeout_seconds": timeout,
                "elapsed_seconds": 1.0}
        campaign.atomic_json(Path(folder) / "outer_heldout.json", step)
        return step

    monkeypatch.setattr(driver, "run_heldout", run_heldout)
    return calls


def materialize(name, tmp_path):
    """A writable copy of a fixture cell: control, runtime with stage1, config."""
    source = FIXTURES / name
    control, repo = tmp_path / "control", tmp_path / "sim"
    (control / "heldout").mkdir(parents=True)
    shutil.copytree(source / "heldout/frozen", control / "heldout/frozen")
    for file in ("heldout_manifest.json", "heldout_result.json", "heldout_state.json"):
        shutil.copy(source / "heldout" / file, control / "heldout" / file)
    for line in (source / "heldout/seed_dirs.txt").read_text().split():
        (control / "heldout" / line).mkdir()
        (control / "heldout" / line / "marker").write_text(line)
    (control / "campaign_state.json").write_bytes(gzip.decompress((source / "campaign_state.json.gz").read_bytes()))
    case = json.loads((source / "slot-case.json").read_text())
    case.update(sim=str(repo), control=str(control), claude_config_dir=str(control / "native/config"))
    task_dir = repo / "outputs/libero_fix_loop" / case["suite"] / case["task"]
    task_dir.mkdir(parents=True)
    (task_dir / "stage1_result.json").write_bytes(gzip.decompress((source / "stage1_result.json.gz").read_bytes()))
    (repo / case["env_config"]).parent.mkdir(parents=True)
    shutil.copy(SIM / case["env_config"], repo / case["env_config"])
    case_path = tmp_path / "slot-case.json"
    case_path.write_text(json.dumps(case))
    return case, case_path, control


def rows(control):
    return json.loads((control / "heldout/heldout_state.json").read_text())["seeds"]


# ---- fix 1 in the driver -----------------------------------------------------------


def test_driver_derives_the_heldout_deadline_from_the_seeds_still_to_run(driver, tmp_path):
    evaluation = tmp_path / "heldout"
    evaluation.mkdir()
    assert driver.heldout_pending(evaluation) == list(range(1, 51))
    (evaluation / "heldout_state.json").write_text(json.dumps(
        {"identity": {}, "seeds": {str(s): {"status": "failure"} for s in range(1, 45)}}))
    pending = driver.heldout_pending(evaluation)
    assert pending == list(range(45, 51))
    args = SimpleNamespace(deadline_heldout=None)
    assert driver.heldout_deadline(args, {"trial_timeout": 900}, pending) == deadlines.heldout_deadline(6, 900)
    assert driver.heldout_deadline(args, {"trial_timeout": 900}, list(range(1, 51))) == deadlines.HELDOUT
    assert driver.heldout_deadline(SimpleNamespace(deadline_heldout=77), {"trial_timeout": 900}, pending) == 77


def test_full_run_hands_the_derived_deadline_to_the_heldout_sweep(driver, tmp_path, monkeypatch):
    repo, control = tmp_path / "sim", tmp_path / "control"
    repo.mkdir(); control.mkdir()
    case = {"sim": str(repo), "control": str(control), "id": "synthetic_C", "condition": "C",
            "suite": "synthetic", "task": "synthetic", "dev_seeds": list(range(51, 66)),
            "campaign_timeout": 36000, "trial_timeout": 900, "claude_config_dir": str(control / "config")}
    case_path = tmp_path / "case.json"
    case_path.write_text(json.dumps(case))
    campaign = driver.campaign_stub
    for name, value in (("native_settings", lambda *a: {"hooks": {"PreToolUse": []}}),
                        ("perception_ready", lambda *a: {}), ("probe", lambda *a: {}),
                        ("native_command", lambda *a, **kw: ["never"]), ("worker_agent", lambda *a: {})):
        setattr(campaign, name, value)

    def prompts(*args):
        (control / "worker-prompt.md").write_text("w"); (control / "coordinator-prompt.md").write_text("c")
        return control / "worker-prompt.md", control / "coordinator-prompt.md"

    ready = {"exit_code": 0, "result": {"ready": True, "errors": [],
                                        "progress": {"seeds_exhausted_without_graded_evidence": []}}}
    campaign.write_prompts = prompts
    campaign.protocol = lambda case, repo, env, folder, step, command, *a: (
        ready if command == "check" else {"exit_code": 0, "result": {}})
    for name, value in (("verify_local_provider", lambda *a: {}), ("verify_capacity", lambda *a: {}),
                        ("fault", lambda *a: None), ("identity", lambda *a: ("s", "w")),
                        ("audit_assignment", lambda *a: {}), ("verify_received_assignment", lambda *a: {})):
        monkeypatch.setattr(driver, name, value)
    monkeypatch.setattr(driver.output_ownership, "verify", lambda *a: {})
    monkeypatch.setattr(driver.runtime_limits, "install", lambda settings, case: settings)
    monkeypatch.setattr(driver.fixed_transport, "run_native_cc", lambda *a, **kw: 0)

    def manifest(case, repo, task_dir, evaluation):
        evaluation.mkdir(parents=True, exist_ok=True)
        (evaluation / "heldout_manifest.json").write_text("{}")
        return {"row_count": 50, "seeds": list(range(1, 51)), "selected_bundle": {}}

    monkeypatch.setattr(driver, "heldout_manifest", manifest)
    seen = []

    def run_heldout(campaign, repo, env, folder, argv, timeout):
        seen.append(timeout)
        return {"exit_code": 1, "timed_out": True, "elapsed_seconds": timeout, "result": None}

    monkeypatch.setattr(driver, "run_heldout", run_heldout)
    assert driver.main(["--case", str(case_path)]) == 1
    state = json.loads((control / "campaign_state.json").read_text())
    assert seen == [deadlines.HELDOUT]
    assert state["deadlines"]["heldout_deadline_seconds"] == deadlines.HELDOUT
    assert state["blocked"]["phase"] == "heldout" and state["blocked"]["detail"]["deadline_seconds"] == deadlines.HELDOUT


# ---- fix 2: the supplement bookkeeping -----------------------------------------------


def synthetic_evaluation(tmp_path, statuses):
    evaluation = tmp_path / "heldout"
    evaluation.mkdir()
    seeds = {str(s): {"seed": s, "status": status} for s, status in statuses.items()}
    (evaluation / "heldout_state.json").write_text(json.dumps({"identity": {"cell": "x"}, "seeds": seeds}))
    for seed in statuses:
        (evaluation / f"seed_{seed:02d}").mkdir()
    return evaluation


def test_plan_keeps_graded_rows_and_reruns_interrupted_and_missing_seeds(tmp_path):
    statuses = {s: "failure" for s in range(1, 41)} | {3: "success", 7: "crash", 41: "infrastructure_error"}
    evaluation = synthetic_evaluation(tmp_path, statuses)
    (evaluation / "seed_44").mkdir()  # interrupted before its row was written
    planned = supplement.plan(evaluation)
    assert planned["rerun_infrastructure_error"] == [41]
    assert planned["missing"] == list(range(42, 51))
    assert planned["orphan_dirs"] == [44]
    assert planned["seeds_to_run"] == list(range(41, 51))
    assert planned["kept_counts"] == {"success": 1, "failure": 38, "crash": 1}
    assert planned["problems"] == []


def test_plan_refuses_unknown_statuses_and_foreign_seeds(tmp_path):
    evaluation = synthetic_evaluation(tmp_path, {1: "running", 51: "failure"})
    assert len(supplement.plan(evaluation)["problems"]) == 2


@pytest.mark.parametrize("state, ok", [
    ({"status": "blocked", "blocked": {"phase": "heldout", "state_preserved": True},
      "heldout_manifest": {"sha256": "m"}}, True),
    ({"status": "blocked", "blocked": {"phase": "heldout_running", "state_preserved": True},
      "heldout_manifest": {"sha256": "m"}}, True),
    ({"status": supplement.RUNNING}, True),
    ({"status": "blocked", "blocked": {"phase": "development", "state_preserved": True},
      "heldout_manifest": {"sha256": "m"}}, False),
    ({"status": "blocked", "blocked": {"phase": "heldout"}, "heldout_manifest": {"sha256": "m"}}, False),
    ({"status": "blocked", "blocked": {"phase": "heldout", "state_preserved": True}}, False),
    ({"status": "full_complete"}, False),
    ({"status": "heldout_running"}, False),
    (None, False)])
def test_eligibility(state, ok):
    assert (supplement.eligibility(state) == []) is ok


def test_prepare_snapshots_then_moves_and_strips_only_the_rerun_seeds(tmp_path):
    statuses = {s: "failure" for s in range(1, 45)} | {45: "infrastructure_error"}
    evaluation = synthetic_evaluation(tmp_path, statuses)
    (evaluation / "heldout_result.json").write_text('{"r": 1}')
    state = tmp_path / "campaign_state.json"
    state.write_text('{"status": "blocked"}')
    before = (evaluation / "heldout_state.json").read_bytes()
    planned = supplement.plan(evaluation)
    attempt = supplement.new_attempt(evaluation)
    record = supplement.prepare(evaluation, planned, attempt, state)
    assert attempt == evaluation / "supplement/1"
    assert (attempt / "heldout_state.before.json").read_bytes() == before
    assert (attempt / "heldout_result.before.json").read_text() == '{"r": 1}'
    assert (attempt / "campaign_state.before.json").read_text() == '{"status": "blocked"}'
    assert record["superseded_rows"] == {"45": {"seed": 45, "status": "infrastructure_error"}}
    assert not (evaluation / "seed_45").exists() and (attempt / "superseded/seed_45").is_dir()
    after = json.loads((evaluation / "heldout_state.json").read_text())["seeds"]
    assert sorted(map(int, after)) == list(range(1, 45))
    assert json.loads((attempt / "supplement.json").read_text())["status"] == "prepared"
    assert supplement.new_attempt(evaluation) == evaluation / "supplement/2"


def test_prepare_refuses_a_complete_sweep(tmp_path):
    evaluation = synthetic_evaluation(tmp_path, {s: "failure" for s in range(1, 51)})
    with pytest.raises(ValueError, match="nothing to supplement"):
        supplement.prepare(evaluation, supplement.plan(evaluation), tmp_path / "a", tmp_path / "s")


def test_stage_entry_copies_the_case_and_records_the_source(tmp_path):
    source = tmp_path / "old/cell_x"
    evaluation = synthetic_evaluation(tmp_path, {s: "failure" for s in range(1, 41)} | {41: "infrastructure_error"})
    source.mkdir(parents=True)
    shutil.move(str(evaluation), source / "heldout")
    case = {"id": "cell_x", "control": str(source)}
    (source / "case.json").write_text(json.dumps(case))
    (source / "campaign_state.json").write_text(json.dumps(
        {"status": "blocked", "blocked": {"phase": "heldout", "state_preserved": True},
         "heldout_manifest": {"sha256": "m"}}))
    entry = tmp_path / "new/cell_x"
    marker = supplement.stage_entry(source, entry)
    assert (entry / "case.json").read_bytes() == (source / "case.json").read_bytes()
    assert marker["seeds_to_run_at_staging"] == list(range(41, 51))
    assert supervisor.check_supplement_entry(entry, case)["source_control"] == str(source)
    with pytest.raises(FileExistsError):
        supplement.stage_entry(source, entry)


def test_supervisor_checks_supplement_entries_and_ordinary_cells(tmp_path):
    cell = tmp_path / "root/cell_x"
    cell.mkdir(parents=True)
    case = {"id": "cell_x", "control": str(cell)}
    (cell / "case.json").write_text(json.dumps(case))
    assert supervisor.check_supplement_entry(cell, case) is None
    with pytest.raises(RuntimeError, match="not its queue cell"):
        supervisor.check_supplement_entry(cell, dict(case, control=str(tmp_path / "elsewhere")))
    source = tmp_path / "old/cell_x"
    case = {"id": "cell_x", "control": str(source)}
    (cell / "case.json").write_text(json.dumps(case))
    (cell / layout.SUPPLEMENT_MARKER).write_text(json.dumps(
        {"cell": "cell_x", "source_control": str(source), "source_case_sha256": "wrong"}))
    with pytest.raises(RuntimeError, match="not the source cell's staged case"):
        supervisor.check_supplement_entry(cell, case)


def test_infra_requeue_keeps_a_supplement_entry(tmp_path):
    cell = tmp_path / "cell_x"
    cell.mkdir()
    (cell / "case.json").write_text("{}")
    (cell / layout.SUPPLEMENT_MARKER).write_text("{}")
    (cell / "slot-case.json").write_text("{}")
    result = layout.infra_requeue(tmp_path, cell, {"state": "infrastructure"})
    assert result["requeued"] and result["moved"] == ["slot-case.json"]
    assert (cell / layout.SUPPLEMENT_MARKER).is_file() and (cell / "case.json").is_file()


# ---- fix 2 end to end: the driver's --heldout-supplement -------------------------------


def test_supplement_runs_only_the_missing_seeds_and_completes_the_cell(driver, evaluator, tmp_path):
    case, case_path, control = materialize("twomoka_p4n_r1", tmp_path)
    original = rows(control)
    assert driver.main(["--case", str(case_path), "--heldout-supplement"]) == 0
    assert evaluator.seeds == list(range(41, 51))
    assert evaluator.runs[0]["argv"] == ["--case", str(case_path), "--resume"]
    assert evaluator.runs[0]["timeout"] == deadlines.heldout_deadline(10, 900)
    final = rows(control)
    assert {k: final[k] for k in map(str, range(1, 41))} == {k: original[k] for k in map(str, range(1, 41))}
    report = json.loads((control / "heldout/heldout_result.json").read_text())
    assert report["all_seeds_accounted"] is True and report["unusable_seeds"] == []
    assert report["supplementary_seeds"] == list(range(41, 51))
    assert report["original_seeds"] == list(range(1, 41))
    state = json.loads((control / "campaign_state.json").read_text())
    assert state["status"] == "full_complete" and state["blocked"] is None
    entry = state["heldout_supplements"][0]
    assert entry["status"] == "complete" and entry["prior"]["blocked"]["phase"] == "heldout"
    assert supervisor.accept_full(state, 0)["heldout"]["rows_accounted"] == 50
    record = json.loads((control / "heldout/supplement/1/supplement.json").read_text())
    assert record["superseded_rows"]["41"]["error"].startswith("TrialInterrupted")
    # Done is done: a second supplement is refused and changes nothing.
    with pytest.raises(ValueError, match="cannot be supplemented"):
        driver.main(["--case", str(case_path), "--heldout-supplement"])


def test_an_interrupted_supplement_can_itself_be_supplemented(driver, evaluator, tmp_path):
    case, case_path, control = materialize("twomoka_p4n_r1", tmp_path)
    evaluator.outcome = {44: "infrastructure_error"}
    assert driver.main(["--case", str(case_path), "--heldout-supplement"]) == 1
    state = json.loads((control / "campaign_state.json").read_text())
    assert state["status"] == "blocked" and state["blocked"]["phase"] == "heldout"
    assert state["heldout_supplements"][0]["status"] == "blocked"
    assert evaluator.seeds == [41, 42, 43, 44]  # stop_on_infra ends the sweep at the infra row
    evaluator.outcome = {}
    assert driver.main(["--case", str(case_path), "--heldout-supplement"]) == 0
    assert evaluator.seeds == [41, 42, 43, 44, 44, 45, 46, 47, 48, 49, 50]
    report = json.loads((control / "heldout/heldout_result.json").read_text())
    assert report["all_seeds_accounted"] and report["supplementary_seeds"] == list(range(41, 51))
    # 41-43 were graded by the blocked first attempt and kept; 44 on was re-run by the second.
    owner = report["supplement"]["attempt_of_seed"]
    assert [owner[str(s)] for s in range(41, 51)] == ["1"] * 3 + ["2"] * 7
    assert report["original_seeds"] == list(range(1, 41))
    assert [a["status"] for a in report["supplement"]["attempts"]] == ["blocked", "complete"]
    assert (control / "heldout/supplement/2/superseded/seed_44").is_dir()


def test_a_development_block_is_never_supplemented(driver, evaluator, tmp_path):
    case, case_path, control = materialize("twomoka_p4n_r1", tmp_path)
    state = json.loads((control / "campaign_state.json").read_text())
    state["blocked"]["phase"] = "development"
    (control / "campaign_state.json").write_text(json.dumps(state))
    before = sorted(p.name for p in (control / "heldout").iterdir())
    with pytest.raises(ValueError, match="not the held-out sweep"):
        driver.main(["--case", str(case_path), "--heldout-supplement"])
    assert sorted(p.name for p in (control / "heldout").iterdir()) == before
    assert evaluator.seeds == []


# ---- replay: spatialbetween_p4n_r1's real held-out state ------------------------------


def test_replay_spatialbetween_p4n_r1_supplements_exactly_seeds_45_to_50(driver, evaluator, tmp_path):
    source = FIXTURES / "spatialbetween_p4n_r1"
    assert "spatialbetween_p4n_r1" in (source / "SOURCE").read_text()
    case, case_path, control = materialize("spatialbetween_p4n_r1", tmp_path)
    original = rows(control)
    planned = supplement.plan(control / "heldout")
    assert planned["kept"] == list(range(1, 45)) and planned["kept_counts"] == {
        "success": 29, "failure": 15, "crash": 0}
    assert planned["rerun_infrastructure_error"] == [45] and planned["missing"] == [46, 47, 48, 49, 50]
    assert planned["seeds_to_run"] == [45, 46, 47, 48, 49, 50]
    assert supplement.eligibility(json.loads((control / "campaign_state.json").read_text())) == []

    assert driver.main(["--case", str(case_path), "--heldout-supplement"]) == 0
    assert evaluator.seeds == [45, 46, 47, 48, 49, 50]
    assert evaluator.runs[0]["timeout"] == deadlines.heldout_deadline(6, 900)
    final = rows(control)
    assert all(final[str(s)] == original[str(s)] for s in range(1, 45))
    report = json.loads((control / "heldout/heldout_result.json").read_text())
    assert report["all_seeds_accounted"] is True
    assert report["supplementary_seeds"] == [45, 46, 47, 48, 49, 50]
    assert report["identity"] == json.loads((source / "heldout/heldout_state.json").read_text())["identity"]
    assert report["counts"]["success"] == 29  # the 6 fixture seeds report failure
    record = json.loads((control / "heldout/supplement/1/supplement.json").read_text())
    assert record["superseded_rows"]["45"] == original["45"]
    assert (control / "heldout/supplement/1/superseded/seed_45/marker").read_text() == "seed_45"
    for seed in range(1, 45):
        assert (control / f"heldout/seed_{seed:02d}/marker").read_text() == f"seed_{seed:02d}"
    state = json.loads((control / "campaign_state.json").read_text())
    accepted = supervisor.accept_full(state, 0)
    assert accepted["heldout"]["rows_accounted"] == 50 and accepted["heldout"]["identity_verified"]


def test_replay_twomoka_p4n_r1_plan_is_seeds_41_to_50():
    evaluation = FIXTURES / "twomoka_p4n_r1/heldout"
    rows_ = json.loads((evaluation / "heldout_state.json").read_text())["seeds"]
    assert len(rows_) == 41 and rows_["41"]["status"] == "infrastructure_error"
    planned = supplement.plan(evaluation)  # read-only: no seed dirs in the fixture
    assert planned["seeds_to_run"] == list(range(41, 51))
    assert planned["kept_counts"] == {"success": 0, "failure": 40, "crash": 0}


def test_replay_plain_resume_would_leave_seed_45_unusable(driver, evaluator, tmp_path):
    """Why a supplement path was needed: the evaluator's own --resume cannot finish it.

    It skips seed 45 (it has a row), and `heldout_stop_on_infra` sees that old
    infrastructure row again after the next ledger write and stops after one seed.
    """
    case, case_path, control = materialize("spatialbetween_p4n_r1", tmp_path)
    step = driver.run_heldout(driver.campaign_stub, None, {}, tmp_path,
                              ["--case", str(case_path), "--resume"], 1)
    assert evaluator.seeds == [46]
    assert step["result"]["stopped_after_infrastructure_error"] is True
    assert step["result"]["all_seeds_accounted"] is False
    assert step["result"]["unusable_seeds"] == [45]
    manifest = json.loads((control / "heldout/heldout_manifest.json").read_text())
    with pytest.raises(driver.Blocked) as stop:
        driver.check_heldout_rows(manifest, step["result"])
    assert stop.value.payload["detail"]["unusable_seeds"] == [45]
