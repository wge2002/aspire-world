"""Pure support-placement geometry for a held body (held_surface.py).

Synthetic visible points only; no simulator state, assets or evaluator.
"""
import numpy as np
import pytest

from aspire.sim.cap.world_model.held_surface import cylinder_cross_section, support_placement_candidates
from aspire.sim.cap.world_model.robot_frames import rotation_wxyz


def visible_cylinder(radius=.04, length=.2, centre=(0., 0., .08), axis=2, view=(1., 0, 0), arc=np.pi,
                     step=.004):
    """Visible half (by default) of a cylinder in the TCP frame, seen along ``view``."""
    centre = np.asarray(centre)
    e = np.eye(3)
    a, u = e[axis], np.asarray(view, float) / np.linalg.norm(view)
    w = np.cross(a, u)
    rows = []
    for t in np.arange(-length / 2, length / 2 + 1e-9, step):
        for phi in np.linspace(-arc / 2, arc / 2, 25):
            # visible side faces the camera: -view direction
            rows.append(centre + t * a + radius * (-np.cos(phi) * u + np.sin(phi) * w))
    return np.array(rows)


TCP = np.eye(4)
TCP[:3, 3] = [.3, 0., .4]
FLAT = {"normal": [0, 0, 1.], "centroid": [.6, -.2, .25]}


def best(out):
    assert out["status"] == "candidates", out.get("reason")
    return out["candidates"][0]


def test_cross_section_fit_recovers_radius_and_centreline():
    pts = visible_cylinder()
    fit = cylinder_cross_section(pts, [0, 0, 1])
    assert fit["status"] == "known" and fit["radius"] == pytest.approx(.04, abs=1e-3)
    np.testing.assert_allclose(fit["centerline_point"], [0, 0, .08], atol=1e-3)
    assert fit["visible_axial_span"] == pytest.approx(.2, abs=.005)


def test_flat_support_tangent_height_uses_radius_and_axis_lies_in_plane():
    out = support_placement_candidates(visible_cylinder(), TCP, FLAT, along=[[1, 0, 0]],
                                       view_direction_tcp=[1, 0, 0])
    c = best(out)
    np.testing.assert_allclose(c["body_center_world"], [.6, -.2, .29], atol=2e-3)
    np.testing.assert_allclose(c["lowest_point_world"][2], .25, atol=2e-3)
    R = rotation_wxyz(c["tcp_quaternion_wxyz"])
    assert abs(abs((R @ [0, 0, 1]) @ [1, 0, 0]) - 1) < 1e-6
    # The TCP is placed so that the measured TCP-frame centreline lands on the body centre.
    np.testing.assert_allclose(np.asarray(c["tcp_position"]) + R @ [0, 0, .08], c["body_center_world"], atol=1e-6)
    assert {k["sign"] for k in out["candidates"]} == {1., -1.} and "arbitrary" in out["ambiguity"][0]
    assert c["layer"] == "predicted" and c["workspace_box"] == "unchecked"
    assert any("not contact" in x for x in out["caveats"])


def test_support_normal_rotation_changes_the_target():
    flat = best(support_placement_candidates(visible_cylinder(), TCP, FLAT, along=[[1, 0, 0]]))
    n = np.array([0, .5, np.sqrt(.75)])
    tilted = best(support_placement_candidates(visible_cylinder(), TCP, {**FLAT, "normal": n}, along=[[1, 0, 0]]))
    np.testing.assert_allclose(tilted["body_center_world"], np.asarray(FLAT["centroid"]) + .04 * n, atol=2e-3)
    assert np.linalg.norm(np.subtract(tilted["tcp_position"], flat["tcp_position"])) > .01
    R = rotation_wxyz(tilted["tcp_quaternion_wxyz"])
    assert abs((R @ [0, 0, 1]) @ n) < 1e-6  # body axis lies in the oblique support plane


def test_body_offset_and_radius_change_the_tcp_candidate():
    a = best(support_placement_candidates(visible_cylinder(), TCP, FLAT, along=[[1, 0, 0]]))
    shifted = best(support_placement_candidates(visible_cylinder(centre=(.02, 0, .08)), TCP, FLAT, along=[[1, 0, 0]]))
    np.testing.assert_allclose(shifted["body_center_world"], a["body_center_world"], atol=2e-3)
    assert np.linalg.norm(np.subtract(shifted["tcp_position"], a["tcp_position"])) == pytest.approx(.02, abs=2e-3)
    thick = best(support_placement_candidates(visible_cylinder(radius=.05), TCP, FLAT, along=[[1, 0, 0]]))
    assert thick["body_center_world"][2] - a["body_center_world"][2] == pytest.approx(.01, abs=2e-3)


def test_tilt_candidates_raise_the_centre_by_the_axial_extent():
    out = support_placement_candidates(visible_cylinder(), TCP, FLAT, along=[[1, 0, 0]], tilts=(0., .3))
    tilted = [c for c in out["candidates"] if c["tilt"] == .3][0]
    expected = .25 + .04 * np.cos(.3) + .1 * np.sin(.3)
    assert tilted["body_center_world"][2] == pytest.approx(expected, abs=3e-3)


def test_rails_cradle_or_reject_by_radius_against_spacing():
    rails = [{"point": [.6, -.23, .25], "direction": [1, 0, 0]}, {"point": [.6, -.17, .25], "direction": [1, 0, 0]}]
    out = support_placement_candidates(visible_cylinder(), TCP, FLAT, rails=rails)
    c = best(out)
    assert "cradled" in c["mode"]
    assert c["body_center_world"][2] == pytest.approx(.25 + np.sqrt(.04 ** 2 - .03 ** 2), abs=2e-3)
    narrow = support_placement_candidates(visible_cylinder(radius=.025), TCP, FLAT, rails=rails)
    assert narrow["status"] == "unknown" and all("rail spacing" in r["reason"] for r in narrow["rejected"])


@pytest.mark.parametrize("kwargs,reason", [
    (dict(points=visible_cylinder()[:10]), "visible held points"),
    (dict(points=visible_cylinder(arc=np.deg2rad(20))), "radius/centreline unknown"),
    (dict(points=np.random.default_rng(0).normal(0, .03, (200, 3))), "body axis unknown"),
    (dict(points=visible_cylinder(arc=np.deg2rad(20)), radius=.04, radius_source="caller test"), "view direction"),
])
def test_insufficient_evidence_is_unknown(kwargs, reason):
    pts = kwargs.pop("points")
    out = support_placement_candidates(pts, TCP, FLAT, along=[[1, 0, 0]], **kwargs)
    assert out["status"] == "unknown" and reason in out["reason"] and "candidates" not in out


def test_caller_radius_needs_a_source_and_workspace_is_only_a_box_flag():
    with pytest.raises(ValueError, match="radius_source"):
        support_placement_candidates(visible_cylinder(), TCP, FLAT, radius=.04, view_direction_tcp=[1, 0, 0])
    out = support_placement_candidates(visible_cylinder(arc=np.deg2rad(40)), TCP, FLAT, along=[[1, 0, 0]],
                                       radius=.04, radius_source="initial upright cross-section fit",
                                       view_direction_tcp=[1, 0, 0], workspace=([0, -.5, 0], [.5, .5, .9]))
    assert out["cross_section"]["radius_source"].startswith("caller")
    assert out["candidates"][0]["workspace_box"] in ("inside", "outside")
    assert all(c["workspace_box"] == "outside" for c in out["candidates"]) or out["candidates"][0]["workspace_box"] == "inside"


def test_contract_namespace_exports_the_helpers(tmp_path, monkeypatch):
    from test_checked_execution_contract import session
    replay, scope, driver, api = session(tmp_path, monkeypatch)
    assert scope["support_placement_candidates"] is support_placement_candidates
    assert scope["cylinder_cross_section"] is cylinder_cross_section
    replay.close()
    assert "support_placement_candidates" not in scope
