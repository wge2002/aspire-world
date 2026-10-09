"""The opt-in must be invisible unless it is set. No simulator or model calls.

Old A/B/C behavior and rendered prompts are the thing being protected here: an
unflagged case must render byte-for-byte what it rendered before this revision
existed, and a flagged case must differ by exactly one inserted paragraph.
"""
import importlib.util
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]

# The campaign and protocol modules are loaded by location below, and each one
# inserts only the sibling script directory it happens to expect. The campaign
# imports `simple_world_profile`, which lives in `scripts/libero` rather than
# `scripts/common`, so collecting this file needed a PYTHONPATH that the
# documented command did not carry. Setting the same two entries here is what
# that PYTHONPATH was doing, so the plain command works on its own.
for _scripts in ("scripts/libero", "scripts/common"):
    _path = str(ROOT / _scripts)
    if _path not in sys.path:
        sys.path.insert(0, _path)


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


campaign = load("native_world_campaign", ROOT / "scripts/libero/native_world_campaign.py")
protocol = load("native_world_protocol", ROOT / "scripts/libero/native_world_protocol.py")
audit = load("world_use_audit_compat", ROOT / "cap/world_model/world_use_audit.py")

FLAG = {"world_use_revision": "r1"}


def case(**overrides):
    base = {"id": "test-cell", "suite": "libero_goal_swap",
            "task": "put_the_bowl_on_the_plate", "gpu": 3, "condition": "C",
            "profile": "judgment", "skill_library_dir": ".claude/libero/skills"}
    base.update(overrides)
    return {k: v for k, v in base.items() if v is not None}


# --- the predicate --------------------------------------------------------

TRUTH_TABLE = [
    ({}, False),
    ({"profile": "judgment", "condition": "C"}, False),
    ({"world_use_revision": "r1", "profile": "judgment", "condition": "C"}, True),
    ({"world_use_revision": "r1", "profile": "judgment", "condition": "B"}, False),
    ({"world_use_revision": "r1", "profile": "simple", "condition": "C"}, False),
    ({"world_use_revision": "r1", "condition": "C"}, False),
    ({"world_use_revision": "r2", "profile": "judgment", "condition": "C"}, False),
]


@pytest.mark.parametrize("subject,expected", TRUTH_TABLE)
def test_opt_in_is_restricted_to_new_c_judgment(subject, expected):
    assert audit.enabled(subject) is expected
    # The campaign spells the predicate locally so rendering needs no import of
    # the runtime; the two definitions must not drift apart.
    assert campaign.world_use_enabled(subject) is expected
    assert (protocol.world_use(subject) is not None) is expected


def test_a_flagged_case_that_is_not_new_c_judgment_is_refused_with_a_reason():
    assert audit.revision_errors({}) == []
    assert audit.revision_errors({"profile": "judgment", "condition": "C"}) == []
    for subject, fragment in [
            ({**FLAG, "profile": "simple", "condition": "C"}, "requires profile"),
            ({**FLAG, "profile": "judgment", "condition": "B"}, "restricted to condition C"),
            ({"world_use_revision": "r9", "profile": "judgment", "condition": "C"},
             "unknown world_use_revision")]:
        problems = audit.revision_errors(subject)
        assert problems and any(fragment in p for p in problems)


# --- rendered prompts -----------------------------------------------------

def test_unflagged_judgment_world_section_is_unchanged():
    assert campaign.world_section(case()) == campaign.JUDGMENT_WORLD_SECTION
    assert campaign.WORLD_USE_CLARIFICATION not in campaign.world_section(case())


def test_flagged_judgment_world_section_appends_exactly_one_paragraph():
    flagged = campaign.world_section(case(**FLAG))
    assert flagged == campaign.JUDGMENT_WORLD_SECTION + campaign.WORLD_USE_CLARIFICATION


def test_legacy_adapter_section_is_untouched_by_the_flag():
    legacy = case(profile=None, condition="B")
    assert campaign.world_section(legacy) == campaign.world_section({**legacy, **FLAG})


def test_flagged_c_prompt_differs_by_exactly_the_one_insertion():
    unflagged = campaign.worker_prompt(case(), ROOT)
    flagged = campaign.worker_prompt(case(**FLAG), ROOT)
    assert campaign.WORLD_USE_CLARIFICATION not in unflagged
    assert unflagged.count(campaign.JUDGMENT_WORLD_SECTION) == 1
    assert flagged == unflagged.replace(
        campaign.JUDGMENT_WORLD_SECTION,
        campaign.JUDGMENT_WORLD_SECTION + campaign.WORLD_USE_CLARIFICATION)


@pytest.mark.parametrize("subject", [
    case(condition="A", profile=None),
    case(condition="B", profile=None),
    case(condition="C", profile=None),
    case(condition="B", profile="judgment"),
])
def test_old_conditions_render_identically_with_or_without_the_flag(subject):
    """An A/B/C cell that is not NEW C judgment cannot be changed by the flag."""
    assert campaign.worker_prompt(subject, ROOT) == campaign.worker_prompt(
        {**subject, **FLAG}, ROOT)


def test_unflagged_judgment_prompt_still_carries_its_own_contract():
    text = campaign.worker_prompt(case(), ROOT)
    assert "query(name, **kwargs)" in text
    assert campaign.FALLBACK_JUDGMENT in text


# --- protocol wiring ------------------------------------------------------

def test_only_ordinary_development_trials_are_audited():
    assert set(protocol.TRIAL_PHASES) == {"smoke", "initial", "repair"}
    assert not set(protocol.TRIAL_PHASES) & protocol.NON_TRIAL_PHASES


def test_the_protocol_loads_the_same_reader_this_test_loads():
    module = protocol.world_use(case(**FLAG))
    assert module is not None
    assert module.REVISION == audit.REVISION == "r1"
    assert module.STATUSES == audit.STATUSES
