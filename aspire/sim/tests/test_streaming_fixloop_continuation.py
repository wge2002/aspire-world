"""CPU tests for the opt-in streaming Fix Loop repair continuation.

No simulator, GPU, model service, SAM2 weights or private experiment artifacts.
Parent ledgers are produced by the real protocol on synthetic fixtures with the
replay faked at `run_replay` (as in test_streaming_fixloop), then a stub
engineering file is edited to stand in for the corrected runtime.
"""
from __future__ import annotations

import io
import json
import shutil
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

SIM = Path(__file__).resolve().parents[1]
for extra in (SIM.parents[1], SIM / "scripts/common", SIM / "scripts/libero", SIM / "tests"):
    sys.path.insert(0, str(extra))

import streaming_dlc_pair as pair  # noqa: E402
import streaming_fixloop_campaign as camp  # noqa: E402
import streaming_fixloop_continuation as cont  # noqa: E402
import streaming_fixloop_heldout as sh  # noqa: E402
import streaming_fixloop_protocol as sp  # noqa: E402
from test_streaming_fixloop import (CONFIG, FINDINGS, POLICY, author, cell, cli,  # noqa: E402,F401
                                    native_records, task_dir, transcript_records, trial)

FIX = POLICY + "print('parent repair')\n"
PASS, FAIL, CRASH, TIMEOUT = (0, "", 0, 1), (0, "", 0, 0), (0, "", 1, 0), (None, "TimeoutExpired: 900", 0, 0)


def run(path: Path, clock, *argv):
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        code = sp.main(["--case", str(path), *argv], clock=clock)
    return code, json.loads(buffer.getvalue())


class Parent:
    """A parent cell run by the real protocol in its own (old) control directory."""

    def __init__(self, c, name="old"):
        self.c = c
        self.case = {**c.case, "control": str(c.path.parent / name / c.case["id"])}
        self.path = c.path.parent / f"{name}-case.json"
        self.path.write_text(json.dumps(self.case))
        self.task = Path(self.case["control"]).resolve() / "cell"

    def cli(self, *argv):
        return run(self.path, self.c.clock, *argv)

    def author(self, stem, policy=POLICY, config=None):
        (self.task / f"{stem}_code.py").write_text(policy)
        (self.task / f"{stem}_streaming_config.json").write_text(json.dumps(config or CONFIG))

    def trial(self, phase, seed, stem="initial", outcome=FAIL):
        self.c.fake.outcomes[seed] = outcome
        code, record = self.cli("trial", "--phase", phase, "--seed", str(seed), "--code",
                                str(self.task / f"{stem}_code.py"), "--streaming-config",
                                str(self.task / f"{stem}_streaming_config.json"))
        assert code in (0, 1), record
        return record

    def start(self):
        assert self.cli("init")[0] == 0 and self.cli("snapshot")[0] == 0
        self.author("initial")


def full_parent(c, name="old") -> Parent:
    """All 15 initial seeds covered; alias, timeout, crash, diagnostic, exhaustion, two passes."""
    p = Parent(c, name)
    p.start()
    p.trial("smoke", 51)
    assert p.cli("alias-smoke", "--seed", "51")[0] == 0
    outcomes = {53: TIMEOUT, 54: CRASH, 65: PASS}
    for seed in range(52, 66):
        p.trial("initial", seed, outcome=outcomes.get(seed, FAIL))
    p.trial("initial", 53)  # the timed-out initial is owed again; still charged
    p.author("fix", policy=FIX)
    p.trial("repair", 52, "fix")
    p.trial("repair", 52, "fix")
    p.trial("diagnostic", 55)
    p.trial("repair", 61, "fix", outcome=PASS)
    return p


PARENT_CHARGED = {51: 1, 52: 3, 53: 2, 54: 1, 55: 2, 61: 2}  # every other seed: 1


def partial_parent(c, name="old") -> Parent:
    """Initial coverage on seeds 51-55 only, like a cell stopped mid-batch."""
    p = Parent(c, name)
    p.start()
    for seed in range(51, 56):
        p.trial("initial", seed)
    return p


def corrected_runtime(c):
    (c.sim / "cap/world_model/World.py").write_text("# corrected engineering world\n")


def ledger_copy(c) -> Path:
    return Path(c.case["control"]).resolve() / "parent/cell/development_state.json"


def bind(c, **parent_overrides):
    ledger = ledger_copy(c)
    contract = {"schema": cont.SCHEMA, "authorization": "synthetic fixture: user-authorized continuation",
                "parent": {"cell": c.case["id"], "development_state": str(ledger),
                           "development_state_sha256": sp.file_sha256(ledger), **parent_overrides}}
    c.case = {**{k: v for k, v in c.case.items() if k != "continuation"}, "continuation": contract}
    c.path.write_text(json.dumps(c.case))


def continue_from(c, parent: Parent, *, mode=0o400):
    target = ledger_copy(c).parent
    shutil.copytree(parent.task, target)
    ledger_copy(c).chmod(mode)
    bind(c)


def tamper(c, edit):
    ledger = ledger_copy(c)
    ledger.chmod(0o600)
    data = json.loads(ledger.read_text())
    edit(data)
    ledger.write_text(json.dumps(data))
    ledger.chmod(0o400)
    bind(c)


def continued(c, builder=full_parent, *, fix_runtime=True):
    parent = builder(c)
    if fix_runtime:
        corrected_runtime(c)
    continue_from(c, parent)
    code, ledger = cli(c, "init")
    assert code == 0, ledger
    return parent


def status(c) -> dict:
    code, result = cli(c, "status")
    assert code == 0, result
    return result


def outcome(result, seed) -> dict:
    return next(s for s in result["seeds"] if s["seed"] == seed)


def copy_parent_files(c, *names):
    for name in names:
        shutil.copy(ledger_copy(c).parent / name, task_dir(c) / name)


# --- import and accounting ----------------------------------------------------------


def test_init_imports_parent_once_with_cumulative_charged_attempts(cell):
    parent = continued(cell)
    result = status(cell)
    used = {int(k): v for k, v in result["retries_used_per_seed"].items()}
    assert used == {s: PARENT_CHARGED.get(s, 1) for s in range(51, 66)}
    summary = result["continuation"]
    totals = summary["execution_totals"]
    assert totals["historical"]["charged_attempts"] == 20 and totals["new"]["charged_attempts"] == 0
    assert totals["historical"]["aliases"] == 1  # the alias spent nothing
    assert result["budget"]["charged_attempts_used"] == 20
    assert result["budget"]["historical_charged_attempts"] == 20
    # Timeout, crash and diagnostic rows stay charged and stay historical.
    assert outcome(result, 53)["historical"]["statuses"]["infrastructure_error"] == 1
    assert outcome(result, 55)["historical"]["retries_used"] == 2
    assert result["infrastructure_errors"] == [] and result["replay_invocations"] == 0
    # The parent's snapshot is this cell's snapshot; nothing new was run.
    assert result["snapshot_complete"] and result["snapshot_source"] == "parent (historical)"
    assert summary["owed_initial_seeds"] == []
    ledger = json.loads((task_dir(cell) / "development_state.json").read_text())
    old = json.loads((parent.task / "development_state.json").read_text())
    assert ledger["trials"] == [] and ledger["continuation"]["historical"]["trials"] == old["trials"]
    assert ledger["continuation"]["historical"]["phase_clock"] == old["phase_clock"]
    assert ledger["identity"]["continuation"]["runtime_changed"] is True
    assert ledger["identity"]["continuation"]["parent_development_state_sha256"] == \
        sp.file_sha256(ledger_copy(cell))
    # Neither the old ledger nor its copy was written by the import.
    assert sp.file_sha256(parent.task / "development_state.json") == sp.file_sha256(ledger_copy(cell))


def test_new_phase_charges_crash_timeout_and_diagnostic_on_top_of_history(cell):
    continued(cell)
    author(cell, "fix", policy=FIX + "print('corrected')\n")
    cell.fake.outcomes[54] = TIMEOUT
    assert trial(cell, "repair", 54, "fix")[1]["status"] == "infrastructure_error"
    cell.fake.outcomes[56] = CRASH
    trial(cell, "repair", 56, "fix")
    cell.fake.outcomes[57] = FAIL
    assert trial(cell, "diagnostic", 57, "fix")[1]["status"] == "complete"
    result = status(cell)
    assert {k: result["retries_used_per_seed"][k] for k in ("54", "56", "57")} == \
        {"54": 2, "56": 2, "57": 2}
    assert outcome(result, 54)["new_retries_used"] == 1
    assert result["continuation"]["execution_totals"]["new"]["charged_attempts"] == 3
    assert result["continuation"]["execution_totals"]["cumulative_charged_attempts"] == 23
    # New attempt directories continue the cumulative numbering.
    assert json.loads((task_dir(cell) / "development_state.json").read_text())["trials"][0][
        "directory"] == "development/repair/seed_54/attempt_2"


def test_exhausted_seed_snapshot_smoke_and_alias_cannot_run_again(cell):
    continued(cell)
    author(cell)  # the parent's initial program, byte-identical
    author(cell, "fix", policy=FIX + "print('corrected')\n")
    calls = len(cell.fake.calls)
    for phase in ("repair", "diagnostic"):
        code, error = trial(cell, phase, 52, "fix")
        assert code == 2 and "spent all 3" in error["error"]
    assert "snapshot runs once" in cli(cell, "snapshot")[1]["error"]
    assert "already ran its smoke/initial" in trial(cell, "smoke", 51)[1]["error"]
    assert "initial evidence from the parent" in cli(cell, "alias-smoke", "--seed", "51")[1]["error"]
    assert "already has a completed result" in trial(cell, "initial", 51)[1]["error"]
    assert len(cell.fake.calls) == calls
    assert status(cell)["continuation"]["execution_totals"]["new"]["rows"] == 0


# --- initial coverage --------------------------------------------------------------


def test_owed_initial_coverage_precedes_repair_and_uses_the_parents_initial_program(cell):
    parent = continued(cell, partial_parent)
    result = status(cell)
    assert result["continuation"]["owed_initial_seeds"] == list(range(56, 66))
    assert result["seeds_needing_initial"] == list(range(56, 66))
    author(cell, "fix", policy=FIX)
    assert "run the initial program on seeds [56," in trial(cell, "repair", 51, "fix")[1]["error"]
    # A different program is not the initial program, however small the change.
    author(cell, "initial", policy=POLICY + "changed = True\n")
    assert "parent's own initial program" in trial(cell, "initial", 56)[1]["error"]
    author(cell, "initial", config={**CONFIG, "notes": "new prompt plan"})
    assert "parent's own initial program" in trial(cell, "initial", 56)[1]["error"]
    assert status(cell)["retries_used_per_seed"]["56"] == 0
    # The prompt's recipe: copy the anchor attempt's exact executed files.
    anchor = status(cell)["continuation"]["initial_anchor"]
    executed = ledger_copy(cell).parent / anchor["directory"]
    shutil.copy(executed / "code.py", task_dir(cell) / "initial_code.py")
    shutil.copy(executed / "authored_streaming_config.json",
                task_dir(cell) / "initial_streaming_config.json")
    code, record = trial(cell, "initial", 56)
    assert code == 0 and record["status"] == "complete"
    old = json.loads((parent.task / "development_state.json").read_text())
    old_initial = next(r for r in old["trials"] if r["phase"] == "initial")
    # Same authored program; the bundle's world digest is the corrected runtime's.
    assert record["bundle"]["policy"] == old_initial["bundle"]["policy"]
    assert record["bundle"]["world"] != old_initial["bundle"]["world"]
    assert record["bundle_components"]["engineered_world_digest"] == \
        sp.engineered_world(cell.sim.resolve())["digest"]
    assert trial(cell, "initial", 57)[0] == 0  # the new initial bundle is frozen and reused
    for seed in range(58, 66):
        trial(cell, "initial", seed)
    assert trial(cell, "repair", 51, "fix")[0] == 0


# --- historical successes and selection ----------------------------------------------


@pytest.mark.parametrize("fix_runtime", [True, False])
def test_parent_successes_are_historical_and_never_selectable(cell, fix_runtime):
    continued(cell, fix_runtime=fix_runtime)
    result = status(cell)
    assert result["seeds_passing"] == [] and result["tested_bundles"] == {}
    assert sorted(s["seed"] for s in result["continuation"]["historical_successes"]) == [61, 65]
    for seed in (61, 65):
        row = outcome(result, seed)
        assert row["historical"]["passed"] and not row["passed"]
        assert row["historical_success_requires_revalidation"]
        assert seed in result["seeds_pending_repair"]
    author(cell, "fix", policy=FIX + "print('corrected')\n")
    trial(cell, "repair", 53, "fix")  # one new candidate exists; it failed
    # The parent's passing repair bundle, even byte-identical when the runtime is
    # unchanged, was never executed in this phase and cannot be selected.
    copy_parent_files(cell, "fix_code.py", "fix_streaming_config.json")
    code, error = cli(cell, "select", "--reason", "passed seed 61 earlier")
    assert code == 2 and "never tested" in error["error"]
    check = cli(cell, "check")[1]
    assert not check["ready"] and any("seeds_pending_repair" in e for e in check["errors"])


def finish_continuation(c) -> Path:
    continued(c)
    d = task_dir(c)
    author(c, "fix", policy=FIX + "print('corrected runtime repair')\n")
    for seed in [s for s in range(51, 66) if s != 52]:
        c.fake.outcomes[seed] = PASS
        assert trial(c, "repair", seed, "fix")[1]["status"] == "complete"
    (d / "attempts").mkdir()
    (d / "attempts/seed_52_BLOCKED.md").write_text(
        "## Root Cause: budget\nexhausted earlier\n## Details: parent\n## What Was Tried: two repairs\n")
    (d / "task_analysis.md").write_text("corrected runtime analysis\n")
    (d / "findings.md").write_text(FINDINGS)
    return d


def test_corrected_runtime_candidate_is_selected_and_finalized_with_provenance(cell, monkeypatch):
    d = finish_continuation(cell)
    result = status(cell)
    assert result["seeds_passing"] == [s for s in range(51, 66) if s != 52]
    assert result["blocked_notes_required"] == [] and result["seeds_pending_repair"] == []
    code, selected = cli(cell, "select", "--reason", "passes 14 seeds under the corrected runtime")
    assert code == 0, selected
    new_dirs = {r["directory"] for r in json.loads((d / "development_state.json").read_text())["trials"]}
    assert set(selected["attempt_references"]) <= new_dirs
    check = cli(cell, "check")[1]
    assert check["ready"], check["errors"]
    transcript = d.parent / "coordinator.stdout.jsonl"
    transcript.write_text(transcript_records())
    settings, probe = native_records(d.parent)
    code, stage1 = cli(cell, "finalize", "--transcript", str(transcript),
                       "--worker-settings", str(settings), "--probe", str(probe))
    assert code == 0, stage1
    totals = stage1["continuation"]["execution_totals"]
    assert totals["historical"]["charged_attempts"] == 20 and totals["new"]["charged_attempts"] == 14
    assert totals["new"]["graded_executions"] == stage1["graded_executions"] == 14
    assert stage1["continuation"]["provenance"]["parent_development_state_sha256"] == \
        sp.file_sha256(ledger_copy(cell))
    assert {r["directory"] for r in stage1["final_development_coverage"]} <= new_dirs
    assert list(stage1["tested_bundles"]) == [stage1["selected_bundle_sha256"]]
    assert stage1["retries_used_per_seed"]["61"] == 3 and stage1["retries_used_per_seed"]["52"] == 3
    # Held-out freeze: identity carries the continuation, evidence is new rows only.
    (d.parent / sh.OUTER_MARKER).write_text(cell.case["id"])
    monkeypatch.setattr(sh, "run_replay", cell.fake)
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        sh.main(["--case", str(cell.path)], clock=cell.clock)
    report = json.loads((d.parent / "heldout/heldout_result.json").read_text())
    assert report["identity"]["development"]["continuation"]["schema"] == cont.SCHEMA
    assert set(report["frozen_manifest"]["development_evidence"]) <= new_dirs


# --- refusals -----------------------------------------------------------------------


def _append_row(data, **changes):
    row = dict(next(r for r in data["trials"] if r["phase"] == "repair"))
    row.update(changes)
    data["trials"].append(row)


@pytest.mark.parametrize("edit,match", [
    (lambda d: d["identity"].update(cell="drawer_other_r1"), "differ"),
    (lambda d: d["identity"].update(dev_seeds=list(range(51, 61))), "differ"),
    (lambda d: d["identity"].update(model="other-model"), "differ"),
    (lambda d: d["identity"].update(trial_timeout=1200), "differ"),
    (lambda d: d["identity"].update(retry_limit=4), "differ"),
    (lambda d: d["identity"].update(repeat=2), "differ"),
    (lambda d: d["identity"]["model_service"].update(served_model="other"), "model_service"),
    # A same-named model on other weights/engine/settings: a different launch manifest.
    (lambda d: d["identity"]["model_service"]["manifest"].update(sha256="1" * 64), "model_service"),
    (lambda d: d["identity"]["model_service"]["manifest"]["verified"].update(max_model_len=262144),
     "model_service"),
    (lambda d: d["identity"]["model_service"].update(endpoint="http://127.0.0.1:8127"), "model_service"),
    (lambda d: d["identity"]["initial_states"].update(manifest_sha256="2" * 64), "initial_states"),
    (lambda d: d["identity"]["initial_states"].update(seed_mapping="modulo 50"), "initial_states"),
    (lambda d: d["identity"].update(continuation={"schema": cont.SCHEMA}), "chained"),
    (lambda d: d.update(stage1_complete=True), "finalized"),
    (lambda d: d["trials"][-1].update(status="running"), "still running"),
    (lambda d: _append_row(d, directory="development/repair/seed_52/attempt_4"), "exceed 3"),
    (lambda d: _append_row(d, seed=7, directory="development/repair/seed_7/attempt_1"), "outside"),
    (lambda d: next(r for r in d["trials"] if r["phase"] == "diagnostic").update(spends_retry=False),
     "does not spend its retry"),
    (lambda d: d.update(heldout_state={"seeds": {}}), "held-out"),
])
def test_tampered_or_inadmissible_parent_is_refused_before_any_ledger(cell, edit, match):
    parent = full_parent(cell)
    corrected_runtime(cell)
    continue_from(cell, parent)
    tamper(cell, edit)
    code, error = cli(cell, "init")
    assert code == 2 and match in error["error"], error
    assert not (task_dir(cell) / "development_state.json").exists()


def test_only_manifest_locations_may_move(cell):
    parent = full_parent(cell)
    corrected_runtime(cell)
    continue_from(cell, parent)

    def relocate(d):
        d["identity"]["model_service"]["manifest"]["path"] = "/mnt/old-deployment/model.json"
        d["identity"]["initial_states"]["manifest"] = "/mnt/old-deployment/initial_state_manifest.json"
    tamper(cell, relocate)
    code, ledger = cli(cell, "init")
    assert code == 0, ledger


def test_bad_hash_wrong_cell_location_and_permissions_are_refused(cell):
    parent = full_parent(cell)
    corrected_runtime(cell)
    continue_from(cell, parent)
    good = dict(cell.case["continuation"]["parent"])
    for overrides, match in (
            ({"development_state_sha256": "0" * 64}, "bound sha256"),
            ({"cell": "wine_sam2articulation_r1"}, "OWN parent"),
            ({"development_state": str(task_dir(cell) / "development_state.json")}, "outside"),
            ({"development_state": str(cell.path.parent / "old" / cell.case["id"] / "cell"
                                       / "development_state.json")}, "own control directory")):
        bind(cell, **overrides)
        code, error = cli(cell, "init")
        assert code == 2 and match in error["error"], (overrides, error)
    bind(cell, **good)
    for mode, match in ((0o600, "read-only"), (0o444, "world-readable")):
        ledger_copy(cell).chmod(mode)
        code, error = cli(cell, "init")
        assert code == 2 and match in error["error"], error
    assert not (task_dir(cell) / "development_state.json").exists()
    ledger_copy(cell).chmod(0o400)
    assert cli(cell, "init")[0] == 0


def test_unbound_import_and_silent_adoption_are_refused(cell):
    parent = full_parent(cell)
    corrected_runtime(cell)
    # A parent ledger dropped into the new task directory without the contract.
    task_dir(cell).mkdir(parents=True)
    shutil.copy(parent.task / "development_state.json", task_dir(cell) / "development_state.json")
    assert cli(cell, "status")[0] == 2  # identity differs: no silent adoption
    assert "resume it explicitly" in cli(cell, "init")[1]["error"]
    shutil.rmtree(task_dir(cell))
    (task_dir(cell)).mkdir(parents=True)
    (task_dir(cell) / "initial_code.py").write_text(POLICY)
    continue_from(cell, parent)
    assert "fresh task directory" in cli(cell, "init")[1]["error"]


def test_resume_keeps_identity_and_deadline_and_refuses_reinit_or_drift(cell):
    parent = continued(cell)
    ledger_path = task_dir(cell) / "development_state.json"
    first = json.loads(ledger_path.read_text())
    clock = first["phase_clock"]["development"]
    old_clock = json.loads((parent.task / "development_state.json").read_text())["phase_clock"]
    assert clock["watchdog_seconds"] == 50400 and clock["deadline"] == clock["started_at"] + 50400
    assert first["continuation"]["historical"]["phase_clock"] == old_clock
    cell.clock.now += 3600
    for action in ("status", "check"):
        cli(cell, action)
    again = json.loads(ledger_path.read_text())
    assert again["identity"] == first["identity"] and again["phase_clock"] == first["phase_clock"]
    assert "resume it explicitly" in cli(cell, "init")[1]["error"]
    # Dropping the contract, or changing the bound parent, is a different identity.
    contract = cell.case["continuation"]
    cell.path.write_text(json.dumps({k: v for k, v in cell.case.items() if k != "continuation"}))
    assert "same protocol" in cli(cell, "status")[1]["error"]
    cell.path.write_text(json.dumps(cell.case))
    tamper(cell, lambda d: d.update(usage={"input_tokens": 1}))
    assert "same protocol" in cli(cell, "status")[1]["error"]
    cell.case["continuation"] = contract
    cell.path.write_text(json.dumps(cell.case))
    assert "bound sha256" in cli(cell, "status")[1]["error"]
    # Restore the bound bytes; a ledger whose imported history was edited is refused.
    ledger_copy(cell).chmod(0o600)
    shutil.copy(parent.task / "development_state.json", ledger_copy(cell))
    ledger_copy(cell).chmod(0o400)
    assert cli(cell, "status")[0] == 0
    # Refunding an imported diagnostic attempt inside the new ledger is detected.
    next(r for r in first["continuation"]["historical"]["trials"]
         if r["phase"] == "diagnostic")["spends_retry"] = False
    ledger_path.write_text(json.dumps(first))
    assert "historical evidence differs" in cli(cell, "status")[1]["error"]


def test_normal_case_has_no_continuation_anywhere(cell):
    assert cli(cell, "init")[0] == 0
    case, repo, state = sp.open_state(cell.path, "status", cell.clock)
    assert type(state) is sp.StreamingFixLoopState and "continuation" not in state.data["identity"]
    assert "continuation" not in state.data and state.continuation_summary() is None
    assert "continuation" not in cli(cell, "status")[1] and "continuation" not in cli(cell, "check")[1]
    assert "Repair continuation" not in camp.worker_prompt(prompt_case(case), SIM)


def prompt_case(case: dict) -> dict:
    return {**case, "claude_bin": "/nonexistent/claude"}  # rendered only, never executed


# --- campaign and DLC pair -------------------------------------------------------------


def test_worker_prompt_explains_continuation_inputs_without_strategy(cell):
    parent = full_parent(cell)
    corrected_runtime(cell)
    continue_from(cell, parent)
    text = camp.worker_prompt(prompt_case(cell.case), SIM)
    section = text[text.index("## Repair continuation"):]
    assert str(ledger_copy(cell).parent) in section and str(ledger_copy(cell)) in section
    for needed in ("cumulative", "owed_initial_seeds", "historical_successes", "not refunded",
                   "robot-frame-contract.md", "world_recovery_status()", "`select` accepts only"):
        assert needed in section, needed
    # Paths and rules only: no parent outcome, code or seed result is rendered.
    assert FIX not in text and "seed 61" not in section and "seed 65" not in section
    assert text.count("## Repair continuation") == 1


def test_dlc_pair_fresh_mode_starts_a_new_continuation_then_only_resumes(cell):
    parent = full_parent(cell)
    corrected_runtime(cell)
    continue_from(cell, parent)
    slot = {"cell": cell.case["id"], "mode": "fresh"}
    assert pair.mode_check(slot, cell.case, task_dir(cell), None)["ledger"] is None
    assert cli(cell, "init")[0] == 0
    with pytest.raises(pair.EntryRefused, match="already has a ledger"):
        pair.mode_check(slot, cell.case, task_dir(cell), None)
    summary = pair.mode_check({**slot, "mode": "resume"}, cell.case, task_dir(cell), None)["ledger"]
    assert summary["identity_cell"] == cell.case["id"] and summary["trials"] == 0
    assert summary["phase_clock"]["development"]["watchdog_seconds"] == 50400
