"""Frame/TCP/gripper contract at the actual driver -> World/action boundary.

The production FrankaLiberoEnv observation, motor and gripper methods are
compiled from source and driven by a CPU stand-in of the physical robot state
(base mounting, gripper site, finger joint, camera mounting). The actual
FrankaLiberoApiReduced.solve_ik/goto_pose run against a synthetic IK solver.
No scene asset geometry, object state or evaluator predicate is read.
"""
import ast
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import viser.transforms as vtf
from scipy.spatial.transform import Rotation

from aspire.sim.cap.integrations.franka.libero_reduced import FrankaLiberoApiReduced
from aspire.sim.cap.world_model import robot_frames as rf
from aspire.sim.cap.world_model.World import FrameError, PublicFrame, World
from aspire.sim.cap.world_model.control_progress import ControlProgress
from aspire.sim.cap.world_model.motion_feedback import FrameContractError, motion_report
from aspire.sim.cap.world_model.sam2_streaming import SAM2Streaming
from aspire.sim.cap.world_model.streaming_replay import StreamingReplay

SOURCE = Path(__file__).resolve().parents[1] / "cap/envs/simulators/libero.py"
_tree = ast.parse(SOURCE.read_text())
_cls = next(n for n in _tree.body if isinstance(n, ast.ClassDef) and n.name == "FrankaLiberoEnv")
DRIVER_METHODS = {"get_observation", "get_robot_observation", "move_to_joints_blocking", "_step_once",
                  "_set_gripper", "_publish_control_progress", "get_current_time_s",
                  "add_control_progress_callback", "add_streaming_reset_callback",
                  "add_streaming_close_callback", "add_motion_gate_callback", "_check_control_permitted"}
_selected = [n for n in _cls.body if isinstance(n, ast.FunctionDef) and n.name in DRIVER_METHODS]
assert {n.name for n in _selected} == DRIVER_METHODS
_ns = {"np": np, "vtf": vtf, "Any": object, "get_real_depth_map": lambda sim, depth: depth}
exec(compile(ast.Module(_selected, type_ignores=[]), str(SOURCE), "exec"), _ns)

W, H, FOVY, DEPTH = 40, 32, 45.0, 0.7
PIXEL = (24, 13)  # (u, v) of the synthetic object's visible centre


def se3(R, t):
    T = np.eye(4)
    T[:3, :3], T[:3, 3] = R, t
    return T


def wxyz(R):
    x, y, z, w = Rotation.from_matrix(R).as_quat()
    return np.array([w, x, y, z])


def rot(q_wxyz):
    w, x, y, z = q_wxyz
    return Rotation.from_quat([x, y, z, w]).as_matrix()


# Nonidentity robot mounting (rotation and translation); not a campaign value.
BASE = se3(Rotation.from_euler("xyz", [.05, -.03, .6]).as_matrix(), [-.52, .11, .87])
# Public panda_hand = gripper site @ (Rz(pi/2), -0.107 m along Z).
CALIB = se3(Rotation.from_euler("z", np.pi / 2).as_matrix(), [0, 0, -.107])


def look_at(eye, target):
    """MuJoCo camera orientation (looks along -Z, +Y up) in world."""
    z = eye - target
    z /= np.linalg.norm(z)
    x = np.cross([0, 0, 1.], z)
    x /= np.linalg.norm(x)
    return np.stack([x, np.cross(z, x), z], axis=1)


CAM_POS = (BASE @ [1.0, 0., .6, 1])[:3]
CAM_R = look_at(CAM_POS, (BASE @ [.5, .05, .1, 1])[:3])
WRIST_POS, WRIST_R = (BASE @ [.4, 0., .5, 1])[:3], look_at((BASE @ [.4, 0., .5, 1])[:3], (BASE @ [.4, 0, 0, 1])[:3])


def object_point_base():
    """Back-project PIXEL independently of the driver's pose composition."""
    f = .5 * H / np.tan(FOVY * np.pi / 360)
    u, v = PIXEL
    p_cv = DEPTH * np.array([(u - W / 2) / f, (v - H / 2) / f, 1])
    p_world = CAM_POS + CAM_R @ np.diag([1, -1, -1]) @ p_cv
    return (np.linalg.inv(BASE) @ [*p_world, 1])[:3]


def object_mask():
    m = np.zeros((H, W), dtype=bool)
    u, v = PIXEL
    m[v - 2:v + 3, u - 2:u + 3] = True
    return m


class Driver:
    """Stand-in for the physical robot; all public outputs come from libero.py."""
    privileged = False

    def __init__(self, *, gain=1.0, reach_limit=None, reject_quaternion=None):
        self.gain, self.reach_limit, self.reject = gain, reach_limit, reject_quaternion
        self._render_width, self._render_height = W, H
        self.gripper_metric_length = .04
        self.segmentation_level = "instance"
        self.base_link_idx, self.gripper_link_idx = 0, 1
        self._panda_joint_qpos_addrs = list(range(7))
        self._control_freq = 20
        self._sim_step_count = 0
        self._gripper_fraction = 1.
        self.viser_debug = self._record_frames = False
        self._streaming_progress = ControlProgress()
        self.base_link_wxyz_xyz = np.concatenate([wxyz(BASE[:3, :3]), BASE[:3, 3]])
        self.finger = .04
        q = np.zeros(9)
        q[:3], q[3:6] = [.4, 0, .4], [np.pi, 0, 0]
        cams = {"agentview": (CAM_POS, CAM_R), "robot0_eye_in_hand": (WRIST_POS, WRIST_R)}
        data = SimpleNamespace(qpos=q, xquat=np.zeros((2, 4)), xpos=np.zeros((2, 3)),
                               get_camera_xmat=lambda n: cams[n][1], get_camera_xpos=lambda n: cams[n][0])
        model = SimpleNamespace(camera_name2id={"agentview": 0, "robot0_eye_in_hand": 1}.__getitem__,
                                cam_fovy=np.array([FOVY, 75.]))
        self.handle = SimpleNamespace(env=SimpleNamespace(sim=SimpleNamespace(data=data, model=model)),
                                      step=self._physical_step)
        self.data = data
        self._current_joints = q[:7].copy()
        self._kinematics()
        self._current_obs = self._sensors()

    def hand_base(self):
        q = self.data.qpos
        return se3(Rotation.from_rotvec(q[3:6]).as_matrix(), q[:3])

    def _kinematics(self):
        site = BASE @ self.hand_base() @ np.linalg.inv(CALIB)
        self.data.xquat[1], self.data.xpos[1] = wxyz(site[:3, :3]), site[:3, 3]
        self.data.xquat[0], self.data.xpos[0] = wxyz(BASE[:3, :3]), BASE[:3, 3]
        self.gripper_link_wxyz_xyz = np.concatenate([self.data.xquat[1], self.data.xpos[1]])

    def _sensors(self):
        rgb = np.full((H, W, 3), 90, dtype=np.uint8)
        depth = np.full((H, W, 1), DEPTH)
        return {"robot0_joint_pos": self.data.qpos[:7].copy(),
                "robot0_gripper_qpos": np.array([self.finger, -self.finger]),
                "agentview_image": rgb[::-1], "agentview_depth": depth[::-1]}

    def _physical_step(self, action):
        self.data.qpos[:7] += self.gain * np.asarray(action[:7]) / self._control_freq
        self.finger = .04 * (1 - action[7]) / 2
        self._kinematics()
        return self._sensors(), 0., False, {}

    def ik(self, target_pose_wxyz_xyz, prev_cfg=None):
        """Synthetic IK: joints encode the reachable panda_hand pose in robot_base."""
        q, pos = np.asarray(target_pose_wxyz_xyz[:4]), np.array(target_pose_wxyz_xyz[4:])
        if self.reject is not None and abs(np.dot(q, self.reject)) > .999:
            raise RuntimeError("synthetic IK cannot reach this orientation")
        if self.reach_limit is not None and np.linalg.norm(pos) > self.reach_limit:
            pos *= self.reach_limit / np.linalg.norm(pos)
        return np.array([*pos, *Rotation.from_matrix(rot(q)).as_rotvec(), 0., .04])


for _name in DRIVER_METHODS:
    setattr(Driver, _name, _ns[_name])


def api_for(driver):
    api = FrankaLiberoApiReduced.__new__(FrankaLiberoApiReduced)
    api._env, api.ik_solve_fn, api.cfg = driver, driver.ik, None
    api.camera_name, api.wrist_camera_name = "agentview", "robot0_eye_in_hand"
    return api


class StaticTracker:
    def initialize(self, rgb, masks):
        self.masks = dict(masks)

    def track(self, rgb):
        return dict(self.masks)

    def reset(self):
        self.masks = {}

    def close(self):
        self.closed = True


def session(tmp_path, monkeypatch, **driver_kw):
    driver = Driver(**driver_kw)
    api = api_for(driver)
    monkeypatch.setattr(SAM2Streaming, "from_checkpoint", lambda **kw: StaticTracker())
    scope = {"segment_sam3_text_prompt": lambda rgb, prompt: [{"mask": object_mask(), "score": .9}],
             "goto_pose": api.goto_pose}
    env = SimpleNamespace(cfg=SimpleNamespace(privileged=False), low_level_env=driver,
                          _exec_globals=scope, _apis={"franka": api})
    path = tmp_path / "semantic.json"
    path.write_text(json.dumps({"mode": "sam2-semantic-world-v2", "sam2": {},
        "objects": [{"identity": "block", "prompt": "public block"}],
        "parts": [{"identity": "block", "tracked": True}]}))
    return StreamingReplay(env, path, episode_id="ep"), scope, driver, api


WRIST = wxyz(Rotation.from_euler("xyz", [np.pi, .3, .8]).as_matrix())  # tilted, wrist-rotated


def test_camera_pose_mat_is_declared_robot_base_and_matches_mounting():
    obs = Driver().get_observation()
    for camera, (pos, R) in (("agentview", (CAM_POS, CAM_R)), ("robot0_eye_in_hand", (WRIST_POS, WRIST_R))):
        expected = np.linalg.inv(BASE) @ se3(R @ np.diag([1, -1, -1]), pos)
        np.testing.assert_allclose(obs[camera]["pose_mat"], expected, atol=1e-9)
        assert obs[camera]["pose_frame"] == "robot_base"
    frame = PublicFrame.from_observation(obs, episode_id="ep", camera="agentview", frame_id=0, captured_at=0.)
    assert frame.coordinate_frame == "robot_base"


def test_driver_robot_observation_is_labeled_and_consistent():
    driver = Driver()
    robot = driver.get_robot_observation()
    found, base = rf.check_consistency(robot)
    np.testing.assert_allclose(rf.pose_matrix(base), BASE, atol=1e-9)
    np.testing.assert_allclose(rf.pose_matrix(found["robot_base"][1]), driver.hand_base(), atol=1e-9)
    np.testing.assert_allclose(robot["robot_hand_pose_base"], robot["robot_cartesian_pos"][:7])
    world_hand = rf.pose_matrix(robot["robot_hand_pose_world"])
    np.testing.assert_allclose(world_hand, BASE @ driver.hand_base(), atol=1e-9)
    assert np.linalg.norm(world_hand[:3, 3] - driver.hand_base()[:3, 3]) > .3  # frames truly differ


def test_gripper_opening_is_named_and_index_seven_not_quaternion_z():
    driver = Driver()
    api_for(driver).goto_pose(np.array([.45, .05, .3]), WRIST)
    driver._set_gripper(.5)
    driver._step_once()
    robot = driver.get_robot_observation()
    assert robot["robot_gripper_opening"] == pytest.approx(.5)
    assert rf.gripper_opening(robot) == pytest.approx(.5)
    assert robot["robot_cartesian_pos"][7] == pytest.approx(.5)
    assert robot["robot_cartesian_pos"][6] == pytest.approx(robot["robot_hand_pose_base"][6])
    assert abs(robot["robot_cartesian_pos"][6] - .5) > .05  # the historical index-6 misread
    assert robot["robot_joint_pos"][7] == pytest.approx(.5)
    driver._set_gripper(0.)
    driver._step_once()
    assert rf.gripper_opening(driver.get_robot_observation()) == pytest.approx(0.)


def test_transformed_frame_target_is_reached_and_world_relations_share_robot_base(tmp_path, monkeypatch):
    replay, scope, driver, api = session(tmp_path, monkeypatch)
    assert replay.world.coordinate_frame == "robot_base"
    block = scope["world_query"]("block")
    assert block["coordinate_frame"] == "robot_base"
    np.testing.assert_allclose(block["value"]["visible_center"], object_point_base(), atol=1e-6)
    target = object_point_base() + [0, 0, .08]
    report = scope["goto_pose_report"](target, WRIST)
    assert report["reached"] is True and report["status"] == "reached"
    assert report["coordinate_frame"] == "robot_base" and report["position_reference"] == "robot_base/TCP"
    assert report["target_modified"] is False and report["joint_motion"]["converged"] is True
    assert report["position_error"] < 1e-6 and report["angle_error"] < 1e-6
    assert report["ik"]["position_reference"] == "robot_base/TCP"
    assert report["ik"]["tcp_offset_hand"] == [0., 0., -.1] and report["tcp_offset_hand"] == [0., 0., -.1]
    # TCP offset: measured panda_hand is 0.1 m behind the TCP along the hand Z axis.
    hand = np.asarray(report["actual_hand_position"])
    np.testing.assert_allclose(hand, target + rot(WRIST) @ [0, 0, -.1], atol=1e-6)
    # The pre-repair comparison against world/panda_hand would have been far off.
    world = driver.get_robot_observation()["robot_hand_pose_world"]
    assert np.linalg.norm(world[:3] - rot(world[3:]) @ [0, 0, -.1] - target) > .3
    # Relation inputs: object geometry and robot frame_view are both robot_base.
    scope["world_refresh"]()
    now = driver.get_current_time_s()
    robot = scope["world_query"]("robot")
    assert robot["coordinate_frame"] == "robot_base"
    view = robot["value"]["frame_view"]
    np.testing.assert_allclose(view["tcp_position"], target, atol=1e-6)
    assert view["gripper_opening"] == pytest.approx(1.) and "robot_hand_pose_world" in robot["value"]
    def tcp_above(b, r):
        gap = np.asarray(r["frame_view"]["tcp_position"]) - b["visible_center"]
        return bool(abs(gap[2] - .08) < .01 and np.linalg.norm(gap[:2]) < .01)
    assert replay.world.verify_relation("tcp_above_block", ["block", "robot"], tcp_above, at=now)["value"] is True
    replay.close()


@pytest.mark.parametrize("case", ["clipped", "orientation_fallback", "unreachable", "nonconverged"])
def test_modified_unreachable_or_unfinished_motion_is_not_reached(tmp_path, monkeypatch, case):
    kwargs = {"orientation_fallback": dict(reject_quaternion=WRIST), "unreachable": dict(reach_limit=.3),
              "nonconverged": dict(gain=.01)}.get(case, {})
    replay, scope, driver, _ = session(tmp_path, monkeypatch, **kwargs)
    target = np.array([.5, .05, .95]) if case == "clipped" else np.array([.55, .1, .2])
    report = scope["goto_pose_report"](target, WRIST)
    assert report["reached"] is False and report["status"] == "not_reached"
    if case == "clipped":
        assert report["ik"]["position_clipped"] and report["target_modified"] is True
        assert report["effective_position_error"] < 1e-6  # arrived only at the clipped target
    elif case == "orientation_fallback":
        assert report["ik"]["orientation_choice"] == "top-down" and report["target_modified"] is True
        assert report["effective_angle_error"] < 1e-6 and report["angle_error"] > .15
    elif case == "unreachable":
        assert report["target_modified"] is False and report["joint_motion"]["converged"] is True
        assert report["position_error"] > .015
    else:
        assert report["joint_motion"]["converged"] is False and report["target_modified"] is False
    replay.close()


def reached_robot():
    driver = Driver()
    api = api_for(driver)
    target = np.array([.45, -.05, .25])
    api.goto_pose(target, WRIST)
    return target, driver.get_robot_observation(), api._last_ik_report, driver._last_motion_report


def test_base_pose_can_be_derived_through_public_mounting_calibration():
    target, robot, ik, joints = reached_robot()
    for key in ("robot_hand_pose_base", "robot_hand_pose_base_reference", "robot_cartesian_pos",
                "robot_cartesian_reference"):
        robot.pop(key)
    report = motion_report(target, WRIST, robot, ik=ik, joints=joints)
    assert report["reached"] is True and report["hand_source"].startswith("derived:")


def test_joint_nonconvergence_overrides_in_tolerance_pose():
    target, robot, ik, joints = reached_robot()
    report = motion_report(target, WRIST, robot, ik=ik, joints={**joints, "converged": False})
    assert report["reached"] is False


def test_missing_frame_metadata_is_unknown_not_reached():
    target, robot, ik, joints = reached_robot()
    only_world = {"robot_hand_pose_world": robot["robot_hand_pose_world"]}
    report = motion_report(target, WRIST, only_world, ik=ik, joints=joints)
    assert report["reached"] is None and "robot_base" in report["reason"]
    undeclared = {k: v for k, v in ik.items() if k != "position_reference"}
    assert motion_report(target, WRIST, robot, ik=undeclared, joints=joints)["reached"] is None
    unlabeled = {"robot_cartesian_pos": robot["robot_cartesian_pos"]}  # frame never guessed
    assert motion_report(target, WRIST, unlabeled, ik=ik, joints=joints)["reached"] is None


@pytest.mark.parametrize("tamper,kwargs", [
    (lambda r, ik: r.update(robot_hand_pose_base_reference="world/panda_hand"), {}),
    (lambda r, ik: r.update(robot_cartesian_reference="world/panda_hand"), {}),
    (lambda r, ik: r.update(robot_base_pose_world=r["robot_base_pose_world"] + [.05, 0, 0, 0, 0, 0, 0]), {}),
    (lambda r, ik: r.update(robot_hand_pose_base=r["robot_hand_pose_base"][:6]), {}),
    (lambda r, ik: r.update(robot_gripper_opening=.2), {}),
    (lambda r, ik: ik.update(position_reference="world/TCP"), {"frame": "robot_base"}),
    (lambda r, ik: ik.update(position_reference="robot_base"), {}),
    (lambda r, ik: ik.update(position_reference="camera/TCP"), {}),
    (lambda r, ik: None, {"tcp_offset": (0, 0, -.107)}),
])
def test_malformed_or_contradictory_frame_metadata_fails_clearly(tamper, kwargs):
    target, robot, ik, joints = reached_robot()
    ik = dict(ik)
    tamper(robot, ik)
    with pytest.raises(FrameContractError):
        motion_report(target, WRIST, robot, ik=ik, joints=joints, **kwargs)


def test_world_requires_declared_camera_frame_and_valid_robot_metadata():
    obs = Driver().get_observation()
    make = lambda i, **kw: PublicFrame.from_observation(obs, episode_id="ep", camera="agentview",
                                                        frame_id=i, captured_at=i / 10, **kw)
    world = World(StaticTracker(), episode_id="ep", coordinate_frame="robot_base", tcp_offset=(0, 0, -.1))
    undeclared = make(0)
    undeclared = PublicFrame(**{**undeclared.__dict__, "coordinate_frame": None})
    with pytest.raises(FrameError, match="declared coordinate frame"):
        world.seed(undeclared, {"block": object_mask()})
    with pytest.raises(FrameError, match="declared coordinate frame"):
        world.seed(PublicFrame(**{**make(0).__dict__, "coordinate_frame": "world"}), {"block": object_mask()})
    with pytest.raises(FrameError, match="unsupported"):
        PublicFrame(**{**make(0).__dict__, "coordinate_frame": "camera"})
    world.seed(make(1), {"block": object_mask()})
    robot = Driver().get_robot_observation()
    bad = {**robot, "robot_base_pose_world": robot["robot_base_pose_world"] + [0, .1, 0, 0, 0, 0, 0]}
    with pytest.raises(FrameError, match="frame contract"):
        world.update_robot(bad, frame_id=1, captured_at=.1)
    assert world.query("robot", at=.1)["status"] == "unknown"
    world.update_robot(robot, frame_id=1, captured_at=.1)
    view = world.query("robot", at=.1)["value"]["frame_view"]
    assert view["status"] == "known" and view["frame"] == "robot_base"
    # Legacy callers without a declared frame keep working but still reject contradictions.
    legacy = World(StaticTracker(), episode_id="legacy")
    legacy.update_robot({"gripper": 0}, frame_id=0, captured_at=0)
    with pytest.raises(FrameError):
        legacy.update_robot(bad, frame_id=1, captured_at=.1)


def test_legacy_gripper_layouts_are_not_guessed():
    pose = [.4, 0, .3, 0, 1, 0, 0]
    assert rf.gripper_opening({"robot_cartesian_pos": [*pose, .7],
                               "robot_cartesian_reference": "robot_base/panda_hand"}) == pytest.approx(.7)
    assert rf.gripper_opening({"robot_cartesian_pos": pose}) is None
    assert rf.gripper_opening({"robot_cartesian_pos": [*pose, .7]}) is None
