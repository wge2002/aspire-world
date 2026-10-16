"""p3 search control: library, contract, protocol hooks and the p2 retrospective.

Every ledger and trace here is synthetic and is never a run result, except the
last test, which reads the frozen p2 ledgers read-only and skips without them.
"""
from __future__ import annotations

import contextlib
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

SIM = Path(__file__).resolve().parents[1]
for p in (SIM.parents[1], SIM, SIM / "scripts/libero", SIM / "scripts/common", SIM / "tests"):
    sys.path.insert(0, str(p))

from aspire.sim.cap.world_model import search_control as sc
from aspire.sim.cap.world_model import prediction_contract as predictions
from aspire.sim.cap.world_model.executable_world import ExecutableSession, WorldProgramError, run_offline
import executable_world_profile as profile
import native_world_fixloop_state as ledger
import native_world_protocol as protocol
import runtime_world_fixture_data as fx
from test_native_world_fixloop import make_case
from test_prediction_contract import executable_case

P2_LEDGERS = SIM / "docs/experiments/code-world-search-control-p3-20261013/p2-ledgers"


# --- synthetic traces -----------------------------------------------------------


def trace(*, gripper=("match", "match"), handle_residual=0.06, handle_y=(0.0, 0.0), outcome=None):
    """A p2-runtime-shaped event list: proprioceptive gripper + perceived handle."""
    events = []
    for value in (0.0, 0.1):
        events.append({"event": "state_write", "record": {
            "entity": "gripper", "fact": "position", "layer": "observed", "status": "known",
            "value": [0.4, value, 0.2], "unit": "m", "source": {"api": "get_observation"}}})
    for y in handle_y:
        events.append({"event": "state_write", "record": {
            "entity": "handle", "fact": "position", "layer": "observed", "status": "known",
            "value": [0.4, y, 0.1], "unit": "m", "source": {"api": "mask_to_world_points"}}})
    for status in gripper:
        events.append({"event": "prediction_check", "entity": "gripper", "fact": "position",
                       "status": status, "residual": 0.01 if status == "match" else 0.2})
    if handle_residual is not None:
        events.append({"event": "prediction_check", "entity": "handle", "fact": "position",
                       "status": "mismatch", "residual": handle_residual})
    return events


def write_trial(task_dir, directory, events, *, cls=None, progress=None, passed=False):
    folder = task_dir / directory
    (folder / "judgment_world").mkdir(parents=True, exist_ok=True)
    (folder / "judgment_world/events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
    header = f'PRIMITIVE_CLASS = "{cls}"\n' if cls else ""
    (folder / "code.py").write_text(header + "get_observation()\n")
    (folder / "judgment_world/manifest.json").write_text(json.dumps({
        "status": "complete", "search_progress": progress,
        "result": {"sandbox_rc": 0, "task_completed": passed}}))


def synthetic_ledger(task_dir, rows):
    """rows: (phase, seed, bundle, events, cls, progress, passed)."""
    trials = []
    for i, (phase, seed, bundle, events, cls, progress, passed) in enumerate(rows):
        directory = f"development/{phase}/seed_{seed}/attempt_{i}"
        write_trial(task_dir, directory, events, cls=cls, progress=progress, passed=passed)
        trials.append({"phase": phase, "seed": seed, "attempt": 1, "status": "complete",
                       "directory": directory, "spends_retry": True, "executed": True,
                       "bundle_sha256": bundle * 64, "sandbox_rc": 0, "task_completed": int(passed)})
    return {"identity": {"dev_seeds": list(range(51, 66))}, "trials": trials}


# --- signatures, informative attempts, early stop --------------------------------


def test_signature_ignores_absolute_position_and_object_change_but_not_residual(tmp_path):
    a = sc.trial_signature(_trial(tmp_path / "a", trace(handle_y=(0.10, 0.10))), {"passed": False})
    b = sc.trial_signature(_trial(tmp_path / "b", trace(handle_y=(-0.20, -0.20))), {"passed": False})
    c = sc.trial_signature(_trial(tmp_path / "c", trace(handle_residual=0.31)), {"passed": False})
    d = sc.trial_signature(_trial(tmp_path / "d", trace(handle_y=(0.0, 0.15))), {"passed": False})
    e = sc.trial_signature(_trial(tmp_path / "e", trace()), {"passed": True})
    assert a["digest"] == b["digest"]
    # The rounded object-fact change is recorded but no longer hashed (CC Task 2).
    assert d["digest"] == a["digest"] and d["object_change"] != a["object_change"]
    assert not d["digest_includes_change"]
    assert len({a["digest"], c["digest"], e["digest"]}) == 3
    # The Task 1 definition stays available for analysis and does separate them.
    with_change = [sc.trial_signature(tmp_path / x, {"passed": False}, include_change=True)
                   for x in ("a", "d")]
    assert with_change[0]["digest"] != with_change[1]["digest"]
    # Gripper-level residuals are not part of the object-level residual set.
    assert all(r.startswith("handle.") for r in a["object_residuals"])


def _trial(folder, events):
    (folder / "judgment_world").mkdir(parents=True)
    (folder / "judgment_world/events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
    return folder


def test_entity_level_comes_from_recorded_source_not_name():
    events = trace()
    events.append({"event": "state_write", "record": {"entity": "gripper_cam_target", "fact": "position",
                   "layer": "observed", "status": "known", "value": [0, 0, 0],
                   "source": {"api": "segment_sam3_text_prompt"}}})
    assert sc.entity_levels(events) == {"gripper": "gripper", "handle": "object",
                                        "gripper_cam_target": "object"}


def test_entity_levels_read_the_rehearsal_layer_but_never_the_predicted_one():
    events = [{"event": "state_write", "record": {"entity": "hand", "layer": "rehearsal",
                                                   "source": {"api": "get_observation"}}},
              {"event": "state_write", "record": {"entity": "knob", "layer": "rehearsal",
                                                   "source": {"api": "mask_to_world_points"}}},
              {"event": "state_write", "record": {"entity": "ghost", "layer": "predicted",
                                                   "source": {"api": "mask_to_world_points"}}}]
    assert sc.entity_levels(events) == {"hand": "gripper", "knob": "object"}
    events += [{"event": "prediction_committed", "entity": e} for e in ("hand", "knob", "ghost")]
    events += [{"event": "prediction_check", "entity": "knob", "status": "mismatch"}]
    levels = sc.prediction_levels(events)
    assert (levels["gripper"]["committed"], levels["object"]["committed"],
            levels["unmeasured"]["committed"]) == (1, 1, 1)
    assert levels["object"]["mismatch"] == 1 and levels["gripper"]["resolved"] == 0
    assert sc.level_contract_errors(levels) == []
    only_object = [e for e in events if e.get("entity") != "hand"]
    assert ["at least one gripper-level" in p
            for p in sc.level_contract_errors(sc.prediction_levels(only_object))] == [True]


def test_informative_attempts_and_early_stop(tmp_path):
    same = trace()
    rows = [("initial", 51, "a", same, None, None, False),
            ("initial", 52, "a", same, None, None, False),
            ("initial", 53, "a", trace(handle_residual=0.4), None, None, False),
            ("initial", 54, "a", same, None, None, False),
            ("initial", 55, "a", same, None, None, False)]
    data = synthetic_ledger(tmp_path, rows)
    info = sc.informative_attempts(data, task_dir=tmp_path)
    assert [r["informative"] for r in info["attempts"]] == [True, False, True, False, False]
    assert info["fraction"] == pytest.approx(2 / 5) and info["first_success"] is None
    stop = sc.initial_early_stop(data, 3, task_dir=tmp_path)
    assert stop["triggered"] and stop["trigger_attempt"] == 4
    assert stop["matching_attempts"] == [1, 2, 4]
    assert stop["deferred_seeds"] == list(range(55, 66))
    assert stop["banked_attempts"] == 11 * 3
    assert sc.initial_early_stop(data, 4, task_dir=tmp_path)["trigger_attempt"] == 5


def test_identical_passes_and_repairs_never_trigger_early_stop(tmp_path):
    rows = [("initial", s, "a", trace(), None, None, True) for s in (51, 52, 53, 54)]
    rows += [("repair", s, "b", trace(), None, None, False) for s in (55, 56, 57)]
    stop = sc.initial_early_stop(synthetic_ledger(tmp_path, rows), 3, task_dir=tmp_path)
    assert not stop["triggered"] and stop["deferred_seeds"] == [] and stop["banked_attempts"] == 0


# --- saturation ----------------------------------------------------------------


def _repairs(tmp_path, *, bundles=5, per=2, progress=0.1, gripper=("match", "match"), cls="pull",
             passed_at=None):
    rows = []
    for b in range(bundles):
        for j in range(per):
            n = b * per + j
            rows.append(("repair", 51 + n % 15, "abcdefghij"[b], trace(gripper=gripper), cls,
                         progress, n == passed_at))
    data = synthetic_ledger(tmp_path, rows)
    report = sc.primitive_report(data, sc.recorded_progress(tmp_path), task_dir=tmp_path)
    return report, sc.saturation(report)


def test_saturation_fires_when_arm_obeys_and_object_does_not_move(tmp_path):
    report, saturated = _repairs(tmp_path)
    row, = saturated
    assert row["saturated"] and row["class"] == "pull"
    # 5 bundles is reached at attempt 9; 8 attempts is already met there.
    assert row["trigger_attempt"] == 9
    cls, = report["classes"]
    assert cls["bundle_count"] == 5 and cls["attempts"] == 10 and cls["gripper_match"] == 1.0
    assert len(cls["bundles"]) == 5 and all(b["attempts"] == 2 for b in cls["bundles"].values())


@pytest.mark.parametrize("change", [
    {"progress": 0.5},                               # the object responded
    {"gripper": ("mismatch", "mismatch")},           # the arm did not go where told
    {"bundles": 4, "per": 3},                        # too few bundles
    {"bundles": 5, "per": 1},                        # too few attempts
    {"passed_at": 3},                                # the class already succeeded
    {"progress": None},                              # unknown progress is not evidence
])
def test_saturation_does_not_fire(tmp_path, change):
    _, saturated = _repairs(tmp_path, **change)
    assert not saturated[0]["saturated"] and saturated[0]["trigger_attempt"] is None


def test_classes_are_tracked_separately_and_undeclared_is_a_class(tmp_path):
    rows = [("repair", 51 + n, "abcde"[n // 2], trace(), "pull", 0.0, False) for n in range(10)]
    rows += [("repair", 61 + n, "fg"[n // 2], trace(), None, 0.0, False) for n in range(4)]
    report = sc.primitive_report(synthetic_ledger(tmp_path, rows), sc.recorded_progress(tmp_path),
                                 task_dir=tmp_path)
    states = {s["class"]: s["saturated"] for s in sc.saturation(report)}
    assert states == {"pull": True, sc.DEFAULT_CLASS: False}


# --- re-observation: unobserved attempts are not saturation evidence -----------


def _mixed(tmp_path, observed):
    """5 bundles x 2 repairs, gripper matching, progress 0.0; `observed[i]` says
    whether repair i decided an object-level prediction."""
    rows = [("repair", 51 + n, "abcde"[n // 2],
             trace(handle_residual=0.06 if observed[n] else None), "pull", 0.0, False)
            for n in range(len(observed))]
    report = sc.primitive_report(synthetic_ledger(tmp_path, rows), sc.recorded_progress(tmp_path),
                                 task_dir=tmp_path)
    return report, sc.saturation(report)


def test_unobserved_attempts_do_not_count_toward_n(tmp_path):
    # 10 attempts in 5 bundles, but only 7 observed: N = 8 is not met.
    observed = [True] * 7 + [False] * 3
    report, (row,) = _mixed(tmp_path, observed)
    cls, = report["classes"]
    assert (cls["attempts"], cls["observed_attempts"], cls["unobserved_attempts"]) == (10, 7, 3)
    assert not row["saturated"] and row["conditions_now"]["observed_attempts>=N"] is False
    assert row["conditions_now"]["bundles>=K"] and row["conditions_now"]["gripper_match>=threshold"]
    # One more observed attempt in place of an unobserved one: fires at the 8th observed.
    report, (row,) = _mixed(tmp_path / "eight", [True] * 8 + [False] * 2)
    assert row["saturated"] and row["trigger_attempt"] == 9


def test_gripper_match_without_judged_object_never_saturates(tmp_path):
    report, (row,) = _mixed(tmp_path, [False] * 10)
    cls, = report["classes"]
    assert cls["gripper_match"] == 1.0 and cls["observed_attempts"] == 0
    assert cls["best_progress"] is None  # the recorded 0.0 values were never observed
    assert not row["saturated"] and row["trigger_attempt"] is None


def test_observed_zero_progress_still_saturates(tmp_path):
    report, (row,) = _mixed(tmp_path, [True] * 10)
    cls, = report["classes"]
    assert cls["best_progress"] == 0.0 and cls["observed_attempts"] == 10
    assert row["saturated"] and row["trigger_attempt"] == 9


def test_none_progress_is_unobserved_not_zero(tmp_path):
    # Library: a None progress with a judged object row is still unobserved.
    rows = [("repair", 51, "a", trace(), "pull", None, False),
            ("repair", 52, "b", trace(), "pull", 0.2, False)]
    report = sc.primitive_report(synthetic_ledger(tmp_path / "lib", rows),
                                 sc.recorded_progress(tmp_path / "lib"), task_dir=tmp_path / "lib")
    cls, = report["classes"]
    assert (cls["observed_attempts"], cls["unobserved_attempts"], cls["best_progress"]) == (1, 1, 0.2)
    assert [h["observed"] for h in cls["history"]] == [False, True]
    assert cls["history"][0]["best_progress"] is None
    # Live session: progress() returning None is recorded as unobserved, not a fault.
    world = SEARCH_WORLD.replace("return 0.25", "return None")
    result, _, session = fx.run(tmp_path / "live", world=world, contract="p3-search")
    assert result["manifest"]["search_progress"] is None
    assert result["manifest"]["search_progress_status"] == "unobserved" and session.errors == []
    events = [json.loads(line) for line in
              (tmp_path / "live/judgment_world/events.jsonl").read_text().splitlines()]
    row, = [e for e in events if e.get("event") == "search_progress"]
    assert row["progress"] is None and row["progress_status"] == "unobserved"
    observation = sc.trial_observation(tmp_path / "live")
    assert observation["observed"] is False and observation["hint"] == sc.UNOBSERVED_HINT


def test_declared_class_parsing():
    assert sc.declared_class('PRIMITIVE_CLASS = "side_push"\nx = 1\n') == "side_push"
    assert sc.declared_class('PRIMITIVE_CLASS = ""\n') is None
    assert sc.declared_class("PRIMITIVE_CLASS = name\n") is None
    assert sc.declared_class("def (") is None


# --- contract --------------------------------------------------------------------


SEARCH_WORLD = fx.WORLD + "\ndef progress():\n    return 0.25\n"
SEARCH_POLICY = 'PRIMITIVE_CLASS = "synthetic_marker_push"\n' + fx.POLICY


def test_p3_search_is_a_superset_of_p2_runtime():
    assert predictions.contract("p3-search") == "p3-search"
    assert set(predictions.RUNTIME_FAMILY) == {"p2-runtime", "p3-search"}
    for value in (None, "off", "p1", "p2-runtime"):
        assert value in predictions.CONTRACTS


@pytest.mark.parametrize("world,policy,missing", [
    (fx.WORLD, SEARCH_POLICY, "progress()"),
    (SEARCH_WORLD, fx.POLICY, "PRIMITIVE_CLASS"),
])
def test_rehearsal_reports_unsupported_contract_not_a_crash(tmp_path, world, policy, missing):
    (tmp_path / "code.py").write_text(policy)
    (tmp_path / "world.py").write_text(world)
    report = run_offline(tmp_path / "code.py", tmp_path / "world.py", tmp_path / "out",
                         mode="rehearsal", prediction_contract="p3-search")
    assert report["status"] == "unsupported_contract" and missing in report["reason"]
    assert json.loads((tmp_path / "out/offline_result.json").read_text())["status"] == "unsupported_contract"
    assert profile.CONCLUSIONS["unsupported_contract"] == "authored_error"
    assert profile.screening_status([{"status": "unsupported_contract"}]) == "rejected"


def test_complete_p3_candidate_rehearses_like_p2_and_records_progress(tmp_path):
    (tmp_path / "code.py").write_text(SEARCH_POLICY)
    (tmp_path / "world.py").write_text(SEARCH_WORLD)
    p3 = run_offline(tmp_path / "code.py", tmp_path / "world.py", tmp_path / "p3",
                     mode="rehearsal", prediction_contract="p3-search")
    p2 = run_offline(tmp_path / "code.py", tmp_path / "world.py", tmp_path / "p2",
                     mode="rehearsal", prediction_contract="p2-runtime")
    assert p3["status"] == p2["status"] == "unsupported"  # fixture simulate() is Unsupported
    assert p3["search_progress"] == 0.25 and "search_progress" not in p2
    assert p3["prediction_checks"]["contract"] == p2["prediction_checks"]["contract"] == "p2-runtime"


def test_live_session_requires_progress_and_records_it(tmp_path):
    result, _, session = fx.run(tmp_path / "ok", world=SEARCH_WORLD, contract="p3-search")
    assert result["manifest"]["search_progress"] == 0.25 and session.errors == []
    assert result["manifest"]["search_progress_status"] == "observed"
    with pytest.raises(WorldProgramError, match="progress"):
        fx.run(tmp_path / "missing", contract="p3-search")
    bad = SEARCH_WORLD.replace("return 0.25", "return 1.5")
    result, _, session = fx.run(tmp_path / "bad", world=bad, contract="p3-search")
    assert result["manifest"]["search_progress"] is None
    assert result["manifest"]["search_progress_status"] == "error"
    assert [e["operation"] for e in session.errors] == ["progress"]


def test_p2_runtime_session_has_no_search_keys(tmp_path):
    result, _, _ = fx.run(tmp_path, world=SEARCH_WORLD)
    assert "search_progress" not in result["manifest"]
    events = (tmp_path / "judgment_world/events.jsonl").read_text()
    assert "search_progress" not in events


#: A toy world whose simulate() supports every call the toy policy makes, so a
#: rehearsal runs to its end and the per-trial level rule is decided.
LEVEL_WORLD = '''STATE_TEMPLATE_REVISION = "p2-runtime"
FOUNDATION_REVISION = "r1"
state = WorldState()
HAND = {"api": "get_observation", "method": "synthetic end-effector"}
OBJECT = {"api": "mask_to_world_points", "method": "synthetic centroid"}
MEASURED = {"status": "measured"}
PREDICT = {PREDICT}
sim = {"hand": [0.0, 0.0, 0.3], "knob": [0.1, 0.0, 0.1]}

def row(entity, value):
    return {"entity": entity, "fact": "position", "kind": "geometry", "frame": "world",
            "unit": "m", "expected": value, "tolerance": 0.02,
            "target": {"kind": "next_observation"}, "valid_for_calls": 4}

def predict(call):
    if call["function"] != "goto_pose" or not PREDICT:
        raise Unsupported("toy")
    target = [float(v) for v in call["args"][0]]
    return {"predictions": [row(e, target if e == "hand" else sim["knob"]) for e in PREDICT]}

def observe(event): pass

def update(obs, last_action=None):
    ids = obs["evidence_ids"]
    for entity, source in (("hand", HAND), ("knob", OBJECT)):
        state.entity(entity, label=entity, evidence_ids=ids, source=source, uncertainty=MEASURED)
        state.set_fact(entity, "position", obs["values"][entity], kind="geometry", frame="world",
                       unit="m", evidence_ids=ids, source=source, uncertainty=MEASURED)

def query(name, **kwargs): return state.query(name, **kwargs)
def snapshot(): return state.snapshot()

def simulate(call):
    if call["function"] == "goto_pose":
        sim["hand"] = [float(v) for v in call["args"][0]]
        return None
    if call["function"] == "get_observation":
        return {"robot": {"eef": list(sim["hand"])}}
    if call["function"] == "mask_to_world_points":
        return [list(sim["knob"])]
    raise Unsupported("toy")

def done(): return {"verdict": "unknown", "evidence_ids": [], "reason": "toy", "clauses": []}
def progress(): return 0.0
'''
LEVEL_POLICY = '''PRIMITIVE_CLASS = "toy_top_push"
import world

def perceive():
    obs = get_observation()
    points = mask_to_world_points(None, None, None, None)
    world.update({"evidence_ids": [world.evidence_id()],
                  "values": {"hand": list(obs["robot"]["eef"]), "knob": list(points[0])}})

perceive()
goto_pose([0.1, 0.0, 0.15], [0.0, 1.0, 0.0, 0.0])
perceive()
'''


@pytest.mark.parametrize("predict,status,missing", [
    (["hand", "knob"], "complete", []),
    (["knob"], "unsupported_contract", ["gripper"]),
    (["hand"], "unsupported_contract", ["object"]),
    ([], "unsupported_contract", ["gripper", "object"]),
])
def test_rehearsal_requires_a_gripper_and_an_object_prediction(tmp_path, predict, status, missing):
    (tmp_path / "code.py").write_text(LEVEL_POLICY)
    (tmp_path / "world.py").write_text(LEVEL_WORLD.replace("{PREDICT}", repr(predict)))
    report = run_offline(tmp_path / "code.py", tmp_path / "world.py", tmp_path / "out",
                         mode="rehearsal", prediction_contract="p3-search")
    assert report["status"] == status and report["prediction_levels"]["decided"]
    assert [level for level in ("gripper", "object")
            if f"at least one {level}-level" in report.get("reason", "")] == missing
    if status == "complete":
        assert report["prediction_levels"]["gripper"]["match"] == 1
        assert report["prediction_levels"]["object"]["resolved"] == 1
    else:
        assert report["stage"] == "prediction_levels"
        assert profile.screening_status([report]) == "rejected"
    # p2-runtime runs the same candidate without the rule.
    p2 = run_offline(tmp_path / "code.py", tmp_path / "world.py", tmp_path / "p2",
                     mode="rehearsal", prediction_contract="p2-runtime")
    assert p2["status"] == "complete" and "prediction_levels" not in p2


def test_level_rule_is_undecided_when_the_rehearsal_did_not_finish(tmp_path):
    (tmp_path / "code.py").write_text(LEVEL_POLICY + "goto_home_joint_position()\n")
    (tmp_path / "world.py").write_text(LEVEL_WORLD.replace("{PREDICT}", "[]"))
    report = run_offline(tmp_path / "code.py", tmp_path / "world.py", tmp_path / "out",
                         mode="rehearsal", prediction_contract="p3-search")
    assert report["status"] == "unsupported" and report["prediction_levels"]["decided"] is False


def test_prompt_adds_search_section_only_under_p3():
    p3 = profile.section(executable_case(prediction_contract="p3-search"))
    p2 = profile.section(executable_case(prediction_contract="p2-runtime"))
    assert "### Search control p3" in p3 and "### Search control p3" not in p2
    assert p3.count("### Runtime world p2") == p2.count("### Runtime world p2") == 1
    assert p3.replace(profile.SEARCH_CONTRACT, "") == p2
    assert profile.interface_doc(executable_case(prediction_contract="p3-search")) == profile.RUNTIME_INTERFACE


def test_prompt_states_the_re_observation_rule():
    p3 = " ".join(profile.section(executable_case(prediction_contract="p3-search")).split())
    for phrase in ("Re-observe the target. After every manipulation primitive, and once more before the trial ends",
                   "move the gripper out of the way first if it occludes the target",
                   "progress() returns None",
                   'would otherwise read as "the arm arrived and the object did not move"',
                   ">= 8 observed graded attempts", "recorded as unobserved"):
        assert phrase in p3, phrase
    assert any("re-perceive the target object" in rule for rule in sc.RULES)


# --- protocol --------------------------------------------------------------------


PROTO_WORLD = '''STATE_TEMPLATE_REVISION = "p2-runtime"
FOUNDATION_REVISION = "r1"
state = WorldState()
def update(obs, last_action=None): pass
def query(name, **kwargs): return state.query(name, **kwargs)
def snapshot(): return state.snapshot()
def predict(call): raise Unsupported("toy")
def observe(event): pass
def simulate(call): raise Unsupported("toy")
def done(): return {"verdict": "unknown", "evidence_ids": [], "reason": "toy", "clauses": []}
def progress(): return 0.0
'''


class ProtocolHarness:
    """A C/executable cell whose faked replay writes a chosen world trace."""

    def __init__(self, stack, contract="p3-search"):
        root = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        self.case_path, case = make_case(root, "C")
        case.update(executable_case(prediction_contract=contract))
        self.case_path.write_text(json.dumps(case))
        self.case, self.repo, self.task_dir = protocol.load_case(self.case_path)
        stack.enter_context(patch.object(protocol, "verify_runtime", lambda case, repo: None))
        stack.enter_context(patch("executable_world_profile.checks", return_value={
            "status": "checked", "directory": "/synthetic/offline", "reports": [{"status": "complete"}],
            "conclusions": ["supported_pass"], "cached": False, "retryable": False, "identity": {}}))
        stack.enter_context(patch.object(protocol, "run_replay", self.fake_replay))
        self.events, self.progress, self.success = trace(), 0.0, False
        self.state = ledger.NativeWorldState(self.task_dir, protocol.identity(self.case, self.repo))
        self.policy("initial_code.py", "pull")
        (self.task_dir / "initial_world_program.py").write_text(PROTO_WORLD)

    def policy(self, name, cls, salt=""):
        header = f'PRIMITIVE_CLASS = "{cls}"\n' if cls else ""
        path = self.task_dir / name
        path.write_text(header + f"get_observation()\n# {salt}\n")
        return path

    def fake_replay(self, command, *, repo, env, directory, timeout, stdin_path=None):
        output = Path(command[command.index("--args.output-dir") + 1])
        seed = int(command[command.index("--args.trial") + 1])
        ok = int(self.success)
        (output / f"trial_{seed:02d}_sandboxrc_0_reward_{float(ok):.3f}_taskcompleted_{ok}").mkdir(parents=True)
        world = Path(directory) / "judgment_world"
        world.mkdir(parents=True, exist_ok=True)
        (world / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in self.events))
        (world / "manifest.json").write_text(json.dumps({
            "status": "complete", "binding": "shadow", "self_evaluations": [], "search_progress": self.progress,
            "result": {"sandbox_rc": 0, "task_completed": bool(ok)}}))
        if "snapshot" in Path(directory).parts:
            for name in ("scene_snapshot.jpg", "scene_snapshot_wrist.jpg"):
                (self.task_dir / name).write_bytes(b"jpg")
        return 0, ""

    def trial(self, phase, seed, code=None, replicate=None):
        world = None if phase == "snapshot" else self.task_dir / "initial_world_program.py"
        code = None if phase == "snapshot" else (code or self.task_dir / "initial_code.py")
        return protocol.run_trial(self.case, self.repo, self.state, phase, seed, code, world,
                                  None, None, replicate=replicate)


@pytest.fixture
def harness():
    with contextlib.ExitStack() as stack:
        yield lambda contract="p3-search": ProtocolHarness(stack, contract)


def test_protocol_defers_initial_seeds_after_three_identical_signatures(harness):
    h = harness()
    h.trial("snapshot", 51)
    for seed in (51, 52, 53):
        record = h.trial("initial", seed)
    assert record["search_control"]["early_stop"]["triggered"]
    deferred = [r for r in h.state.data["trials"] if r["status"] == ledger.DEFERRED_STATUS]
    assert [r["seed"] for r in deferred] == list(range(54, 66))
    assert all(not r["spends_retry"] and not r["executed"] and r["banked_attempts"] == 3 for r in deferred)
    assert h.state.retries_used(54) == 0 and h.state.retries_remaining(54) == 3
    progress = h.state.progress()
    assert progress["seeds_deferred_initial"] == list(range(54, 66)) and progress["banked_attempts"] == 36
    assert progress["seeds_needing_initial"] == [] and progress["infrastructure_errors"] == []
    with pytest.raises(ledger.ProtocolError, match="deferred"):
        h.trial("initial", 54)
    # Repair is unlocked, and a deferred seed spends its banked attempts there.
    record = h.trial("repair", 54, h.policy("fix_code.py", "pull", "v2"))
    assert record["status"] == "complete" and h.state.retries_used(54) == 1
    assert (h.task_dir / protocol.SEARCH_REPORT).is_file()
    assert record["search_control"]["remaining_budget"]["banked_from_deferred_initial"] == 36
    assert "infeasible" in " ".join(record["search_control"]["rules"])


def test_protocol_refuses_saturated_class_without_spending_and_admits_replicate(harness):
    h = harness()
    h.policy("initial_code.py", "initial_probe")  # its own class: one bundle never saturates
    h.trial("snapshot", 51)
    for i, seed in enumerate(range(51, 66)):
        h.events = trace(handle_residual=0.1 * (i + 1))  # all informative: no early stop
        h.trial("initial", seed)
    assert not h.state.deferred_seeds()
    h.events = trace()
    seeds = iter(range(51, 66))
    for n in range(8):
        h.trial("repair", next(seeds), h.policy(f"fix_{n // 2}.py", "pull", str(n // 2)))
    # The fifth bundle: the report written before this charge is not yet
    # saturated; the one written after it (governing the next repair) is.
    record = h.trial("repair", next(seeds), h.policy("fix_4.py", "pull", "4"))
    assert record["search_control"]["saturated_classes"] == []
    assert record["search_control_next"]["saturated_classes"] == ["pull"]
    report = json.loads((h.task_dir / protocol.SEARCH_REPORT).read_text())
    assert report["summary"]["saturated_classes"] == ["pull"]
    seed = next(seeds)
    used = h.state.retries_used(seed)
    rejected = len(h.state.data["rejected"])
    with pytest.raises(ledger.ProtocolError, match="saturated"):
        h.trial("repair", seed, h.policy("fix_4.py", "pull", "4"))  # even a tested bundle
    assert h.state.retries_used(seed) == used and len(h.state.data["rejected"]) == rejected + 1
    report = json.loads((h.task_dir / protocol.SEARCH_REPORT).read_text())
    assert report["summary"]["saturated_classes"] == ["pull"]
    assert report["saturation"][-1]["trigger_attempt"] == 15 + 9
    # Audit trail: every bundle digest per class in first-seen order.
    pull, = [c for c in report["primitive_report"]["classes"] if c["class"] == "pull"]
    assert pull["first_attempt"] == 16 and pull["bundle_count"] == 5
    assert [b["first_attempt"] for b in pull["bundle_digests"]] == [16, 18, 20, 22, 24]
    assert len({b["bundle_sha256"] for b in pull["bundle_digests"]}) == 5
    assert report["primitive_report"]["same_policy_body_across_classes"] == []
    # A new class is admitted, and so is an explicit replication of the old one.
    assert h.trial("repair", seed, h.policy("fix_6.py", "hook_drag", "6"))["status"] == "complete"
    record = h.trial("repair", seed, h.policy("fix_5.py", "pull", "5"), replicate="confirm on a new seed")
    assert record["search_control"]["replicate"] == "confirm on a new seed"


def test_report_flags_a_renamed_class_with_an_unchanged_policy(harness):
    h = harness()
    h.trial("snapshot", 51)
    for i, seed in enumerate(range(51, 66)):
        h.events = trace(handle_residual=0.1 * (i + 1))
        h.trial("initial", seed)
    h.trial("repair", 51, h.policy("fix_a.py", "pull", "same body"))
    record = h.trial("repair", 52, h.policy("fix_b.py", "lever_push", "same body"))
    renamed = record["search_control_next"]["same_policy_body_across_classes"]
    assert [r["classes"] for r in renamed] == [["lever_push", "pull"]]
    assert sc.policy_body_sha256('PRIMITIVE_CLASS = "a"\nx = 1\n') == \
        sc.policy_body_sha256('PRIMITIVE_CLASS = (\n    "b")\nx = 1\n')


def test_solver_reads_the_search_summary_from_trial_stdout(harness, capsys, monkeypatch):
    """Where the summary enters the solver's context: `trial` and `status` stdout.

    The native solver runs native_world_protocol.py through its shell tool and
    reads the printed JSON. main() prints the trial record, which carries the
    admission-time summary (`search_control`, run_trial) and the post-trial one
    (`search_control_next`, search_after_trial); `status` prints it too.
    """
    h = harness()
    h.trial("snapshot", 51)
    for seed in (51, 52, 53):
        h.trial("initial", seed)
    fix = h.policy("fix_code.py", "pull", "v2")
    monkeypatch.setattr(protocol, "verify_runtime", lambda case, repo: None)
    for argv, key in ((["trial", "--phase", "repair", "--seed", "54", "--code", str(fix),
                        "--world-program", str(h.task_dir / "initial_world_program.py")], "search_control_next"),
                      (["status"], "search_control")):
        monkeypatch.setattr(sys, "argv", ["native_world_protocol.py", "--case", str(h.case_path), *argv])
        assert protocol.main() == 0
        printed = json.loads(capsys.readouterr().out)
        summary = printed[key]
        assert summary["revision"] == "p3-search" and summary["report"].endswith(protocol.SEARCH_REPORT)
        assert any("infeasible" in rule for rule in summary["rules"])
        assert any("renamed class" in rule for rule in summary["rules"])
        assert summary["remaining_budget"]["banked_from_deferred_initial"] == 36
    assert {c["class"] for c in printed["search_control"]["classes"]} == {"pull"}


def test_search_control_next_flags_an_unobserved_trial(harness):
    h = harness()
    h.trial("snapshot", 51)
    observed = h.trial("initial", 51)["search_control_next"]
    assert observed["this_trial"]["observed"] is True and "hint" not in observed
    h.events = trace(handle_residual=None)  # gripper re-checked, target never judged
    unobserved = h.trial("initial", 52)["search_control_next"]
    assert unobserved["this_trial"] == {"progress": 0.0, "object_predictions_decided": 0,
                                        "gripper_predictions_decided": 2, "observed": False,
                                        "hint": sc.UNOBSERVED_HINT}
    assert unobserved["hint"] == "target not re-observed: this attempt is not evidence for saturation"
    h.events, h.progress = trace(), None  # object judged, but progress() returned None
    none = h.trial("initial", 53)["search_control_next"]
    assert none["this_trial"]["observed"] is False and none["hint"] == sc.UNOBSERVED_HINT
    cls, = none["classes"]
    assert (cls["observed_attempts"], cls["unobserved_attempts"]) == (1, 2)


def test_protocol_refuses_a_bundle_missing_the_p3_declarations(harness):
    h = harness()
    h.trial("snapshot", 51)
    with pytest.raises(ledger.ProtocolError, match="PRIMITIVE_CLASS"):
        h.trial("initial", 51, h.policy("bare.py", None))
    assert h.state.retries_used(51) == 0


def test_admission_rejects_a_rehearsal_without_both_prediction_levels_free_of_charge(harness):
    h = harness()
    h.trial("snapshot", 51)
    rejected = {"status": "rejected", "directory": "/synthetic/offline", "conclusions": ["authored_error"],
                "reports": [{"status": "unsupported_contract", "stage": "prediction_levels"}],
                "cached": False, "retryable": False, "identity": {}}
    with patch("executable_world_profile.checks", return_value=rejected):
        with pytest.raises(ledger.ProtocolError, match="offline candidate error"):
            h.trial("initial", 51)
    assert h.state.retries_used(51) == 0 and len(h.state.data["rejected"]) == 1
    assert not [r for r in h.state.data["trials"] if r["phase"] == "initial"]


def test_p2_runtime_protocol_is_untouched_by_search_control(harness):
    h = harness("p2-runtime")
    h.trial("snapshot", 51)
    for seed in (51, 52, 53, 54):
        record = h.trial("initial", seed)
        assert "search_control" not in record
    assert not h.state.deferred_seeds()
    assert "seeds_deferred_initial" not in h.state.progress()
    assert "search_control" not in h.state.data
    assert not (h.task_dir / protocol.SEARCH_REPORT).exists()
    assert "prediction_contract" in protocol.identity(h.case, h.repo)
    with pytest.raises(TypeError):
        protocol.run_trial(h.case, h.repo, h.state, "initial", 55, None, None, None, None, "x")


# --- retrospective -----------------------------------------------------------------


@pytest.mark.skipif(not (P2_LEDGERS / "stove_p2_r1/case.json").is_file(),
                    reason="frozen p2 ledgers are not present on this host")
def test_retrospective_on_frozen_p2_ledgers(tmp_path):
    import subprocess
    out = tmp_path / "retro.json"
    subprocess.run([sys.executable, str(SIM / "scripts/libero/search_control_retro.py"),
                    "--study-root", str(P2_LEDGERS), "--cells", "stove_p2_r1,drawer_p2_r2",
                    "--out", str(out)], check=True, capture_output=True)
    data = json.loads(out.read_text())
    assert data["read_only"] and data["thresholds_used"]["K"] == 5
    for cell in data["cells"]:
        assert cell["progress_source"] == "retrospective_proxy"
        assert cell["class_source"] == "single_class_assumption"
        assert cell["declared_classes_seen"] == [sc.DEFAULT_CLASS]
        assert cell["charged_attempts"] >= cell["graded_attempts"] > 0
        assert [a["n"] for a in cell["per_attempt"]] == list(range(1, cell["charged_attempts"] + 1))
    stove = data["cells"][0]
    assert stove["first_success"] == 21  # PLAN.md table
    assert len(data["sensitivity"]["saturation"]) == 27
