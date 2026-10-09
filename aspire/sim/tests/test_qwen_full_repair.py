# SPDX-License-Identifier: MIT
"""Regression tests for the Qwen C full-repair core fixes (2026-09-24).

Each test pins one behavior a previous run got wrong, not an implementation
detail. Modules are imported by their ordinary package/sys.path names; nothing
here slices or re-execs source text.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np
import pytest

SIM = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SIM / "scripts/libero"))
sys.path.insert(0, str(SIM.parents[1]))

import native_world_fixloop_state as fixloop  # noqa: E402
import native_world_protocol as wprotocol  # noqa: E402
import executable_world_profile as profile  # noqa: E402
from aspire.sim.cap.world_model import executable_world as ew  # noqa: E402


# ---- ledger: budget, evidence, honesty -----------------------------------


IDENTITY = {"dev_seeds": [51, 52], "profile": "judgment"}


def ledger(tmp_path) -> fixloop.NativeWorldState:
    return fixloop.NativeWorldState(tmp_path / "task", dict(IDENTITY))


def admit(state, phase, seed, *, status="complete", completed=0):
    """One admitted attempt that spent a retry, with a settled outcome."""
    record = state.begin_trial(phase, seed, {"policy": "a" * 64}, {"policy": "p"})
    record.update(status=status, sandbox_rc=0 if status == "complete" else 1,
                  reward=0.0, task_completed=completed, trial_dir=record["directory"])
    state.save()
    return record


def crashing_smoke(state, seed):
    """A real graded execution that crashed: the legitimate way to spend a smoke."""
    record = state.begin_trial("smoke", seed, {"policy": "a" * 64}, {"policy": "p"})
    record.update(status="complete", sandbox_rc=1, reward=0.0, task_completed=0,
                  trial_dir=record["directory"])
    state.save()
    return record


def lost_attempt(state, phase, seed):
    """An admitted attempt the infrastructure lost: retry spent, resolved nothing."""
    record = state.begin_trial(phase, seed, {"policy": "a" * 64}, {"policy": "p"})
    state.finish_trial(record, result=None, exit_code=1, error="the worker died")
    return record


def blocker_note(state, seed):
    (state.task_dir / "attempts").mkdir(parents=True, exist_ok=True)
    (state.task_dir / "attempts" / f"seed_{seed}_BLOCKED.md").write_text(
        "## Root Cause\nEvery smoke crashed in the same grasp.\n\n"
        "## Details\nThree retry-spending smokes, sandbox_rc=1 each.\n\n"
        "## What Was Tried\nTwo approach heights and a wider segmentation prompt.\n")


def test_snapshot_then_initial_is_the_only_path_to_repair(tmp_path):
    state = ledger(tmp_path)
    admit(state, "snapshot", 51)
    admit(state, "initial", 51)
    with pytest.raises(fixloop.ProtocolError, match=r"initial program on seeds \[52\]"):
        state.begin_trial("repair", 51, {"policy": "a" * 64}, {"policy": "p"})


def test_spent_diagnostics_do_not_unlock_repair(tmp_path):
    """The exact exploit: burn a seed's budget on inspection, call it triaged.

    The last-diagnostic reservation means the third diagnostic on a seed with
    no graded evidence is now refused before it can be admitted, so this test
    builds the exhausted ledger by injecting the rows directly rather than
    going through begin_trial.
    """
    state = ledger(tmp_path)
    admit(state, "snapshot", 51)
    admit(state, "initial", 51)
    # Simulate a historical ledger where 3 diagnostics were admitted before the
    # reservation rule existed.  Inject the rows directly into state.data so
    # the test exercises the repair-gate and completion-errors logic, not
    # begin_trial admission.
    for i in range(fixloop.RETRY_LIMIT):
        row = {"phase": "diagnostic", "seed": 52, "attempt": i + 1,
               "status": "diagnostic_program_error",
               "directory": f"development/diagnostic/seed_52/attempt_{i + 1}",
               "spends_retry": True, "executed": True,
               "bundle_sha256": "a" * 64, "bundle": {"policy": "a" * 64},
               "sources": {"policy": "p"},
               "sandbox_rc": 0, "reward": 0.0, "task_completed": 0,
               "trial_dir": f"development/diagnostic/seed_52/attempt_{i + 1}",
               "process_exit_code": 0, "error": ""}
        state.data["trials"].append(row)
    state.save()
    assert state.retries_remaining(52) == 0
    assert not state.has_initial_evidence(52)
    with pytest.raises(fixloop.ProtocolError, match="no initial evidence and no attempts left"):
        state.begin_trial("repair", 51, {"policy": "a" * 64}, {"policy": "p"})


def test_missing_initial_evidence_fails_completion(tmp_path):
    state = ledger(tmp_path)
    admit(state, "snapshot", 51)
    admit(state, "initial", 51, completed=1)
    # Inject a historical ledger with 3 diagnostics on seed 52 directly,
    # bypassing begin_trial admission (reservation now blocks the 3rd call).
    for i in range(fixloop.RETRY_LIMIT):
        row = {"phase": "diagnostic", "seed": 52, "attempt": i + 1,
               "status": "diagnostic_program_error",
               "directory": f"development/diagnostic/seed_52/attempt_{i + 1}",
               "spends_retry": True, "executed": True,
               "bundle_sha256": "a" * 64, "bundle": {"policy": "a" * 64},
               "sources": {"policy": "p"},
               "sandbox_rc": 0, "reward": 0.0, "task_completed": 0,
               "trial_dir": f"development/diagnostic/seed_52/attempt_{i + 1}",
               "process_exit_code": 0, "error": ""}
        state.data["trials"].append(row)
    state.save()
    progress = state.progress()
    assert progress["seeds_exhausted_without_initial"] == [52]
    assert progress["seeds_exhausted_without_graded_evidence"] == [52]
    errors = state.completion_errors(bundle={"policy": "a" * 64},
                                     working_code=tmp_path / "absent.py",
                                     world_required=False)
    assert any("seeds_exhausted_without_graded_evidence" in e for e in errors)


# ---- graded exhaustion is not diagnostic exhaustion -----------------------


def exhausted_by_smokes(tmp_path, *, initial_completed=1):
    """Seed 51 spends all three attempts on real, crashing smokes."""
    state = ledger(tmp_path)
    admit(state, "snapshot", 51)
    for _ in range(fixloop.RETRY_LIMIT):
        crashing_smoke(state, 51)
    admit(state, "initial", 52, completed=initial_completed)
    return state


def test_graded_smoke_exhaustion_still_lets_other_seeds_be_repaired(tmp_path):
    state = exhausted_by_smokes(tmp_path, initial_completed=0)
    assert state.retries_remaining(51) == 0
    assert not state.has_initial_evidence(51) and state.has_graded_evidence(51)
    record = state.begin_trial("repair", 52, {"policy": "a" * 64}, {"policy": "p"})
    assert record["phase"] == "repair" and record["spends_retry"] is True
    progress = state.progress()
    # Missing initial coverage is still reported; it is simply not the same fact
    # as "this seed was never graded".
    assert progress["seeds_exhausted_without_initial"] == [51]
    assert progress["seeds_exhausted_without_graded_evidence"] == []
    assert progress["graded_executions"] == 4
    assert progress["blocked_notes_required"] == [51]


def test_graded_smoke_exhaustion_completes_once_its_blocker_note_exists(tmp_path):
    state = exhausted_by_smokes(tmp_path)
    errors = state.completion_errors(bundle={"policy": "a" * 64},
                                     working_code=tmp_path / "absent.py",
                                     world_required=False)
    assert any("blocked_notes_required" in e for e in errors)
    blocker_note(state, 51)
    errors = state.completion_errors(bundle={"policy": "a" * 64},
                                     working_code=tmp_path / "absent.py",
                                     world_required=False)
    assert not [e for e in errors if "seeds_exhausted" in e or "blocked_notes_required" in e]
    assert not [e for e in errors if "no graded development execution" in e]


def test_infrastructure_only_exhaustion_unlocks_nothing(tmp_path):
    state = ledger(tmp_path)
    admit(state, "snapshot", 51)
    admit(state, "initial", 51, completed=1)
    for _ in range(fixloop.RETRY_LIMIT):
        lost_attempt(state, "initial", 52)
    assert state.retries_remaining(52) == 0 and not state.has_graded_evidence(52)
    with pytest.raises(fixloop.ProtocolError, match="no initial evidence and no attempts left"):
        state.begin_trial("repair", 51, {"policy": "a" * 64}, {"policy": "p"})
    progress = state.progress()
    assert progress["seeds_exhausted_without_graded_evidence"] == [52]
    blocker_note(state, 52)
    errors = state.completion_errors(bundle={"policy": "a" * 64},
                                     working_code=tmp_path / "absent.py",
                                     world_required=False)
    # A written blocker note does not manufacture the missing graded evidence.
    assert any("seeds_exhausted_without_graded_evidence" in e for e in errors)


# ---- Qwen capacity at finalization ---------------------------------------


QWEN_TRANSCRIPT = "\n".join([
    json.dumps({"type": "assistant", "message": {"model": "claude-opus-4-6"}}),
    json.dumps({"type": "result", "usage": {"input_tokens": 10, "output_tokens": 5},
                # What Claude Code reports for a custom alias: its built-in
                # default output limit, not the configured 64000 request.
                "modelUsage": {"claude-opus-4-6": {"contextWindow": 1000000,
                                                   "maxOutputTokens": 32000}}}),
]) + "\n"


def finalized_cell(monkeypatch, stack, provider):
    import test_native_world_fixloop as fixtures

    harness = fixtures.Harness(stack)
    harness.complete_cell()
    if provider:
        harness.case["model_provider"] = provider
    # The capacity rule under test belongs to the code-world profiles; the
    # in-process predicate itself is not what this test is about.
    monkeypatch.setattr(wprotocol, "in_process_world", lambda case: True)
    transcript = harness.task_dir / "coordinator.stdout.jsonl"
    transcript.write_text(QWEN_TRANSCRIPT)
    return wprotocol.finalize(harness.case, harness.repo, harness.state, [transcript])


def test_local_vllm_default_output_limit_does_not_fail_finalization(monkeypatch):
    import contextlib
    with contextlib.ExitStack() as stack:
        result = finalized_cell(monkeypatch, stack, "local-vllm")
    assert result["stage1_complete"] is True
    assert result["model_context_windows"] == [1000000]


def test_a_nonlocal_provider_still_enforces_the_configured_output_limit(monkeypatch):
    import contextlib
    with contextlib.ExitStack() as stack, \
            pytest.raises(fixloop.ProtocolError, match="native capacity mismatch"):
        finalized_cell(monkeypatch, stack, None)


def test_the_served_context_window_is_still_enforced_for_local_vllm(monkeypatch):
    import contextlib
    with contextlib.ExitStack() as stack, \
            pytest.raises(fixloop.ProtocolError, match="native capacity mismatch"):
        import test_native_world_fixloop as fixtures

        harness = fixtures.Harness(stack)
        harness.complete_cell()
        harness.case["model_provider"] = "local-vllm"
        monkeypatch.setattr(wprotocol, "in_process_world", lambda case: True)
        transcript = harness.task_dir / "coordinator.stdout.jsonl"
        transcript.write_text(QWEN_TRANSCRIPT.replace("1000000", "200000"))
        wprotocol.finalize(harness.case, harness.repo, harness.state, [transcript])



def test_diagnostics_alone_are_not_graded_evidence(tmp_path):
    state = ledger(tmp_path)
    admit(state, "snapshot", 51)
    admit(state, "diagnostic", 51, status="diagnostic_program_error", completed=1)
    assert state.progress()["graded_executions"] == 0
    assert state.candidates() == {}
    errors = state.completion_errors(bundle={"policy": "a" * 64},
                                     working_code=tmp_path / "absent.py",
                                     world_required=False)
    assert any("no graded development execution" in e for e in errors)


def test_authored_repl_failure_spends_retry_but_not_infrastructure(tmp_path):
    state = ledger(tmp_path)
    admit(state, "snapshot", 51)
    record = state.begin_trial("diagnostic", 51, {"policy": "a" * 64}, {"policy": "p"})
    state.finish_trial(record, result=None, exit_code=0, diagnostic_error={
        "error_count": 1,
        "errors": [{"type": "AttributeError", "message": "no attribute 'handle'"}]})
    assert record["status"] == "diagnostic_program_error"
    assert record["spends_retry"] is True and record["task_completed"] == 0
    progress = state.progress()
    assert progress["infrastructure_errors"] == []
    assert progress["diagnostic_program_errors"] == [record["directory"]]
    assert progress["graded_executions"] == 0


def test_missing_repl_artifact_stays_infrastructure(tmp_path):
    state = ledger(tmp_path)
    admit(state, "snapshot", 51)
    record = state.begin_trial("diagnostic", 51, {"policy": "a" * 64}, {"policy": "p"})
    state.finish_trial(record, result=None, exit_code=0,
                       error="diagnostic session produced no artifact")
    assert record["status"] == "infrastructure_error"
    assert state.progress()["infrastructure_errors"] == [record["directory"]]


def test_blocker_is_attributable_and_spends_nothing(tmp_path):
    state = ledger(tmp_path)
    before = state.retries_used(51)
    blocker = state.record_blocker("initial", 51, "screening could not run",
                                   {"policy": "p"}, {"status": "blocked"})
    assert blocker["spends_retry"] is False and blocker["retryable"] is True
    assert state.retries_used(51) == before
    assert state.retries_remaining(51) == fixloop.RETRY_LIMIT
    reloaded = json.loads(state.path.read_text())
    assert reloaded["blockers"][0]["reason"] == "screening could not run"
    assert state.progress()["screening_blockers"] == [blocker]


def test_retry_limit_and_retry_phases_are_unchanged():
    assert fixloop.RETRY_LIMIT == 3
    assert fixloop.RETRY_PHASES == ("smoke", "initial", "repair", "diagnostic")


# ---- tape integrity and replay -------------------------------------------


def encode_tape(tmp_path, rows):
    store = ew.TapeStore(tmp_path)
    path = tmp_path / "public_tape.jsonl"
    with path.open("w") as out:
        for index, (name, args, result) in enumerate(rows):
            call = {"id": index, "function": name, "args": args, "kwargs": {}}
            out.write(json.dumps({"call": store.encode(call, save=True),
                                  "result": store.encode(result, save=True),
                                  "error": None}) + "\n")
    return path


def test_every_array_read_revalidates_its_digest(tmp_path):
    store = ew.TapeStore(tmp_path)
    array = np.arange(12, dtype=np.float64).reshape(3, 4)
    encoded = store.encode(array, save=True)
    assert np.array_equal(store.decode(encoded), array)
    target = tmp_path / "arrays" / (encoded["sha256"] + ".npy")
    np.save(target, np.zeros((3, 4), dtype=np.float64), allow_pickle=False)
    # Same dtype and shape, different bytes: a shape-only check would pass it.
    with pytest.raises(ValueError, match="public tape array changed"):
        store.decode(encoded)


def test_unchanged_replay_returns_the_recorded_results(tmp_path):
    tape = encode_tape(tmp_path, [("get_observation", (), {"rgb": 1}),
                                  ("solve_ik", (1.0,), [0.1, 0.2])])
    binding = ew.ReplayBinding(tape)
    assert binding.call("get_observation") == {"rgb": 1}
    assert binding.call("solve_ik", 1.0) == [0.1, 0.2]
    assert binding.index == 2


def test_a_changed_call_stops_replay_without_leaking_the_future(tmp_path):
    tape = encode_tape(tmp_path, [("get_observation", (), {"rgb": 1}),
                                  ("solve_ik", (1.0,), [0.1, 0.2])])
    binding = ew.ReplayBinding(tape)
    binding.call("get_observation")
    with pytest.raises(ew.ReplayDivergence, match="first changed API call at index 1"):
        binding.call("solve_ik", 2.0)
    assert binding.index == 1  # Not advanced: the unmatched row stays unread.
    with pytest.raises(ew.ReplayDivergence):
        binding.call("solve_ik", 2.0)


def test_exhausted_history_is_unsupported_not_invented(tmp_path):
    tape = encode_tape(tmp_path, [("get_observation", (), {"rgb": 1})])
    binding = ew.ReplayBinding(tape)
    binding.call("get_observation")
    with pytest.raises(ew.ReplayDivergence, match="no counterfactual observation"):
        binding.call("get_observation")


# ---- offline screening ----------------------------------------------------


CASE = {"executable_world_revision": "r1", "condition": "C", "profile": "judgment",
        "c_arm": "full", "c_lineage": "fresh", "task": "put_the_bowl_on_the_stove"}


def screen(tmp_path, monkeypatch, reports):
    sources = {"policy": tmp_path / "code.py", "world": tmp_path / "world.py"}
    for path in sources.values():
        path.write_text("x = 1\n")
    state = SimpleNamespace(task_dir=tmp_path, data={"trials": []})
    monkeypatch.setattr(profile, "run_mode",
                        lambda *a, **k: dict(reports.pop(0)))
    return profile.checks(dict(CASE), tmp_path, state, sources, {}), tmp_path


def test_timeout_is_retryable_and_never_caches_a_verdict(tmp_path, monkeypatch):
    blocked = {"mode": "rehearsal", "status": "infrastructure_error", "retryable": True,
               "reason": "offline process watchdog expired (600s)", "conclusion": "unknown"}
    result, root = screen(tmp_path, monkeypatch, [dict(blocked)])
    assert result["status"] == "blocked" and result["retryable"] is True
    assert result["establishes_task_success"] is False
    assert result["real_simulator_executions"] == 0
    index = [p for p in (root / "attempts/offline").glob("*.json")]
    assert index == [], "a transient block must not become a permanent verdict"
    # The identical candidate is screened again rather than rejected from cache.
    again, _ = screen(tmp_path, monkeypatch, [{"mode": "rehearsal", "status": "complete",
                                               "conclusion": "supported_pass", "retryable": False}])
    assert again["status"] == "checked" and again["cached"] is False


def test_the_default_watchdog_is_the_repaired_ceiling():
    assert profile.OFFLINE_TIMEOUT_SECONDS == 600


def test_a_real_python_error_rejects_the_candidate(tmp_path, monkeypatch):
    result, _ = screen(tmp_path, monkeypatch, [{
        "mode": "rehearsal", "status": "program_error", "retryable": False,
        "conclusion": "authored_error", "reason": "NameError: name 'plate_c' is not defined"}])
    assert result["status"] == "rejected"
    assert result["conclusions"] == ["authored_error"]


def test_unsupported_is_unknown_and_never_success(tmp_path, monkeypatch):
    result, _ = screen(tmp_path, monkeypatch, [{
        "mode": "rehearsal", "status": "unsupported", "retryable": False,
        "conclusion": "unknown", "reason": "no supported prediction"}])
    assert result["status"] == "checked"
    assert result["conclusions"] == ["unknown"]
    assert result["establishes_task_success"] is False


def test_an_authored_error_outranks_a_concurrent_block():
    assert profile.screening_status([
        {"status": "infrastructure_error"}, {"status": "program_error"}]) == "rejected"
    assert profile.screening_status([
        {"status": "complete"}, {"status": "infrastructure_error"}]) == "blocked"
    assert profile.screening_status([{"status": "unsupported"}]) == "checked"
