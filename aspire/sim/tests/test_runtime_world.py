"""p2 CPU engineering acceptance and lifecycle regressions; no real experiments."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import sys
import types
from unittest.mock import patch

import pytest

SIM = Path(__file__).resolve().parents[1]
for p in (SIM.parents[1], SIM / "scripts/libero", SIM / "scripts/common", SIM / "tests"):
    sys.path.insert(0, str(p))

from aspire.sim.cap.world_model.runtime_predictions import RuntimePredictionLedger, parse
from aspire.sim.cap.world_model.prediction_contract import Malformed
from aspire.sim.cap.world_model.executable_world import run_offline, run_executable_world, WorldProgramError
from aspire.sim.cap.world_model.world_use_audit import trial_feedback
import executable_world_profile as profile
import native_world_protocol as protocol
import native_world_campaign as campaign
import runtime_world_fixture_data as fx
from test_prediction_contract import executable_case


class Harness:
    """Evidence bookkeeper exercising the same ledger/state without a simulator."""
    def __init__(self):
        self.ids, self.last_motion, self.last_obs, self.events = {0}, -1, 0, []
        self.ledger = RuntimePredictionLedger(lambda event, **row: self.events.append({"event": event, **row}),
                                              lambda: self.last_obs)
        def valid(ids, fresh=False):
            return bool(ids) and all(i in self.ids for i in ids) and (not fresh or any(i > self.last_motion for i in ids))
        self.state = self.ledger.state(valid, "shadow")
        for entity in ("a", "b", "container"):
            self.state.entity(entity, label=entity, evidence_ids=[0],
                              source={"api": "synthetic_public", "method": "unique association"},
                              uncertainty={"status": "estimated"})

    def call(self, index, name):
        motion = name in {"goto_pose", "goto_home_joint_position"}
        call = {"id": index, "function": name, "args": (), "kwargs": {}}
        self.ledger.begin_call(call, motion=motion)
        if motion:
            self.last_motion = index
        if name == "get_observation":
            self.ids.add(index)
            self.last_obs = index
            self.ledger.begin_observation(index)
        return call

    def predict(self, index=1, **change):
        if self.ledger.current_call != index:
            self.call(index, "goto_pose")
        item = {"entity": "a", "fact": "position", "frame": "world", "unit": "m",
                "kind": "geometry", "expected": [1, 0, 0], "tolerance": 0.01,
                "target": {"kind": "next_observation"}, "valid_for_calls": 10, **change}
        self.ledger.record(index, {"predictions": [item]})

    def write(self, *, entity="a", eid=2, frame="world", unit="m", value=None,
              valid=True, kind="geometry", related_entities=()):
        if entity in self.state.entities and eid > self.last_motion:
            for e in [entity] + list(related_entities):
                self.state.entity(e, label=e, evidence_ids=[eid],
                                  source={"api": "synthetic_public", "method": "unique association"},
                                  uncertainty={"status": "estimated"})
        return self.state.set_fact(entity, "position", [1, 0, 0] if value is None and valid else value,
                 frame=frame, unit=unit, evidence_ids=[eid],
                 source={"api": "synthetic_public", "method": "synthetic measurement"},
                 uncertainty={"status": "estimated" if valid else "unknown"}, valid=valid,
                 kind=kind, related_entities=related_entities)

    def feedback(self, **kwargs):
        return self.ledger.answer("prediction_feedback", kwargs)


@pytest.mark.parametrize("mode,branch,function", [("match", "continue", "goto_home_joint_position"),
                                                ("mismatch", "revise", "goto_pose"),
                                                ("unknown", "reobserve", "get_observation")])
def test_public_perception_template_prediction_policy_branch_chain(tmp_path, mode, branch, function):
    result, ns, session = fx.run(tmp_path, mode)
    audit = result["feedback"]["runtime_mechanism"]
    assert audit["status"] == "action_evidence" and audit["causal_claim"] is False
    chain, = audit["action_chains"]
    assert (chain["branch"], chain["next_function"]) == (branch, function)
    row, = chain["results"]
    assert row["status"] == mode and row["evidence_id"] == 4 and row["prediction_call"] == 3
    assert row["entity"] == "marker" and row["frame"] == "world" and row["unit"] == "m"
    assert row["expected"] == [0.1, 0.0, 0.2]
    if mode == "mismatch":
        assert row["residual"] == pytest.approx(0.04)
        assert result["calls"][-1]["position"] == pytest.approx([0.08, 0, 0.2])
    assert result["manifest"]["prediction_checks"]["contract"] == "p2-runtime"
    assert ns["measured"]["source"]["api"] == "mask_to_world_points"
    assert session.errors == []


def test_unknown_does_not_keep_old_observed_or_predicted_position(tmp_path):
    result, ns, session = fx.run(tmp_path, "unknown")
    assert ns["measured"]["value"] is None and ns["measured"]["status"] == "unknown"
    fact = session.module.state.get_fact("marker", "position", fresh=False)
    assert fact["value"] is None and fact["layer"] == "observed"
    assert result["calls"][-1]["function"] == "get_observation"


def test_next_motor_unsupported_supersedes_old_prediction_before_final_home_observation():
    h = Harness()
    h.predict()
    h.call(2, "goto_home_joint_position")
    h.ledger.record(2, None, unsupported="home dynamics unmodelled")
    h.call(3, "get_observation")
    h.write(eid=3, value=[0, 0, 0])
    assert [r["status"] for r in h.ledger.checks] == ["superseded", "unsupported"]
    assert h.feedback(entity="a")["results"][0]["status"] == "superseded"
    assert h.ledger.counts["mismatch"] == 0


def test_new_same_slot_prediction_supersedes_even_without_motion():
    h = Harness()
    h.predict()
    h.call(2, "solve_ik")
    h.predict(index=2, expected=[2, 0, 0])
    h.call(3, "get_observation")
    h.write(eid=3, value=[2, 0, 0])
    assert [r["status"] for r in h.ledger.checks] == ["superseded", "match"]
    assert h.feedback()["results"][0]["prediction_call"] == 2


@pytest.mark.parametrize("change,reason", [({"frame": "camera"}, "frame mismatch"),
                                          ({"unit": "cm"}, "unit mismatch"),
                                          ({"kind": "fact"}, "fact kind"),
                                          ({"valid": False}, "unreliable perception")])
def test_reference_mismatch_or_unknown_is_not_physical_mismatch(change, reason):
    h = Harness()
    h.predict()
    h.call(2, "get_observation")
    h.write(**change)
    row = h.ledger.checks[-1]
    assert row["status"] == "unknown"
    assert reason in row["reason"]


def test_same_fact_of_other_entity_does_not_resolve_target_identity():
    h = Harness()
    h.predict()
    h.call(2, "get_observation")
    h.write(entity="b", value=[99, 0, 0])
    assert h.ledger.pending and not h.ledger.checks
    assert h.feedback(entity="a")["results"][0]["status"] == "unknown"
    assert h.ledger.counts["mismatch"] == 0


def test_wrong_fact_identity_is_unknown():
    h = Harness()
    h.predict()
    h.call(2, "get_observation")
    # Malformed authored association metadata must not become a numeric mismatch.
    h.ledger.observed(("a", "position"), {"status": "known", "identity": "b", "frame": "world",
                                         "unit": "m", "evidence_ids": [2], "value": [1, 0, 0]})
    assert h.ledger.checks[-1]["status"] == "unknown"
    assert h.ledger.checks[-1]["reason"] == "identity mismatch"


def test_relation_target_identity_is_part_of_comparison():
    h = Harness()
    h.predict(kind="relation", related_entities=["container"], expected=True)
    h.call(2, "get_observation")
    h.write(kind="relation", related_entities=["b"], value=True)
    assert h.ledger.checks[-1]["status"] == "unknown"


def test_stale_or_mixed_evidence_never_resolves_current_window():
    h = Harness()
    h.predict()
    h.call(2, "get_observation")
    h.write(eid=0, value=[99, 0, 0])
    assert h.ledger.pending and not h.ledger.checks
    h.ledger.observed(("a", "position"), {"evidence_ids": [0, 2], "status": "known", "value": [99, 0, 0]})
    assert h.ledger.pending and not h.ledger.checks
    assert h.feedback()["results"][0]["status"] == "unknown"


def test_same_call_real_observation_can_match_but_receipt_cannot():
    h = Harness()
    # Real session commits predict before beginning the measurement.
    call = {"id": 1, "function": "get_observation", "args": (), "kwargs": {}}
    h.ledger.begin_call(call)
    h.predict(target={"kind": "same_call"})
    h.ids.add(1)
    h.last_obs = 1
    h.ledger.begin_observation(1)
    h.write(eid=1)
    assert h.ledger.checks[-1]["status"] == "match" and h.ledger.checks[-1]["evidence_id"] == 1
    call["function"] = "goto_pose"
    with pytest.raises(Malformed, match="actual observation"):
        parse({"predictions": [{"entity": "a", "fact": "x", "frame": "world", "unit": "m",
                                "expected": 1, "target": {"kind": "same_call"}, "valid_for_calls": 2}]}, call)


def test_next_observation_target_waits_for_derived_updates_but_never_uses_later_frames():
    h = Harness()
    h.predict()
    h.call(2, "get_observation")
    h.call(3, "segment_sam3_text_prompt")
    h.call(4, "mask_to_world_points")
    h.write(eid=2)
    assert h.ledger.checks[-1]["status"] == "match"
    h.predict(index=5)
    h.call(6, "get_observation")
    h.call(7, "get_observation")
    h.write(eid=7)
    assert [r["status"] for r in h.ledger.checks] == ["match", "unknown"]
    assert h.ledger.checks[-1]["evidence_id"] == 6


def test_declared_horizon_expires_and_cannot_be_revived():
    h = Harness()
    h.predict(valid_for_calls=1)
    h.call(2, "solve_ik")
    assert h.ledger.pending
    h.call(3, "get_observation")
    h.write(eid=3)
    assert h.ledger.checks[-1]["status"] == "expired"


def test_named_checkpoint_retains_only_when_author_explicitly_models_intermediate_motion():
    h = Harness()
    h.predict(target={"kind": "checkpoint", "name": "after sequence"}, intervening_motion="retain")
    h.call(2, "goto_home_joint_position")
    h.ledger.record(2, None, unsupported="no independent home prediction")
    h.call(3, "get_observation")
    h.write(eid=3)
    assert h.ledger.pending
    h.ledger.checkpoint("unrelated")
    assert h.ledger.pending
    h.ledger.checkpoint("after sequence")
    assert h.ledger.checks[-1]["status"] == "match"


def test_checkpoint_rejects_old_observation_even_with_retained_model():
    h = Harness()
    h.predict(target={"kind": "checkpoint", "name": "goal"}, intervening_motion="retain")
    h.ledger.checkpoint("goal")
    assert h.ledger.checks[-1]["status"] == "unknown"


def test_current_feedback_does_not_recycle_old_mismatch_after_motion_or_horizon():
    h = Harness()
    h.predict(valid_for_calls=3)
    h.call(2, "get_observation")
    h.write(value=[99, 0, 0])
    assert h.feedback()["results"][0]["status"] == "mismatch"
    h.call(3, "goto_home_joint_position")
    assert h.feedback()["results"][0]["status"] == "expired"
    assert h.ledger.checks[-1]["status"] == "mismatch"  # history stays honest


def test_state_template_requires_metadata_and_does_not_make_predictions_observed():
    h = Harness()
    h.write(eid=0)
    h.predict(expected=[99, 0, 0])
    assert h.state.get_fact("a", "position", fresh=False)["value"] == [1, 0, 0]
    assert h.state.get_fact("a", "position")["status"] == "unknown"
    with pytest.raises(TypeError, match="set_fact"):
        h.state.set("position", [1, 0, 0], evidence_ids=[0])
    with pytest.raises(ValueError, match="declare entity"):
        h.write(entity="never observed", eid=0)
    assert h.state is h.ledger.state(lambda *a, **k: False, "shadow")


def test_policy_cannot_modify_query_answer_or_reuse_it_across_public_call():
    h = Harness()
    h.predict()
    h.call(2, "get_observation")
    h.write()
    state = h.ledger.answer("state", {"entity": "a", "fact": "position"})
    feedback = h.feedback()
    changed = copy.deepcopy(feedback)
    changed["results"][0]["status"] = "mismatch"
    with pytest.raises(ValueError, match="exact runtime"):
        h.ledger.decision("revise", feedback=changed, facts=[state], reason="forged")
    h.call(3, "solve_ik")
    with pytest.raises(ValueError, match="stale"):
        h.ledger.decision("continue", feedback=feedback, facts=[state], reason="old answer")


@pytest.mark.parametrize("policy,status", [
    ('import world\nget_observation()\n', "no_query"),
    ('import world\nget_observation()\nprint(world.query("prediction_feedback"))\n', "logging_only"),
    ('import world\nget_observation()\nfeedback = world.query("prediction_feedback")\nif feedback["results"]:\n    goto_home_joint_position()\n', "inconclusive")])
def test_audit_distinguishes_no_query_logging_and_inconclusive(tmp_path, policy, status):
    result, _, _ = fx.run(tmp_path, policy=policy)
    assert result["feedback"]["runtime_mechanism"]["status"] == status
    assert result["manifest"]["runtime_mechanism"]["status"] == status
    assert result["feedback"]["runtime_mechanism"]["action_chains"] == []


def test_all_unsupported_is_not_mechanism_acceptance(tmp_path):
    world = fx.WORLD.replace('if call["function"] == "goto_pose":', 'if False:')
    policy = fx.POLICY[:fx.POLICY.index('status = feedback')]
    result, _, _ = fx.run(tmp_path, policy=policy, world=world)
    audit = result["feedback"]["runtime_mechanism"]
    assert audit["supported_predictions"] == 0
    assert audit["status"] != "action_evidence" and audit["action_chains"] == []


def test_same_call_and_unsupported_supersession_through_actual_session(tmp_path):
    world = fx.WORLD.replace('if call["function"] == "goto_pose":', 'if call["function"] == "get_observation":')
    world = world.replace('[float(v) for v in call["args"][0]]', '[0.0, 0.0, 0.2]')
    world = world.replace('"next_observation"', '"same_call"')
    policy = fx.POLICY[:fx.POLICY.index('perceive()\ninitial')]+ '\nperceive()\n'
    _, _, session = fx.run(tmp_path / "same", world=world, policy=policy)
    checks = [c for c in session.prediction_checks if c["status"] == "match"]
    assert len(checks) == 1 and checks[0]["prediction_call"] == checks[0]["evidence_id"] == 0
    policy = fx.POLICY[:fx.POLICY.index('perceive()\nmeasured')]
    policy += '\ngoto_home_joint_position()\nperceive()\n'
    _, _, session = fx.run(tmp_path / "supersede", policy=policy)
    assert session.predictions.counts["superseded"] == 1 and session.predictions.counts["mismatch"] == 0


def test_template_declaration_required_at_runtime(tmp_path):
    with pytest.raises(WorldProgramError, match="STATE_TEMPLATE_REVISION"):
        fx.run(tmp_path, world=fx.WORLD.replace('STATE_TEMPLATE_REVISION = "p2-runtime"', ''))


def test_profile_actual_solver_section_and_public_doc_are_connected():
    case = executable_case(prediction_contract="p2-runtime")
    profile.validate(case)
    text = campaign.world_section(case)
    assert text == profile.section(case)
    assert text.count("### Runtime world p2") == 1 and "### Prediction contract p1" not in text
    for phrase in ("world.query(\"state\"", "prediction_feedback", "world.record_decision", "Unsupported",
                   "done() observational", "dev seeds 51–65", "three total attempts", "mechanically"):
        assert phrase in text
    assert profile.interface_doc(case) == profile.RUNTIME_INTERFACE
    assert (SIM / profile.RUNTIME_INTERFACE).is_file()
    assert protocol.world_use(case) is not None  # no separate world-use flag needed


def test_full_native_worker_prompt_renders_runtime_contract_without_changing_outer_protocol(tmp_path):
    from test_native_world_fixloop import make_case
    _, case = make_case(tmp_path, "C")
    case.update(executable_case(prediction_contract="p2-runtime"))
    # The exact shared template is read from this engineering checkout; no solver runs.
    text = campaign.worker_prompt(case, SIM)
    assert text.count("### Runtime world p2") == 1
    assert profile.RUNTIME_INTERFACE in text
    assert "3 TOTAL retries per seed" in text and "Do NOT run held-out seeds 1–50" in text
    assert "world.record_decision" in text and "prediction_feedback" in text
    assert "high-level MD" in text or "four strategy files" not in text


def test_protocol_identity_pins_new_revision_and_preserves_solver_model(tmp_path):
    from test_native_world_fixloop import make_case
    _, case = make_case(tmp_path, "C")
    case.update(executable_case(prediction_contract="p2-runtime"))
    repo = Path(case["sim"])
    identity = protocol.identity(case, repo)
    assert identity["prediction_contract"] == "p2-runtime"
    assert identity["model"] == case["model"] and identity["effort"] == case["effort"]
    assert identity["dev_seeds"] == list(range(51, 66)) and identity["retry_limit"] == 3


def test_selected_pair_report_contains_runtime_evidence_separate_from_static_candidates(tmp_path):
    from aspire.sim.cap.world_model.world_use_audit import selected_pair_report
    fx.run(tmp_path / "trial")
    report = selected_pair_report([tmp_path / "trial"])
    assert report["runtime_mechanism"]["status"] == "action_evidence"
    assert report["runtime_mechanism"]["causal_claim"] is False


def test_template_update_invalidates_already_read_state_answer():
    h = Harness()
    h.predict()
    h.call(2, "get_observation")
    h.write()
    state = h.ledger.answer("state", {"entity": "a", "fact": "position"})
    feedback = h.feedback()
    h.write(value=[0.9, 0, 0])
    with pytest.raises(ValueError, match="changed after an update"):
        h.ledger.decision("revise", feedback=feedback, facts=[state], reason="old state answer")


def test_unrelated_feedback_query_does_not_close_another_perception_slot():
    h = Harness()
    h.predict()
    h.call(2, "get_observation")
    assert h.feedback(entity="b")["results"] == [] and h.ledger.pending
    h.write()
    assert h.feedback(entity="a")["results"][0]["status"] == "match"


def test_authored_substrate_predicates_and_observed_only_use_scoped_template_keys():
    h = Harness()
    h.write(eid=0)
    clause = h.state.predicate("authored geometry check", lambda xyz: xyz[0] == 1,
                               [("a", "position")])
    assert clause["verdict"] == "true" and clause["evidence_ids"] == [0]
    assert h.state.observed_only(("a", "position"))["value"] == [1, 0, 0]
    h.call(1, "goto_pose")
    clause = h.state.predicate("authored geometry check", lambda xyz: True, [("a", "position")])
    assert clause["verdict"] == "unknown"


def test_unknown_or_stale_entity_association_cannot_become_known_geometry():
    h = Harness()
    h.call(1, "goto_pose")
    h.call(2, "get_observation")
    fact = h.state.set_fact("a", "position", [1, 0, 0], frame="world", unit="m",
                           evidence_ids=[2], source={"api": "synthetic_public", "method": "measurement"},
                           uncertainty={"status": "estimated"}, kind="geometry")
    assert fact["status"] == "unknown" and "association" in fact["reason"]
    h.state.entity("a", label="a", evidence_ids=[2],
                   source={"api": "synthetic_public", "method": "ambiguous association"},
                   uncertainty={"status": "unknown"})
    fact = h.state.set_fact("a", "position", [1, 0, 0], frame="world", unit="m",
                           evidence_ids=[2], source={"api": "synthetic_public", "method": "measurement"},
                           uncertainty={"status": "estimated"}, kind="geometry")
    assert fact["status"] == "unknown"


def test_entity_label_drift_is_unknown_until_stable_association_reestablished():
    h = Harness()
    entity = h.state.entity("a", label="different object", evidence_ids=[0],
                            source={"api": "synthetic_public", "method": "changed association"},
                            uncertainty={"status": "estimated"})
    assert entity["uncertainty"]["status"] == "unknown"
    assert entity["label"] == "a" and entity["candidate_label"] == "different object"
    entity = h.state.entity("a", label="different object", evidence_ids=[0],
                            source={"api": "synthetic_public", "method": "repeated changed association"},
                            uncertainty={"status": "estimated"})
    assert entity["uncertainty"]["status"] == "unknown"  # no silent target switch on repetition


def test_association_change_invalidates_current_fact_and_feedback_reads():
    h = Harness()
    h.predict()
    h.call(2, "get_observation")
    h.write()
    assert h.feedback()["results"][0]["status"] == "match"
    h.state.entity("a", label="a", evidence_ids=[2],
                   source={"api": "synthetic_public", "method": "ambiguous association"},
                   uncertainty={"status": "unknown"})
    assert h.state.observed_only(("a", "position"))["status"] == "unknown"
    assert h.feedback()["results"][0]["status"] == "expired"


def test_refined_same_frame_measurement_cannot_recycle_an_old_residual():
    h = Harness()
    h.predict()
    h.call(2, "get_observation")
    h.write(value=[0.9, 0, 0])
    assert h.feedback()["results"][0]["status"] == "mismatch"
    h.write(value=[1.0, 0, 0])
    assert h.feedback()["results"][0]["status"] == "expired"
    assert h.ledger.checks[-1]["status"] == "mismatch"


def test_bad_query_filter_is_not_silently_an_empty_feedback_result():
    h = Harness()
    with pytest.raises(TypeError, match="source_call"):
        h.feedback(source_call="1")
    with pytest.raises(ValueError, match="entity"):
        h.feedback(entity=0)


def test_old_feedback_cannot_mix_with_new_state_after_same_observation_refinement():
    h = Harness()
    h.predict()
    h.call(2, "get_observation")
    h.write(value=[1, 0, 0])
    old_feedback = h.feedback()
    h.write(value=[9, 0, 0])
    new_state = h.ledger.answer("state", {"entity": "a", "fact": "position"})
    new_feedback = h.feedback()
    assert new_feedback["results"][0]["status"] == "expired"
    with pytest.raises(ValueError, match="changed after an update"):
        h.ledger.decision("continue", feedback=old_feedback, facts=[new_state], reason="stale match")
    # Fresh current feedback/state remain usable; the policy chooses how to
    # handle the expired interpretation, rather than the harness routing it.
    assert h.ledger.decision("reobserve", feedback=new_feedback, facts=[new_state],
                            reason="measurement interpretation changed") == 0


def test_old_feedback_cannot_mix_with_new_state_after_association_only_update():
    h = Harness()
    h.predict()
    h.call(2, "get_observation")
    h.write()
    old_feedback = h.feedback()
    h.state.entity("a", label="a", evidence_ids=[2],
                   source={"api": "synthetic_public", "method": "ambiguous association"},
                   uncertainty={"status": "unknown"})
    new_state = h.ledger.answer("state", {"entity": "a", "fact": "position"})
    assert new_state["status"] == "unknown"
    with pytest.raises(ValueError, match="changed after an update"):
        h.ledger.decision("continue", feedback=old_feedback, facts=[new_state], reason="old identity")


def test_p2_trial_config_roundtrips_into_replay_session_and_identity(tmp_path):
    case = executable_case(prediction_contract="p2-runtime")
    trial = tmp_path / "trial"
    trial.mkdir()
    code = trial / "code.py"
    world = trial / "world_program.py"
    code.write_text("get_observation()\n")
    world.write_text(fx.WORLD)
    config = protocol.write_world_config(case, trial, {"world": str(world)})
    assert json.loads(config.read_text())["prediction_contract"] == "p2-runtime"
    seen = {}
    module = types.ModuleType("replay_trial")
    module._run_replay = lambda args, _world_capture: seen.update(contract=_world_capture.prediction_contract)
    args = types.SimpleNamespace(world_model_config=str(config), suite=case["suite"], task=case["task"],
                                 replay_code=str(code), interactive=False)
    with patch.dict(sys.modules, {"aspire.sim.scripts.libero.replay_trial": module}):
        run_executable_world(args)
    assert seen["contract"] == "p2-runtime"


def test_same_policy_exact_public_tape_replay_preserves_p2_decision_dataflow(tmp_path):
    live, _, _ = fx.run(tmp_path / "live", "mismatch")
    folder = Path(live["folder"])
    report = run_offline(folder / "code.py", folder / "world_program.py", tmp_path / "replay",
                         arm="full", mode="replay", tape=folder / "judgment_world/public_tape.jsonl",
                         prediction_contract="p2-runtime")
    assert report["status"] == "complete" and report["matched_calls"] == report["tape_calls"] == 8
    assert report["prediction_checks"]["contract"] == "p2-runtime"
    assert report["runtime_mechanism"]["action_chains"][0]["next_function"] == "goto_pose"
    changed = folder / "changed.py"
    changed.write_text(fx.POLICY.replace('+ 0.1', '+ 0.2'))
    report = run_offline(changed, folder / "world_program.py", tmp_path / "divergent",
                         arm="full", mode="replay", tape=folder / "judgment_world/public_tape.jsonl",
                         prediction_contract="p2-runtime")
    assert report["status"] == "unsupported" and report["matched_calls"] == 3
    assert report["runtime_mechanism"]["action_chains"] == []


def test_rehearsal_unsupported_reports_missing_mechanism_honestly(tmp_path):
    folder = tmp_path / "inputs"
    folder.mkdir()
    (folder / "code.py").write_text(fx.POLICY)
    (folder / "world.py").write_text(fx.WORLD)
    report = run_offline(folder / "code.py", folder / "world.py", tmp_path / "rehearsal",
                         mode="rehearsal", prediction_contract="p2-runtime")
    assert report["status"] == "unsupported"
    assert report["runtime_mechanism"]["supported_predictions"] == 0
    assert report["runtime_mechanism"]["status"] != "action_evidence"


def test_freeze_inventory_includes_interface_and_runtime_modules():
    from native_cc_freeze import source_inventory
    inventory = source_inventory(SIM)["files"]
    for name in (profile.RUNTIME_INTERFACE, "cap/world_model/runtime_state.py",
                 "cap/world_model/runtime_predictions.py", "cap/world_model/runtime_mechanism_audit.py"):
        assert inventory[name] == hashlib.sha256((SIM / name).read_bytes()).hexdigest()


def test_invalid_schema_no_partial_commit_and_finalization_is_idempotent():
    h = Harness()
    h.call(1, "goto_pose")
    h.ledger.record(1, {"predictions": [{"entity": "a"}]})
    assert not h.ledger.pending and h.ledger.counts["malformed"] == 1
    h.predict()
    h.ledger.finalize()
    count = len(h.ledger.checks)
    h.ledger.finalize()
    assert len(h.ledger.checks) == count and h.ledger.checks[-1]["status"] == "unresolved"
