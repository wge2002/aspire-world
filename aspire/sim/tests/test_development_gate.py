"""Behavioral tests for the opt-in development gate (oracle / self_eval / vlm_judge).

Everything here is synthetic: toy worlds, a faked replay that writes the same
artifacts a sealed simulator run writes, and a faked judge. No simulator, no
model request, no GPU. What the tests establish is the protocol contract:

- an absent or `oracle` gate keeps the legacy ledger byte-identical in shape;
- a sealed gate never stores the task label or the reward in anything the
  solver can read, grades progress and selection from the recorded verdict, and
  records oracle and verdict side by side under the control root;
- the replay's sealed mode names and logs artifacts without the outcome and
  narrows the generated program's sandbox to the task language;
- the judge is independent, bounded, and never turns infrastructure trouble
  into a verdict.
"""
from __future__ import annotations

import contextlib
import importlib.machinery
import importlib.util
import json
import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest

SIM = Path(__file__).resolve().parents[1]
for extra in (SIM, SIM / "scripts/common", SIM / "scripts/libero"):
    sys.path.insert(0, str(extra))
sys.path.insert(0, str(SIM.parents[1]))

import executable_world_profile as profile
import native_world_campaign as campaign
import native_world_fixloop_state as ledger
import native_world_protocol as protocol
import test_native_world_fixloop as fixtures
from aspire.sim.cap.world_model import development_gate as gate
from aspire.sim.cap.world_model import vlm_judge

POLICY = "obs = get_observation()\nprint(env.handle.task_language)\n"
WORLD = '''FOUNDATION_REVISION = "r1"
state = WorldState()
def update(obs, last_action=None): pass
def query(name, **kwargs): return state.query(name, **kwargs)
def snapshot(): return state.snapshot()
def predict(call): raise Unsupported("toy")
def observe(event): pass
def simulate(call): raise Unsupported("toy")
def done(): return {"verdict": "unknown", "evidence_ids": [], "reason": "toy", "clauses": []}
'''


def sealed_case(**overrides):
    case = {"condition": "C", "profile": "judgment", "executable_world_revision": "r1",
            "foundation_revision": "r1", "c_arm": "full", "c_lineage": "fresh",
            "development_gate": "self_eval", "id": "toy", "task": "put_the_bowl_on_the_plate",
            "inference_endpoint": "http://127.0.0.1:8121", "model": "qwen3.8-flash-next",
            "effort": "high", "control": "/tmp/toy-control"}
    case.update(overrides)
    return case


# --- validation ---------------------------------------------------------------


def test_absent_or_oracle_gate_adds_no_constraint():
    assert gate.revision_errors({}) == []
    assert gate.revision_errors({"development_gate": "oracle", "condition": "A"}) == []
    assert gate.gate({}) == "oracle" and not gate.sealed({})


@pytest.mark.parametrize("override, needle", [
    ({"development_gate": "oracle_plus"}, "unknown development_gate"),
    ({"executable_world_revision": None}, "executable_world_revision"),
    ({"foundation_revision": None}, "foundation_revision"),
    ({"condition": "A", "profile": "legacy_native"}, "C-only"),
    ({"c_arm": "no_self_eval"}, "c_arm 'full'"),
    ({"closed_loop_revision": "r1"}, "closed_loop_revision"),
    ({"development_gate": "vlm_judge", "inference_endpoint": None, "judge": {}}, "endpoint"),
])
def test_incompatible_gate_combinations_are_refused(override, needle):
    problems = gate.revision_errors(sealed_case(**override))
    assert any(needle in p for p in problems), problems
    with pytest.raises(ValueError):
        profile.validate(sealed_case(**override))


def test_identity_carries_the_gate_only_when_set(tmp_path):
    path, case = fixtures.make_case(tmp_path, "C")
    case.update(profile="judgment", executable_world_revision="r1", foundation_revision="r1",
                c_arm="full", c_lineage="fresh")
    with patch.object(protocol, "verify_runtime", lambda c, r: None):
        legacy = protocol.identity(case, SIM)
        assert "development_gate" not in legacy
        sealed = protocol.identity({**case, "development_gate": "self_eval"}, SIM)
        assert sealed["development_gate"] == "self_eval"
        with pytest.raises(ValueError):
            protocol.identity({**case, "development_gate": "self_eval", "c_arm": "no_rehearsal"},
                              SIM)


def test_sealed_sources_refuse_every_route_to_the_label():
    for bad in ("x = env.task_completed()", "r = env.compute_reward()", "APIS['a']",
                "get_observation.__self__.env", "env.handle.env.sim", "env.low_level_env"):
        assert gate.source_errors(bad), bad
        with pytest.raises(protocol.ProtocolError):
            protocol.check_policy(bad, sealed=True)
    assert gate.source_errors(POLICY) == []
    protocol.check_policy(POLICY, sealed=True)
    protocol.check_policy("x = env.task_completed()")  # legacy cells are unchanged


# --- ledger -------------------------------------------------------------------


def test_ledger_grades_by_gate_when_sealed(tmp_path):
    identity = {"dev_seeds": [51, 52], "profile": "judgment", "development_gate": "self_eval"}
    state = ledger.NativeWorldState(tmp_path / "task", identity)
    assert state.sealed and state.gate == "self_eval"
    passed = {"gate": {"passed": True}, "task_completed": 0}
    failed = {"gate": {"passed": False}, "task_completed": 1}
    assert state.passed(passed) and not state.passed(failed)
    outcome = state.failure_outcome("why")
    assert outcome["gate"]["verdict"] == "not_evaluated" and "task_completed" not in outcome
    legacy = ledger.NativeWorldState(tmp_path / "legacy", {"dev_seeds": [51], "profile": "judgment"})
    assert not legacy.sealed and legacy.passed({"task_completed": 1})
    assert legacy.failure_outcome("why") == {"reward": 0.0, "task_completed": 0}


# --- the full sealed trial path ------------------------------------------------


class SealedHarness:
    """A C/executable cell with a faked replay that writes sealed artifacts."""

    def __init__(self, stack, development_gate="self_eval", verdict="true", success=True):
        root = Path(stack.enter_context(__import__("tempfile").TemporaryDirectory()))
        self.case_path, self.case = fixtures.make_case(root, "C")
        self.case.update(profile="judgment", executable_world_revision="r1",
                         foundation_revision="r1", c_arm="full", c_lineage="fresh",
                         development_gate=development_gate,
                         inference_endpoint="http://127.0.0.1:8121")
        self.case_path.write_text(json.dumps(self.case))
        self.case, self.repo, self.task_dir = protocol.load_case(self.case_path)
        (self.repo / "scripts/libero/scene_snapshot.py").write_text(POLICY)
        stack.enter_context(patch.object(protocol, "verify_runtime", lambda case, repo: None))
        stack.enter_context(patch("executable_world_profile.checks", return_value={
            "status": "checked", "directory": "/synthetic/offline", "reports": [{"status": "complete"}],
            "conclusions": ["supported_pass"], "cached": False, "retryable": False, "identity": {}}))
        self.verdict, self.success = verdict, success
        self.calls = []
        stack.enter_context(patch.object(protocol, "run_replay", self.fake_replay))
        self.state = ledger.NativeWorldState(self.task_dir, protocol.identity(self.case, self.repo))
        (self.task_dir / "initial_code.py").write_text(POLICY)
        (self.task_dir / "initial_world_program.py").write_text(WORLD)

    def fake_replay(self, command, *, repo, env, directory, timeout, stdin_path=None):
        """What a sealed replay leaves behind: public artifacts without the label,
        the world manifest, and the real outcome at the sealed path."""
        self.calls.append({"command": command, "env": dict(env)})
        output = Path(command[command.index("--args.output-dir") + 1])
        seed = int(command[command.index("--args.trial") + 1])
        trial = output / self.case["suite"] / self.case["task"] / self.case["model"] / "run" / f"trial_{seed:02d}_sandboxrc_0_sealed"
        (trial / "keyframes").mkdir(parents=True)
        for rank in range(10):
            (trial / "keyframes" / f"video_frame_{rank:02d}_of_10_step_{rank * 50:04d}.jpg").write_bytes(b"\xff\xd8jpeg")
        (trial / "summary.txt").write_text("Environment response:\n  Sandbox failed: 0\n  Outcome: sealed\n")
        (trial / "video.mp4").write_bytes(b"mp4")
        (directory / "replay.log").write_text("--- Executing code block 0 ---\n  sandbox_rc=0 (task outcome sealed for the development gate)\nTrial complete!\n  Outcome: sealed\n")
        world = directory / "judgment_world"
        world.mkdir(parents=True, exist_ok=True)
        (world / "manifest.json").write_text(json.dumps({
            "status": "complete", "binding": "shadow", "self_evaluations": [
                {"verdict": self.verdict, "binding": "shadow", "reason": "toy clause",
                 "evidence_ids": [3], "clauses": [{"name": "placed", "verdict": self.verdict, "reason": "toy"}]}],
            "result": {"trial_dir": str(trial), "sandbox_rc": 0, "outcome": "sealed"}}))
        (world / "events.jsonl").write_text(json.dumps({"event": "complete", "result": {"outcome": "sealed"}}) + "\n")
        sealed_path = Path(env[gate.SEALED_ENV])
        sealed_path.parent.mkdir(parents=True, exist_ok=True)
        sealed_path.write_text(json.dumps({"schema_version": 1, "seed": seed, "sandbox_rc": 0,
                                           "reward": 1.0 if self.success else 0.0,
                                           "task_completed": bool(self.success), "terminated": self.success,
                                           "truncated": False, "trial_dir": str(trial)}))
        if "snapshot" in Path(directory).parts:
            for name in ("scene_snapshot.jpg", "scene_snapshot_wrist.jpg"):
                (self.task_dir / name).write_bytes(b"jpg")
        return 0, ""

    def snapshot(self):
        return protocol.run_trial(self.case, self.repo, self.state, "snapshot", 51, None, None, None, None)

    def submit(self, phase, seed):
        return protocol.run_trial(self.case, self.repo, self.state, phase, seed,
                                  self.task_dir / "initial_code.py",
                                  self.task_dir / "initial_world_program.py", None, None)


def public_text(task_dir: Path) -> str:
    chunks = []
    for path in sorted(task_dir.rglob("*")):
        chunks.append(str(path))
        if path.is_file() and path.suffix in {".json", ".jsonl", ".txt", ".log", ".md"}:
            chunks.append(path.read_text(errors="replace"))
    return "\n".join(chunks)


@pytest.mark.parametrize("verdict, success, expect_pass, flag", [
    ("true", True, True, None), ("true", False, True, "gate_false_accept"),
    ("false", True, False, "gate_false_reject"), ("unknown", False, False, None)])
def test_self_eval_gate_grades_and_seals(verdict, success, expect_pass, flag):
    with contextlib.ExitStack() as stack:
        h = SealedHarness(stack, "self_eval", verdict=verdict, success=success)
        h.snapshot()
        record = h.submit("initial", 51)
        assert record["status"] == "complete" and record["gate"]["source"] == "self_eval"
        assert record["gate"]["verdict"] == verdict and record["gate"]["passed"] is expect_pass
        assert "task_completed" not in record and "reward" not in record
        assert "foundation_calibration" not in record
        assert h.calls[-1]["env"][gate.SEALED_ENV].startswith(str(gate.sealed_root(h.case)))
        # Nothing under the solver's task directory names the outcome.
        text = public_text(h.task_dir)
        for token in ("taskcompleted", "task_completed", "_reward_", "Task Completed"):
            assert token not in text, token
        assert gate.public_leaks(Path(record["trial_dir"])) == []
        # Progress and selection read the gate.
        progress = h.state.progress()
        assert (51 in progress["seeds_passing"]) is expect_pass
        assert (51 in progress["seeds_pending_repair"]) is (not expect_pass)
        only = next(iter(progress["tested_bundles"].values()))
        assert (only["passes"] == [51]) is expect_pass
        # Oracle beside gate, outside the boundary.
        rows = [r for r in gate.read_sealed_ledger(h.case) if r["phase"] == "initial"]
        assert len(rows) == 1 and rows[0]["oracle"]["task_completed"] is success
        assert rows[0]["self_eval_verdict"] == verdict
        for name in ("gate_false_accept", "gate_false_reject"):
            assert rows[0][name] is (name == flag)
        assert rows[0]["self_eval_calibration"] == gate.calibration(verdict, success)
        assert (gate.sealed_trial_dir(h.case, record["directory"]) / "oracle.json").is_file()
        status = protocol.check(h.case, h.repo, h.state)
        assert "foundation_calibration" not in status and status["development_gate"]["sealed"]


def test_vlm_judge_gate_uses_the_judge_not_the_world():
    seen = {}
    def fake_judge(endpoint, model, task_language, trial_dir, **kwargs):
        seen.update(endpoint=endpoint, model=model, task=task_language, frames=vlm_judge.keyframes(trial_dir))
        return {"status": "complete", "verdict": "failure", "reason": "bowl still on table",
                "frames": ["keyframes/a.jpg"], "model": model, "elapsed_seconds": 1.0}
    with contextlib.ExitStack() as stack:
        stack.enter_context(patch.object(vlm_judge, "judge", fake_judge))
        h = SealedHarness(stack, "vlm_judge", verdict="true", success=True)
        h.snapshot()
        record = h.submit("initial", 51)
        assert record["gate"] == {"source": "vlm_judge", "verdict": "failure", "passed": False,
                                  "reason": "bowl still on table", "frames": ["keyframes/a.jpg"],
                                  "model": "claude-opus-4-6", "judge_status": "complete", "elapsed_seconds": 1.0}
        assert seen["task"] == "put the bowl on the plate" and len(seen["frames"]) == 10
        row = [r for r in gate.read_sealed_ledger(h.case) if r["phase"] == "initial"][0]
        assert row["self_eval_verdict"] == "true" and row["gate_false_reject"] is True
        assert row["oracle"]["task_completed"] is True
        # The judge's verdict is also recorded in its own ledger column (2026-10-06 fix:
        # the first launch left it null and the analysis had to recover it from the gate report).
        assert row["judge_verdict"] == "failure"


def test_unavailable_judge_is_not_a_pass_and_not_an_infrastructure_error():
    with contextlib.ExitStack() as stack:
        stack.enter_context(patch.object(vlm_judge, "judge", lambda *a, **k: {
            "status": "unavailable", "verdict": None, "reason": "HTTP 503", "frames": []}))
        h = SealedHarness(stack, "vlm_judge")
        h.snapshot()
        record = h.submit("initial", 51)
        assert record["status"] == "complete" and record["gate"]["verdict"] == "unavailable"
        assert record["gate"]["passed"] is False and h.state.retries_used(51) == 1


def test_missing_sealed_outcome_is_infrastructure_not_a_grade():
    with contextlib.ExitStack() as stack:
        h = SealedHarness(stack)
        h.snapshot()
        original = h.fake_replay
        def lossy(command, **kwargs):
            code, error = original(command, **kwargs)
            Path(kwargs["env"][gate.SEALED_ENV]).unlink()
            return code, error
        with patch.object(protocol, "run_replay", lossy):
            record = h.submit("initial", 51)
        assert record["status"] == "infrastructure_error" and "gate" not in record
        assert h.state.retries_used(51) == 1  # the retry stays spent, as in a legacy cell


def test_sealed_outcome_must_agree_with_the_public_directory(tmp_path):
    results = tmp_path / "results"
    trial = results / "x" / "trial_51_sandboxrc_0_sealed"
    trial.mkdir(parents=True)
    outcome = tmp_path / "outcome.json"
    outcome.write_text(json.dumps({"sandbox_rc": 0, "reward": 1.0, "task_completed": True, "trial_dir": str(trial)}))
    parsed = protocol.parse_sealed_result(results, 51, outcome)
    assert parsed["task_completed"] == 1 and parsed["trial_dir"] == str(trial)
    outcome.write_text(json.dumps({"sandbox_rc": 1, "reward": 1.0, "task_completed": True, "trial_dir": str(trial)}))
    assert protocol.parse_sealed_result(results, 51, outcome) is None
    assert protocol.parse_sealed_result(results, 51, tmp_path / "missing.json") is None
    assert protocol.parse_result(results, 51) is None  # a sealed name is not a legacy result


# --- replay sealed mode -------------------------------------------------------


def load_replay_trial():
    if "tyro" not in sys.modules and importlib.util.find_spec("tyro") is None:
        stub = types.ModuleType("tyro")
        stub.__spec__ = importlib.machinery.ModuleSpec("tyro", None)
        stub.conf = types.SimpleNamespace()
        sys.modules["tyro"] = stub
    if "replay_trial_under_test" in sys.modules:
        return sys.modules["replay_trial_under_test"]
    spec = importlib.util.spec_from_file_location("replay_trial_under_test", SIM / "scripts/libero/replay_trial.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve string annotations through sys.modules
    spec.loader.exec_module(module)
    return module


def test_sealed_env_view_exposes_only_the_task_language(monkeypatch):
    replay = load_replay_trial()
    assert replay.SEALED_OUTCOME_ENV == gate.SEALED_ENV
    low_level = types.SimpleNamespace(handle=types.SimpleNamespace(task_language="put the bowl on the plate",
                                                                   env="SIMULATOR"),
                                      task_completed=lambda: True, compute_reward=lambda: 1.0)
    view = replay._SealedEnvView(low_level)
    assert view.handle.task_language == "put the bowl on the plate"
    assert not hasattr(view.handle, "env") and not hasattr(view, "task_completed")
    monkeypatch.delenv(gate.SEALED_ENV, raising=False)
    assert replay._sealed_outcome_path() is None
    monkeypatch.setenv(gate.SEALED_ENV, "/tmp/outcome.json")
    assert replay._sealed_outcome_path() == "/tmp/outcome.json"


def test_sandbox_proxy_hides_the_simulator_from_generated_code():
    from aspire.sim.cap.envs.tasks.base import CodeExecutionEnvBase
    env = CodeExecutionEnvBase.__new__(CodeExecutionEnvBase)
    low_level = types.SimpleNamespace(handle=types.SimpleNamespace(task_language="toy"), task_completed=lambda: True)
    env.low_level_env, env._apis = low_level, {}
    env._init_exec_globals()
    assert env._exec_globals["env"] is low_level and "APIS" in env._exec_globals
    replay = load_replay_trial()
    replay._seal_sandbox(env)
    assert isinstance(env._exec_globals["env"], replay._SealedEnvView)
    assert env._exec_globals["env"].handle.task_language == "toy"
    assert "APIS" not in env._exec_globals and "task_completed" not in dir(env._exec_globals["env"])


# --- judge ----------------------------------------------------------------------


def test_judge_frame_selection_and_parsing(tmp_path):
    folder = tmp_path / "keyframes"
    folder.mkdir()
    for rank in range(10):
        (folder / f"video_frame_{rank:02d}_of_10_step_{rank * 7:04d}.jpg").write_bytes(b"\xff\xd8")
    frames = vlm_judge.keyframes(tmp_path)
    assert [p.name[12:14] for p in frames] == [f"{i:02d}" for i in range(10)]
    chosen = vlm_judge.select_frames(frames, 3)
    assert [p.name[12:14] for p in chosen] == ["00", "04", "09"]
    assert vlm_judge.select_frames(frames[:2], 3) == frames[:2]
    messages = vlm_judge.build_messages("put the bowl on the plate", chosen)
    assert messages[0]["role"] == "system" and "put the bowl on the plate" in messages[1]["content"][0]["text"]
    assert sum(1 for c in messages[1]["content"] if c["type"] == "image_url") == 3
    assert vlm_judge.parse_verdict("<think>hmm</think>\nVERDICT: Success\nREASON: bowl rests on plate") == ("success", "bowl rests on plate")
    assert vlm_judge.parse_verdict("I think it failed.")[0] is None
    assert vlm_judge.parse_verdict("VERDICT: unsure\nREASON: occluded")[0] == "unsure"


def test_judge_is_bounded_and_honest_about_infrastructure(tmp_path):
    report = vlm_judge.judge("http://127.0.0.1:1", "m", "task", tmp_path)
    assert report["status"] == "unavailable" and "no keyframes" in report["reason"]
    folder = tmp_path / "keyframes"
    folder.mkdir()
    (folder / "video_frame_00_of_10_step_0000.jpg").write_bytes(b"\xff\xd8")
    def failing(*args, **kwargs):
        raise RuntimeError("judge endpoint unavailable after 3 attempts: HTTP 503")
    report = vlm_judge.judge("http://127.0.0.1:1", "m", "task", tmp_path, requester=failing)
    assert report["status"] == "unavailable" and report["verdict"] is None
    def answering(endpoint, model, messages, **kwargs):
        return {"choices": [{"message": {"content": "VERDICT: failure\nREASON: nothing moved", "reasoning": "..."}}]}
    report = vlm_judge.judge("http://127.0.0.1:1", "m", "task", tmp_path, requester=answering)
    assert report["status"] == "complete" and report["verdict"] == "failure" and report["reasoning_present"]
    unparsed = vlm_judge.judge("http://127.0.0.1:1", "m", "task", tmp_path,
                               requester=lambda *a, **k: {"choices": [{"message": {"content": "maybe"}}]})
    assert unparsed["verdict"] == "unsure"


# --- prompts --------------------------------------------------------------------


def test_sealed_prompt_tells_the_truth_and_legacy_prompt_is_unchanged(tmp_path):
    path, case = fixtures.make_case(tmp_path, "C")
    case.update(profile="judgment", executable_world_revision="r1", foundation_revision="r1",
                c_arm="full", c_lineage="fresh", world_interface_doc="docs/x/NATIVE_WORLD_INTERFACE.md",
                inference_endpoint="http://127.0.0.1:8121")
    legacy = campaign.worker_prompt(case, SIM)
    assert "_reward_1.000" in legacy and "Development gate" not in legacy
    assert "Read the per-trial development calibration feedback" in legacy
    assert campaign.worker_prompt({**case, "development_gate": "oracle"}, SIM) == legacy
    for which in ("self_eval", "vlm_judge"):
        sealed = campaign.worker_prompt({**case, "development_gate": which}, SIM)
        assert f"Development gate: {which}" in sealed
        assert "_reward_1.000" not in sealed and "stdout/stderr/reward" not in sealed
        assert "Read the per-trial development calibration feedback" not in sealed
        assert "graded by the environment, not" in sealed and "gate.passed" in sealed
        assert "3 TOTAL retries per seed" in sealed
    assert "FINAL shadow `done()`" in campaign.worker_prompt({**case, "development_gate": "self_eval"}, SIM)
    assert "independent judge" in campaign.worker_prompt({**case, "development_gate": "vlm_judge"}, SIM)


def test_public_leak_scan_catches_names_and_text(tmp_path):
    (tmp_path / "trial_51_sandboxrc_0_reward_1.000_taskcompleted_1").mkdir()
    (tmp_path / "summary.txt").write_text("  Task Completed: True\n")
    (tmp_path / "code.py").write_text("# reward= is fine in authored code\n")
    leaks = gate.public_leaks(tmp_path)
    assert any(l.startswith("name:") for l in leaks) and any("summary.txt" in l for l in leaks)
    assert not any("code.py" in l for l in leaks)
