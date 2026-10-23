"""CC_TASK_6 (p5): the ASPIRE v1.0 development budget (`development_budget: "aspire-v1"`).

Rules under test, one group each:
  A. phase A — before the first development seed passes: only that seed runs,
     attempts there are uncapped, no sweep / select / freeze, never terminal;
  B. phase B — after it passes: v0 is swept free; at most 8 versions, every
     version on all 15 seeds before the next, each declaring its failure mode,
     at most 3 per mode (then blocked, with a note), selection by passes then
     crashes, and RETRY_LIMIT no longer binds;
  C. freeze conditions — 15/15, 8 versions used, or every remaining failure in a
     blocked mode;
  D. probes — observation-only, spend nothing, never a version; refused when
     they name an action; a probe that moved the robot is a violation;
  E. reporting — `check` / `status` / `finalize` carry the budget block;
  F. prompts — the budget text replaces the per-seed retry text, and every
     non-budget render is byte-identical to the frozen p4fix prompt;
  G. supervisor — node pinning (`node_cells`), and the p5 support overlay
     (24 h development, job window) and driver text;
  H. replay — drawer_p4_r1c's real frozen ledger (0/15, froze b72… under p4) can
     not freeze under the new rules: it is phase A, not ready, not terminal.

Model and simulator boundaries are faked exactly as in `test_search_control.py`.
"""
from __future__ import annotations

import contextlib
import gzip
import hashlib
import importlib.util
import json
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

SIM = Path(__file__).resolve().parents[1]
for p in (SIM.parents[1], SIM, SIM / "scripts/libero", SIM / "scripts/common", SIM / "tests",
          SIM / "scripts/common/node_layout"):
    sys.path.insert(0, str(p))

import executable_world_profile as profile  # noqa: E402
import native_world_fixloop_state as ledger  # noqa: E402
import native_world_protocol as protocol  # noqa: E402
import unit_layout as layout  # noqa: E402
from aspire.sim.cap.world_model import search_control as sc  # noqa: E402
from test_native_world_fixloop import make_case  # noqa: E402
from test_prediction_contract import executable_case  # noqa: E402
from test_search_control import PROTO_WORLD, trace  # noqa: E402

FIXTURE = SIM / "tests/fixtures/p5_budget/drawer_p4_r1c"
P3_SUPPORT = SIM / "docs/experiments/code-world-search-control-p3-20261013/support"
P4_SUPPORT = SIM / "docs/experiments/code-world-coverage-p4-20261016/support"
P5_SUPPORT = SIM / "docs/experiments/code-world-budget-p5-20261019/support"
SEEDS = list(range(51, 66))


# --- harness ----------------------------------------------------------------------


class BudgetHarness:
    """A C/executable p3-search cell under aspire-v1 whose faked replay passes chosen seeds."""

    def __init__(self, stack, budget="aspire-v1"):
        root = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        self.case_path, case = make_case(root, "C")
        case.update(executable_case(prediction_contract="p3-search"))
        if budget:
            case["development_budget"] = budget
        self.case_path.write_text(json.dumps(case))
        self.case, self.repo, self.task_dir = protocol.load_case(self.case_path)
        stack.enter_context(patch.object(protocol, "verify_runtime", lambda case, repo: None))
        stack.enter_context(patch("executable_world_profile.checks", return_value={
            "status": "checked", "directory": "/synthetic/offline", "reports": [{"status": "complete"}],
            "conclusions": ["supported_pass"], "cached": False, "retryable": False, "identity": {}}))
        stack.enter_context(patch.object(protocol, "run_replay", self.fake_replay))
        self.events, self.progress = trace(), 0.0
        #: (bundle policy file name, seed) -> passed; default False. "*" = every seed.
        self.passing: dict[str, set] = {}
        self.crashing: dict[str, set] = {}
        self.session = {"observation_only": True, "robot_state_unchanged": True}
        self.state = ledger.NativeWorldState(self.task_dir, protocol.identity(self.case, self.repo))
        self.policy("initial_code.py", "pull")
        (self.task_dir / "initial_world_program.py").write_text(PROTO_WORLD)
        self.current = None

    def policy(self, name, cls="pull", salt=""):
        path = self.task_dir / name
        path.write_text(f'PRIMITIVE_CLASS = "{cls}"\nget_observation()\n# {salt}\n')
        return path

    def fake_replay(self, command, *, repo, env, directory, timeout, stdin_path=None):
        output = Path(command[command.index("--args.output-dir") + 1])
        seed = int(command[command.index("--args.trial") + 1])
        output.mkdir(parents=True, exist_ok=True)
        if "--args.interactive" in command:
            observation_only = env.get(protocol.OBSERVATION_ONLY_ENV) == "1"
            session = {"schema_version": 1, "session": "probe" if observation_only else "diagnostic",
                       "statements": 1, "errors": [], "error_count": 0}
            if observation_only:
                session.update(self.session, refused_actions=[])
            (output / "diagnostic_session.json").write_text(json.dumps(session))
            self.last_env = env
            return 0, ""
        name = self.current
        ok = int(seed in self.passing.get(name, set()) or "*" in self.passing.get(name, set()))
        rc = int(seed in self.crashing.get(name, set()))
        (output / f"trial_{seed:02d}_sandboxrc_{rc}_reward_{float(ok):.3f}_taskcompleted_{ok}").mkdir(
            parents=True)
        world = Path(directory) / "judgment_world"
        world.mkdir(parents=True, exist_ok=True)
        (world / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in self.events))
        (world / "manifest.json").write_text(json.dumps({
            "status": "complete", "binding": "shadow", "self_evaluations": [],
            "search_progress": self.progress, "result": {"sandbox_rc": rc, "task_completed": bool(ok)}}))
        if "snapshot" in Path(directory).parts:
            for image in ("scene_snapshot.jpg", "scene_snapshot_wrist.jpg"):
                (self.task_dir / image).write_bytes(b"jpg")
        return 0, ""

    def trial(self, phase, seed, code=None, *, mode=None, seeds=None):
        world = None if phase in ("snapshot",) else self.task_dir / "initial_world_program.py"
        if phase in ("diagnostic", "probe"):
            world = None
        code = None if phase == "snapshot" else Path(code or self.task_dir / "initial_code.py")
        self.current = code.name if code else None
        declaration = None if mode is None and seeds is None else {"mode": mode or "",
                                                                   "target_seeds": list(seeds or [])}
        stdin = code if phase in ("diagnostic", "probe") else None
        return protocol.run_trial(self.case, self.repo, self.state, phase, seed, code, world,
                                  None, stdin, declaration=declaration)

    def digest(self, name):
        bundle = {"policy": ledger.code_hash((self.task_dir / name).read_text()),
                  "world": ledger.code_hash((self.task_dir / "initial_world_program.py").read_text())}
        return ledger.bundle_identity(bundle)

    # ---- canned histories ------------------------------------------------------

    def to_phase_b(self, passing_v0=(51,)):
        """Snapshot, failing initial on 51, a passing repair on 51 (v0 = fix0.py)."""
        self.trial("snapshot", 51)
        self.trial("initial", 51)
        v0 = self.policy("fix0.py", "pull", "v0")
        self.passing["fix0.py"] = set(passing_v0)
        self.trial("repair", 51, v0)
        assert self.state.budget_phase() == "B"
        return v0

    def sweep(self, code, seeds=None):
        name = Path(code).name
        covered = set(self.state.covered_seeds(self.digest(name)))
        for seed in seeds or SEEDS:
            if seed not in covered:
                self.trial("sweep", seed, code)

    def version(self, name, mode, targets, *, passing=(), crashing=(), sweep=True):
        code = self.policy(name, "pull", name)
        self.passing[name] = set(passing)
        self.crashing[name] = set(crashing)
        record = self.trial("repair", targets[0], code, mode=mode, seeds=targets)
        if sweep:
            self.sweep(code)
        return code, record

    def select_on_disk(self, name):
        shutil.copy(self.task_dir / name, self.task_dir / "fix_code.py")
        shutil.copy(self.task_dir / "initial_world_program.py", self.task_dir / "fix_world_program.py")
        working = self.repo / "outputs/working_codes"
        working.mkdir(parents=True, exist_ok=True)
        (working / f"{self.case['suite']}_{self.case['task']}_fix.py").write_bytes(
            (self.task_dir / "fix_code.py").read_bytes())

    def reports(self):
        (self.task_dir / "task_analysis.md").write_text("analysis\n")
        (self.task_dir / "findings.md").write_text(
            "# Findings\n\n## Root causes observed\nx\n\n## What fixed them\nx\n\n"
            "## Generalizable patterns\nnone\n\n## Blocked seeds\nnone\n")

    def check(self):
        with patch.object(protocol, "world_use", lambda case: None):
            return protocol.check(self.case, self.repo, self.state)

    def select(self, name, reason="best version"):
        self.select_on_disk(name)
        bundle = protocol.selected_bundle(self.case, self.state)
        errors = self.state.selection_errors(bundle, (self.task_dir / "fix_code.py").read_text())
        if errors:
            raise ledger.ProtocolError("; ".join(errors))
        return self.state.select(bundle, reason)


@pytest.fixture
def budget():
    with contextlib.ExitStack() as stack:
        yield lambda **kw: BudgetHarness(stack, **kw)


def codes(result):
    return {i["code"] for i in result.get("issues", [])} if "issues" in result else None


def error_codes(state, bundle):
    return {i["code"] for i in state.budget_issues(bundle)}


# --- A. phase A -------------------------------------------------------------------


def test_identity_carries_the_budget_only_when_set(budget):
    h = budget()
    assert h.state.data["identity"]["development_budget"] == "aspire-v1" and h.state.budgeted()
    plain = budget(budget=None)
    assert "development_budget" not in plain.state.data["identity"] and not plain.state.budgeted()
    with pytest.raises(ledger.ProtocolError, match="unknown development_budget"):
        protocol.budget_revision({"development_budget": "v2", "prediction_contract": "p3-search"})
    with pytest.raises(ledger.ProtocolError, match="p3-search"):
        protocol.budget_revision({"development_budget": "aspire-v1", "prediction_contract": "p2-runtime"})


def test_phase_a_runs_only_on_the_first_seed(budget):
    h = budget()
    h.trial("snapshot", 51)
    assert h.state.budget_phase() == "A"
    with pytest.raises(ledger.ProtocolError, match="phase A: every attempt runs on the first development seed"):
        h.trial("initial", 52)
    h.trial("initial", 51)
    for phase in ("repair", "diagnostic"):
        with pytest.raises(ledger.ProtocolError, match="phase A"):
            h.trial(phase, 53, h.policy("x.py"))
    with pytest.raises(ledger.ProtocolError, match="phase A: no sweep"):
        h.trial("sweep", 52)
    # Not one row for the refused attempts.
    assert [r["seed"] for r in h.state.data["trials"] if r["phase"] != "snapshot"] == [51]


def test_phase_a_attempts_on_the_first_seed_are_uncapped(budget):
    h = budget()
    h.trial("snapshot", 51)
    h.trial("initial", 51)
    for n in range(7):  # 1 + 7 charged rows on seed 51: RETRY_LIMIT (3) is long gone
        h.trial("repair", 51, h.policy(f"r{n}.py", "pull", str(n)))
    h.trial("diagnostic", 51, h.policy("diag.py", None))
    assert h.state.retries_used(51) == 9 and h.state.retries_remaining(51) is None
    progress = h.state.progress()
    assert progress["retry_limit"] is None
    for key in ("seeds_needing_initial", "seeds_exhausted_without_initial",
                "seeds_exhausted_without_graded_evidence", "seeds_pending_repair", "blocked_notes_required"):
        assert progress[key] == [], key
    assert progress["development_budget"]["phase"] == "A"
    assert progress["development_budget"]["phase_a"]["attempts_on_first_seed"] == 9


def test_phase_a_refuses_select_and_never_freezes(budget):
    h = budget()
    h.trial("snapshot", 51)
    h.trial("initial", 51)
    h.reports()
    with pytest.raises(ledger.ProtocolError, match="phase A: seed 51 has not passed"):
        h.select("initial_code.py")
    result = h.check()
    assert not result["ready"] and result["decision_type"] == "incomplete"
    assert any("phase A: seed 51 has not passed" in e for e in result["errors"])
    assert result["development_budget"]["phase"] == "A"
    with patch.object(protocol, "world_use", lambda case: None), pytest.raises(ledger.ProtocolError):
        protocol.finalize(h.case, h.repo, h.state, [])
    assert not h.state.data["stage1_complete"]


def test_phase_a_is_never_terminal_however_many_attempts_fail(budget):
    h = budget()
    h.trial("snapshot", 51)
    h.trial("initial", 51)
    for n in range(12):  # distinct contact classes: p3 saturation still applies in phase A
        h.trial("repair", 51, h.policy(f"r{n}.py", f"class{n}", str(n)))
    h.reports()
    h.select_on_disk("r11.py")
    result = h.check()
    assert result["decision_type"] == "incomplete" and result["terminal_reasons"] == []
    assert result["legal_actions"]["any"] and result["legal_actions"]["charged_retries"] == {"51": "uncapped"}
    assert result["coverage_gate"]["reachability"]["reachable"]


# --- B. phase B -------------------------------------------------------------------


def test_first_pass_on_the_first_seed_starts_phase_b_and_v0_is_swept_free(budget):
    h = budget()
    v0 = h.to_phase_b(passing_v0=(51, 52, 53))
    status = h.state.budget_status()
    assert status["phase"] == "B" and status["phase_a"]["first_pass"]["phase"] == "repair"
    assert status["versions_used"] == 0 and status["incomplete_versions"][0]["label"] == "v0"
    for phase in ("initial", "smoke"):
        with pytest.raises(ledger.ProtocolError, match="phase B"):
            h.trial(phase, 52)
    with pytest.raises(ledger.ProtocolError, match="acting diagnostic session is not available"):
        h.trial("diagnostic", 52, h.policy("d.py", None))
    with pytest.raises(ledger.ProtocolError, match="sweeps only v0"):
        h.trial("sweep", 52)  # the failing phase-A initial bytes are not v0
    h.sweep(v0)
    assert all(not r["spends_retry"] for r in h.state.records("sweep"))
    record = h.state.budget_candidates()[0]
    assert record["complete"] and record["passes"] == [51, 52, 53]


def test_a_version_waits_for_every_earlier_version_to_run_every_seed(budget):
    h = budget()
    h.to_phase_b()
    h.trial("sweep", 52, h.task_dir / "fix0.py")
    with pytest.raises(ledger.ProtocolError, match="every version runs on all development seeds: sweep v0"):
        h.version("v1.py", "missed_handle", [53], sweep=False)
    h.sweep(h.task_dir / "fix0.py")
    h.version("v1.py", "missed_handle", [53], sweep=False)
    with pytest.raises(ledger.ProtocolError, match="sweep v1"):
        h.version("v2.py", "missed_handle", [54], sweep=False)


def test_a_version_declares_its_mode_and_that_modes_failing_seeds(budget):
    h = budget()
    h.sweep(h.to_phase_b(passing_v0=(51, 52)))
    code = h.policy("v1.py", "pull", "v1")
    for mode, seeds, match in ((None, [53], "declares the failure mode"),
                               ("Bad Name", [53], "declares the failure mode"),
                               ("missed_handle", [], "names the failing seeds"),
                               ("missed_handle", [52], "did not fail under any complete"),
                               ("missed_handle", [54, 55], "run a version first on one of its mode's seeds")):
        with pytest.raises(ledger.ProtocolError, match=match):
            h.trial("repair", 53, code, mode=mode, seeds=seeds)
    record = h.trial("repair", 53, code, mode="missed_handle", seeds=[53, 57])
    assert record["budget"] == {"revision": "aspire-v1", "phase": "B", "version": 1,
                                "mode": "missed_handle", "target_seeds": [53, 57]}
    # Tested bytes are covered by a sweep, never re-admitted as another version.
    with pytest.raises(ledger.ProtocolError, match="already ran"):
        h.trial("repair", 57, code, mode="missed_handle", seeds=[57])


def test_mode_declarations_are_refused_outside_the_budget(budget):
    h = budget(budget=None)
    h.trial("snapshot", 51)
    with pytest.raises(ledger.ProtocolError, match="only under the aspire-v1"):
        h.trial("initial", 51, mode="m", seeds=[51])


def test_retry_limit_no_longer_binds_in_phase_b(budget):
    h = budget()
    h.sweep(h.to_phase_b())
    for n in range(4):  # four versions admitted on seed 53, plus seed 53's earlier rows
        h.version(f"v{n}.py", f"mode{n}", [53, 54])
    assert h.state.retries_used(53) == 4 and h.state.budget_status()["versions_used"] == 4


def test_at_most_three_versions_per_mode_then_the_mode_is_blocked(budget):
    h = budget()
    h.sweep(h.to_phase_b())
    for n in range(3):
        h.version(f"v{n}.py", "missed_handle", [53, 54])
    with pytest.raises(ledger.ProtocolError, match="already had 3 versions .*it is blocked"):
        h.version("v3.py", "missed_handle", [53], sweep=False)
    status = h.state.budget_status()
    assert status["mode_versions"] == {"missed_handle": 3}
    assert status["modes"]["missed_handle"]["blocked"] and status["blocked_modes"] == ["missed_handle"]
    # A different mode is still admitted.
    h.version("v3.py", "late_release", [55])
    assert h.state.budget_status()["versions_used"] == 4


def test_an_exhausted_mode_the_reference_passes_is_fixed_not_blocked(budget):
    h = budget()
    h.sweep(h.to_phase_b())
    for n in range(3):
        h.version(f"v{n}.py", "missed_handle", [53], passing=(51,) + ((53,) if n == 2 else ()))
    status = h.state.budget_status(h.digest("v2.py"))
    assert status["modes"]["missed_handle"]["exhausted"] and not status["modes"]["missed_handle"]["blocked"]


def test_at_most_eight_versions(budget):
    h = budget()
    h.sweep(h.to_phase_b())
    for n in range(8):
        h.version(f"v{n}.py", f"mode{n // 3}", [53])
    with pytest.raises(ledger.ProtocolError, match="all 8 versions are used"):
        h.version("v8.py", "mode9", [53], sweep=False)
    assert h.state.budget_status()["versions_left"] == 0


def test_an_infrastructure_lost_version_is_readmitted_without_a_new_number(budget):
    h = budget()
    h.sweep(h.to_phase_b())
    code = h.policy("v1.py", "pull", "v1")
    bundle = {"policy": ledger.code_hash(code.read_text()),
              "world": ledger.code_hash((h.task_dir / "initial_world_program.py").read_text())}
    record = h.state.begin_trial("repair", 53, bundle, {"policy": str(code)},
                                 {"mode": "missed_handle", "target_seeds": [53]})
    h.state.finish_trial(record, result=None, exit_code=None, error="unit down")
    h.state.resolve_interrupted(record, result=None, exit_code=None, error="unit down",
                                recovery={"bundle_sha256": record["bundle_sha256"],
                                          "disposition": "closed_without_usable_evidence"})
    again = h.trial("repair", 53, code)  # no declaration needed: the version already exists
    assert again["budget"]["version"] == 1 and again["budget"]["readmission"]
    assert h.state.budget_status()["versions_used"] == 1


def test_selection_is_most_passes_then_fewer_crashes(budget):
    h = budget()
    h.sweep(h.to_phase_b(passing_v0=(51, 52)))
    h.version("v1.py", "m1", [53], passing=(51, 52, 53), crashing=(60,))
    h.version("v2.py", "m2", [53], passing=(51, 52, 53))
    best = h.state.best_complete()
    assert best["bundle_sha256"] == [h.digest("v2.py")] and best["score"] == [3, 0]
    assert "selection_not_best" in error_codes(h.state, {"policy": ledger.code_hash(
        (h.task_dir / "v1.py").read_text()), "world": ledger.code_hash(PROTO_WORLD)})
    with pytest.raises(ledger.ProtocolError, match="most passes, then fewer crashes"):
        h.select("v1.py")


# --- C. freeze conditions ------------------------------------------------------------


def _ready(h, name):
    h.reports()
    h.select(name)
    return h.check()


def test_freeze_when_the_selection_passes_every_seed(budget):
    h = budget()
    h.sweep(h.to_phase_b(passing_v0=("*",)))
    result = _ready(h, "fix0.py")
    assert result["ready"], result["errors"]
    assert result["development_budget"]["freeze_conditions"]["all_pass"]


def test_no_freeze_condition_refuses_select_and_check(budget):
    h = budget()
    h.sweep(h.to_phase_b(passing_v0=(51, 52)))
    with pytest.raises(ledger.ProtocolError, match="no freeze condition holds yet"):
        h.select("fix0.py")
    h.reports()
    h.select_on_disk("fix0.py")
    result = h.check()
    assert not result["ready"] and result["decision_type"] == "incomplete"
    assert result["legal_actions"]["versions_left"] == 8 and result["legal_actions"]["any"]


def test_freeze_when_all_eight_versions_are_used_with_honest_failures(budget):
    h = budget()
    h.sweep(h.to_phase_b(passing_v0=(51,)))
    for n in range(8):
        h.version(f"v{n}.py", f"mode{n}", [53], passing=(51, 52) if n == 4 else (51,))
    result = _ready(h, "v4.py")
    assert result["ready"], result["errors"]
    freeze = result["development_budget"]["freeze_conditions"]
    assert freeze["versions_exhausted"] and not freeze["all_pass"] and freeze["failing_seeds"]


def test_freeze_when_every_remaining_failure_is_in_a_blocked_mode(budget):
    h = budget()
    h.sweep(h.to_phase_b(passing_v0=tuple(s for s in SEEDS if s not in (53, 57))))
    for n in range(3):
        h.version(f"v{n}.py", "missed_handle", [53, 57],
                  passing=tuple(s for s in SEEDS if s not in (53, 57)))
    h.reports()
    h.select("v2.py")
    result = h.check()
    assert not result["ready"] and any("mode_missed_handle_BLOCKED.md" in e for e in result["errors"])
    (h.task_dir / "attempts").mkdir(exist_ok=True)
    (h.task_dir / "attempts/mode_missed_handle_BLOCKED.md").write_text(
        "## Root Cause: Physical\nhandle\n## Details: seeds 53, 57\n## What Was Tried: v1-v3\n")
    result = h.check()
    assert result["ready"], result["errors"]
    freeze = result["development_budget"]["freeze_conditions"]
    assert freeze["all_remaining_blocked"] and freeze["unattributed_failing_seeds"] == []
    assert result["development_budget"]["blocked_modes"] == ["missed_handle"]


def test_a_failure_outside_every_blocked_mode_keeps_the_cell_open(budget):
    h = budget()
    h.sweep(h.to_phase_b(passing_v0=tuple(s for s in SEEDS if s not in (53, 57, 60))))
    for n in range(3):
        h.version(f"v{n}.py", "missed_handle", [53, 57],
                  passing=tuple(s for s in SEEDS if s not in (53, 57, 60)))
    with pytest.raises(ledger.ProtocolError, match=r"not in a blocked mode \[60\]"):
        h.select("v2.py")


# --- D. probes ----------------------------------------------------------------------


PROBE = 'obs = get_observation()\nmasks = segment_sam3_text_prompt(obs["agentview"]["images"]["rgb"], "handle")\n' \
        'print(len(masks), env.handle.task_language, flush=True)\n'


def test_a_probe_spends_nothing_and_is_never_a_version(budget):
    h = budget()
    h.trial("snapshot", 51)
    probe = h.task_dir / "probe.py"
    probe.write_text(PROBE)
    record = h.trial("probe", 57, probe)  # any development seed, even in phase A
    assert record["status"] == "complete" and record["spends_retry"] is False
    assert record["directory"] == "development/probe/seed_57/attempt_1"
    assert h.last_env[protocol.OBSERVATION_ONLY_ENV] == "1"
    assert h.state.retries_used(57) == 0 and h.state.candidates() == {}
    h.trial("initial", 51)  # reach phase B: probes stay free there too
    v0 = h.policy("fix0.py", "pull", "v0"); h.passing["fix0.py"] = {51}
    h.trial("repair", 51, v0)
    again = h.trial("probe", 57, probe)
    assert again["directory"].endswith("attempt_2")
    status = h.state.budget_status()
    assert status["probes"] == 2 and status["versions_used"] == 0
    assert h.digest("fix0.py") in h.state.candidates() and len(h.state.candidates()) == 2


@pytest.mark.parametrize("source, match", [
    ("move_to_joints([0] * 7)\n", "may not call move_to_joints"),
    ("close_gripper()\n", "may not call close_gripper"),
    ("step('open_gripper()')\n", "may not use step"),
    ("print(get_observation.__self__)\n", "dunder attribute"),
    ("import sys\nprint(sys.modules)\n", "may not import sys"),
    ("print(env.handle.task)\n", "only env.handle.task_language"),
    ("print(env.handle.env)\n", "forbidden simulator ground-truth"),
    ("f = getattr(env, 'step')\n", "may not use getattr"),
])
def test_a_probe_that_names_an_action_is_refused_before_it_runs(budget, source, match):
    h = budget()
    probe = h.task_dir / "probe.py"
    probe.write_text(source)
    with pytest.raises(ledger.ProtocolError, match=match):
        h.trial("probe", 51, probe)
    assert not h.state.records("probe") and h.state.data["rejected"][-1]["phase"] == "probe"


def test_a_probe_that_moved_the_robot_is_a_violation(budget):
    h = budget()
    probe = h.task_dir / "probe.py"
    probe.write_text(PROBE)
    h.session = {"observation_only": True, "robot_state_unchanged": False,
                 "robot_state_change": {"robot_joint_pos": 0.2}}
    record = h.trial("probe", 51, probe)
    assert record["status"] == ledger.PROBE_VIOLATION and not record["spends_retry"]
    assert h.state.progress()["infrastructure_errors"] == []
    issues = h.state.completion_issues(bundle={"policy": "0" * 64}, working_code=h.task_dir / "none",
                                       world_required=True)
    assert "probe_violations" in {i["code"] for i in issues}
    assert "probe_violations" not in protocol.AUTHORABLE_ISSUES


def test_probes_exist_only_under_the_budget(budget):
    h = budget(budget=None)
    probe = h.task_dir / "probe.py"
    probe.write_text(PROBE)
    with pytest.raises(ledger.ProtocolError, match="only under the aspire-v1"):
        h.trial("probe", 51, probe)


def test_the_replay_repl_runs_a_probe_observation_only(tmp_path, monkeypatch):
    """The real REPL, with a fake environment: no motor command, no step, read-only env."""
    import io
    sys.modules.pop("aspire.sim.scripts.libero.replay_trial", None)
    replay = pytest.importorskip("aspire.sim.scripts.libero.replay_trial")
    joints = [0.0] * 7

    class Api:
        def functions(self):
            return {"get_observation": lambda: {"robot_joint_pos": list(joints),
                                                "robot_cartesian_pos": [0.0] * 8},
                    "move_to_joints": lambda q: joints.__setitem__(slice(None), list(q)),
                    "segment_sam3_text_prompt": lambda rgb, text: []}

        def get_trace_logger(self):
            return "trace"

    env = SimpleNamespace(_apis={"api": Api()}, low_level_env=SimpleNamespace(
        handle=SimpleNamespace(task_language="open the drawer")), step=lambda code: None)
    args = SimpleNamespace(suite="s", task="t", trial=51, output_dir=str(tmp_path))
    script = ("print(env.handle.task_language)\n"
              "try:\n    move_to_joints([1.0] * 7)\nexcept PermissionError as exc:\n    print(exc)\n"
              "print('step' in globals(), 'trace_logger' in globals())\n")
    monkeypatch.setenv(replay.OBSERVATION_ONLY_ENV, "1")
    monkeypatch.setattr(sys, "stdin", io.StringIO(script))
    replay._run_interactive_repl(env, {}, args)
    session = json.loads((tmp_path / replay.DIAGNOSTIC_SESSION_ARTIFACT).read_text())
    assert session["observation_only"] and session["robot_state_unchanged"]
    assert session["refused_actions"] == ["move_to_joints"] and joints == [0.0] * 7
    assert session["session"] == "probe" and session["error_count"] == 0
    # The same REPL without the switch is the unchanged acting diagnostic session.
    monkeypatch.delenv(replay.OBSERVATION_ONLY_ENV)
    monkeypatch.setattr(sys, "stdin", io.StringIO("move_to_joints([1.0] * 7)\n"))
    out = tmp_path / "acting"
    replay._run_interactive_repl(env, {}, SimpleNamespace(**{**vars(args), "output_dir": str(out)}))
    session = json.loads((out / replay.DIAGNOSTIC_SESSION_ARTIFACT).read_text())
    assert joints == [1.0] * 7 and "observation_only" not in session and session["session"] == "diagnostic"


# --- E. reporting --------------------------------------------------------------------


def test_check_status_and_search_control_report_the_budget(budget):
    h = budget()
    h.sweep(h.to_phase_b(passing_v0=(51, 52)))
    h.version("v1.py", "missed_handle", [53], passing=(51, 52))
    h.reports()
    h.select_on_disk("v1.py")
    result = h.check()
    block = result["development_budget"]
    for key in ("phase", "versions_used", "versions_left", "mode_versions", "blocked_modes",
                "freeze_conditions", "candidates", "probes", "next"):
        assert key in block, key
    assert block["phase"] == "B" and block["versions_used"] == 1 and block["mode_versions"] == {"missed_handle": 1}
    assert result["legal_actions"]["phase"] == "B"
    summary = protocol.search_control(h.case, h.state)
    assert summary["remaining_budget"]["development_budget"]["versions_used"] == 1
    assert any("aspire-v1 phase B" in rule for rule in summary["rules"])
    assert not any("sweep only the candidate you intend to freeze" in rule for rule in summary["rules"])
    plain = budget(budget=None)
    plain.trial("snapshot", 51)
    plain.trial("initial", 51)
    assert sc.rules_for(plain.state.data) == sc.RULES
    assert "development_budget" not in plain.state.progress()


def test_finalize_writes_the_budget_into_the_result(budget):
    h = budget()
    h.sweep(h.to_phase_b(passing_v0=("*",)))
    h.reports()
    h.select("fix0.py")
    transcript = h.task_dir.parent / "t.jsonl"
    transcript.write_text(json.dumps({"type": "assistant", "message": {"model": h.case["expected_served_model"]}})
                          + "\n" + json.dumps({"type": "result", "usage": {}, "modelUsage": {
                              "m": {"contextWindow": h.case["context_tokens"],
                                    "maxOutputTokens": h.case["max_output_tokens"]}}}) + "\n")
    with patch.object(protocol, "world_use", lambda case: None):
        result = protocol.finalize(h.case, h.repo, h.state, [transcript])
    assert result["development_budget"]["freeze_conditions"]["all_pass"]
    assert result["retry_limit"] == ledger.RETRY_LIMIT  # the identity's legacy field, unchanged


# --- F. prompts ----------------------------------------------------------------------


def _staged_case():
    case = json.loads((FIXTURE / "control/case.json").read_text())
    case["sim"] = str(SIM)
    return case


def _render(case):
    import native_world_campaign as campaign
    spec = importlib.util.spec_from_file_location("p5_scope", P3_SUPPORT / "two_task_scope.py")
    scope = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scope)
    campaign = importlib.reload(campaign)
    scope.apply(campaign, case)
    return (campaign.worker_prompt(case, SIM),
            campaign.coordinator_prompt(case, Path(case["control"]) / "worker-prompt.md"))


def test_non_budget_renders_are_byte_identical_to_the_frozen_p4fix_prompts():
    case = _staged_case()
    worker, coordinator = _render(case)
    frozen = (FIXTURE / "control/worker-prompt.md").read_text()
    # The staged prompt was re-rendered at claim time for slot GPU 2.
    assert worker.replace(f"GPU:   {case['gpu']}", "GPU:   2").replace(
        f"physical GPU {case['gpu']} ", "physical GPU 2 ") == frozen
    frozen_sim = json.loads((FIXTURE / "control/case.json").read_text())["sim"]
    assert coordinator.replace(str(SIM), frozen_sim) == (FIXTURE / "control/coordinator-prompt.md").read_text()


def test_the_budget_render_replaces_the_per_seed_retry_text():
    case = dict(_staged_case(), development_budget="aspire-v1")
    worker, coordinator = _render(case)
    for gone in ("Hard limit: 3 TOTAL retries per seed", "three total attempts per seed",
                 "One repair round is allowed", "Each of these spends one of that seed's three retries",
                 "sweep only the candidate you intend to freeze", "Sweep only the candidate you intend to freeze",
                 "re-test it on the SAME failed seed while that"):
        assert gone not in worker, gone
    for present in ("Development budget aspire-v1", "**Development budget (aspire-v1)**",
                    "--phase initial --seed 51", "At most 3 versions per mode", "at most **8 versions**",
                    "--phase probe", "attempts/mode_<mode>_BLOCKED.md", "Freeze only when one of these holds",
                    "100% success is not required", "Coverage before freezing (p4)", "Repair the MODE"):
        assert present in worker, present
    assert "charged attempt" not in worker  # forbidden by the frozen two_task_scope gate
    assert "keep the worker on the first development seed" in coordinator
    assert "100% success is not required" in coordinator
    assert worker.count(profile.BUDGET_CONTRACT.strip()) == 1


def test_profile_section_is_unchanged_without_the_budget():
    case = executable_case(prediction_contract="p3-search")
    section = profile.section(case)
    assert section.count(profile.SEARCH_CONTRACT) == 1 and "Development budget aspire-v1" not in section
    budget = dict(case, development_budget="aspire-v1")
    text = profile.section(budget)
    assert profile.SEARCH_CONTRACT not in text and text.count(profile.BUDGET_CONTRACT) == 1
    with pytest.raises(ValueError, match="requires prediction_contract p3-search"):
        profile.validate(dict(executable_case(prediction_contract="p2-runtime"), development_budget="aspire-v1"))
    # SEARCH_CONTRACT itself is the CC_TASK_4 text (its digest is pinned there).
    pinned = (SIM / "docs/experiments/code-world-coverage-p4-20261016/CC_TASK_4_PROMPT_SECTION.md").read_text()
    assert hashlib.sha256(profile.SEARCH_CONTRACT.encode()).hexdigest() in pinned


# --- G. supervisor, support overlay, driver ---------------------------------------------


def _cells(tmp_path, names):
    out = []
    for name in names:
        (tmp_path / name).mkdir()
        out.append(tmp_path / name)
    return out


def test_node_cells_pins_each_cell_to_one_node(tmp_path):
    cells = _cells(tmp_path, ["a", "b", "c", "d", "e"])
    study = {"node_cells": {"0": ["a", "c"], "1": ["b"], "2": ["d", "e"]}}
    assert [c.name for c in layout.node_cells(study, cells, 0, 3)] == ["a", "c"]
    assert [c.name for c in layout.node_cells(study, cells, 2, 3)] == ["d", "e"]
    assert [c.name for c in layout.node_cells({}, cells, 1, 3)] == ["a", "b", "c", "d", "e"]  # shared queue
    for bad, nodes, match in (({"0": ["a", "b", "c", "d"], "1": ["e", "a"]}, 2, "partition"),
                              ({"0": ["a", "b", "c"], "1": ["d"]}, 2, "partition"),
                              ({"0": ["a", "b", "c", "d", "e"]}, 2, "node-count")):
        with pytest.raises(RuntimeError, match=match):
            layout.node_cells({"node_cells": bad}, cells, 0, nodes)
    with pytest.raises(RuntimeError, match="node-id"):
        layout.node_cells(study, cells, 3, 3)


def test_a_pinned_node_claims_only_its_own_cells(tmp_path):
    cells = _cells(tmp_path, ["a", "b", "c"])
    study = {"node_cells": {"0": ["b"], "1": ["a", "c"]}}
    mine = layout.node_cells(study, cells, 1, 2)
    first = layout.claim_next(tmp_path, mine, "w1", {})
    second = layout.claim_next(tmp_path, mine, "w1", {})
    assert [first.name, second.name] == ["a", "c"] and layout.claim_next(tmp_path, mine, "w1", {}) is None
    assert layout.claim_next(tmp_path, layout.node_cells(study, cells, 0, 2), "w0", {}).name == "b"


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_p5_deadlines_differ_from_p4_only_in_the_development_watchdog():
    p4 = _load(P4_SUPPORT / "full_deadlines.py", "p4_deadlines_for_p5_test")
    p5 = _load(P5_SUPPORT / "full_deadlines.py", "p5_deadlines_for_p5_test")
    assert p4.DEVELOPMENT == 14 * 3600 and p5.DEVELOPMENT == 24 * 3600
    for name in ("TRIAL_TIMEOUT", "SERVICE_STARTUP", "NATIVE_COMPAT", "HELDOUT", "HELDOUT_SEED_MARGIN",
                 "HELDOUT_SWEEP_OVERHEAD", "FINALIZE_MARGIN"):
        assert getattr(p4, name) == getattr(p5, name), name
    assert p5.heldout_deadline(50, 900) == p4.heldout_deadline(50, 900) == 51120
    cell = p5.DEVELOPMENT + p5.HELDOUT + p5.FINALIZE_MARGIN
    assert cell == 139320 and p5.TOTAL == 143640 and p5.DLC_MAX_RUNNING_MINUTES == 2394


def test_the_p5_job_window_fits_one_whole_cell_and_the_p4_window_does_not():
    sys.path.insert(0, str(SIM / "scripts/common/node_layout"))
    import unit_supervisor as supervisor
    p5 = _load(P5_SUPPORT / "full_deadlines.py", "p5_deadlines_window")
    cell = p5.DEVELOPMENT + p5.HELDOUT + p5.FINALIZE_MARGIN
    minimum = supervisor.minimum_window_minutes(cell)
    assert minimum == 2369
    window, job_max = 2420, 2430  # the p5 submit scripts
    assert window >= minimum and job_max > window and job_max >= p5.DLC_MAX_RUNNING_MINUTES
    assert supervisor.minimum_window_minutes(cell) > 1800  # the p4 / supplement 1800 window is refused


def test_p5_driver_differs_from_p4_only_in_the_coordinator_text(tmp_path):
    import ast
    def functions(path):
        tree = ast.parse(path.read_text())
        return {n.name: ast.dump(n) for n in tree.body if isinstance(n, ast.FunctionDef)}
    p4, p5 = functions(P4_SUPPORT / "run_two_task_cell.py"), functions(P5_SUPPORT / "run_two_task_cell.py")
    changed = {n for n in p4 if n in p5 and p4[n] != p5[n]}
    assert changed == {"continuation", "main"} and set(p5) - set(p4) == {"budgeted", "budget_continuation",
                                                                      "round_suffix"}
    support = tmp_path / "support"
    support.mkdir()
    for source in (*P3_SUPPORT.glob("*.py"), *P4_SUPPORT.glob("*.py"), *P5_SUPPORT.glob("*.py")):
        (support / source.name).write_bytes(source.read_bytes())
    sys.path.insert(0, str(support))
    for module in [m for m in sys.modules if m in {p.stem for p in support.glob("*.py")}]:
        sys.modules.pop(module, None)
    try:
        driver = _load(support / "run_two_task_cell.py", "p5_driver_text")
    finally:
        sys.path.remove(str(support))
    budget_case = {"development_budget": "aspire-v1"}
    text = driver.continuation("w", 600, budget_case)
    assert "three-retry cap" not in text and "Phase A" in text and "8 in total" in text
    assert "three-retry cap per seed" in driver.continuation("w", 600, {})
    assert "development_budget phase A" in driver.round_suffix(budget_case)
    assert "seeds_pending_repair" in driver.round_suffix({})
    assert driver.full_deadlines.DEVELOPMENT == 24 * 3600


# --- H. replay: drawer_p4_r1c under the new rules -----------------------------------------


def _replay_state(tmp_path, *, budgeted):
    task_dir = tmp_path / "drawer_p4_r1c"
    task_dir.mkdir()
    raw = gzip.decompress((FIXTURE / "development_state.json.gz").read_bytes())
    assert hashlib.sha256(raw).hexdigest() == (FIXTURE / "development_state.sha256").read_text().strip()
    data = json.loads(raw)
    if budgeted:
        data["identity"]["development_budget"] = "aspire-v1"
    (task_dir / "development_state.json").write_text(json.dumps(data))
    for name in ("fix_code.py", "fix_world_program.py", "findings.md", "task_analysis.md"):
        shutil.copy(FIXTURE / name, task_dir / name)
    shutil.copytree(FIXTURE / "attempts", task_dir / "attempts")
    state = ledger.NativeWorldState.__new__(ledger.NativeWorldState)
    state.task_dir, state.path, state.data = task_dir, task_dir / "development_state.json", data
    state.seeds, state.gate, state.sealed = data["identity"]["dev_seeds"], "oracle", False
    data["stage1_complete"] = False  # replay the freeze decision, not the frozen flag
    repo = tmp_path / "repo"
    working = repo / "outputs/working_codes"
    working.mkdir(parents=True)
    for path in (FIXTURE / "working_codes").iterdir():
        shutil.copy(path, working / path.name)
    return state, repo


CASE = {"suite": "libero_goal_swap", "task": "open_the_middle_drawer_of_the_cabinet", "condition": "C",
        "profile": "judgment", "prediction_contract": "p3-search", "development_gate": "oracle",
        "foundation_revision": "r1", "world_use_revision": "r1"}


def _replay_check(state, repo, case):
    with patch.object(protocol, "search_control", lambda case, state: None), \
            patch.object(protocol, "coverage_triage", lambda case, state: None), \
            patch.object(protocol, "world_use", lambda case: None), \
            patch.object(ledger.NativeWorldState, "save", lambda self: None):
        return protocol.check(case, repo, state)


def test_replay_drawer_p4_r1c_froze_at_zero_passes_under_p4(tmp_path):
    state, repo = _replay_state(tmp_path, budgeted=False)
    selected = state.data["selected"]["bundle_sha256"]
    candidates = state.candidates()
    assert not any(row["passes"] for row in candidates.values())  # 0/15, every bundle
    result = _replay_check(state, repo, CASE)
    assert result["ready"] and result["coverage_gate"]["complete"]  # what the real cell did
    assert result["selected"]["bundle_sha256"] == selected


def test_replay_drawer_p4_r1c_cannot_freeze_at_zero_passes_under_the_budget(tmp_path):
    state, repo = _replay_state(tmp_path, budgeted=True)
    assert state.budget_phase() == "A" and state.first_pass() is None
    result = _replay_check(state, repo, dict(CASE, development_budget="aspire-v1"))
    assert not result["ready"] and result["decision_type"] == "incomplete"
    assert result["terminal_reasons"] == []  # phase A is never terminal; only the deadline ends it
    assert any("phase A: seed 51 has not passed" in e for e in result["errors"])
    assert result["development_budget"]["phase"] == "A"
    assert result["legal_actions"]["charged_retries"] == {"51": "uncapped"}
    bundle = protocol.selected_bundle(CASE, state)
    errors = state.selection_errors(bundle, (state.task_dir / "fix_code.py").read_text())
    assert any("phase A" in e for e in errors)  # `select` is refused too
    # The p4 history spent seed 51's three retries; phase A would admit a fourth attempt there.
    assert state.retries_used(51) == 3
    digest = protocol.bundle_identity({"policy": "a" * 64, "world": "b" * 64})
    assert state.validate_admission("repair", 51, digest) == {"revision": "aspire-v1", "phase": "A"}
    with pytest.raises(ledger.ProtocolError, match="phase A"):
        state.validate_admission("sweep", 65, state.data["selected"]["bundle_sha256"])


def test_p5_infra_guard_allows_uncapped_diagnostics_but_stops_on_infrastructure():
    p3 = _load(P3_SUPPORT / "infra_guard.py", "p3_infra_guard_for_p5")
    p5 = _load(P5_SUPPORT / "infra_guard.py", "p5_infra_guard_for_p5")

    def ledger_with(statuses, budget):
        rows = [{"phase": "diagnostic", "seed": 51, "spends_retry": True, "status": s} for s in statuses]
        return {"identity": {"development_budget": "aspire-v1"} if budget else {}, "trials": rows}

    diagnostics = ["complete", "diagnostic_program_error", "complete", "complete"]
    assert p3.classify(ledger_with(diagnostics, False))["kind"] == "terminal_missing_graded_evidence"
    assert p5.classify(ledger_with(diagnostics, False)) == p3.classify(ledger_with(diagnostics, False))
    assert p5.classify(ledger_with(diagnostics, True)) is None  # phase A: uncapped sessions are legal
    infra = ["infrastructure_error"] * 3
    assert p5.classify(ledger_with(infra, True))["kind"] == "terminal_missing_graded_evidence"
    swept = ledger_with(infra, True)
    swept["trials"].append({"phase": "sweep", "seed": 51, "spends_retry": False, "status": "complete"})
    assert p5.classify(swept) is None


def test_the_rendered_budget_prompt_section_reads_its_text_from_source():
    sys.path.insert(0, str(SIM / "scripts/libero"))
    renderer = _load(SIM / "scripts/libero/render_prompt_section.py", "p5_render_section")
    text, meta = renderer.render("CC_TASK_6", budget=True)
    assert meta["occurrences"] == 1
    assert profile.BUDGET_CONTRACT.strip() in text
    assert "Development budget (aspire-v1)" in text and "keep the worker on the first development seed" in text
    plain, meta4 = renderer.render("CC_TASK_4")
    assert "Development budget aspire-v1" not in plain
    assert meta4["sha256"] == hashlib.sha256(profile.SEARCH_CONTRACT.encode()).hexdigest()
