"""Prediction contract p1: off path byte-identical, every status, exposure, audit, prompt, analysis.

CPU only. The world, API and policy are the synthetic fixtures in
tests/prediction_contract_fixture.py; nothing here is a task result.
"""
from __future__ import annotations

import hashlib
import json
import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest

SIM = Path(__file__).resolve().parents[1]
for extra in (SIM.parents[1], SIM / "scripts/libero", SIM / "scripts/common", SIM / "tests"):
    sys.path.insert(0, str(extra))

import executable_world_profile as profile
import native_world_protocol as protocol
import prediction_contract_fixture as fx
import prediction_study_analysis as analysis
from aspire.sim.cap.world_model import prediction_contract as pc
from aspire.sim.cap.world_model import world_use_audit
from aspire.sim.cap.world_model.executable_world import ExecutableSession, run_executable_world

FRAMEWORK = "cap/world_model/executable_world.py"


def without_framework_lines(value):
    """Normalize checkout paths and line numbers of framework callers.

    The recorded caller of the framework's final judgment is a line in
    executable_world.py, so edits move it and each checkout has a different root.
    Keep the framework-relative path and function identity. Everything else,
    including every policy caller line, is compared exactly.
    """
    if isinstance(value, list):
        return [without_framework_lines(v) for v in value]
    if isinstance(value, dict):
        out = {k: without_framework_lines(v) for k, v in value.items()}
        if isinstance(out.get("file"), str) and out["file"].endswith(FRAMEWORK) and "line" in out:
            out["file"] = FRAMEWORK
            out["line"] = "<FRAMEWORK_LINE>"
        return out
    return value


def comparable(text, name):
    if name.endswith(".jsonl"):
        return [without_framework_lines(json.loads(line)) for line in text.splitlines() if line.strip()]
    return without_framework_lines(json.loads(text))


def golden(name):
    return (fx.GOLDEN / name).read_text()


def rows(output, event=None):
    data = [json.loads(line) for line in (Path(output) / "events.jsonl").read_text().splitlines()]
    return [r for r in data if event is None or r["event"] == event]


# --- off: byte-identical to the code before p1 -------------------------------------


@pytest.mark.parametrize("contract", [None, "off"])
def test_off_session_matches_the_pre_p1_golden(tmp_path, contract):
    output, _, session = fx.run_session(tmp_path, contract)
    files = fx.normalized(output, tmp_path)
    assert set(files) == {"events.jsonl", "public_tape.jsonl", "manifest.json"}
    for name, text in files.items():
        expected = golden(f"off_session_{name}")
        assert comparable(text, name) == comparable(expected, name), name
        if name == "public_tape.jsonl":
            assert text == expected  # no framework caller inside: exact bytes
    assert session.predictions is None and session.prediction_checks == []
    manifest = json.loads((output / "manifest.json").read_text())
    assert "prediction_checks" not in manifest
    assert not rows(output, "prediction_check")


@pytest.mark.parametrize("contract", [None, "off"])
def test_off_rehearsal_matches_the_pre_p1_golden(tmp_path, contract):
    output, report = fx.run_rehearsal(tmp_path, contract)
    files = fx.normalized(output, tmp_path)
    for name, text in files.items():
        assert comparable(text, name) == comparable(golden(f"off_rehearsal_{name}"), name), name
    assert "prediction_checks" not in report


def test_off_audit_and_prompt_match_the_pre_p1_golden(tmp_path):
    feedback = world_use_audit.trial_feedback(fx.trial_dir(tmp_path))
    report = json.loads((tmp_path / world_use_audit.ARTIFACT).read_text())
    assert fx._scrub(feedback, tmp_path) == json.loads(golden("off_audit_feedback.json"))
    assert fx._scrub(report, tmp_path) == json.loads(golden("off_audit_report.json"))
    assert "prediction_use" not in feedback and "prediction_use" not in report
    hashes = json.loads(golden("off_prompt_sha256.json"))
    for key, case in fx.PROMPT_CASES.items():
        for variant in (case, {**case, "prediction_contract": "off"}):
            assert hashlib.sha256(profile.section(variant).encode()).hexdigest() == hashes[key]


def test_off_delegates_reserved_names_to_the_author_query(tmp_path):
    output, namespace, _ = fx.run_session(tmp_path)
    queries = rows(output, "world_query")
    assert [q["name"] for q in queries] == ["prediction_summary", "prediction_checks"]
    # The author's state.query answered: an unmeasured fact, not a framework summary.
    assert queries[0]["result"]["reason"] == "not measured"
    assert namespace["latest"] is None


# --- p1: every status, through the live session ------------------------------------


def test_p1_end_to_end_produces_every_status_and_the_policy_reads_latest_mismatch(tmp_path):
    output, namespace, session = fx.run_session(tmp_path, "p1")
    checks = session.prediction_checks
    statuses = {c["status"] for c in checks}
    assert statuses == {"match", "mismatch", "unknown", "unresolved", "unsupported", "malformed"}
    # The policy read latest_mismatch through world.query and branched on it.
    latest = namespace["latest"]
    assert latest["fact"] == "grasped" and latest["prediction_call"] == 3
    assert latest["predicted"] is True and latest["observed"] is False and latest["evidence_id"] == 4
    assert [c["status"] for c in namespace["grasp_checks"]] == ["mismatch", "match"]
    by = {(c["prediction_call"], c["fact"]): c for c in checks if c["fact"]}
    assert by[(1, "ee_pos")]["status"] == "match"
    assert by[(1, "ee_pos")]["residual"] == pytest.approx(0.005)
    assert by[(1, "ee_pos")]["evidence_id"] == 2 and by[(1, "ee_pos")]["tolerance"] == 0.01
    assert by[(3, "label")]["status"] == "unknown" and by[(3, "label")]["observed"] is None
    # Resolved by world.update with evidence 7, for both close calls.
    assert by[(3, "height")]["status"] == "match" and by[(3, "height")]["evidence_id"] == 7
    assert by[(6, "height")]["residual"] == pytest.approx(0.01)
    assert by[(6, "grasped")]["status"] == "match"
    assert by[(8, "ee_pos")]["status"] == "unresolved" and by[(8, "ee_pos")]["observed"] is None
    unsupported = [c["prediction_call"] for c in checks if c["status"] == "unsupported"]
    assert unsupported == [0, 2, 4, 7]
    assert [c["prediction_call"] for c in checks if c["status"] == "malformed"] == [5]
    # Every check is an event with the contract's keys, in the same order.
    events = rows(output, "prediction_check")
    assert len(events) == len(checks)
    for event, check in zip(events, checks):
        assert set(pc.ROW_KEYS) <= set(event)
        assert {k: event[k] for k in pc.ROW_KEYS} == {k: check[k] for k in pc.ROW_KEYS}
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["prediction_checks"] == {
        "contract": "p1",
        "counts": {"committed": 8, "supported": 4, "unsupported": 4, "malformed": 1,
                   "match": 4, "mismatch": 1, "unknown": 2, "unresolved": 1},
        "facts": ["ee_pos", "grasped", "height", "label"], "policy_queries": 3}


def test_p1_summary_mid_episode_counts_pending_as_unresolved(tmp_path):
    output, namespace, _ = fx.run_session(tmp_path, "p1")
    summary = namespace["summary"]
    assert set(summary) == {"committed", "supported", "unsupported", "malformed", "match",
                            "mismatch", "unknown", "unresolved", "latest_mismatch"}
    # At that point: ee_pos matched, grasped mismatched, label unknown, height pending.
    assert (summary["match"], summary["mismatch"], summary["unknown"], summary["unresolved"]) == (1, 1, 1, 1)
    query_rows = rows(output, "world_query")
    assert [q["name"] for q in query_rows] == ["prediction_summary", "prediction_checks", "prediction_checks"]
    assert query_rows[0]["caller"]["file"] == "policy.py" and query_rows[0]["caller"]["line"] == 8


def test_nan_and_other_malformed_predictions_are_counted_never_fatal(tmp_path):
    policy = "goto_home_joint_position()\nget_observation()\n"
    output, _, session = fx.run_session(tmp_path, "p1", policy=policy)
    malformed = [c for c in session.prediction_checks if c["status"] == "malformed"]
    assert [c["prediction_call"] for c in malformed] == [0] and "ee_pos" in malformed[0]["reason"]
    assert session.errors == []
    for bad in ("text", None, {}, {"facts": [1]}, {"facts": {"x": float("inf")}},
                {"facts": {"x": 1}, "tolerance": {"x": -1}}, {"facts": {"x": 1}, "tolerance": {"x": True}},
                {"facts": {"x": 1}, "tolerance": 3}):
        with pytest.raises(pc.Malformed):
            pc.parse(bad)


def test_compare_rules():
    assert pc.compare(1.0, 1.0 + 5e-7)[0] == "match"
    assert pc.compare(1.0, 1.0 + 2e-6)[0] == "mismatch"
    assert pc.compare([0, 0, 1], [0.0, 0.02, 1.0], 0.01)[:2] == ("mismatch", pytest.approx(0.02))
    assert pc.compare([[1, 2]], [[1, 2.001]], 0.01)[0] == "match"
    assert pc.compare([1, 2], [1, 2, 3])[0] == "mismatch"
    assert pc.compare(True, True) == ("match", None, None)
    assert pc.compare(True, 1)[0] == "mismatch"
    assert pc.compare("bowl", "plate")[0] == "mismatch"
    assert pc.compare(None, None)[0] == "match"


def test_predictions_never_overwrite_observed_facts_and_carry_the_call_id(tmp_path):
    world = fx.WORLD.replace("def snapshot():", "def layer(name, which):\n    return state.query(name, layer=which, fresh=False)\n\n\ndef snapshot():")
    policy = "get_observation()\ngoto_pose([0.1, 0.0, 0.3], [0.0, 1.0, 0.0, 0.0])\n"
    _, _, session = fx.run_session(tmp_path, "p1", policy=policy, world=world)
    observed = session.module.layer("ee_pos", "observed")
    predicted = session.module.layer("ee_pos", "predicted")
    assert observed["value"] == [0.0, 0.0, 0.5] and observed["layer"] == "observed"
    assert predicted["value"] == [0.1, 0.0, 0.3] and predicted["evidence_ids"] == []
    assert "call 1" in predicted["reason"]
    assert session.module.state._layers["predicted"]["ee_pos"]["prediction_call"] == 1


def test_a_prediction_is_not_resolved_by_an_observation_up_to_its_own_call(tmp_path):
    ledger = pc.PredictionLedger(lambda *a, **k: None, lambda: None)
    ledger.record(4, {"facts": {"x": 1.0}})
    ledger.observed("x", {"status": "known", "value": 1.0}, 4)
    assert ledger.pending and not ledger.checks
    ledger.observed("x", {"status": "known", "value": 1.0}, 5)
    assert [c["status"] for c in ledger.checks] == ["match"] and not ledger.pending


def test_reserved_queries_are_intercepted_only_under_p1(tmp_path):
    world = fx.WORLD.replace(
        "def query(name, **kwargs):\n",
        "def query(name, **kwargs):\n    if name.startswith('prediction_'):\n        raise AssertionError('reserved')\n")
    policy = 'import world\nget_observation()\ns = world.query("prediction_summary")\nc = world.query("prediction_checks", since_call=0, fact="ee_pos")\n'
    for sub in ("p1", "off", "bad0", "bad1", "bad2"):
        (tmp_path / sub).mkdir()
    _, namespace, session = fx.run_session(tmp_path / "p1", "p1", policy=policy, world=world)
    assert namespace["s"]["unsupported"] == 1 and namespace["c"] == []
    assert session.predictions.policy_queries == 2
    with pytest.raises(AssertionError, match="reserved"):
        fx.run_session(tmp_path / "off", None, policy=policy, world=world)
    for i, bad in enumerate(('world.query("prediction_summary", fact="x")', 'world.query("prediction_checks", since=1)',
                             'world.query("prediction_checks", since_call="3")')):
        with pytest.raises(TypeError):
            fx.run_session(tmp_path / f"bad{i}", "p1", policy="import world\n" + bad + "\n", world=world)


def test_unknown_contract_is_refused():
    with pytest.raises(ValueError, match="prediction_contract"):
        pc.contract("p2")
    with pytest.raises(ValueError):
        ExecutableSession(Path("/nonexistent"), "0", Path("/nonexistent"), "full", prediction_contract="yes")


def test_p1_rehearsal_reports_checks_against_simulate(tmp_path):
    output, report = fx.run_rehearsal(tmp_path, "p1")
    block = report["prediction_checks"]
    assert block["contract"] == "p1"
    # simulate() returns exactly what the world predicted, so its checks match;
    # that is consistency with the world's own model, not real evidence.
    assert block["counts"]["mismatch"] == 0 and block["counts"]["match"] >= 1
    assert json.loads((output / "manifest.json").read_text())["prediction_checks"]["contract"] == "p1"


# --- config, protocol identity and validation --------------------------------------


def executable_case(**extra):
    case = {"id": "c", "condition": "C", "profile": "judgment", "suite": "libero_goal_swap",
            "task": "put_the_bowl_on_the_plate", "executable_world_revision": "r1",
            "foundation_revision": "r1", "c_arm": "full", "c_lineage": "fresh",
            "development_gate": "oracle"}
    case.update(extra)
    return case


@pytest.mark.parametrize("contract", [None, "off", "p1"])
def test_config_is_written_and_read_back_by_the_replay(tmp_path, contract):
    case = executable_case(**({} if contract is None else {"prediction_contract": contract}))
    trial = tmp_path / "trial"
    trial.mkdir()
    (trial / "code.py").write_text(fx.POLICY)
    world, _ = fx.write_world(tmp_path)
    config_path = protocol.write_world_config(case, trial, {"world": str(world)})
    config = json.loads(config_path.read_text())
    assert config.get("prediction_contract") == ("p1" if contract == "p1" else None)
    if contract != "p1":
        assert "prediction_contract" not in config
    seen = {}

    def fake_replay(args, _world_capture):
        seen["contract"] = _world_capture.prediction_contract
        seen["ledger"] = _world_capture.predictions

    module = types.ModuleType("replay_trial")
    module._run_replay = fake_replay
    args = types.SimpleNamespace(world_model_config=str(config_path), suite=case["suite"], task=case["task"],
                                 replay_code=str(trial / "code.py"), interactive=False)
    with patch.dict(sys.modules, {"aspire.sim.scripts.libero.replay_trial": module}):
        run_executable_world(args)
    assert seen["contract"] == ("p1" if contract == "p1" else None)
    assert (seen["ledger"] is not None) == (contract == "p1")
    manifest = json.loads((trial / "judgment_world/manifest.json").read_text())
    assert ("prediction_checks" in manifest) == (contract == "p1")


def test_profile_validation_and_identity():
    profile.validate(executable_case(prediction_contract="p1"))
    with pytest.raises(ValueError, match="unknown prediction_contract"):
        profile.validate(executable_case(prediction_contract="on"))
    with pytest.raises(ValueError, match="requires the r1 executable world"):
        profile.validate({"condition": "C", "profile": "judgment", "prediction_contract": "p1"})


# --- prompt -----------------------------------------------------------------------


@pytest.mark.parametrize("key", sorted(fx.PROMPT_CASES))
def test_prompt_section_appears_only_under_p1_and_once(key):
    case = fx.PROMPT_CASES[key]
    off = profile.section(case)
    on = profile.section({**case, "prediction_contract": "p1"})
    block = profile.PREDICTION_CONTRACT.lstrip("\n") + "\n"
    assert "Prediction contract p1" not in off
    assert on.count("### Prediction contract p1") == 1
    assert on.replace(block, "", 1) == off
    assert on.index("### Prediction contract p1") < on.index(profile.CLOSED_LOOP_ANCHOR)
    for phrase in ("NEXT REAL OBSERVATION", "prediction_summary", "prediction_checks",
                   "information, not a verdict", "does not route recovery",
                   "judged only by\nheld-out task success"):
        assert phrase in on


def test_interface_copy_is_the_gate_study_doc_plus_the_p1_section():
    base = (SIM / "docs/experiments/code-world-gate-ablation-20261005/NATIVE_WORLD_INTERFACE.md").read_text()
    copy = (SIM / "docs/experiments/code-world-prediction-base-20261008/NATIVE_WORLD_INTERFACE.md").read_text()
    assert copy.startswith(base.rstrip("\n"))
    assert copy.count("### Prediction contract p1") == 1


# --- audit ------------------------------------------------------------------------


AUDIT_POLICIES = {
    "supported": ('import world\nget_observation()\nclose_gripper()\nget_observation()\n'
                  'summary = world.query("prediction_summary")\nif summary["mismatch"]:\n    open_gripper()\n'),
    "logging_only": 'import world\nget_observation()\nprint(world.query("prediction_summary"))\n',
    "no_query": 'import world\nget_observation()\nx = world.query("ee_pos")\ngoto_pose(x["value"], [0.0, 1.0, 0.0, 0.0])\n',
    "inconclusive": fx.POLICY,
}


@pytest.mark.parametrize("kind", sorted(AUDIT_POLICIES))
def test_prediction_use_classification(tmp_path, kind):
    feedback = world_use_audit.trial_feedback(fx.trial_dir(tmp_path, "p1", AUDIT_POLICIES[kind]))
    report = json.loads((tmp_path / world_use_audit.ARTIFACT).read_text())
    use = report["prediction_use"]
    assert feedback["prediction_use"] == use
    expected = {"supported": world_use_audit.SUPPORTED, "logging_only": world_use_audit.LOGGING_ONLY,
                "no_query": world_use_audit.NO_QUERY, "inconclusive": world_use_audit.INCONCLUSIVE}[kind]
    assert use["status"] == expected
    if kind == "supported":
        assert use["corroborated_sites"] == use["sites"] and use["recorded_queries"] == 1
    if kind == "no_query":
        # The ordinary audit still sees the non-reserved query consumed.
        assert report["status"] == world_use_audit.SUPPORTED and use["sites"] == []


def test_prediction_use_absent_when_off(tmp_path):
    # Off: the reserved name reaches the author's query(); the audit adds no block.
    feedback = world_use_audit.trial_feedback(fx.trial_dir(tmp_path, None, AUDIT_POLICIES["logging_only"]))
    assert "prediction_use" not in feedback


# --- analysis ---------------------------------------------------------------------


def write_trial(directory, counts, mismatch, use):
    folder = directory / "judgment_world"
    folder.mkdir(parents=True)
    block = {"contract": "p1", "counts": counts, "facts": ["x"], "policy_queries": 1}
    (folder / "manifest.json").write_text(json.dumps({"prediction_checks": block}))
    events = [{"event": "prediction_check", "status": "match", "fact": "x", "residual": 0.001}]
    if mismatch:
        events.append({"event": "prediction_check", "status": "mismatch", "fact": "x", "residual": 0.1})
    (folder / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
    (directory / "world_use_audit.json").write_text(json.dumps({"prediction_use": {"status": use}}))


def write_cell(root, cell, task, arm, dev, heldout_success):
    """dev: list of (task_completed, mismatch, use) per graded trial."""
    control, sim = root / cell, root / f"{cell}-sim"
    task_dir = sim / "outputs/libero_fix_loop/libero_goal_swap" / task
    task_dir.mkdir(parents=True)
    control.mkdir(parents=True)
    case = {"id": cell, "task": task, "suite": "libero_goal_swap", "sim": str(sim),
            "control": str(control), "development_gate": "oracle",
            **({"prediction_contract": "p1"} if arm == "p1" else {})}
    (control / "case.json").write_text(json.dumps(case))
    (control / "campaign_state.json").write_text(json.dumps({"status": "full_complete",
                                                              "development_seconds": 7200}))
    trials = []
    for i, (success, mismatch, use) in enumerate(dev, start=1):
        directory = f"development/initial/seed_{50 + i}/attempt_1"
        trials.append({"phase": "initial", "seed": 50 + i, "status": "complete", "spends_retry": True,
                       "task_completed": int(success), "directory": directory})
        if arm == "p1":
            counts = {"committed": 3, "supported": 2, "unsupported": 1, "malformed": 0,
                      "match": 1, "mismatch": int(mismatch), "unknown": 1, "unresolved": 1 - int(mismatch)}
            write_trial(task_dir / directory, counts, mismatch, use)
    (task_dir / "development_state.json").write_text(json.dumps({"trials": trials}))
    evaluation = control / "heldout"
    evaluation.mkdir()
    per_seed = {str(s): "success" if s <= heldout_success else "failure" for s in range(1, 51)}
    (evaluation / "heldout_result.json").write_text(json.dumps({
        "per_seed": per_seed, "success_rate": heldout_success / 50, "all_seeds_accounted": True,
        "counts": {"success": heldout_success, "failure": 50 - heldout_success}}))
    return control / "case.json"


def test_analysis_on_synthetic_ledgers(tmp_path, capsys):
    supported = world_use_audit.SUPPORTED
    p1 = write_cell(tmp_path, "bowl_p1_r1", "bowl", "p1",
                    [(True, False, supported), (False, True, supported),
                     (False, True, world_use_audit.LOGGING_ONLY), (True, True, supported)], 30)
    off = write_cell(tmp_path, "bowl_off_r1", "bowl", "off", [(True, False, None), (False, False, None)], 20)
    out = tmp_path / "out"
    assert analysis.main(["--parent", str(tmp_path), "--out", str(out)]) == 0
    summary = json.loads((out / "summary.json").read_text())
    cells = {c["cell"]: c for c in summary["cells"]}
    dev = cells["bowl_p1_r1"]["development"]
    assert dev["counts"]["match"] == 4 and dev["counts"]["mismatch"] == 3
    assert dev["mismatch_rate"] == pytest.approx(3 / 7)
    assert dev["prediction_use_rate"] == pytest.approx(3 / 4)
    assert {k: dev["mismatch_x_outcome"][k] for k in ("mismatch_success", "mismatch_failure",
                                                      "no_mismatch_success", "no_mismatch_failure")} \
        == {"mismatch_success": 1, "mismatch_failure": 2, "no_mismatch_success": 1, "no_mismatch_failure": 0}
    assert dev["residuals_by_fact"]["x"]["n"] == 7
    off_dev = cells["bowl_off_r1"]["development"]
    assert off_dev["counts"] is None and off_dev["mismatch_rate"] is None and off_dev["trials_with_predictions"] == 0
    agg = summary["aggregate"]
    assert agg["by_arm"]["p1"]["heldout_success_mean"] == pytest.approx(0.6)
    assert agg["by_arm"]["off"]["heldout_success_mean"] == pytest.approx(0.4)
    assert agg["by_arm"]["p1"]["dev_mismatch_x_outcome"]["n"] == 4
    assert set(agg["by_task_arm"]) == {"bowl|off", "bowl|p1"}
    text = (out / "summary.md").read_text()
    assert "## By arm" in text and "| p1 | 1 | 0.60 |" in text and "| off | 1 | 0.40 |" in text
    assert "bowl_p1_r1" in capsys.readouterr().out
    # --case selects one cell, and is the same reader.
    assert analysis.main(["--case", str(off)]) == 0 and analysis.main(["--case", str(p1)]) == 0
