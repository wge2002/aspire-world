"""CPU tests: OBB axis convention (columns of Open3D R are box axes) and long-axis semantics.

Pure math plus the real Open3D OBB on synthetic box points; no robot, model,
perception service or simulator.
"""
from __future__ import annotations

import numpy as np
import pytest

from aspire.sim.cap.integrations.franka.common import get_oriented_bounding_box_from_3d_points
from aspire.sim.cap.world_model.geometry_queries import axis_tilt_from_up, box_axes


def confusing_rotation():
    """A proper rotation whose long axis (column 0) is nearly horizontal (R[2,0] ~ -.087)
    while R[0,2] ~ .893: reading row 0 as the axis would look strongly tilted."""
    u = np.array([0.3, np.sqrt(1 - 0.3 ** 2 - 0.087 ** 2), -0.087])
    e1 = np.cross(u, [0, 0, 1.])
    e1 /= np.linalg.norm(e1)
    e2 = np.cross(u, e1)
    amplitude = np.hypot(e1[0], e2[0])
    phi = np.arctan2(e2[0], e1[0]) + np.arccos(0.893 / amplitude)
    v = np.cos(phi) * e1 + np.sin(phi) * e2  # column 2, unit, orthogonal to u
    R = np.column_stack([u, np.cross(v, u), v])
    assert np.allclose(R.T @ R, np.eye(3)) and np.isclose(np.linalg.det(R), 1)
    assert np.isclose(R[0, 2], 0.893) and np.isclose(R[2, 0], -0.087)
    return R


def box_points(R, extent, center=(0.1, -0.2, 0.9), n=4000, seed=0):
    rng = np.random.default_rng(seed)
    local = (rng.random((n, 3)) - 0.5) * np.asarray(extent)
    return local @ R.T + np.asarray(center)


def test_lying_box_with_confusing_rotation_is_horizontal_by_columns():
    R = confusing_rotation()
    result = box_axes(R, [0.20, 0.05, 0.04])
    assert result["long_axis_status"] == "known" and result["long_axis_index"] == 0
    assert np.isclose(abs(np.dot(result["long_axis"], R[:, 0])), 1)
    assert result["long_axis_orientation"] == "horizontal"
    assert np.degrees(result["long_axis_tilt_from_up"]) > 85
    assert np.allclose(result["axes"], R.T)  # rows of `axes` are the columns of R
    # The row/column mix-up this interface exists to prevent: row 0 is not horizontal.
    assert axis_tilt_from_up(R[0])[1] != "horizontal"


def test_real_open3d_obb_keeps_center_extent_R_and_adds_documented_axes():
    R = confusing_rotation()
    extent = np.array([0.20, 0.05, 0.04])
    obb = get_oriented_bounding_box_from_3d_points(box_points(R, extent))
    assert {"center", "extent", "R"} <= set(obb)
    assert isinstance(obb["R"], np.ndarray) and obb["R"].shape == (3, 3)
    assert np.allclose(obb["center"], [0.1, -0.2, 0.9], atol=0.01)
    assert np.isclose(max(obb["extent"]), 0.20, atol=0.02)
    assert obb["long_axis_status"] == "known" and obb["long_axis_orientation"] == "horizontal"
    i = obb["long_axis_index"]
    assert np.isclose(obb["extent"][i], max(obb["extent"]))
    assert np.allclose(np.abs(obb["long_axis"]), np.abs(obb["R"][:, i]))
    assert np.allclose(obb["axes"], obb["R"].T)
    assert abs(np.dot(obb["long_axis"], R[:, 0])) > 0.99
    assert "columns" in obb["axis_convention"] and obb["coordinate_frame"] == "same frame as the input points"
    assert any("visible surface only" in c for c in obb["caveats"])


@pytest.mark.parametrize("tilt_deg,label", [(0, "vertical"), (10, "vertical"), (45, "oblique"),
                                            (88, "horizontal")])
def test_upright_tilted_and_lying_boxes(tilt_deg, label):
    a = np.radians(tilt_deg)
    # Long axis (column 2 here) tilted from +z towards +x by `a`.
    R = np.array([[np.cos(a), 0, np.sin(a)], [0, 1, 0], [-np.sin(a), 0, np.cos(a)]])
    pure = box_axes(R, [0.05, 0.04, 0.22])
    assert pure["long_axis_orientation"] == label and pure["long_axis_index"] == 2
    obb = get_oriented_bounding_box_from_3d_points(box_points(R, [0.05, 0.04, 0.22], seed=tilt_deg))
    assert obb["long_axis_status"] == "known" and obb["long_axis_orientation"] == label


def test_near_equal_extents_are_ambiguous_and_imply_no_orientation():
    R = confusing_rotation()
    pure = box_axes(R, [0.050, 0.047, 0.045])
    assert pure["long_axis_status"] == "ambiguous" and pure["long_axis"] is None
    assert pure["long_axis_orientation"] is None and "ratio" in pure["reason"]
    obb = get_oriented_bounding_box_from_3d_points(box_points(R, [0.050, 0.047, 0.045]))
    assert obb["long_axis_status"] == "ambiguous" and obb["long_axis"] is None
    assert obb["axes"] is not None  # axes stay available; only the long-axis claim is withheld


def test_custom_up_and_invalid_inputs_are_unknown():
    R = confusing_rotation()
    # With up along the long axis itself, the same box is vertical in that frame.
    assert box_axes(R, [0.2, 0.05, 0.04], up=R[:, 0])["long_axis_orientation"] == "vertical"
    assert box_axes(np.ones((3, 3)), [0.2, 0.05, 0.04])["long_axis_status"] == "unknown"
    assert box_axes(R, [0.2, 0.05])["long_axis_status"] == "unknown"
    assert box_axes(R, [0.2, 0.05, 0.04], up=(0, 0, 0))["long_axis_status"] == "unknown"
    assert box_axes(R, [0, 0, 0])["long_axis_status"] == "unknown"
