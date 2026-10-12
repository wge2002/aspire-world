"""CPU tests for the SAM2-streaming held-out runner and stage-1 provenance.

No simulator, GPU, model service or SAM2 weights. Every manifest, settings file
and probe here is a synthetic test fixture, never service evidence. Replay
execution is faked at the `run_replay` boundary.
"""
from __future__ import annotations

import hashlib
import io
import json
import shutil
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

SIM = Path(__file__).resolve().parents[1]
for extra in (SIM.parents[1], SIM / "scripts/common", SIM / "scripts/libero", SIM / "tests"):
    sys.path.insert(0, str(extra))

import streaming_fixloop_heldout as sh  # noqa: E402
import streaming_fixloop_protocol as sp  # noqa: E402
from test_streaming_fixloop import (POLICY, cell, cli, finish_development,  # noqa: E402,F401
                                    native_records, task_dir, transcript_records)


def hcli(c, *argv):
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        code = sh.main(["--case", str(c.path), *argv], clock=c.clock)
    return code, json.loads(buffer.getvalue())


def heldout_dir(c) -> Path:
    return Path(c.case["control"]).resolve() / "heldout"


def result(c) -> dict:
    return json.loads((heldout_dir(c) / "heldout_result.json").read_text())


def heldout_calls(c) -> list[dict]:
    return [call for call in c.fake.calls if "/heldout/" in str(call["directory"])]


def finalized(c, monkeypatch) -> Path:
    """A finished, selected, finalized development cell, authorized for held-out."""
    d = finish_development(c)
    shutil.copy(d / "initial_code.py", d / "fix_code.py")
    shutil.copy(d / "initial_streaming_config.json", d / "fix_streaming_config.json")
    assert cli(c, "select", "--reason", "passes 8 of 15 development seeds")[0] == 0
    transcript = d.parent / "coordinator.stdout.jsonl"
    transcript.write_text(transcript_records())
    settings, probe = native_records(d.parent)
    code, stage1 = cli(c, "finalize", "--transcript", str(transcript),
                       "--worker-settings", str(settings), "--probe", str(probe))
    assert code == 0, stage1
    (d.parent / sh.OUTER_MARKER).write_text(c.case["id"])
    monkeypatch.setattr(sh, "run_replay", c.fake)
    return d


def snapshot_tree(root: Path) -> dict:
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file() and p.name != ".trial.lock"}


def test_scoring_follows_native_heldout_and_one_bundle_runs_every_seed(cell, monkeypatch):
    d = finalized(cell, monkeypatch)
    before = snapshot_tree(d)
    cell.fake.outcomes.update({
        1: (0, "", 0, 1),                    # success
        2: (0, "", 0, 0),                    # failure
        3: (0, "", 1, 1),                    # program error with oracle success: crash
        4: (None, "OSError: no GPU", 0, 0),  # runner failure: infrastructure
        5: "seed_error",                     # authored prompt cannot seed: crash
        6: (1, "", 0, 1),                    # nonzero process exit: infrastructure
        7: (None, "TimeoutExpired: 900", 0, 0)})
    code, summary = hcli(cell)
    assert code == 1  # complete, but infrastructure errors are reported
    report = result(cell)
    assert report["completed"] and not report["incomplete"] and report["not_run_seeds"] == []
    assert [report["per_seed"][str(s)] for s in range(1, 8)] == [
        "success", "failure", "crash", "infrastructure_error", "crash",
        "infrastructure_error", "infrastructure_error"]
    assert report["counts"]["success"] == 1 and report["success_rate"] == 1 / 50
    assert report["success_rate_denominator"] == 50
    assert report["infrastructure_error_seeds"] == [4, 6, 7]
    state = json.loads((heldout_dir(cell) / "heldout_state.json").read_text())
    assert state["seeds"]["7"]["timed_out"] and state["seeds"]["5"]["world_error"]["authored"]
    calls = heldout_calls(cell)
    assert len(calls) == 50
    codes = {(Path(c["directory"]) / "code.py").read_bytes() for c in calls}
    configs = {Path(c["command"][c["command"].index("--args.streaming-world-config") + 1]).read_text()
               for c in calls}
    assert codes == {POLICY.encode()} and len(configs) == 1
    for call in calls:
        assert call["timeout"] == 900 and call["env"]["SNAPSHOT_DIR"] == str(call["directory"])
        assert "--args.world-model-config" not in call["command"]
        assert not any("API_KEY" in k or "AUTH_TOKEN" in k for k in call["env"])
    manifest = report["frozen_manifest"]
    assert manifest["policy"]["author"].startswith("cell solver (Qwen)")
    assert manifest["authored_streaming_config"]["author"].startswith("cell solver (Qwen)")
    assert manifest["engineered_world"]["author"].startswith("engineering")
    assert manifest["object_inventory"] is None and manifest["strategy_md"] is None
    assert report["dependency_evidence"] == "cpu-test-fake"
    assert snapshot_tree(d) == before  # the development cell is never written
    assert hcli(cell)[1]["error"].startswith("held-out state exists")
    assert hcli(cell, "--resume")[0] == 1 and len(heldout_calls(cell)) == 50  # nothing re-run


def test_persisted_deadline_clips_then_leaves_seeds_not_run(cell, monkeypatch):
    finalized(cell, monkeypatch)
    fake = cell.fake

    def slow(command, **kwargs):
        cell.clock.now += 1100
        return fake(command, **kwargs)
    monkeypatch.setattr(sh, "run_replay", slow)
    code, summary = hcli(cell)
    report = result(cell)
    assert code == 1 and report["incomplete"] and report["deadline_expired"]
    assert report["seeds_evaluated"] == 17 and report["not_run_seeds"] == list(range(18, 51))
    assert report["counts"]["not_run"] == 33 and report["success_rate_denominator"] == 50
    state = json.loads((heldout_dir(cell) / "heldout_state.json").read_text())
    assert state["seeds"]["17"]["watchdog_seconds"] == 400 and state["seeds"]["17"]["deadline_clipped"]
    deadline = state["phase_clock"]["evaluation"]["deadline"]
    cell.clock.now += 50000
    assert hcli(cell, "--resume")[0] == 1
    state = json.loads((heldout_dir(cell) / "heldout_state.json").read_text())
    assert state["phase_clock"]["evaluation"]["deadline"] == deadline  # never refreshed
    assert len(heldout_calls(cell)) == 17 and "18" not in state["seeds"]
    assert result(cell)["not_run_seeds"] == list(range(18, 51))


def test_inflight_seed_is_settled_from_evidence_and_never_replayed(cell, monkeypatch):
    finalized(cell, monkeypatch)
    fake = cell.fake

    def killed_on_three(command, *, directory, **kwargs):
        outcome = fake(command, directory=directory, **kwargs)
        if "seed_03" in str(directory):
            (directory / "lifecycle.json").write_text(json.dumps(
                {"pid": 999999999, "finished_at": 1.0, "process_exit_code": 0, "error": ""}))
            raise KeyboardInterrupt  # the runner dies before recording the result
        return outcome
    monkeypatch.setattr(sh, "run_replay", killed_on_three)
    fake.outcomes[3] = (0, "", 0, 1)
    with pytest.raises(KeyboardInterrupt):
        hcli(cell)
    code, summary = hcli(cell, "--resume")
    assert code == 1 and summary["unresolved_running_seeds"] == [3]
    assert "in flight" in summary["stop_reason"] and len(heldout_calls(cell)) == 3
    code, summary = hcli(cell, "--resolve-interrupted", "3")
    state = json.loads((heldout_dir(cell) / "heldout_state.json").read_text())
    assert state["seeds"]["3"]["status"] == "success"
    assert state["seeds"]["3"]["recovery"]["replayed"] is False and len(heldout_calls(cell)) == 3
    assert hcli(cell, "--resolve-interrupted", "3")[0] == 2  # already settled
    assert hcli(cell, "--resume")[0] == 0 and result(cell)["completed"]
    calls = [Path(c["directory"]).name for c in heldout_calls(cell)]
    assert len(calls) == 50 and calls.count("seed_03") == 1


def test_interrupted_seed_without_lifecycle_is_infrastructure(cell, monkeypatch):
    finalized(cell, monkeypatch)

    def crash(command, **kwargs):
        raise KeyboardInterrupt
    monkeypatch.setattr(sh, "run_replay", crash)
    with pytest.raises(KeyboardInterrupt):
        hcli(cell)
    hcli(cell, "--resolve-interrupted", "1")
    state = json.loads((heldout_dir(cell) / "heldout_state.json").read_text())
    assert state["seeds"]["1"]["status"] == "infrastructure_error"


def test_artifacts_without_a_row_stop_the_sweep_and_are_kept(cell, monkeypatch):
    finalized(cell, monkeypatch)
    stray = heldout_dir(cell) / "seed_02"
    stray.mkdir(parents=True)
    (stray / "replay.log").write_text("evidence")
    code, summary = hcli(cell)
    assert code == 1 and "no recorded row" in summary["stop_reason"]
    assert (stray / "replay.log").read_text() == "evidence"
    assert result(cell)["not_run_seeds"] == list(range(2, 51))


@pytest.mark.parametrize("target", ["frozen_policy", "checkpoint", "world", "model_manifest"])
def test_tampering_mid_sweep_is_infrastructure_and_stops(cell, monkeypatch, target):
    finalized(cell, monkeypatch)
    fake = cell.fake

    def tamper(command, *, directory, **kwargs):
        outcome = fake(command, directory=directory, **kwargs)
        if "seed_03" in str(directory):
            path = {"frozen_policy": heldout_dir(cell) / "frozen/fix_code.py",
                    "checkpoint": cell.checkpoint,
                    "world": cell.sim / "cap/world_model/World.py",
                    "model_manifest": cell.service}[target]
            path.write_bytes(path.read_bytes() + b"\n# changed\n")
        return outcome
    monkeypatch.setattr(sh, "run_replay", tamper)
    code, summary = hcli(cell)
    report = result(cell)
    assert code == 1 and report["per_seed"]["3"] == "infrastructure_error"
    assert report["not_run_seeds"] == list(range(4, 51)) and report["incomplete"]
    assert len(heldout_calls(cell)) == 3
    assert hcli(cell, "--resume")[0] == 2 and len(heldout_calls(cell)) == 3


def test_runtime_dependency_freeze_is_verified_before_and_after_each_seed(cell, monkeypatch):
    from native_cc_freeze import dependency_bindings, source_inventory
    manifest = cell.path.parent / "runtime_manifest.json"
    manifest.write_text(json.dumps({"schema_version": 1, **source_inventory(cell.sim),
                                    "dependency_links": dependency_bindings(cell.sim),
                                    "external_files": {str(cell.checkpoint): sp.file_sha256(cell.checkpoint)},
                                    "executables": {}}))
    cell.case.update(require_runtime_freeze=True, runtime_manifest=str(manifest),
                     runtime_manifest_sha256=sp.file_sha256(manifest))
    cell.path.write_text(json.dumps(cell.case))
    finalized(cell, monkeypatch)
    fake = cell.fake

    def add_dependency(command, *, directory, **kwargs):
        if "seed_02" in str(directory):
            (cell.sim / "cap/injected_dependency.py").write_text("x = 1\n")
        return fake(command, directory=directory, **kwargs)
    monkeypatch.setattr(sh, "run_replay", add_dependency)
    code, summary = hcli(cell)
    report = result(cell)
    assert report["per_seed"]["1"] == "failure" and report["per_seed"]["2"] == "infrastructure_error"
    assert "frozen runtime changed" in report["stop_reason"]
    assert report["frozen_manifest"]["runtime_manifest_sha256"] == sp.file_sha256(manifest)
    (cell.sim / "cap/injected_dependency.py").unlink()
    # Restored: the sweep continues; seed 2 stays infrastructure and is never re-run.
    assert hcli(cell, "--resume")[0] == 1 and result(cell)["completed"]
    assert result(cell)["infrastructure_error_seeds"] == [2] and len(heldout_calls(cell)) == 50


def test_untested_forged_or_unfinished_selection_is_refused(cell, monkeypatch):
    d = finalized(cell, monkeypatch)
    marker = d.parent / sh.OUTER_MARKER
    marker.unlink()
    assert "outer-only" in hcli(cell)[1]["error"]
    marker.write_text(cell.case["id"])
    original = (d / "fix_code.py").read_text()
    (d / "fix_code.py").write_text(POLICY + "print('never ran')\n")
    assert "do not match the selected" in hcli(cell)[1]["error"]
    # Forge stage-1 and ledger selection for the untested bundle: rows decide.
    assert not (heldout_dir(cell) / "frozen").exists()  # a refused freeze copies nothing
    identity = json.loads((d / "development_state.json").read_text())["identity"]
    bundle, _ = sh.frozen_bundle(identity, {"policy": d / "fix_code.py",
                                            "authored_streaming_config": d / "fix_streaming_config.json"})
    digest = sp.bundle_identity(bundle)
    stage1 = json.loads((d / "stage1_result.json").read_text())
    stage1.update(selected_bundle=bundle, selected_bundle_sha256=digest,
                  selection={**stage1["selection"], "bundle_sha256": digest},
                  tested_bundles={**stage1["tested_bundles"], digest: {"passes": 1}})
    (d / "stage1_result.json").write_text(json.dumps(stage1))
    ledger = json.loads((d / "development_state.json").read_text())
    ledger["selected"]["bundle_sha256"] = digest
    (d / "development_state.json").write_text(json.dumps(ledger))
    assert "never tested" in hcli(cell)[1]["error"]
    ledger["stage1_complete"] = False
    (d / "development_state.json").write_text(json.dumps(ledger))
    assert "stage 1 is not complete" in hcli(cell)[1]["error"]
    assert heldout_calls(cell) == [] and not (heldout_dir(cell) / "heldout_state.json").exists()
    assert original == POLICY


def test_finalize_requires_actual_capacity_effort_and_local_route_proof(cell):
    d = finish_development(cell)
    shutil.copy(d / "initial_code.py", d / "fix_code.py")
    shutil.copy(d / "initial_streaming_config.json", d / "fix_streaming_config.json")
    assert cli(cell, "select", "--reason", "development evidence")[0] == 0
    transcript = d.parent / "coordinator.stdout.jsonl"

    def finalize(contexts=(1000000,), **records):
        transcript.write_text(transcript_records(contexts))
        settings, probe = native_records(d.parent, **records)
        return cli(cell, "finalize", "--transcript", str(transcript),
                   "--worker-settings", str(settings), "--probe", str(probe))

    # The case declares 1M/64K/xhigh; only recorded effective values can pass.
    for contexts, records, message in (
            ((), {}, "context window"),
            ((200000,), {}, "context window"),
            ((1000000, 200000), {}, "context window"),
            ((1000000,), {"settings": {"effortLevel": "high"}}, "effective worker settings"),
            ((1000000,), {"env": {"CLAUDE_CODE_MAX_OUTPUT_TOKENS": "32000"}}, "effective worker settings"),
            ((1000000,), {"env": {"CLAUDE_CODE_MAX_CONTEXT_TOKENS": "200000"}}, "effective worker settings"),
            ((1000000,), {"env": {"ANTHROPIC_BASE_URL": "https://api.example.com"}}, "effective worker settings"),
            ((1000000,), {"settings": {"apiKeyHelper": "helper.sh"}}, "effective worker settings"),
            ((1000000,), {"probe": {"context_windows": [200000]}}, "route proof"),
            ((1000000,), {"probe": {"exit_code": 1}}, "route proof"),
            ((1000000,), {"probe": {"configured": {"effort": "high", "context_tokens": 1000000,
                                                   "max_output_tokens": 64000}}}, "route proof")):
        code, error = finalize(contexts, **records)
        assert code == 2 and message in error["error"], (contexts, records, error)
    settings, probe = native_records(d.parent)
    transcript.write_text(transcript_records())
    probe.unlink()
    assert "missing local route proof" in cli(cell, "finalize", "--transcript", str(transcript),
                                              "--worker-settings", str(settings),
                                              "--probe", str(probe))[1]["error"]
    settings.unlink()
    native_records(d.parent)
    settings.unlink()
    assert "missing recorded worker settings" in cli(
        cell, "finalize", "--transcript", str(transcript), "--worker-settings", str(settings),
        "--probe", str(probe))[1]["error"]
    assert not (d / "stage1_result.json").exists()
    code, stage1 = finalize()
    assert code == 0
    provenance = stage1["native_provenance"]
    assert provenance["configured"]["max_output_tokens"] == 64000
    assert provenance["observed"]["context_windows"] == [1000000]
    assert provenance["observed"]["reported_max_output_tokens"] == [32000]  # alias default, visible
    assert provenance["worker_settings"]["effective"]["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == "64000"


def test_settings_from_the_native_launcher_pass_the_effective_check(cell):
    """The worker settings shape is the launcher's own local_vllm_settings()."""
    from native_world_campaign import local_vllm_settings
    settings = local_vllm_settings({
        "model_tag": "qwen3.8-flash-next", "expected_served_model": "qwen3.8-flash-next",
        "effort": "xhigh", "context_tokens": 1000000, "max_output_tokens": 64000,
        "inference_endpoint": "http://127.0.0.1:8121", "trial_timeout": 900})
    path = cell.path.parent / "launcher-settings.json"
    path.write_text(json.dumps(settings))
    _, probe = native_records(cell.path.parent)
    observed = {"model_served": {"qwen3.8-flash-next": 1}, "context_windows": [1000000]}
    record, errors = sp.native_provenance(cell.case, observed, path, probe)
    assert errors == [] and record["worker_settings"]["effective"]["api_key_helper"] is False


def test_model_service_manifest_is_read_and_hashed(cell):
    assert cli(cell, "init")[0] == 0
    content = json.loads(cell.service.read_text())
    cell.service.write_text(json.dumps({**content, "note": "relaunched"}))
    assert "manifest hash" in cli(cell, "status")[1]["error"]
    cell.service.unlink()
    assert "manifest is missing" in cli(cell, "status")[1]["error"]
    # Correctly hashed, but the recorded server serves something else.
    cell.service.write_text(json.dumps({**content, "served_model": "other"}))
    case = {**cell.case, "model_service": {**cell.case["model_service"],
                                           "server_manifest_sha256": sp.file_sha256(cell.service)}}
    cell.path.write_text(json.dumps(case))
    assert "disagrees with the case" in cli(cell, "status")[1]["error"]
    cell.service.write_text(json.dumps({**content, "max_model_len": 262144}))
    case["model_service"]["server_manifest_sha256"] = sp.file_sha256(cell.service)
    cell.path.write_text(json.dumps(case))
    assert "max_model_len" in cli(cell, "status")[1]["error"]


def test_formal_case_requires_frozen_dependencies_and_example_fakes_nothing(cell):
    formal = {k: v for k, v in cell.case.items() if k != "dependency_evidence"}
    with pytest.raises(sp.ProtocolError) as refused:
        sp.validate_case(formal)
    for key in ("require_runtime_freeze", "runtime_manifest", "perception_manifest",
                "initial_state_manifest", "initial_state_manifest_sha256"):
        assert key in str(refused.value)
    with pytest.raises(sp.ProtocolError, match="loopback"):
        sp.validate_case({**cell.case, "model_service": {**cell.case["model_service"],
                                                         "endpoint": "https://api.example.com"}})
    example = json.loads((SIM / "env_configs/libero/streaming_fixloop_case.example.json").read_text())
    sp.validate_case(example)  # shape only; placeholders are not evidence
    assert example.get("dependency_evidence", "frozen") == "frozen"
    with pytest.raises(sp.RuntimeChanged, match="manifest is missing"):
        sp.model_service_identity(example)


def test_legacy_heldout_runner_is_untouched():
    import native_world_heldout as legacy
    assert legacy.HELDOUT_SEEDS == sh.HELDOUT_SEEDS
    assert "streaming" not in Path(legacy.__file__).read_text()
