"""CPU tests for the common LIBERO reset fix (libero-pro-common-v1 environment).

No MuJoCo/LIBERO is imported: the handle is real code over a recording stub
env, and FrankaLiberoEnv.reset is compiled from source like the motor methods
in test_streaming_world.py.
"""
import ast
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from aspire.sim.cap.integrations.libero import LiberoHandle, init_state_index
from aspire.sim.cap.world_model.control_progress import ControlProgress, reset_scope

N_STATES = 50


class StubLiberoEnv:
    """Mimics OffScreenRenderEnv: reset() re-samples, set_init_state() restores
    a stored state and returns that state's observation without resetting."""

    def __init__(self):
        self.calls = []
        self.state = None

    def seed(self, seed):
        self.calls.append(("seed", seed))

    def reset(self):
        self.calls.append(("reset",))
        self.state = ("sampled", len(self.calls))
        return {"state": self.state, "robot0_joint_pos": [0.0] * 7}

    def set_init_state(self, init_state):
        self.calls.append(("set_init_state", init_state))
        self.state = init_state
        return {"state": self.state, "robot0_joint_pos": [float(init_state)] * 7}


def handle(env=None):
    return LiberoHandle(env=env or StubLiberoEnv(), suite_name="libero_goal_swap", task_id=0,
                        task_language="put the bowl on the plate", init_states=list(range(N_STATES)))


@pytest.mark.parametrize("seed,expected", [(51, 0), (52, 1), (65, 14), (1, 0), (50, 49),
                                           (None, 0), (101, 0), (0, 49)])
def test_seed_maps_to_initial_state_cyclically(seed, expected):
    assert init_state_index(seed, N_STATES) == expected
    h = handle()
    obs, info = h.reset(seed=seed)
    assert info == {"init_state_index": expected}
    assert h.env.state == expected and obs["state"] == expected


def test_handle_resets_once_then_loads_state_once_and_returns_its_observation():
    h = handle()
    obs, _ = h.reset(seed=53)
    assert h.env.calls == [("seed", 53), ("reset",), ("set_init_state", 2)]
    # The returned observation is the restored state's, not the pre-load sample.
    assert obs["state"] == h.env.state == 2


def test_handle_without_init_states_keeps_plain_reset():
    h = LiberoHandle(env=StubLiberoEnv(), suite_name="s", task_id=0, task_language="t", init_states=None)
    obs, info = h.reset(seed=7)
    assert h.env.calls == [("seed", 7), ("reset",)] and info == {}
    assert obs["state"][0] == "sampled"
    with pytest.raises(ValueError):
        init_state_index(1, 0)


SOURCE = Path(__file__).resolve().parents[1] / "cap/envs/simulators/libero.py"
_cls = next(n for n in ast.parse(SOURCE.read_text()).body
            if isinstance(n, ast.ClassDef) and n.name == "FrankaLiberoEnv")
_reset = next(n for n in _cls.body if isinstance(n, ast.FunctionDef) and n.name == "reset")
_ns = {"np": np, "reset_scope": reset_scope, "Any": object}
exec(compile(ast.Module([_reset], type_ignores=[]), str(SOURCE), "exec"), _ns)


class ResetEnv:
    def __init__(self):
        self.handle = handle()
        self._streaming_progress = ControlProgress()
        self.resets_seen = []
        self._streaming_progress.add("reset", lambda: self.resets_seen.append(list(self.handle.env.calls)))
        self.gripper_link_idx = 0
        self.viser_debug = False
        self.settle_states = []
        data = SimpleNamespace(qpos=np.zeros(9), xquat=np.array([[1.0, 0, 0, 0]]), xpos=np.zeros((1, 3)))
        self.handle.env.sim = SimpleNamespace(data=data)

    def _step_once(self):
        assert self._streaming_progress.suspended  # settling stays inside reset_scope
        self.settle_states.append(self.handle.env.state)

    def get_observation(self):
        return {"state": self.handle.env.state}


ResetEnv.reset = _ns["reset"]


@pytest.mark.parametrize("seed,expected", [(51, 0), (65, 14), (None, 0)])
def test_franka_reset_delegates_once_and_settles_on_selected_state(seed, expected):
    env = ResetEnv()
    obs, info = env.reset(seed=seed)
    calls = env.handle.env.calls
    assert calls == [("seed", seed), ("reset",), ("set_init_state", expected)]
    # No reset after set_init_state, so the 10 settling steps run on the selected state.
    assert env.settle_states == [expected] * 10
    assert env._current_obs["state"] == expected and obs["state"] == expected
    assert env.home_joint_position.tolist() == [float(expected)] * 7
    assert info == {"task_prompt": "put the bowl on the plate"}
    # The streaming reset callback fired before the env was touched, and the scope closed.
    assert env.resets_seen == [[]] and not env._streaming_progress.suspended


def test_franka_reset_source_has_no_second_reset_or_state_override():
    src = ast.get_source_segment(SOURCE.read_text(), _reset)
    assert "set_init_state" not in src and "env.reset()" not in src
    assert src.count("self.handle.reset(seed=seed)") == 1
