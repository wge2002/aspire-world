"""Behavioral infrastructure checks; no simulator, assets, or model calls."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import types
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("simple_world_tested", ROOT / "cap/world_model/simple_world.py")
simple = importlib.util.module_from_spec(spec)
spec.loader.exec_module(simple)
sys.path.insert(0, str(ROOT / "scripts/libero"))
import native_world_protocol as protocol
import native_world_heldout as heldout
from native_world_fixloop_state import NativeWorldState, ProtocolError, code_hash, bundle_identity
from simple_world_profile import worker_prompt

WORLD = '''state = {"n": 0, "x": None}
def update(obs, last_action):
    state["n"] += 1
    state["x"] = obs["x"]
def snapshot():
    return dict(state)
def estimate():
    return state["x"]
'''


def source(tmp_path, text=WORLD):
    p = tmp_path / "world.py"
    p.write_text(text)
    return p, hashlib.sha256(p.read_bytes()).hexdigest()


def test_import_persistence_reset_and_observational_trace(tmp_path):
    path, digest = source(tmp_path)
    old = types.ModuleType("previous_world")

    class Api:
        calls = 0
        def observe(self):
            self.calls += 1
            return {"x": self.calls}
        def functions(self):
            return {"get_observation": self.observe}

    api = Api()
    with patch.dict(sys.modules, {"world": old}):
        for episode in range(2):
            with simple.WorldSession(path, digest, tmp_path / f"episode{episode}") as session:
                session.bind_api(api)
                ns = dict(api.functions())
                exec("import world\nassert world.snapshot()['n'] == 0\n"
                     "world.update(get_observation(), None)\n"
                     "assert world.snapshot()['n'] == 1\n"
                     "world.update(get_observation(), 'observe')\n"
                     "assert world.snapshot()['n'] == 2\n", ns)
                session.complete(task_completed=True)
            assert sys.modules["world"] is old
            rows = [json.loads(s) for s in (tmp_path / f"episode{episode}/events.jsonl").read_text().splitlines()]
            assert [r["snapshot"]["n"] for r in rows if r["event"] == "world_update"] == [1, 2]
            assert rows[0]["snapshot"]["n"] == 0
            assert sum(r["event"] == "public_api" for r in rows) == 2
    assert api.calls == 4  # snapshots/logging added no sensing calls.


def test_hash_and_same_path_source_bytes(tmp_path):
    p, h = source(tmp_path)
    with pytest.raises(ValueError, match="hash"):
        simple.WorldSession(p, "bad", tmp_path / "bad")
    with simple.WorldSession(p, h, tmp_path / "first") as session:
        assert session.snapshot()["n"] == 0
    p.write_text(WORLD.replace('"n": 0', '"n": 7'))
    with simple.WorldSession(p, hashlib.sha256(p.read_bytes()).hexdigest(), tmp_path / "second") as session:
        assert session.snapshot()["n"] == 7


def test_real_executor_rebinds_wrapped_api_and_keeps_world_between_blocks(tmp_path):
    from aspire.sim.cap.envs.tasks.base import CodeExecutionEnvBase
    path, digest = source(tmp_path)
    class Api:
        def functions(self):
            return {"get_observation": lambda: {"x": 9}}
    # Exercise the real production executor without constructing any simulator.
    env = CodeExecutionEnvBase.__new__(CodeExecutionEnvBase)
    env.low_level_env = object()
    env._apis = {"public": Api()}
    env._get_observation = lambda: {}
    env._init_exec_globals()
    with simple.WorldSession(path, digest, tmp_path / "executed") as session:
        session.attach(env)
        result = env._exec_user_code("import world\nworld.update(get_observation(), None)")
        assert result["ok"], result["stderr"]
        result = env._exec_user_code("assert world.snapshot()['n'] == 1\nworld.update(get_observation(), 'observe')")
        assert result["ok"], result["stderr"]
        assert session.snapshot() == {"n": 2, "x": 9}
        assert "env" not in session.module.__dict__ and "APIS" not in session.module.__dict__
        assert session.api_calls == 2


def test_authored_errors_visible_and_module_restored(tmp_path):
    p, h = source(tmp_path, WORLD.replace('return dict(state)', 'return {"bad": object()}'))
    before = sys.modules.get("world")
    with pytest.raises(simple.WorldProgramError):
        with simple.WorldSession(p, h, tmp_path / "bad"):
            pass
    assert sys.modules.get("world") is before
    manifest = json.loads((tmp_path / "bad/manifest.json").read_text())
    assert manifest["status"] == "program_error" and manifest["errors"]
    assert simple.module_errors(WORLD) == []
    assert simple.module_errors(WORLD + '\nget_observation()')


@pytest.mark.parametrize("task,gpu", [("put_the_bowl_on_the_plate", 2), ("open_the_middle_drawer_of_the_cabinet", 3)])
def test_two_task_bundle_config_environment_and_prompt(tmp_path, task, gpu):
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    policy = task_dir / "initial_code.py"
    policy.write_text('import world\nworld.update({"x": 1}, None)\n')
    world, h = source(task_dir)
    case = dict(id="C", condition="C", profile="simple", suite="libero_goal_swap", task=task,
                sim=str(ROOT), gpu=gpu, cuda_visible_devices=str(gpu), egl_device_id=gpu,
                sam3_url="sam", graspnet_url="grasp", pyroki_url="ik", egl_vendor_config="/vendor.json",
                skill_library_dir="unused")
    bundle, sources = protocol.collect_bundle(case, task_dir, "initial", policy, world, None)
    assert set(bundle) == {"policy", "world"}
    changed = dict(bundle, world=code_hash(WORLD + '\n# version2\n'))
    assert bundle_identity(bundle) != bundle_identity(changed)
    trial = tmp_path / "trial"
    trial.mkdir()
    (trial / "code.py").write_bytes(policy.read_bytes())
    cfg = json.loads(protocol.write_world_config(case, trial, sources).read_text())
    assert cfg["task_gate"] == {"suite": case["suite"], "task": task}
    assert cfg["world_program_sha256"] == h
    assert protocol.result_root(case, trial, "initial", 51) == trial / "results"
    env = protocol.runtime_env(case, ROOT, {"ANTHROPIC_API_KEY": "not-a-real-secret"})
    assert env["MUJOCO_EGL_DEVICE_ID"] == str(gpu)
    assert env["__EGL_VENDOR_LIBRARY_FILENAMES"] == "/vendor.json"
    assert "ANTHROPIC_API_KEY" not in env
    text = worker_prompt(case, ROOT)
    assert task in text and "snapshot()" in text and "initial_inventory" not in text
    assert "3cm" not in text and "SUPPORT" not in text


def test_attempts_and_heldout_remain_enforced(tmp_path):
    state = NativeWorldState(tmp_path / "task", {"dev_seeds": [51], "profile": "simple"})
    bundle = {"policy": code_hash("get_observation()"), "world": code_hash(WORLD)}
    for _ in range(2):
        row = state.begin_trial("diagnostic", 51, bundle, {})
        state.finish_trial(row, result={"sandbox_rc": 0, "task_completed": 0, "reward": 0.0}, exit_code=0)
    with pytest.raises(ProtocolError, match="reserve it for a real graded trial"):
        state.begin_trial("diagnostic", 51, bundle, {})
    row = state.begin_trial("smoke", 51, bundle, {})
    state.finish_trial(row, result={"sandbox_rc": 0, "task_completed": 0, "reward": 0.0}, exit_code=0)
    with pytest.raises(ProtocolError):
        state.begin_trial("diagnostic", 51, bundle, {})
    with pytest.raises(ProtocolError):
        state.begin_trial("diagnostic", 1, bundle, {})
    assert not state.observation_fallback(bundle, "get_observation()")
    assert heldout.HELDOUT_SEEDS == tuple(range(1, 51))


def test_frozen_world_must_match_selected_tested_bundle(tmp_path):
    task = tmp_path / "task"
    task.mkdir()
    (task / "fix_code.py").write_text("import world\nworld.update({}, None)\n")
    (task / "fix_world_program.py").write_text(WORLD)
    bundle = {"policy": code_hash((task / "fix_code.py").read_text()), "world": code_hash(WORLD)}
    stage = {"stage1_complete": True, "selected_bundle": bundle,
             "tested_bundles": {bundle_identity(bundle): {}},
             "selection": {"bundle_sha256": bundle_identity(bundle)}}
    (task / "stage1_result.json").write_text(json.dumps(stage))
    evaluation = tmp_path / "eval"
    got, kept = heldout.frozen_bundle({"condition": "C", "profile": "simple"}, task, evaluation)
    assert got == bundle and set(kept) == {"policy", "world"}
    kept["world"].write_text(WORLD + "\n# change\n")
    with pytest.raises(ProtocolError, match="changed"):
        heldout.assert_frozen(bundle, kept, 2)
