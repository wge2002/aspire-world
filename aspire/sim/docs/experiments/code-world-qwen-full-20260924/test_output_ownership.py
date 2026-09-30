"""Actual filesystem and PreToolUse payload regressions for output ownership."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "support"))
import output_ownership


@pytest.fixture
def cell(tmp_path):
    sim, control = tmp_path / "runtime", tmp_path / "control"
    sim.mkdir()
    (control / "outputs/working_codes").mkdir(parents=True)
    (sim / "outputs").symlink_to(control / "outputs", target_is_directory=True)
    case = {"sim": str(sim), "control": str(control)}
    return sim, control, case


def test_stale_control_output_symlink_is_not_a_canonical_root(cell, tmp_path):
    sim, control, case = cell
    old = tmp_path / "old-output"
    (control / "outputs").rename(old)
    (control / "outputs").symlink_to(old, target_is_directory=True)
    with pytest.raises(output_ownership.OwnershipError):
        output_ownership.verify(case, sim)


def test_runtime_link_must_target_root_exactly(cell):
    sim, control, case = cell
    nested = control / "outputs/nested"
    (nested / "working_codes").mkdir(parents=True)
    (sim / "outputs").unlink()
    (sim / "outputs").symlink_to(nested, target_is_directory=True)
    with pytest.raises(output_ownership.OwnershipError):
        output_ownership.verify(case, sim)


@pytest.mark.parametrize("target,allowed", [
    ("own_image", True), ("own_code", True), ("other_cell", False),
    ("heldout", False), ("stale_runtime", False), ("stale_control", False),
])
def test_real_read_hook(cell, tmp_path, target, allowed):
    sim, control, case = cell
    path = sim / "outputs/libero_fix_loop/suite/task/scene_snapshot.jpg"
    if target == "own_code":
        path = sim / "outputs/libero_fix_loop/suite/task/initial_code.py"
    elif target == "other_cell":
        path = tmp_path / "other-cell/outputs/image.jpg"
    elif target == "heldout":
        path = control / "heldout/seed_01/image.jpg"
    elif target in {"stale_runtime", "stale_control"}:
        old = tmp_path / "old-output"
        (old / "working_codes").mkdir(parents=True)
        link = sim / "outputs" if target == "stale_runtime" else control / "outputs"
        if target == "stale_control":
            link.rename(tmp_path / "preserved-new-output")
        else:
            link.unlink()
        link.symlink_to(old, target_is_directory=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("synthetic fixture")
    case_path = tmp_path / "case.json"
    case_path.write_text(json.dumps(case))
    audit = tmp_path / "audit.jsonl"
    run = subprocess.run([sys.executable, str(HERE / "support/cell_read_guard.py"),
                          "--case", str(case_path), "--audit", str(audit)],
                         input=json.dumps({"tool_name": "Read", "cwd": str(sim),
                                           "tool_input": {"file_path": str(path)}}),
                         text=True, capture_output=True)
    assert run.returncode == (0 if allowed else 2), run.stderr
    if allowed:
        assert json.loads(audit.read_text())["denied"] is False
