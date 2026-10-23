"""CC_TASK_14 (p6d): method switches M2/M3/M4, success latch, held-out timeout rule.

Every switch is off without the case key `method_switches`; the existing suites pin
that the legacy renders, identities and scores are unchanged. These tests pin what
each switch does when it is set:

  A. switch validation, identity and prompt render;
  B. sweep-only version score (M2a) and the pre-freeze re-sweep (M2b);
  C. record-only screening (M3c) and judgment calibration feedback (M3b);
  D. held-out: watchdog settle rule, unseen seed set (M2d);
  E. policy API m4-r1 (solve_ik report, segmented goto_pose, seeded subsample, GraspNet);
  F. success latch (libero_latch) in the simulator, the code env and the world session;
  G. the p6d driver's deadline reserve and outer steps.
"""
from __future__ import annotations

import contextlib
import importlib.util
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest

SIM = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SIM / "tests"))
from test_p5_budget import SEEDS, BudgetHarness, ledger, profile, protocol  # noqa: E402
import method_switches as ms  # noqa: E402

P6D_DIR = SIM / "docs/experiments/code-world-method-repair-p6d-20261022"
ORIGINAL_CHECKS = profile.checks  # The budget harness replaces it with a canned report.


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class P6dHarness(BudgetHarness):
    """BudgetHarness whose case carries `method_switches` (fresh ledger, same faked replay)."""

    def __init__(self, stack, switches=None, **kw):
        super().__init__(stack, **kw)
        self.case[ms.KEY] = dict(ms.P6D if switches is None else switches)
        self.case_path.write_text(json.dumps(self.case))
        keep = {n: (self.task_dir / n).read_bytes() for n in ("initial_code.py", "initial_world_program.py")}
        for child in self.task_dir.iterdir():  # Only the fresh ledger and the two initial files.
            child.unlink()
        self.state = ledger.NativeWorldState(self.task_dir, protocol.identity(self.case, self.repo))
        for name, data in keep.items():
            (self.task_dir / name).write_bytes(data)

    def fake_replay(self, command, *, repo, env, directory, timeout, stdin_path=None, **batch):
        return super().fake_replay(command, repo=repo, env=env, directory=directory, timeout=timeout,
                                   stdin_path=stdin_path)

    def sweep_all(self, code):
        """Sweep every development seed this candidate's sweep rows do not cover yet."""
        self.sweep(code, SEEDS)


@pytest.fixture
def p6d():
    with contextlib.ExitStack() as stack:
        yield lambda **kw: P6dHarness(stack, **kw)


# --- A. switches, identity, render -------------------------------------------------


def test_switch_validation_refuses_unknown_keys_and_values():
    base = {"development_budget": "aspire-v1", "dev_seeds": SEEDS, "heldout_seeds": list(range(1, 51))}
    assert ms.errors(base) == []
    assert ms.errors({**base, ms.KEY: dict(ms.P6D)}) == []
    assert ms.errors({**base, ms.KEY: {"bogus": 1}}) == ["unknown method switch 'bogus'"]
    assert ms.errors({**base, ms.KEY: {"screening_gate": "off"}})
    assert ms.errors({**base, ms.KEY: {"success_rule": "end"}})
    assert ms.errors({**base, ms.KEY: {"unseen_seeds": [65, 66]}})  # overlaps / outside 66..80
    assert ms.errors({**base, ms.KEY: {"prefreeze_resweep": {"margin": 2, "max_candidates": 3}}}), \
        "the re-sweep merges sweep-only evidence"
    assert ms.errors({"dev_seeds": SEEDS, ms.KEY: {"version_score": "sweep_only"}}), "needs aspire-v1"


def test_env_and_manifest_entry_name_every_api_and_scoring_change():
    case = {ms.KEY: dict(ms.P6D)}
    assert ms.api_env(case) == {ms.API_REVISION_ENV: "m4-r1", ms.SUCCESS_RULE_ENV: "libero_latch"}
    assert ms.api_env({}) == {} and ms.manifest_entry({}) is None
    entry = ms.manifest_entry(case)
    assert len(entry["api_changes"]) == 3 and "latch" in entry["scoring_change"]
    assert "not directly comparable" in entry["comparability"]


def test_identity_carries_the_switches_only_when_set(p6d):
    h = p6d()
    assert h.state.data["identity"][ms.KEY] == ms.P6D
    case = {k: v for k, v in h.case.items() if k != ms.KEY}
    assert ms.KEY not in protocol.identity(case, h.repo)


def test_runtime_env_sets_the_switch_variables_and_drops_inherited_ones(p6d):
    h = p6d()
    with patch.dict(os.environ, {ms.SUCCESS_RULE_ENV: "stale", ms.API_REVISION_ENV: "stale"}):
        on = protocol.runtime_env(h.case, h.repo)
        off = protocol.runtime_env({k: v for k, v in h.case.items() if k != ms.KEY}, h.repo)
    assert on[ms.SUCCESS_RULE_ENV] == "libero_latch" and on[ms.API_REVISION_ENV] == "m4-r1"
    assert ms.SUCCESS_RULE_ENV not in off and ms.API_REVISION_ENV not in off


def test_prompt_renders_each_switch_and_nothing_task_specific(p6d):
    h = p6d()
    legacy = {k: v for k, v in h.case.items() if k != ms.KEY}
    plain, text = profile.section(legacy), profile.section(h.case)
    assert "Subgoal verification contract" not in plain and "libero_latch" not in plain
    for marker in ("### Subgoal verification contract", "cap/world_model/skills/subgoal-verification.md",
                   "### World goal-judgment calibration", "### Screening and predictions are recorded",
                   "### Selection at the freeze", "within 2 passes", "### Policy API revision m4-r1",
                   "cap/world_model/skills/api-revision-m4.md", "### Success rule libero_latch",
                   "then ALL 15 development seeds", "(recorded, not refused in this cell)"):
        assert text.count(marker) == 1, marker
    # The oracle feedback sentence the scope verifier requires survives the goal edit.
    assert "Read the per-trial development calibration feedback" in text
    assert "do not add a new recovery or termination controller" not in text
    added = text.split("### Subgoal verification contract")[1]
    for word in ("drawer", "stove", "bowl", "milk", "cookie", "moka", "basket", "plate"):
        assert word not in added.lower(), word


def test_skill_files_exist_outside_dot_claude_and_name_no_task():
    for name in ("subgoal-verification.md", "api-revision-m4.md"):
        text = (SIM / "cap/world_model/skills" / name).read_text()
        assert text.startswith("---\nname: ")
        for word in ("drawer", "stove", "bowl", "milk", "cookie", "moka", "basket"):
            assert word not in text.lower(), (name, word)


# --- B. sweep-only score (M2a) and pre-freeze re-sweep (M2b) ------------------------


def test_sweep_only_needs_the_admitting_seed_swept_too(p6d):
    h = p6d()
    v0 = h.to_phase_b(passing_v0=(51,))
    digest = h.digest("fix0.py")
    assert h.state.covered_seeds(digest) == [], "the passing repair on 51 is not score evidence"
    assert h.state.tested_seeds(digest) == [51]
    h.sweep_all(v0)
    record = next(c for c in h.state.budget_candidates() if c["label"] == "v0")
    assert record["complete"] and record["passes"] == [51]
    assert len(h.state.records("sweep", 51)) == 1


def test_a_version_scores_on_sweep_rows_not_on_its_repair_pass(p6d):
    h = p6d()
    v0 = h.to_phase_b(passing_v0=SEEDS[:10])
    h.sweep_all(v0)
    # v1 passes its target 61 only in the repair run (the faked replay passes 61 once).
    code, record = h.version("v1.py", "slip", [61], passing=(61, 62), sweep=False)
    assert record["task_completed"] == 1
    h.passing["v1.py"] = {62}
    h.sweep_all(code)
    v1 = next(c for c in h.state.budget_candidates() if c["label"] == "v1")
    assert v1["complete"] and v1["passes"] == [62] and 61 in v1["failing_seeds"]
    # Legacy scoring would have counted the repair pass on 61.
    legacy = h.state.candidates()[h.digest("v1.py")]
    assert 61 in legacy["passes"]


def _two_close_versions(h):
    v0 = h.to_phase_b(passing_v0=SEEDS[:12])
    h.sweep_all(v0)                                        # v0: 12/15 on its sweep
    code, _ = h.version("v1.py", "slip", [63], passing=SEEDS[:13], sweep=False)
    h.sweep_all(code)                                      # v1: 13/15 on its sweep
    return v0, code


def test_resweep_plan_takes_near_best_candidates_and_the_selection(p6d):
    h = p6d()
    _two_close_versions(h)
    plan = h.state.resweep_plan()
    assert [plan["labels"][d] for d in plan["candidates"]] == ["v1", "v0"]
    assert plan["best_sweep_score"] == [13, 0]
    narrow = p6d(switches={**ms.P6D, "prefreeze_resweep": {"margin": 0, "max_candidates": 3}})
    _two_close_versions(narrow)
    assert [narrow.state.resweep_plan()["labels"][d] for d in narrow.state.resweep_plan()["candidates"]] == ["v1"]


def test_resweep_rows_are_outer_only_and_never_versions(p6d):
    h = p6d()
    _, v1 = _two_close_versions(h)
    with pytest.raises(ledger.ProtocolError, match="only after development has ended"):
        h.trial("resweep", 51, v1)
    h.state.data["development_end"] = {"reason": "ready", "selected": h.digest("v1.py")}
    h.state.data["prefreeze_resweep"] = h.state.resweep_plan()
    h.state.save()
    h.passing["v1.py"] = set(SEEDS[:9])                    # a noisy second run of v1
    for seed in SEEDS:
        h.trial("resweep", seed, v1)
    with pytest.raises(ledger.ProtocolError, match="already re-swept"):
        h.trial("resweep", 51, v1)
    assert len(h.state.versions()) == 1
    assert h.state.covered_seeds(h.digest("v1.py")) == SEEDS
    score = h.state.resweep_scores()[h.digest("v1.py")]
    assert score["merged_score"] == [13 + 9, 0] and score["complete"]
    assert score["flipped_seeds"] == SEEDS[9:13]
    # v0's re-sweep is not complete, so the merged comparison holds v1 alone.
    best = h.state.best_complete()
    assert best["evidence"] == "sweep + pre-freeze re-sweep" and best["bundle_sha256"] == [h.digest("v1.py")]
    assert h.state.progress()["infrastructure_errors"] == []


def test_prefreeze_resweep_selects_on_both_runs(p6d):
    h = p6d()
    v0, v1 = _two_close_versions(h)
    h.reports()
    with patch.object(protocol, "world_use", lambda case: None):
        end = protocol.end_development(h.case, h.repo, h.state, "no_progress")
    assert end["development_end"]["selected"] == h.digest("v1.py")
    h.passing["fix0.py"] = set(SEEDS[:12])                 # v0 repeats 12/15
    h.passing["v1.py"] = set(SEEDS[:8])                    # v1 drops to 8/15: it was a lucky draw
    names = {h.digest("fix0.py"): "fix0.py", h.digest("v1.py"): "v1.py"}
    original = protocol._admit_trial

    def admit(case, repo, state, phase, seed, code, *a, **kw):
        h.current = names[ledger.bundle_identity(protocol.collect_bundle(case, state.task_dir, phase, code,
                                                                          a[0], a[1])[0])]
        return original(case, repo, state, phase, seed, code, *a, **kw)
    with patch.object(protocol, "_admit_trial", admit), patch.object(protocol, "world_use", lambda case: None):
        result = protocol.prefreeze_resweep(h.case, h.repo, h.state)
    assert result["changed"], json.dumps({k: result[k] for k in ("runs", "scores", "best", "selected_before")
                                          if k in result}, default=str)[:3000]
    assert result["selected"] == h.digest("fix0.py")
    assert result["scores"][h.digest("fix0.py")]["merged_score"] == [24, 0]
    assert result["scores"][h.digest("v1.py")]["merged_score"] == [21, 0]
    assert h.state.data["selected"]["bundle_sha256"] == h.digest("fix0.py")
    assert (h.task_dir / "fix_code.py").read_bytes() == (h.task_dir / "fix0.py").read_bytes()
    assert len(h.state.versions()) == 1
    with patch.object(protocol, "world_use", lambda case: None):
        again = protocol.prefreeze_resweep(h.case, h.repo, h.state)  # resumable, runs nothing new
    assert all(not run["seeds"] for run in again["runs"])


def test_ready_end_reason_exists_only_with_the_resweep(p6d):
    h = p6d(switches={k: v for k, v in ms.P6D.items() if k != "prefreeze_resweep"})
    h.to_phase_b()
    with pytest.raises(ledger.ProtocolError, match="invalid development end reason"):
        protocol.end_development(h.case, h.repo, h.state, "ready")


# --- C. record-only screening (M3c), calibration feedback (M3b) -------------------


def test_prediction_level_rule_is_recorded_not_rejected():
    level = {"status": "unsupported_contract", "stage": "prediction_levels", "conclusion": "unknown"}
    authored = {"status": "program_error", "conclusion": "authored_error"}
    assert profile.screening_status([dict(level)]) == "rejected"           # legacy
    assert profile.screening_status([dict(level, recorded_only=True)]) == "checked"
    assert profile.screening_status([dict(level, recorded_only=True), authored]) == "rejected"


def test_checks_marks_the_level_rule_record_only_and_keys_its_own_cache(p6d, tmp_path):
    h = p6d()
    sources = {"policy": str(h.task_dir / "initial_code.py"), "world": str(h.task_dir / "initial_world_program.py")}
    report = {"status": "unsupported_contract", "stage": "prediction_levels", "conclusion": "unknown",
              "retryable": False}
    with patch.object(profile, "run_mode", lambda *a, **k: dict(report)), \
            patch.object(profile, "latest_tape", lambda state: None):
        on = ORIGINAL_CHECKS(h.case, h.repo, h.state, sources, {})
        off = ORIGINAL_CHECKS({k: v for k, v in h.case.items() if k != ms.KEY}, h.repo, h.state, sources, {})
    assert on["status"] == "checked" and on["reports"][0]["recorded_only"] is True
    assert on["identity"]["screening_gate"] == "record_only"
    assert off["status"] == "rejected" and "screening_gate" not in off["identity"]


def test_a_saturated_class_is_reported_not_refused(p6d):
    h = p6d()
    saturated = {"saturated_classes": ["pull"], "report": "r.json"}
    with patch.object(protocol, "search_control", lambda case, state: dict(saturated)):
        summary = protocol.search_gate(h.case, h.state, "repair", 51,
                                       {"policy": str(h.task_dir / "initial_code.py")}, None)
        assert "would_refuse" in summary and not h.state.data["rejected"]
        legacy = {k: v for k, v in h.case.items() if k != ms.KEY}
        with pytest.raises(ledger.ProtocolError, match="saturated"):
            protocol.search_gate(legacy, h.state, "repair", 51, {"policy": str(h.task_dir / "initial_code.py")}, None)


def _judge(h, verdicts):
    """Wrap the faked replay: write a framework_final judgment per seed (verdicts: seed -> verdict)."""
    replay = h.fake_replay

    def judged(command, **kw):
        result = replay(command, **kw)
        seed = int(command[command.index("--args.trial") + 1])
        path = Path(kw["directory"]) / "judgment_world/manifest.json"
        if path.is_file():
            manifest = json.loads(path.read_text())
            manifest["self_evaluations"] = [{"verdict": verdicts.get(seed, "unknown"), "origin": "framework_final",
                                             "binding": "shadow"}]
            path.write_text(json.dumps(manifest))
        return result
    return judged


def test_calibration_reports_false_accepts_per_seed_to_the_worker(p6d):
    h = p6d()
    with patch.object(protocol, "run_replay", _judge(h, {51: "true", 52: "true", 53: "false"})):
        v0 = h.to_phase_b(passing_v0=(51, 53))
        record = h.trial("sweep", 52, v0)          # world says done, simulator says failed
        last = h.trial("sweep", 53, v0)            # world says not done, simulator says success
    note = record["judgment_calibration"]
    assert note["this_trial"]["category"] == "false_accept"
    # The failing initial run on 51 was judged done too: both are false accepts of this world.
    assert [x["seed"] for x in note["this_world"]["false_accept"]] == [51, 52]
    assert "judged DONE but the simulator says FAILED on seeds 51, 52" in note["headline"]
    assert last["judgment_calibration"]["this_trial"]["category"] == "false_reject"
    status = h.check()["judgment_calibration"]
    assert status["all_trials"]["counts"]["false_accept"] == 2
    legacy = {k: v for k, v in h.case.items() if k != ms.KEY}
    with patch.object(protocol, "world_use", lambda case: None):
        assert "judgment_calibration" not in protocol.check(legacy, h.repo, h.state)


def test_calibration_uses_the_raw_outcome_of_a_world_error_row(tmp_path):
    from aspire.sim.cap.world_model import judgment_calibration as jc
    row = {"seed": 55, "phase": "sweep", "directory": "d", "status": "world_program_error",
           "task_completed": 0, "raw_result": {"task_completed": 1},
           "foundation_calibration": {"final_self_evaluation": {"verdict": "true"}}}
    entry = jc.classify(tmp_path, row)
    assert entry["category"] == "agree_success" and entry["outcome_source"].startswith("raw")


# --- D. held-out timeout rule and unseen seeds (M2d) ------------------------------

import native_world_heldout as heldout  # noqa: E402


def _killed_seed(tmp_path, *, last_api, recorded=False):
    """A held-out seed directory as the watchdog left it (lifecycle + world tape [+ outcome])."""
    directory = tmp_path / "seed_07"
    (directory / "judgment_world").mkdir(parents=True)
    (directory / "lifecycle.json").write_text(json.dumps({"started_at": 1000.0, "finished_at": 1900.5}))
    (directory / "judgment_world/events.jsonl").write_text(
        json.dumps({"event": "public_api", "elapsed_seconds": last_api}) + "\n")
    results = directory / "results"
    results.mkdir()
    if recorded:
        trial = results / "s/t/m/r/trial_07_sandboxrc_0_reward_1.000_taskcompleted_1"
        trial.mkdir(parents=True)
        (trial / "summary.txt").write_text("done")
        os.utime(trial / "summary.txt", (1890.0, 1890.0))
    return {"seed": 7, "directory": directory, "results": results}


@pytest.mark.parametrize("last_api, recorded, settle, status", [
    (880.0, False, True, "failure"),               # policy still acting at the budget: failed seed
    (880.0, False, False, "infrastructure_error"),  # legacy rule
    (300.0, False, True, "infrastructure_error"),   # stalled: still infrastructure
    (100.0, True, True, "success"),                 # killed while saving a recorded success
])
def test_heldout_watchdog_settle_rule(tmp_path, last_api, recorded, settle, status):
    case = {"id": "c", "condition": "C", "profile": "judgment", "trial_timeout": 900,
            **({ms.KEY: {"heldout_watchdog": "settle"}} if settle else {})}
    launch = _killed_seed(tmp_path, last_api=last_api, recorded=recorded)
    with patch.object(heldout, "verify_runtime", lambda case, repo: None):
        row = heldout.settle_seed(case, tmp_path, launch, None, "TimeoutExpired: replay exceeded 900 s")
    assert row["status"] == status
    if settle:
        assert row["watchdog"]["kind"] == {880.0: "policy_timeout", 300.0: "stalled", 100.0: "persistence_cut"}[last_api]
    if status == "failure":
        assert row["result"]["task_completed"] == 0 and row["error"] == ""


def test_unseen_seed_set_runs_the_frozen_bundle_into_its_own_ledger(tmp_path):
    from test_p6_parallel import Cell
    cell = Cell(tmp_path, heldout_parallel=1, **{ms.KEY: {"unseen_seeds": list(range(66, 81)),
                                                          "heldout_watchdog": "settle"}})
    cell.set_plan({66: {"outcome": "failure"}, 70: {"outcome": "crash"}})
    argv = ["--case", str(cell.case_path), "--seed-set", "unseen"]
    with patch.dict(os.environ, cell.env()), patch.object(sys, "argv", ["native_world_heldout.py", *argv]), \
            contextlib.redirect_stdout(__import__("io").StringIO()):
        code = heldout.main()
    assert code == 0
    report = json.loads((cell.evaluation / "unseen/unseen_result.json").read_text())
    assert report["seeds_evaluated"] == 15 and report["identity"]["seed_set"] == "unseen"
    assert report["counts"] == {"success": 13, "failure": 1, "crash": 1, "infrastructure_error": 0}
    assert sorted(int(s) for s in report["per_seed"]) == list(range(66, 81))
    assert not (cell.evaluation / "heldout_state.json").exists(), "the held-out ledger is untouched"
    started = {e["seed"] for e in cell.events("start")}
    assert started == set(range(66, 81))


def test_unseen_seed_set_requires_the_switch(tmp_path):
    from test_p6_parallel import Cell
    cell = Cell(tmp_path)
    argv = ["--case", str(cell.case_path), "--seed-set", "unseen"]
    with patch.object(sys, "argv", ["native_world_heldout.py", *argv]), pytest.raises(SystemExit, match="no unseen"):
        heldout.main()


# --- E. policy API m4-r1 -----------------------------------------------------------

from aspire.sim.cap.integrations.franka import libero_reduced as lr  # noqa: E402


class FakeArm:
    """`self._env` of the reduced API: a hand that moves a fraction of the way per blocking move."""

    def __init__(self, start=(0.4, 0.0, 0.3), reach=1.0):
        self.hand, self.reach, self.moves = np.asarray(start, float), reach, []

    def get_observation(self):
        return {"robot_cartesian_pos": np.concatenate([self.hand, [1, 0, 0, 0, 1]])}

    def move_to_joints_blocking(self, joints):
        target = np.asarray(joints[:3], float)  # The fake IK returns the target in the first 3 joints.
        self.hand = self.hand + (target - self.hand) * self.reach
        self.moves.append(target)


def fake_api(arm, fail=()):
    api = lr.FrankaLiberoApiReduced.__new__(lr.FrankaLiberoApiReduced)
    api._env, api.cfg, api._TCP_OFFSET, api.ik_solve_fn = arm, None, 0.0, None
    calls = []

    def solve(fn, quat, position, cfg):
        calls.append(np.asarray(quat, float))
        if any(np.allclose(quat, q) for q in fail):
            raise RuntimeError("no solution")
        return np.concatenate([position, [0, 0, 0, 0]])
    return api, calls, solve


def test_solve_ik_reports_clipping_and_orientation_substitution(capsys):
    arm = FakeArm()
    requested = np.array([0.0, 0.0, 1.0, 0.0])
    api, calls, solve = fake_api(arm, fail=(requested,))
    with patch.object(lr, "solve_ik_with_convergence", solve), \
            patch.object(lr, "apply_tcp_offset", lambda pos, quat, off: pos), \
            patch.object(lr, "extract_arm_joints", lambda cfg: np.asarray(cfg[:7], float)):
        joints = api.solve_ik_m4(np.array([0.9, 0.0, 0.3]), requested)
        quiet = api.solve_ik_m4(np.array([0.5, 0.0, 0.3]), np.array([0.0, 1.0, 0.0, 0.0]))
    out = capsys.readouterr().out.splitlines()
    assert joints.shape == (7,) and joints[0] == pytest.approx(0.75)
    assert len(out) == 1 and out[0].startswith("[solve_ik] target clipped")
    assert "requested [0.9, 0.0, 0.3] -> used [0.75, 0.0, 0.3]" in out[0] and "used top-down" in out[0]
    assert quiet.shape == (7,)


def test_goto_pose_segments_long_moves_and_reports_the_remaining_distance(capsys):
    arm = FakeArm(reach=0.6)                        # every blocking move stops 40 % short
    api, _, solve = fake_api(arm)
    quat = np.array([0.0, 1.0, 0.0, 0.0])
    with patch.object(lr, "solve_ik_with_convergence", solve), \
            patch.object(lr, "apply_tcp_offset", lambda pos, quat, off: pos), \
            patch.object(lr, "extract_arm_joints", lambda cfg: np.asarray(cfg[:7], float)):
        api.goto_pose_m4(np.array([0.4, 0.2, 0.3]), quat, max_step=0.05)
        steps = len(arm.moves)
        api.goto_pose_m4(np.array([0.4, 0.2, 0.3]), quat)  # the single move is unchanged
    assert steps == 4 + 1                            # 0.2 m / 0.05 m, then the final target once more
    assert all(np.linalg.norm(b - a) <= 0.05 + 1e-9 for a, b in zip(arm.moves[:4], arm.moves[1:4]))
    assert "[goto_pose] segmented move: 4 step(s)" in capsys.readouterr().out
    with pytest.raises(ValueError):
        api.goto_pose_m4(np.array([0.4, 0.2, 0.3]), quat, max_step=0.0)


def test_m4_functions_are_registered_under_the_same_names_only_with_the_revision():
    api = lr.FrankaLiberoApiReduced.__new__(lr.FrankaLiberoApiReduced)
    api._molmo_available = False
    with patch.dict(os.environ, {lr.API_REVISION_ENV: ""}):
        plain = api.functions()
    with patch.dict(os.environ, {lr.API_REVISION_ENV: "m4-r1"}):
        m4 = api.functions()
    assert set(plain) == set(m4)
    assert m4["solve_ik"] == api.solve_ik_m4 and plain["solve_ik"] == api.solve_ik
    assert m4["goto_pose"] == api.goto_pose_m4 and m4["subsample_point_cloud"] == api.subsample_point_cloud_m4


def test_seeded_subsample_is_a_function_of_the_point_cloud():
    api = lr.FrankaLiberoApiReduced.__new__(lr.FrankaLiberoApiReduced)
    pc = np.random.RandomState(0).rand(500, 3)
    state = np.random.get_state()[1].copy()
    a, b = api.subsample_point_cloud_m4(pc, 100), api.subsample_point_cloud_m4(pc, 100)
    assert np.array_equal(a, b) and len(a) == 100
    assert np.array_equal(np.random.get_state()[1], state), "the global RNG is never touched"
    assert not np.array_equal(api.subsample_point_cloud_m4(pc[::-1].copy(), 100), a)


def test_graspnet_requests_draw_from_their_own_content_seed(monkeypatch):
    import types
    import torch
    from aspire.sim.cap.serving import launch_contact_graspnet_server as server
    vendor = types.ModuleType("contact_graspnet_pytorch.data")
    vendor.np = np

    def regularize(pc, n):            # the vendor's unseeded draw (data.py:204), via its own `np`
        return pc[vendor.np.random.choice(len(pc), n, replace=False)]
    monkeypatch.setitem(sys.modules, "contact_graspnet_pytorch.data", vendor)
    pc = np.arange(3000.0).reshape(1000, 3)

    def plan(req):
        return regularize(pc, 50), torch.randint(0, 1000, (4,))
    request = SimpleNamespace(model_dump_json=lambda: '{"depth_base64": "AAA", "segmap_id": 1}')
    other = SimpleNamespace(model_dump_json=lambda: '{"depth_base64": "BBB", "segmap_id": 1}')
    np.random.seed(1); torch.manual_seed(1)
    np_state, torch_state = np.random.get_state()[1].copy(), torch.get_rng_state().clone()
    first = server._seeded(plan, request)
    server._seeded(plan, other)                   # another request in between, as in a long-lived service
    second = server._seeded(plan, request)
    assert np.array_equal(first[0], second[0]) and torch.equal(first[1], second[1])
    assert not np.array_equal(server._seeded(plan, other)[0], first[0])
    assert vendor.np is np, "the vendor module gets numpy back after the request"
    assert np.array_equal(np.random.get_state()[1], np_state) and torch.equal(torch.get_rng_state(), torch_state)


def test_graspnet_seeding_is_off_without_the_revision():
    from aspire.sim.cap.serving import launch_contact_graspnet_server as server
    source = (SIM / "cap/serving/launch_contact_graspnet_server.py").read_text()
    assert 'os.environ.get("ASPIRE_API_REVISION") == REQUEST_SEEDING_REVISION' in source
    assert "np.random.seed(" not in source
    assert server.REQUEST_SEEDING_REVISION == ms.P6D["api_revision"]


# --- F. success latch (libero_latch) ------------------------------------------------

from aspire.sim.cap.envs.simulators import libero as libero_sim  # noqa: E402


class FakeHandle:
    """LIBERO's env: done = success at that control step; robosuite's horizon error after `horizon`."""

    def __init__(self, success_steps, horizon):
        self.success_steps, self.horizon, self.t = set(success_steps), horizon, 0
        self.env = SimpleNamespace(check_success=lambda: self.t in self.success_steps)

    def step(self, action):
        if self.t >= self.horizon:
            raise ValueError("executing action in terminated episode")
        self.t += 1
        done = self.t in self.success_steps
        return {}, float(done), done, {}


def fake_sim(success_steps, horizon):
    sim = libero_sim.FrankaLiberoEnv.__new__(libero_sim.FrankaLiberoEnv)
    sim.handle = FakeHandle(success_steps, horizon)
    sim._success_latched = sim._budget_exhausted = False
    sim._current_reward = 0.0
    return sim


@pytest.mark.parametrize("rule", ["libero_latch", None])
def test_success_anywhere_within_the_budget_is_latched(rule):
    sim = fake_sim(success_steps={3}, horizon=5)
    with patch.dict(os.environ, {libero_sim.SUCCESS_RULE_ENV: rule or ""}):
        for _ in range(5):
            sim._current_obs, sim._current_reward, _, _ = sim._handle_step(None)
        assert sim.task_completed() is (rule is not None)      # step 3 succeeded, step 5 did not
        assert sim.compute_reward() == (1.0 if rule else 0.0)
        with pytest.raises(ValueError, match="executing action in terminated episode") as raised:
            sim._handle_step(None)
    assert getattr(raised.value, "episode_budget_exhausted", False) is (rule is not None)


def test_budget_exhaustion_is_not_a_program_error_of_the_code_env():
    from aspire.sim.cap.envs.tasks.base import CodeExecutionEnvBase as BaseCodeEnv
    env = BaseCodeEnv.__new__(BaseCodeEnv)
    env._apis, env._exec_globals = {}, {}
    env._hide_sandbox_names = lambda: None
    env._get_observation = lambda: {}
    env._sandbox_env = lambda: None

    def run(error):
        env._exec_globals["boom"] = lambda: (_ for _ in ()).throw(error)
        return env._exec_user_code("boom()")
    marked = run(libero_sim.EpisodeBudgetExhausted("executing action in terminated episode"))
    plain = run(ValueError("executing action in terminated episode"))
    assert marked["ok"] and "[episode] step budget exhausted" in marked["stdout"]
    assert not plain["ok"] and "Traceback" in plain["stderr"]


def test_budget_exhaustion_is_not_a_world_fault():
    from aspire.sim.cap.world_model import executable_world as ew
    session = SimpleNamespace(errors=[], events=[])
    session.fault = lambda op, exc: session.errors.append(op)
    session.emit = lambda kind, **f: session.events.append(kind)
    session.restores = []

    def invoke(name, fn, args, kwargs):
        return fn(*args, **kwargs)
    session.invoke = invoke

    def raiser(error):
        def fn():
            raise error
        return fn
    api = SimpleNamespace(functions=lambda: {"goto_pose": raiser(libero_sim.EpisodeBudgetExhausted("x")),
                                             "open_gripper": raiser(ValueError("y"))})
    ew.ExecutableSession.bind_api(session, api)
    for name in ("goto_pose", "open_gripper"):
        with pytest.raises(ValueError):
            api.functions()[name]()
    assert session.errors == ["public_call"] and session.events == ["episode_budget_exhausted"]


# --- G. p6d driver: deadline reserve and outer steps --------------------------------


def test_cell_timeout_reserves_the_resweep_and_unseen_measurement():
    limits = load(P6D_DIR / "support/full_deadlines.py", "p6d_deadlines")
    case = {"heldout_parallel": 4, "sweep_parallel": 4, "trial_timeout": 900, "dev_seeds": SEEDS}
    legacy = limits.cell_timeout(case)
    assert legacy == 101580
    extra = limits.cell_timeout({**case, ms.KEY: dict(ms.P6D)}) - legacy
    assert extra == limits.heldout_deadline(45, 900, 4) + limits.heldout_deadline(15, 900, 4)


def test_p6d_driver_keeps_p6c_and_adds_only_switched_steps():
    p6c = (SIM / "docs/experiments/code-world-pipeline-repair-p6c-20261022/support/run_two_task_cell.py").read_text()
    p6d = (P6D_DIR / "support/run_two_task_cell.py").read_text()
    for marker in ("failure_evidence", "INFRA_DISPOSITIONS = 3", "worker_api_failure"):
        assert marker in p6c and marker in p6d
    assert 'finish_development("ready")' in p6d and '"prefreeze-resweep", "resweep"' in p6d
    assert '"--seed-set", "unseen"' in p6d and 'state["unseen"]' in p6d
    assert p6d.count('switches.get("prefreeze_resweep")') == 1
    assert (P6D_DIR / "support/worker_recovery.py").read_bytes() == \
        (SIM / "docs/experiments/code-world-pipeline-repair-p6c-20261022/support/worker_recovery.py").read_bytes()


def test_real_twomoka_watchdog_record_settles_as_a_heldout_seed(tmp_path):
    """The real l10 twomoka attempt-19 evidence (p6c fixture) through the held-out settle rule."""
    from test_p6c_repair import MOKA_CASE, extract
    source, manifest = extract(tmp_path, "twomoka_p6b_r1")
    task = next(p.parent for p in source.rglob("development_state.json"))
    data = json.loads((task / "development_state.json").read_text())
    row = next(r for r in data["trials"] if r["directory"] == manifest["attempt"])
    directory = task / manifest["attempt"]
    launch = {"seed": row["seed"], "directory": directory, "results": directory / "results"}
    rows = {}
    for name, case in (("legacy", MOKA_CASE), ("p6d", {**MOKA_CASE, ms.KEY: {"heldout_watchdog": "settle"}})):
        with patch.object(heldout, "verify_runtime", lambda case, repo: None):
            rows[name] = heldout.settle_seed(case, tmp_path, launch, None, row["error"])
    assert rows["legacy"]["status"] == "infrastructure_error"
    assert rows["p6d"]["status"] == "failure" and rows["p6d"]["watchdog"]["kind"] == "persistence_cut"
    assert rows["p6d"]["result"]["task_completed"] == 0
