"""CPU tests for the SAM2-streaming native Fix Loop development profile.

No simulator, GPU, model service or SAM2 weights. Programs, prompts and the
"checkpoint" are synthetic fixtures and never run results. Replay execution is
faked at the `run_replay` boundary, exactly where the legacy native tests fake it.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

SIM = Path(__file__).resolve().parents[1]
for extra in (SIM.parents[1], SIM / "scripts/common", SIM / "scripts/libero", SIM / "tests"):
    sys.path.insert(0, str(extra))

import native_world_fixloop_state as legacy_ledger  # noqa: E402
import streaming_fixloop_protocol as sp  # noqa: E402

POLICY = "obs = get_observation()\nprint(world_snapshot())\n"
CONFIG = {"objects": [{"identity": "bowl", "prompt": "white bowl", "min_seed_score": 0.6},
                      {"identity": "plate", "prompt": "plate"}],
          "cadence": {"object_period": 0.2}}
FINDINGS = ("## Root causes observed\nrim contact\n\n## What fixed them\nhigher approach\n\n"
            "## Generalizable patterns\nmeasure first\n\n## Blocked seeds\nnone\n")


class Clock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now


class FakeReplay:
    """Writes the artifact names replay_trial writes; no process is started."""

    def __init__(self):
        self.calls = []
        self.outcomes = {}  # seed -> (exit_code, error, sandbox_rc, completed) or "seed_error"

    def __call__(self, command, *, repo, env, directory, timeout, stdin_path=None):
        self.calls.append({"command": command, "env": env, "timeout": timeout,
                           "directory": directory})
        seed = int(command[command.index("--args.trial") + 1])
        out = Path(command[command.index("--args.output-dir") + 1])
        run = out / "libero_goal_swap" / "put_the_bowl_on_the_plate" / "m" / "run"
        if "scene_snapshot.py" in command[command.index("--args.replay-code") + 1] or \
                Path(command[command.index("--args.replay-code") + 1]).read_text().startswith("# snap"):
            for name in ("scene_snapshot.jpg", "scene_snapshot_wrist.jpg"):
                (Path(env["SNAPSHOT_DIR"]) / name).write_bytes(b"jpg")
            (run / f"trial_{seed:02d}_sandboxrc_0_reward_0.000_taskcompleted_0").mkdir(parents=True)
            return 0, ""
        outcome = self.outcomes.get(seed, (0, "", 0, 0))
        if outcome == "seed_error":
            run.mkdir(parents=True)
            (run / "streaming_world_startup_error.json").write_text(json.dumps(
                {"authored": True, "error": "StreamingSeedError: ambiguous seed for bowl"}))
            return 1, ""
        if outcome == "infra_startup":
            run.mkdir(parents=True)
            (run / "streaming_world_startup_error.json").write_text(json.dumps(
                {"authored": False, "error": "FileNotFoundError: checkpoint"}))
            return 1, ""
        exit_code, error, rc, completed = outcome
        if not error:
            trial = run / f"trial_{seed:02d}_sandboxrc_{rc}_reward_{float(completed):.3f}_taskcompleted_{completed}"
            trial.mkdir(parents=True)
            (trial / "streaming_world_journal.jsonl").write_text('{"event": "summary"}\n')
        return exit_code, error


@pytest.fixture
def cell(tmp_path, monkeypatch):
    sim = tmp_path / "sim"
    for name in (*sp.ENGINEERED_WORLD_FILES, "env_configs/libero/franka_libero_traced.yaml"):
        (sim / name).parent.mkdir(parents=True, exist_ok=True)
        (sim / name).write_text(f"# stub {name}\n")
    (sim / "scripts/libero/scene_snapshot.py").write_text("# snapshot\nget_observation()\n")
    sam2 = tmp_path / "SAM2_streaming"
    (sam2 / "sam2").mkdir(parents=True)
    (sam2 / "sam2/build_sam.py").write_text("def build_sam2_camera_predictor(): pass\n")
    checkpoint = tmp_path / "sam2.1_hiera_small.pt"
    checkpoint.write_bytes(b"weights")
    # A synthetic launch record standing in for the real service manifest.
    service = tmp_path / "model-service-manifest.json"
    service.write_text(json.dumps({"endpoint": "http://127.0.0.1:8121",
                                   "served_model": "qwen3.8-flash-next", "max_model_len": 1000000}))
    case = {"id": "bowl_streaming_r1", "method": "sam2-streaming", "repeat": 1,
            "sim": str(sim), "control": str(tmp_path / "campaign/bowl_streaming_r1"),
            "suite": "libero_goal_swap", "task": "put_the_bowl_on_the_plate",
            "profile": sp.PROFILE, "dev_seeds": list(range(51, 66)),
            **{k: v for k, v in sp.FIXED.items() if k not in {"condition", "development_gate"}},
            "model_service": {"endpoint": "http://127.0.0.1:8121", "served_model": "qwen3.8-flash-next",
                              "server_manifest": str(service),
                              "server_manifest_sha256": sp.file_sha256(service)},
            # CPU fixture: no frozen dependency/perception manifests. Recorded as fake.
            "dependency_evidence": "cpu-test-fake",
            "cuda_visible_devices": "0", "egl_device_id": 0,
            "sam3_url": "http://127.0.0.1:8214", "graspnet_url": "http://127.0.0.1:8215",
            "pyroki_url": "http://127.0.0.1:8216",
            "streaming": {"camera": "agentview", "sam2": {
                "source_root": str(sam2), "config_file": "sam2.1/sam2.1_hiera_s.yaml",
                "checkpoint": str(checkpoint), "device": "cuda"}}}
    path = tmp_path / "case.json"
    path.write_text(json.dumps(case))
    fake = FakeReplay()
    monkeypatch.setattr(sp, "run_replay", fake)
    clock = Clock()
    return SimpleNamespace(path=path, case=case, sim=sim, fake=fake, clock=clock,
                           checkpoint=checkpoint, sam2=sam2, service=service)


def native_records(directory: Path, **overrides) -> tuple[Path, Path]:
    """Worker settings and native probe in the shapes the native launcher records.

    Test fakes only: they stand in for the files local_vllm_settings() and
    native_world_campaign.probe() write; they are not model-service evidence.
    """
    env = {"ANTHROPIC_BASE_URL": "http://127.0.0.1:8121", "ANTHROPIC_MODEL": "qwen3.8-flash-next",
           "CLAUDE_CODE_EFFORT_LEVEL": "xhigh", "CLAUDE_CODE_ALWAYS_ENABLE_EFFORT": "1",
           "CLAUDE_CODE_MAX_CONTEXT_TOKENS": "1000000", "CLAUDE_CODE_MAX_OUTPUT_TOKENS": "64000"}
    env.update(overrides.pop("env", {}))
    settings = {"env": env, "model": "qwen3.8-flash-next", "effortLevel": "xhigh",
                **overrides.pop("settings", {})}
    probe = {"exit_code": 0, "model_served": ["qwen3.8-flash-next"], "context_windows": [1000000],
             "max_output_tokens": [32000],
             "configured": {"model_tag": "qwen3.8-flash-next", "effort": "xhigh",
                            "context_tokens": 1000000, "max_output_tokens": 64000}}
    probe.update(overrides.pop("probe", {}))
    (directory / "settings.json").write_text(json.dumps(settings))
    (directory / "probe.json").write_text(json.dumps(probe))
    return directory / "settings.json", directory / "probe.json"


def transcript_records(contexts=(1000000,), model="qwen3.8-flash-next") -> str:
    return "\n".join(json.dumps(r) for r in (
        {"type": "assistant", "message": {"model": model}},
        {"type": "result", "usage": {"input_tokens": 5, "output_tokens": 2},
         "modelUsage": {"q": {"contextWindow": c, "maxOutputTokens": 32000}
                        for c in contexts}})) + "\n"


def cli(c, *argv):
    import io
    from contextlib import redirect_stdout
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        code = sp.main(["--case", str(c.path), *argv], clock=c.clock)
    return code, json.loads(buffer.getvalue())


def task_dir(c) -> Path:
    return Path(c.case["control"]).resolve() / "cell"


def author(c, stem="initial", policy=POLICY, config=None):
    d = task_dir(c)
    (d / f"{stem}_code.py").write_text(policy)
    (d / f"{stem}_streaming_config.json").write_text(json.dumps(config or CONFIG))
    return d / f"{stem}_code.py", d / f"{stem}_streaming_config.json"


def trial(c, phase, seed, stem="initial"):
    code, config = task_dir(c) / f"{stem}_code.py", task_dir(c) / f"{stem}_streaming_config.json"
    return cli(c, "trial", "--phase", phase, "--seed", str(seed), "--code", str(code),
               "--streaming-config", str(config))


def started(c):
    assert cli(c, "init")[0] == 0
    assert cli(c, "snapshot")[0] == 0
    author(c)
    return task_dir(c)


def test_snapshot_is_uncharged_and_uses_no_streaming_profile(cell):
    started(cell)
    status = cli(cell, "status")[1]
    assert status["snapshot_complete"] and status["retries_used_per_seed"]["51"] == 0
    command = cell.fake.calls[0]["command"]
    assert "--args.streaming-world-config" not in command
    assert cli(cell, "snapshot")[0] == 2  # exactly once


def test_streaming_cli_arguments_and_materialized_runtime_config(cell):
    started(cell)
    code, record = trial(cell, "initial", 51)
    assert code == 0 and record["status"] == "complete"
    call = cell.fake.calls[-1]
    command = call["command"]
    assert command[command.index("--args.task") + 1] == "put_the_bowl_on_the_plate"
    assert command[command.index("--args.config") + 1] == "env_configs/libero/franka_libero_traced.yaml"
    for frozen in ("--args.world-model-config", "--args.ordinary-budget-config", "--args.interactive"):
        assert frozen not in command
    runtime = json.loads(Path(command[command.index("--args.streaming-world-config") + 1]).read_text())
    assert runtime["mode"] == "sam2-streaming-world-v1"
    assert runtime["sam2"] == cell.case["streaming"]["sam2"]
    assert runtime["objects"] == CONFIG["objects"]
    assert call["timeout"] == 900 and call["env"]["ASPIRE_MAX_STEPS"] == "4000"
    assert call["env"]["PYTHONPATH"].split(":")[0] == str(cell.sam2.resolve())
    assert not any(k for k in call["env"] if "API_KEY" in k or "AUTH_TOKEN" in k)
    assert record["bundle_components"]["authored_by"].startswith("cell solver")
    assert record["streaming_artifacts"]


def test_three_total_charged_attempts_and_revisions_do_not_reset(cell):
    started(cell)
    assert trial(cell, "smoke", 51)[0] == 0
    assert trial(cell, "initial", 51)[0] == 0
    for seed in range(52, 66):
        trial(cell, "initial", seed)
    author(cell, "fix", policy=POLICY + "print('rev1')\n")
    assert trial(cell, "repair", 51, "fix")[0] == 0
    # Third charged execution reached: any further program, new or old, is refused.
    author(cell, "fix", policy=POLICY + "print('rev2')\n")
    code, error = trial(cell, "repair", 51, "fix")
    assert code == 2 and "spent all 3" in error["error"]
    status = cli(cell, "status")[1]
    assert status["retries_used_per_seed"]["51"] == 3
    assert status["budget"]["charged_attempts_limit"] == 45
    # An invalid revision is preserved, executes nothing and spends nothing.
    author(cell, "fix", policy="  ")
    assert trial(cell, "repair", 52, "fix")[0] == 2
    assert cli(cell, "status")[1]["retries_used_per_seed"]["52"] == 1


def test_initial_bundle_is_frozen_and_initial_batch_precedes_repair(cell):
    started(cell)
    trial(cell, "initial", 51)
    author(cell, "fix", policy=POLICY + "x = 1\n")
    assert trial(cell, "repair", 51, "fix")[1]["error"].startswith("run the initial program")
    author(cell, "initial", policy=POLICY + "changed = True\n")
    assert "frozen" in trial(cell, "initial", 52)[1]["error"]
    # A config change is a different bundle too.
    author(cell, "initial", config={**CONFIG, "notes": "different prompt plan"})
    assert "frozen" in trial(cell, "initial", 52)[1]["error"]


def test_heldout_seeds_are_refused_before_any_record(cell):
    started(cell)
    before = len(cell.fake.calls)
    for seed in (1, 25, 50):
        code, error = trial(cell, "initial", seed)
        assert code == 2 and "held out" in error["error"]
    assert len(cell.fake.calls) == before
    assert not cli(cell, "status")[1]["rejected_revisions"]
    with pytest.raises(SystemExit):
        sp.main(["--case", str(cell.path), "heldout"], clock=cell.clock)


def test_program_error_is_never_an_oracle_success(cell):
    started(cell)
    cell.fake.outcomes[51] = (0, "", 1, 1)  # authored code raised, env said completed
    assert trial(cell, "initial", 51)[0] == 0
    status = cli(cell, "status")[1]
    assert 51 not in status["seeds_passing"] and 51 in status["seeds_pending_repair"]
    assert all(not row["passes"] for row in status["tested_bundles"].values())


def test_authored_seed_prompt_failure_is_charged_program_fault_not_infra(cell):
    started(cell)
    cell.fake.outcomes[51] = "seed_error"
    code, record = trial(cell, "initial", 51)
    assert record["status"] == "world_program_error" and record["spends_retry"]
    cell.fake.outcomes[52] = "infra_startup"
    code, record = trial(cell, "initial", 52)
    assert record["status"] == "infrastructure_error" and record["spends_retry"]


def test_timeout_is_charged_infrastructure_and_blocks_until_settled(cell):
    started(cell)
    cell.fake.outcomes[51] = (None, "TimeoutExpired: 900", 0, 0)
    code, record = trial(cell, "initial", 51)
    assert record["status"] == "infrastructure_error" and record["timed_out"]
    assert cli(cell, "status")[1]["retries_used_per_seed"]["51"] == 1


def test_development_deadline_is_persisted_and_never_refreshed(cell):
    started(cell)
    deadline = cli(cell, "status")[1]["budget"]["phase_clock"]["development"]["deadline"]
    assert deadline == 1000.0 + 50400
    cell.clock.now += 50000
    trial(cell, "initial", 51)
    assert cell.fake.calls[-1]["timeout"] == 400  # clipped to the remaining window
    cell.clock.now += 1000
    status = cli(cell, "status")[1]
    assert status["budget"]["phase_clock"]["development"]["deadline"] == deadline
    code, error = trial(cell, "initial", 52)
    assert code == 2 and error["type"] == "DeadlineExpired"
    assert cli(cell, "status")[1]["retries_used_per_seed"]["52"] == 0  # budget kept unspent
    check = cli(cell, "check")[1]
    assert check["decision_type"] == "terminal" and check["deadline_expired"]
    assert cli(cell, "init")[0] == 2  # no fresh ledger over an existing cell


def test_cell_identity_binds_world_sam2_model_and_task(cell):
    started(cell)
    (cell.sim / "cap/world_model/World.py").write_text("# edited engineering world\n")
    assert "same protocol" in cli(cell, "status")[1]["error"]
    (cell.sim / "cap/world_model/World.py").write_text("# stub cap/world_model/World.py\n")
    assert cli(cell, "status")[0] == 0
    cell.checkpoint.write_bytes(b"other weights")
    assert "same protocol" in cli(cell, "status")[1]["error"]
    cell.checkpoint.write_bytes(b"weights")
    for key, value in (("model", "other-model"), ("effort", "high"), ("trial_timeout", 1200),
                       ("task", "pick_up_anything"), ("repeat", 3)):
        case = {**cell.case, key: value}
        cell.path.write_text(json.dumps(case))
        assert cli(cell, "status")[0] == 2
    for key, value in (("skill_library_dir", ".claude/libero/skills"),
                       ("inventory", [{"id": "bowl"}])):
        cell.path.write_text(json.dumps({**cell.case, key: value}))
        assert "condition C supplies no" in cli(cell, "status")[1]["error"]


def test_authored_config_cannot_override_engineering_runtime(cell):
    started(cell)
    author(cell, config={**CONFIG, "sam2": {"checkpoint": "/elsewhere.pt"}})
    code, error = trial(cell, "initial", 51)
    assert code == 2 and "frozen engineering runtime" in error["error"]
    author(cell, config={"objects": [{"identity": "robot", "prompt": "arm"}]})
    assert "robot" in trial(cell, "initial", 51)[1]["error"]
    author(cell, config={"objects": [{"identity": "bowl", "prompt": "bowl"}],
                         "cadence": {"object_period": -1}})
    assert "cadence" in trial(cell, "initial", 51)[1]["error"]
    assert cli(cell, "status")[1]["retries_used_per_seed"]["51"] == 0


@pytest.mark.parametrize("leak", [
    "done = env.task_completed()\n", "r = env.compute_reward()\n",
    "ok = env.handle.env.check_success()\n", "x = env.handle.init_states\n",
    "fn = APIS['get_observation']\n", "owner = get_observation.__self__\n",
    "inner = env.low_level_env\n"])
def test_authored_policy_and_world_cannot_query_the_oracle(cell, leak):
    # The development gate is the outer oracle; executing code may not read it.
    started(cell)
    author(cell, policy=POLICY + leak)
    refused = ("oracle", "ground-truth", "simulator state")
    code, error = trial(cell, "initial", 51)
    assert code == 2 and any(r in error["error"] for r in refused)
    author(cell, config={**CONFIG, "notes": leak})
    code, error = trial(cell, "initial", 51)
    assert code == 2 and any(r in error["error"] for r in refused)
    assert cli(cell, "status")[1]["retries_used_per_seed"]["51"] == 0
    # task_language stays the public instruction.
    author(cell, policy=POLICY + "print(env.handle.task_language)\n")
    assert trial(cell, "initial", 51)[0] == 0


def test_legacy_oracle_check_policy_default_is_unchanged():
    from native_world_protocol import check_policy
    check_policy("done = env.task_completed()\n")  # legacy oracle cells: no new refusal
    with pytest.raises(sp.ProtocolError):
        check_policy("done = env.task_completed()\n", sealed=True)


def test_resume_preserves_inflight_charged_attempt(cell):
    started(cell)
    case, repo, state = sp.open_state(cell.path, "status", cell.clock)
    bundle, sources, _ = sp.collect_bundle(case, repo, state, "initial",
                                           task_dir(cell) / "initial_code.py",
                                           task_dir(cell) / "initial_streaming_config.json")
    record = state.begin_trial("initial", 51, bundle, sources)  # process then killed
    status = cli(cell, "status")[1]
    assert status["retries_used_per_seed"]["51"] == 1
    assert "unresolved" in trial(cell, "initial", 52)[1]["error"]
    lifecycle = task_dir(cell) / record["directory"] / "lifecycle.json"
    lifecycle.write_text(json.dumps({"process_exit_code": -15, "error": "TrialInterrupted"}))
    code, settled = cli(cell, "resolve-interrupted", "--directory", record["directory"])
    assert code == 0 and settled["status"] == "infrastructure_error" and settled["spends_retry"]
    assert cli(cell, "status")[1]["retries_used_per_seed"]["51"] == 1
    assert trial(cell, "initial", 52)[0] == 0


def finish_development(cell):
    started(cell)
    for seed in range(51, 66):
        cell.fake.outcomes[seed] = (0, "", 0, 1 if seed % 2 else 0)
        trial(cell, "initial", seed)
    d = task_dir(cell)
    for seed in range(52, 66, 2):
        cell.fake.outcomes[seed] = (0, "", 0, 0)
        for _ in range(2):
            trial(cell, "repair", seed)
        (d / "attempts").mkdir(exist_ok=True)
        (d / "attempts" / f"seed_{seed}_BLOCKED.md").write_text(
            "## Root Cause: Physical\nslip\n## Details: rim\n## What Was Tried: two lifts\n")
    (d / "task_analysis.md").write_text("bowl and plate visible\n")
    (d / "findings.md").write_text(FINDINGS)
    return d


def test_unexecuted_bundle_cannot_be_selected_and_tested_one_can(cell):
    d = finish_development(cell)
    (d / "fix_code.py").write_text(POLICY + "print('never ran')\n")
    shutil.copy(d / "initial_streaming_config.json", d / "fix_streaming_config.json")
    code, error = cli(cell, "select", "--reason", "looks better")
    assert code == 2 and "never tested" in error["error"]
    # Same policy, untested prompt plan: also a different, unexecuted bundle.
    shutil.copy(d / "initial_code.py", d / "fix_code.py")
    (d / "fix_streaming_config.json").write_text(json.dumps({**CONFIG, "notes": "x"}))
    assert "never tested" in cli(cell, "select", "--reason", "x")[1]["error"]
    shutil.copy(d / "initial_streaming_config.json", d / "fix_streaming_config.json")
    code, selected = cli(cell, "select", "--reason", "passes 8 of 15 development seeds")
    assert code == 0 and selected["attempt_references"]
    check = cli(cell, "check")[1]
    assert check["ready"], check["errors"]
    transcript = d.parent / "coordinator.stdout.jsonl"
    settings, probe = native_records(d.parent)
    records = ("--worker-settings", str(settings), "--probe", str(probe))
    transcript.write_text(json.dumps({"type": "assistant", "message": {"model": "other"}}) + "\n")
    assert "provenance" in cli(cell, "finalize", "--transcript", str(transcript), *records)[1]["error"]
    transcript.write_text(transcript_records())
    code, result = cli(cell, "finalize", "--transcript", str(transcript), *records)
    assert code == 0 and result["observation_fallback"] is False
    assert result["native_provenance"]["worker_settings"]["effective"]["effortLevel"] == "xhigh"
    assert result["dependency_evidence"] == "cpu-test-fake"
    assert result["freeze_inputs"]["engineered_world"]["author"].startswith("engineering")
    assert cli(cell, "trial", "--phase", "repair", "--seed", "52", "--code", str(d / "fix_code.py"),
               "--streaming-config", str(d / "fix_streaming_config.json"))[1]["error"] == "this cell is frozen"


def test_select_after_deadline_is_refused(cell):
    finish_development(cell)
    d = task_dir(cell)
    shutil.copy(d / "initial_code.py", d / "fix_code.py")
    shutil.copy(d / "initial_streaming_config.json", d / "fix_streaming_config.json")
    cell.clock.now += 60000
    assert cli(cell, "select", "--reason", "late")[1]["type"] == "DeadlineExpired"


def test_legacy_ledger_behavior_is_unchanged():
    """The subclass overrides; the legacy class keeps the oracle label rule."""
    assert legacy_ledger.NativeWorldState.passed is not sp.StreamingFixLoopState.passed
    state = legacy_ledger.NativeWorldState.__new__(legacy_ledger.NativeWorldState)
    state.sealed = False
    assert state.passed({"status": "complete", "sandbox_rc": 1, "task_completed": 1})
    assert "phase_clock" not in legacy_ledger.NativeWorldState.__init__.__code__.co_names


def test_bridge_journal_records_public_cadence_loss_and_checkpoints():
    from test_streaming_world import MotorEnv, Tracker, mask
    from aspire.sim.cap.world_model.World import Cadence, TrackingError, World
    from aspire.sim.cap.world_model.control_progress import MotionInterrupted
    from aspire.sim.cap.world_model.streaming_bridge import StreamingJournal, StreamingWorldBridge
    env, tracker, journal = MotorEnv(), Tracker(), StreamingJournal()
    world = World(tracker, episode_id="test", cadence=Cadence(object_period=.1))
    with StreamingWorldBridge(env, world, on_checkpoint=lambda s: s["robot"]["frame_id"] < 4,
                              journal=journal) as bridge:
        bridge.seed(bridge.capture(), {"bowl": mask()})
        with pytest.raises(MotionInterrupted):
            env.move_to_joints_blocking(np.ones(7), max_steps=10)
        tracker.failure = True
        bridge.on_checkpoint = None
        world.request_verification()  # next checkpoint acquires a frame regardless of cadence
        with pytest.raises(TrackingError):
            env._step_once()
    kinds = [e["event"] for e in journal.events]
    assert kinds[0] == "seed" and "checkpoint_interrupt" in kinds and "tracking_lost" in kinds
    assert journal.counts["robot_updates"] >= 4 and journal.counts["object_frames"] >= 1
    assert journal.counts["checkpoint_interrupt"] == 1 and journal.counts["tracking_lost"] == 1
    text = json.dumps(journal.events)
    assert "visible_center" not in text and "task_completed" not in text and "reward" not in text
    lost = next(e for e in journal.events if e["event"] == "tracking_lost")
    assert lost["objects"]["bowl"]["status"] == "unknown" and "captured_at" in lost


def test_replay_journal_logs_reacquisition_and_final_snapshot(tmp_path, monkeypatch):
    from test_streaming_world import MotorEnv, Tracker, mask, replay_config
    from aspire.sim.cap.world_model.sam2_streaming import SAM2Streaming
    from aspire.sim.cap.world_model.streaming_replay import StreamingReplay, StreamingSeedError
    monkeypatch.setattr(SAM2Streaming, "from_checkpoint", lambda **kw: Tracker())
    motor = MotorEnv()
    motor.privileged = False
    scope = {"segment_sam3_text_prompt": lambda rgb, prompt: [{"mask": mask(), "score": .9}]}
    env = SimpleNamespace(cfg=SimpleNamespace(privileged=False), low_level_env=motor,
                          _exec_globals=scope)
    with StreamingReplay(env, replay_config(tmp_path), episode_id="test") as session:
        motor.move_to_joints_blocking(np.ones(7), max_steps=3)
        scope["segment_sam3_text_prompt"] = lambda *a: []
        with pytest.raises(StreamingSeedError):
            scope["reacquire_world"]()
    session.write_journal(tmp_path / "journal.jsonl")
    events = [json.loads(line) for line in (tmp_path / "journal.jsonl").read_text().splitlines()]
    kinds = [e["event"] for e in events]
    assert kinds[:2] == ["seed", "reacquire_ok"]
    assert "reacquire_failed" in kinds and kinds[-2:] == ["final_snapshot", "summary"]
    assert events[-1]["counts"]["reacquire_failed"] == 1
    with pytest.raises(FileExistsError):
        session.write_journal(tmp_path / "journal.jsonl")
