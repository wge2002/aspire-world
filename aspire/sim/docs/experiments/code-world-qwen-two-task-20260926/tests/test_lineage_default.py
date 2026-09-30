#!/usr/bin/env python3
"""The `c_lineage` knob must not move any previously staged study's C prompt.

`executable_world_profile.section()` gained two optional case keys so that a task
with no prior C bundle can be staged as a first generation. Every study staged
before those keys existed passes neither, so the default must render byte for
byte what the pre-edit source rendered. The oracle is not a stored string: it is
the actual pre-edit module frozen into the Sep-24 full-study runtime, imported
here under a separate name and called with the same case.

Run from the engineering checkout root:
  .venv-libero/bin/python3 -m pytest -q \
    docs/experiments/code-world-qwen-two-task-20260926/tests/test_lineage_default.py
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ENGINEERING = Path("/mnt/home/gewang/code/ASPIRE-world-revision-20260910-01a0894b/aspire/sim")
CURRENT = ENGINEERING / "scripts/libero/executable_world_profile.py"
# The byte copy the Sep-24 full run was staged and launched from. Read only; it
# is preserved experiment evidence and must never be written by this test.
PRE_EDIT = Path("/mnt/home/gewang/code/ASPIRE-code-world-qwen-full-20260924"
                "/cells/bowl_C_full/aspire/sim/scripts/libero/executable_world_profile.py")

# Exactly the keys the full study's staged case carries for this renderer.
FULL_STUDY_CASE = {
    "id": "bowl_C_full", "condition": "C", "profile": "judgment",
    "executable_world_revision": "r1", "c_arm": "full",
    "task": "put_the_bowl_on_the_plate",
    "c_starter": "docs/experiments/code-world-qwen-full-20260924/cells/bowl_C_full/prior_c",
}


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def pair():
    if not PRE_EDIT.is_file():
        pytest.skip(f"pre-edit oracle not available: {PRE_EDIT}")
    return load(PRE_EDIT, "pre_edit_profile"), load(CURRENT, "current_profile")


@pytest.mark.parametrize("arm", ("full", "no_self_eval", "no_rehearsal"))
def test_absent_key_renders_pre_edit_bytes(pair, arm):
    """No `c_lineage` at all -- the state of every already-staged study."""
    before, after = pair
    case = {**FULL_STUDY_CASE, "c_arm": arm}
    assert "c_lineage" not in case
    assert after.section(case) == before.section(case)


@pytest.mark.parametrize("arm", ("full", "no_self_eval", "no_rehearsal"))
def test_explicit_repair_renders_pre_edit_bytes(pair, arm):
    before, after = pair
    case = {**FULL_STUDY_CASE, "c_arm": arm, "c_lineage": "repair"}
    assert after.section(case) == before.section(case)


def test_repair_still_interpolates_its_own_starter(pair):
    """The starter path is still the cell's, not a constant baked into the text."""
    _, after = pair
    case = {**FULL_STUDY_CASE, "c_starter": "docs/experiments/other-study/cells/x/prior_c"}
    text = after.section(case)
    assert case["c_starter"] in text
    assert FULL_STUDY_CASE["c_starter"] not in text


def test_fresh_grants_no_prior_input(pair):
    _, after = pair
    case = {k: v for k, v in FULL_STUDY_CASE.items() if k != "c_starter"}
    text = after.section({**case, "c_lineage": "fresh"})
    assert "FRESH first generation" in text
    assert "CONTINUATION/REPAIR" not in text
    # No prior-input exception is granted, and no starter path is named.
    assert "prior_c" not in text
    assert "The sole permitted prior input" not in text
    assert "applies in full, with no\nexception" in text
    # The ban on the other partition and on cross-arm reuse survives intact.
    assert "seeds 1–50 remain unavailable" in text
    assert "Do not\ncopy A solutions, inspect historical held-out data, or modify framework." in text


def test_fresh_heading_does_not_claim_a_repair(pair):
    _, after = pair
    case = {k: v for k, v in FULL_STUDY_CASE.items() if k != "c_starter"}
    assert "## Executable C fresh generation and ablation: full" in after.section(
        {**case, "c_lineage": "fresh"})
    assert "## Executable C repair and ablation: full" in after.section(FULL_STUDY_CASE)


def test_fresh_keeps_the_rest_of_the_contract(pair):
    """Only the lineage paragraph, heading and interface path may differ."""
    _, after = pair
    fresh_case = {k: v for k, v in FULL_STUDY_CASE.items() if k != "c_starter"}
    fresh = after.section({**fresh_case, "c_lineage": "fresh"})
    repair = after.section(FULL_STUDY_CASE)
    for required in ("All arms must use real observations rather than commanded targets",
                     "Arm switches are frozen.",
                     "Offline check (only the rehearsal-enabled arms):",
                     "The report's `status` says how the check ended",
                     "Everything else below retains the original native failure-by-failure Fix Loop,"
                     "\n51–65 development budget, tested-bundle selection and outer frozen evaluation."):
            assert required in fresh, required
            assert required in repair, required


def test_lineage_and_starter_must_agree(pair):
    """A fresh cell cannot smuggle a starter; a repair cell cannot lose one."""
    _, after = pair
    with pytest.raises(ValueError, match="must not declare a c_starter"):
        after.section({**FULL_STUDY_CASE, "c_lineage": "fresh"})
    with pytest.raises(ValueError, match="must declare its c_starter"):
        after.section({k: v for k, v in FULL_STUDY_CASE.items() if k != "c_starter"})
    with pytest.raises(ValueError, match="invalid C lineage"):
        after.section({**FULL_STUDY_CASE, "c_lineage": "continuation"})


def test_interface_doc_defaults_to_the_original_pin(pair):
    before, after = pair
    assert after.interface_doc(FULL_STUDY_CASE) == before.INTERFACE
    staged = "docs/experiments/code-world-qwen-two-task-20260926/NATIVE_WORLD_INTERFACE.md"
    text = after.section({**FULL_STUDY_CASE, "world_interface_doc": staged})
    assert f"Read `{staged}` in full before editing." in text
    assert before.INTERFACE not in text


def test_non_c_cells_are_still_refused(pair):
    _, after = pair
    for bad in ({"condition": "A"}, {"profile": "legacy_native"}, {"c_arm": "bogus"}):
        with pytest.raises(ValueError):
            after.validate({**FULL_STUDY_CASE, **bad})
    # An A cell with no executable-world opt-in stays untouched by all of this.
    after.validate({"condition": "A", "profile": "legacy_native"})
