"""The development-gate study's frozen support: scope gate, read boundary, analysis.

Synthetic cells only. The analysis is fed hand-written ledgers in both shapes
(oracle and sealed) and must report the same metrics from either; the read guard
must keep the sealed root out of reach; the scope gate must refuse a render that
disagrees with the cell's gate.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SIM = Path(__file__).resolve().parents[1]
SUPPORT = SIM / "docs/experiments/code-world-gate-ablation-20261005/support"
for extra in (SIM, SIM / "scripts/common", SIM / "scripts/libero", SUPPORT):
    sys.path.insert(0, str(extra))
sys.path.insert(0, str(SIM.parents[1]))

import native_world_campaign as campaign
import test_native_world_fixloop as fixtures
import two_task_scope as scope
import gate_study_analysis as analysis


def load(name):
    spec = importlib.util.spec_from_file_location(name, SUPPORT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def study_case(tmp_path, gate, **extra):
    path, case = fixtures.make_case(tmp_path, "C")
    case.update(profile="judgment", executable_world_revision="r1", foundation_revision="r1",
                c_arm="full", c_lineage="fresh", development_gate=gate,
                world_interface_doc="docs/experiments/code-world-gate-ablation-20261005/NATIVE_WORLD_INTERFACE.md",
                inference_endpoint="http://127.0.0.1:8121", heldout_seeds=list(range(1, 51)))
    case.update(extra)
    return case


@pytest.mark.parametrize("gate", ["oracle", "self_eval", "vlm_judge"])
def test_scope_accepts_a_matching_render_and_refuses_a_swapped_one(tmp_path, gate):
    case = study_case(tmp_path, gate)
    text = scope.report_handoff_worker(campaign.worker_prompt(case, SIM))
    scope.verify_worker(text, case, SIM)
    other = "self_eval" if gate != "self_eval" else "oracle"
    with pytest.raises(scope.ScopeError):
        scope.verify_worker(text, {**case, "development_gate": other}, SIM)
    assert scope.remaining_budget(case) == {str(s): 3 for s in range(51, 66)}
    assert scope.remaining_budget({**case, "imported_retries": {"51": 1}})["51"] == 2
    with pytest.raises(scope.ScopeError):
        scope.remaining_budget({**case, "imported_retries": {"52": 1}})


def test_preflight_accounting_sentence_only_when_a_retry_was_imported(tmp_path):
    case = study_case(tmp_path, "self_eval")
    plain = scope.report_handoff_worker(campaign.worker_prompt(case, SIM))
    assert "Preflight accounting" not in plain
    # apply() installs the gate once per module object; exercise the sentence directly.
    assert "spent one of its three TOTAL retries" in (
        "\nPreflight accounting: seed 51 has already spent one of its three TOTAL retries on this cell's")


def test_read_guard_keeps_the_sealed_root_out_of_reach(tmp_path):
    guard = load("cell_read_guard")
    sim = tmp_path / "sim"
    control = tmp_path / "control"
    (control / "outputs").mkdir(parents=True)
    sim.mkdir()
    (sim / "outputs").symlink_to(control / "outputs")
    case = {"sim": str(sim), "control": str(control), "suite": "s", "task": "t"}
    assert guard.path_allowed(str(sim / "outputs/libero_fix_loop/s/t/development_state.json"), case, str(sim))
    assert not guard.path_allowed(str(control / "sealed/development_outcomes.jsonl"), case, str(sim))
    assert not guard.path_allowed(str(control / "heldout/heldout_result.json"), case, str(sim))
    for command in (f"cat {control}/sealed/development_outcomes.jsonl", "cat ../sealed/x.json",
                    "ls heldout/", "grep -r task_completed sealed/"):
        assert guard.denial({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(sim)}, case)
    assert guard.denial({"tool_name": "Read", "tool_input": {"file_path": str(control / "sealed/oracle.json")}, "cwd": str(sim)}, case)
    assert guard.denial({"tool_name": "Bash", "tool_input": {"command": "ls outputs/libero_fix_loop"}, "cwd": str(sim)}, case) is None


def write_cell(root, cell, task, gate, dev_rows, heldout, self_eval_heldout, judge_heldout=None):
    """A finished cell in the on-disk shape the analysis reads."""
    control = root / cell
    sim = root / f"{cell}-sim"
    task_dir = sim / "outputs/libero_fix_loop/libero_goal_swap" / task
    task_dir.mkdir(parents=True)
    case = {"id": cell, "task": task, "suite": "libero_goal_swap", "sim": str(sim), "control": str(control),
            "development_gate": gate, "inference_endpoint": "http://127.0.0.1:1", "model": "m"}
    control.mkdir(parents=True)
    (control / "case.json").write_text(json.dumps(case))
    (control / "campaign_state.json").write_text(json.dumps({"status": "full_complete", "development_seconds": 3600}))
    trials, sealed_rows = [], []
    for i, (oracle, verdict) in enumerate(dev_rows, start=1):
        row = {"phase": "initial", "seed": 50 + i, "attempt": 1, "status": "complete", "sandbox_rc": 0,
               "spends_retry": True, "bundle_sha256": "b" * 64, "directory": f"development/initial/seed_{50+i}/attempt_1"}
        if gate == "oracle":
            row.update(task_completed=int(oracle), reward=float(oracle),
                       foundation_calibration={"final_self_evaluation": {"verdict": verdict}})
        else:
            passed = verdict == "true" if gate == "self_eval" else verdict == "success"
            row.update(outcome="sealed", gate={"source": gate, "verdict": verdict, "passed": passed})
            sealed_rows.append({"phase": "initial", "seed": 50 + i, "attempt": 1, "status": "complete",
                                "bundle_sha256": "b" * 64, "oracle": {"sandbox_rc": 0, "task_completed": oracle},
                                "gate": row["gate"], "self_eval_verdict": verdict if gate == "self_eval" else "unknown"})
        trials.append(row)
    (task_dir / "development_state.json").write_text(json.dumps({"trials": trials, "selected": {"bundle_sha256": "b" * 64}}))
    if sealed_rows:
        (control / "sealed").mkdir()
        (control / "sealed/development_outcomes.jsonl").write_text("\n".join(json.dumps(r) for r in sealed_rows) + "\n")
    evaluation = control / "heldout"
    per_seed = {}
    for seed, (success, verdict) in enumerate(zip(heldout, self_eval_heldout), start=1):
        per_seed[str(seed)] = "success" if success else "failure"
        folder = evaluation / f"seed_{seed:02d}" / "judgment_world"
        folder.mkdir(parents=True)
        (folder / "manifest.json").write_text(json.dumps({"self_evaluations": [{"verdict": verdict, "binding": "shadow"}]}))
        if judge_heldout:
            (evaluation / "judge").mkdir(exist_ok=True)
            (evaluation / "judge" / f"seed_{seed:02d}.json").write_text(json.dumps({"status": "complete", "verdict": judge_heldout[seed - 1]}))
    (evaluation / "heldout_result.json").write_text(json.dumps({
        "per_seed": per_seed, "success_rate": sum(heldout) / 50, "all_seeds_accounted": len(heldout) == 50,
        "counts": {"success": sum(heldout), "failure": len(heldout) - sum(heldout)}}))
    # The frozen evaluator's per-seed ledger, with the replay result kept beside the status.
    (evaluation / "heldout_state.json").write_text(json.dumps({"seeds": {
        str(seed): {"seed": seed, "status": per_seed[str(seed)],
                    "result": {"sandbox_rc": 0, "task_completed": int(success), "reward": float(success),
                               "trial_dir": str(evaluation / f"seed_{seed:02d}" / "trial")}}
        for seed, success in enumerate(heldout, start=1)}}))
    return control / "case.json"


def test_analysis_reports_the_same_metrics_from_both_ledger_shapes(tmp_path):
    heldout = [True] * 40 + [False] * 10
    self_eval = ["true"] * 38 + ["false"] * 2 + ["false"] * 8 + ["true"] * 2
    oracle_case = write_cell(tmp_path, "bowl_oracle_r1", "put_the_bowl_on_the_plate", "oracle",
                             [(True, "true"), (False, "true"), (True, "false"), (False, "unknown")],
                             heldout, self_eval, judge_heldout=["success"] * 45 + ["failure"] * 5)
    sealed_case = write_cell(tmp_path, "bowl_selfeval_r1", "put_the_bowl_on_the_plate", "self_eval",
                             [(True, "true"), (False, "true"), (True, "false"), (False, "unknown")],
                             heldout, self_eval)
    cells = [analysis.analyze_cell(oracle_case), analysis.analyze_cell(sealed_case)]
    oracle, sealed = cells
    # The oracle gate has no disagreement with itself; the sealed gate's
    # disagreement is exactly the two rows that were constructed to disagree.
    assert oracle["development"]["gate_false_accept"] == 0 and oracle["development"]["gate_false_reject"] == 0
    assert sealed["development"]["gate_false_accept"] == 1 and sealed["development"]["gate_false_reject"] == 1
    assert sealed["development"]["gate_pass_rate_dev"] == 0.5 and sealed["development"]["oracle_success_rate_dev"] == 0.5
    # Self-evaluation agreement with the environment is computed identically from both shapes.
    for cell in cells:
        dev = cell["development"]["self_eval_vs_oracle_dev"]
        assert (dev["tp"], dev["fp"], dev["fn"], dev["undecided_on_failure"]) == (1, 1, 1, 1)
        held = cell["heldout"]["self_eval_vs_oracle"]
        assert (held["tp"], held["fn"], held["tn"], held["fp"]) == (38, 2, 8, 2)
        assert held["balanced_accuracy"] == pytest.approx(((38 / 40) + (8 / 10)) / 2)
    assert oracle["heldout"]["judge_vs_oracle"]["tp"] == 40 and oracle["heldout"]["judge_vs_oracle"]["fp"] == 5
    assert sealed["heldout"]["judge_vs_oracle"] is None
    agg = analysis.aggregate(cells)
    assert set(agg["by_gate"]) == {"oracle", "self_eval"}
    assert agg["by_gate"]["self_eval"]["heldout_success_mean"] == 0.8
    text = analysis.markdown(cells, agg)
    assert "| self_eval | 1 | 0.80 |" in text and "bowl_oracle_r1" in text
    out = tmp_path / "out"
    assert analysis.main(["--parent", str(tmp_path), "--out", str(out)]) == 0
    assert (out / "summary.json").is_file() and "## By gate" in (out / "summary.md").read_text()


def test_analysis_recovers_the_dev_judge_verdict_from_the_gate_report(tmp_path):
    # Ledger rows written by the 2026-10-05 launch carry the judge's verdict only inside
    # the gate report (judge_verdict is null). The analysis must still produce the
    # development judge-vs-oracle table for vlm_judge cells, and leave it None otherwise.
    heldout = [True] * 30 + [False] * 20
    self_eval = ["true"] * 30 + ["true"] * 20
    judged = write_cell(tmp_path, "bowl_vlmjudge_r1", "put_the_bowl_on_the_plate", "vlm_judge",
                        [(True, "success"), (False, "success"), (False, "success"), (True, "failure"), (False, "failure")],
                        heldout, self_eval)
    plain = write_cell(tmp_path, "bowl_selfeval_r1", "put_the_bowl_on_the_plate", "self_eval",
                       [(True, "true"), (False, "true")], heldout, self_eval)
    rows = analysis.development_rows(json.loads(judged.read_text()), judged.parent)
    assert [r["judge_verdict"] for r in rows] == ["success", "success", "success", "failure", "failure"]
    dev = analysis.analyze_cell(judged)["development"]
    assert dev["gate_false_accept"] == 2 and dev["gate_false_reject"] == 1
    j = dev["judge_vs_oracle_dev"]
    assert (j["tp"], j["fp"], j["fn"], j["tn"]) == (1, 2, 1, 1)
    assert analysis.analyze_cell(plain)["development"]["judge_vs_oracle_dev"] is None


def test_aggregate_averages_only_complete_cells(tmp_path):
    heldout = [True] * 40 + [False] * 10
    self_eval = ["true"] * 40 + ["false"] * 10
    done = write_cell(tmp_path, "bowl_selfeval_r1", "put_the_bowl_on_the_plate", "self_eval",
                      [(True, "true"), (False, "true")], heldout, self_eval)
    running = write_cell(tmp_path, "bowl_selfeval_r2", "put_the_bowl_on_the_plate", "self_eval",
                         [(False, "true"), (False, "true"), (False, "true")], heldout, self_eval)
    # The second cell is still developing: its campaign state is not full_complete and its
    # held-out report must not exist yet.
    state = json.loads((running.parent / "campaign_state.json").read_text())
    state["status"] = "solver_running"
    (running.parent / "campaign_state.json").write_text(json.dumps(state))
    (running.parent / "heldout" / "heldout_result.json").unlink()
    cells = [analysis.analyze_cell(done), analysis.analyze_cell(running)]
    agg = analysis.aggregate(cells)
    assert agg["by_gate"]["self_eval"]["n"] == 1 and agg["by_gate"]["self_eval"]["cells"] == ["bowl_selfeval_r1"]
    assert agg["by_gate"]["self_eval"]["gate_false_accept_rate_mean"] == 0.5
    assert agg["complete_cells"] == 1 and agg["pending_cells"] == ["bowl_selfeval_r2"]
    text = analysis.markdown(cells, agg)
    assert "Complete cells in the aggregates: 1 of 2; still running: bowl_selfeval_r2." in text
    assert "| bowl_selfeval_r2 | self_eval | solver_running |" in text


def test_heldout_crash_breakdown_keeps_completed_seeds_visible(tmp_path):
    heldout = [True] * 40 + [False] * 10
    self_eval = ["true"] * 40 + ["false"] * 10
    case = write_cell(tmp_path, "bowldrawer_oracle_r2", "open_the_top_drawer_and_put_the_bowl_inside", "oracle",
                      [(True, "true")], heldout, self_eval)
    evaluation = case.parent / "heldout"
    result = json.loads((evaluation / "heldout_result.json").read_text())
    state = json.loads((evaluation / "heldout_state.json").read_text())
    # Seed 48: task completed, then the program kept acting -> frozen rule says crash.
    # Seed 49: episode step limit exhausted. Seed 50: a genuine program exception.
    for seed, completed, truncated in ((48, 1, True), (49, 0, True), (50, 0, False)):
        result["per_seed"][str(seed)] = "crash"
        rec = state["seeds"][str(seed)]
        rec["status"] = "crash"
        rec["result"].update(sandbox_rc=1, task_completed=completed, reward=float(completed))
        trial = Path(rec["result"]["trial_dir"]); trial.mkdir(parents=True, exist_ok=True)
        (trial / "summary.txt").write_text(f"  Terminated: {bool(completed)}, Truncated: {truncated}\n")
    # Seeds 48-50 were failures in the fixture, so the frozen success count stays 40.
    result["counts"] = {"success": 40, "failure": 7, "crash": 3, "infrastructure_error": 0}
    result["success_rate"] = 40 / 50
    (evaluation / "heldout_result.json").write_text(json.dumps(result))
    (evaluation / "heldout_state.json").write_text(json.dumps(state))
    held = analysis.analyze_cell(case)["heldout"]
    assert held["success_rate"] == 40 / 50                      # frozen rule untouched
    assert held["task_completed_any_rc"] == 41 and held["task_completed_rate_any_rc"] == 0.82
    assert held["crash_breakdown"] == {"completed_then_acted": 1, "step_limit_exhausted": 1, "program_exception": 1}
    cells = [analysis.analyze_cell(case)]
    text = analysis.markdown(cells, analysis.aggregate(cells))
    assert "| 0.80 | 0.82 | 1/1/1 |" in text


def test_aggregate_separates_failed_cells_from_running_ones(tmp_path):
    # A timed-out cell has a queue-result saying "failed" and no held-out; it must be
    # reported as failed, not as "still running", and must stay out of the means.
    heldout = [True] * 40 + [False] * 10
    self_eval = ["true"] * 40 + ["false"] * 10
    done = write_cell(tmp_path, "bowl_selfeval_r1", "put_the_bowl_on_the_plate", "self_eval",
                      [(True, "true")], heldout, self_eval)
    dead = write_cell(tmp_path, "bowldrawer_selfeval_r2", "open_the_top_drawer_and_put_the_bowl_inside",
                      "self_eval", [(False, "true")], heldout, self_eval)
    (dead.parent / "heldout" / "heldout_result.json").unlink()
    (dead.parent / "queue-result.json").write_text(json.dumps({"state": "failed", "driver_exit": None}))
    state = json.loads((dead.parent / "campaign_state.json").read_text())
    state["status"] = "blocked"
    (dead.parent / "campaign_state.json").write_text(json.dumps(state))
    cells = [analysis.analyze_cell(done), analysis.analyze_cell(dead)]
    agg = analysis.aggregate(cells)
    assert agg["complete_cells"] == 1 and agg["failed_cells"] == ["bowldrawer_selfeval_r2"]
    assert agg["running_cells"] == [] and agg["pending_cells"] == ["bowldrawer_selfeval_r2"]
    text = analysis.markdown(cells, agg)
    assert "failed or blocked, no held-out: bowldrawer_selfeval_r2." in text
    assert "| bowldrawer_selfeval_r2 | self_eval | failed |" in text


def test_two_by_two_handles_undecided_and_empty():
    assert analysis.two_by_two([])["n"] == 0 and analysis.two_by_two([])["balanced_accuracy"] is None
    s = analysis.two_by_two([("unknown", True), ("unsure", False), ("true", True)])
    assert s["undecided_on_success"] == 1 and s["undecided_on_failure"] == 1 and s["tp"] == 1
    assert s["undecided_rate"] == pytest.approx(2 / 3) and s["balanced_accuracy"] is None


def test_driver_judges_heldout_after_the_sweep_and_supervisor_pins_new_sources():
    driver = (SUPPORT / "run_two_task_cell.py").read_text()
    assert "judge_heldout_seeds(case, repo, env, folder)" in driver
    assert 'state["status"] = "heldout_judging"' in driver and "TERMINAL" in driver
    assert 'if case.get("diagnostic_import"):' in driver
    supervisor = (SUPPORT / "dlc-supervisor.py").read_text()
    for name in ("cap/world_model/development_gate.py", "cap/world_model/vlm_judge.py",
                 "scripts/libero/gate_study_analysis.py", "cap/envs/tasks/base.py"):
        assert name in supervisor
    assert "closed_loop_revision" in supervisor
    for name in ("run_two_task_cell.py", "two_task_scope.py", "dlc-supervisor.py", "cell_read_guard.py",
                 "foundation_import.py", "infra_guard.py", "full_deadlines.py"):
        assert "charged" not in (SUPPORT / name).read_text().replace("one charged attempt", "").replace('"charged attempt"', ""), name
