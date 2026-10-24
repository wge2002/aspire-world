"""Opt-in checked-motion-v1 contract at the actual driver/API/replay boundary.

The production FrankaLiberoEnv motor/gripper/hook methods are compiled from
source (see test_robot_frame_contract) and the actual FrankaLiberoApiReduced
solve_ik/goto_pose/open_gripper/close_gripper run against a synthetic IK.
No simulator object state, asset geometry or evaluator predicate is used.
"""
import ast
import json
from types import SimpleNamespace

import numpy as np
import pytest

import test_robot_frame_contract as rfc
from aspire.sim.cap.world_model.World import TrackingError
from aspire.sim.cap.world_model.execution_contract import (CONTRACT_VERSION, CheckedMotionContract,
                                                           ExecutionContractError, validate_contract_config)
from aspire.sim.cap.world_model.held_geometry import HeldGeometry
from aspire.sim.cap.world_model.sam2_streaming import SAM2Streaming
from aspire.sim.cap.world_model.streaming_replay import StreamingReplay

_tree = ast.parse(rfc.SOURCE.read_text())
_cls = next(n for n in _tree.body if isinstance(n, ast.ClassDef) and n.name == "FrankaLiberoEnv")
_extra = [n for n in _cls.body if isinstance(n, ast.FunctionDef) and n.name == "add_gripper_gate_callback"]
assert len(_extra) == 1
_ns = {}
exec(compile(ast.Module(_extra, type_ignores=[]), str(rfc.SOURCE), "exec"), _ns)


class ContractDriver(rfc.Driver):
    add_gripper_gate_callback = _ns["add_gripper_gate_callback"]

    def __init__(self, **kw):
        super().__init__(**kw)
        self.arm_motion_count = 0  # FrankaLiberoEnv.__init__ value


TOP = np.array([0., 1., 0., 0.])
GOOD = np.array([.45, .05, .3])


def session(tmp_path, monkeypatch, *, contract=True, **driver_kw):
    driver = ContractDriver(**driver_kw)
    api = rfc.api_for(driver)
    monkeypatch.setattr(SAM2Streaming, "from_checkpoint", lambda **kw: rfc.StaticTracker())
    scope = {"segment_sam3_text_prompt": lambda rgb, prompt: [{"mask": rfc.object_mask(), "score": .9}],
             "goto_pose": api.goto_pose, "solve_ik": api.solve_ik, "move_to_joints": api.move_to_joints,
             "open_gripper": api.open_gripper, "close_gripper": api.close_gripper}
    env = SimpleNamespace(cfg=SimpleNamespace(privileged=False), low_level_env=driver,
                          _exec_globals=scope, _apis={"franka": api})
    config = {"mode": "sam2-semantic-world-v2", "sam2": {},
              "objects": [{"identity": "block", "prompt": "public block"}],
              "parts": [{"identity": "block", "tracked": True}]}
    if contract:
        config["execution_contract"] = {"version": CONTRACT_VERSION}
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config))
    return StreamingReplay(env, path, episode_id="ep"), scope, driver, api


def test_legacy_profile_is_unchanged_without_opt_in(tmp_path, monkeypatch):
    replay, scope, driver, api = session(tmp_path, monkeypatch, contract=False)
    assert replay.contract is None and replay.world.held is None
    assert not driver._streaming_progress._callbacks["gripper_gate"]
    for name in ("motion_workspace", "motion_contract_status", "world_bind_held", "world_visible_geometry"):
        assert name not in scope
    report = scope["goto_pose_report"](np.array([.5, .05, .95]), rfc.WRIST)  # clipped, executed as before
    assert report["reached"] is False and report["target_modified"] is True and "contract" not in report
    scope["close_gripper"]()  # legacy: no arrival gate
    assert driver._gripper_fraction == 0.
    with pytest.raises(ValueError, match="opt-in"):
        scope["goto_pose_report"](GOOD, TOP, execute_modified=True)
    replay.close()


def test_contract_config_is_explicit_and_needs_semantic_mode(tmp_path, monkeypatch):
    assert validate_contract_config(None) is None
    for bad in ({"version": "checked-motion-v0"}, {"version": CONTRACT_VERSION, "extra": 1}, "on", True):
        with pytest.raises(ValueError):
            validate_contract_config(bad)
    driver = ContractDriver()
    monkeypatch.setattr(SAM2Streaming, "from_checkpoint", lambda **kw: rfc.StaticTracker())
    env = SimpleNamespace(cfg=SimpleNamespace(privileged=False), low_level_env=driver, _exec_globals={},
                          _apis={"franka": rfc.api_for(driver)})
    path = tmp_path / "v1.json"
    path.write_text(json.dumps({"mode": "sam2-streaming-world-v1", "sam2": {}, "objects": [],
                                "execution_contract": {"version": CONTRACT_VERSION}}))
    with pytest.raises(ValueError, match="semantic"):
        StreamingReplay(env, path, episode_id="ep")


def test_initial_gripper_commands_and_public_workspace(tmp_path, monkeypatch):
    replay, scope, driver, api = session(tmp_path, monkeypatch)
    status = scope["motion_contract_status"]()
    assert status["state"] == "initial" and status["gripper_permitted"] is True
    scope["close_gripper"]()
    scope["open_gripper"]()  # no arm motion since the episode started
    workspace = scope["motion_workspace"]()
    assert workspace["workspace_high"] == [.75, .5, .9] and workspace["workspace_low"] == [-.1, -.5, .005]
    assert workspace["target_reference"] == "robot_base/TCP" and workspace["tcp_offset_hand"] == [0, 0, -.1]
    assert [f["label"] for f in workspace["orientation_fallbacks"]] == ["top-down", "45-tilt", "side-approach"]
    assert workspace["position_tolerance"] == .015 and workspace["angle_tolerance"] == .15
    assert isinstance(replay.world.held, HeldGeometry)
    replay.close()


def test_clipped_target_is_not_executed_and_blocks_dependent_gripper(tmp_path, monkeypatch):
    replay, scope, driver, api = session(tmp_path, monkeypatch)
    ok = scope["goto_pose_report"](GOOD, rfc.WRIST)
    assert ok["reached"] is True and ok["contract"]["gripper_permitted"] is True
    before = driver.arm_motion_count
    target = np.array([.8, .05, .3])  # x beyond the published software clip
    report = scope["goto_pose_report"](target, rfc.WRIST)
    assert report["status"] == "refused_target_modified" and report["executed"] is False
    assert report["reached"] is False and report["target_modified"] is True
    assert report["workspace"]["violated_axes"] == ["x"]
    assert report["workspace"]["clip_delta"][0] == pytest.approx(-.05)
    assert driver.arm_motion_count == before  # nothing moved
    with pytest.raises(ExecutionContractError, match="refused_target_modified"):
        scope["close_gripper"]()
    assert driver._gripper_fraction == 1.  # the refused command never took effect
    with pytest.raises(ExecutionContractError):
        api.close_gripper()  # the driver gate also covers non-global API paths
    scope["open_gripper"]()  # unchanged command (already open) is a no-op and allowed
    # Actual replanning that arrives restores permission.
    replanned = scope["goto_pose_report"](np.array([.7, .05, .3]), rfc.WRIST)
    assert replanned["reached"] is True and replanned["target_modified"] is False
    scope["close_gripper"]()
    assert driver._gripper_fraction == 0.
    counts = replay.journal.counts
    assert counts["gripper_refused"] == 2
    replay.close()


def test_orientation_fallback_is_never_accepted_as_arrival(tmp_path, monkeypatch):
    replay, scope, driver, _ = session(tmp_path, monkeypatch, reject_quaternion=rfc.WRIST)
    report = scope["goto_pose_report"](GOOD, rfc.WRIST)
    assert report["status"] == "refused_target_modified" and report["ik"]["orientation_choice"] == "top-down"
    forced = scope["goto_pose_report"](GOOD, rfc.WRIST, execute_modified=True)
    assert forced["executed"] is True and forced["target_modified"] is True
    assert forced["effective_angle_error"] < 1e-6  # arrived only at the fallback orientation
    assert forced["contract"]["state"] == "target_modified"
    with pytest.raises(ExecutionContractError, match="target_modified"):
        scope["close_gripper"]()
    replay.close()


@pytest.mark.parametrize("kw,state", [(dict(reach_limit=.3), "not_reached"), (dict(gain=.01), "not_reached")])
def test_unreached_or_nonconverged_motion_blocks_gripper(tmp_path, monkeypatch, kw, state):
    replay, scope, driver, _ = session(tmp_path, monkeypatch, **kw)
    report = scope["goto_pose_report"](np.array([.55, .1, .2]), rfc.WRIST)
    assert report["reached"] is False and report["target_modified"] is False
    assert report["contract"]["state"] == state and report["contract"]["gripper_permitted"] is False
    with pytest.raises(ExecutionContractError):
        scope["close_gripper"]()
    assert driver._gripper_fraction == 1.
    replay.close()


def test_failed_approach_leg_stops_before_the_final_leg(tmp_path, monkeypatch):
    replay, scope, driver, _ = session(tmp_path, monkeypatch)
    before = driver.arm_motion_count
    report = scope["goto_pose_report"](np.array([.45, .05, .85]), TOP, z_approach=.1)  # approach z=.95 > .9
    assert report["leg"] == "approach" and report["status"] == "refused_target_modified"
    assert [leg["leg"] for leg in report["legs"]] == ["approach"] and driver.arm_motion_count == before
    assert report["final_requested"] == pytest.approx([.45, .05, .85])
    both = scope["goto_pose_report"](GOOD, TOP, z_approach=.05)
    assert [leg["leg"] for leg in both["legs"]] == ["approach", "final"] and both["reached"] is True
    assert driver.arm_motion_count == before + 2
    replay.close()


def test_unchecked_arm_motion_after_verification_blocks_gripper(tmp_path, monkeypatch):
    replay, scope, driver, api = session(tmp_path, monkeypatch)
    scope["goto_pose_report"](GOOD, rfc.WRIST)
    scope["close_gripper"]()
    scope["open_gripper"]()  # no arm motion since the verified report: still permitted
    api.goto_pose(np.array([.5, 0., .3]), rfc.WRIST)  # direct, unchecked
    status = scope["motion_contract_status"]()
    assert status["state"] == "unchecked_motion" and status["gripper_permitted"] is False
    with pytest.raises(ExecutionContractError, match="unchecked_motion"):
        scope["close_gripper"]()
    scope["goto_pose_report"](np.array([.5, 0., .3]), rfc.WRIST)
    scope["close_gripper"]()
    scope["move_to_joints"](api.solve_ik(GOOD, rfc.WRIST))
    with pytest.raises(ExecutionContractError):
        scope["open_gripper"]()
    replay.close()


def test_in_flight_exception_discards_previous_arrival(tmp_path, monkeypatch):
    replay, scope, driver, _ = session(tmp_path, monkeypatch)
    assert scope["goto_pose_report"](GOOD, rfc.WRIST)["reached"] is True
    def interrupt():
        raise TrackingError("synthetic interruption during the motor trajectory")
    remove = driver.add_motion_gate_callback(interrupt)
    with pytest.raises(TrackingError):
        scope["goto_pose_report"](np.array([.5, 0., .3]), rfc.WRIST)
    remove()
    status = scope["motion_contract_status"]()
    assert status["state"] == "in_flight" and status["gripper_permitted"] is False
    with pytest.raises(ExecutionContractError, match="in_flight"):
        scope["close_gripper"]()
    assert scope["goto_pose_report"](np.array([.5, 0., .3]), rfc.WRIST)["reached"] is True
    scope["close_gripper"]()
    replay.close()


def test_episode_reset_clears_contract_and_held_state(tmp_path, monkeypatch):
    replay, scope, driver, api = session(tmp_path, monkeypatch)
    scope["goto_pose_report"](np.array([.8, .05, .3]), rfc.WRIST)  # refused
    replay.world.held.bindings["block"] = {"valid": True}
    with driver._streaming_progress.reset_scope():
        driver._set_gripper(1.)  # reset settling is not gated
    assert replay.world.held.bindings == {}
    assert scope["motion_contract_status"]()["state"] == "initial"
    scope["reacquire_world"]()
    scope["close_gripper"]()  # new episode, no arm motion yet
    # Verified evidence from before a reset never survives it.
    scope["goto_pose_report"](GOOD, rfc.WRIST)
    with driver._streaming_progress.reset_scope():
        pass
    scope["reacquire_world"]()
    api.goto_pose(np.array([.5, 0., .3]), rfc.WRIST)
    with pytest.raises(ExecutionContractError, match="unchecked_motion"):
        scope["open_gripper"]()
    replay.close()


def test_closing_removes_gripper_gate_and_helpers(tmp_path, monkeypatch):
    replay, scope, driver, _ = session(tmp_path, monkeypatch)
    scope["goto_pose_report"](np.array([.8, .05, .3]), rfc.WRIST)
    replay.close()
    assert not driver._streaming_progress._callbacks["gripper_gate"]
    assert "motion_contract_status" not in scope and "world_held_state" not in scope


def test_contract_state_machine_without_driver():
    count = [0]
    contract = CheckedMotionContract(lambda: count[0])
    assert contract.check_gripper(0., 1.)["state"] == "initial"
    contract.begin("motion-1")
    count[0] += 1
    with pytest.raises(ExecutionContractError):
        contract.check_gripper(0., 1.)
    contract.record({"action_id": "motion-1", "reached": None, "target_modified": None, "status": "unknown"})
    assert contract.permission()["state"] == "unknown"
    contract.record({"action_id": "motion-2", "status": "ik_failed", "reached": False})
    assert contract.permission()["state"] == "ik_failed"
    contract.record({"action_id": "motion-3", "reached": True, "target_modified": True, "status": "reached"})
    assert contract.permission()["state"] == "target_modified"  # a small clip still counts as modified
    contract.record({"action_id": "motion-4", "reached": True, "target_modified": False, "status": "reached"})
    assert contract.check_gripper(0., 1.)["gripper_permitted"] is True
    assert contract.check_gripper(1., 1.) is None  # unchanged command
    assert contract.refusals == 1


def test_replay_held_and_geometry_helpers_report_unknown_without_evidence(tmp_path, monkeypatch):
    replay, scope, driver, api = session(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="robot"):
        scope["world_bind_held"]("block")  # no synchronized public robot pose yet
    scope["goto_pose_report"](GOOD, rfc.WRIST)
    scope["world_refresh"]()
    state = scope["world_bind_held"]("block")
    assert state["status"] == "bound_unverified" and state["reference"] == "robot_base/TCP"
    assert scope["world_held_state"]("block")["valid"] is True
    assert scope["world_release_held"]("block")["status"] == "released"
    geometry = scope["world_visible_geometry"]("block")  # 25 visible points < 30
    assert geometry["status"] == "unknown" and "visible points" in geometry["reason"]
    assert geometry["up_source"] == "robot_base_pose_world" and geometry["coordinate_frame"] == "robot_base"
    np.testing.assert_allclose(geometry["up"], rfc.BASE[:3, :3].T @ [0, 0, 1], atol=1e-9)
    xs = np.arange(0, .2, .005)
    known = scope["geometry_from_points"](np.array([[x, y, .1] for x in xs for y in xs[:10]]))
    assert known["status"] == "known" and known["up"] == geometry["up"]
    with pytest.raises(ValueError, match="part identity"):
        scope["world_visible_geometry"]("rack")
    replay.close()


class FailingSecondUpdate(dict):
    """Policy namespace whose second helper installation fails mid-construction."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.updates = 0

    def update(self, *a, **kw):
        self.updates += 1
        if self.updates == 2:
            raise RuntimeError("synthetic failure after helpers were installed")
        super().update(*a, **kw)


def _failing_env(driver, api, scope):
    return SimpleNamespace(cfg=SimpleNamespace(privileged=False), low_level_env=driver,
                           _exec_globals=scope, _apis={"franka": api})


def _contract_config(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"mode": "sam2-semantic-world-v2", "sam2": {},
                                "objects": [{"identity": "block", "prompt": "public block"}],
                                "parts": [{"identity": "block", "tracked": True}],
                                "execution_contract": {"version": CONTRACT_VERSION}}))
    return path


def _scope(api, cls=dict):
    return cls({"segment_sam3_text_prompt": lambda rgb, prompt: [{"mask": rfc.object_mask(), "score": .9}],
                "goto_pose": api.goto_pose, "solve_ik": api.solve_ik, "move_to_joints": api.move_to_joints,
                "open_gripper": api.open_gripper, "close_gripper": api.close_gripper})


def test_construction_failure_leaves_no_helpers_or_hooks(tmp_path, monkeypatch):
    driver = ContractDriver()
    api = rfc.api_for(driver)
    monkeypatch.setattr(SAM2Streaming, "from_checkpoint", lambda **kw: rfc.StaticTracker())
    scope = _scope(api, FailingSecondUpdate)
    before = set(scope)
    with pytest.raises(RuntimeError, match="synthetic failure"):
        StreamingReplay(_failing_env(driver, api, scope), _contract_config(tmp_path), episode_id="ep")
    assert set(scope) == before  # first-installed helpers (world_snapshot, ...) removed again
    assert not any(driver._streaming_progress._callbacks.values())
    scope["goto_pose"](np.array([.5, 0., .3]), rfc.WRIST)
    scope["close_gripper"]()  # no leftover gate from the failed construction
    assert driver._gripper_fraction == 0.


def test_contract_install_failure_removes_the_gripper_gate(tmp_path, monkeypatch):
    import aspire.sim.cap.world_model.streaming_replay as sr

    def broken(observation):
        raise ValueError("synthetic calibration failure")
    monkeypatch.setattr(sr, "base_pose_world", broken)
    monkeypatch.setattr(SAM2Streaming, "from_checkpoint", lambda **kw: rfc.StaticTracker())
    driver = ContractDriver()
    api = rfc.api_for(driver)
    scope = _scope(api)
    before = set(scope)
    with pytest.raises(ValueError, match="synthetic calibration"):
        StreamingReplay(_failing_env(driver, api, scope), _contract_config(tmp_path), episode_id="ep")
    assert set(scope) == before and not any(driver._streaming_progress._callbacks.values())
