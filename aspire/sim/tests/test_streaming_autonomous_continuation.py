"""Remaining rounds: real ledger/approval transitions, fake replay/native model only."""
import copy
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_streaming_supervised_repair import (sup, round_one, ledger, trial, FAIL, PASS,
                                            task_dir, cli, author, semantic_config, POLICY,
                                            Solver, campaign, drive)
from test_streaming_fixloop import cell  # fixture dependency
import streaming_fixloop_autonomous as auto
import streaming_fixloop_campaign as camp
import streaming_fixloop_guard as guard
import streaming_fixloop_protocol as sp
import streaming_fixloop_review as review
import streaming_fixloop_rounds as rounds


@pytest.fixture
def continued(sup, monkeypatch):
    round_one(sup)
    assert cli(sup, "close-round")[0] == 0
    old = task_dir(sup)
    parent = ledger(sup)
    # Include both a spent infrastructure failure and a crashed raw success.
    parent["trials"] = [r for r in parent["trials"] if not (r["seed"] == 51 and r.get("alias_of"))]
    for row in parent["trials"]:
        if row["seed"] == 51 and row.get("spends_retry"):
            row.update(status="infrastructure_error", task_completed=0)
        if row["seed"] == 57 and row.get("spends_retry"):
            row.update(sandbox_rc=1, task_completed=1)
    parent["identity"]["runtime_manifest_sha256"] = "a" * 64
    (old / "development_state.json").write_text(json.dumps(parent))
    state = old.parent / "campaign_state.json"
    state.write_text(json.dumps({"cell": sup.case["id"], "status": "development_failed", "heldout": "skipped"}))
    seeds = sup.tmp / "corrected-seeds"
    seeds.mkdir()
    for name, text in (("policy", POLICY + "print('corrected')\n"),
                       ("streaming_config", json.dumps(semantic_config())),
                       ("guidance", "Own development-only engineering corrections. " * 4)):
        (seeds / name).write_text(text)
    # CPU fixture uses the same validation except OS ownership; separately test
    # permission rejection and deployment preflights use actual experimental UIDs.
    monkeypatch.setattr(auto, "readonly", lambda p: None)
    sup.parent_path, sup.parent_bytes = old / "development_state.json", (old / "development_state.json").read_bytes()
    sup.case["control"] = str(sup.tmp / "continued" / sup.case["id"])
    sup.case["supervised_repair"].update(continuation={
        "schema": auto.SCHEMA, "phase_id": "remaining-rounds-fixture",
        "authorization": "explicit continuation after parent expired; keep15, only30 new",
        "parent": {"cell": sup.case["id"], "study_id": sup.case["supervised_repair"]["study_id"],
                   "completed_round": 1, "ledger": str(sup.parent_path), "ledger_sha256": auto.sha(sup.parent_path),
                   "campaign_state": str(state), "campaign_state_sha256": auto.sha(state),
                   "runtime_manifest_sha256": "a" * 64, "phase_clock": parent["phase_clock"]},
        "seed_input": {k: {"path": str(seeds / k), "sha256": auto.sha(seeds / k)}
                       for k in ("policy", "streaming_config", "guidance")}},
        autonomous_review={"schema": auto.REVIEW_SCHEMA, "operator_dir": str(sup.tmp / "operator"),
                           "timeout_seconds": 600, "max_attempts": 2,
                           **{k: sup.case[k] for k in ("model", "effort", "context_tokens", "max_output_tokens", "claude_version")}})
    sup.path.write_text(json.dumps(sup.case))
    sup.clock.now += 60000
    return sup


def native_answer(c, evidence, **changes):
    a = {"cell": c.case["id"], "study_id": c.case["supervised_repair"]["study_id"],
         "phase_id": auto.config(c.case)["phase_id"], "round": evidence["round"],
         "evidence_sha256": evidence["evidence_sha256"], "decision": "continue", "fatal": False,
         "analysis": "Seeds51-65 show failed approach and uncertain grasp in their own recorded logs. " * 2,
         "next_round_guidance": "Reobserve visible geometry, verify the commanded pose, and keep held-object evidence current before release. " * 2}
    a.update(changes)
    return a


def fake_native(c, calls, changes=None):
    def run(argv, **kw):
        assert argv[:3] == ["/usr/sbin/runuser", "-u", "fixture-user"]
        assert argv[argv.index("--tools")+1] == ""
        assert "--allowedTools" not in argv and "--dangerously-skip-permissions" not in argv
        assert argv[argv.index("--effort")+1] == c.case["effort"]
        assert "--agents" not in argv
        _, _, _, e = review.awaiting(c.path)
        calls.append(e["round"])
        answer = native_answer(c, e, **(changes or {}))
        records = [{"type": "assistant", "message": {"model": c.case["model"], "content": []}},
                   {"type": "result", "is_error": False, "result": json.dumps(answer),
                    "modelUsage": {"q": {"contextWindow": 1000000, "maxOutputTokens": 32000}}}]
        kw["stdout_path"].write_text("\n".join(json.dumps(x) for x in records))
        kw["stderr_path"].write_text("")
        return 0
    return run


def reviewer_setup(c, monkeypatch):
    monkeypatch.setattr(camp, "native_binary", lambda case: {"version_output": "2.1.220"})
    monkeypatch.setattr(camp, "native_env", lambda *a: {})
    home = c.tmp / "review-user-home"
    home.mkdir(exist_ok=True)
    user = SimpleNamespace(pw_name="fixture-user", pw_uid=os.geteuid(), pw_gid=os.getegid(), pw_dir=str(home))
    operator = c.tmp / "operator"
    operator.mkdir(exist_ok=True)
    return user, operator


def test_two_remaining_rounds_run_with_automatic_reviews_and_no_refund(continued, monkeypatch):
    c = continued
    code, record = cli(c, "init")
    assert code == 0, record
    first = ledger(c)
    assert first["phase_clock"] != auto.config(c.case)["parent"]["phase_clock"]
    assert cli(c, "status")[1]["budget"]["charged_attempts_used"] == 15
    _, _, state = sp.open_state(c.path, "status", c.clock)
    assert not state.candidates() and not rounds.study_successes(state.data)
    assert not state.has_graded_evidence(51)
    assert trial(c, "repair", 51, "fix")[0] == 2  # cannot borrow round2 before review
    assert cli(c, "close-round")[0] == 0
    user, operator = reviewer_setup(c, monkeypatch)
    calls = []
    for closed, next_round in ((1, 2), (2, 3)):
        approved = auto.automatic_review(c.path, user, operator, runner=fake_native(c, calls), clock=c.clock)
        # Restart before consumption reuses the root approval, never calls model twice.
        assert auto.automatic_review(c.path, user, operator, runner=lambda *a, **k: pytest.fail("duplicate model"), clock=c.clock)["reused"]
        assert cli(c, "consume-review", "--review", approved["review"])[0] == 0
        assert ledger(c)["supervised"]["current_round"] == next_round
        for seed in range(51, 66):
            assert trial(c, "repair", seed, "fix", PASS if seed == 51 else FAIL)[0] == 0
            assert trial(c, "repair", seed, "fix")[0] == 2
        assert cli(c, "close-round")[0] == 0
    last = ledger(c)
    assert calls == [1, 2]
    assert sum(bool(r.get("spends_retry")) for r in last["trials"]) == 45
    assert sum(bool(r.get("spends_retry")) and not r.get("inherited_parent") for r in last["trials"]) == 30
    assert all(x == 0 for x in cli(c, "status")[1]["retries_remaining_per_seed"].values())
    assert last["supervised"]["state"] == "closed"
    assert last["phase_clock"] == first["phase_clock"]
    assert c.parent_path.read_bytes() == c.parent_bytes
    assert cli(c, "init")[0] == 2
    assert not (Path(c.case["control"]) / guard.OUTER_MARKER).exists()


@pytest.mark.parametrize("change", ["hash", "cell", "model", "seeds", "running", "finalized", "stopped", "budget", "seed"])
def test_invalid_parent_and_seed_inputs_are_refused(continued, change):
    c = continued
    contract = auto.config(c.case)
    p = json.loads(c.parent_path.read_text())
    if change == "hash":
        contract["parent"]["ledger_sha256"] = "0" * 64
    elif change == "seed":
        Path(contract["seed_input"]["policy"]["path"]).write_text("changed")
    elif change == "stopped":
        s = Path(contract["parent"]["campaign_state"])
        d = json.loads(s.read_text());d["status"] = "running";s.write_text(json.dumps(d))
        contract["parent"]["campaign_state_sha256"] = auto.sha(s)
    else:
        if change == "cell": p["identity"]["cell"] = "other_cell"
        if change == "model": p["identity"]["model"] = "other-model"
        if change == "seeds": p["trials"][1]["seed"] = 1
        if change == "running": p["trials"][1]["status"] = "running"
        if change == "finalized": p["stage1_complete"] = True
        if change == "budget": p["trials"].append(copy.deepcopy(p["trials"][1]))
        c.parent_path.write_text(json.dumps(p));contract["parent"]["ledger_sha256"] = auto.sha(c.parent_path)
    c.path.write_text(json.dumps(c.case))
    code, record = cli(c, "init")
    assert code == 2, record
    assert not (task_dir(c) / "development_state.json").exists()


@pytest.mark.parametrize("changes", [{"cell": "other"}, {"round": 2}, {"evidence_sha256": "f"*64},
                                    {"analysis": ""}, {"decision": "freeze"}])
def test_bad_review_is_bounded_and_never_approved(continued, monkeypatch, changes):
    c = continued
    code, record = cli(c, "init")
    assert code == 0, record
    assert cli(c, "close-round")[0] == 0
    user, operator = reviewer_setup(c, monkeypatch)
    calls = []
    with pytest.raises(sp.ProtocolError, match="bounded"):
        auto.automatic_review(c.path, user, operator, runner=fake_native(c, calls, changes), clock=c.clock)
    assert len(calls) == 2 and not rounds.review_path(c.case, 1).exists()
    with pytest.raises(sp.ProtocolError, match="bounded"):
        auto.automatic_review(c.path, user, operator, runner=lambda *a, **k: pytest.fail("retry budget reset"), clock=c.clock)
    assert cli(c, "status")[1]["budget"]["charged_attempts_used"] == 15


def test_prompt_and_guard_preserve_remaining_round_scope(continued):
    c = continued
    text = camp.supervised_section(c.case, {"round": 2, "reviews": [], "summaries": []})
    assert "at most30" in text and "--phase repair" in text and "No new snapshot" in text
    own = Path(auto.config(c.case)["seed_input"]["policy"]["path"])
    assert guard.read_denial(own, c.case) is None
    assert guard.write_denial(own, c.case)
    assert guard.bash_denial("python scripts/libero/streaming_fixloop_autonomous.py", c.case, c.sim)


def test_remaining_campaign_finishes_without_human_and_zero_success_skips_heldout(continued, monkeypatch):
    c = continued
    solver = Solver(c)
    control, session, info, record = campaign(c, monkeypatch, solver)
    user, operator = reviewer_setup(c, monkeypatch)
    reviewed = []

    def sleep(seconds):
        auto.automatic_review(c.path, user, operator, runner=fake_native(c, reviewed), clock=c.clock)
        c.clock.now += seconds

    assert drive(c, control, session, info, record, sleep) == "development_failed"
    assert [s["round"] for s in solver.sessions] == [2, 3]
    assert reviewed == [1, 2]
    assert sum(bool(r.get("spends_retry")) for r in ledger(c)["trials"]) == 45
    assert record["heldout"] == "skipped"
    assert not (control / guard.OUTER_MARKER).exists()
    assert c.parent_path.read_bytes() == c.parent_bytes


def test_review_timeouts_are_bounded_and_keep_charged_attempts(continued, monkeypatch):
    c = continued
    assert cli(c, "init")[0] == 0 and cli(c, "close-round")[0] == 0
    user, operator = reviewer_setup(c, monkeypatch)
    calls = []

    def timeout(argv, **kw):
        calls.append(kw["timeout"])
        return 124

    with pytest.raises(sp.ProtocolError, match="bounded"):
        auto.automatic_review(c.path, user, operator, runner=timeout, clock=c.clock)
    assert calls == [600, 600]
    assert cli(c, "status")[1]["budget"]["charged_attempts_used"] == 15
    assert not rounds.review_path(c.case, 1).exists()


def test_readonly_parent_checks_real_os_permissions(tmp_path):
    if os.geteuid() != 0:
        pytest.skip("deployment inputs must be root-owned")
    path = tmp_path / "parent.json"
    path.write_text("{}")
    path.chmod(0o600)
    auto.readonly(path)
    path.chmod(0o644)
    with pytest.raises(sp.ProtocolError, match="world readable"):
        auto.readonly(path)
    path.chmod(0o660)
    with pytest.raises(sp.ProtocolError, match="read-only"):
        auto.readonly(path)


def test_native_review_refuses_wrong_model_and_tool_use(continued, tmp_path):
    c = continued
    evidence = {"round": 1, "evidence_sha256": "b"*64}
    answer = native_answer(c, evidence)
    expected = {k: answer[k] for k in ("cell", "study_id", "phase_id", "round", "evidence_sha256")}
    records = [{"type": "assistant", "message": {"model": "other", "content": []}},
               {"type": "result", "is_error": False, "result": json.dumps(answer),
                "modelUsage": {"q": {"contextWindow": 1000000}}}]
    path = tmp_path / "native.jsonl"
    for model, content in (("other", []), (c.case["model"], [{"type": "tool_use", "name": "Bash"}])):
        records[0]["message"].update(model=model, content=content)
        path.write_text("\n".join(json.dumps(r) for r in records))
        with pytest.raises(sp.ProtocolError, match="model/context"):
            auto.parse_native_review(path, expected, c.case)


def test_outer_controller_review_failure_stops_children_and_records_terminal(continued, monkeypatch):
    import streaming_dlc_entry as entry
    c = continued
    assert cli(c, "init")[0] == 0 and cli(c, "close-round")[0] == 0
    control = Path(c.case["control"])
    state_path = control / "campaign_state.json"
    state_path.write_text(json.dumps({"cell": c.case["id"], "status": "awaiting_review"}))
    if os.geteuid() != 0:
        pytest.skip("root wrapper fixture")
    os.chown(control, 30701, 30701)
    user = SimpleNamespace(pw_name="fixture-user", pw_uid=30701, pw_gid=30701, pw_dir=str(c.tmp))
    monkeypatch.setattr(auto.pwd, "getpwnam", lambda _: user)
    monkeypatch.setattr(camp, "load", lambda _: (c.case, c.sim, task_dir(c)))
    calls = []

    class Lifecycle:
        def __init__(self, *a, **k): pass
        def start(self, name, argv, **kw):
            assert argv[:3] == ["/usr/sbin/runuser", "-u", "fixture-user"]
            calls.append("start")
            return SimpleNamespace(poll=lambda: None)
        def stop(self):
            calls.append("stop")
            return {"remaining_children": []}

    monkeypatch.setattr(entry, "Lifecycle", Lifecycle)
    monkeypatch.setattr(auto.signal, "signal", lambda *a: None)
    monkeypatch.setattr(auto, "automatic_review", lambda *a, **k: (_ for _ in ()).throw(sp.ProtocolError("bounded failure")))
    assert auto.run_controller(c.path, "fixture-user") == 1
    assert calls == ["start", "stop"]
    assert json.loads(state_path.read_text())["status"] == "development_review_failed"
    assert json.loads((control/"supervised/heldout_skipped.json").read_text())["status"] == "development_review_failed"
    assert sum(bool(r.get("spends_retry")) for r in ledger(c)["trials"]) == 15
    assert c.parent_path.read_bytes() == c.parent_bytes
