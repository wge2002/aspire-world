"""Held-object relative geometry and visible-surface geometry queries.

Synthetic public RGB-D frames from a downward robot_base camera and labelled
public robot_base hand poses; no simulator state, assets or evaluator.
"""
import json
from types import SimpleNamespace

import numpy as np
import pytest

from aspire.sim.cap.world_model.World import PublicFrame
from aspire.sim.cap.world_model.semantic_world import SemanticWorld
from aspire.sim.cap.world_model.articulated_world import ArticulatedWorld
from aspire.sim.cap.world_model.geometry_queries import visible_geometry_summary
from aspire.sim.cap.world_model.held_geometry import HeldGeometry, tcp_target_for_feature
from aspire.sim.cap.world_model.robot_frames import quaternion_from_rotation
from test_streaming_world import Tracker

K = np.array([[100., 0, 64], [0, 100, 48], [0, 0, 1]])
# Camera 1 m above the robot_base origin region, optical axis along -z.
CAM = np.array([[1., 0, 0, .5], [0, -1, 0, 0], [0, 0, -1, 1.], [0, 0, 0, 1]])
DOWN = np.diag([1., -1, -1])  # top-down TCP orientation
PX = .009  # metres per pixel at the object top (depth 0.9)


def top_down(theta=0.):
    c, s = np.cos(theta), np.sin(theta)
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]]) @ DOWN


def _patch():
    import cv2
    rgb = np.random.default_rng(7).integers(0, 256, (24, 24, 3), dtype=np.uint8)
    return cv2.GaussianBlur(rgb, (3, 3), .6)


PATCH = _patch()  # public surface texture printed on the 24x24 px box; moves with the box


def scene(i, u0, *, visible=True, depth_ok=True, rgb=None, disc=None, tex=False):
    depth = np.ones((96, 128))
    m = np.zeros((96, 128), bool)
    if disc is not None:
        yy, xx = np.mgrid[:96, :128]
        m = (xx - disc[0]) ** 2 + (yy - disc[1]) ** 2 <= disc[2] ** 2
    else:
        m[36:60, u0:u0 + 24] = True
    depth[m] = .9 if depth_ok else 0.
    if rgb is None:
        rgb = np.zeros((96, 128, 3), np.uint8)
        if tex and disc is None:
            rgb[36:60, u0:u0 + 24] = PATCH
    frame = PublicFrame("held", "agentview", i, i * .1, rgb, depth, K, CAM,
                        coordinate_frame="robot_base")
    return frame, (m if visible else None)


def center_of(u0):
    return np.array([.5 + PX * (u0 + 11.5 - 64), -PX * (47.5 - 48), .1])


def robot(tcp, R=DOWN, opening=0.):  # opening: measured normalized gripper opening
    hand = np.asarray(tcp) + R @ [0, 0, -.1]
    return {"robot_hand_pose_base": [*hand, *quaternion_from_rotation(R)],
            "robot_hand_pose_base_reference": "robot_base/panda_hand", "robot_gripper_opening": opening}


def setup(world_class=SemanticWorld, u0=50, tex=True, opening=0.):
    tracker = Tracker()
    world = world_class(tracker, episode_id="held", coordinate_frame="robot_base", tcp_offset=(0, 0, -.1))
    world.held = HeldGeometry(world)
    frame, m = scene(0, u0, tex=tex)
    world.revise_structure(frame, {"obj": m}, [{"identity": "obj"}], reason="public fixture")
    tcp0 = center_of(u0) + [0, 0, .05]
    world.update_robot(robot(tcp0, opening=opening), frame_id=0, captured_at=0.)
    return world, tracker, tcp0


def step(world, tracker, i, u0, tcp, **kw):
    kw.setdefault("tex", True)
    frame, m = scene(i, u0, **{k: v for k, v in kw.items() if k in ("visible", "depth_ok", "rgb", "disc", "tex")})
    if "mask" in kw:
        m = kw["mask"]
    world.update_robot(robot(tcp, **{k: v for k, v in kw.items() if k in ("R", "opening")}),
                       frame_id=i, captured_at=kw.get("robot_at", frame.captured_at))
    tracker.result = {"obj": m}
    world.observe_frame(frame)
    return frame.captured_at


def test_binding_records_tcp_frame_features_from_one_synchronized_measurement():
    world, _, tcp0 = setup()
    state = world.held.bind("obj", at=0.)
    assert state["status"] == "bound_unverified" and state["valid"] is True
    assert state["coordinate_frame"] == "robot_base" and state["reference"] == "robot_base/TCP"
    # robot_base centre (z=.1) under a top-down TCP at z=.15 is +0.05 along TCP z.
    c = np.asarray(state["features_tcp"]["visible_center"])
    expected = DOWN.T @ (center_of(50) - tcp0)
    np.testing.assert_allclose(c, expected, atol=1e-3)
    assert c[2] == pytest.approx(.05, abs=1e-6)
    assert state["features_tcp"]["long_axis_status"] in ("known", "ambiguous")
    assert any(e.endswith(":robot:0") for e in state["evidence_ids"])
    np.testing.assert_allclose(state["prediction"]["visible_center"], center_of(50), atol=1e-3)
    assert state["prediction"]["layer"] == "predicted"


def test_co_motion_is_verified_only_from_measured_frames_after_hand_motion():
    world, tracker, tcp0 = setup()
    world.held.bind("obj", at=0.)
    at = step(world, tracker, 1, 50, tcp0)  # stationary: consistent but uninformative
    assert world.held.state("obj", at=at)["status"] == "consistent_stationary"
    for i in range(2, 5):  # hand and object move +2 px (0.018 m) per frame
        at = step(world, tracker, i, 50 + 2 * (i - 1), tcp0 + [2 * PX * (i - 1), 0, 0])
    state = world.held.state("obj", at=at)
    assert state["status"] == "co_moving_verified" and state["measurement_status"] == "fresh"
    assert state["last_measurement"]["relation"]["source"] == "rgbd_correspondences"
    assert state["verified_scope"] == "se3" and state["measured_relation"]["layer"] == "observed"
    assert state["last_measurement"]["center_drift"] < 1e-6
    np.testing.assert_allclose(state["prediction"]["visible_center"], center_of(56), atol=1e-3)


def test_textureless_translation_is_only_partial_geometry_never_verified():
    world, tracker, tcp0 = setup(tex=False)
    world.held.bind("obj", at=0.)
    for i in range(1, 5):
        at = step(world, tracker, i, 50 + 2 * (i - 1), tcp0 + [2 * PX * (i - 1), 0, 0], tex=False)
    state = world.held.state("obj", at=at)
    assert state["valid"] is True and state["status"] == "relation_partial"
    assert state["co_motion_verified"] is False and state["co_motion_verified_historical"] is False
    assert state["partial_geometry_consistent"] is True and state["verified_scope"] is None
    assert state["last_measurement"]["relation"]["translation"] == "unknown"


def test_object_left_behind_is_detected_as_slip_from_correspondences_and_stays_invalid():
    world, tracker, tcp0 = setup()
    world.held.bind("obj", at=0.)
    step(world, tracker, 1, 50, tcp0 + [PX * 2, 0, 0])
    assert world.held.state("obj", at=.1)["valid"] is True  # 0.018 m < 0.02 m tolerance (+2 sigma)
    at = step(world, tracker, 2, 50, tcp0 + [PX * 4, 0, 0])
    state = world.held.state("obj", at=at)
    assert state["status"] == "slip_detected" and state["valid"] is False and "prediction" not in state
    assert state["cause"] == "rgbd_correspondences"
    at = step(world, tracker, 3, 54, tcp0 + [PX * 4, 0, 0])  # later agreement does not revive it
    assert world.held.state("obj", at=at)["status"] == "slip_detected"
    with pytest.raises(ValueError, match="slip_detected"):
        world.held.bind("obj", at=at)  # rebinding cannot erase the slip
    at = step(world, tracker, 4, 54, tcp0 + [PX * 4, 0, 0], opening=.5)  # measured release
    assert world.held.bind("obj", at=at)["previous_binding_status"] == "slip_detected"


def test_same_span_other_visible_face_is_not_slip_or_verification():
    """Textureless box: the visible patch shifts 5.4 cm in the TCP frame with
    identical spans (e.g. a cylinder showing another face). Without physical
    correspondences that is neither a measured slip nor a measured relation."""
    world, tracker, tcp0 = setup(tex=False)
    world.held.bind("obj", at=0.)
    for i, u0 in ((1, 53), (2, 56)):
        at = step(world, tracker, i, u0, tcp0 + [0, 0, .02 * i], tex=False)
        state = world.held.state("obj", at=at)
        assert state["valid"] is True and state["status"] != "slip_detected"
        assert state["co_motion_verified"] is False
        relation = state["last_measurement"]["relation"]
        assert relation["source"] == "visible_surface_only" and relation["visible_signature_similar"] is True
        assert relation["status"] in ("partial", "undetermined")
    assert state["last_measurement"]["center_drift"] > .05


def test_measured_gripper_closing_beyond_bound_opening_is_grasp_loss():
    world, tracker, tcp0 = setup(opening=.4)
    world.held.bind("obj", at=0.)
    at = step(world, tracker, 1, 50, tcp0, opening=.25)
    state = world.held.state("obj", at=at)
    assert state["status"] == "slip_detected" and state["cause"] == "gripper_closed_further"


def test_occlusion_missing_depth_and_unsynchronized_pose_stay_unknown():
    world, tracker, tcp0 = setup()
    world.held.bind("obj", at=0.)
    for i, kw in ((1, dict(visible=False)), (2, dict(depth_ok=False)), (3, dict(robot_at=.25))):
        at = step(world, tracker, i, 50, tcp0 + [.03 * i, 0, 0], **kw)
        state = world.held.state("obj", at=at)
        assert state["valid"] is True and state["status"] == "bound_unverified"
        assert state["measurement_status"] == "unknown" and state["last_measurement"]["status"] == "unknown"
        assert "center_drift" not in state["last_measurement"]  # nothing fabricated
        assert state["prediction"]["layer"] == "predicted"
    assert "synchronized" in world.held.bindings["obj"]["last_measurement"]["reason"]


def verified(world, tracker, tcp0):
    world.held.bind("obj", at=0.)
    for i in range(1, 4):
        at = step(world, tracker, i, 50 + 2 * i, tcp0 + [2 * PX * i, 0, 0])
    state = world.held.state("obj", at=at)
    assert state["status"] == "co_moving_verified" and state["co_motion_verified"] is True
    return tcp0 + [6 * PX, 0, 0]


@pytest.mark.parametrize("interruption", ["occluded", "missing_depth", "unsynchronized", "stale"])
def test_verified_co_motion_is_not_current_after_occlusion_staleness_or_desync(interruption):
    world, tracker, tcp0 = setup()
    tcp = verified(world, tracker, tcp0)
    kw = {"occluded": dict(visible=False), "missing_depth": dict(depth_ok=False),
          "unsynchronized": dict(robot_at=.47), "stale": {}}[interruption]
    if interruption == "stale":
        at = 30.  # no new frame: the last measurement ages out
    else:
        at = step(world, tracker, 4, 56, tcp, **kw)
    state = world.held.state("obj", at=at)
    assert state["valid"] is True and state["measurement_status"] == "unknown"
    assert state["status"] == "measurement_unknown" and state["co_motion_verified"] is False
    assert state["co_motion_verified_historical"] is True and "historical" in state["reason"]
    assert state["prediction"]["layer"] == "predicted"  # labelled prediction is kept
    # The internal history is not erased, and a later real measurement re-verifies.
    assert world.held.bindings["obj"]["co_motion_verified"] is True
    at = step(world, tracker, 5, 58, tcp + [2 * PX, 0, 0])
    state = world.held.state("obj", at=at)
    assert state["status"] == "co_moving_verified" and state["co_motion_verified"] is True


def test_verified_co_motion_is_not_current_after_identity_interruption():
    world, tracker, tcp0 = setup()
    tcp = verified(world, tracker, tcp0)
    tracker.failure = True
    with pytest.raises(Exception):
        step(world, tracker, 4, 56, tcp)
    state = world.held.state("obj", at=.4)
    assert state["identity_interrupted"] is True and state["co_motion_verified"] is False
    assert state["status"] == "measurement_unknown" and state["co_motion_verified_historical"] is True
    tracker.failure = False
    frame, m = scene(5, 58, tex=True)
    world.update_robot(robot(tcp + [2 * PX, 0, 0]), frame_id=5, captured_at=frame.captured_at)
    world.seed(frame, {"obj": m})  # reacquired and compared again, not trusted blindly
    state = world.held.state("obj", at=frame.captured_at)
    assert state["identity_interrupted"] is False and state["co_motion_verified"] is False
    assert state["status"] == "relation_partial" and state["full_chain_since_bind"] is False
    at = step(world, tracker, 6, 60, tcp + [4 * PX, 0, 0])
    state = world.held.state("obj", at=at)
    assert state["co_motion_verified"] is True and state["verified_scope"].endswith("_since_reanchor")


def test_identity_interruption_structure_revision_release_and_reset():
    world, tracker, tcp0 = setup()
    world.held.bind("obj", at=0.)
    tracker.failure = True
    with pytest.raises(Exception):
        step(world, tracker, 1, 50, tcp0)
    assert world.held.bindings["obj"]["identity_interrupted"] is True
    tracker.failure = False
    frame, m = scene(2, 50)
    world.update_robot(robot(tcp0), frame_id=2, captured_at=frame.captured_at)
    world.seed(frame, {"obj": m})  # explicit reacquisition: compared again, not trusted
    state = world.held.state("obj", at=frame.captured_at)
    assert state["valid"] is True and state["identity_interrupted"] is False
    frame, m = scene(3, 50)
    world.revise_structure(frame, {"obj": m}, [{"identity": "obj"}], reason="revision",
                           evidence_ids=sorted(world._evidence))
    assert world.held.state("obj", at=frame.captured_at)["status"] == "invalidated"
    world.update_robot(robot(tcp0), frame_id=3, captured_at=frame.captured_at)
    world.held.bind("obj", at=frame.captured_at)
    at = step(world, tracker, 4, 50, tcp0, opening=.5)  # measured fingers opened
    assert world.held.state("obj", at=at)["status"] == "released"
    world.reset()
    assert world.held.state("obj", at=0.)["status"] == "unbound"


def test_binding_refuses_missing_stale_or_untracked_evidence():
    tracker = Tracker()
    world = SemanticWorld(tracker, episode_id="held", coordinate_frame="robot_base", tcp_offset=(0, 0, -.1))
    world.held = HeldGeometry(world)
    frame, m = scene(0, 50)
    world.revise_structure(frame, {"obj": m}, [{"identity": "obj"}, {"identity": "rack", "tracked": False}],
                           reason="fixture")
    with pytest.raises(ValueError, match="robot"):
        world.held.bind("obj", at=0.)  # no robot pose
    world.update_robot({"robot_hand_pose_world": [.4, 0, .3, 0, 1, 0, 0]}, frame_id=0, captured_at=0.)
    with pytest.raises(ValueError, match="robot"):
        world.held.bind("obj", at=0.)  # simulator-world pose is not robot_base geometry
    world.update_robot(robot([.4, 0, .2]), frame_id=1, captured_at=0.)
    with pytest.raises(ValueError, match="tracked"):
        world.held.bind("rack", at=0.)
    with pytest.raises(ValueError, match="fresh"):
        world.held.bind("obj", at=5.)


def textured(theta_deg):
    import cv2
    rng = np.random.default_rng(3)
    rgb = rng.integers(0, 256, (96, 128, 3), dtype=np.uint8)
    rgb = cv2.GaussianBlur(rgb, (3, 3), 0)
    M = cv2.getRotationMatrix2D((64., 48.), theta_deg, 1.)
    return cv2.warpAffine(rgb, M, (128, 96), borderMode=cv2.BORDER_REFLECT)


@pytest.mark.parametrize("hand_rotates,expected", [(True, "co_moving_verified"), (False, "slip_detected")])
def test_rigid_relative_rotation_catches_slip_with_unchanged_centre(hand_rotates, expected):
    tracker = Tracker()
    world = ArticulatedWorld(tracker, episode_id="held", coordinate_frame="robot_base", tcp_offset=(0, 0, -.1))
    world.held = HeldGeometry(world)
    disc = (64, 48, 22)
    frame, m = scene(0, 0, rgb=textured(0), disc=disc)
    world.revise_structure(frame, {"obj": m}, [{"identity": "obj"}], reason="fixture")
    tcp = np.array([.5, 0, .15])  # directly above the disc centre
    world.update_robot(robot(tcp), frame_id=0, captured_at=0.)
    assert world.held.bind("obj", at=0.)["rigid"] is not None
    theta = 10.
    R = top_down(np.deg2rad(theta)) if hand_rotates else DOWN
    at = step(world, tracker, 1, 0, tcp, R=R, rgb=textured(theta), disc=disc)
    state = world.held.state("obj", at=at)
    assert world.part_motion["obj"]["status"] == "estimated"
    assert state["status"] == expected
    if hand_rotates:
        assert state["last_measurement"]["rigid_drift"]["angle"] < .03
    else:
        assert state["last_measurement"]["center_drift"] < .005  # centre alone looks unchanged


@pytest.mark.parametrize("world_class", [SemanticWorld, ArticulatedWorld])
def test_partial_occlusion_moves_median_but_same_features_do_not_fake_slip(world_class):
    """Same physical texture under a co-rotating hand while part of the disc
    becomes occluded: the visible median moves several cm in the TCP frame,
    the interior correspondences register the identity relation."""
    tracker = Tracker()
    world = world_class(tracker, episode_id="held", coordinate_frame="robot_base", tcp_offset=(0, 0, -.1))
    world.held = HeldGeometry(world)
    disc = (64, 48, 22)
    frame, m = scene(0, 0, rgb=textured(0), disc=disc)
    world.revise_structure(frame, {"obj": m}, [{"identity": "obj"}], reason="fixture")
    tcp = np.array([.5, 0, .15])
    world.update_robot(robot(tcp), frame_id=0, captured_at=0.)
    world.held.bind("obj", at=0.)
    yy, xx = np.mgrid[:96, :128]
    occluded = m & (xx < 70)  # right part hidden behind e.g. the wrist
    at = step(world, tracker, 1, 0, tcp, R=top_down(np.deg2rad(10)), rgb=textured(10), disc=disc, mask=occluded)
    state = world.held.state("obj", at=at)
    assert state["last_measurement"]["center_drift"] > .03  # the median alone would have latched slip
    assert state["status"] == "co_moving_verified" and state["valid"] is True
    relation = state["last_measurement"]["relation"]
    assert relation["source"] == "rgbd_correspondences" and relation["observable_rotation_axes"] == 3
    assert relation["position_std"] < .01 and all(a["std"] < .05 for a in relation["rotation_axes"])
    # The occluded next frame keeps only a labelled prediction; nothing is promoted.
    at = step(world, tracker, 2, 0, tcp, R=top_down(np.deg2rad(10)), rgb=textured(10), disc=disc, visible=False)
    state = world.held.state("obj", at=at)
    assert state["measurement_status"] == "unknown" and state["co_motion_verified"] is False
    assert state["measured_relation"] is None and state["prediction"]["layer"] == "predicted"
    assert state["partial_geometry_consistent"] is False


def test_collinear_correspondences_leave_rotation_about_their_line_unobservable():
    from aspire.sim.cap.world_model.rigid_tracking import registration_uncertainty
    line = np.c_[np.linspace(0, .2, 40), np.zeros(40), np.zeros(40)] + np.random.default_rng(0).normal(0, 1e-4, (40, 3))
    u = registration_uncertainty(line, .001)
    about_line = u["rotation_std"][np.argmax(np.abs(u["rotation_axes"][:, 0]))]
    assert about_line > .05 and np.sort(u["rotation_std"])[:2].max() < .05


def test_tcp_target_for_feature_is_a_labelled_prediction():
    out = tcp_target_for_feature([0, 0, .05], [.6, .1, .2], [0, 1, 0, 0])
    np.testing.assert_allclose(out["tcp_position"], [.6, .1, .25])
    assert out["layer"] == "predicted"
    with pytest.raises(ValueError):
        tcp_target_for_feature([0, 0], [.6, .1, .2], [0, 1, 0, 0])


# --- pure visible-surface geometry ---------------------------------------

def box_points(length=.2, width=.06, height=.04, step=.004):
    xs, ys = np.arange(0, length + 1e-9, step), np.arange(0, width + 1e-9, step)
    top = np.array([[x, y, height] for x in xs for y in ys])
    side = np.array([[x, 0., z] for x in xs for z in np.arange(0, height, step)])
    return np.vstack([top, side]) + [.4, -.03, 0]


def test_box_long_axis_planes_and_upward_support_candidate():
    g = visible_geometry_summary(box_points())
    assert g["status"] == "known"
    axes = g["principal_axes"]
    assert axes["long_axis_status"] == "known"
    assert abs(axes["long_axis"][0]) > .99 and axes["long_axis_visible_span"] == pytest.approx(.2, abs=.005)
    facing = sorted(p["facing"] for p in g["planes"])
    assert "up" in facing and "side" in facing
    support = g["support_candidates"][0]
    assert support["height_along_up"] == pytest.approx(.04, abs=1e-6)
    assert max(support["visible_spans"]) == pytest.approx(.2, abs=.005)
    assert g["top_slab"]["height_along_up"] == pytest.approx(.04)
    assert any("not contact" in c for c in g["caveats"])


def test_local_region_and_tilted_up_vector():
    pts = box_points()
    local = visible_geometry_summary(pts, near=[.45, 0, .04], radius=.03)
    assert local["status"] == "known" and local["point_count"] < len(pts)
    assert local["region"]["radius"] == .03
    tilted = visible_geometry_summary(pts, up=[1, 0, 0])
    assert not any(c["height_along_up"] == pytest.approx(.04) for c in tilted["support_candidates"])


@pytest.mark.parametrize("points,reason", [
    (np.zeros((0, 3)), "visible points"),
    (np.random.default_rng(0).normal(size=(10, 3)), "visible points"),
    (np.tile([.4, 0, .1], (50, 1)), "coincident"),
    (np.vstack([np.full((60, 3), np.nan), np.random.default_rng(1).normal(size=(5, 3))]), "visible points"),
])
def test_degenerate_inputs_are_unknown(points, reason):
    g = visible_geometry_summary(points)
    assert g["status"] == "unknown" and reason in g["reason"]
    assert "planes" not in g and "support_candidates" not in g


def test_collinear_points_have_an_axis_but_no_plane():
    line = np.c_[np.linspace(0, .2, 60), np.zeros(60), np.full(60, .1)]
    g = visible_geometry_summary(np.vstack([line, [[np.nan, 0, 0]]]))
    assert g["status"] == "known" and g["nonfinite_dropped"] == 1
    assert g["principal_axes"]["long_axis_status"] == "known" and g["planes"] == []
    assert g["support_candidates"] == [] and "planes_reason" in g


def test_square_patch_has_no_fabricated_long_axis():
    xs = np.arange(0, .1, .005)
    patch = np.array([[x, y, .1] for x in xs for y in xs])
    g = visible_geometry_summary(patch)
    assert g["principal_axes"]["long_axis_status"] == "ambiguous" and g["principal_axes"]["long_axis"] is None
    with pytest.raises(ValueError):
        visible_geometry_summary(patch, near=[0, 0, 0], radius=-1)
