"""The prediction-contract v1 study's staging, preflight and submission support, CPU only.

Synthetic cases only. The scope gate must accept a render whose p1 section
matches the case and refuse a swapped one; the deadlines must carry the 14 h
development watchdog; the preflight proof inserted into every generated runner
must separate the two arms; prepare, supervisor, driver and submit must pin this
study's paths, arms and launch directory and nothing of the gate study's; and the
gate study's own support must still be the 10 h, judge-capable original.
"""
from __future__ import annotations

import contextlib
import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

SIM = Path(__file__).resolve().parents[1]
STUDY = SIM / "docs/experiments/code-world-prediction-base-20261008"
SUPPORT = STUDY / "support"
GATE_STUDY = SIM / "docs/experiments/code-world-gate-ablation-20261005"
for extra in (SIM, SIM / "scripts/common", SIM / "scripts/libero"):
    sys.path.insert(0, str(extra))
sys.path.insert(0, str(SIM.parents[1]))

import executable_world_profile as profile
import native_world_campaign as campaign
import test_native_world_fixloop as fixtures

#: Support modules both studies import by bare name. The gate-study tests keep
#: theirs in sys.modules; this file loads its own copies under private names and
#: never leaves a bare-name entry behind.
SHARED_SUPPORT = ("two_task_scope", "full_deadlines", "output_ownership", "pristine_a_render")


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@contextlib.contextmanager
def private_support_imports():
    saved = {n: sys.modules.pop(n) for n in SHARED_SUPPORT if n in sys.modules}
    path = list(sys.path)
    try:
        yield
    finally:
        for name in SHARED_SUPPORT:
            sys.modules.pop(name, None)
        sys.modules.update(saved)
        sys.path[:] = path


scope = load(SUPPORT / "two_task_scope.py", "prediction_two_task_scope")
deadlines = load(SUPPORT / "full_deadlines.py", "prediction_full_deadlines")
preflights = load(STUDY / "make-preflights.py", "prediction_make_preflights")
submit = load(STUDY / "submit-prediction-study.py", "prediction_submit")


@pytest.fixture(scope="module")
def prepare():
    with private_support_imports():
        return load(STUDY / "prepare-prediction-study.py", "prediction_prepare_under_test")


def study_case(tmp_path, arm):
    path, case = fixtures.make_case(tmp_path, "C")
    doc = {"off": "docs/experiments/code-world-gate-ablation-20261005/NATIVE_WORLD_INTERFACE.md",
           "p1": "docs/experiments/code-world-prediction-base-20261008/NATIVE_WORLD_INTERFACE.md"}[arm]
    case.update(profile="judgment", executable_world_revision="r1", foundation_revision="r1",
                c_arm="full", c_lineage="fresh", development_gate="oracle", world_interface_doc=doc,
                inference_endpoint="http://127.0.0.1:8121", heldout_seeds=list(range(1, 51)))
    if arm == "p1":
        case["prediction_contract"] = "p1"
    return case


# ---- scope gate -------------------------------------------------------------


def test_p1_heading_is_the_one_the_renderer_writes():
    first = profile.PREDICTION_CONTRACT.strip().splitlines()[0]
    assert scope.P1_HEADING == first == preflights.P1_HEADING
    assert "prediction_checks" not in (GATE_STUDY / "NATIVE_WORLD_INTERFACE.md").read_text()


@pytest.mark.parametrize("arm", ["off", "p1"])
def test_scope_accepts_the_matching_arm_and_refuses_the_swapped_one(tmp_path, arm):
    case = study_case(tmp_path, arm)
    text = scope.report_handoff_worker(campaign.worker_prompt(case, SIM))
    assert (scope.P1_HEADING in text) is (arm == "p1")
    scope.verify_worker(text, case, SIM)
    swapped = dict(case)
    if arm == "p1":
        swapped.pop("prediction_contract")
    else:
        swapped["prediction_contract"] = "p1"
    with pytest.raises(scope.ScopeError, match="prediction-contract section"):
        scope.verify_worker(text, swapped, SIM)
    with pytest.raises(scope.ScopeError, match="unknown prediction_contract"):
        scope.verify_worker(text, {**case, "prediction_contract": "p2"}, SIM)
    assert scope.remaining_budget(case) == {str(s): 3 for s in range(51, 66)}


# ---- deadlines --------------------------------------------------------------


def test_development_watchdog_is_fourteen_hours_and_derived_values_follow():
    assert deadlines.DEVELOPMENT == 14 * 3600
    assert deadlines.TOTAL == 2400 + 1920 + 14 * 3600 + 5 * 3600 + 1800 == 74520
    assert deadlines.DLC_MAX_RUNNING_MINUTES == 1242
    assert deadlines.ESTIMATE_HOURS == (12, 20)
    assert deadlines.summary()["development_deadline_seconds"] == 50400
    doc = deadlines.__doc__
    assert "14 h (10 h in the gate study" in doc and "    10 h. Worst case" not in doc
    # Every other watchdog is the gate study's.
    gate = load(GATE_STUDY / "support/full_deadlines.py", "gate_full_deadlines_reference")
    assert gate.DEVELOPMENT == 10 * 3600
    for name in ("TRIAL_TIMEOUT", "SERVICE_STARTUP", "NATIVE_COMPAT", "HELDOUT", "FINALIZE_MARGIN"):
        assert getattr(deadlines, name) == getattr(gate, name), name


# ---- preflight proof --------------------------------------------------------


def run_proof(tmp_path, contract, manifest, events, prompt):
    namespace = {"json": json, "PREDICTION_CONTRACT": contract, "P1_HEADING": preflights.P1_HEADING}
    exec(preflights.PROOF_SOURCE, namespace)
    events_path, prompt_path = tmp_path / "events.jsonl", tmp_path / "worker-prompt.md"
    events_path.write_text("".join(json.dumps({"event": e}) + "\n" for e in events))
    if prompt is None:
        prompt_path.unlink(missing_ok=True)
    else:
        prompt_path.write_text(prompt)
    return namespace["prediction_proof"](manifest, events_path, prompt_path)


P1_PROMPT = "intro\n" + "### Prediction contract p1: predictions checked against the next real observation\n"
OFF_PROMPT = "intro\nno contract here\n"
ORACLE = {"result": {"task_completed": 0}}


def test_p1_proof_needs_prediction_checks_one_prediction_event_and_the_section(tmp_path):
    good = {**ORACLE, "prediction_checks": {"committed": 1, "resolved": 0}}
    ok, evidence = run_proof(tmp_path, "p1", good, ["observe", "prediction"], P1_PROMPT)
    assert ok and evidence["prediction_events"] == 1 and evidence["prediction_check_events"] == 0
    assert not run_proof(tmp_path, "p1", good, ["observe"], P1_PROMPT)[0]
    assert not run_proof(tmp_path, "p1", dict(ORACLE), ["prediction"], P1_PROMPT)[0]
    assert not run_proof(tmp_path, "p1", good, ["prediction"], OFF_PROMPT)[0]
    assert not run_proof(tmp_path, "p1", good, ["prediction"], None)[0]
    assert not run_proof(tmp_path, "p1", {"result": {}, "prediction_checks": {}}, ["prediction"], P1_PROMPT)[0]


def test_off_proof_refuses_any_prediction_check_or_section(tmp_path):
    assert run_proof(tmp_path, None, dict(ORACLE), ["observe", "prediction"], OFF_PROMPT)[0]
    assert not run_proof(tmp_path, None, {**ORACLE, "prediction_checks": {}}, [], OFF_PROMPT)[0]
    assert not run_proof(tmp_path, None, dict(ORACLE), [], P1_PROMPT)[0]
    assert not run_proof(tmp_path, None, dict(ORACLE), [], None)[0]


@pytest.mark.parametrize("cell,contract", [("bowl_off_r1", None), ("stove_p1_r2", "p1")])
def test_generated_runner_carries_the_arm_and_the_proof(cell, contract):
    pins = {name: "0" * 64 for name in preflights.PINNED}
    text = preflights.render(cell, "put_the_bowl_on_the_plate", contract, pins, str(SIM))
    assert f"PREDICTION_CONTRACT = {contract!r}" in text and f"CELL = {cell!r}" in text
    assert "def prediction_proof(" in text and "world_ok and contract_ok" in text
    assert "/mnt/home/gewang/experiments/code-world-prediction-base-20261008" in text
    assert f"dsw-preflight-{cell.replace('_', '-')}" in text and "drawer_C" not in text
    assert "development_gate='oracle'" in text and "charged" not in text.lower()
    for name in ("cap/world_model/prediction_contract.py", "cap/world_model/world_use_audit.py"):
        assert name in preflights.PINNED
    with pytest.raises(SystemExit):
        preflights.render(cell, "t", "p2", pins, str(SIM))


# ---- prepare ----------------------------------------------------------------


def test_prepare_declares_twenty_cells_two_arms_and_this_study_only(prepare):
    assert prepare.NAME == "code-world-prediction-base-20261008"
    assert prepare.ARMS == {"off": None, "p1": "p1"} and prepare.GATE == "oracle"
    assert len(prepare.CELLS) == 20 and {c for _, c in prepare.CELLS} == {"C"}
    assert sorted(prepare.contract_of(c) or "off" for c, _ in prepare.CELLS).count("p1") == 10
    assert prepare.LAUNCH.name == "launch-20261009"
    assert prepare.STUDY_LAUNCH_FILES == ("dlc-supervisor.py", "dlc-queue-entry.sh")
    assert "scripts/libero/prediction_study_analysis.py" in prepare.COMMON_OVERLAY
    assert "cap/world_model/prediction_contract.py" in prepare.C_ONLY_OVERLAY
    assert prepare.base.PARENT == Path("/mnt/home/gewang/experiments/code-world-prediction-base-20261008")
    off_doc = SIM / prepare.INTERFACE_DOCS["off"]
    assert prepare.base.digest(off_doc) == prepare.INTERFACE_SHA256["off"]
    assert prepare.INTERFACE_DOCS["p1"] == Path("docs/experiments/code-world-prediction-base-20261008/NATIVE_WORLD_INTERFACE.md")


def synthetic_case(prepare, cell):
    case = {**prepare.COMMON_EXPECTED, **prepare.CONDITION_EXPECTED["C"],
            "id": cell, "task": prepare.TASKS[cell.split("_")[0]],
            "world_interface_doc": str(prepare.interface_doc(cell)),
            "development_gate": "oracle", "repeat": prepare.repeat_of(cell)}
    if prepare.contract_of(cell):
        case["prediction_contract"] = "p1"
    return case


def test_check_case_keeps_the_contract_key_on_p1_only_and_refuses_a_judge(prepare):
    off, p1 = synthetic_case(prepare, "bowl_off_r1"), synthetic_case(prepare, "wine_p1_r2")
    assert "prediction_contract" not in off and p1["prediction_contract"] == "p1"
    assert prepare.check_case("bowl_off_r1", "C", off)["problems"] == []
    assert prepare.check_case("wine_p1_r2", "C", p1)["problems"] == []
    assert prepare.check_case("bowl_off_r1", "C", {**off, "prediction_contract": "off"})["problems"]
    assert prepare.check_case("wine_p1_r2", "C", {k: v for k, v in p1.items() if k != "prediction_contract"})["problems"]
    for cell, case in (("bowl_off_r1", off), ("wine_p1_r2", p1)):
        assert prepare.check_case(cell, "C", {**case, "judge": {"model": "x"}})["problems"]
        assert prepare.check_case(cell, "C", {**case, "development_gate": "self_eval"})["problems"]
    assert off["world_interface_doc"].startswith("docs/experiments/code-world-gate-ablation-20261005/")


# ---- supervisor, driver, submit --------------------------------------------


def load_supervisor():
    if "native_cc_runtime" not in sys.modules:
        stub = types.ModuleType("native_cc_runtime")
        stub.Service = stub.ServiceWatch = object
        stub.atomic_json = lambda path, value: None
        stub.isolated_jit_env = lambda name: {}
        stub.new_attempt = lambda control: control
        stub.stop_processes = lambda processes: None
        sys.modules["native_cc_runtime"] = stub
    return load(SUPPORT / "dlc-supervisor.py", "prediction_dlc_supervisor")


def test_supervisor_pins_the_contract_sources_and_admits_only_this_study_cases():
    sup = load_supervisor()
    for name in ("scripts/libero/prediction_study_analysis.py", "cap/world_model/prediction_contract.py",
                 "cap/world_model/development_gate.py", "scripts/libero/gate_study_analysis.py"):
        assert name in sup.REQUIRED_RUNTIME
    text = (SUPPORT / "dlc-supervisor.py").read_text()
    assert 'assert case.get("development_gate") == "oracle"' in text
    assert 'assert case.get("prediction_contract") in (None, "p1")' in text
    assert 'assert "judge" not in case' in text
    assert '("oracle", "self_eval", "vlm_judge")' not in text


def test_driver_skips_post_hoc_judging_without_a_judge():
    driver = (SUPPORT / "run_two_task_cell.py").read_text()
    block = driver.split('state["heldout"]["accounting"] = check_heldout_rows')[1].split("except Blocked")[0]
    assert 'if "judge" in case:' in block
    judged, skipped = block.split("else:", 1)
    assert "judge_heldout_seeds(case, repo, env, folder)" in judged
    assert '"performed": False' in skipped and "judge_heldout_seeds" not in skipped
    assert block.index('state["status"] = TERMINAL') > block.index("else:")


def test_submit_targets_this_study_queue_and_needs_both_platform_entries(tmp_path, monkeypatch):
    text = (STUDY / "submit-prediction-study.py").read_text()
    assert "code-world-gate-ablation-20261005" not in text and "prepare-gate-study.py" not in text
    assert submit.ROOT == Path("/mnt/home/gewang/experiments/code-world-prediction-base-20261008")
    assert submit.JOB_PREFIX == "aspire-pred-1008-queue" and submit.LAUNCH.name == "launch-20261009"
    assert submit.PLATFORM_ENTRIES == ("dlc-entry.sh", "dlc-queue-entry.sh")
    plan = submit.command("aspire-pred-1008-queue-01", 8, 4320)
    assert plan[-1] == str(submit.ROOT / "launch-20261009/dlc-queue-entry.sh") and "--case" not in plan
    assert plan[plan.index("--gpus") + 1] == "8" and plan[plan.index("--max-minutes") + 1] == "4320"
    assert 3 * deadlines.DLC_MAX_RUNNING_MINUTES <= 4320
    with pytest.raises(Exception):
        submit.gpu_total("4")
    # preflights_passed: both entry logs and the real-work summary are required.
    root, doc = tmp_path / "root", tmp_path / "doc"
    monkeypatch.setattr(submit, "ROOT", root)
    monkeypatch.setattr(submit, "DOC", doc)
    monkeypatch.setattr(submit, "LAUNCH", root / "launch-20261009")
    cell = "bowl_p1_r1"
    summary = root / "coordination/dsw-preflight-bowl-p1-r1/summary.json"
    summary.parent.mkdir(parents=True)
    summary.write_text(json.dumps({"state": "passed", "cell": cell}))
    doc.mkdir()

    def log(entry):
        (doc / f"platform-{Path(entry).stem}-{cell}.log").write_text(
            f"bash {root / 'launch-20261009' / entry} --case x\n"
            '"environment_preflight": "passed"\npreflight 通过(rc=0)\n')

    log("dlc-queue-entry.sh")
    with pytest.raises(RuntimeError, match="no dlc-entry.sh platform preflight log"):
        submit.preflights_passed(cell)
    log("dlc-entry.sh")
    submit.preflights_passed(cell)
    (doc / f"platform-dlc-entry-{cell}.log").write_text('"environment_preflight": "passed"\npreflight 通过(rc=0)\n')
    with pytest.raises(RuntimeError, match="did not exercise"):
        submit.preflights_passed(cell)
    log("dlc-entry.sh")
    summary.write_text(json.dumps({"state": "failed", "cell": cell}))
    with pytest.raises(RuntimeError, match="real-work preflight"):
        submit.preflights_passed(cell)


def test_gate_study_support_is_untouched_by_this_study():
    supervisor = (GATE_STUDY / "support/dlc-supervisor.py").read_text()
    assert '("oracle", "self_eval", "vlm_judge")' in supervisor
    assert "prediction_contract.py" not in supervisor
    driver = (GATE_STUDY / "support/run_two_task_cell.py").read_text()
    assert 'if "judge" in case:' not in driver
    assert "P1_HEADING" not in (GATE_STUDY / "support/two_task_scope.py").read_text()
