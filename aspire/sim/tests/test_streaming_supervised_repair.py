"""CPU tests for the opt-in supervised repair study (three outer-reviewed rounds).

No simulator, GPU, model, perception service or DLC job: the replay is faked at
`run_replay` exactly as in test_streaming_fixloop, native sessions are fakes
that call the real protocol, and reviews are written by the real outer CLI.
"""
from __future__ import annotations

import io
import json
import os
import shutil
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from test_streaming_fixloop import (FINDINGS, POLICY, cell, cli, native_records,  # noqa: F401
                                    task_dir, transcript_records)
from test_semantic_fixloop import semantic_case, semantic_config
import streaming_dlc_pair as pair
import streaming_fixloop_campaign as camp
import streaming_fixloop_guard as guard
import streaming_fixloop_heldout as sh
import streaming_fixloop_protocol as sp
import streaming_fixloop_review as review_cli
import streaming_fixloop_rounds as rounds

STUDY = "supervised-repair-fixture"
PASS, FAIL, CRASH, TIMEOUT = (0, "", 0, 1), (0, "", 0, 0), (0, "", 1, 0), (None, "TimeoutExpired: 900", 0, 0)
FIX = POLICY + "print('round repair')\n"


@pytest.fixture
def sup(cell, tmp_path, monkeypatch):
    """The `cell` fixture opted into semantic v2 + checked-motion-v1 + supervised repair."""
    monkeypatch.setattr(rounds, "REVIEW_OWNER_UID", os.geteuid())
    semantic_case(cell)
    cid = cell.case["id"]
    prior = tmp_path / "deploy/prior" / cid
    prior.mkdir(parents=True)
    for name, text in (("development_state.json", '{"old": "ledger"}'), ("findings.md", "old\n")):
        (prior / name).write_text(text)
    manifest = {"schema": rounds.PRIOR_SCHEMA, "study_id": STUDY, "cell": cid,
                "suite": cell.case["suite"], "task": cell.case["task"], "parent_cell": cid,
                "parent_control": "/old/control", "historical_attempts": 45,
                "new_budget_separate": True, "seed_partition": "development51-65only",
                "no_heldout_inputs": True,
                "files": [{"path": n, "source": f"/old/{n}", "sha256": sp.file_sha256(prior / n)}
                          for n in ("development_state.json", "findings.md")]}
    (prior / "manifest.json").write_text(json.dumps(manifest))
    reviews = tmp_path / "deploy/reviews"
    (reviews / cid).mkdir(parents=True)
    (tmp_path / "deploy/reviews/other_cell").mkdir()
    for d in (tmp_path / "deploy", reviews, reviews / cid):
        d.chmod(0o750)
    cell.case.update(claude_bin="/recorded/native/claude", supervised_repair={
        "schema": rounds.SCHEMA, "study_id": STUDY, "authorization": "fixture: user-authorized study",
        "prior_input": {"manifest": str(prior / "manifest.json"),
                        "manifest_sha256": sp.file_sha256(prior / "manifest.json")},
        "rounds": 3, "round_seeds": list(range(51, 66)), "attempts_per_seed_per_round": 1,
        "require_current_selected_success": True, "review_dir": str(reviews / cid),
        "review_poll_seconds": 5, "boundary_grace_seconds": 0})
    cell.case["streaming"] = {**cell.case["streaming"],
                              "execution_contract": {"version": "checked-motion-v1"}}
    cell.path.write_text(json.dumps(cell.case))
    cell.prior, cell.reviews, cell.tmp = prior, reviews / cid, tmp_path
    return cell


def author(c, stem="initial", policy=POLICY):
    d = task_dir(c)
    (d / f"{stem}_code.py").write_text(policy)
    (d / f"{stem}_streaming_config.json").write_text(json.dumps(semantic_config()))


def trial(c, phase, seed, stem="initial", outcome=FAIL):
    c.fake.outcomes[seed] = outcome
    d = task_dir(c)
    return cli(c, "trial", "--phase", phase, "--seed", str(seed), "--code", str(d / f"{stem}_code.py"),
               "--streaming-config", str(d / f"{stem}_streaming_config.json"))


def round_one(c, outcomes=None, seeds=range(51, 66)):
    outcomes = outcomes or {}
    if not (task_dir(c) / "development_state.json").exists():
        assert cli(c, "init")[0] == 0 and cli(c, "snapshot")[0] == 0
        author(c)
    for seed in seeds:
        if seed == 51:
            assert trial(c, "smoke", 51, outcome=outcomes.get(51, FAIL))[0] in (0, 1)
            if outcomes.get(51, FAIL) != CRASH:
                assert cli(c, "alias-smoke", "--seed", "51")[0] == 0
        else:
            assert trial(c, "initial", seed, outcome=outcomes.get(seed, FAIL))[0] in (0, 1)


def repair_round(c, outcomes=None, seeds=range(51, 66), stem="fix"):
    outcomes = outcomes or {}
    for seed in seeds:
        code, record = trial(c, "repair", seed, stem, outcomes.get(seed, FAIL))
        assert code in (0, 1), record


def ledger(c) -> dict:
    return json.loads((task_dir(c) / "development_state.json").read_text())


def files(c, tag="r"):
    analysis, guidance = c.tmp / f"analysis-{tag}.md", c.tmp / f"guidance-{tag}.md"
    analysis.write_text("Seeds 52-65 fail at the approach; grasp closes early.\n")
    guidance.write_text("Re-measure the handle before closing; keep the initial prompts.\n")
    return analysis, guidance


def approve(c, round_, decision="continue", tag="r"):
    analysis, guidance = files(c, f"{tag}{round_}")
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        code = review_cli.main(["approve-review", "--case", str(c.path), "--round", str(round_),
                                "--decision", decision, "--analysis-file", str(analysis),
                                "--guidance-file", str(guidance), "--reviewer", "outer Codex"])
    return code, json.loads(buffer.getvalue())


def close_and_continue(c, round_):
    assert cli(c, "close-round")[0] == 0
    assert approve(c, round_)[0] == 0
    code, result = cli(c, "consume-review", "--review", str(rounds.review_path(c.case, round_)))
    assert code == 0, result


# --- contract and identity -------------------------------------------------------------


@pytest.mark.parametrize("change,match", [
    (lambda s: s.pop("review_dir"), "exactly"),
    (lambda s: s.update(rounds=2), "3 rounds"),
    (lambda s: s.update(attempts_per_seed_per_round=2), "one charged attempt"),
    (lambda s: s.update(require_current_selected_success=False), "must be true"),
    (lambda s: s.update(review_dir="reviews/x"), "absolute"),
    (lambda s: s["prior_input"].update(manifest_sha256="0" * 64), "bound sha256"),
])
def test_contract_shape_and_prior_binding_are_strict(sup, change, match):
    change(sup.case["supervised_repair"])
    sup.path.write_text(json.dumps(sup.case))
    code, error = cli(sup, "init")
    assert code == 2 and match in error["error"], error
    assert not (task_dir(sup) / "development_state.json").exists()


def test_continuation_and_missing_contract_are_refused_and_prior_files_rehashed_at_init(sup):
    with pytest.raises(sp.ProtocolError, match="separate studies"):
        sp.validate_case({**sup.case, "continuation": {}})
    streaming = {k: v for k, v in sup.case["streaming"].items() if k != "execution_contract"}
    with pytest.raises(sp.ProtocolError, match="execution_contract"):
        sp.validate_case({**sup.case, "streaming": streaming})
    (sup.prior / "findings.md").write_text("edited after binding\n")
    code, error = cli(sup, "init")
    assert code == 2 and "does not match its recorded sha256" in error["error"]
    (sup.prior / "findings.md").write_text("old\n")
    assert cli(sup, "init")[0] == 0
    identity = ledger(sup)["identity"]["supervised_repair"]
    assert identity["study_id"] == STUDY and identity["prior_input"]["imported_into_trials"] is False
    assert ledger(sup)["trials"] == [] and ledger(sup)["supervised"]["current_round"] == 1


def test_ordinary_case_is_unchanged(cell):
    assert cli(cell, "init")[0] == 0
    _, _, state = sp.open_state(cell.path, "status", cell.clock)
    assert type(state) is sp.StreamingFixLoopState
    assert "supervised" not in ledger(cell) and "supervised_repair" not in ledger(cell)["identity"]
    assert "supervised" not in cli(cell, "check")[1] and state.check_waivers() == set()


# --- budget and barrier ---------------------------------------------------------------


def test_one_charged_attempt_per_seed_per_round_and_the_16th_waits_for_approval(sup):
    round_one(sup, seeds=[51, 52])
    calls = len(sup.fake.calls)
    author(sup, "fix", FIX)
    code, error = trial(sup, "initial", 52)
    assert code == 2 and "already used its one charged attempt of round 1" in error["error"]
    assert "round 1" in trial(sup, "smoke", 51)[1]["error"]
    assert cli(sup, "close-round")[0] == 2  # incomplete round cannot close
    round_one(sup, seeds=range(53, 66))
    assert ledger(sup)["supervised"]["state"] == "open"
    for phase, seed in (("repair", 53), ("diagnostic", 60)):  # the 16th, before closure
        assert "already used" in trial(sup, phase, seed, "fix")[1]["error"]
    code, closed = cli(sup, "close-round")
    assert code == 0 and closed["closed_round"] == 1
    summary = json.loads(Path(closed["summary"]).read_text())
    assert set(summary["per_seed"]) == {str(s) for s in range(51, 66)}
    code, error = trial(sup, "repair", 53, "fix")  # the 16th, after closure, before approval
    assert code == 2 and "awaits outer review" in error["error"]
    assert len(sup.fake.calls) == calls + 13
    status = cli(sup, "status")[1]
    assert status["budget"]["charged_attempts_used"] == 15
    assert all(v == 1 for v in status["retries_used_per_seed"].values())


def test_every_failure_kind_is_charged_without_refund(sup):
    round_one(sup, {53: TIMEOUT, 54: CRASH, 55: "seed_error", 56: "infra_startup"})
    close_and_continue(sup, 1)
    author(sup, "fix", FIX)
    assert trial(sup, "diagnostic", 57, "fix")[0] in (0, 1)
    status = cli(sup, "status")[1]
    used = status["retries_used_per_seed"]
    assert used["53"] == used["54"] == used["55"] == used["56"] == 1 and used["57"] == 2
    statuses = {r["seed"]: r["status"] for r in ledger(sup)["trials"] if r.get("supervised_round") == 1}
    assert statuses[53] == "infrastructure_error" and statuses[55] == "world_program_error"
    assert status["supervised"]["current_round"] == 2
    assert 57 not in status["supervised"]["round_seeds_remaining"]
    assert "already used" in trial(sup, "repair", 57, "fix")[1]["error"]


# --- outer reviews ----------------------------------------------------------------------


def test_reviews_are_bound_root_owned_and_consumed_exactly_once(sup):
    round_one(sup)
    assert cli(sup, "close-round")[0] == 0
    path = rounds.review_path(sup.case, 1)
    consume = lambda: cli(sup, "consume-review", "--review", str(path))  # noqa: E731
    assert "missing" in consume()[1]["error"] or consume()[0] == 2  # nothing written yet
    good = {"schema": rounds.REVIEW_SCHEMA, "study_id": STUDY, "cell": sup.case["id"], "round": 1,
            "decision": "continue", "analysis": "real analysis", "next_round_guidance": "next",
            "evidence_sha256": ledger(sup)["supervised"]["rounds"][0]["evidence_sha256"],
            "reviewer": "outer", "created_at": "now"}
    for bad, match in (({"cell": "wine_other_r1"}, "cell"), ({"round": 2}, "round"),
                       ({"study_id": "old-study"}, "study_id"),
                       ({"evidence_sha256": "1" * 64}, "evidence_sha256"),
                       ({"analysis": "  "}, "analysis"), ({"decision": "approve"}, "decision")):
        path.write_text(json.dumps({**good, **bad}))
        path.chmod(0o640)
        code, error = consume()
        assert code == 2 and match in error["error"], (bad, error)
    path.write_text(json.dumps(good))
    for mode, match in ((0o666, "writable"), (0o620, "writable")):
        path.chmod(mode)
        assert match in consume()[1]["error"]
    path.chmod(0o640)
    sup.reviews.chmod(0o770)
    assert "review directory must not be group" in consume()[1]["error"]
    sup.reviews.chmod(0o750)
    if os.geteuid() == 0:  # ownership by the solver's own user is refused
        os.chown(path, 65534, -1)
        assert "owned by the outer root user" in consume()[1]["error"]
        os.chown(path, 0, -1)
    path.unlink()
    path.symlink_to(sup.tmp / "elsewhere.json")
    (sup.tmp / "elsewhere.json").write_text(json.dumps(good))
    assert "symlink" in consume()[1]["error"]
    path.unlink()
    assert approve(sup, 1)[0] == 0
    assert approve(sup, 1)[1]["error"].endswith("one immutable review")
    assert consume()[0] == 0
    assert "not awaiting review" in consume()[1]["error"]  # never consumed twice
    data = ledger(sup)["supervised"]
    assert data["current_round"] == 2 and data["rounds"][0]["review"]["decision"] == "continue"
    assert cli(sup, "status")[0] == 0 and ledger(sup)["supervised"]["current_round"] == 2


def test_changed_evidence_after_closure_is_refused_by_export_approve_and_consume(sup):
    round_one(sup)
    assert cli(sup, "close-round")[0] == 0
    row = ledger(sup)["trials"][-1]
    target = task_dir(sup) / row["directory"] / "command.json"
    target.write_text(target.read_text() + " ")
    out = sup.tmp / "outer/request.json"
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        assert review_cli.main(["export-review-request", "--case", str(sup.path), "--out", str(out)]) == 2
    assert "evidence changed" in buffer.getvalue()
    assert "evidence changed" in approve(sup, 1)[1]["error"]
    assert not rounds.review_path(sup.case, 1).exists()


def test_export_and_queue_describe_the_pending_review(sup):
    round_one(sup, {60: PASS})
    assert cli(sup, "close-round")[0] == 0
    out = sup.tmp / "outer/request.json"
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        assert review_cli.main(["export-review-request", "--case", str(sup.path), "--out", str(out)]) == 0
        assert review_cli.main(["queue", "--case", str(sup.path)]) == 0
    request = json.loads(out.read_text())
    assert request["round"] == 1 and request["evidence"]["per_seed"]["60"]["oracle_success"] is True
    entry = json.loads(buffer.getvalue().split("\n}\n", 1)[1])["cells"][0]
    assert entry["awaiting_review"] and entry["review_path"] == str(rounds.review_path(sup.case, 1))


# --- gate -------------------------------------------------------------------------------


def write_reports(c, stem):
    d = task_dir(c)
    for name in ("code.py", "streaming_config.json"):
        if stem != "fix":
            shutil.copy(d / f"{stem}_{name}", d / f"fix_{name}")
    (d / "task_analysis.md").write_text("analysis\n")
    (d / "findings.md").write_text(FINDINGS)


def finalize(c):
    transcript = task_dir(c).parent / "coordinator.stdout.jsonl"
    transcript.write_text(transcript_records())
    settings, probe = native_records(task_dir(c).parent)
    return cli(c, "finalize", "--transcript", str(transcript), "--worker-settings", str(settings),
               "--probe", str(probe))


def forge_stage1(c):
    """A forged stage-1 result for the tested initial bundle: the held-out gate must still refuse."""
    d = task_dir(c)
    write_reports(c, "initial")  # the frozen files are the tested initial bundle
    data = ledger(c)
    row = next(r for r in data["trials"] if r["phase"] == "initial")
    data.update(stage1_complete=True, selected={"bundle_sha256": row["bundle_sha256"],
                                                "bundle": row["bundle"], "reason": "forged"})
    (d / "development_state.json").write_text(json.dumps(data))
    (d / "stage1_result.json").write_text(json.dumps({
        "stage1_complete": True, "profile": c.case["profile"], "protocol_id": sp.PROTOCOL_ID,
        "selected_bundle": row["bundle"], "selected_bundle_sha256": row["bundle_sha256"],
        "selection": data["selected"], "tested_bundles": {row["bundle_sha256"]: {}},
        "observation_fallback": False, "supervised_repair": {"heldout_gate": "passed"}}))
    (d.parent / sh.OUTER_MARKER).write_text(c.case["id"])
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        code = sh.main(["--case", str(c.path)], clock=c.clock)
    return code, buffer.getvalue()


def test_45_attempts_without_success_are_refused_on_check_finalize_and_heldout(sup, monkeypatch):
    round_one(sup)
    close_and_continue(sup, 1)
    author(sup, "fix", FIX)
    repair_round(sup)
    close_and_continue(sup, 2)
    repair_round(sup)
    assert "already used" in trial(sup, "repair", 51, "fix")[1]["error"]
    assert cli(sup, "close-round")[0] == 0
    data = ledger(sup)
    assert data["supervised"]["state"] == "closed"
    assert data["supervised"]["closure"]["reason"] == "rounds_exhausted"
    assert sum(r["spends_retry"] for r in data["trials"]) == 45
    calls = len(sup.fake.calls)
    assert "closed" in trial(sup, "repair", 52, "fix")[1]["error"]  # no 46th attempt
    assert len(sup.fake.calls) == calls
    write_reports(sup, "fix")
    (task_dir(sup) / "attempts").mkdir()
    for seed in range(51, 66):
        (task_dir(sup) / f"attempts/seed_{seed}_BLOCKED.md").write_text(
            "## Root Cause: x\ny\n## Details: d\n## What Was Tried: t\n")
    assert cli(sup, "select", "--reason", "best")[0] == 2  # no bundle has its own success
    check = cli(sup, "check")[1]
    assert not check["ready"] and any("development_failed" in e for e in check["errors"])
    code, error = finalize(sup)
    assert code == 2 and "development_failed" in error["error"]
    assert not (task_dir(sup) / "stage1_result.json").exists()
    monkeypatch.setattr(sh, "run_replay", sup.fake)
    calls = len(sup.fake.calls)
    code, out = forge_stage1(sup)
    assert code == 2 and "no oracle success of its own" in out
    assert len(sup.fake.calls) == calls


def test_selected_bundle_without_own_success_is_refused_even_if_another_bundle_passed(sup):
    round_one(sup, {60: PASS})  # the initial bundle has a success
    close_and_continue(sup, 1)
    author(sup, "fix", FIX)
    repair_round(sup)  # the repair bundle fails everywhere
    write_reports(sup, "fix")
    code, error = cli(sup, "select", "--reason", "latest repair")
    assert code == 2 and "require_current_selected_success" in error["error"]
    check = cli(sup, "check")[1]
    assert any("require_current_selected_success" in e for e in check["errors"])
    assert any("not closed" in e for e in check["errors"])


def test_freeze_with_own_success_passes_integrity_and_heldout_runs_once(sup, monkeypatch):
    round_one(sup, {60: PASS, 61: PASS})
    write_reports(sup, "initial")
    assert cli(sup, "select", "--reason", "initial bundle passed 60 and 61")[0] == 0
    assert cli(sup, "close-round")[0] == 0
    assert approve(sup, 1, "freeze")[0] == 0
    assert cli(sup, "consume-review", "--review", str(rounds.review_path(sup.case, 1)))[0] == 0
    check = cli(sup, "check")[1]
    assert check["ready"], check["errors"]
    assert check["waived"] == ["seeds_needing_initial", "seeds_pending_repair"]
    code, stage1 = finalize(sup)
    assert code == 0, stage1
    assert stage1["supervised_repair"]["heldout_gate"] == "passed"
    assert {s["seed"] for s in stage1["supervised_repair"]["selected_bundle_successes"]} == {60, 61}
    assert not {"close-round", "consume-review"} & guard.SOLVER_SUBCOMMANDS
    (task_dir(sup).parent / sh.OUTER_MARKER).write_text(sup.case["id"])
    monkeypatch.setattr(sh, "run_replay", sup.fake)
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        sh.main(["--case", str(sup.path)], clock=sup.clock)
    report = json.loads((task_dir(sup).parent / "heldout/heldout_result.json").read_text())
    assert report["seeds_evaluated"] == 50


# --- campaign loop ----------------------------------------------------------------------


class Solver:
    """Fake native session: runs the real protocol for the current round."""

    def __init__(self, c, limits=None):
        self.c, self.sessions, self.limits = c, [], dict(limits or {})

    def __call__(self, command, *, cwd, env, stdout_path, stderr_path, timeout, **_):
        c = self.c
        data = ledger(c)
        r = data["supervised"]["current_round"]
        self.sessions.append({"round": r, "timeout": timeout,
                              "clock": data["phase_clock"]["development"]})
        owed = cli(c, "status")[1]["supervised"]["round_seeds_remaining"]
        owed = owed[:self.limits.pop(len(self.sessions), len(owed))]
        if r == 1:
            if not data["trials"]:
                assert cli(c, "snapshot")[0] == 0
                author(c)
            round_one(c, seeds=owed)
        else:
            author(c, "fix", FIX + f"print({r})\n")
            repair_round(c, seeds=owed)
        Path(stdout_path).write_text(transcript_records())
        c.clock.now += 600
        return 0


def campaign(sup, monkeypatch, solver):
    monkeypatch.setattr(camp, "run_native_cc", solver)
    monkeypatch.setattr(camp, "verify_runtime", lambda case, repo: "rt")

    def prompts(case, repo, directory, context=None):
        (directory / "worker-prompt.md").write_text(json.dumps(context))
        (directory / "coordinator-prompt.md").write_text("coordinate")
        return directory / "worker-prompt.md", directory / "coordinator-prompt.md"
    monkeypatch.setattr(camp, "write_prompts", prompts)

    def step(case_path, repo, env, control, name, script, *args):
        code, result = cli(sup, *args)
        return {"step": name, "exit_code": code, "result": result, "stderr_tail": ""}
    monkeypatch.setattr(camp, "outer_step", step)
    assert cli(sup, "init")[0] == 0
    control = Path(sup.case["control"]).resolve()
    session = control / "sessions/01"
    session.mkdir(parents=True)
    info = {"session": str(session)}
    record = {"cell": sup.case["id"], "sessions": [info]}
    return control, session, info, record


def drive(sup, control, session, info, record, sleep):
    return camp.supervised_development(sup.case, sup.path, sup.sim, {}, control, task_dir(sup),
                                       record, control / "campaign_state.json", info, session, {},
                                       sup.clock, sleep)


def test_round_three_zero_success_fails_automatically_without_marker(sup, monkeypatch):
    solver = Solver(sup)
    control, session, info, record = campaign(sup, monkeypatch, solver)
    approvals = []

    def sleep(seconds):  # the outer coordinator approves while the runner waits
        r = ledger(sup)["supervised"]["current_round"]
        approvals.append(approve(sup, r)[0])
        sup.clock.now += seconds
    assert drive(sup, control, session, info, record, sleep) == "development_failed"
    assert [s["round"] for s in solver.sessions] == [1, 2, 3] and approvals == [0, 0]
    data = ledger(sup)
    assert sum(r["spends_retry"] for r in data["trials"]) == 45
    assert {s["clock"]["started_at"] for s in solver.sessions} == {data["phase_clock"]["development"]["started_at"]}
    skipped = json.loads((control / "supervised/heldout_skipped.json").read_text())
    assert skipped["status"] == "development_failed" and record["heldout"] == "skipped"
    assert not (control / guard.OUTER_MARKER).exists()
    assert len(record["sessions"]) == 3 and all(s.get("supervised_round") for s in record["sessions"])
    assert "development_failed" in camp.TERMINAL and "development_failed" in pair.TERMINAL


def test_interrupted_round_resumes_only_remaining_seeds_and_stall_is_visible(sup, monkeypatch):
    solver = Solver(sup, limits={2: 4, 4: 0})  # session 2 stops after 4 seeds; session 4 stalls
    control, session, info, record = campaign(sup, monkeypatch, solver)

    def sleep(seconds):
        approve(sup, ledger(sup)["supervised"]["current_round"])
        sup.clock.now += seconds
    clock = ledger(sup)["phase_clock"]
    assert drive(sup, control, session, info, record, sleep) == "stage1_incomplete"
    rows = [r for r in ledger(sup)["trials"] if r.get("supervised_round") == 2]
    assert len(rows) == 15 and len({r["seed"] for r in rows}) == 15  # nobody ran twice
    assert [s["round"] for s in solver.sessions] == [1, 2, 2, 3]
    assert "round 3 session ended without a new attempt" in record["blocker"]
    assert ledger(sup)["phase_clock"] == clock  # never re-initialized or refreshed


def test_awaiting_review_rejects_forgery_and_deadline_is_not_approval(sup, monkeypatch):
    solver = Solver(sup)
    control, session, info, record = campaign(sup, monkeypatch, solver)
    path = rounds.review_path(sup.case, 1)

    def sleep(seconds):
        if not path.exists():  # a group-writable forgery is rejected, then time runs out
            path.write_text(json.dumps({"decision": "continue"}))
            path.chmod(0o666)
        sup.clock.now += 20000
    assert drive(sup, control, session, info, record, sleep) == "development_failed"
    assert record["supervised"]["rejected_reviews"][0]["round"] == 1
    assert ledger(sup)["supervised"]["state"] == "awaiting_review"
    assert len(solver.sessions) == 1


def test_prompts_render_round_rules_and_own_inputs_only(sup):
    from test_streaming_fixloop import SIM
    text = camp.worker_prompt(sup.case, SIM, {"round": 2, "reviews": ["/r/round_1.review.json"],
                                              "summaries": []})
    section = text[text.index("## Supervised repair study"):]
    for needed in ("ROUND 2 of 3", "exactly ONE charged attempt", "/r/round_1.review.json",
                   str(sup.prior), "its OWN oracle success", "checked-motion-v1"):
        assert needed in text, needed
    assert "other_cell" not in text and "EXPERIMENT_RESULTS" not in section
    assert "ONE round per session" in camp.coordinator_prompt(sup.case, Path("/p"))
    assert "Supervised repair" not in camp.worker_prompt(
        {k: v for k, v in sup.case.items() if k != "supervised_repair"}, SIM)


# --- guard and DLC pair -----------------------------------------------------------------


def ask(c, tool, **values):
    return guard.denial({"tool_name": tool, "tool_input": values, "cwd": str(c.sim)},
                        c.case, c.sim.resolve())


def test_guard_allows_own_review_and_prior_reads_only(sup):
    own = rounds.review_path(sup.case, 1)
    assert ask(sup, "Read", file_path=str(own)) is None
    assert ask(sup, "Read", file_path=str(sup.prior / "findings.md")) is None
    assert ask(sup, "Write", file_path=str(own), content="{}")
    assert ask(sup, "Edit", file_path=str(sup.prior / "findings.md"), old_string="a", new_string="b")
    other = sup.tmp / "deploy/reviews/other_cell/round_1.review.json"
    assert ask(sup, "Read", file_path=str(other))
    assert ask(sup, "Bash", command=f"cat {other}")
    for doc in ("docs/EXPERIMENT_RESULTS.md", "docs/EXPERIMENT_RESULTS_DETAILS.md"):
        assert ask(sup, "Read", file_path=str(sup.sim / doc))
    assert ask(sup, "Read", file_path=str(sup.tmp / "deploy/preparation-state.json"))
    assert ask(sup, "Bash", command=".venv-libero/bin/python3 scripts/libero/streaming_fixloop_review.py "
                                    "approve-review --case c")
    for sub in ("close-round", "consume-review --review x"):
        assert ask(sup, "Bash", command=f".venv-libero/bin/python3 {camp.PROTOCOL} {sub}")
    assert ask(sup, "Bash", command=f"echo x > {own}")


def test_pair_gpu_layout_is_disjoint_and_full_resolution_probe_is_synthetic():
    assert pair.layout_problems() == []
    bad = {k: dict(v) for k, v in pair.SLOTS.items()}
    bad["B"]["graspnet"] = "5"
    assert any("overlap the shared Qwen" in p for p in pair.layout_problems(bad))
    bad = {k: dict(v) for k, v in pair.SLOTS.items()}
    bad["B"].update(sam3="0", graspnet="0")
    assert any("share GPUs" in p for p in pair.layout_problems(bad))
    url, payload = pair.grasp_probe_request("http://127.0.0.1:8215", 800, 512)
    import base64
    import numpy as np
    depth = np.load(io.BytesIO(base64.b64decode(payload["depth_base64"])))
    seg = np.load(io.BytesIO(base64.b64decode(payload["segmap_base64"])))
    assert url.endswith("/plan") and depth.shape == seg.shape == (512, 800)
    assert seg.sum() == 200 * 128 and float(depth.max()) == pytest.approx(.6)

    class Host:
        def __init__(self, body):
            self.body, self.calls = body, []

        def http(self, url, payload, timeout):
            self.calls.append(url)
            return 200, self.body
    slot = {"case": {"graspnet_url": "http://127.0.0.1:8115"}}
    ok = pair.full_resolution_probe({"width": 800, "height": 512}, slot, Host({"grasps_base64": ""}))
    assert ok["ok"] and ok["width"] == 800
    oom = pair.full_resolution_probe({"width": 800, "height": 512}, slot,
                                     Host({"error": "CUDA out of memory"}))
    assert not oom["ok"] and "out of memory" in oom["body"]
