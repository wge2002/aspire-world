"""CPU checks for real mask geometry and fail-closed grasp orchestration.

Load the actual methods without starting Isaac Sim. These checks do not stand
in for the real nonprivileged seed-1 replay and its task/video review.
"""
import ast
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace
import time

import numpy as np
import pytest

SIM = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "grasp_checks", SIM / "cap/integrations/r1pro/grasp_checks.py")
checks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checks)


def load_api_methods():
    tree = ast.parse((SIM / "cap/integrations/r1pro/control.py").read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "R1ProControlApi")
    methods = [n for n in cls.body if isinstance(n, ast.FunctionDef)
               and n.name in {"_move_grasp_stage", "grasp_object", "detect_object_sam3",
                              "find_object_torso_rotate"}]
    scope = {"np": np, "json": json, "time": time, "eef_pose_error": checks.eef_pose_error,
             "mask_view_quality": checks.mask_view_quality,
             "obs_get_rgb_depth": lambda obs: (obs, None),
             "torch": SimpleNamespace(tensor=np.asarray)}
    exec(compile(ast.Module(body=methods, type_ignores=[]), "control.py", "exec"), scope)
    return type("ActualGraspMethods", (), {n.name: scope[n.name] for n in methods})


Api = load_api_methods()
POSE = (np.array([0.4, 0.1, 0.2]), np.array([1., 0., 0., 0.]))


def test_real_failed_mask_is_rejected():
    p = Path(os.environ.get("ASPIRE_R24_MASK", str(SIM / "docs/engineering/behavior-bringup-20261022/acceptance-20261023-0725/repair-24-joint-servo/perception/007-graspnet-input-segmap.npy")))
    if not p.exists():
        pytest.skip("Optional preserved R24 evidence is not part of the source checkout")
    quality = checks.mask_view_quality(np.load(p, allow_pickle=False))
    assert quality["pixels"] == 364
    assert quality["bbox"] == [271, 486, 286, 511]
    assert not quality["complete"] and not quality["centered"]


def test_complete_mask_must_also_be_centrally_visible():
    mask = np.zeros((100, 100), dtype=bool)
    mask[45:55, 45:55] = True
    assert checks.mask_view_quality(mask)["centered"]
    mask[:] = False
    mask[94:98, 45:55] = True
    quality = checks.mask_view_quality(mask)
    assert quality["complete"] and not quality["centered"]
    assert not checks.mask_view_quality(np.zeros((100, 100)))["complete"]


@pytest.mark.parametrize("edge", ["top", "bottom", "left", "right"])
def test_all_image_edges_reject_truncation(edge):
    mask = np.zeros((10, 10), dtype=bool)
    mask[3:7, 3:7] = True
    index = {"top": (0, 4), "bottom": (9, 4), "left": (4, 0), "right": (4, 9)}[edge]
    mask[index] = True
    assert not checks.mask_view_quality(mask)["complete"]


def test_quaternion_sign_and_invalid_pose():
    assert checks.eef_pose_error(POSE, (POSE[0], -POSE[1])) == (0., 0.)
    assert np.isinf(checks.eef_pose_error(POSE, (POSE[0], np.zeros(4)))).all()


def test_detector_accepts_partial_for_search_but_not_for_grasp_view():
    api = Api()
    api.debug = False
    api.sam3_score_threshold = 0.1
    pixels = np.zeros((100, 100, 3), dtype=np.uint8)
    rgb = SimpleNamespace(cpu=lambda: SimpleNamespace(numpy=lambda: pixels))
    api._env = SimpleNamespace(get_observation=lambda: rgb)
    mask = np.zeros((100, 100), dtype=bool)
    mask[95:, 40:60] = True
    api.sam3_seg_fn = lambda *a, **kw: [{"score": 0.3, "mask": mask}]
    assert api.detect_object_sam3("can")
    assert not api.detect_object_sam3("can", require_full_view=True)
    assert api._last_target_view_quality["center_fraction"][1] > 0.9
    mask[:] = False
    mask[40:60, 40:60] = True
    assert api.detect_object_sam3("can", require_full_view=True)


@pytest.mark.parametrize("center,first_sign", [([0.54, 0.97], 1), ([0.5, 0.03], -1), (None, -1)])
def test_torso_search_uses_observed_vertical_error(center, first_sign):
    api = Api()
    moves = []
    detections = iter([False, True])
    api._last_target_view_quality = {"center_fraction": center} if center else None
    api.detect_object_sam3 = lambda *a, **kw: next(detections)
    joint = SimpleNamespace(get_state=lambda: (np.array([0.]),))
    api._env = SimpleNamespace(
        controller=SimpleNamespace(robot=SimpleNamespace(joints={"torso_joint1": joint, "torso_joint2": joint})),
        update_torso=lambda name, angle, *a: moves.append((name, float(angle))))
    assert api.find_object_torso_rotate("can", require_full_view=True)
    assert [n for n, _ in moves] == ["torso_joint1", "torso_joint2"]
    assert np.allclose([x for _, x in moves], [first_sign*0.3, -first_sign*0.5])


def test_torso_search_keeps_opposite_sweep_as_fallback():
    api = Api()
    moves = []
    detections = iter([False]*6 + [True])
    api._last_target_view_quality = {"center_fraction": [0.5, 0.97]}
    api.detect_object_sam3 = lambda *a, **kw: next(detections)
    joint = SimpleNamespace(get_state=lambda: (np.array([0.]),))
    api._env = SimpleNamespace(
        controller=SimpleNamespace(robot=SimpleNamespace(joints={"torso_joint1": joint, "torso_joint2": joint})),
        update_torso=lambda name, angle, *a: moves.append((name, float(angle))))
    assert api.find_object_torso_rotate("can", require_full_view=True)
    assert len(moves) == 14
    assert np.allclose([x for _, x in moves[-4:]], [0., 0., -0.3, 0.5])


def test_torso_search_does_not_move_when_current_view_is_acceptable():
    api = Api()
    api.detect_object_sam3 = lambda *a, **kw: True
    assert api.find_object_torso_rotate("can", require_full_view=True)


def test_complete_view_does_not_relax_sam_threshold():
    api = Api()
    api.debug = False
    api.sam3_score_threshold = 0.1
    pixels = np.zeros((100, 100, 3), dtype=np.uint8)
    rgb = SimpleNamespace(cpu=lambda: SimpleNamespace(numpy=lambda: pixels))
    api._env = SimpleNamespace(get_observation=lambda: rgb)
    mask = np.zeros((100, 100), dtype=bool)
    mask[40:60, 40:60] = True
    api.sam3_seg_fn = lambda *a, **kw: [{"score": 0.05, "mask": mask}]
    api._last_target_view_quality = {"center_fraction": [0.5, 0.99]}
    assert not api.detect_object_sam3("can", require_full_view=True)
    assert api._last_target_view_quality is None


@pytest.mark.parametrize("stage_results,held,expected", [
    ([False], True, ["open", "pregrasp"]),
    ([True, False], True, ["open", "pregrasp", "grasp"]),
    ([True, True], False, ["open", "pregrasp", "grasp", "close", "lift", "check"]),
    ([True, True], True, ["open", "pregrasp", "grasp", "close", "lift", "check"]),
])
def test_closure_requires_both_stages(stage_results, held, expected):
    api = Api()
    events = []
    results = iter(stage_results)
    api.open_gripper = lambda **kw: events.append("open")
    api.close_gripper = lambda **kw: events.append("close")
    api.lift_arm = lambda **kw: events.append("lift")
    api.check_object_in_hand = lambda **kw: (events.append("check") or held)
    api._move_grasp_stage = lambda pose, stage, arm: (events.append(stage) or next(results))
    outcome = api.grasp_object(POSE, POSE, "visible can")
    assert events == expected
    assert outcome == (all(stage_results) and held)


@pytest.mark.parametrize("planner_ok,delta,quat,expected", [
    (True, 0., POSE[1], True),
    (False, 0., POSE[1], False),
    (True, 0.031, POSE[1], False),
    (True, 0., np.array([0., 0., 0., 1.]), False),
])
def test_measured_pose_can_veto_planner_success(planner_ok, delta, quat, expected):
    api = Api()
    api.move_hand = lambda *a, **kw: planner_ok
    api.get_current_eef_pose = lambda **kw: (POSE[0] + [delta, 0., 0.], quat)
    assert api._move_grasp_stage(POSE, "pregrasp", 0) == expected


def test_timeout_is_not_swallowed_or_followed_by_closure():
    api = Api()
    api.open_gripper = lambda **kw: None
    def timed_out(*a, **kw):
        raise TimeoutError("original trial deadline")
    api.move_hand = timed_out
    api.close_gripper = lambda **kw: pytest.fail("must not close after timeout")
    with pytest.raises(TimeoutError):
        api.grasp_object(POSE, POSE, "can")
