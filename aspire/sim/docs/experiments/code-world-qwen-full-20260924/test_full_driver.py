"""Full-driver acceptance through the real control flow. No model, no simulator.

Only the model/coordinator transport, the protocol steps and the charged
diagnostic import are faked; the ledger reads, ownership check, freeze, manifest,
accounting, subprocess held-out sweep and its deadline are the real code.
"""
import io
import json
import os
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace

import pytest

HERE = Path(__file__).resolve().parent
SIM = HERE.parents[2]
for path in (SIM / "scripts/libero", HERE / "support"):
    sys.path.insert(0, str(path))
import heldout_stop_on_infra
import infra_guard
# Imported here, before any driver run inserts a fixture `scripts/libero` ahead
# of the real one on sys.path.
import native_world_heldout as real_heldout
import run_full_cell

#: Stands in for `native_world_heldout`: same entry contract the wrapper needs —
#: a `main()` whose seed loop persists each row through module-global
#: `atomic_json`, and `HELDOUT_SEEDS`. No simulator, no replay.
EVALUATOR = '''
import json, subprocess, sys, time
from pathlib import Path
HELDOUT_SEEDS = tuple(range(1, 51))
STATUSES = ("success", "failure", "crash", "infrastructure_error")

def atomic_json(path, data):
    Path(path).write_text(json.dumps(data, indent=2))

def main():
    case = json.loads(Path(sys.argv[sys.argv.index("--case") + 1]).read_text())
    evaluation = Path(case["control"]) / "heldout"
    manifest = json.loads((evaluation / "heldout_manifest.json").read_text())
    mode = case["heldout_fixture_mode"]
    identity = {k: manifest[k] for k in ("cell", "condition", "suite", "task")}
    identity["bundle"] = manifest["selected_bundle"]
    identity["bundle_sha256"] = manifest["selected_bundle_sha256"]
    if mode == "bad_bundle":
        identity["bundle_sha256"] = "0" * 64
    ledger = evaluation / "heldout_state.json"
    data = {"identity": identity, "seeds": {}}
    for seed in manifest["seeds"]:
        if mode == "hang" and seed == 2:
            child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"])
            (evaluation / "child.pid").write_text(str(child.pid))
            time.sleep(300)
        status = "success" if seed <= 20 else "failure"
        if mode == "infra" and seed == 3:
            status = "infrastructure_error"
        if mode == "missing_seed" and seed == 50:
            continue
        data["seeds"][str(seed)] = {"seed": seed, "status": status}
        atomic_json(ledger, data)
    records = list(data["seeds"].values())
    counts = {s: sum(1 for r in records if r["status"] == s) for s in STATUSES}
    report = {"identity": identity, "counts": counts, "seeds_evaluated": len(records),
              "success_rate": counts["success"] / 50, "all_seeds_accounted": True,
              "per_seed": {str(r["seed"]): r["status"] for r in records},
              "unusable_seeds": [r["seed"] for r in records
                                 if r["status"] == "infrastructure_error"]}
    if mode == "bad_counts":
        report["counts"] = dict(counts, success=counts["success"] + 3)
        report["success_rate"] = 0.46
    (evaluation / "heldout_result.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
'''


class FakeCampaign:
    """Only the boundary the driver calls out through."""

    def __init__(self, cell):
        self.turns, self.steps, self.cell = [], [], cell
        self.worker_prompt = lambda case, repo: "worker"
        self.coordinator_prompt = lambda case, path: "coordinator"

    @staticmethod
    def atomic_json(path, payload):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(payload, indent=2, default=str) + "\n")

    def verify_runtime(self, case, repo):
        return True

    def native_settings(self, case, repo, case_path):
        return {"hooks": {"PreToolUse": []},
                "env": {"ANTHROPIC_BASE_URL": case["inference_endpoint"],
                        "CLAUDE_CODE_MAX_OUTPUT_TOKENS": str(case["max_output_tokens"])}}

    def perception_ready(self, case):
        return {"ready": True}

    def probe(self, case, control, env, settings):
        return {"context_windows": [case["context_tokens"]], "max_output_tokens": [32000]}

    def write_prompts(self, case, repo, control):
        prompt, coordinator = control / "worker-prompt.md", control / "coordinator.md"
        prompt.write_text("worker assignment")
        coordinator.write_text("coordinator prompt")
        return prompt, coordinator

    def native_environment(self, case, repo, case_path, config):
        return {"PATH": os.environ["PATH"], "HOME": os.environ.get("HOME", "/tmp")}

    def native_command(self, case, prompt, settings, agents=None):
        return ["true"]

    def worker_agent(self, case, prompt_path):
        return {}

    def protocol(self, case, repo, env, folder, name, *argv):
        self.steps.append(name)
        step = {"step": name, "exit_code": 0, "result": {}}
        self.atomic_json(Path(folder) / f"outer_{name}.json", step)
        return step

    def run_native_cc(self, command, *, cwd, env, stdout_path, stderr_path, timeout, on_start=None):
        self.turns.append(str(stdout_path))
        Path(stdout_path).write_text("{}\n")
        Path(stderr_path).write_text("")
        return self.cell.get("solver_exit_code", 0)


def build(tmp_path, mode="ok", **case_extra):
    repo, control = tmp_path / "sim", tmp_path / "control"
    (repo / "scripts/libero").mkdir(parents=True)
    (repo / ".venv-libero/bin").mkdir(parents=True)
    (repo / "configs").mkdir()
    (control / "outputs/working_codes").mkdir(parents=True)
    (control / "heldout").mkdir()
    (tmp_path / "config").mkdir()
    (repo / "outputs").symlink_to(control / "outputs")
    (repo / ".venv-libero/bin/python3").symlink_to(sys.executable)
    (repo / "scripts/libero/native_world_heldout.py").write_text(EVALUATOR)
    (repo / "configs/env.yaml").write_text("env: {}\n")
    case = {"id": "cell-c-full-01", "condition": "C", "suite": "libero_90", "task": "task_x",
            "sim": str(repo), "control": str(control), "dev_seeds": list(range(51, 66)),
            "claude_config_dir": str(tmp_path / "config"), "model_provider": "local-vllm",
            "model": "qwen3.8-flash-next", "model_tag": "qwen3.8-flash-next",
            "expected_served_model": "qwen3.8-flash-next", "context_tokens": 262144,
            "max_output_tokens": 64000, "inference_endpoint": "http://127.0.0.1:8100",
            "campaign_timeout": 600, "trial_timeout": 900, "max_steps": 600,
            "env_config": "configs/env.yaml", "egl_vendor_config": "/etc/egl.json",
            "egl_device_id": 6, "cuda_visible_devices": "6",
            "heldout_fixture_mode": mode, **case_extra}
    case_path = tmp_path / "case.json"
    case_path.write_text(json.dumps(case))
    task_dir = control / "outputs/libero_fix_loop" / case["suite"] / case["task"]
    task_dir.mkdir(parents=True)
    (task_dir / "development_state.json").write_text(json.dumps(
        {"identity": {"cell": case["id"]}, "trials": [], "blockers": []}))
    (task_dir / "stage1_result.json").write_text(json.dumps(
        {"stage1_complete": True, "selected_bundle": {"policy": "p.py", "world": "w.py"},
         "selection": {"bundle_sha256": "a" * 64}, "tested_bundles": {"a" * 64: {}}}))
    return case, case_path, repo, control, task_dir


@pytest.fixture
def driver(monkeypatch):
    def install(case, hook=None):
        campaign = FakeCampaign(case)
        monkeypatch.setitem(sys.modules, "native_world_campaign", campaign)
        monkeypatch.setattr(run_full_cell, "identity", lambda *a: ("session-1", "worker-1"))
        record = {"seed": 51, "phase": "diagnostic", "charged": True, "status": "complete",
                  "directory": "d", "imported_from": {"source_digest": "b" * 64,
                                                      "remaining_real_budget": {"51": 2}}}
        monkeypatch.setattr(run_full_cell, "full_import", SimpleNamespace(
            SOURCE_REL="docs/src", open_state=lambda task_dir: object(),
            import_diagnostic=lambda ledger, source: record))
        # The driver replaces `campaign.run_native_cc` with the fixed transport,
        # so the fake has to be installed there.
        transport = hook(campaign) if hook else campaign.run_native_cc
        monkeypatch.setattr(run_full_cell.fixed_transport, "run_native_cc", transport)
        return campaign
    return install


def state_of(control):
    return json.loads((control / "campaign_state.json").read_text())


def test_blocked_rejects_a_resumable_kwarg():
    with pytest.raises(TypeError):
        run_full_cell.Blocked("init", "x", resumable=True)
    payload = run_full_cell.Blocked("init", "x").payload
    assert payload["automatic_resume"] is False and payload["state_preserved"] is True


def test_dev_freeze_and_fifty_seed_heldout(tmp_path, driver):
    case, case_path, repo, control, _ = build(tmp_path)
    campaign = driver(case)
    code = run_full_cell.main(["--case", str(case_path)])
    state = state_of(control)
    assert code == 0 and state["status"] == "full_complete", state.get("blocked")
    assert state["heldout_manifest"]["seeds"] == list(range(1, 51))
    assert state["heldout"]["accounting"] == {
        "rows_accounted": 50, "counts": {"success": 20, "failure": 30, "crash": 0,
                                         "infrastructure_error": 0},
        "success_rate": 0.4, "identity_verified": True, "unusable_seeds": []}
    assert state["heldout"]["timed_out"] is False and len(campaign.turns) == 1
    assert campaign.steps == ["init", "check-00", "finalize"]


def test_unresolved_blocker_stops_before_heldout_and_further_turns(tmp_path, driver):
    case, case_path, repo, control, task_dir = build(tmp_path)
    ledger = task_dir / "development_state.json"

    def hook(campaign):
        original = campaign.run_native_cc
        def run(*args, **kwargs):
            data = json.loads(ledger.read_text())
            data["blockers"] = [
                {"phase": "initial", "seed": 52, "reason": "graspnet unreachable",
                 "status": "blocked", "charged": False, "executed": False, "resolved": False},
                {"phase": "initial", "seed": 51, "reason": "old", "status": "blocked",
                 "charged": False, "resolved": True, "resolved_by": "trial_51_1"}]
            ledger.write_text(json.dumps(data))
            return original(*args, **kwargs)
        return run

    campaign = driver(case, hook)
    before = ledger.read_bytes()
    code = run_full_cell.main(["--case", str(case_path)])
    state = state_of(control)
    assert code == 1 and state["status"] == "blocked"
    assert state["blocked"]["phase"] == "infrastructure"
    assert state["blocked"]["detail"]["kind"] == "screening_blocker"
    assert state["blocked"]["detail"]["seed"] == 52 and state["blocked"]["detail"]["charged"] is False
    assert len(campaign.turns) == 1 and "check-00" not in campaign.steps
    assert "heldout" not in state and not list(control.glob("campaign-*/outer_heldout.json"))
    assert json.loads(ledger.read_bytes())["blockers"] == json.loads(ledger.read_text())["blockers"]
    assert len(json.loads(before)["blockers"]) == 0


def test_heldout_timeout_stops_the_group_and_preserves_rows(tmp_path, driver):
    case, case_path, repo, control, _ = build(tmp_path, mode="hang")
    driver(case)
    code = run_full_cell.main(["--case", str(case_path), "--deadline-heldout", "5"])
    state = state_of(control)
    assert code == 1 and state["status"] == "blocked"
    assert state["blocked"]["phase"] == "heldout" and state["heldout"]["timed_out"] is True
    assert state["heldout"]["elapsed_seconds"] >= 5
    child = int((control / "heldout/child.pid").read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(child, 0)
    rows = json.loads((control / "heldout/heldout_state.json").read_text())
    assert rows["seeds"]["1"]["status"] == "success"


@pytest.mark.parametrize("mode,key", [("bad_bundle", "identity"), ("bad_counts", "counts"),
                                      ("missing_seed", "missing_seeds")])
def test_corrupt_heldout_report_is_rejected(tmp_path, driver, mode, key):
    case, case_path, repo, control, _ = build(tmp_path, mode=mode)
    driver(case)
    assert run_full_cell.main(["--case", str(case_path)]) == 1
    state = state_of(control)
    assert state["blocked"]["phase"] == "heldout"
    assert key in state["blocked"]["detail"]


def test_manifest_identity_is_immutable(tmp_path, driver):
    case, case_path, repo, control, task_dir = build(tmp_path)
    (control / "heldout/heldout_manifest.json").write_text(json.dumps(
        {"cell": case["id"], "condition": "C", "suite": case["suite"], "task": case["task"],
         "seeds": list(range(1, 51)), "row_count": 50,
         "selected_bundle": {"policy": "other.py"}, "selected_bundle_sha256": "c" * 64,
         "rows": [{"row": i, "seed": i, "status": "pending"} for i in range(1, 51)]}))
    driver(case)
    assert run_full_cell.main(["--case", str(case_path)]) == 1
    state = state_of(control)
    assert state["blocked"]["phase"] == "freeze"
    assert set(state["blocked"]["detail"]["differing"]) >= {"selected_bundle"}


def test_driver_sweep_stops_after_a_persisted_infrastructure_row(tmp_path, driver):
    case, case_path, repo, control, _ = build(tmp_path, mode="infra")
    driver(case)
    assert run_full_cell.main(["--case", str(case_path)]) == 1
    state = state_of(control)
    assert state["blocked"]["phase"] == "heldout" and state["heldout"]["timed_out"] is False
    rows = json.loads((control / "heldout/heldout_state.json").read_text())["seeds"]
    assert sorted(int(s) for s in rows) == [1, 2, 3]
    report = json.loads((control / "heldout/heldout_result.json").read_text())
    assert report["stopped_after_infrastructure_error"] is True
    assert report["stopped_on"]["seed"] == 3 and report["all_seeds_accounted"] is False


def test_wrapper_stops_the_real_native_loop_after_one_persisted_row(tmp_path, monkeypatch):
    """The real `native_world_heldout` seed loop, with only per-seed evaluation faked."""
    case, case_path, repo, control, _ = build(tmp_path, mode="infra")
    heldout = real_heldout
    monkeypatch.setattr(heldout, "frozen_bundle", lambda c, t, e: ({"policy": "a" * 64}, {}))
    evaluated = []

    def evaluate(case, repo, evaluation, bundle, kept, seed):
        evaluated.append(seed)
        return {"seed": seed, "status": "infrastructure_error" if seed == 3 else "success",
                "directory": str(evaluation / f"seed_{seed:02d}")}

    monkeypatch.setattr(heldout, "evaluate_seed", evaluate)
    monkeypatch.setitem(sys.modules, "native_world_heldout", heldout)
    code = heldout_stop_on_infra.main(["--case", str(case_path)])
    assert code == 1 and evaluated == [1, 2, 3]
    rows = json.loads((control / "heldout/heldout_state.json").read_text())["seeds"]
    assert sorted(int(s) for s in rows) == [1, 2, 3]
    assert rows["3"]["status"] == "infrastructure_error"
    report = json.loads((control / "heldout/heldout_result.json").read_text())
    assert report["counts"]["infrastructure_error"] == 1 and report["unusable_seeds"] == [3]
    assert report["identity"]["bundle_sha256"]


@pytest.mark.parametrize("resolved,expected", [(False, 2), (True, 0)])
def test_infra_guard_gate_sees_unresolved_screening_blockers(tmp_path, monkeypatch,
                                                             resolved, expected):
    case, case_path, repo, control, task_dir = build(tmp_path)
    data = json.loads((task_dir / "development_state.json").read_text())
    data["blockers"] = [{"phase": "initial", "seed": 52, "reason": "egl", "status": "blocked",
                         "charged": False, "executed": False, "resolved": resolved}]
    (task_dir / "development_state.json").write_text(json.dumps(data))
    payload = {"tool_name": "Bash", "tool_input": {
        "command": "python scripts/libero/native_world_protocol.py trial --seed 52"}}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    monkeypatch.setattr(sys, "argv", ["infra_guard.py", "--case", str(case_path)])
    assert infra_guard.main() == expected
