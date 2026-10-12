"""The v2 method retains native budgets, input partitions and frozen scoring."""
import copy
import json

import pytest

import test_streaming_fixloop as old
from test_streaming_fixloop import cell, cli, task_dir, POLICY, CONFIG
import streaming_fixloop_protocol as sp
import streaming_fixloop_campaign as campaign
import streaming_fixloop_heldout as heldout
from test_streaming_fixloop_heldout import finalized, hcli, result


def semantic_config():
    config = copy.deepcopy(CONFIG)
    config["parts"] = [{"identity": "assembly", "tracked": False},
                       *[{"identity": item["identity"], "parent": "assembly", "tracked": True}
                         for item in config["objects"]]]
    return config


def semantic_case(c):
    c.case.update(profile=sp.SEMANTIC_PROFILE, method="sam2-semantic-v2",
                  method_differences=["part hypotheses", "identity association", "motion reports", "interaction revisions"])
    c.path.write_text(json.dumps(c.case))


def test_semantic_config_is_explicit_and_legacy_rejects_parts():
    text = json.dumps(semantic_config())
    assert not sp.streaming_config_errors(text, sp.SEMANTIC_PROFILE)
    assert sp.streaming_config_errors(text, sp.PROFILE)
    assert sp.streaming_config_errors(json.dumps(CONFIG), sp.SEMANTIC_PROFILE)


def test_v2_case_requires_declared_method_and_preserves_fixed_model_budget(cell):
    semantic_case(cell)
    with pytest.raises(sp.ProtocolError, match="sam2-semantic-v2"):
        sp.validate_case({**cell.case, "method": "sam2-streaming"})
    for key, value in [("trial_timeout", 1800), ("effort", "high"), ("dev_seeds", [1])]:
        with pytest.raises(sp.ProtocolError):
            sp.validate_case({**cell.case, key: value})


def test_semantic_parts_enter_identity_and_materialized_runtime(cell):
    semantic_case(cell)
    assert cli(cell, "init")[0] == 0
    d = task_dir(cell)
    authored = d / "initial_streaming_config.json"
    authored.write_text(json.dumps(semantic_config()))
    directory = d / "materialization"
    directory.mkdir()
    runtime = json.loads(sp.materialize_config(cell.case, directory, authored).read_text())
    assert runtime["mode"] == sp.SEMANTIC_MODE and runtime["parts"] == semantic_config()["parts"]
    ledger = json.loads((d / "development_state.json").read_text())
    assert ledger["identity"]["profile"] == sp.SEMANTIC_PROFILE
    assert ledger["identity"]["retry_limit"] == 3
    assert ledger["identity"]["dev_seeds"] == list(range(51, 66))


def test_native_v2_prompt_documents_public_helpers_without_solutions(cell):
    semantic_case(cell)
    cell.case["claude_bin"] = "/recorded/native/claude"
    prompt = campaign.worker_prompt(cell.case, old.SIM)
    assert "sam2-semantic-native-v2" in prompt
    assert "goto_pose_report" in prompt and "world_revise_structure" in prompt
    assert "No task solution or scene inventory is supplied" in prompt


def test_new_profile_freezes_one_bundle_for_all_fifty_seeds(cell, monkeypatch):
    semantic_case(cell)
    monkeypatch.setattr(old, "CONFIG", semantic_config())
    d = finalized(cell, monkeypatch)
    assert json.loads((d / "stage1_result.json").read_text())["profile"] == sp.SEMANTIC_PROFILE
    assert hcli(cell)[0] == 0
    report = result(cell)
    assert report["completed"] is True and report["incomplete"] is False
    assert report["profile"] == sp.SEMANTIC_PROFILE
    actual = [c for c in cell.fake.calls if "/heldout/" in str(c["directory"])]
    assert len(actual) == 50
    assert {json.loads((c["directory"] / "streaming_world_config.json").read_text())["mode"] for c in actual} == {sp.SEMANTIC_MODE}
