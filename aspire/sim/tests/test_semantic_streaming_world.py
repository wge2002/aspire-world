"""Semantic revisions and execution attribution using detached public fixtures."""
import copy
import json

import numpy as np
import pytest

from aspire.sim.cap.world_model.World import Cadence, FrameError, TrackingError
from aspire.sim.cap.world_model.semantic_world import SemanticWorld, choose_seed, validate_parts
from aspire.sim.cap.world_model.motion_feedback import motion_report, rotation_wxyz
from test_streaming_world import Tracker, frame, mask
from test_streaming_world import MotorEnv
from types import SimpleNamespace
from aspire.sim.cap.world_model.streaming_replay import StreamingReplay
from aspire.sim.cap.world_model.sam2_streaming import SAM2Streaming
from aspire.sim.cap.world_model.control_progress import MotionInterrupted


def start(**kwargs):
    tracker = Tracker()
    world = SemanticWorld(tracker, episode_id="test", max_identity_jump=kwargs.pop("max_identity_jump", 1), **kwargs)
    parts = [{"identity": "assembly", "tracked": False, "semantic": "candidate assembly"},
             {"identity": "component", "parent": "assembly", "tracked": True,
              "semantic": "candidate movable region", "hypotheses": {"sliding": .8, "unknown": .2}}]
    world.revise_structure(frame(), {"component": mask()}, parts, reason="public initial image")
    return world, tracker, parts


def evidence(world):
    return world.query("component", at=world._object_at)["evidence_ids"]


def test_parts_and_kinematics_are_hypotheses_never_geometry():
    world, _, _ = start()
    assert world.query("component", at=0, layer="hypothesis")["status"] == "hypothesis"
    assert world.query("assembly", at=0)["status"] == "unknown"
    result = world.snapshot(at=0)
    result["semantic"]["parts"]["component"]["semantic"] = "edited"
    assert world.parts["component"]["semantic"] == "candidate movable region"
    json.dumps(result, allow_nan=False)


def test_split_reseeds_tracker_preserving_lineage_and_other_ids():
    world, tracker, parts = start()
    changed = [parts[0], {"identity": "part-a", "parent": "assembly"},
               {"identity": "part-b", "parent": "assembly"}]
    world.revise_structure(frame(1, .1), {"part-a": mask(), "part-b": mask(5)}, changed,
        reason="two distinct regions in public image", evidence_ids=evidence(world),
        lineage={"component": ["part-a", "part-b"]})
    assert tracker.ids == ["part-a", "part-b"] and tracker.resets == 2
    assert world.structure_history[-1]["lineage"] == {"component": ["part-a", "part-b"]}
    assert world.query("component", at=.1)["status"] == "unknown"
    with pytest.raises(ValueError, match="retired"):
        world.revise_structure(frame(2, .2), {"component": mask()}, parts,
            reason="cannot reuse a retired identity", evidence_ids=list(world._evidence))


@pytest.mark.parametrize("changes", [
    [{"identity": "a", "parent": "missing"}],
    [{"identity": "a", "parent": "b"}, {"identity": "b", "parent": "a", "tracked": False}],
    [{"identity": "a", "tracked": False}],
    [{"identity": "a", "hypotheses": {"sliding": float("nan")}}],
])
def test_malformed_graph_cannot_mutate_tracker_or_state(changes):
    world, tracker, _ = start()
    before, resets = world.snapshot(at=0), tracker.resets
    with pytest.raises(ValueError):
        world.revise_structure(frame(1, .1), {"a": mask()}, changes,
            reason="invalid proposal", evidence_ids=evidence(world))
    assert world.snapshot(at=0) == before and tracker.resets == resets


def test_unregistered_or_predicted_evidence_cannot_authorize_revision():
    world, _, parts = start()
    with pytest.raises(ValueError, match="public visual"):
        world.revise_structure(frame(1, .1), {"component": mask()}, parts,
            reason="unsupported semantic explanation", evidence_ids=["invented"])


def test_backend_failure_keeps_graph_but_withdraws_geometry():
    world, tracker, parts = start()
    before = copy.deepcopy(world.parts)
    tracker.initialize = lambda *_: (_ for _ in ()).throw(RuntimeError("test backend fault"))
    with pytest.raises(TrackingError):
        world.revise_structure(frame(1, .1), {"component": mask()}, parts,
            reason="public reassociation", evidence_ids=evidence(world))
    assert world.parts == before and world.structure_revision == 1
    assert world.query("component", at=.1)["status"] == "unknown" and not world._seeded


def test_parent_mask_cannot_double_count_its_child_motion():
    world = SemanticWorld(Tracker(), episode_id="test")
    parent, child = np.ones((12, 12), dtype=bool), mask()
    world.revise_structure(frame(), {"assembly": parent, "component": child},
        [{"identity": "assembly"}, {"identity": "component", "parent": "assembly"}], reason="public scene")
    assert world.query("assembly", at=0)["value"]["mask_pixels"] == 144 - int(child.sum())
    assert world.query("component", at=0)["value"]["mask_pixels"] == int(child.sum())


def test_tracking_jump_withdraws_target_before_false_opening():
    world, _, _ = start(max_identity_jump=.06)
    world.observe_frame(frame(1, .1))
    assert world.query("component", at=.1)["status"] == "unknown"
    assert world.review_requests[-1]["reason"] == "identity_conflict"


def test_reacquisition_uses_xyz_identity_instead_of_score_or_xy_only():
    f = frame()
    same = {"mask": mask(), "score": .6}
    other = {"mask": mask(6), "score": .99}
    seed = choose_seed(f, [other, same], {"identity": "component"}, previous=np.array([.25, .35, 1]))
    np.testing.assert_array_equal(seed, same["mask"])
    with pytest.raises(FrameError):
        choose_seed(f, [other], {"identity": "component"}, previous=np.array([.25, .35, 1]))


def test_initial_rank_selection_is_from_public_candidates():
    candidates = [{"mask": mask(6), "score": .8}, {"mask": mask(), "score": .8}]
    result = choose_seed(frame(), candidates, {"selector": {"axis": "image_x", "rank": 1}})
    np.testing.assert_array_equal(result, mask(6))
    with pytest.raises(FrameError):
        choose_seed(frame(), candidates, {})


def finish_with(world, *, reached=True, modified=False, contact=True, shift=1):
    key = world.begin_interaction("component", [.4, 0, 0], at=0, hypothesis="sliding")
    world.observe_frame(frame(shift, .1))
    world.update_robot({"robot_joint_pos": [0]*7}, frame_id=1, captured_at=.1)
    if contact:
        world.verify_relation("contact", ["component", "robot"], lambda *_: True, at=.1)
    return world.finish_interaction(key, {"reached": reached, "target_modified": modified,
        "contact_relation": "contact", "captured_at": .1}, at=.1)


@pytest.mark.parametrize("reached,modified", [(False, False), (None, False), (True, True)])
def test_execution_failure_cannot_change_semantic_support(reached, modified):
    world, _, _ = start()
    result = finish_with(world, reached=reached, modified=modified)
    assert result["diagnosis"] == "execution_unverified"
    assert world.parts["component"]["hypotheses"]["sliding"] == .8
    assert not world.review_requests


def test_closed_gripper_or_rpc_return_does_not_establish_contact():
    world, _, _ = start()
    result = finish_with(world, contact=False)
    assert result["diagnosis"] == "contact_unverified"
    assert not world.review_requests


def test_reliable_effect_contradiction_updates_support_and_requests_review():
    world, _, _ = start()
    result = finish_with(world)
    assert result["status"] == "mismatch"
    assert world.parts["component"]["hypotheses"]["sliding"] == .4
    assert world.review_requests[-1]["reason"] == "effect_contradiction"
    assert world.parts["component"]["semantic"] == "candidate movable region"


def test_no_new_post_action_frame_is_unknown_not_failure():
    world, _, _ = start()
    key = world.begin_interaction("component", [.1, 0, 0], at=0)
    row = world.finish_interaction(key, {"reached": True, "target_modified": False, "captured_at": .1}, at=.1)
    assert row["status"] == "unknown" and not world.review_requests


def test_episode_reset_clears_structure_evidence_and_support():
    world, _, _ = start()
    finish_with(world)
    world.reset()
    assert not world.parts and not world._evidence and not world.interactions
    assert world.structure_revision == 0 and world.episode_id != "test"


def test_old_motion_report_cannot_verify_a_new_interaction():
    world, _, _ = start()
    key = world.begin_interaction("component", [0, 0, 0], at=0)
    row = world.finish_interaction(key, {"reached": True, "target_modified": False,
                                       "captured_at": 0}, at=.1)
    assert row["diagnosis"] == "execution_unverified" and not world.review_requests


def test_production_motor_limit_is_reported_as_nonconvergence():
    motor = MotorEnv()
    motor.move_to_joints_blocking(np.zeros(7), max_steps=2)
    assert motor._last_motion_report["converged"] is False
    assert motor._last_motion_report["steps"] > 0


@pytest.mark.parametrize("quat", [[1, 0, 0, 0], [0, 1, 0, 0], [.7071, 0, .7071, 0]])
def test_motion_report_converts_hand_to_requested_tcp_consistently(quat):
    pos = np.array([.3, .1, .2])
    hand = pos + rotation_wxyz(quat) @ [0, 0, -.1]
    row = motion_report(pos, quat, {"robot_hand_pose_world": [*hand, *quat]},
        ik={"position_clipped": False, "orientation_fallback": False}, joints={"converged": True})
    assert row["reached"] is True and row["position_error"] < 1e-8


def test_stationary_robot_far_from_target_is_not_arrival():
    row = motion_report([.3, 0, .2], [1, 0, 0, 0],
        {"robot_hand_pose_world": [.5, 0, .1, 1, 0, 0, 0]},
        ik={"position_clipped": False, "orientation_fallback": False}, joints={"converged": False})
    assert row["reached"] is False


def test_missing_pose_or_missing_ik_feedback_is_unknown():
    assert motion_report([0, 0, 0], [1, 0, 0, 0], {}, ik={})["reached"] is None
    with pytest.raises(ValueError, match="one finite"):
        motion_report([0, 0, 0], [[1, 0, 0, 0]]*3, {})


def replay(tmp_path, monkeypatch):
    tracker = Tracker()
    monkeypatch.setattr(SAM2Streaming, "from_checkpoint", lambda **kw: tracker)
    motor = MotorEnv()
    motor.privileged = False
    scope = {"segment_sam3_text_prompt": lambda *_: [{"mask": mask(), "score": .9}]}
    env = SimpleNamespace(cfg=SimpleNamespace(privileged=False), low_level_env=motor,
                          _exec_globals=scope)
    path = tmp_path / "semantic.json"
    path.write_text(json.dumps({"mode": "sam2-semantic-world-v2", "sam2": {},
        "objects": [{"identity": "component", "prompt": "public component"}],
        "parts": [{"identity": "assembly", "tracked": False},
                  {"identity": "component", "parent": "assembly", "tracked": True}]}))
    return StreamingReplay(env, path, episode_id="test"), scope, motor, tracker


def test_actual_motor_checkpoint_interrupts_when_focused_part_is_lost(tmp_path, monkeypatch):
    session, scope, motor, tracker = replay(tmp_path, monkeypatch)
    scope["world_set_focus"](["component"])
    tracker.result = {}
    with pytest.raises(MotionInterrupted):
        motor.move_to_joints_blocking(np.ones(7), max_steps=12)
    assert motor._sim_step_count < 12
    assert scope["world_query"]("component")["status"] == "unknown"
    session.close()
    assert set(scope) == {"segment_sam3_text_prompt"} and tracker.closed


def test_required_relation_cannot_accept_unknown_or_false(tmp_path, monkeypatch):
    session, scope, motor, _ = replay(tmp_path, monkeypatch)
    with pytest.raises(MotionInterrupted):
        scope["world_require_relation"]("verified_effect")
    session.world.verify_relation("verified_effect", ["component"], lambda *_: False, at=0)
    with pytest.raises(MotionInterrupted):
        scope["world_require_relation"]("verified_effect")
    session.world.verify_relation("verified_effect", ["component"], lambda *_: True, at=0)
    assert scope["world_require_relation"]("verified_effect")["value"] is True
    session.close()


def test_replay_rejects_unrecorded_motion_reports(tmp_path, monkeypatch):
    session, scope, motor, _ = replay(tmp_path, monkeypatch)
    key = scope["world_begin_interaction"]("component", [0, 0, 0])
    with pytest.raises(ValueError, match="recorded motion"):
        scope["world_finish_interaction"](key, {"action_id": "invented", "reached": True})
    session.close()
