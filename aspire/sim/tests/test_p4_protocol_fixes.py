"""CC_TASK_4: the two p4 rules that kept cells from a score, and the terminal guard.

1. No cap on swept candidates (`SWEEP_ROUNDS` removed).
2. The all-fail tie-break compares freezable candidates only; a strictly better
   candidate that is still partially covered is ADVISORY, a complete one is
   SUBSTANTIVE.
3. `check` is terminal (`no_legal_action`) when errors remain and no admissible
   action can change them; an advisory-only check is ready.

Unit tests use synthetic ledgers. The two retro tests replay the real frozen
ledgers of drawer_p4_r1b (rerun-c) and goaltaskbowldrawer_p4n_r1 (p4n), copied
read-only into `tests/fixtures/p4_retro/` (ledger + report files only; the trial
directories are not copied, so the report-only readers that need them are
patched out — they never enter `errors` or `ready`).
"""
from __future__ import annotations

import gzip
import hashlib
import json
import shutil
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

SIM = Path(__file__).resolve().parents[1]
for p in (SIM.parents[1], SIM, SIM / "scripts/libero", SIM / "scripts/common", SIM / "tests"):
    sys.path.insert(0, str(p))

import native_world_fixloop_state as ledger
import native_world_protocol as protocol
from test_search_control import _bundle, _early_stopped, _p4_state, harness, trace  # noqa: F401

RETRO = SIM / "tests/fixtures/p4_retro"


def _rows(state):
    """Synthetic rows carry the digest only; `candidates()` also wants the bundle."""
    for row in state.data["trials"]:
        row.setdefault("bundle", {"policy": row["bundle_sha256"]})
        row.setdefault("sandbox_rc", 0)
    return state


def _search(state):
    state.data["identity"]["prediction_contract"] = "p3-search"
    return _rows(state)


def _crash(state, digest_name, seed):
    digest = ledger.bundle_identity(_bundle(digest_name))
    for row in state.data["trials"]:
        if row["bundle_sha256"] == digest and row["seed"] == seed:
            row["sandbox_rc"] = 1
            return
    raise AssertionError("no such row")


# --- 1. sweep cap removed ---------------------------------------------------------


def test_sweep_rounds_constant_is_gone_and_the_gate_reports_no_limit():
    assert not hasattr(ledger, "SWEEP_ROUNDS")


def test_any_number_of_tested_candidates_can_be_swept(harness):
    h = harness()
    _early_stopped(h)
    for i, (seed, swept) in enumerate(((55, 65), (57, 64), (59, 63), (61, 62))):
        candidate = h.policy(f"c{i}.py", "pull", f"v{i}")
        h.trial("repair", seed, candidate)
        assert h.trial("sweep", swept, candidate)["status"] == "complete"
    assert len(h.state.swept_bundles()) == 4
    assert all(not r["spends_retry"] for r in h.state.records("sweep"))


def test_sweep_still_once_per_seed_untested_refused_and_held_out_refused(harness):
    h = harness()
    _early_stopped(h)
    h.trial("sweep", 54)
    with pytest.raises(ledger.ProtocolError, match="already covered"):
        h.trial("sweep", 54)
    with pytest.raises(ledger.ProtocolError, match="already tested candidate"):
        h.trial("sweep", 55, h.policy("new.py", "pull", "untested"))
    with pytest.raises(ledger.ProtocolError, match="held out"):
        h.trial("sweep", 7)


def test_gate_has_no_rounds_fields(tmp_path):
    state = _search(_p4_state(tmp_path, [("repair", s, "a", trace(), "pull", 0.0, False) for s in range(51, 56)]))
    gate = state.coverage_gate(_bundle("a"))
    assert "sweep_rounds_left" not in gate and "sweep_rounds_allowed" not in gate
    assert gate["sweep_candidate_limit"] is None and gate["sweepable"] == list(range(56, 66))


# --- 2. tie-break among freezable candidates --------------------------------------


def _all_fail(tmp_path, *, partial=("p", 51)):
    """'s' covers all 15 seeds with one crash; 'p' ran one seed with no crash."""
    rows = [("repair", s, "s", trace(), "pull", 0.0, False) for s in range(51, 66)]
    rows.append(("repair", partial[1], partial[0], trace(), "pull", 0.0, False))
    state = _search(_p4_state(tmp_path, rows))
    _crash(state, "s", 53)
    return state


def test_partially_covered_better_candidate_is_advisory_when_the_selection_is_complete(tmp_path):
    state = _all_fail(tmp_path)
    rule = state.tie_break(_bundle("s"))
    assert rule["severity"] == "advisory"
    assert [c["complete"] for c in rule["better_freezable"]] == [False]
    (issue,) = state.evidence_issues(_bundle("s"))
    assert issue["code"] == "all_fail_tie_break" and issue["severity"] == "advisory"
    assert state.evidence_errors(_bundle("s")) == []


def test_complete_better_candidate_keeps_the_tie_break_substantive(tmp_path):
    rows = [("repair", s, "s", trace(), "pull", 0.0, False) for s in range(51, 66)]
    rows += [("sweep" if s != 51 else "repair", s, "z", trace(), "pull", 0.0, False) for s in range(51, 66)]
    state = _search(_p4_state(tmp_path, rows))
    _crash(state, "s", 53)
    rule = state.tie_break(_bundle("s"))
    assert rule["severity"] == "substantive" and rule["better_freezable"][0]["complete"]
    assert state.evidence_errors(_bundle("s")) == [ledger.TIE_BREAK_ERROR]
    # Selecting the complete zero-crash candidate clears it.
    assert state.evidence_issues(_bundle("z")) == []


def test_an_incomplete_selection_never_gets_the_advisory(tmp_path):
    rows = [("repair", s, "s", trace(), "pull", 0.0, False) for s in range(51, 60)]
    rows.append(("repair", 61, "p", trace(), "pull", 0.0, False))
    state = _search(_p4_state(tmp_path, rows))
    _crash(state, "s", 53)
    assert state.tie_break(_bundle("s"))["severity"] == "substantive"


def test_unfreezable_candidates_are_excluded_from_the_comparison(tmp_path, monkeypatch):
    state = _all_fail(tmp_path)
    p = ledger.bundle_identity(_bundle("p"))
    real = state._coverage
    monkeypatch.setattr(state, "_coverage", lambda d: {**real(d), "payable": False, "complete": False}
                        if d == p else real(d))
    rule = state.tie_break(_bundle("s"))
    assert rule["severity"] is None and rule["excluded_unfreezable"] == [p]


def test_a_tie_is_the_best_and_passes(tmp_path):
    rows = [("repair", s, "s", trace(), "pull", 0.0, False) for s in range(51, 66)]
    rows += [("repair", s, "t", trace(), "pull", 0.0, False) for s in range(51, 66)]
    state = _search(_p4_state(tmp_path, rows))
    assert state.tie_break(_bundle("s"))["severity"] is None


def test_without_the_coverage_contract_the_original_tie_break_is_unchanged(tmp_path):
    rows = [("repair", s, "s", trace(), "pull", 0.0, False) for s in range(51, 66)]
    rows.append(("repair", 51, "p", trace(), "pull", 0.0, False))
    state = _rows(_p4_state(tmp_path, rows))  # no prediction_contract: every record is final
    _crash(state, "s", 53)
    assert state.tie_break(_bundle("s"))["severity"] == "substantive"
    assert state.evidence_errors(_bundle("s")) == [ledger.TIE_BREAK_ERROR]


def test_a_successful_bundle_still_overrides_the_tie_break(tmp_path):
    rows = [("repair", s, "s", trace(), "pull", 0.0, False) for s in range(51, 66)]
    rows.append(("repair", 51, "win", trace(), "pull", 0.0, True))
    state = _search(_p4_state(tmp_path, rows))
    (issue,) = state.evidence_issues(_bundle("s"))
    assert issue["code"] == "selection_without_success" and issue["severity"] == "substantive"


# --- 3. legal actions and the generic terminal guard ------------------------------


def _spend_every_retry(state):
    for s in state.seeds:
        while state.retries_remaining(s):
            state.data["trials"].append({"phase": "diagnostic", "seed": s, "status": "diagnostic_program_error",
                                         "spends_retry": True, "executed": True, "directory": None,
                                         "bundle_sha256": "d" * 64})


def test_legal_actions_lists_retries_sweeps_and_better_selections(tmp_path):
    state = _all_fail(tmp_path)
    actions = state.legal_actions(_bundle("s"))
    p = ledger.bundle_identity(_bundle("p"))
    assert actions["any"] and actions["sweeps"] == {p: list(range(52, 66))}
    assert actions["better_selection"] == [p]
    assert set(actions["charged_retries"]) == {str(s) for s in range(51, 66)}


def test_legal_actions_is_empty_when_nothing_is_admissible(tmp_path):
    rows = [("repair", s, "s", trace(), "pull", 0.0, False) for s in range(51, 66)]
    state = _search(_p4_state(tmp_path, rows))
    _spend_every_retry(state)
    actions = state.legal_actions(_bundle("s"))
    assert not actions["any"] and actions == {"charged_retries": {}, "pending_repairs": [], "sweeps": {},
                                              "better_selection": [], "any": False}


def _check(h, *, issues, actions):
    """protocol.check over a harness cell, with the state's issues/actions pinned."""
    with patch.object(protocol, "search_control", lambda case, state: None), \
            patch.object(protocol, "coverage_triage", lambda case, state: None), \
            patch.object(h.state, "completion_issues", lambda **kw: list(issues)), \
            patch.object(h.state, "legal_actions", lambda bundle: dict(actions)), \
            patch.object(h.state, "coverage_errors", lambda bundle: []), \
            patch.object(h.state, "coverage_reachability", lambda bundle: {"reachable": True}):
        return protocol.check(h.case, h.repo, h.state)


NO_ACTIONS = {"charged_retries": {}, "pending_repairs": [], "sweeps": {}, "better_selection": [], "any": False}


def _selected_on_disk(h):
    h.trial("snapshot", 51)
    h.trial("initial", 51)
    for src, dst in (("initial_code.py", "fix_code.py"), ("initial_world_program.py", "fix_world_program.py")):
        (h.task_dir / dst).write_text((h.task_dir / src).read_text())
    bundle = protocol.selected_bundle(h.case, h.state)
    h.state.data["selected"] = {"bundle_sha256": ledger.bundle_identity(bundle), "bundle": bundle,
                                "reason": "synthetic"}


def test_errors_with_no_legal_action_are_terminal(harness):
    h = harness()
    _selected_on_disk(h)
    stuck = {"code": "seeds_pending_repair", "severity": "substantive", "message": "stuck"}
    result = _check(h, issues=[stuck], actions=NO_ACTIONS)
    assert result["decision_type"] == "terminal" and not result["ready"]
    (reason,) = result["terminal_reasons"]
    assert reason["code"] == "no_legal_action" and reason["blocking_issues"] == ["seeds_pending_repair"]


def test_errors_with_a_legal_action_stay_incomplete(harness):
    h = harness()
    _selected_on_disk(h)
    stuck = {"code": "seeds_pending_repair", "severity": "substantive", "message": "stuck"}
    result = _check(h, issues=[stuck], actions={**NO_ACTIONS, "sweeps": {"x": [52]}, "any": True})
    assert result["decision_type"] == "incomplete" and result["terminal_reasons"] == []


@pytest.mark.parametrize("code", sorted(protocol.AUTHORABLE_ISSUES))
def test_authorable_errors_are_never_terminal(harness, code):
    h = harness()
    _selected_on_disk(h)
    result = _check(h, issues=[{"code": code, "severity": "substantive", "message": code}], actions=NO_ACTIONS)
    assert result["decision_type"] == "incomplete"


def test_advisory_only_check_is_ready_and_reports_the_advisory(harness):
    h = harness()
    _selected_on_disk(h)
    advisory = {"code": "all_fail_tie_break", "severity": "advisory", "message": ledger.TIE_BREAK_ERROR}
    result = _check(h, issues=[advisory], actions=NO_ACTIONS)
    assert result["ready"] and result["decision_type"] == "ready" and result["errors"] == []
    assert result["advisories"] == [advisory]


def test_completion_errors_keeps_its_string_api_and_drops_advisories(tmp_path):
    state = _all_fail(tmp_path)
    issues = [{"code": "report", "severity": "substantive", "message": "m1"},
              {"code": "all_fail_tie_break", "severity": "advisory", "message": "m2"}]
    with patch.object(state, "completion_issues", lambda **kw: issues):
        assert state.completion_errors(bundle={}, working_code=Path("x"), world_required=False) == ["m1"]


# --- retro replay on the real frozen ledgers --------------------------------------


def _retro_state(tmp_path, cell):
    source = RETRO / cell
    task_dir = tmp_path / cell
    task_dir.mkdir()
    raw = gzip.decompress((source / "development_state.json.gz").read_bytes())
    assert hashlib.sha256(raw).hexdigest() == (source / "development_state.sha256").read_text().strip()
    (task_dir / "development_state.json").write_bytes(raw)
    for name in ("fix_code.py", "fix_world_program.py", "findings.md", "task_analysis.md"):
        shutil.copy(source / name, task_dir / name)
    shutil.copytree(source / "attempts", task_dir / "attempts")
    data = json.loads(raw)
    state = ledger.NativeWorldState.__new__(ledger.NativeWorldState)
    state.task_dir, state.path, state.data = task_dir, task_dir / "development_state.json", data
    state.seeds, state.gate, state.sealed = data["identity"]["dev_seeds"], "oracle", False
    return state


def _digest(state, prefix):
    (digest,) = [d for d in state.candidates() if d.startswith(prefix)]
    return digest


def test_retro_A_drawer_p4_r1b_e8b057f8_is_now_sweepable(tmp_path):
    state = _retro_state(tmp_path, "drawer_p4_r1b")
    e8 = _digest(state, "e8b057f8")
    # The real history: b23ea329 swept one seed, b72b5733 fifteen — the old cap's two slots.
    assert [d[:8] for d in state.swept_bundles()] == ["b23ea329", "b72b5733"]
    gate = state._coverage(e8)
    assert gate["covered_seeds"] == [51, 52, 53, 55] and gate["unsweepable"] == {}
    assert gate["payable"] and gate["sweepable"] == [54, 56, 57, 58, 59, 60, 61, 62, 63, 64, 65]
    # The ledger admits the sweep (validation only; nothing is written).
    state.data["stage1_complete"] = False
    state.validate_admission("sweep", 54, e8)


def test_retro_B_goaltaskbowldrawer_p4n_r1_is_ready_with_886c32ac(tmp_path):
    state = _retro_state(tmp_path, "goaltaskbowldrawer_p4n_r1")
    selected = state.data["selected"]
    assert selected["bundle_sha256"].startswith("886c32ac")
    repo = tmp_path / "repo"
    working = repo / "outputs/working_codes"
    working.mkdir(parents=True)
    for path in (RETRO / "goaltaskbowldrawer_p4n_r1/working_codes").iterdir():
        shutil.copy(path, working / path.name)
    case = {"suite": "libero_goal_task", "task": "open_the_top_drawer_and_put_the_bowl_inside",
            "condition": "C", "profile": "judgment", "prediction_contract": "p3-search",
            "development_gate": "oracle", "foundation_revision": "r1", "world_use_revision": "r1"}
    with patch.object(protocol, "search_control", lambda case, state: None), \
            patch.object(protocol, "coverage_triage", lambda case, state: None), \
            patch.object(protocol, "world_use", lambda case: None), \
            patch.object(ledger.NativeWorldState, "save", lambda self: None):
        result = protocol.check(case, repo, state)
    assert result["ready"] and result["decision_type"] == "ready" and result["errors"] == []
    assert result["selected"]["bundle_sha256"] == selected["bundle_sha256"]
    assert result["coverage_gate"]["complete"]
    (advisory,) = result["advisories"]
    assert advisory["code"] == "all_fail_tie_break" and advisory["severity"] == "advisory"
    assert all(not c["complete"] for c in advisory["tie_break"]["better_freezable"])
    # Under the old rule this very ledger returned exactly this one error.
    assert advisory["message"] == ledger.TIE_BREAK_ERROR
