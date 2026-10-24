"""CPU regressions using public synthetic RGB-D and the actual motor-loop body."""
import ast
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from aspire.sim.cap.world_model.World import Cadence, FrameError, PublicFrame, TrackingError, World
from aspire.sim.cap.world_model.control_progress import ControlProgress, MotionInterrupted
from aspire.sim.cap.world_model.streaming_bridge import StreamingWorldBridge
from aspire.sim.cap.world_model.streaming_replay import StreamingReplay
from aspire.sim.cap.world_model.sam2_streaming import SAM2Streaming


def mask(shift=0):
    result = np.zeros((12, 12), dtype=bool)
    result[2:6, 1 + shift:5 + shift] = True
    return result


def frame(index=0, at=0.0, **kw):
    rgb = np.zeros((12, 12, 3), dtype=np.uint8)
    rgb[0, 0, 0] = index
    args = dict(episode_id="test", camera="agentview", frame_id=index, captured_at=at,
                rgb=rgb, depth=np.ones((12, 12)), intrinsics=np.diag([10., 10., 1.]),
                camera_to_world=np.eye(4))
    args.update(kw)
    return PublicFrame(**args)


class Tracker:
    def __init__(self):
        self.ids = []
        self.frames = []
        self.result = None
        self.failure = False
        self.resets = 0
        self.closed = False

    def initialize(self, rgb, masks):
        self.ids = list(masks)

    def track(self, rgb):
        if self.failure:
            raise RuntimeError("test backend unavailable")
        self.frames.append(int(rgb[0, 0, 0]))
        if self.result is not None:
            return self.result
        return {identity: mask(min(int(rgb[0, 0, 0]), 7)) for identity in self.ids}

    def reset(self):
        self.ids = []
        self.resets += 1

    def close(self):
        self.reset()
        self.closed = True


def seeded(**kw):
    tracker = Tracker()
    world = World(tracker, episode_id="test", **kw)
    world.seed(frame(), {"bowl": mask()})
    return world, tracker


def test_tracking_updates_visible_geometry_in_calibrated_world_frame():
    T = np.array([[0., -1, 0, 1], [1, 0, 0, 2], [0, 0, 1, 3], [0, 0, 0, 1]])
    world = World(Tracker(), episode_id="test")
    world.seed(frame(camera_to_world=T), {"bowl": mask()})
    np.testing.assert_allclose(world.query("bowl", at=0)["value"]["visible_center"], [.65, 2.25, 4])
    world.observe_frame(frame(1, .1, camera_to_world=T))
    np.testing.assert_allclose(world.query("bowl", at=.1)["value"]["visible_center"], [.65, 2.35, 4])
    result = world.query("bowl", at=.1)
    result["value"]["visible_center"][0] = 1000
    assert world.query("bowl", at=.1)["value"]["visible_center"][0] != 1000


@pytest.mark.parametrize("kwargs", [dict(frame_id=0), dict(captured_at=.05),
    dict(camera="wrist"), dict(episode_id="other"), dict(clock="monotonic")])
def test_out_of_order_or_mismatched_frames_cannot_overwrite(kwargs):
    world, _ = seeded()
    world.observe_frame(frame(1, .1))
    before = world.snapshot(at=.1)
    with pytest.raises(FrameError):
        world.observe_frame(frame(2, .2, **kwargs))
    assert world.snapshot(at=.1) == before


def test_frame_is_detached_and_calibration_is_validated():
    image = np.zeros((12, 12, 3), dtype=np.uint8)
    f = frame(rgb=image)
    image[:] = 100
    assert not f.rgb.any() and not f.rgb.flags.writeable
    with pytest.raises(FrameError):
        frame(depth=np.ones((4, 4)))
    with pytest.raises(FrameError):
        frame(intrinsics=np.zeros((3, 3)))
    with pytest.raises(FrameError):
        frame(camera_to_world=np.zeros((4, 4)))


@pytest.mark.parametrize("lost", [None, np.zeros((12, 12), dtype=bool)])
def test_lost_track_replaces_previous_geometry_with_unknown(lost):
    world, tracker = seeded()
    tracker.result = {} if lost is None else {"bowl": lost}
    world.observe_frame(frame(1, .1))
    assert world.query("bowl", at=.1)["status"] == "unknown"
    assert world.query("bowl", at=.1)["value"] is None


def test_invalid_depth_and_expired_evidence_are_not_success():
    world, _ = seeded()
    assert world.query("bowl", at=.7)["status"] == "unknown"
    with pytest.raises(ValueError):
        world.query("bowl", at=-1)
    world.observe_frame(frame(1, .1, depth=np.full((12, 12), np.nan)))
    assert world.query("bowl", at=.1)["value"] is None
    goal = world.verify_relation("held", ["bowl"], lambda b: True, at=.1)
    assert goal["status"] == "unknown"


def test_failure_invalidates_and_requires_explicit_reacquisition():
    world, tracker = seeded()
    tracker.failure = True
    with pytest.raises(TrackingError):
        world.observe_frame(frame(1, .1))
    assert world.query("bowl", at=.1)["status"] == "unknown"
    with pytest.raises(TrackingError):
        world.observe_frame(frame(2, .2))
    tracker.failure = False
    world.seed(frame(2, .2), {"bowl": mask()})
    assert world.query("bowl", at=.2)["status"] == "known"


def test_unknown_tracker_identity_fails_without_switching_objects():
    world, tracker = seeded()
    tracker.result = {"other bowl": mask()}
    with pytest.raises(TrackingError):
        world.observe_frame(frame(1, .1))
    assert world.query("bowl", at=.1)["status"] == "unknown"


def test_predictions_never_become_measured_geometry():
    world, _ = seeded()
    world.set_prediction("bowl", {"visible_center": [9, 9, 9]}, at=0)
    assert world.query("bowl", at=0)["value"]["visible_center"] != [9, 9, 9]
    guess = world.query("bowl", at=0, layer="predicted")
    assert guess["value"]["visible_center"] == [9, 9, 9] and guess["evidence_ids"] == []
    json.dumps(world.snapshot(at=0), allow_nan=False)


def test_overlapping_masks_do_not_establish_two_object_geometries():
    world = World(Tracker(), episode_id="test")
    world.seed(frame(), {"bowl": mask(), "plate": mask()})
    assert world.query("bowl", at=0)["status"] == "unknown"
    assert world.query("plate", at=0)["status"] == "unknown"


def test_old_episode_frame_cannot_enter_reset_world():
    world, _ = seeded()
    old = frame(99, 10.)
    world.reset()
    with pytest.raises(FrameError):
        world.seed(old, {"bowl": mask()})


def test_duplicate_robot_update_and_invalid_predicate_are_rejected():
    world, _ = seeded()
    world.update_robot({"gripper": 0}, frame_id=0, captured_at=0)
    with pytest.raises(FrameError):
        world.update_robot({"gripper": 1}, frame_id=0, captured_at=0)
    with pytest.raises(TypeError):
        world.verify_relation("held", ["bowl"], lambda b: 1, at=0)


def test_relation_requires_post_motion_synchronized_current_dependencies():
    world, _ = seeded()
    world.update_robot({"gripper": 0.0}, frame_id=0, captured_at=0)
    answer = world.verify_relation("held", ["bowl", "robot"], lambda b, r: False, at=0)
    assert answer["status"] == "known" and answer["value"] is False
    world.note_motion(.1)
    assert world.query("held", at=.1, layer="relation")["status"] == "unknown"
    assert world.verify_relation("held", ["bowl", "robot"], lambda b, r: True, at=.1)["status"] == "unknown"
    world.observe_frame(frame(1, .1))
    world.update_robot({"gripper": 0.0}, frame_id=1, captured_at=.1)
    assert world.verify_relation("held", ["bowl", "robot"], lambda b, r: True, at=.1)["value"] is True
    world.observe_frame(frame(2, .2))
    assert world.query("held", at=.2, layer="relation")["status"] == "unknown"
    assert world.verify_relation("held", ["bowl", "robot"], lambda b, r: True, at=.2)["status"] == "unknown"


class CameraPredictor:
    def __init__(self):
        self.frame_idx = 17
        self.calls = []
        self.ids = []

    def load_first_frame(self, rgb):
        self.calls.append(("load", self.frame_idx))
        self.ids = []

    def add_new_mask(self, frame_idx, obj_id, mask):
        self.calls.append(("seed", frame_idx, obj_id))
        self.ids.append(obj_id)

    def track(self, rgb):
        self.frame_idx += 1
        logits = np.where(mask(1), 1., -1.)[None, None]
        return self.ids, logits

    def reset_state(self):
        self.calls.append(("reset", self.frame_idx))  # matches real backend's counter bug


def test_adapter_uses_temporal_api_and_resets_frame_counter():
    predictor = CameraPredictor()
    tracker = SAM2Streaming(predictor)
    tracker.initialize(frame().rgb, {"bowl": mask()})
    assert predictor.calls[:2] == [("load", 0), ("seed", 0, 1)]
    assert tracker.track(frame(1).rgb)["bowl"].sum() == 16
    tracker.reset()
    assert predictor.frame_idx == 0
    tracker.initialize(frame().rgb, {"bowl": mask()})
    tracker.close()
    with pytest.raises(RuntimeError):
        tracker.track(frame().rgb)


def test_wrong_installed_sam2_package_is_rejected_before_loading_weights(tmp_path, monkeypatch):
    weights = tmp_path / "test.pt"
    weights.write_bytes(b"test checkpoint, never loaded")
    import importlib.util
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: SimpleNamespace(origin="/other/sam2/__init__.py"))
    with pytest.raises(ImportError, match="outside"):
        SAM2Streaming.from_checkpoint(source_root=tmp_path, checkpoint=weights, config_file="test")


# Compile the production motor methods without importing MuJoCo, Viser or GPUs.
# Their handle is a stand-in; no generated program receives simulator state.
SOURCE = Path(__file__).resolve().parents[1] / "cap/envs/simulators/libero.py"
tree = ast.parse(SOURCE.read_text())
methods = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "FrankaLiberoEnv")
selected = [n for n in methods.body if isinstance(n, ast.FunctionDef) and n.name in {
    "move_to_joints_blocking", "_step_once", "_set_gripper", "_publish_control_progress",
    "add_control_progress_callback", "add_streaming_reset_callback", "add_streaming_close_callback",
    "add_motion_gate_callback", "_check_control_permitted"}]
namespace = {"np": np}
exec(compile(ast.Module(selected, type_ignores=[]), str(SOURCE), "exec"), namespace)


class MotorEnv:
    def __init__(self):
        self._streaming_progress = ControlProgress()
        self._sim_step_count = 0
        self._current_joints = np.zeros(7)
        self._gripper_fraction = 1.
        self._control_freq = 20
        self._panda_joint_qpos_addrs = list(range(7))
        self.gripper_link_idx = 0
        self.viser_debug = self._record_frames = False
        data = SimpleNamespace(qpos=np.zeros(7), xquat=np.array([[1., 0, 0, 0]]), xpos=np.zeros((1, 3)))
        self.handle = SimpleNamespace(env=SimpleNamespace(sim=SimpleNamespace(data=data)), step=self._step)
        self.reads = 0

    def _step(self, action):
        return {}, 0., False, {}

    def get_current_time_s(self):
        return self._sim_step_count / 20

    def get_robot_observation(self):
        return {"robot_joint_pos": [float(self._sim_step_count)] * 7,
                "robot_cartesian_pos": [0., 0., 1., 1., 0., 0., 0., self._gripper_fraction]}

    def get_observation(self):
        self.reads += 1
        f = frame(min(self._sim_step_count, 7), self.get_current_time_s())
        # The production driver labels pose_mat as camera -> robot_base.
        return {"agentview": {"images": {"rgb": f.rgb, "depth": f.depth},
                              "intrinsics": f.intrinsics, "pose_mat": f.camera_to_world,
                              "pose_frame": "robot_base"}}


for name in (n.name for n in selected):
    setattr(MotorEnv, name, namespace[name])


def test_real_blocking_motor_loop_updates_world_at_multiple_cadences():
    env, tracker = MotorEnv(), Tracker()
    world = World(tracker, episode_id="test", cadence=Cadence(object_period=.1, relation_period=.2))
    seen = []
    verified = []
    def relations(w, at):
        verified.append(at)
        assert w.verify_relation("visible", ["bowl", "robot"], lambda b, r: True, at=at)["value"] is True
    with StreamingWorldBridge(env, world, on_checkpoint=lambda s: seen.append(s), verify_relations=relations) as bridge:
        bridge.seed(bridge.capture(), {"bowl": mask()})
        env.move_to_joints_blocking(np.ones(7), max_steps=6)
        assert len(seen) == 6 and seen[-1]["robot"]["captured_at"] == .3
        assert tracker.frames == [1, 3, 5]
        assert verified == [.05, .25]
        assert env.reads == 4  # initial frame plus 3 tracking frames
    assert not env._streaming_progress.active and tracker.closed


def test_policy_can_interrupt_before_next_motor_step_and_gripper_also_publishes():
    env, tracker = MotorEnv(), Tracker()
    world = World(tracker, episode_id="test", cadence=Cadence(object_period=0))
    with StreamingWorldBridge(env, world, on_checkpoint=lambda s: s["robot"]["frame_id"] < 3) as bridge:
        bridge.seed(bridge.capture(), {"bowl": mask()})
        with pytest.raises(MotionInterrupted):
            env.move_to_joints_blocking(np.ones(7), max_steps=10)
        assert env._sim_step_count == 3 and tracker.frames == [1, 2, 3]
        bridge.on_checkpoint = None
        env._step_once()


def test_absent_hook_keeps_legacy_motion_and_does_not_acquire_camera_frames():
    env = MotorEnv()
    env.move_to_joints_blocking(np.ones(7), max_steps=5)
    env._step_once()
    assert env._sim_step_count == 6 and env.reads == 0


def test_reset_clears_world_and_suspends_settling_checkpoints():
    env, tracker = MotorEnv(), Tracker()
    world = World(tracker, episode_id="test")
    with StreamingWorldBridge(env, world) as bridge:
        bridge.seed(bridge.capture(), {"bowl": mask()})
        env._step_once()
        with env._streaming_progress.reset_scope():
            env._sim_step_count = 0
            env._step_once()
        assert world.query("bowl", at=.05)["status"] == "unknown"
        with pytest.raises(TrackingError):
            env._step_once()
        bridge.seed(bridge.capture(), {"bowl": mask()})
        env._step_once()
        assert world.query("bowl", at=env.get_current_time_s())["status"] == "known"


def replay_config(tmp_path):
    p = tmp_path / "streaming.json"
    p.write_text(json.dumps({"mode": "sam2-streaming-world-v1", "sam2": {},
        "objects": [{"identity": "bowl", "prompt": "bowl"}]}))
    return p


def test_saved_policy_receives_helpers_and_cleanup_restores_namespace(tmp_path, monkeypatch):
    tracker = Tracker()
    monkeypatch.setattr(SAM2Streaming, "from_checkpoint", lambda **kw: tracker)
    motor = MotorEnv()
    motor.privileged = False
    scope = {"segment_sam3_text_prompt": lambda rgb, prompt: [{"mask": mask(), "score": .9}]}
    env = SimpleNamespace(cfg=SimpleNamespace(privileged=False), low_level_env=motor, _exec_globals=scope)
    with StreamingReplay(env, replay_config(tmp_path), episode_id="test") as session:
        scope["set_world_checkpoint"](lambda s: True)
        motor.move_to_joints_blocking(np.ones(7), max_steps=3)
        assert scope["world_query"]("bowl")["status"] == "known"
        scope["request_world_verification"]()
        motor._step_once()
    assert list(scope) == ["segment_sam3_text_prompt"]
    assert session.final_snapshot["observed"]["bowl"]["status"] == "known"
    assert tracker.closed and not motor._streaming_progress.active


def test_failed_reacquisition_withdraws_old_geometry_and_stops_future_motion(tmp_path, monkeypatch):
    monkeypatch.setattr(SAM2Streaming, "from_checkpoint", lambda **kw: Tracker())
    motor = MotorEnv()
    motor.privileged = False
    scope = {"segment_sam3_text_prompt": lambda rgb, prompt: [{"mask": mask(), "score": .9}]}
    env = SimpleNamespace(cfg=SimpleNamespace(privileged=False), low_level_env=motor, _exec_globals=scope)
    with StreamingReplay(env, replay_config(tmp_path), episode_id="test"):
        scope["segment_sam3_text_prompt"] = lambda *a: []
        with pytest.raises(ValueError, match="ambiguous"):
            scope["reacquire_world"]()
        assert scope["world_query"]("bowl")["status"] == "unknown"
        with pytest.raises(TrackingError):
            motor._step_once()


def test_privileged_environment_cannot_enable_streaming_profile(tmp_path):
    env = SimpleNamespace(cfg=SimpleNamespace(privileged=True), low_level_env=MotorEnv())
    with pytest.raises(ValueError, match="nonprivileged"):
        StreamingReplay(env, replay_config(tmp_path), episode_id="test")


def test_replay_profile_dispatch_does_not_silently_change_frozen_conditions():
    source = SOURCE.parents[3] / "scripts/libero/replay_trial.py"
    parsed = ast.parse(source.read_text())
    main = next(n for n in parsed.body if isinstance(n, ast.FunctionDef) and n.name == "main")
    ns = {"ReplayTrialArgs": object, "_run_replay": lambda args: "legacy", "json": json, "Path": Path}
    exec(compile(ast.Module([main], type_ignores=[]), str(source), "exec"), ns)
    args = SimpleNamespace(streaming_world_config=None, world_model_config=None, ordinary_budget_config=None)
    assert ns["main"](args) == "legacy"
    args.streaming_world_config = "config.json"
    args.replay_code, args.interactive = "policy.py", False
    args.world_model_config = "frozen-world.json"
    with pytest.raises(ValueError, match="separate profile"):
        ns["main"](args)
    args.world_model_config = None
    args.replay_code = None
    with pytest.raises(ValueError, match="saved policy"):
        ns["main"](args)
