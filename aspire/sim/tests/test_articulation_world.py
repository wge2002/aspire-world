"""Behavioral v3 contracts: actual geometry math, event handling and admission."""
import copy
import json
from types import SimpleNamespace

import numpy as np
import pytest

from aspire.sim.cap.world_model.joint_models import evaluate, fit_models, rotation
from aspire.sim.cap.world_model.rigid_tracking import PublicRigidTracker, rigid_registration
from aspire.sim.cap.world_model.articulated_world import ArticulatedWorld, ARTICULATION_MODE, validate_joints
from aspire.sim.cap.world_model.qwen_world_review import QwenWorldReviewer, ReviewRejected, validate_reply
from aspire.sim.cap.world_model.World import PublicFrame
from aspire.sim.cap.world_model.streaming_replay import StreamingReplay
from aspire.sim.cap.world_model.control_progress import MotionInterrupted
from aspire.sim.cap.world_model.sam2_streaming import SAM2Streaming
from test_streaming_world import Tracker, frame, mask, MotorEnv
import test_streaming_fixloop as old
from test_streaming_fixloop import cell, cli, task_dir
import streaming_fixloop_protocol as sp
import streaming_fixloop_campaign as campaign
from test_streaming_fixloop_heldout import finalized, hcli, result

REVIEW = {"endpoint": "http://127.0.0.1:8121", "model": "qwen3.8-flash-next",
          "effort": "xhigh", "context_tokens": 1000000, "max_output_tokens": 64000, "timeout_seconds": 900}


def samples(model, coordinates):
    return [{"relative_transform": evaluate(model, q).tolist(), "captured_at": i*.2,
             "evidence_ids": [f"public:{i}"]} for i, q in enumerate(coordinates)]


def test_sliding_axis_and_held_back_motion_are_estimated():
    model = {"family": "sliding", "axis": np.array([1., 2, -1])/np.sqrt(6)}
    fit = fit_models(samples(model, [0, .02, .04, .06, .08]))
    assert fit["status"] == "fitted" and fit["selected"]["family"] == "sliding"
    np.testing.assert_allclose(fit["selected"]["axis"], model["axis"], atol=1e-8)
    assert fit["selected"]["validation_error"]["position_rmse"] < 1e-8


def test_rotating_axis_point_and_predicted_arc_are_estimated():
    model = {"family": "rotating", "axis": [0, 0, 1], "axis_point": [.3, -.2, 0]}
    fit = fit_models(samples(model, [0, .12, .25, .4, .55]))
    assert fit["status"] == "fitted" and fit["selected"]["family"] == "rotating"
    np.testing.assert_allclose(fit["selected"]["axis_point"], model["axis_point"], atol=1e-7)
    np.testing.assert_allclose(evaluate(fit["selected"], .3), evaluate(model, .3), atol=1e-7)


def test_qwen_preference_cannot_override_motion():
    fit = fit_models(samples({"family": "sliding", "axis": [1, 0, 0]}, [0, .02, .04, .06]), preferred="rotating")
    assert fit["status"] == "unknown" and "contradicts" in fit["reason"]
    assert any(c["valid"] and c["family"] == "sliding" for c in fit["candidates"])


def test_stationary_or_small_motion_does_not_establish_a_fixed_joint():
    stationary = fit_models(samples({"family": "fixed"}, [0, 0, 0, 0]))
    assert stationary["selected"] is None and "unexcited" in stationary["reason"]
    small = fit_models(samples({"family": "rotating", "axis": [0, 0, 1], "axis_point": [.1, 0, 0]}, [0, .01, .02, .03]))
    assert small["status"] == "unknown"
    # A long lever makes even a small arc translate by >1cm; still not a slider.
    lever = fit_models(samples({"family":"rotating","axis":[0,0,1],"axis_point":[.8,0,0]}, [0,.01,.02,.03]))
    assert lever["selected"] is None


def test_free_motion_hypothesis_does_not_impose_a_sliding_constraint():
    rows = samples({"family": "sliding", "axis": [1, 0, 0]}, [0, .02, .04, .06])
    fit = fit_models(rows, families=("fixed", "sliding", "rotating", "free"), preferred="free")
    assert fit["selected"] is None and "unconstrained" in fit["reason"]


def test_last_sample_contradiction_is_not_fitted_away():
    rows = samples({"family": "sliding", "axis": [1, 0, 0]}, [0, .02, .04, .06])
    rows[-1]["relative_transform"][1][3] = .04
    assert fit_models(rows)["status"] == "unknown"


def test_rigid_registration_recovers_rotation_with_outliers():
    rng = np.random.default_rng(7)
    a = rng.uniform(-.1, .1, (50, 3))
    r, p = rotation([0, 0, 1], .3), np.array([.02, -.01, .03])
    b = a @ r.T+p
    b[:8] += .2
    estimate = rigid_registration(a, b)
    assert estimate["inliers"] == 42
    np.testing.assert_allclose(estimate["transform"][:3, :3], r, atol=1e-8)
    np.testing.assert_allclose(estimate["transform"][:3, 3], p, atol=1e-8)


def test_collinear_or_sparse_correspondences_are_unknown():
    a = np.c_[np.arange(12), np.zeros((12, 2))]
    assert rigid_registration(a, a+.01) is None
    assert rigid_registration(a[:5], a[:5]) is None


def textured_frame(i, shift):
    import cv2
    rgb = np.random.default_rng(11).integers(0, 256, (96, 128, 3), dtype=np.uint8)
    rgb = cv2.warpAffine(rgb, np.float32([[1, 0, shift], [0, 1, 0]]), (128, 96))
    return PublicFrame("texture", "agentview", i, i*.2, rgb, np.ones((96, 128)),
        np.array([[100., 0, 64], [0, 100, 48], [0, 0, 1]]), np.eye(4))


def test_rgbd_feature_tracking_estimates_motion_and_breaks_missing_epoch():
    tracker = PublicRigidTracker()
    m = np.zeros((96, 128), bool)
    m[15:80, 20:100] = True
    assert tracker.update(textured_frame(0, 0), {"p": m})["p"]["status"] == "reference_only"
    moved = tracker.update(textured_frame(1, 2), {"p": m})["p"]
    assert moved["status"] == "estimated"
    np.testing.assert_allclose(np.array(moved["motion_transform"])[:3, 3], [.02, 0, 0], atol=.002)
    assert tracker.update(textured_frame(2, 4), {"p": None})["p"]["status"] == "unknown"
    restarted = tracker.update(textured_frame(3, 6), {"p": m})["p"]
    assert restarted["status"] == "reference_only" and restarted["epoch"] > moved["epoch"]


def test_world_fits_child_motion_relative_to_a_moving_tracked_parent():
    def scene(i):
        rng = np.random.default_rng(19)
        rgb = np.zeros((96, 128, 3), np.uint8)
        a, b = np.zeros((96, 128), bool), np.zeros((96, 128), bool)
        a[20:80, 10+i:50+i] = True
        b[20:80, 65+3*i:100+3*i] = True
        rgb[a] = rng.integers(0, 256, (a.sum(), 3), dtype=np.uint8)
        rgb[b] = rng.integers(0, 256, (b.sum(), 3), dtype=np.uint8)
        f = PublicFrame("moving", "agentview", i, i*.2, rgb, np.ones((96,128)),
            np.array([[100.,0,64],[0,100,48],[0,0,1]]), np.eye(4))
        return f, {"base":a,"part":b}
    tracker = Tracker()
    world = ArticulatedWorld(tracker, episode_id="moving")
    f, masks = scene(0)
    world.revise_structure(f, masks, [{"identity":"base"}, {"identity":"part","parent":"base"}], reason="public texture fixture")
    world.configure_joints([{"child":"part","parent":"base","family":"unknown"}],
        reason="candidate edge", evidence_ids=list(world._evidence))
    for i in range(1,5):
        f, tracker.result = scene(i)
        world.observe_frame(f)
    fit = world.joint_query("part", at=.8)
    assert fit["status"] == "fitted" and fit["selected"]["family"] == "sliding"
    np.testing.assert_allclose(fit["selected"]["axis"], [1,0,0], atol=.02)
    assert fit["selected"]["q"] == pytest.approx(.06, abs=.004)
    assert world.predict_joint("part", .03, at=.8)["status"] == "predicted"
    assert world.predict_joint("part", .09, at=.8)["status"] == "unknown"
    future = world.predict_joint("part", .09, at=.8, extrapolate=True)
    assert future["status"] == "predicted" and future["validation_scope"] == "unvalidated_extrapolation"
    assert world.joint_query("part", at=1.5)["status"] == "unknown"
    world.reset()
    assert world.rigid_tracker.previous is None and not world.joints


def packet():
    return {"evidence_ids": ["public:0"], "snapshot": {"semantic": {"parts": {
        "base": {"identity": "base", "parent": None, "tracked": True},
        "part": {"identity": "part", "parent": "base", "tracked": True}}}}}


def decision():
    return {"action": "select_models", "reason": "public relative motion supports a candidate",
            "evidence_ids": ["public:0"], "joints": [{"child": "part", "parent": "base", "family": "sliding"}]}


@pytest.mark.parametrize("change", [{"code": "exec(...)"}, {"evidence_ids": ["invented"]},
    {"joints": [{"child": "part", "parent": "base", "family": "magic"}]},
    {"joints": [{"child": "base", "parent": "part", "family": "sliding"}]}])
def test_reviewer_rejects_code_false_evidence_and_invalid_models(change):
    with pytest.raises(ValueError):
        validate_reply({**decision(), **change}, packet())


def test_reviewer_preserves_fixed_request_parameters_and_records_usage():
    calls = []
    def transport(payload):
        calls.append(payload)
        return {"content": [{"type": "text", "text": json.dumps(decision())}], "stop_reason": "end_turn", "usage": {"input_tokens": 123}}
    reply, receipt = QwenWorldReviewer(REVIEW, transport=transport).review(packet())
    assert reply["action"] == "select_models"
    assert calls[0]["max_tokens"] == 64000 and calls[0]["output_config"]["effort"] == "xhigh"
    assert calls[0]["thinking"] == {"type": "adaptive"}
    assert receipt["usage"]["input_tokens"] == 123 and len(receipt["request_sha256"]) == 64


@pytest.mark.parametrize("config", [{**REVIEW, "endpoint": "https://external.invalid"}, {**REVIEW, "effort": "low"}])
def test_reviewer_refuses_route_or_model_substitution(config):
    with pytest.raises(ValueError):
        QwenWorldReviewer(config)


def test_model_output_truncation_is_not_a_valid_revision():
    reviewer = QwenWorldReviewer(REVIEW, transport=lambda _: {"content": [{"type": "text", "text": json.dumps(decision())}], "stop_reason": "max_tokens"})
    with pytest.raises(ValueError, match="truncated"):
        reviewer.review(packet())


def articulation_config():
    return {"objects": copy.deepcopy(old.CONFIG["objects"]), "parts": [
        {"identity": "bowl", "tracked": True}, {"identity": "plate", "parent": "bowl", "tracked": True}],
        "joints": [{"child": "plate", "parent": "bowl", "family": "unknown"}]}


def test_v3_is_explicit_and_freezes_online_review_route(cell):
    cell.case.update(profile=sp.ARTICULATION_PROFILE, method="sam2-articulation-qwen-v3",
                     method_differences=["public rigid registration", "parametric joints", "online Qwen review"])
    cell.path.write_text(json.dumps(cell.case))
    config = articulation_config()
    assert not sp.streaming_config_errors(json.dumps(config), sp.ARTICULATION_PROFILE)
    assert sp.streaming_config_errors(json.dumps(config), sp.SEMANTIC_PROFILE)
    assert sp.streaming_config_errors(json.dumps({**config, "review": REVIEW}), sp.ARTICULATION_PROFILE)
    assert cli(cell, "init")[0] == 0
    ledger = json.loads((task_dir(cell)/"development_state.json").read_text())
    assert ledger["identity"]["online_world_review"] == REVIEW
    assert ledger["identity"]["retry_limit"] == 3
    authored = task_dir(cell)/"initial_streaming_config.json"
    authored.write_text(json.dumps(config))
    directory = task_dir(cell)/"materialized"
    directory.mkdir()
    runtime = json.loads(sp.materialize_config(cell.case, directory, authored).read_text())
    assert runtime["mode"] == ARTICULATION_MODE and runtime["review"] == REVIEW
    assert set(sp.ENGINEERED_WORLD_FILES) >= {"cap/world_model/qwen_world_review.py", "cap/world_model/joint_models.py"}
    cell.case["claude_bin"] = "/recorded/native/claude"
    prompt = campaign.worker_prompt(cell.case, old.SIM)
    assert sp.ARTICULATION_PROFILE in prompt and "world_query_joint" in prompt


def test_joint_graph_rejects_unrelated_or_cyclic_edge():
    p = packet()["snapshot"]["semantic"]["parts"]
    with pytest.raises(ValueError):
        validate_joints([{"child": "base", "parent": "part"}], p)
    assert validate_joints([{"child":"base","parent":None,"family":"free"}],p)["base"]["family"] == "free"
    with pytest.raises(ValueError):
        validate_joints([{"child":"base","parent":None,"family":"sliding"}],p)


def test_frozen_v3_rules_and_model_route_are_identical_for_all_fifty_seeds(cell, monkeypatch):
    cell.case.update(profile=sp.ARTICULATION_PROFILE, method="sam2-articulation-qwen-v3",
                     method_differences=["public motion fitting", "online Qwen review"])
    cell.path.write_text(json.dumps(cell.case))
    monkeypatch.setattr(old, "CONFIG", articulation_config())
    finalized(cell, monkeypatch)
    assert hcli(cell)[0] == 0
    assert result(cell)["completed"] and result(cell)["profile"] == sp.ARTICULATION_PROFILE
    calls = [c for c in cell.fake.calls if "/heldout/" in str(c["directory"])]
    assert len(calls) == 50
    configs = [json.loads((c["directory"]/"streaming_world_config.json").read_text()) for c in calls]
    assert all(c["mode"] == ARTICULATION_MODE and c["review"] == REVIEW for c in configs)


def replay_v3(tmp_path, monkeypatch, responses):
    tracker, motor, calls = Tracker(), MotorEnv(), []
    motor.privileged = False
    monkeypatch.setattr(SAM2Streaming, "from_checkpoint", lambda **_: tracker)
    def review(self, packet, **kwargs):
        calls.append(copy.deepcopy(packet))
        reply = responses[len(calls)-1](packet)
        receipt = {"usage": {"input_tokens": 7}, "duration_seconds": .01, "response_sha256": "f"*64}
        try:
            validate_reply(reply, packet)
        except ValueError as exc:  # mirrors QwenWorldReviewer: data errors are ReviewRejected
            raise ReviewRejected(str(exc), {**receipt, "status": "rejected"}) from exc
        return reply, {**receipt, "decision": reply, "status": "accepted"}
    monkeypatch.setattr(QwenWorldReviewer, "review", review)
    scope = {"segment_sam3_text_prompt": lambda *_: [{"mask": mask(), "score": .9}]}
    env = SimpleNamespace(cfg=SimpleNamespace(privileged=False), low_level_env=motor, _exec_globals=scope)
    config = {"mode": ARTICULATION_MODE, "sam2": {}, "review": REVIEW,
        "objects": [{"identity": "part", "prompt": "public part"}],
        "parts": [{"identity": "base", "tracked": False}, {"identity": "part", "parent": "base", "tracked": True}]}
    path = tmp_path/"articulation.json"
    path.write_text(json.dumps(config))
    session = StreamingReplay(env, path, episode_id="test")
    return session, scope, motor, calls


def retain(packet):
    return {"action": "retain", "reason": "public model remains uncertain", "evidence_ids": packet["evidence_ids"]}


def test_events_call_qwen_once_and_do_not_poll_each_frame(tmp_path, monkeypatch):
    session, scope, motor, calls = replay_v3(tmp_path, monkeypatch, [retain])
    # Uninformative initial review: explicit deferral, no Qwen call or receipt.
    assert not calls and not session.world.review_history
    deferred = [e for e in session.journal.events if e["event"] == "world_review_deferred_no_call"]
    assert len(deferred) == 1 and deferred[0]["qwen_called"] is False and "usage" not in deferred[0]
    assert session.world.review_requests[0]["status"] == "deferred_no_call"
    session.world._request_review("identity_conflict", "part", list(session.world._evidence))
    scope["world_review_pending"]()
    session._semantic_checkpoint(session.world.snapshot(at=0))
    assert len(calls) == 1 and [e["reason"] for e in calls[0]["events"]] == ["identity_conflict"]
    assert scope["world_query_joint"]("part")["status"] == "unknown"
    session.world.reset()
    assert not session.world.review_history and not session.world.joints
    session.close()


def test_credible_event_revises_structure_with_current_masks(tmp_path, monkeypatch):
    def revise(packet):
        return {"action": "revise", "reason": "public boundaries support renaming with lineage",
            "evidence_ids": packet["evidence_ids"], "parts": [{"identity": "base", "tracked": False},
                {"identity": "part2", "parent": "base", "tracked": True}],
            "objects": [{"identity": "part2", "prompt": "current public part"}],
            "lineage": {"part": ["part2"]}, "joints": [{"child": "part2", "parent": "base", "family": "sliding"}]}
    session, scope, motor, calls = replay_v3(tmp_path, monkeypatch, [revise])
    session.world._request_review("effect_contradiction", "part", list(session.world._evidence))
    scope["world_review_pending"]()
    assert len(calls) == 1 and session.world.structure_revision == 2
    assert session.world.joints["part2"]["family"] == "sliding"
    assert session.world.structure_history[-1]["lineage"] == {"part": ["part2"]}
    assert session.world.joint_query("part2", at=0)["status"] == "unknown"
    session.close()


def test_rejected_review_keeps_a_valid_world_without_mutating_graph(tmp_path, monkeypatch):
    session, scope, motor, calls = replay_v3(tmp_path, monkeypatch, [lambda p: {**retain(p), "evidence_ids": ["invented"]}])
    original = copy.deepcopy(session.world.parts)
    session.world._request_review("identity_conflict", "part", list(session.world._evidence))
    result = scope["world_review_pending"]()
    assert result[0]["status"] == "rejected"
    assert session.world.parts == original and session.world.structure_revision == 1
    assert session.journal.events[-1]["event"] == "qwen_world_review_rejected"
    session.close()


def test_actual_transport_outage_still_interrupts_control(tmp_path, monkeypatch):
    session, scope, motor, calls = replay_v3(tmp_path, monkeypatch, [retain])
    def failed(*a, **k):
        raise TimeoutError("fixture service failure")
    monkeypatch.setattr(QwenWorldReviewer,"review",failed)
    session.world._request_review("identity_conflict","part",list(session.world._evidence))
    with pytest.raises(MotionInterrupted):
        scope["world_review_pending"]()
    session.close()


@pytest.mark.parametrize("actual_delta,reached,contact,expected",[
    (0.,True,True,"mismatch"),(.2,True,True,"match"),
    (0.,False,True,"unresolved"),(0.,True,False,"unresolved")])
def test_joint_effect_checks_rotation_even_with_unchanged_visible_centre(actual_delta,reached,contact,expected):
    tracker=Tracker()
    world=ArticulatedWorld(tracker,episode_id="test")
    masks={"base":mask(),"part":mask(6)}
    world.revise_structure(frame(),masks,[{"identity":"base"},{"identity":"part","parent":"base"}],reason="public fixture")
    world.configure_joints([{"child":"part","parent":"base","family":"rotating"}],reason="motion candidate",evidence_ids=list(world._evidence))
    model={"family":"rotating","axis":[0,0,1],"axis_point":[0,0,0]}
    world.joint_models["part"]=fit_models(samples(model,[0,.12,.25,.4]))
    def motion(angle,index):
        return {"base":{"status":"estimated","epoch":1,"motion_transform":np.eye(4).tolist(),"frame_id":index},
                "part":{"status":"estimated","epoch":1,"motion_transform":evaluate(model,angle).tolist(),"frame_id":index}}
    world.part_motion=motion(.4,0)
    key=world.begin_joint_interaction("part",.2,at=0)
    tracker.result=masks
    world.observe_frame(frame(1,.1))
    world.part_motion=motion(.4+actual_delta,1)
    if contact:
        world.verify_relation("fixture_contact",["part"],lambda _:True,at=.1)
    result=world.finish_joint_interaction(key,{"reached":reached,"target_modified":False,
        "captured_at":.1,"contact_relation":"fixture_contact"},at=.1)
    assert result["status"]==expected
    if expected=="mismatch":
        assert result["angle_error"]==pytest.approx(.2)
        assert world.review_requests[-1]["reason"]=="joint_effect_contradiction"
    else:
        assert not world.review_requests


def test_new_representation_interrupts_an_ongoing_motor_trajectory(tmp_path,monkeypatch):
    def models(packet):
        return {"action":"select_models","reason":"public candidate family",
            "evidence_ids":packet["evidence_ids"],"joints":[{"child":"part","parent":"base","family":"sliding"}]}
    session,scope,motor,calls=replay_v3(tmp_path,monkeypatch,[models])
    session.world._request_review("effect_contradiction","part",list(session.world._evidence))
    with pytest.raises(MotionInterrupted):
        motor.move_to_joints_blocking(np.ones(7),max_steps=10)
    assert session.world.joints["part"]["family"]=="sliding"
    assert motor._sim_step_count<10
    session.close()
