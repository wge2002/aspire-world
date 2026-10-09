"""Software invariants; the toy world is not experimental task evidence."""
import ast
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import types
from unittest.mock import patch

import numpy as np
import pytest

SIM = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SIM.parents[1]))
sys.path.insert(0, str(SIM / "scripts/libero"))
sys.path.insert(0, str(SIM / "scripts/common"))
from aspire.sim.cap.world_model.executable_world import (
    ExecutableSession, ReplayBinding, ReplayDivergence, SelfEvaluationDisabled,
    TapeStore, Unsupported, WorldProgramError, API_NAMES, module_errors, run_offline)
import executable_world_profile as profile

WORLD = '''
state = {"value": None, "evidence": None, "commands": []}
def snapshot(): return dict(state)
def update(obs, last_action): state["derived"] = obs["values"]
def query(name, **kwargs): return dict(state)
def predict(call): return {"predicted": call["function"]}
def observe(event):
    if event["kind"] == "measurement" and event["error"] is None:
        state["value"] = event["result"]["value"]
        state["evidence"] = event["evidence_id"]
    if event["kind"] == "command_receipt": state["commands"].append(event["call"]["function"])
def simulate(call):
    if call["function"] == "get_observation": return {"value": 7}
    if call["function"] == "goto_pose": return None
    raise Unsupported(call["function"])
def done(): return {"verdict": "true", "evidence_ids": [state["evidence"]], "reason": "toy"}
'''


def source(tmp_path, text=WORLD):
    p = tmp_path / "world.py"
    p.write_text(text)
    return p, hashlib.sha256(p.read_bytes()).hexdigest()


def session(tmp_path, arm="full", text=WORLD):
    p, sha = source(tmp_path, text)
    return ExecutableSession(p, sha, tmp_path / "session", arm)


def test_command_cannot_become_observed_progress_or_fresh_goal(tmp_path):
    with session(tmp_path) as s:
        s.invoke("get_observation", lambda: {"value": 2}, (), {})
        assert s.done()["verdict"] == "true"
        s.invoke("goto_pose", lambda position: None, ([999, 0, 0],), {})
        assert s.module.query("state")["value"] == 2
        assert s.done()["verdict"] == "unknown"
        with pytest.raises(WorldProgramError, match="commands are not measurements"):
            s.module.update({"values": {"position": 999}, "evidence_ids": [1]}, "move")
        s.invoke("get_observation", lambda: {"value": 3}, (), {})
        assert s.done()["verdict"] == "true"
        s.module.update({"values": {"derived": 3}, "evidence_ids": [2]}, "observe")
        assert s.module.snapshot()["derived"] == {"derived": 3}


def test_prediction_precedes_response_and_cannot_mutate_real_arguments(tmp_path):
    modified = WORLD.replace('return {"predicted": call["function"]}',
                             'call["kwargs"].clear(); return {"predicted": call["function"]}')
    with session(tmp_path, text=modified) as s:
        received = []
        def motion(**kwargs):
            received.append(kwargs)
            rows = [json.loads(x) for x in (s.output / "events.jsonl").read_text().splitlines()]
            assert rows[-1]["event"] == "prediction"
        s.invoke("goto_pose", motion, (), {"position": [1, 2, 3]})
        assert received == [{"position": [1, 2, 3]}]
        assert s.module.snapshot()["value"] is None


def test_ablation_disables_predicate_execution_not_just_label(tmp_path):
    text = WORLD.replace('def done(): return', 'def done(): raise AssertionError("must not evaluate"); return')
    with session(tmp_path, "no_self_eval", text) as s:
        with pytest.raises(SelfEvaluationDisabled):
            s.module.done()
        s.complete(task_completed=False)
        assert s.self_evaluations == []
        assert s.errors == []


def test_tape_arrays_roundtrip_and_integrity(tmp_path):
    store = TapeStore(tmp_path)
    value = {"x": (np.arange(12, dtype=np.float32).reshape(3, 4), np.bool_(True))}
    encoded = store.encode(value, save=True)
    restored = store.decode(encoded)
    assert isinstance(restored["x"], tuple)
    np.testing.assert_array_equal(restored["x"][0], value["x"][0])
    assert restored["x"][0].dtype == np.float32
    path = next((tmp_path / "arrays").glob("*.npy"))
    with path.open("wb") as f:
        np.save(f, np.zeros((3, 4), dtype=np.float32), allow_pickle=False)
    with pytest.raises(ValueError, match="changed"):
        store.decode(encoded)


def test_replay_divergence_never_releases_old_future(tmp_path):
    with session(tmp_path) as s:
        s.invoke("goto_pose", lambda position: None, (np.array([1., 2., 3.]),), {})
        s.invoke("get_observation", lambda: {"value": 12345}, (), {})
    replay = ReplayBinding(tmp_path / "session/public_tape.jsonl")
    with patch.object(replay.store, "decode", side_effect=AssertionError("future must stay hidden")):
        with pytest.raises(ReplayDivergence):
            replay.call("goto_pose", np.array([4., 2., 3.]))
    assert replay.index == 0
    assert replay.call("goto_pose", np.array([1., 2., 3.])) is None
    assert replay.call("get_observation") == {"value": 12345}


def test_same_source_real_replay_and_rehearsal_bindings(tmp_path):
    with session(tmp_path) as s:
        s.invoke("get_observation", lambda: {"value": 2}, (), {})
        s.invoke("goto_pose", lambda position: None, ([2, 0, 0],), {})
        s.invoke("get_observation", lambda: {"value": 999}, (), {})
    policy = tmp_path / "policy.py"
    policy.write_text('import world\na=get_observation()\ngoto_pose([a["value"],0,0])\nb=get_observation()\nworld.done()\n')
    world = tmp_path / "world.py"
    tape = tmp_path / "session/public_tape.jsonl"
    replay = run_offline(policy, world, tmp_path / "replay", mode="replay", tape=tape)
    assert replay["status"] == "complete"
    assert replay["matched_calls"] == 3
    rehearsal = run_offline(policy, world, tmp_path / "rehearsal", tape=tape)
    assert rehearsal["status"] == "complete"
    rows = [json.loads(x) for x in (tmp_path / "rehearsal/events.jsonl").read_text().splitlines()]
    observations = [x for x in rows if x["event"] == "public_api" and x["function"] == "get_observation"]
    assert observations[0]["result"]["value"] == 2  # Only the recorded initial scene.
    assert observations[1]["result"]["value"] == 7  # Generated effect, never old future 999.
    assert any(x["event"] == "model_goal" for x in rows)
    assert not any(x["event"] == "self_evaluation" for x in rows)


def test_changed_policy_stops_replay_at_first_changed_action(tmp_path):
    with session(tmp_path) as s:
        s.invoke("get_observation", lambda: {"value": 2}, (), {})
        s.invoke("goto_pose", lambda position: None, ([2, 0, 0],), {})
        s.invoke("get_observation", lambda: {"value": 999}, (), {})
    policy = tmp_path / "policy.py"
    policy.write_text('get_observation()\ngoto_pose([9,0,0])\nget_observation()\n')
    r = run_offline(policy, tmp_path / "world.py", tmp_path / "replay", mode="replay", tape=tmp_path / "session/public_tape.jsonl")
    assert r["status"] == "unsupported"
    assert r["matched_calls"] == 1
    assert r["api_calls"] == 1


def test_missing_semantics_is_unknown_and_not_fictitious_success(tmp_path):
    p, _ = source(tmp_path, WORLD.replace('if call["function"] == "get_observation": return {"value": 7}',
                                        'if call["function"] == "get_observation": raise Unsupported("unmodeled image")'))
    code = tmp_path / "policy.py"; code.write_text('get_observation()\n')
    r = run_offline(code, p, tmp_path / "offline")
    assert r["status"] == "unsupported"
    assert "goal" not in r


def test_no_rehearsal_never_starts_offline_process(tmp_path):
    case = {"executable_world_revision": "r1", "c_arm": "no_rehearsal", "condition": "C", "profile": "judgment", "c_lineage": "fresh"}
    with patch.object(profile.subprocess, "run", side_effect=AssertionError("must not start")):
        assert profile.checks(case, tmp_path, None, None, {})["status"] == "disabled"


def test_offline_namespace_covers_every_exported_public_api():
    exported = set()
    for name in ("libero_reduced.py", "libero_reduced_skill_library.py"):
        tree = ast.parse((SIM / "cap/integrations/franka" / name).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and node.value.id == "fns" and isinstance(node.slice, ast.Constant):
                exported.add(node.slice.value)
    assert exported <= API_NAMES


@pytest.mark.parametrize("api", ["plan_grasp_from_point_clouds", "subsample_point_cloud", "filter_noise"])
def test_unmodeled_real_public_api_is_unsupported_not_a_policy_error(tmp_path, api):
    world, _ = source(tmp_path)
    code = tmp_path / "policy.py"; code.write_text(api + "([])\n")
    r = run_offline(code, world, tmp_path / "offline")
    assert r["status"] == "unsupported"


def test_profile_rejects_a_and_unknown_arm():
    with pytest.raises(ValueError):
        profile.validate({"executable_world_revision": "r1", "c_arm": "full", "condition": "A", "profile": "judgment"})
    assert module_errors(WORLD) == []
    assert "world must define done()" in module_errors(WORLD.replace("def done()", "def wrong()"))


def test_existing_prompts_unchanged_without_flag():
    before = SIM / "docs/experiments/code-world-c-opt-ablation-20260920/engineering-base/scripts/libero/native_world_campaign.py"
    if not before.exists():
        pytest.skip("comparison requires saved engineering source from integration")
    def load(name, path):
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        return module
    old = load("campaign_before", before)
    new = load("campaign_after", SIM / "scripts/libero/native_world_campaign.py")
    for condition in ("A", "C"):
        for revision in (None, "r1"):
            case = {"id": "test", "suite": "libero_goal_swap", "task": "task", "gpu": 2,
                    "condition": condition, "profile": "judgment" if condition == "C" else "legacy_native",
                    "skill_library_dir": "test/skills", "control": "/tmp/control"}
            if revision and condition == "C": case["world_use_revision"] = revision
            # The user-requested retry rename (2026-10-05) changes the budget
            # wording deliberately. Everything else must still render byte for
            # byte: every differing line has to be budget vocabulary.
            import difflib, re
            before_text, after_text = old.worker_prompt(case, SIM), new.worker_prompt(case, SIM)
            changed = [line for line in difflib.ndiff(before_text.splitlines(), after_text.splitlines())
                       if line[:1] in "+-"]
            vocabulary = re.compile(r"retr(y|ies)|attempt|charge|spent|spends|slot|budget|interrupted|BLOCKED|smoke|per-seed|snapshot", re.I)
            assert changed, "the rename should have changed the budget wording"
            assert all(vocabulary.search(line) for line in changed), [l for l in changed if not vocabulary.search(l)]


def test_supported_rehearsal_error_rejects_without_charging_trial(tmp_path):
    import native_world_protocol as protocol
    case = {"executable_world_revision": "r1", "c_arm": "full"}
    state = types.SimpleNamespace(task_dir=tmp_path, reject=lambda *args: rejections.append(args),
                                  validate_admission=lambda *args: None,
                                  begin_trial=lambda *args: pytest.fail("cannot charge simulator"))
    rejections = []
    with patch.object(protocol, "verify_runtime"), patch.object(protocol, "collect_bundle", return_value=({"policy": "a" * 64}, {})), \
         patch.object(protocol, "runtime_env", return_value={}), \
         patch.object(profile, "checks", return_value={"status": "rejected", "directory": "/tmp/check"}):
        with pytest.raises(protocol.ProtocolError, match="offline candidate error"):
            protocol._run_trial(case, tmp_path, state, "repair", 51, None, None, None, None)
    assert len(rejections) == 1
