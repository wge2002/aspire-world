"""repair_start: a NEW engineering-assisted repair study from the own declared history.

No simulator, GPU, model or DLC: replay and native sessions are fakes that call
the real protocol, and reviews are written by the real root approval path.

The direct tests exercise streaming_fixloop_autonomous alone; the cross-module
tests run the integrated rounds/campaign/protocol code through the real CLI.
"""
import json
from collections import Counter
from pathlib import Path

import pytest

from test_streaming_fixloop import cell  # noqa: F401  fixture dependency
from test_streaming_supervised_repair import (sup, round_one, ledger, trial, FAIL, PASS,  # noqa: F401
                                              task_dir, cli, author, semantic_config, POLICY,
                                              Solver, campaign, drive, write_reports, finalize)
from test_streaming_autonomous_continuation import reviewer_setup
import streaming_fixloop_autonomous as auto
import streaming_fixloop_campaign as camp
import streaming_fixloop_guard as guard
import streaming_fixloop_protocol as sp
import streaming_fixloop_review as review
import streaming_fixloop_rounds as rounds

SEED_POLICY = POLICY + "print('engineering-assisted seed')\n"


def historical_ledger(cid, study="supervised-repair-historical", **changes):
    """The lineage's own earlier 45 attempts, including old raw successes and a selection."""
    rows = []
    for attempt in (1, 2, 3):
        for seed in range(51, 66):
            ok = attempt == 3 and seed in (52, 60)
            rows.append({"seed": seed, "phase": "repair", "status": "complete", "spends_retry": True,
                         "supervised_round": attempt, "sandbox_rc": 0, "task_completed": int(ok),
                         "directory": f"development/repair/seed_{seed}/attempt_{attempt}",
                         "bundle_sha256": "d" * 64 if ok else "e" * 64})
    data = {"identity": {"cell": cid, "suite": "libero_goal_swap",
                         "task": "put_the_wine_bottle_on_the_rack"},
            "supervised": {"study_id": study, "state": "closed"}, "trials": rows,
            "selected": {"bundle_sha256": "d" * 64, "reason": "historical"}}
    data.update(changes)
    return data


def write_manifest(c, *, drop=(), **changes):
    entries = [{"path": str(p.relative_to(c.prior)), "source": f"/old/{p.relative_to(c.prior)}",
                "sha256": sp.file_sha256(p)}
               for p in sorted(c.prior.rglob("*")) if p.is_file() and p.name != "manifest.json"]
    entries = [e for e in entries if e["path"] not in drop]
    manifest = {"schema": rounds.PRIOR_SCHEMA, "study_id": c.case["supervised_repair"]["study_id"],
                "cell": c.case["id"], "suite": c.case["suite"], "task": c.case["task"],
                "parent_cell": "wine_old_r1", "parent_control": "/old/control",
                "historical_attempts": 45, "new_budget_separate": True,
                "seed_partition": "development51-65only", "no_heldout_inputs": True, "files": entries}
    manifest.update(changes)
    path = c.prior / "manifest.json"
    path.write_text(json.dumps(manifest))
    c.case["supervised_repair"]["prior_input"]["manifest_sha256"] = auto.sha(path)
    c.path.write_text(json.dumps(c.case))
    return manifest


@pytest.fixture
def repair(sup):
    c = sup
    c.case.update(suite="libero_goal_swap", task="put_the_wine_bottle_on_the_rack") \
        if c.case["task"] != "put_the_wine_bottle_on_the_rack" else None
    (c.prior / "development_state.json").write_text(json.dumps(historical_ledger("wine_old_r1")))
    seed = c.prior / "repaired-seed"
    seed.mkdir()
    (seed / "seed_code.py").write_text(SEED_POLICY)
    (seed / "seed_streaming_config.json").write_text(json.dumps(semantic_config()))
    (seed / "changes.md").write_text("Engineering-assisted, unvalidated: explicit axis rows.\n")
    c.case["supervised_repair"].update(repair_start=True, autonomous_review={
        "schema": auto.REVIEW_SCHEMA, "operator_dir": str(c.tmp / "operator"),
        "timeout_seconds": 600, "max_attempts": 2,
        **{k: c.case[k] for k in ("model", "effort", "context_tokens", "max_output_tokens",
                                  "claude_version")}})
    c.manifest = write_manifest(c)
    c.prior_bytes = {p: p.read_bytes() for p in c.prior.rglob("*") if p.is_file()}
    return c


def historical_unchanged(c):
    return {p: p.read_bytes() for p in c.prior.rglob("*") if p.is_file()} == c.prior_bytes


def data_of(c):
    return json.loads((c.prior / "manifest.json").read_text())


# --- direct: autonomous module only ------------------------------------------------


def test_exact_contract_and_exclusive_starts(repair):
    c = repair
    assert auto.contract_errors(c.case) == []
    assert auto.repair_start(c.case) and not auto.enabled(c.case)
    assert auto.review_phase_id(c.case) == c.case["supervised_repair"]["study_id"]
    sup = c.case["supervised_repair"]
    cases = {
        "false": lambda s: s.update(repair_start=False),
        "string": lambda s: s.update(repair_start="true"),
        "independent": lambda s: s.update(independent_start=True),
        "continuation": lambda s: s.update(continuation={}),
        "no review": lambda s: s.pop("autonomous_review"),
        "extra key": lambda s: s["autonomous_review"].update(extra=1),
        "schema": lambda s: s["autonomous_review"].update(schema="other"),
        "model": lambda s: s["autonomous_review"].update(model="other-model"),
        "timeout": lambda s: s["autonomous_review"].update(timeout_seconds=30),
        "operator in control": lambda s: s["autonomous_review"].update(
            operator_dir=str(Path(c.case["control"]) / "op")),
        "operator in prior": lambda s: s["autonomous_review"].update(operator_dir=str(c.prior / "op")),
        "operator in reviews": lambda s: s["autonomous_review"].update(
            operator_dir=str(Path(s["review_dir"]) / "op")),
        "heldout operator": lambda s: s["autonomous_review"].update(operator_dir=str(c.tmp / "heldout-op")),
    }
    for name, change in cases.items():
        changed = json.loads(json.dumps(c.case))
        change(changed["supervised_repair"])
        assert auto.contract_errors(changed), name
    assert sup["repair_start"] is True  # the originals were not mutated


def test_other_starts_keep_their_contracts(repair):
    c = repair
    legacy = json.loads(json.dumps(c.case))
    legacy["supervised_repair"].pop("repair_start")
    assert auto.contract_errors(legacy) == [
        "remaining-round continuation and autonomous_review require their exact paired schemas"]
    legacy["supervised_repair"]["independent_start"] = True
    assert auto.contract_errors(legacy) == []
    legacy["supervised_repair"].pop("autonomous_review")
    assert auto.contract_errors(legacy) == []
    assert not auto.repair_start(legacy)


def test_prior_scope_binds_seed_and_own_history(repair):
    c = repair
    data = data_of(c)
    assert auto.repair_start_prior_errors(c.case, data, c.prior, verify_files=True) == []
    for missing in ("repaired-seed/seed_code.py", "repaired-seed/seed_streaming_config.json",
                    "development_state.json"):
        trimmed = {**data, "files": [e for e in data["files"] if e["path"] != missing]}
        assert auto.repair_start_prior_errors(c.case, trimmed, c.prior, verify_files=False)
    assert auto.repair_start_prior_errors(c.case, {**data, "historical_attempts": 15}, c.prior,
                                          verify_files=False)
    old = historical_ledger("wine_old_r1")
    variants = {
        "short": {**old, "trials": old["trials"][:-1]},
        "same study": historical_ledger("wine_old_r1", study=c.case["supervised_repair"]["study_id"]),
        "other cell": historical_ledger("another_repeat"),
        "heldout seed": {**old, "trials": old["trials"] + [{"seed": 7, "spends_retry": False}]},
        "running": {**old, "trials": old["trials"] + [{"seed": 51, "status": "running"}]},
        "other task": {**old, "identity": {**old["identity"], "task": "turn_on_the_stove"}},
    }
    for name, value in variants.items():
        (c.prior / "development_state.json").write_text(json.dumps(value))
        assert auto.repair_start_prior_errors(c.case, data, c.prior, verify_files=True), name
    (c.prior / "development_state.json").write_text("not json")
    assert "unreadable" in auto.repair_start_prior_errors(c.case, data, c.prior, verify_files=True)[0]


def test_provenance_declares_new_study_and_unvalidated_seed(repair):
    c = repair
    p = auto.repair_start_provenance(c.case, data_of(c))
    rs = p["repair_start"]
    assert rs["imported_trials"] == 0 and rs["historical_attempts"] == 45
    assert rs["historical_successes_are_candidates"] is False and rs["engineering_seed_validated"] is False
    assert rs["new_budget"] == {"rounds": 3, "per_seed_per_round": 1, "per_seed": 3, "total": 45}
    assert rs["first_round"] == 1 and "new" in rs["phase_clock"]
    assert rs["engineering_seed"]["policy"]["sha256"] == auto.sha(c.prior / "repaired-seed/seed_code.py")
    assert p["autonomous_review"] == c.case["supervised_repair"]["autonomous_review"]
    assert "independent" not in json.dumps(p) or "not" in p["kind"]
    assert "independent_start" not in p and "continuation" not in p


def _payload_fixture(c, tmp_path, rows):
    tdir = tmp_path / "control-cell"
    for row in rows:
        d = tdir / row["directory"]
        d.mkdir(parents=True)
        (d / "replay.log").write_text(f"seed {row['seed']} log\n")
        (d / "code.py").write_text(SEED_POLICY)
    identity = {"supervised_repair": auto.repair_start_provenance(c.case, data_of(c))}
    data = {"trials": rows, "identity": identity}
    evidence = {"round": 2, "evidence_sha256": "b" * 64, "per_seed": {}, "charged_attempts_used": 16}
    return tdir, data, evidence


def test_review_payload_is_current_round_own_evidence_and_rootbound_refs(repair, tmp_path):
    c = repair
    rows = [{"seed": 51, "spends_retry": True, "supervised_round": 1, "directory": "r1/s51",
             "bundle_sha256": "1" * 64},
            {"seed": 51, "spends_retry": True, "supervised_round": 2, "directory": "r2/s51",
             "bundle_sha256": "2" * 64}]
    tdir, data, evidence = _payload_fixture(c, tmp_path, rows)
    payload = auto.review_payload(c.case, tdir, data, evidence)
    assert "independent_start" not in payload and "corrected_starting_policy" not in payload
    assert [s["directory"] for s in payload["programs_and_logs"]] == [str((tdir / "r2/s51").resolve())]
    refs = payload["repair_start"]
    assert refs["imported_trials"] == 0 and refs["historical_successes_are_candidates"] is False
    assert refs["prior_manifest_sha256"] == c.case["supervised_repair"]["prior_input"]["manifest_sha256"]
    assert refs["engineering_seed"]["policy"]["path"] == str(c.prior / "repaired-seed/seed_code.py")
    assert "print('engineering-assisted seed')" not in json.dumps(refs)  # references, not contents
    assert payload["phase_id"] == c.case["supervised_repair"]["study_id"] and payload["limit"] == 45
    # Imported/inherited rows, escaping paths and a rebound manifest are refused.
    with pytest.raises(sp.ProtocolError, match="inherited"):
        auto.review_payload(c.case, tdir, {**data, "trials": [{**rows[1], "inherited_parent": "x"}]},
                            evidence)
    with pytest.raises(sp.ProtocolError, match="escapes"):
        auto.review_payload(c.case, tdir, {**data, "trials": [{**rows[1], "directory": "../../x"}]},
                            evidence)
    with pytest.raises(sp.ProtocolError, match="not initialized"):
        auto.review_payload(c.case, tdir, {**data, "identity": {"supervised_repair": {}}}, evidence)
    (c.prior / "manifest.json").write_text((c.prior / "manifest.json").read_text() + " ")
    with pytest.raises(sp.ProtocolError, match="manifest changed"):
        auto.review_payload(c.case, tdir, data, evidence)


def test_prompt_section_is_new_study_scope(repair):
    c = repair
    one = auto.repair_start_section(c.case, {"round": 1}, [])
    three = auto.repair_start_section(c.case, {"round": 3}, ["- `r2.json` — review"])
    for text in (one, three):
        assert "NEW repair study" in text and "neither a" in text and "UNVALIDATED" in text
        assert str(c.prior / "repaired-seed/seed_code.py") in text
        assert "never candidates" in text and "historical success never counts" in text
        assert "round_seeds_remaining" in text and "status" in text and "at most45" in text
        assert "do not wait for chat" in text and "held-out is skipped" in text
    assert "scene snapshot" in one and "(none yet: round1)" in one
    assert "no new snapshot" in three and "r2.json" in three
    assert "independent repeat" not in one.replace("fresh independent repeat", "")


# --- cross-module: integrated rounds/campaign/protocol ---------------------------------


@pytest.fixture
def glue(repair):
    """The integrated rounds/campaign/protocol path (no test-local shim)."""
    return repair


def native_runner(c, calls, decision="continue"):
    def run(argv, **kw):
        assert argv[:3] == ["/usr/sbin/runuser", "-u", "fixture-user"]
        assert argv[argv.index("--tools") + 1] == "" and "--allowedTools" not in argv
        prompt = argv[argv.index("-p") + 1]
        assert "NEW engineering-assisted repair study" in prompt
        _, _, data, e = review.awaiting(c.path)
        payload = auto.review_payload(c.case, task_dir(c), data, e)
        assert "independent_start" not in payload and payload["repair_start"]["imported_trials"] == 0
        assert {s["seed"] for s in payload["programs_and_logs"]} <= set(range(51, 66))
        assert all(Path(s["directory"]).is_relative_to(task_dir(c)) for s in payload["programs_and_logs"])
        calls.append(e["round"])
        answer = {k: payload[k] for k in ("cell", "study_id", "phase_id", "round", "evidence_sha256")}
        answer.update(decision=decision, fatal=decision == "stop",
                      analysis="Own current round failures: approach and release evidence in own logs. " * 2,
                      next_round_guidance="Reobserve support plane, verify held relation before release. " * 2)
        events = [{"type": "assistant", "message": {"model": c.case["model"], "content": []}},
                  {"type": "result", "is_error": False, "result": json.dumps(answer),
                   "modelUsage": {"q": {"contextWindow": 1000000}}}]
        kw["stdout_path"].write_text("\n".join(json.dumps(x) for x in events))
        kw["stderr_path"].write_text("")
        return 0
    return run


def charged(c):
    return sum(r.get("spends_retry") is True for r in ledger(c)["trials"])


@pytest.mark.parametrize("change,match", [
    (lambda s: s.pop("autonomous_review"), "autonomous_review"),
    (lambda s: s.update(independent_start=True), "separate from continuation"),
    (lambda s: s.update(repair_start=False), "must be true"),
])
def test_init_refuses_invalid_contract(glue, change, match):
    c = glue
    change(c.case["supervised_repair"])
    c.path.write_text(json.dumps(c.case))
    code, error = cli(c, "init")
    assert code == 2 and match in error["error"], error
    assert not (task_dir(c) / "development_state.json").exists()


@pytest.mark.parametrize("tamper", ["seed", "history", "drop-seed", "independent-manifest"])
def test_init_refuses_unbound_or_wrong_prior(glue, tamper):
    c = glue
    if tamper == "seed":  # edited after binding: read_prior's full-file rehash refuses
        (c.prior / "repaired-seed/seed_code.py").write_text("changed")
    elif tamper == "history":
        (c.prior / "development_state.json").write_text(json.dumps(historical_ledger("x")))
    elif tamper == "drop-seed":
        write_manifest(c, drop=("repaired-seed/seed_code.py",))
    else:
        write_manifest(c, schema=rounds.INDEPENDENT_SCHEMA, historical_attempts=0)
    code, error = cli(c, "init")
    assert code == 2, error
    assert not (task_dir(c) / "development_state.json").exists()


def test_init_is_zero_new_round_one_and_resume_cannot_change_identity(glue):
    c = glue
    code, result = cli(c, "init")
    assert code == 0, result
    first = ledger(c)
    assert first["trials"] == [] and not first.get("supervised_continuation")
    assert first["supervised"]["current_round"] == 1 and first["supervised"]["state"] == "open"
    ident = first["identity"]["supervised_repair"]
    assert ident["repair_start"]["imported_trials"] == 0 and "independent_start" not in ident
    assert ident["prior_input"]["historical_attempts"] == 45
    assert ident["prior_input"]["imported_into_trials"] is False
    assert ident["autonomous_review"] == c.case["supervised_repair"]["autonomous_review"]
    status = cli(c, "status")[1]
    assert status["budget"]["charged_attempts_used"] == 0
    assert status["supervised"]["round_seeds_remaining"] == list(range(51, 66))
    assert not status["supervised"]["study_successes"]  # 4 historical successes are not here
    # Old success cannot qualify: selecting the never-run engineered seed is refused.
    d = task_dir(c)
    (d / "fix_code.py").write_text(SEED_POLICY)
    (d / "fix_streaming_config.json").write_text(json.dumps(semantic_config()))
    code, error = cli(c, "select", "--reason", "historical round3 success")
    assert code == 2 and ("never tested" in error["error"]
                          or "no development execution evidence" in error["error"]), error
    assert cli(c, "init")[0] == 2  # no re-init / refund
    for change in (lambda a: a.update(timeout_seconds=900), lambda a: a.update(max_attempts=3)):
        saved = json.dumps(c.case)
        change(c.case["supervised_repair"]["autonomous_review"])
        c.path.write_text(json.dumps(c.case))
        assert cli(c, "status")[0] == 2  # identity differs: resume refused
        c.case = json.loads(saved)
        c.path.write_text(saved)
    assert ledger(c)["phase_clock"] == first["phase_clock"]
    assert historical_unchanged(c)


def test_three_rounds_two_automatic_reviews_45_and_no_success_skips_heldout(glue, monkeypatch):
    c = glue
    solver = Solver(c)
    control, session, info, record = campaign(c, monkeypatch, solver)
    clock = ledger(c)["phase_clock"]
    user, operator = reviewer_setup(c, monkeypatch)
    reviewed = []

    def sleep(seconds):
        auto.automatic_review(c.path, user, operator, runner=native_runner(c, reviewed), clock=c.clock)
        c.clock.now += seconds

    assert drive(c, control, session, info, record, sleep) == "development_failed"
    assert [s["round"] for s in solver.sessions] == [1, 2, 3] and reviewed == [1, 2]
    final = ledger(c)
    rows = [r for r in final["trials"] if r.get("spends_retry") is True]
    assert len(rows) == 45 and set(Counter(r["seed"] for r in rows).values()) == {3}
    assert all(Counter(r["seed"] for r in rows if r["supervised_round"] == k) == Counter(range(51, 66))
               for k in (1, 2, 3))
    assert not any(r.get("inherited_parent") for r in final["trials"])
    assert final["phase_clock"] == clock and record["heldout"] == "skipped"
    assert not (control / guard.OUTER_MARKER).exists()
    assert historical_unchanged(c)
    approvals = [json.loads(rounds.review_path(c.case, r).read_text()) for r in (1, 2)]
    assert all("independent native local" in a["reviewer"] for a in approvals)


def test_current_same_bundle_success_freezes_normally(glue, monkeypatch):
    c = glue
    assert cli(c, "init")[0] == 0 and cli(c, "snapshot")[0] == 0
    d = task_dir(c)
    (d / "initial_code.py").write_text(SEED_POLICY)  # engineered seed as the starting program
    (d / "initial_streaming_config.json").write_text(json.dumps(semantic_config()))
    round_one(c, {60: PASS, 61: PASS})
    user, operator = reviewer_setup(c, monkeypatch)
    calls = []
    for r in (1, 2):
        assert cli(c, "close-round")[0] == 0
        approved = auto.automatic_review(c.path, user, operator, runner=native_runner(c, calls), clock=c.clock)
        assert cli(c, "consume-review", "--review", approved["review"])[0] == 0
        author(c, "fix", POLICY + f"print('round {r + 1}')\n")
        for seed in range(51, 66):
            assert trial(c, "repair", seed, "fix", FAIL)[0] == 0
    assert cli(c, "close-round")[0] == 0 and calls == [1, 2] and charged(c) == 45
    assert trial(c, "repair", 52, "fix")[0] == 2  # no 46th
    write_reports(c, "initial")
    (d / "attempts").mkdir(exist_ok=True)
    for seed in set(range(51, 66)) - {60, 61}:
        (d / f"attempts/seed_{seed}_BLOCKED.md").write_text(
            "## Root Cause: x\ny\n## Details: d\n## What Was Tried: t\n")
    assert cli(c, "select", "--reason", "engineered seed passed 60 and 61 in this study")[0] == 0
    check = cli(c, "check")[1]
    assert check["ready"], check["errors"]
    code, stage1 = finalize(c)
    assert code == 0, stage1
    assert {s["seed"] for s in stage1["supervised_repair"]["selected_bundle_successes"]} == {60, 61}
    assert historical_unchanged(c)


def test_campaign_prompt_and_guard_scope(glue):
    c = glue
    text = camp.supervised_section(c.case, {"round": 2, "reviews": ["/r/round_1.review.json"],
                                            "summaries": []})
    assert "NEW repair study" in text and "round_1.review.json" in text
    assert "Fresh independent" not in text and "remaining-round continuation —" not in text
    seed = c.prior / "repaired-seed/seed_code.py"
    assert guard.read_denial(seed, c.case) is None
    assert guard.write_denial(seed, c.case) and guard.write_denial(c.prior / "development_state.json", c.case)
