"""checked-motion-v1 at the protocol boundary: engineering opt-in only.

The case file (engineering) opts in; the authored streaming config cannot set,
disable or alter it. Without the opt-in, identity, materialized runtime and
worker prompt are exactly the legacy ones. Synthetic CPU fixtures only.
"""
import json

import pytest

from test_streaming_fixloop import SIM, cell, cli, task_dir, author, trial, started  # noqa: F401
from test_semantic_fixloop import semantic_case, semantic_config
import streaming_fixloop_protocol as sp
import streaming_fixloop_campaign as campaign

CONTRACT = {"version": "checked-motion-v1"}


def opt_in(c, value=CONTRACT):
    semantic_case(c)
    c.case.update(condition="C", development_gate="oracle", claude_bin="/recorded/native/claude")
    c.case["streaming"] = {**c.case["streaming"], "execution_contract": value}
    c.path.write_text(json.dumps(c.case))


def materialized(c, tmp_path, config):
    authored = tmp_path / "authored.json"
    authored.write_text(json.dumps(config))
    directory = tmp_path / "materialization"
    directory.mkdir(exist_ok=True)
    return json.loads(sp.materialize_config(c.case, directory, authored).read_text())


def test_legacy_case_identity_runtime_and_prompt_are_unchanged(cell, tmp_path):
    semantic_case(cell)
    cell.case["claude_bin"] = "/recorded/native/claude"
    assert cli(cell, "init")[0] == 0
    ledger = json.loads((task_dir(cell) / "development_state.json").read_text())
    assert "execution_contract" not in ledger["identity"]
    assert "execution_contract" not in materialized(cell, tmp_path, semantic_config())
    assert "checked-motion-v1" not in campaign.worker_prompt(cell.case, SIM)


def test_opt_in_enters_identity_runtime_and_prompt(cell, tmp_path):
    opt_in(cell)
    assert cli(cell, "init")[0] == 0
    ledger = json.loads((task_dir(cell) / "development_state.json").read_text())
    assert ledger["identity"]["execution_contract"] == CONTRACT
    assert materialized(cell, tmp_path, semantic_config())["execution_contract"] == CONTRACT
    prompt = campaign.worker_prompt(cell.case, SIM)
    assert "docs/checked-execution-contract.md" in prompt and "reach first" in prompt
    assert "docs/checked-execution-contract.md" in sp.ENGINEERED_WORLD_FILES


@pytest.mark.parametrize("value", [None, {"version": "checked-motion-v0"}, {"version": "checked-motion-v1", "x": 1},
                                   "checked-motion-v1", True])
def test_case_accepts_only_the_exact_versioned_contract(cell, value):
    opt_in(cell, value)
    with pytest.raises(sp.ProtocolError, match="execution_contract"):
        sp.validate_case(cell.case)


def test_contract_requires_semantic_or_articulation_profile(cell):
    cell.case.update(condition="C", development_gate="oracle")
    cell.case["streaming"] = {**cell.case["streaming"], "execution_contract": CONTRACT}
    with pytest.raises(sp.ProtocolError, match="semantic v2 or articulation v3"):
        sp.validate_case(cell.case)


@pytest.mark.parametrize("authored", [None, {"version": "checked-motion-v1"}, {}])
def test_authored_config_cannot_set_or_disable_the_gate(cell, tmp_path, authored):
    opt_in(cell)
    text = json.dumps({**semantic_config(), "execution_contract": authored})
    assert any("frozen engineering runtime" in e
               for e in sp.streaming_config_errors(text, sp.SEMANTIC_PROFILE))
    # Materialization reads the case, never an authored value.
    runtime = materialized(cell, tmp_path, {**semantic_config(), "execution_contract": authored})
    assert runtime["execution_contract"] == CONTRACT
    started(cell)
    author(cell, config={**semantic_config(), "execution_contract": authored})
    code, error = trial(cell, "initial", 51)
    assert code == 2 and "frozen engineering runtime" in error["error"]
    assert cli(cell, "status")[1]["retries_used_per_seed"]["51"] == 0


def test_contract_cannot_be_toggled_or_its_doc_edited_after_init(cell):
    opt_in(cell)
    assert cli(cell, "init")[0] == 0
    case = json.loads(cell.path.read_text())
    del case["streaming"]["execution_contract"]
    cell.path.write_text(json.dumps(case))
    assert cli(cell, "status")[0] != 0
    opt_in(cell)
    assert cli(cell, "status")[0] == 0
    doc = cell.sim / "docs/checked-execution-contract.md"
    original = doc.read_text()
    doc.write_text("# edited contract doc\n")
    assert "same protocol" in cli(cell, "status")[1]["error"]
    doc.write_text(original)
    assert cli(cell, "status")[0] == 0
