# SPDX-License-Identifier: MIT
"""Focused CPU tests for the 2026-10-03 pipeline repair.

Three bug chains are covered:
  A  cap/world_model/evidence_state.py  — numpy.bool_ coercion in predicate()
  B  native_world_fixloop_state.py      — validate_admission() extracted;
                                          last-diagnostic reservation;
                                          TerminalBlocker for irrecoverable seeds
  C  native_world_protocol.py           — validate_admission before screening;
                                          frozen-initial baseline exception;
                                          check() always returns decision_type

No simulator, no model requests, no GPU.  Offline screening is faked at the
`executable_world_profile.checks` boundary so admission logic can be tested
without the screening implementation.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

SIM = Path(__file__).resolve().parents[1]
for extra in (SIM, SIM / "scripts/common", SIM / "scripts/libero"):
    sys.path.insert(0, str(extra))

import native_world_fixloop_state as fixloop
import native_world_protocol as protocol
from native_world_fixloop_state import (
    NativeWorldState, ProtocolError, TerminalBlocker, bundle_identity, code_hash
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

IDENTITY = {"dev_seeds": [51, 52, 53], "profile": "judgment"}
POLICY_HASH = "a" * 64  # valid-length digest placeholder


def make_state(tmp_path) -> NativeWorldState:
    return NativeWorldState(tmp_path / "task", dict(IDENTITY))


def _admit(state, phase, seed, *, status="complete", completed=0, digest=None):
    """Inject one settled trial record, bypassing admission guards."""
    d = digest or POLICY_HASH
    bundle = {"policy": d}
    attempt = state.retries_used(seed) + 1 if phase != "snapshot" else 1
    row = {
        "phase": phase, "seed": seed, "attempt": attempt,
        "status": status,
        "directory": f"development/{phase}/seed_{seed}/attempt_{attempt}",
        "spends_retry": phase in fixloop.RETRY_PHASES,
        "executed": True, "bundle_sha256": d, "bundle": bundle,
        "sources": {"policy": "p"},
        "sandbox_rc": 0 if status == "complete" else 1,
        "reward": float(completed), "task_completed": completed,
        "trial_dir": f"development/{phase}/seed_{seed}/attempt_{attempt}",
        "process_exit_code": 0, "error": "",
    }
    state.data["trials"].append(row)
    state.save()
    return row


def _snapshot(state):
    row = _admit(state, "snapshot", 51, status="complete")
    return row


# ===========================================================================
# A — numpy.bool_ coercion in evidence_state.predicate()
# ===========================================================================

def _make_state_es():
    from cap.world_model.evidence_state import EvidenceState
    return EvidenceState(lambda ids, *, fresh=True: True)


def test_native_bool_accepted():
    es = _make_state_es()
    es.set("x", 1.0, evidence_ids=[1])
    clause = es.predicate("p", lambda v: v > 0.5, ("x",))
    assert clause["verdict"] == "true"


def test_numpy_bool_scalar_accepted_and_coerced():
    """numpy.bool_ must be accepted as a valid return type and coerced to bool."""
    import numpy as np
    es = _make_state_es()
    es.set("x", 1.0, evidence_ids=[1])
    clause = es.predicate("p", lambda v: np.bool_(v > 0.5), ("x",))
    assert clause["verdict"] == "true"
    clause2 = es.predicate("p2", lambda v: np.bool_(v < 0.0), ("x",))
    assert clause2["verdict"] == "false"


def test_numpy_bool_false_coerced_correctly():
    import numpy as np
    es = _make_state_es()
    es.set("x", 0.1, evidence_ids=[1])
    clause = es.predicate("p", lambda v: np.bool_(v > 0.5), ("x",))
    assert clause["verdict"] == "false"


def test_int_return_rejected():
    es = _make_state_es()
    es.set("x", 1.0, evidence_ids=[1])
    with pytest.raises(TypeError, match="predicate must return a bool"):
        es.predicate("p", lambda v: 1, ("x",))


def test_float_return_rejected():
    es = _make_state_es()
    es.set("x", 1.0, evidence_ids=[1])
    with pytest.raises(TypeError, match="predicate must return a bool"):
        es.predicate("p", lambda v: 1.0, ("x",))


def test_string_return_rejected():
    es = _make_state_es()
    es.set("x", 1.0, evidence_ids=[1])
    with pytest.raises(TypeError, match="predicate must return a bool"):
        es.predicate("p", lambda v: "yes", ("x",))


def test_array_return_rejected():
    import numpy as np
    es = _make_state_es()
    es.set("x", 1.0, evidence_ids=[1])
    with pytest.raises(TypeError, match="predicate must return a bool"):
        es.predicate("p", lambda v: np.array([True]), ("x",))


# ===========================================================================
# B — validate_admission extracted, last-diagnostic reservation, TerminalBlocker
# ===========================================================================

class TestValidateAdmission:
    """validate_admission is non-mutating and callable independently."""

    def test_valid_initial_does_not_raise(self, tmp_path):
        state = make_state(tmp_path)
        _snapshot(state)
        # Should not raise
        state.validate_admission("initial", 51, POLICY_HASH)

    def test_frozen_cell_raises(self, tmp_path):
        state = make_state(tmp_path)
        state.data["stage1_complete"] = True
        state.save()
        with pytest.raises(ProtocolError, match="frozen"):
            state.validate_admission("initial", 51, POLICY_HASH)

    def test_unknown_phase_raises(self, tmp_path):
        state = make_state(tmp_path)
        with pytest.raises(ProtocolError, match="unknown phase"):
            state.validate_admission("foobar", 51, POLICY_HASH)

    def test_out_of_partition_seed_raises(self, tmp_path):
        state = make_state(tmp_path)
        with pytest.raises(ProtocolError, match="outside the development partition"):
            state.validate_admission("initial", 99, POLICY_HASH)

    def test_running_trial_blocks_admission(self, tmp_path):
        state = make_state(tmp_path)
        # Inject a running record
        state.data["trials"].append({
            "phase": "initial", "seed": 51, "attempt": 1, "status": "running",
            "directory": "development/initial/seed_51/attempt_1",
            "spends_retry": True, "executed": True,
            "bundle_sha256": POLICY_HASH, "bundle": {"policy": POLICY_HASH},
            "sources": {"policy": "p"},
        })
        state.save()
        with pytest.raises(ProtocolError, match="unresolved infrastructure evidence"):
            state.validate_admission("initial", 52, POLICY_HASH)

    def test_begin_trial_delegates_to_validate(self, tmp_path):
        """begin_trial still rejects what validate_admission rejects."""
        state = make_state(tmp_path)
        # No snapshot yet: initial should be refused
        with pytest.raises(ProtocolError, match="scene snapshot"):
            state.begin_trial("initial", 51, {"policy": POLICY_HASH}, {"policy": "p"})


class TestLastDiagnosticReservation:
    """Diagnostic sessions must not consume the last attempt when no graded evidence exists."""

    def test_diagnostic_blocked_when_last_attempt_no_graded_evidence(self, tmp_path):
        state = make_state(tmp_path)
        _snapshot(state)
        # Use 2 of 3 attempts on infrastructure failures
        for _ in range(fixloop.RETRY_LIMIT - 1):
            row = {"phase": "initial", "seed": 52, "attempt": len(state.retries(52)) + 1,
                   "status": "infrastructure_error",
                   "directory": f"development/initial/seed_52/attempt_{len(state.retries(52)) + 1}",
                   "spends_retry": True, "executed": True,
                   "bundle_sha256": POLICY_HASH, "bundle": {"policy": POLICY_HASH},
                   "sources": {"policy": "p"},
                   "process_exit_code": 1, "error": "watchdog"}
            state.data["trials"].append(row)
        state.save()
        assert state.retries_remaining(52) == 1
        assert not state.has_graded_evidence(52)
        with pytest.raises(ProtocolError, match="reserve it for a real graded trial"):
            state.validate_admission("diagnostic", 52, POLICY_HASH)

    def test_diagnostic_allowed_when_graded_evidence_exists(self, tmp_path):
        """Crashing smoke is graded evidence; diagnostics proceed after it."""
        state = make_state(tmp_path)
        _snapshot(state)
        _admit(state, "initial", 51, status="complete", completed=1)
        # Burn seed 52 budget: 2 infra failures then a crashing smoke
        for _ in range(2):
            row = {"phase": "initial", "seed": 52, "attempt": len(state.retries(52)) + 1,
                   "status": "infrastructure_error",
                   "directory": f"development/initial/seed_52/attempt_{len(state.retries(52)) + 1}",
                   "spends_retry": True, "executed": True,
                   "bundle_sha256": POLICY_HASH, "bundle": {"policy": POLICY_HASH},
                   "sources": {"policy": "p"},
                   "process_exit_code": 1, "error": "watchdog"}
            state.data["trials"].append(row)
        state.save()
        # Add a crashing smoke (graded, sandbox_rc=1)
        smoke = {"phase": "smoke", "seed": 51,
                 "attempt": len(state.retries(51)) + 1, "status": "complete",
                 "directory": "development/smoke/seed_51/attempt_2",
                 "spends_retry": True, "executed": True, "bundle_sha256": POLICY_HASH,
                 "bundle": {"policy": POLICY_HASH}, "sources": {"policy": "p"},
                 "sandbox_rc": 1, "reward": 0.0, "task_completed": 0,
                 "trial_dir": "t", "process_exit_code": 0, "error": ""}
        state.data["trials"].append(smoke)
        state.save()
        # Seed 51 now has graded evidence; last-attempt diagnostic should not be blocked
        assert state.has_graded_evidence(51)
        # Should not raise (retries_remaining might be > 1 after snapshot + initial)
        state.validate_admission("diagnostic", 51, POLICY_HASH)

    def test_diagnostic_allowed_when_retries_remaining_gt_1(self, tmp_path):
        """With 2+ attempts left and no graded evidence, diagnostic is still allowed."""
        state = make_state(tmp_path)
        _snapshot(state)
        assert state.retries_remaining(52) == fixloop.RETRY_LIMIT  # 3
        assert not state.has_graded_evidence(52)
        # Two attempts remaining: reservation doesn't apply
        state.validate_admission("diagnostic", 52, POLICY_HASH)


class TestTerminalBlocker:
    def test_terminal_blocker_is_protocol_error_subclass(self):
        tb = TerminalBlocker("bad", seeds=[52], details=["foo"])
        assert isinstance(tb, ProtocolError)
        assert tb.seeds == [52]
        assert tb.recoverable is False

    def test_terminal_blocker_as_dict_has_expected_keys(self):
        tb = TerminalBlocker("bad", seeds=[52, 53], details=["a", "b"])
        d = tb.as_dict()
        assert d["terminal"] is True
        assert d["recoverable"] is False
        assert d["seeds"] == [52, 53]
        assert d["reason"] == "bad"

    def test_repair_raises_terminal_blocker_for_diagnostic_exhausted_seeds(self, tmp_path):
        """Seeds exhausted only by diagnostic sessions raise TerminalBlocker."""
        state = make_state(tmp_path)
        _snapshot(state)
        _admit(state, "initial", 51, status="complete", completed=1)
        _admit(state, "initial", 53, status="complete", completed=1)
        # Inject 3 diagnostic rows on seed 52 directly (historical ledger simulation)
        for i in range(fixloop.RETRY_LIMIT):
            state.data["trials"].append({
                "phase": "diagnostic", "seed": 52, "attempt": i + 1,
                "status": "diagnostic_program_error",
                "directory": f"development/diagnostic/seed_52/attempt_{i + 1}",
                "spends_retry": True, "executed": True,
                "bundle_sha256": POLICY_HASH, "bundle": {"policy": POLICY_HASH},
                "sources": {"policy": "p"},
                "sandbox_rc": 0, "reward": 0.0, "task_completed": 0,
                "trial_dir": f"development/diagnostic/seed_52/attempt_{i + 1}",
                "process_exit_code": 0, "error": "",
            })
        state.save()
        assert state.retries_remaining(52) == 0
        assert not state.has_graded_evidence(52)
        with pytest.raises(TerminalBlocker, match="no initial evidence and no attempts left"):
            state.begin_trial("repair", 51, {"policy": POLICY_HASH}, {"policy": "p"})

    def test_terminal_blocker_seeds_attribute_is_correct(self, tmp_path):
        state = make_state(tmp_path)
        _snapshot(state)
        _admit(state, "initial", 51, status="complete", completed=1)
        _admit(state, "initial", 53, status="complete", completed=1)
        # Exhaust seed 52 via infra errors (no graded evidence)
        for i in range(fixloop.RETRY_LIMIT):
            state.data["trials"].append({
                "phase": "initial", "seed": 52, "attempt": i + 1,
                "status": "infrastructure_error",
                "directory": f"development/initial/seed_52/attempt_{i + 1}",
                "spends_retry": True, "executed": True,
                "bundle_sha256": POLICY_HASH, "bundle": {"policy": POLICY_HASH},
                "sources": {"policy": "p"},
                "process_exit_code": 1, "error": "watchdog",
            })
        state.save()
        with pytest.raises(TerminalBlocker) as exc_info:
            state.begin_trial("repair", 51, {"policy": POLICY_HASH}, {"policy": "p"})
        assert exc_info.value.seeds == [52]

    def test_graded_smoke_exhaustion_remains_recoverable(self, tmp_path):
        """Three crashing smokes = graded evidence; repair of other seeds proceeds."""
        state = make_state(tmp_path)
        _snapshot(state)
        for i in range(fixloop.RETRY_LIMIT):
            state.data["trials"].append({
                "phase": "smoke", "seed": 51, "attempt": i + 1,
                "status": "complete",
                "directory": f"development/smoke/seed_51/attempt_{i + 1}",
                "spends_retry": True, "executed": True,
                "bundle_sha256": POLICY_HASH, "bundle": {"policy": POLICY_HASH},
                "sources": {"policy": "p"},
                "sandbox_rc": 1, "reward": 0.0, "task_completed": 0,
                "trial_dir": f"development/smoke/seed_51/attempt_{i + 1}",
                "process_exit_code": 0, "error": "",
            })
        state.save()
        _admit(state, "initial", 52, status="complete", completed=1)
        _admit(state, "initial", 53, status="complete", completed=1)
        # seed 51 exhausted with graded evidence → not a TerminalBlocker
        # Repair on seed 52 should proceed
        record = state.begin_trial("repair", 52, {"policy": POLICY_HASH}, {"policy": "p"})
        assert record["phase"] == "repair"


# ===========================================================================
# C — validate_admission before screening; frozen-initial baseline exception;
#     check() always returns decision_type
# ===========================================================================

def _make_protocol_harness(tmp_path, *, condition="A"):
    """Minimal protocol harness: fakes verify_runtime and run_replay."""
    import test_native_world_fixloop as fixtures
    import contextlib
    stack = contextlib.ExitStack()
    harness = fixtures.Harness(stack, condition=condition)
    return harness, stack


class TestValidateAdmissionBeforeScreening:
    """validate_admission must fire before offline screening is called."""

    def test_exhausted_seed_refuses_before_screening(self, tmp_path):
        """A seed with no remaining budget is refused before screening starts.

        Previously, screening ran first, then begin_trial refused — consuming
        screening time on a candidate that could never be admitted.
        """
        import test_native_world_fixloop as fixtures
        import contextlib
        with contextlib.ExitStack() as stack:
            harness = fixtures.Harness(stack, condition="A")
            harness.snapshot()
            for _ in range(fixloop.RETRY_LIMIT):
                harness.outcome(51, success=False, crash=True)
                harness.trial("smoke", 51)
            # Now try another initial on exhausted seed 51 — must refuse before screening
            screening_called = []
            def fake_checks(*a, **kw):
                screening_called.append(True)
                return {"status": "checked"}
            with patch.object(protocol, "run_replay", harness.replay):
                with pytest.raises(ProtocolError, match="spent all"):
                    # Patch directly on the protocol module's checks call-site
                    harness.case["executable_world_revision"] = "r1"
                    harness.case["c_arm"] = "c_arm"
                    with patch("executable_world_profile.checks", fake_checks):
                        harness.trial("initial", 51)
            # Screening must NOT have been called
            assert not screening_called


class TestMixedInfraBlock:
    """A screening report with both authored error and infrastructure failure stays blocked."""

    def test_mixed_report_cannot_admit_baseline(self, tmp_path):
        """Real report statuses block the baseline even when aggregate status rejects."""
        import test_native_world_fixloop as fixtures
        import contextlib
        with contextlib.ExitStack() as stack:
            harness = fixtures.Harness(stack, condition="A")
            harness.snapshot()
            harness.outcome(51, success=False)
            harness.trial("initial", 51)  # seed 51 now has a real failure (initial evidence)
            harness.case["executable_world_revision"] = "r1"
            harness.case["c_arm"] = "c_arm"
            # Mixed screening: authored error + infrastructure failure
            mixed_screening = {
                "status": "rejected",
                "directory": "/tmp/x",
                "conclusions": ["authored_error"],
                "retryable": True,
                "cached": False,
                "identity": {},
                "reports": [{"status": "program_error"},
                            {"status": "infrastructure_error"}],
            }
            with patch("executable_world_profile.checks", return_value=mixed_screening):
                result = harness.trial("initial", 52)
                assert result["status"] == "screening_blocked"
                assert result["screening"]["reports"] == mixed_screening["reports"]
            # Confirm no retry was spent
            assert harness.state.retries_used(52) == 0


class TestCheckDecisionType:
    """check() must always return a decision_type key."""

    def _ready_check(self, harness):
        return protocol.check(harness.case, harness.repo, harness.state)

    def test_missing_fix_code_returns_incomplete(self, tmp_path):
        import test_native_world_fixloop as fixtures
        import contextlib
        with contextlib.ExitStack() as stack:
            harness = fixtures.Harness(stack)
            result = self._ready_check(harness)
        assert "decision_type" in result
        assert result["decision_type"] == "incomplete"
        assert result["ready"] is False

    def test_complete_cell_returns_ready(self, tmp_path):
        import test_native_world_fixloop as fixtures
        import contextlib
        with contextlib.ExitStack() as stack:
            harness = fixtures.Harness(stack)
            harness.complete_cell()
            result = self._ready_check(harness)
        assert "decision_type" in result
        assert result["decision_type"] == "ready"
        assert result["ready"] is True

    def test_terminal_seeds_returns_terminal_decision(self, tmp_path):
        import test_native_world_fixloop as fixtures
        import contextlib
        with contextlib.ExitStack() as stack:
            harness = fixtures.Harness(stack)
            harness.snapshot()
            harness.outcome(51, success=True)
            harness.trial("initial", 51)
            # Exhaust seed 52 via infra failures only (no graded evidence)
            for i in range(fixloop.RETRY_LIMIT):
                harness.state.data["trials"].append({
                    "phase": "initial", "seed": 52, "attempt": i + 1,
                    "status": "infrastructure_error",
                    "directory": f"development/initial/seed_52/attempt_{i + 1}",
                    "spends_retry": True, "executed": True,
                    "bundle_sha256": POLICY_HASH, "bundle": {"policy": POLICY_HASH},
                    "sources": {"policy": "p"},
                    "process_exit_code": 1, "error": "watchdog",
                })
            # Do the same for seeds 53 (if in identity) — need all seeds covered
            # Actually harness uses dev_seeds from make_case which is range(51,66)
            # Let's just ensure seed 52 is terminal; other seeds still need initial
            harness.state.save()
            (harness.task_dir / "fix_code.py").write_text(
                "obs = get_observation()\nprint(obs)\n")
            result = self._ready_check(harness)
        assert "decision_type" in result
        # Terminal seeds present → terminal decision type
        assert result["decision_type"] == "terminal"
        assert result["terminal_seeds"] == [52]
        assert result["ready"] is False

    def test_check_returns_decision_type_for_incomplete_bundle(self, tmp_path):
        """Missing selected bundle also gets a decision_type."""
        import test_native_world_fixloop as fixtures
        import contextlib
        with contextlib.ExitStack() as stack:
            harness = fixtures.Harness(stack, condition="B")
            harness.complete_cell()
            # Remove the world file so selected_bundle raises OSError
            (harness.task_dir / "fix_world_program.py").unlink()
            result = self._ready_check(harness)
        assert "decision_type" in result
        assert result["decision_type"] == "incomplete"
        assert result["ready"] is False

@pytest.fixture
def executable_harness():
    import contextlib
    import test_native_world_fixloop as fixtures
    from test_executable_world import WORLD
    with contextlib.ExitStack() as stack:
        h = fixtures.Harness(stack, condition="C")
        h.case.update(profile="judgment", executable_world_revision="r1", c_arm="full",
                      c_lineage="fresh")
        h.write("initial_world_program.py", WORLD)
        h.submit = lambda phase, seed: protocol.run_trial(
            h.case, h.repo, h.state, phase, seed, h.task_dir / "initial_code.py",
            h.task_dir / "initial_world_program.py", None, None)
        h.screen = stack.enter_context(patch("executable_world_profile.checks", return_value={
            "status": "checked", "directory": "/synthetic/offline",
            "reports": [{"status": "complete"}], "conclusions": ["supported_pass"]}))
        h.snapshot()
        yield h


def rejected_screen(reports=None):
    return {"status": "rejected", "directory": "/synthetic/offline",
            "reports": reports if reports is not None else [{"status": "program_error"}],
            "conclusions": ["authored_error"], "establishes_task_success": False}


@pytest.mark.parametrize("alias", [False, True])
def test_exact_executed_initial_can_collect_real_failure(executable_harness, alias):
    h = executable_harness
    h.outcome(51, success=False)
    h.submit("smoke" if alias else "initial", 51)
    if alias:
        h.state.alias_smoke_as_initial(51)
    h.screen.return_value = rejected_screen()
    h.outcome(52, success=False)
    before = h.replay.call_count
    record = h.submit("initial", 52)
    assert h.replay.call_count == before + 1
    assert h.state.retries_used(52) == 1
    assert record["status"] == "complete" and record["task_completed"] == 0
    assert record["screening"]["status"] == "rejected"
    assert record["screening"]["reports"] == [{"status": "program_error"}]
    assert record["screening_baseline_exception"]["screening_status"] == "rejected"
    assert h.state.has_initial_evidence(52)


def test_new_candidate_still_rejected_without_spending(executable_harness):
    h = executable_harness
    h.screen.return_value = rejected_screen()
    before = h.replay.call_count
    with pytest.raises(ProtocolError, match="offline candidate error"):
        h.submit("initial", 51)
    assert h.state.retries_used(51) == 0 and h.replay.call_count == before


def test_changed_initial_refused_before_screening(executable_harness):
    h = executable_harness
    h.outcome(51, success=False); h.submit("initial", 51)
    p = h.task_dir / "initial_world_program.py"
    p.write_text(p.read_text() + "\n# changed revision\n")
    before = h.screen.call_count
    with pytest.raises(ProtocolError, match="initial bundle is frozen"):
        h.submit("initial", 52)
    assert h.screen.call_count == before and h.state.retries_used(52) == 0


@pytest.mark.parametrize("unexecuted", [True, False])
def test_initial_row_alone_does_not_authorize_exception(executable_harness, unexecuted):
    h = executable_harness
    h.outcome(51, success=False); row = h.submit("initial", 51)
    if unexecuted:
        row["executed"] = False
    else:
        row["status"] = "infrastructure_error"
    h.state.save()
    h.screen.return_value = rejected_screen()
    with pytest.raises(ProtocolError, match="offline candidate error"):
        h.submit("initial", 52)
    assert h.state.retries_used(52) == 0


def test_repair_does_not_inherit_initial_exception(executable_harness):
    h = executable_harness
    for seed in h.case["dev_seeds"]:
        h.outcome(seed, success=False); h.submit("initial", seed)
    h.screen.return_value = rejected_screen()
    with pytest.raises(ProtocolError, match="offline candidate error"):
        h.submit("repair", 52)
    assert h.state.retries_used(52) == 1


@pytest.mark.parametrize("reports", [[], [None], [{"status": []}], [{"status": "unknown"}],
                                     [{"status": "program_error"}, {"status": "infrastructure_error"}]])
def test_baseline_exception_requires_valid_infrastructure_reports(executable_harness, reports):
    h = executable_harness
    h.outcome(51, success=False); h.submit("initial", 51)
    h.screen.return_value = rejected_screen(reports)
    before = h.replay.call_count
    result = h.submit("initial", 52)
    assert result["status"] == "screening_blocked" and not result["spends_retry"]
    assert h.replay.call_count == before and h.state.retries_used(52) == 0


@pytest.mark.parametrize("has_fix", [False, True])
def test_terminal_decision_survives_missing_solution_files(executable_harness, has_fix):
    h = executable_harness
    for _ in range(fixloop.RETRY_LIMIT):
        _admit(h.state, "diagnostic", 52, status="diagnostic_program_error")
    if has_fix:
        h.write("fix_code.py", (h.task_dir / "initial_code.py").read_text())
    result = protocol.check(h.case, h.repo, h.state)
    assert result["decision_type"] == "terminal" and result["terminal_seeds"] == [52]
    assert not result["ready"]


def test_diagnostic_reservation_is_nonmutating_and_initial_still_runs(executable_harness):
    h = executable_harness
    for _ in range(2):
        _admit(h.state, "diagnostic", 52, status="diagnostic_program_error")
    before = json.dumps(h.state.data, sort_keys=True)
    directories = sorted(str(p) for p in h.task_dir.rglob("*"))
    with pytest.raises(ProtocolError, match="reserve it"):
        h.state.begin_trial("diagnostic", 52, {"policy": POLICY_HASH}, {"policy": "p"})
    assert json.dumps(h.state.data, sort_keys=True) == before
    assert sorted(str(p) for p in h.task_dir.rglob("*")) == directories
    h.outcome(52, success=False); h.submit("initial", 52)
    assert h.state.retries_used(52) == 3 and h.state.has_graded_evidence(52)


def test_zero_dimensional_array_and_truthy_object_are_not_boolean_scalars():
    import numpy as np
    class Truthy:
        def __bool__(self):
            raise AssertionError("arbitrary truthiness must never run")
    es = _make_state_es(); es.set("x", 1, evidence_ids=[1])
    for result in [np.array(True), Truthy()]:
        with pytest.raises(TypeError, match="predicate must return a bool"):
            es.predicate("p", lambda v: result, ("x",))
    assert es.predicate("p", lambda v: np.dot([1., 2.], [1., 2.]) > 0, ("x",))["verdict"] == "true"


def test_mixed_screening_not_cached_and_legacy_mixed_cache_rechecked(tmp_path, monkeypatch):
    import executable_world_profile as profile
    from test_qwen_full_repair import CASE
    sources = {"policy": tmp_path / "policy.py", "world": tmp_path / "world.py"}
    for p in sources.values(): p.write_text("x = 1\n")
    tape = tmp_path / "tape.jsonl"; tape.write_text("{}\n")
    state = SimpleNamespace(task_dir=tmp_path, data={"trials": []})
    monkeypatch.setattr(profile, "latest_tape", lambda state: tape)
    reports = iter([{"status": "program_error", "conclusion": "authored_error"},
                    {"status": "infrastructure_error", "conclusion": "unknown"}])
    monkeypatch.setattr(profile, "run_mode", lambda *a: next(reports))
    mixed = profile.checks(dict(CASE), tmp_path, state, sources, {})
    assert mixed["status"] == "rejected"
    root = tmp_path / "attempts/offline"
    assert list(root.glob("*.json")) == []
    # Reproduce an old mixed cache entry, then prove both modes execute again.
    import hashlib
    key = hashlib.sha256(json.dumps(mixed["identity"], sort_keys=True).encode()).hexdigest()
    (root / (key + ".json")).write_text(json.dumps(mixed))
    calls = []
    def recovered(*args):
        calls.append(args[5])
        return {"status": "complete", "conclusion": "supported_pass"}
    monkeypatch.setattr(profile, "run_mode", recovered)
    result = profile.checks(dict(CASE), tmp_path, state, sources, {})
    assert not result["cached"] and result["status"] == "checked" and len(calls) == 2
    cached = profile.checks(dict(CASE), tmp_path, state, sources, {})
    assert cached["cached"] and len(calls) == 2
