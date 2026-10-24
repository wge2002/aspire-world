"""CPU tests for the opt-in round closeout (trial completion != closeout completion).

Fake native sessions call the real protocol; reviews are written by the real
outer CLI. No simulator, GPU, model, perception service or DLC job.
"""
from __future__ import annotations

import json
import shutil
import types
from pathlib import Path

import pytest

from test_streaming_fixloop import FINDINGS, cell, transcript_records  # noqa: F401
from test_streaming_supervised_repair import (FAIL, FIX, PASS, approve, author, campaign, cli,  # noqa: F401
                                              drive, ledger, repair_round, round_one, sup, task_dir)
import streaming_fixloop_campaign as camp
import streaming_fixloop_closeout as closeout
import streaming_fixloop_guard as guard
import streaming_fixloop_protocol as sp
import streaming_fixloop_rounds as rounds

PLAN = {"schema": rounds.CLOSEOUT_SCHEMA, "report_grace_seconds": 1800,
        "session_timeout_seconds": 1200, "max_sessions": 2}


@pytest.fixture
def closing(sup):
    sup.case["supervised_repair"]["closeout"] = dict(PLAN)
    sup.path.write_text(json.dumps(sup.case))
    return sup


def write_closeout(c, r, *, select=True):
    """What a complete closeout writes: this round's reports, notes, and a tested winner."""
    d = task_dir(c)
    (d / "task_analysis.md").write_text(f"analysis after round {r}\n")
    (d / "findings.md").write_text(FINDINGS + f"\nRound {r}: own evidence.\n")
    status = cli(c, "status")[1]
    (d / "attempts").mkdir(exist_ok=True)
    for seed in status["blocked_notes_required"]:
        (d / f"attempts/seed_{seed}_BLOCKED.md").write_text(
            "## Root Cause\nmissed grasp\n## Details\nown logs\n## What Was Tried\nthree rounds\n")
    successes = status["supervised"]["study_successes"]
    if not select:
        return
    if successes:  # the fixtures' only winner is the tested initial bundle
        for name in ("code.py", "streaming_config.json"):
            shutil.copy(d / f"initial_{name}", d / f"fix_{name}")
        assert cli(c, "select", "--reason", "own tested success")[0] == 0
    else:
        assert cli(c, "select", "--reason", "nothing passed")[0] == 2  # never forced


class Native:
    """Fake native CC: round sessions run the round's trials; closeout sessions call `closeout`."""

    def __init__(self, c, *, reports=True, passes=None, closeout_fn=None):
        self.c, self.reports, self.passes = c, reports, passes or {}
        self.closeout_fn = closeout_fn or (lambda c, r, call: write_closeout(c, r))
        self.calls = []

    def __call__(self, command, *, cwd, env, stdout_path, stderr_path, timeout, **_):
        c, data = self.c, ledger(self.c)
        r = data["supervised"]["current_round"]
        kind = "round" if "--agents" in command else "closeout"
        call = {"kind": kind, "round": r, "timeout": timeout, "prompt": command[command.index("-p") + 1],
                "charged": sum(1 for t in data["trials"] if t.get("spends_retry"))}
        self.calls.append(call)
        code = 0
        if kind == "round":
            owed = cli(c, "status")[1]["supervised"]["round_seeds_remaining"]
            if r == 1:
                if not data["trials"]:
                    assert cli(c, "snapshot")[0] == 0
                    author(c)
                round_one(c, self.passes.get(1), seeds=owed)
            else:
                author(c, "fix", FIX + f"print({r})\n")
                repair_round(c, self.passes.get(r), seeds=owed)
            if self.reports:
                write_closeout(c, r)
        else:
            code = self.closeout_fn(c, r, call) or 0
        Path(stdout_path).write_text(transcript_records())
        c.clock.now += 600
        return code


def reviewer(c):
    def sleep(seconds):
        approve(c, ledger(c)["supervised"]["current_round"])
        c.clock.now += seconds
    return sleep


def charged(c):
    return sum(1 for t in ledger(c)["trials"] if t.get("spends_retry"))


def test_completed_round_refuses_even_an_uncharged_snapshot_before_closeout(closing):
    round_one(closing)
    before = ledger(closing)
    code, result = cli(closing, "snapshot")
    assert code == 2 and "trials are complete" in result["error"]
    after = ledger(closing)
    assert after["trials"] == before["trials"]
    assert after["phase_clock"] == before["phase_clock"]


# --- config, identity, compatibility ----------------------------------------------------


@pytest.mark.parametrize("change,match", [
    (lambda p: p.update(schema="v0"), "schema"),
    (lambda p: p.update(extra=1), "exactly"),
    (lambda p: p.pop("max_sessions"), "exactly"),
    (lambda p: p.update(report_grace_seconds=300), "report_grace_seconds"),
    (lambda p: p.update(report_grace_seconds=True), "report_grace_seconds"),
    (lambda p: p.update(max_sessions=4), "max_sessions"),
    (lambda p: p.update(session_timeout_seconds=1.5e3), "session_timeout_seconds"),
])
def test_closeout_config_is_strict(closing, change, match):
    change(closing.case["supervised_repair"]["closeout"])
    closing.path.write_text(json.dumps(closing.case))
    code, result = cli(closing, "init")
    assert code == 2 and match in result["error"]
    assert not (task_dir(closing) / "development_state.json").exists()


def test_identity_records_closeout_only_when_opted_in_and_resume_keeps_clock(closing, sup):
    assert cli(closing, "init")[0] == 0
    first = ledger(closing)
    block = first["identity"]["supervised_repair"]["closeout"]
    assert {k: block[k] for k in PLAN} == PLAN and "method_difference" in block
    assert cli(closing, "status")[0] == 0 and ledger(closing)["phase_clock"] == first["phase_clock"]
    # Changing the plan after init is an identity change, never a silent resume.
    closing.case["supervised_repair"]["closeout"]["max_sessions"] = 3
    closing.path.write_text(json.dumps(closing.case))
    assert cli(closing, "status")[0] == 2
    # A case without the block keeps its earlier identity shape and prompts.
    legacy_case = {**closing.case, "supervised_repair": {
        k: v for k, v in closing.case["supervised_repair"].items() if k != "closeout"}}
    assert rounds.closeout_errors(legacy_case) == [] and rounds.closeout_config(legacy_case) is None
    from test_streaming_fixloop import SIM
    assert "Round closeout" not in camp.worker_prompt(legacy_case, SIM, {"round": 1, "reviews": [], "summaries": []})
    text = camp.worker_prompt(closing.case, SIM, {"round": 1, "reviews": [], "summaries": []})
    assert "Round closeout" in text and "1800 s" in text


def test_report_grace_lets_a_session_keep_writing_past_five_minutes(closing, monkeypatch):
    """Trials complete at t=0; the session writes until t=420 s and ends by itself."""
    round_one(closing)
    now = {"t": 0.0}
    killed = []
    monkeypatch.setattr(camp.time, "monotonic", lambda: now["t"])
    monkeypatch.setattr(camp.time, "sleep", lambda s: now.__setitem__("t", now["t"] + s))
    monkeypatch.setattr(camp.os, "killpg", lambda pid, sig: killed.append(pid))

    class Inline:
        def __init__(self, target, daemon):
            self.target = target

        def start(self):
            self.target()
    monkeypatch.setattr(camp.threading, "Thread", Inline)
    process = types.SimpleNamespace(pid=4242, poll=lambda: None if now["t"] < 420 else 0)
    info = {}
    camp.boundary_watch(task_dir(closing), 1, 1800, info, report_grace=True)(process)
    assert not killed and info["round_state"] == "trials_complete"
    assert "stopped_at_report_grace" not in info
    # The same writing session under the legacy 300 s boundary grace would be stopped.
    now["t"], legacy = 0.0, {}
    camp.boundary_watch(task_dir(closing), 1, 300, legacy)(process)
    assert killed == [4242] and legacy["stopped_at_round_boundary"]
    # A session that never ends is still stopped, at the report grace limit.
    killed.clear()
    now["t"], forever, endless = 0.0, {}, types.SimpleNamespace(pid=7, poll=lambda: None)
    camp.boundary_watch(task_dir(closing), 1, 1800, forever, report_grace=True)(endless)
    assert killed == [7] and forever["stopped_at_report_grace"] and 1800 <= now["t"] < 1900


# --- campaign loop ----------------------------------------------------------------------


def test_complete_round_reports_need_no_closeout_session_and_check_is_ready(closing, monkeypatch):
    native = Native(closing, passes={1: {60: PASS}})
    control, session, info, record = campaign(closing, monkeypatch, native)
    clock = ledger(closing)["phase_clock"]
    assert drive(closing, control, session, info, record, reviewer(closing)) is None
    assert [c["kind"] for c in native.calls] == ["round"] * 3
    states = record["supervised"]["closeout"]
    assert [states[str(r)]["state"] for r in (1, 2, 3)] == ["complete"] * 3
    assert all(not states[str(r)]["sessions"] for r in (1, 2, 3))
    assert charged(closing) == 45 and ledger(closing)["phase_clock"] == clock
    assert ledger(closing)["supervised"]["state"] == "closed"
    check = cli(closing, "check")[1]
    assert check["ready"], check["errors"]
    saved = json.loads((control / "supervised/round_3/closeout.json").read_text())
    assert saved["state"] == "complete" and saved["verifications"][-1]["complete"]


def test_missing_reports_are_completed_by_restricted_sessions_without_trials(closing, monkeypatch):
    seen = []

    def finish(c, r, call):
        seen.append(call)
        assert charged(c) == call["charged"]
        write_closeout(c, r)
    native = Native(closing, reports=False, passes={1: {60: PASS}}, closeout_fn=finish)
    control, session, info, record = campaign(closing, monkeypatch, native)
    assert drive(closing, control, session, info, record, reviewer(closing)) is None
    assert [c["kind"] for c in native.calls] == ["round", "closeout"] * 3
    assert [c["charged"] for c in seen] == [15, 30, 45] and charged(closing) == 45
    prompt = seen[0]["prompt"]
    assert "runs NO\ntrial" in prompt and "seed 60" in prompt and "select --reason" in prompt
    assert "heldout" not in prompt.lower().replace("held-out path", "")
    assert all(c["timeout"] <= PLAN["session_timeout_seconds"] for c in seen)
    kinds = [s.get("kind") for s in record["sessions"]]
    assert kinds.count("closeout") == 3  # their transcripts reach finalize's provenance
    assert cli(closing, "check")[1]["ready"]


def test_no_success_closes_out_without_selection_and_fails_development(closing, monkeypatch):
    native = Native(closing, reports=False,
                    closeout_fn=lambda c, r, call: write_closeout(c, r, select=True))
    control, session, info, record = campaign(closing, monkeypatch, native)
    assert drive(closing, control, session, info, record, reviewer(closing)) == "development_failed"
    assert ledger(closing)["selected"] is None
    assert record["supervised"]["closeout"]["3"]["state"] == "complete"
    assert not (control / guard.OUTER_MARKER).exists()


def test_refused_trial_and_timeout_exit_bounded_as_incomplete(closing, monkeypatch):
    def misbehave(c, r, call):
        if len([x for x in native.calls if x["kind"] == "closeout"]) == 1:
            code, result = cli(c, "trial", "--phase", "initial", "--seed", "52", "--code",
                               str(task_dir(c) / "initial_code.py"), "--streaming-config",
                               str(task_dir(c) / "initial_streaming_config.json"))
            assert code == 2 and "trials are complete" in result["error"]
            return 0
        return 124  # a timed-out session that wrote nothing
    native = Native(closing, reports=False, closeout_fn=misbehave)
    control, session, info, record = campaign(closing, monkeypatch, native)
    assert drive(closing, control, session, info, record, reviewer(closing)) == "development_incomplete"
    assert [c["kind"] for c in native.calls] == ["round", "closeout", "closeout"]
    store = record["supervised"]["closeout"]["1"]
    assert store["state"] == "incomplete" and len(store["sessions"]) == 2
    assert "after 2 restricted session(s)" in record["blocker"]
    data = ledger(closing)
    assert charged(closing) == 15 and data["supervised"]["state"] == "open"  # round never closed
    assert record["heldout"] == "skipped" and not (control / guard.OUTER_MARKER).exists()
    assert json.loads((control / "supervised/heldout_skipped.json").read_text())["status"] == \
        "development_incomplete"


def test_deadline_during_closeout_is_explicitly_incomplete(closing, monkeypatch):
    def overrun(c, r, call):
        c.clock.now = ledger(c)["phase_clock"]["development"]["deadline"] + 1
    native = Native(closing, reports=False, closeout_fn=overrun)
    control, session, info, record = campaign(closing, monkeypatch, native)
    clock = ledger(closing)["phase_clock"]
    assert drive(closing, control, session, info, record, reviewer(closing)) == "development_incomplete"
    assert "deadline" in record["blocker"] and len(record["supervised"]["closeout"]["1"]["sessions"]) == 1
    assert ledger(closing)["phase_clock"] == clock


def test_editing_a_tested_program_during_closeout_is_a_violation(closing, monkeypatch):
    def tamper(c, r, call):
        row = next(t for t in ledger(c)["trials"] if t.get("spends_retry"))
        target = next(p for p in sorted((task_dir(c) / row["directory"]).rglob("*")) if p.is_file())
        target.write_text(target.read_text() + "\n# edited after the trial\n")
        write_closeout(c, r)
    native = Native(closing, reports=False, closeout_fn=tamper)
    control, session, info, record = campaign(closing, monkeypatch, native)
    assert drive(closing, control, session, info, record, reviewer(closing)) == "development_incomplete"
    assert "integrity violation" in record["blocker"]
    assert record["supervised"]["closeout"]["1"]["state"] == "violated"


def test_verify_flags_unchanged_reports_mismatched_files_and_unearned_selection(closing):
    round_one(closing, {60: PASS})
    d = task_dir(closing)
    write_closeout(closing, 1)
    data, status = ledger(closing), cli(closing, "status")[1]
    start = closeout.boundary(data, d, 1)
    ok = closeout.verify(data, status, d, 1, start, None)
    assert ok["complete"], ok["errors"]
    again = closeout.verify(data, status, d, 1, start, ok["reports"])
    assert any("unchanged since the previous round" in e for e in again["errors"])
    (d / "fix_code.py").write_text(FIX)
    assert any("do not match the selected bundle" in e
               for e in closeout.verify(data, status, d, 1, start, None)["errors"])
    forged = {**status, "supervised": {**status["supervised"], "study_successes": []}}
    assert any("no bundle of this study has an oracle success" in e
               for e in closeout.verify(data, forged, d, 1, start, None)["errors"])
