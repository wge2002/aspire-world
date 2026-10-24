"""Phase-2 repair: pre-step motion refusal, recovery state, candidate/identity
evidence, event-local review packets and one-time inference receipts.

Motor/gripper behaviour uses the production FrankaLiberoEnv methods compiled
from source (test_robot_frame_contract.Driver) and the actual
FrankaLiberoApiReduced. Perception/review services are CPU fakes; RGB-D
frames are synthetic public images. No simulator asset or evaluator is read.
"""
import copy
import json
from types import SimpleNamespace

import numpy as np
import pytest

from aspire.sim.cap.world_model.World import PublicFrame, TrackingError
from aspire.sim.cap.world_model.control_progress import MotionInterrupted
from aspire.sim.cap.world_model.identity_evidence import (SeedSelectionError, deduplicate,
                                                          measured_motion_association)
from aspire.sim.cap.world_model.motion_feedback import motion_report
from aspire.sim.cap.world_model.qwen_world_review import QwenWorldReviewer, ReviewRejected, validate_reply
from aspire.sim.cap.world_model.sam2_streaming import SAM2Streaming
from aspire.sim.cap.world_model.semantic_world import SemanticWorld, select_seed
from aspire.sim.cap.world_model.streaming_replay import StreamingReplay, StreamingSeedError

import streaming_fixloop_protocol as sp
from test_robot_frame_contract import WRIST, Driver, api_for, object_mask
from test_streaming_world import MotorEnv, Tracker, frame, mask
from test_articulation_world import REVIEW


# --- actual driver pre-step boundary ---------------------------------------


class FlakyTracker:
    def __init__(self):
        self.masks, self.failure, self.result, self.closed = {}, False, None, False

    def initialize(self, rgb, masks):
        self.masks = dict(masks)

    def track(self, rgb):
        if self.failure:
            raise RuntimeError("fixture tracker failure")
        return dict(self.masks) if self.result is None else dict(self.result)

    def reset(self):
        self.masks = {}

    def close(self):
        self.closed = True


def driver_session(tmp_path, monkeypatch, segment=None):
    driver = Driver()
    api = api_for(driver)
    tracker = FlakyTracker()
    monkeypatch.setattr(SAM2Streaming, "from_checkpoint", lambda **kw: tracker)
    candidates = {"value": [{"mask": object_mask(), "score": .9}]}
    scope = {"segment_sam3_text_prompt": segment or (lambda rgb, prompt: copy.deepcopy(candidates["value"])),
             "goto_pose": api.goto_pose}
    env = SimpleNamespace(cfg=SimpleNamespace(privileged=False), low_level_env=driver,
                          _exec_globals=scope, _apis={"franka": api})
    path = tmp_path / "semantic.json"
    path.write_text(json.dumps({"mode": "sam2-semantic-world-v2", "sam2": {},
        "objects": [{"identity": "block", "prompt": "public block"}],
        "parts": [{"identity": "block", "tracked": True}]}))
    return StreamingReplay(env, path, episode_id="ep"), scope, driver, api, tracker, candidates


def target(driver):
    return driver._current_joints + .05


def assert_all_motion_refused(driver, api, error):
    steps, fraction = driver._sim_step_count, driver._gripper_fraction
    with pytest.raises(error):
        driver.move_to_joints_blocking(target(driver), max_steps=5)
    with pytest.raises(error):
        api.close_gripper()
    with pytest.raises(error):
        api.open_gripper()
    with pytest.raises(error):
        driver._step_once()
    assert driver._sim_step_count == steps and driver._gripper_fraction == fraction


def test_tracking_loss_refuses_next_arm_and_gripper_step_before_it_executes(tmp_path, monkeypatch):
    replay, scope, driver, api, tracker, _ = driver_session(tmp_path, monkeypatch)
    assert scope["world_recovery_status"]()["motion_permitted"] is True
    tracker.failure = True
    replay.world.request_verification()  # next checkpoint acquires a frame
    with pytest.raises(TrackingError):
        driver.move_to_joints_blocking(target(driver), max_steps=5)
    assert driver._sim_step_count == 1  # loss is only detectable after the step that observed it
    status = scope["world_recovery_status"]()
    assert status["state"] == "tracking_lost" and status["motion_permitted"] is False
    assert status["objects"]["block"]["status"] == "unknown"
    assert_all_motion_refused(driver, api, TrackingError)
    assert scope["world_recovery_status"]()["motion_refusals"] == 4
    refused = [e for e in replay.journal.events if e["event"] == "motion_refused"]
    assert len(refused) == 4 and all(e["state"] == "tracking_lost" for e in refused)
    replay.close()


def test_failed_reacquire_keeps_refusing_then_fresh_reseed_resumes(tmp_path, monkeypatch):
    replay, scope, driver, api, tracker, candidates = driver_session(tmp_path, monkeypatch)
    tracker.failure = True
    replay.world.request_verification()
    with pytest.raises(TrackingError):
        driver.move_to_joints_blocking(target(driver), max_steps=5)
    tracker.failure = False
    candidates["value"] = []
    with pytest.raises(StreamingSeedError) as caught:
        scope["reacquire_world"]()
    assert caught.value.evidence["attempts"][0]["kind"] == "missing"
    status = scope["world_recovery_status"]()
    assert status["state"] == "reacquire_failed" and status["evidence"]["attempts"][0]["kind"] == "missing"
    # Detached: mutating the report cannot change the recovery state.
    status["evidence"]["attempts"][0]["kind"] = "edited"
    status["state"] = "armed"
    assert scope["world_recovery_status"]()["evidence"]["attempts"][0]["kind"] == "missing"
    assert_all_motion_refused(driver, api, TrackingError)
    # Fresh valid same-frame reseed: motion resumes from the documented state.
    candidates["value"] = [{"mask": object_mask(), "score": .9}]
    scope["reacquire_world"]()
    status = scope["world_recovery_status"]()
    assert status["state"] == "armed" and status["motion_permitted"] is True
    assert status["recovered_from"]["state"] == "reacquire_failed"
    status["recovered_from"]["state"] = "edited"
    assert scope["world_recovery_status"]()["recovered_from"]["state"] == "reacquire_failed"
    steps = driver._sim_step_count
    driver.move_to_joints_blocking(target(driver), max_steps=3)
    api.close_gripper()
    assert driver._sim_step_count > steps and driver._gripper_fraction == 0.
    assert scope["world_query"]("block")["status"] == "known"
    replay.close()


def test_tracker_seed_failure_during_reacquire_is_recorded_and_disarms(tmp_path, monkeypatch):
    replay, scope, driver, api, tracker, _ = driver_session(tmp_path, monkeypatch)
    tracker.initialize = lambda *a: (_ for _ in ()).throw(RuntimeError("fixture init failure"))
    with pytest.raises(TrackingError):
        scope["reacquire_world"]()
    assert scope["world_recovery_status"]()["state"] == "reacquire_failed"
    assert replay.journal.events[-1]["event"] == "reacquire_failed"
    assert_all_motion_refused(driver, api, TrackingError)
    replay.close()


def test_caught_focus_interrupt_cannot_let_next_command_slip_through(tmp_path, monkeypatch):
    replay, scope, driver, api, tracker, _ = driver_session(tmp_path, monkeypatch)
    scope["world_set_focus"](["block"])
    tracker.result = {}  # focused part disappears; tracking itself does not fail
    replay.world.request_verification()
    with pytest.raises(MotionInterrupted):
        driver.move_to_joints_blocking(target(driver), max_steps=5)
    assert driver._sim_step_count == 1  # discovered only after the observing step
    assert replay.bridge._armed  # not a tracking failure ...
    assert_all_motion_refused(driver, api, MotionInterrupted)  # ... but the next command is refused
    assert any(e["event"] == "focus_motion_refused" for e in replay.journal.events)
    tracker.result = None  # part visible again: actual fresh evidence lifts the refusal
    scope["world_refresh"]()
    driver.move_to_joints_blocking(target(driver), max_steps=2)
    replay.close()


def test_reset_settling_is_not_gated_and_legacy_driver_without_hooks_is_unchanged():
    env = MotorEnv()
    env.move_to_joints_blocking(np.ones(7), max_steps=3)
    env._set_gripper(0.)
    env._step_once()
    assert env._sim_step_count == 4


# --- tiny clipping inside the original tolerance -------------------------------


def test_small_workspace_clip_can_arrive_but_never_verifies_an_interaction(tmp_path, monkeypatch):
    replay, scope, driver, api, tracker, _ = driver_session(tmp_path, monkeypatch)
    key = scope["world_begin_interaction"]("block", [0, 0, 0])
    # z=0.0 is clipped to the API workspace floor 0.005: 5 mm < 15 mm tolerance.
    report = scope["goto_pose_report"]([.45, .05, 0.], WRIST)
    assert report["ik"]["position_clipped"] is True and report["target_modified"] is True
    assert report["position_error"] == pytest.approx(.005, abs=1e-6)
    assert report["reached"] is True and report["position_tolerance"] == .015
    row = scope["world_finish_interaction"](key, report)
    assert row["diagnosis"] == "execution_unverified" and row["status"] == "unresolved"
    far = scope["goto_pose_report"]([.45, .05, -.03], WRIST)  # 35 mm clip
    assert far["target_modified"] is True and far["reached"] is False
    replay.close()


def test_motion_report_clip_flag_does_not_change_requested_pose_arrival():
    hand = [.3, .1, .3, 1, 0, 0, 0]
    base = dict(frame="robot_base", joints={"converged": True})
    clipped = motion_report([.3, .1, .4 + .004], [1, 0, 0, 0], {"robot_hand_pose_base": hand},
                            ik={"position_clipped": True, "orientation_fallback": False}, **base)
    assert clipped["reached"] is True and clipped["target_modified"] is True
    fallback = motion_report([.3, .1, .4], [1, 0, 0, 0], {"robot_hand_pose_base": hand},
                             ik={"position_clipped": False, "orientation_fallback": True},
                             joints={"converged": False}, frame="robot_base")
    assert fallback["reached"] is False and fallback["target_modified"] is True


# --- candidate handling ----------------------------------------------------------


def test_duplicate_masks_do_not_fabricate_instances_or_shift_ranks():
    a, b = mask(), mask(6)
    dup = {"mask": a.copy(), "score": .7}
    assert len(deduplicate([{"mask": a, "score": .9}, dup])[0]) == 1
    seed, evidence = select_seed(frame(), [{"mask": a, "score": .9}, dup], {"identity": "x"})
    np.testing.assert_array_equal(seed, a)
    assert evidence["duplicates_merged"] == 1 and evidence["association"] == "initial_unique"
    seed, _ = select_seed(frame(), [{"mask": a, "score": .9}, dup, {"mask": b, "score": .8}],
                          {"identity": "x", "selector": {"axis": "image_x", "rank": 1}})
    np.testing.assert_array_equal(seed, b)


@pytest.mark.parametrize("candidates,definition,kind", [
    ([], {}, "missing"),
    ([{"mask": mask(), "score": .3}], {}, "missing"),
    ([{"mask": mask(), "score": .9}, {"mask": mask(6), "score": .9}], {}, "ambiguous"),
    ([{"mask": mask(), "score": .9}], {"selector": {"axis": "image_x", "rank": 1}}, "rank_unavailable"),
])
def test_seed_failures_are_typed_with_public_evidence(candidates, definition, kind):
    with pytest.raises(SeedSelectionError) as caught:
        select_seed(frame(), candidates, {"identity": "x", **definition})
    assert caught.value.kind == kind and caught.value.evidence["kind"] == kind
    assert caught.value.evidence["candidates_total"] == len(candidates)


def test_rank_without_well_defined_image_order_is_ambiguous():
    upper = np.zeros((12, 12), dtype=bool)
    upper[0:2, 2:6] = True
    upper[0:4, 2:6] = True
    lower = np.zeros((12, 12), dtype=bool)
    lower[7:11, 2:6] = True  # same mean image_x, distinct regions
    with pytest.raises(SeedSelectionError) as caught:
        select_seed(frame(), [{"mask": upper, "score": .9}, {"mask": lower, "score": .9}],
                    {"selector": {"axis": "image_x", "rank": 0}})
    assert caught.value.kind == "ambiguous"


def alt_session(tmp_path, monkeypatch, responses):
    tracker = Tracker()
    monkeypatch.setattr(SAM2Streaming, "from_checkpoint", lambda **kw: tracker)
    motor = MotorEnv()
    motor.privileged = False
    asked = []
    def segment(rgb, prompt):
        asked.append(prompt)
        return copy.deepcopy(responses.get(prompt, []))
    env = SimpleNamespace(cfg=SimpleNamespace(privileged=False), low_level_env=motor,
                          _exec_globals={"segment_sam3_text_prompt": segment})
    path = tmp_path / "alt.json"
    path.write_text(json.dumps({"mode": "sam2-semantic-world-v2", "sam2": {},
        "objects": [{"identity": "part", "prompt": "first", "alternative_prompts": ["second", "third"]}],
        "parts": [{"identity": "part", "tracked": True}]}))
    return lambda: StreamingReplay(env, path, episode_id="test"), asked


def test_alternative_prompt_is_used_only_when_earlier_prompts_are_missing(tmp_path, monkeypatch):
    start, asked = alt_session(tmp_path, monkeypatch, {"second": [{"mask": mask(), "score": .9}]})
    session = start()
    assert asked == ["first", "second"]
    ok = [e for e in session.journal.events if e["event"] == "reacquire_ok"][-1]
    assert ok["associations"]["part"]["prompt_used"] == "second"
    session.close()


def test_ambiguous_primary_prompt_never_falls_through_to_alternatives(tmp_path, monkeypatch):
    two = [{"mask": mask(), "score": .9}, {"mask": mask(6), "score": .9}]
    start, asked = alt_session(tmp_path, monkeypatch, {"first": two, "second": [{"mask": mask(), "score": .9}]})
    with pytest.raises(StreamingSeedError) as caught:
        start()
    assert asked == ["first"] and caught.value.evidence["attempts"][0]["kind"] == "ambiguous"


@pytest.mark.parametrize("alternatives,valid", [(["other"], True), ([], False), (["same"], False),
                                               (["a", "a"], False), ([" "], False), ("text", False)])
def test_alternative_prompts_schema_matches_protocol_and_reviewer(alternatives, valid):
    obj = {"identity": "p", "prompt": "same", "alternative_prompts": alternatives}
    config = {"objects": [obj], "parts": [{"identity": "p", "tracked": True}]}
    errors = sp.streaming_config_errors(json.dumps(config), sp.SEMANTIC_PROFILE)
    assert (not errors) is valid
    assert sp.streaming_config_errors(json.dumps(config), sp.PROFILE)  # legacy v1 keeps its schema
    packet = {"evidence_ids": ["e"], "snapshot": {"semantic": {"parts": {"p": {"identity": "p", "parent": None}}}}}
    reply = {"action": "revise", "reason": "public boundary", "evidence_ids": ["e"], "objects": [obj],
             "parts": [{"identity": "p", "tracked": True}], "joints": []}
    if valid:
        validate_reply(reply, packet)
    else:
        with pytest.raises(ValueError):
            validate_reply(reply, packet)


def test_identity_evidence_module_is_a_hashed_engineered_input():
    assert "cap/world_model/identity_evidence.py" in sp.ENGINEERED_WORLD_FILES


# --- measured-motion identity association ------------------------------------------

S, F = 64, 50.  # image size and focal length: 1 px at depth 1 m = 0.02 m
TEXTURE = np.random.default_rng(3).integers(0, 255, (16, 16, 3), dtype=np.uint8)
OTHER = np.random.default_rng(9).integers(0, 255, (16, 16, 3), dtype=np.uint8)
BACKGROUND = np.random.default_rng(5).integers(0, 255, (S, S, 3), dtype=np.uint8) // 4


def box(x, y, size=16):
    m = np.zeros((S, S), dtype=bool)
    m[y:y + size, x:x + size] = True
    return m


def scene(index, at, objects, *, depth_hole=None, coordinate_frame="robot_base", episode="ep"):
    rgb = BACKGROUND.copy()
    for texture, x, y in objects:
        rgb[y:y + 16, x:x + 16] = texture
    depth = np.ones((S, S))
    if depth_hole is not None:
        depth[depth_hole] = 0
    K = np.array([[F, 0, S / 2], [0, F, S / 2], [0, 0, 1]])
    return PublicFrame(episode, "agentview", index, at, rgb, depth, K, np.eye(4),
                       coordinate_frame=coordinate_frame)


def centre(f, m):
    from aspire.sim.cap.world_model.semantic_world import visible_geometry
    return visible_geometry(f, m)["visible_center"]


def previous_at(x, y, *, at=0., texture=TEXTURE, **kw):
    f = scene(0, at, [(texture, x, y)], **kw)
    return {"frame": f, "mask": box(x, y), "center": np.asarray(centre(f, box(x, y))), "at": at}


def test_large_actual_motion_is_associated_with_coherent_rgbd_correspondences():
    old = previous_at(10, 20)
    new = scene(1, .1, [(TEXTURE, 18, 22), (OTHER, 40, 40)])  # 8 px = 0.16 m, beyond 0.06 m
    candidates = [{"mask": box(40, 40)}, {"mask": box(18, 22)}]
    for c in candidates:
        c["visible_center"] = centre(new, c["mask"])
    result = measured_motion_association(old, new, candidates, gate=.06, max_age=.6)
    assert result["status"] == "verified" and result["candidate"] == 1
    assert result["candidates"][1]["motion_translation"] > .06
    assert result["candidates"][0]["verified"] is False
    # Through select_seed: the strict 0.06 m gate alone rejects; measured motion associates.
    with pytest.raises(SeedSelectionError):
        select_seed(new, [{"mask": c["mask"], "score": .9} for c in candidates], {"identity": "x"},
                    old["center"], max_distance=.06)
    seed, evidence = select_seed(new, [{"mask": c["mask"], "score": .9} for c in candidates],
                                 {"identity": "x"}, old["center"], max_distance=.06,
                                 motion_previous=old, max_age=.6)
    np.testing.assert_array_equal(seed, box(18, 22))
    assert evidence["association"] == "measured_motion"


def test_motion_beyond_flow_range_stays_unverified_not_guessed():
    old = previous_at(10, 20)
    new = scene(1, .1, [(TEXTURE, 34, 20)])  # 24 px in one frame: correspondences break
    m = box(34, 20)
    result = measured_motion_association(old, new, [{"mask": m, "visible_center": centre(new, m)}],
                                         gate=.06, max_age=.6)
    assert result["status"] == "unverified"


def test_wrong_instance_only_keeps_strict_identity_conflict():
    old = previous_at(10, 20)
    new = scene(1, .1, [(OTHER, 40, 40)])  # original left view; a different instance remains
    with pytest.raises(SeedSelectionError) as caught:
        select_seed(new, [{"mask": box(40, 40), "score": .9}], {"identity": "x"}, old["center"],
                    max_distance=.06, motion_previous=old, max_age=.6)
    assert caught.value.kind == "identity_conflict"
    assert caught.value.evidence["measured_motion"]["status"] == "unverified"


def test_nearby_candidate_contradicted_by_measured_motion_is_refused():
    old = previous_at(10, 20)
    # The object moved 12 px (0.24 m); a small distinct bright region appears
    # inside the strict gate of its old centre. (A distractor that repaints
    # the whole old surface breaks correspondences instead: see handoff limits.)
    base = scene(1, .1, [(TEXTURE, 22, 20)])
    near = np.zeros((S, S), dtype=bool)
    near[25:31, 14:20] = True
    rgb = np.array(base.rgb)
    rgb[near] = 250
    new = PublicFrame("ep", "agentview", 1, .1, rgb, base.depth, base.intrinsics, base.camera_to_world,
                      coordinate_frame="robot_base")
    moved = box(22, 20)
    candidates = [{"mask": near, "score": .9}, {"mask": moved, "score": .9}]
    _, evidence = select_seed(new, candidates, {"identity": "x"}, old["center"], max_distance=.06)
    assert evidence["association"] == "strict_gate"  # old rule alone would take the wrong instance
    with pytest.raises(SeedSelectionError) as caught:
        select_seed(new, candidates, {"identity": "x"}, old["center"], max_distance=.06,
                    motion_previous=old, max_age=.6)
    assert caught.value.kind == "identity_conflict"
    assert caught.value.evidence["measured_motion"]["candidate"] == 1


@pytest.mark.parametrize("case", ["stale", "incompatible_frame", "other_episode", "textureless", "bad_depth"])
def test_unusable_correspondence_evidence_never_authorizes_identity(case):
    kw = {}
    texture = TEXTURE
    if case == "textureless":
        texture = np.full((16, 16, 3), 200, dtype=np.uint8)
    old = previous_at(10, 20, texture=texture, at=0.,
                      coordinate_frame=None if case == "incompatible_frame" else "robot_base",
                      episode="old" if case == "other_episode" else "ep")
    at = 1.0 if case == "stale" else .1
    if case == "bad_depth":
        kw["depth_hole"] = (slice(0, S), slice(0, S))
    new = scene(1, at, [(texture, 18, 22)], **kw)
    m = box(18, 22)
    result = measured_motion_association(old, new, [{"mask": m, "visible_center": [0., 0., 1.]}],
                                         gate=.06, max_age=.6)
    assert result["status"] == "unverified" and result["candidate"] is None


def test_occluded_moving_object_is_not_associated_with_a_distractor():
    old = previous_at(10, 20)
    new = scene(1, .1, [(TEXTURE, 18, 22), (OTHER, 18, 22 + 6)])  # bottom part occluded
    distractor = box(40, 40)
    new_d = scene(1, .1, [(TEXTURE, 18, 22), (OTHER, 18, 28), (OTHER, 40, 40)])
    result = measured_motion_association(old, new_d, [{"mask": distractor, "visible_center": centre(new_d, distractor)}],
                                         gate=.06, max_age=.6)
    assert result["status"] == "unverified"
    visible = box(18, 22) & ~box(18, 28)
    partial = measured_motion_association(old, new, [{"mask": visible, "visible_center": centre(new, visible)}],
                                          gate=.06, max_age=.6)
    # The visible fraction alone may or may not suffice; it is never a wrong instance.
    assert partial["status"] in ("verified", "unverified")
    if partial["status"] == "verified":
        assert partial["candidates"][0]["coherence"] >= .7


def test_genuinely_ambiguous_motion_support_is_refused():
    old = previous_at(10, 20)
    new = scene(1, .1, [(TEXTURE, 18, 22)])
    tight = box(18, 22)
    loose = np.zeros((S, S), dtype=bool)
    loose[20:40, 16:36] = True  # contains the moved surface; IoU 0.64 (not a duplicate)
    candidates = [{"mask": m, "visible_center": centre(new, m)} for m in (tight, loose)]
    result = measured_motion_association(old, new, candidates, gate=.06, max_age=.6)
    assert result["status"] == "ambiguous" and result["candidate"] is None


def test_world_tracking_jump_survives_only_with_measured_motion():
    for texture, expected in ((TEXTURE, "known"), (np.full((16, 16, 3), 200, dtype=np.uint8), "unknown")):
        tracker = FlakyTracker()
        world = SemanticWorld(tracker, episode_id="ep", coordinate_frame="robot_base")
        f0 = scene(0, 0., [(texture, 10, 20)])
        world.revise_structure(f0, {"o": box(10, 20)}, [{"identity": "o", "tracked": True}], reason="public")
        tracker.result = {"o": box(18, 22)}
        world.observe_frame(scene(1, .1, [(texture, 18, 22)]))
        fact = world.query("o", at=.1)
        assert fact["status"] == expected
        if expected == "known":
            assert fact["identity_association"]["status"] == "verified"
            assert not world.review_requests
        else:
            assert world.review_requests[-1]["reason"] == "identity_conflict"


# --- event-local review packets and receipts ---------------------------------------


def review_session(tmp_path, monkeypatch, responses, segment=None):
    from aspire.sim.cap.world_model.articulated_world import ARTICULATION_MODE
    tracker, motor, calls = Tracker(), MotorEnv(), []
    motor.privileged = False
    monkeypatch.setattr(SAM2Streaming, "from_checkpoint", lambda **_: tracker)
    def transport(payload):
        packet = json.loads(payload["messages"][0]["content"][0]["text"])
        calls.append((payload, packet))
        reply = responses[len(calls) - 1](packet)
        if isinstance(reply, Exception):
            raise reply
        return {"content": [{"type": "text", "text": json.dumps(reply)}], "stop_reason": "end_turn",
                "usage": {"input_tokens": 11, "output_tokens": 5}}
    original = QwenWorldReviewer.__init__
    monkeypatch.setattr(QwenWorldReviewer, "__init__",
                        lambda self, config: original(self, config, transport=transport))
    scope = {"segment_sam3_text_prompt": segment or (lambda *_: [{"mask": mask(), "score": .9}])}
    env = SimpleNamespace(cfg=SimpleNamespace(privileged=False), low_level_env=motor, _exec_globals=scope)
    path = tmp_path / "v3.json"
    path.write_text(json.dumps({"mode": ARTICULATION_MODE, "sam2": {}, "review": REVIEW,
        "objects": [{"identity": "part", "prompt": "public part"}],
        "parts": [{"identity": "base", "tracked": False}, {"identity": "part", "parent": "base", "tracked": True}]}))
    session = StreamingReplay(env, path, episode_id="test")
    session.world.latest_frame = None  # no image encoding in CPU tests
    return session, scope, motor, tracker, calls


def retain(packet):
    return {"action": "retain", "reason": "uncertain", "evidence_ids": packet["evidence_ids"][-1:]}


def accumulate(session, tracker, n=6):
    tracker.result = {"part": mask()}
    for _ in range(n):
        session.bridge.refresh()
    session.world.latest_frame = None


def events(session, kind):
    return [e for e in session.journal.events if e["event"] == kind]


def test_packet_is_event_local_with_complete_graph_and_only_relevant_ids(tmp_path, monkeypatch):
    session, scope, motor, tracker, calls = review_session(tmp_path, monkeypatch, [retain])
    accumulate(session, tracker)
    current = session.world.query("part", at=motor.get_current_time_s())["evidence_ids"]
    assert len(session.world._evidence) >= 7
    session.world._request_review("identity_conflict", "part", current)
    [receipt] = scope["world_review_pending"]()
    payload, packet = calls[0]
    assert packet["evidence_ids"] == current
    assert set(packet["snapshot"]["semantic"]["parts"]) == {"base", "part"}
    assert "revisions" not in packet["snapshot"]["semantic"]
    assert "model_history" not in packet["snapshot"]["articulation"] and "reviews" not in packet["snapshot"]["articulation"]
    assert payload["max_tokens"] == 64000 and payload["output_config"]["effort"] == "xhigh"
    assert payload["model"] == "qwen3.8-flash-next"
    assert receipt["outcome"] == "accepted" and receipt["usage"]["input_tokens"] == 11
    assert receipt["qwen_called"] is True and receipt["duration_seconds"] >= 0
    session.close()


def test_reply_citing_withheld_registered_history_is_one_data_rejection(tmp_path, monkeypatch):
    old_id = "test:agentview:1"
    reply = lambda p: {"action": "retain", "reason": "old", "evidence_ids": [old_id]}
    session, scope, motor, tracker, calls = review_session(tmp_path, monkeypatch, [reply])
    accumulate(session, tracker)
    assert old_id in session.world._evidence
    current = session.world.query("part", at=motor.get_current_time_s())["evidence_ids"]
    parts = copy.deepcopy(session.world.parts)
    session.world._request_review("identity_conflict", "part", current)
    [receipt] = scope["world_review_pending"]()
    assert receipt["outcome"] == "data_rejected" and receipt["usage"]["input_tokens"] == 11
    assert len(events(session, "qwen_world_review_rejected")) == 1 and not events(session, "qwen_world_review")
    assert session.world.parts == parts and scope["world_recovery_status"]()["motion_permitted"]
    session.close()


def test_failed_reacquire_action_keeps_inference_receipt_and_disarms(tmp_path, monkeypatch):
    seg = {"value": [{"mask": mask(), "score": .9}]}
    reacquire = lambda p: {"action": "reacquire", "reason": "identity", "evidence_ids": p["evidence_ids"][-1:]}
    session, scope, motor, tracker, calls = review_session(tmp_path, monkeypatch, [reacquire],
                                                           segment=lambda *_: copy.deepcopy(seg["value"]))
    session.world._request_review("identity_conflict", "part", sorted(session.world._evidence))
    seg["value"] = []
    with pytest.raises(MotionInterrupted):
        scope["world_review_pending"]()
    [row] = events(session, "qwen_world_review")
    assert row["outcome"] == "grounding_failed" and row["usage"]["output_tokens"] == 5
    assert len(row["request_sha256"]) == 64 and row["duration_seconds"] >= 0
    assert row["action_outcome"]["recovery_state"] == "reacquire_failed"
    assert not events(session, "qwen_world_review_rejected") and not events(session, "qwen_world_review_outage")
    assert session.world.review_history[-1]["outcome"] == "grounding_failed"
    steps = motor._sim_step_count
    with pytest.raises(TrackingError):
        motor.move_to_joints_blocking(np.ones(7), max_steps=3)
    assert motor._sim_step_count == steps
    session.close()


def test_rejected_action_on_valid_world_keeps_receipt_and_world(tmp_path, monkeypatch):
    models = lambda p: {"action": "select_models", "reason": "candidate", "evidence_ids": p["evidence_ids"][-1:],
                        "joints": [{"child": "part", "parent": "base", "family": "sliding"}]}
    session, scope, motor, tracker, calls = review_session(tmp_path, monkeypatch, [models])
    def refuse(*a, **k):
        raise ValueError("fixture: numerical validation refused the family")
    monkeypatch.setattr(session.world, "configure_joints", refuse)
    session.world._request_review("effect_contradiction", "part", sorted(session.world._evidence))
    [receipt] = scope["world_review_pending"]()
    assert receipt["outcome"] == "action_rejected" and receipt["usage"]["input_tokens"] == 11
    assert len(events(session, "qwen_world_review")) == 1 and not events(session, "qwen_world_review_rejected")
    assert scope["world_recovery_status"]()["motion_permitted"]
    session.close()


def test_service_outage_interrupts_once_without_fake_receipt(tmp_path, monkeypatch):
    session, scope, motor, tracker, calls = review_session(tmp_path, monkeypatch,
                                                           [lambda p: TimeoutError("fixture outage")])
    session.world._request_review("identity_conflict", "part", sorted(session.world._evidence))
    with pytest.raises(MotionInterrupted):
        scope["world_review_pending"]()
    [row] = events(session, "qwen_world_review_outage")
    assert row["outcome"] == "service_outage" and "usage" not in row
    assert not events(session, "qwen_world_review") and not events(session, "qwen_world_review_rejected")
    session.close()


def test_uninformative_initial_review_waits_and_informative_motion_rearms_it(tmp_path, monkeypatch):
    session, scope, motor, tracker, calls = review_session(tmp_path, monkeypatch, [retain])
    assert not calls and session.world.review_requests[0]["status"] == "deferred_no_call"
    assert session.journal.counts["world_review_deferred_no_call"] == 1
    assert not session.world.review_history  # no fake inference receipt
    ids = session.world.query("part", at=0)["evidence_ids"]
    small = np.eye(4)
    small[0, 3] = .005
    session.world.part_motion = {"part": {"status": "estimated", "epoch": 0, "motion_transform": small.tolist()}}
    assert scope["world_review_pending"]() == [] and not calls
    moved = np.eye(4)
    moved[0, 3] = .03
    session.world.part_motion = {"part": {"status": "estimated", "epoch": 0, "motion_transform": moved.tolist()}}
    [receipt] = scope["world_review_pending"]()
    assert len(calls) == 1 and calls[0][1]["events"][0]["reason"] == "initial_model_review"
    assert set(ids) <= set(calls[0][1]["evidence_ids"])
    assert events(session, "world_review_rearmed") and receipt["qwen_called"] is True
    session.close()


def test_accepted_model_update_still_interrupts_motion(tmp_path, monkeypatch):
    models = lambda p: {"action": "select_models", "reason": "candidate", "evidence_ids": p["evidence_ids"][-1:],
                        "joints": [{"child": "part", "parent": "base", "family": "sliding"}]}
    session, scope, motor, tracker, calls = review_session(tmp_path, monkeypatch, [models])
    session.world._request_review("effect_contradiction", "part", sorted(session.world._evidence))
    with pytest.raises(MotionInterrupted):
        motor.move_to_joints_blocking(np.ones(7), max_steps=10)
    assert session.world.joints["part"]["family"] == "sliding" and motor._sim_step_count < 10
    session.close()


# --- phase 3 acceptance hardening ----------------------------------------------------


def test_good_declared_same_frame_motion_still_verifies():
    old = previous_at(10, 20)
    new = scene(1, .1, [(TEXTURE, 18, 22)])
    m = box(18, 22)
    result = measured_motion_association(old, new, [{"mask": m, "visible_center": centre(new, m)}],
                                         gate=.06, max_age=.6)
    assert result["status"] == "verified"


@pytest.mark.parametrize("case", ["both_undeclared", "old_undeclared", "new_undeclared", "changed_label",
                                  "other_camera", "other_episode", "stale"])
def test_frame_labels_and_stream_must_be_declared_and_identical(case):
    from dataclasses import replace
    old_frame = {"both_undeclared": None, "old_undeclared": None, "changed_label": "world"}.get(case, "robot_base")
    new_frame = {"both_undeclared": None, "new_undeclared": None}.get(case, "robot_base")
    old = previous_at(10, 20, coordinate_frame=old_frame, episode="old" if case == "other_episode" else "ep")
    new = scene(1, 1.0 if case == "stale" else .1, [(TEXTURE, 18, 22)], coordinate_frame=new_frame)
    if case == "other_camera":
        new = replace(new, camera="robot0_eye_in_hand")
    m = box(18, 22)
    result = measured_motion_association(old, new, [{"mask": m, "visible_center": centre(new, m)}],
                                         gate=.06, max_age=.6)
    assert result["status"] == "unverified" and result["candidate"] is None


def seeded_world(texture):
    world = SemanticWorld(FlakyTracker(), episode_id="ep", coordinate_frame="robot_base")
    world.revise_structure(scene(0, 0., [(texture, 10, 20)]), {"o": box(10, 20)},
                           [{"identity": "o", "tracked": True}], reason="public")
    return world


FORGED = {"association": "measured_motion", "frame_id": 1,
          "measured_motion": {"status": "verified", "candidate": 0}}


@pytest.mark.parametrize("case", ["textureless", "wrong_mask", "stale", "undeclared_world"])
def test_caller_association_claim_never_authorizes_an_identity_jump(case):
    texture = np.full((16, 16, 3), 200, dtype=np.uint8) if case == "textureless" else TEXTURE
    if case == "undeclared_world":
        world = SemanticWorld(FlakyTracker(), episode_id="ep")
        world.revise_structure(scene(0, 0., [(texture, 10, 20)], coordinate_frame=None), {"o": box(10, 20)},
                               [{"identity": "o", "tracked": True}], reason="public")
        new = scene(1, .1, [(texture, 18, 22)], coordinate_frame=None)
    else:
        world = seeded_world(texture)
        new = scene(1, 1.0 if case == "stale" else .1, [(texture, 18, 22), (OTHER, 40, 40)])
    seed_mask = box(40, 40) if case == "wrong_mask" else box(18, 22)  # >= 0.16 m: beyond 0.06 m
    world.seed(new, {"o": seed_mask}, association={"o": FORGED})
    fact = world.query("o", at=new.captured_at)
    assert fact["status"] == "unknown" and "identity jump" in fact["reason"]
    assert fact["identity_association"]["status"] != "verified"
    assert world.review_requests[-1]["reason"] == "identity_conflict"


def test_claim_is_diagnostic_and_actual_motion_is_independently_recomputed():
    world = seeded_world(TEXTURE)
    new = scene(1, .1, [(TEXTURE, 18, 22)])
    world.seed(new, {"o": box(18, 22)}, association={"o": {"association": "strict_gate", "frame_id": 1}})
    fact = world.query("o", at=.1)
    assert fact["status"] == "known"
    assert fact["claimed_association"]["association"] == "strict_gate"  # recorded, not trusted
    assert fact["identity_association"]["status"] == "verified"  # recomputed from current evidence


def test_reacquire_after_actual_large_motion_succeeds_and_textureless_jump_is_refused(tmp_path, monkeypatch):
    tracker = FlakyTracker()
    monkeypatch.setattr(SAM2Streaming, "from_checkpoint", lambda **kw: tracker)
    driver = Driver()
    view = {"frame": scene(0, 0., [(TEXTURE, 10, 20)])}
    candidates = {"value": [{"mask": box(10, 20), "score": .9}]}

    def observation():
        f = view["frame"]
        return {"agentview": {"images": {"rgb": np.array(f.rgb), "depth": np.array(f.depth)},
                              "intrinsics": f.intrinsics, "pose_mat": f.camera_to_world,
                              "pose_frame": "robot_base"}}
    monkeypatch.setattr(driver, "get_observation", observation, raising=False)
    monkeypatch.setattr(driver, "get_current_time_s", lambda: view["frame"].captured_at, raising=False)
    scope = {"segment_sam3_text_prompt": lambda rgb, prompt: copy.deepcopy(candidates["value"])}
    env = SimpleNamespace(cfg=SimpleNamespace(privileged=False), low_level_env=driver, _exec_globals=scope)
    path = tmp_path / "semantic.json"
    path.write_text(json.dumps({"mode": "sam2-semantic-world-v2", "sam2": {},
        "objects": [{"identity": "o", "prompt": "public object"}], "parts": [{"identity": "o", "tracked": True}]}))
    replay = StreamingReplay(env, path, episode_id="ep")
    # Actual 0.16 m move with coherent RGB-D support: reacquisition succeeds.
    view["frame"] = scene(1, .1, [(TEXTURE, 18, 22), (OTHER, 40, 40)])
    candidates["value"] = [{"mask": box(40, 40), "score": .9}, {"mask": box(18, 22), "score": .9}]
    scope["reacquire_world"]()
    fact = scope["world_query"]("o")
    assert fact["status"] == "known" and fact["identity_association"]["status"] == "verified"
    assert scope["world_recovery_status"]()["motion_permitted"] is True
    # Textureless large move: no correspondence support -> original strict refusal.
    flat = np.full((16, 16, 3), 200, dtype=np.uint8)
    view["frame"] = scene(2, .2, [(flat, 30, 30)])
    candidates["value"] = [{"mask": box(30, 30), "score": .9}]
    with pytest.raises(StreamingSeedError) as caught:
        scope["reacquire_world"]()
    assert caught.value.evidence["attempts"][0]["kind"] == "identity_conflict"
    assert scope["world_recovery_status"]()["state"] == "reacquire_failed"
    replay.close()


def test_strict_nearby_and_tie_ambiguity_are_unchanged_without_motion_support():
    f = frame()
    seed, evidence = select_seed(f, [{"mask": mask(), "score": .6}, {"mask": mask(6), "score": .99}],
                                 {"identity": "x"}, np.array([.25, .35, 1]), max_distance=.06)
    np.testing.assert_array_equal(seed, mask())
    assert evidence["association"] == "strict_gate"
    twin = np.zeros((12, 12), dtype=bool)
    twin[6:10, 1:5] = True  # distinct region equally far (0.2 m) from the previous centre
    with pytest.raises(SeedSelectionError) as caught:
        select_seed(f, [{"mask": mask(), "score": .9}, {"mask": twin, "score": .9}], {"identity": "x"},
                    np.array([.25, .55, 1]), max_distance=.3)
    assert caught.value.kind == "ambiguous"
