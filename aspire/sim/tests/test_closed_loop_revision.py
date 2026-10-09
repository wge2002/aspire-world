"""Behavioral regressions for the opt-in closed-loop revision.

Everything here is a synthetic fixture. The worlds are task-neutral toy state
machines and the policies are the same few lines of generated-style code; no
task predicate, simulator, model or held-out artifact is involved. What the
tests establish is the runtime contract the revision promises: online branches
are admissible only against current evidence, observed and predicted facts stay
apart, target identity survives missing or ambiguous perception, and the
framework's final shadow judgment is recorded separately from the decisions the
policy made while acting.
"""
import hashlib
import json
from pathlib import Path
import sys

import pytest

SIM = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SIM.parents[1]))
sys.path.insert(0, str(SIM / "scripts/libero"))
from aspire.sim.cap.world_model.decision_revision import (
    ADMITS, ARMS, BRANCHES, adjudicate, aggregate, decision_counts, feedback,
    revision_errors, source_findings)
from aspire.sim.cap.world_model.evidence_state import EvidenceState
from aspire.sim.cap.world_model.executable_world import ExecutableSession, run_offline
import executable_world_profile as profile


#: One synthetic world for every branch. It reads two measured facts through the
#: control accessor, states them as clauses, and names the branch its own
#: authored rule supports. `ANNOUNCED` is a fixture hook that lets a test make
#: the world contradict itself, which is exactly what the runtime must refuse.
CLOSED_WORLD = '''
FOUNDATION_REVISION = "r1"
ANNOUNCED = None
state = WorldState()

def clause(name, fact):
    if fact["status"] != "known":
        return {"name": name, "verdict": "unknown", "layer": fact.get("layer", "observed"),
                "evidence_ids": [], "reason": fact["reason"]}
    return {"name": name, "verdict": "true" if fact["value"] else "false",
            "layer": fact["layer"], "evidence_ids": fact["evidence_ids"],
            "reason": "authored clause on a measured fact"}

def update(obs, last_action=None):
    layers = obs.get("layers") or {}
    for key, value in (obs.get("values") or {}).items():
        state.set(key, value, evidence_ids=obs["evidence_ids"], identity=obs.get("identity"),
                  layer=layers.get(key, "observed"), reason="authored measurement")

def query(name, **kwargs): return state.query(name, **kwargs)
def snapshot(): return state.snapshot()
def predict(call): raise Unsupported("no learned predictor")
def observe(event): pass
def simulate(call):
    if call["function"] == "get_observation": return {}
    raise Unsupported("no modeled effect")

def done():
    grip = state.observed_only("grip")
    goal = state.observed_only("goal")
    progress = state.observed_only("progress")
    clauses = [clause("grip", grip), clause("goal", goal)]
    if progress["status"] == "known":
        clauses.append(clause("progress", progress))
    ids = sorted({i for c in clauses for i in c["evidence_ids"]})
    if ANNOUNCED is not None:
        verdict, branch = ANNOUNCED
        if verdict == "unknown":
            announced_clauses = [{"name": "g", "verdict": "unknown", "layer": "observed",
                                  "evidence_ids": [], "reason": "authored unknown"}]
        else:
            announced_clauses = [{"name": "g", "verdict": verdict, "layer": "observed",
                                  "evidence_ids": ids, "reason": "authored clause"}]
        return {"verdict": verdict, "branch": branch, "evidence_ids": ids,
                "reason": "authored announcement", "clauses": announced_clauses}
    if grip["status"] == "known" and grip["value"] is False:
        return {"verdict": "false", "branch": "recover", "evidence_ids": ids,
                "reason": "authored: attachment lost", "clauses": clauses}
    if progress["status"] == "known" and progress["value"] is False:
        return {"verdict": "false", "branch": "recover", "evidence_ids": ids,
                "reason": "authored: measured lack of progress while holding", "clauses": clauses}
    if grip["status"] == "known" and goal["status"] == "known" and goal["value"] and grip["value"]:
        return {"verdict": "true", "branch": "stop", "evidence_ids": ids,
                "reason": "authored: goal met while holding", "clauses": clauses}
    if grip["status"] != "known" or goal["status"] != "known":
        return {"verdict": "unknown", "branch": "observe", "evidence_ids": ids,
                "reason": "authored: state not established", "clauses": clauses}
    return {"verdict": "false", "branch": "continue", "evidence_ids": ids,
            "reason": "authored: target not yet displaced", "clauses": clauses}
'''

#: A world whose goal carries two evidence IDs, one of them from before the last
#: motion. Legacy freshness admits it through any-fresh; the closed-loop revision
#: must not.
MIXED_WORLD = '''
FOUNDATION_REVISION = "r1"
EVIDENCE = []
state = WorldState()
def update(obs, last_action=None): pass
def query(name, **kwargs): return state.query(name, **kwargs)
def snapshot(): return state.snapshot()
def predict(call): raise Unsupported("no learned predictor")
def observe(event): pass
def simulate(call): raise Unsupported("no modeled effect")
def done():
    return {"verdict": "true", "branch": "stop", "evidence_ids": EVIDENCE, "reason": "authored",
            "clauses": [{"name": "g", "verdict": "true", "layer": "observed",
                         "evidence_ids": EVIDENCE, "reason": "authored clause"}]}
'''

#: The one synthetic policy. It never inspects the world directly: it asks for a
#: judgment and maps the branch the judgment returns to its next action.
POLICY = '''
ACTIONS = {"stop": "finish", "observe": "reobserve", "recover": "replan", "continue": "proceed"}

def next_action(world):
    answer = world.done()
    return ACTIONS[answer["branch"]], answer
'''


def open_session(tmp_path, name, text=CLOSED_WORLD, **kwargs):
    path = tmp_path / f"{name}.py"
    path.write_text(text)
    return ExecutableSession(path, hashlib.sha256(path.read_bytes()).hexdigest(),
                             tmp_path / f"{name}_out", **kwargs)


def measure(session, values, layers=None, identity=None):
    """One real observation, then the policy's derived update from it."""
    session.invoke("get_observation", lambda: {"values": values}, (), {})
    obs = {"values": values, "evidence_ids": [session.last_observation]}
    if layers:
        obs["layers"] = layers
    if identity is not None:
        obs["identity"] = identity
    session.module.update(obs, "observe")
    return session


def run_policy(session):
    namespace = {}
    exec(compile(POLICY, "policy.py", "exec"), namespace)
    return namespace["next_action"](session.module)


def closed_session(tmp_path, name, text=CLOSED_WORLD, **kwargs):
    return open_session(tmp_path, name, text, closed_loop=True, **kwargs)


# --- the same policy, four outcomes -----------------------------------------


@pytest.mark.parametrize("values,layers,action,verdict,branch", [
    ({"grip": True, "goal": True}, None, "finish", "true", "stop"),
    ({"grip": False, "goal": False}, None, "replan", "false", "recover"),
    ({"grip": True, "goal": False}, None, "proceed", "false", "continue"),
    # Grip is intact and the goal is not claimed, but the measured progress fact
    # is false: grounded lack of progress chooses recovery, not `continue`.
    ({"grip": True, "goal": False, "progress": False}, None, "replan", "false", "recover"),
    ({"grip": True, "goal": None}, None, "reobserve", "unknown", "observe"),
    ({"grip": None, "goal": True}, None, "reobserve", "unknown", "observe"),
    ({"grip": True, "goal": True}, {"goal": "predicted"}, "reobserve", "unknown", "observe"),
])
def test_one_policy_branches_on_grounded_state(tmp_path, values, layers, action, verdict, branch):
    """A measured state selects the branch; a prediction never does.

    The last row is the central claim of the revision: a predicted goal that the
    *measurement* does not confirm leaves the state unestablished, so the same
    policy reobserves instead of stopping.
    """
    with closed_session(tmp_path, "world") as session:
        measure(session, values, layers)
        chosen, answer = run_policy(session)
        assert answer["verdict"] == verdict
        assert answer["branch"] == branch
        assert chosen == action
        assert answer["origin"] == "policy"
        assert answer["branch_source"] == "authored"
        assert answer["requested_branch"] == branch


def test_prediction_is_readable_as_a_hint_but_never_through_the_control_accessor(tmp_path):
    with closed_session(tmp_path, "world") as session:
        measure(session, {"grip": True, "goal": False})
        session.module.state.set("goal", True, layer="predicted")
        assert session.module.state.query("goal", layer="predicted")["value"] is True
        assert session.module.state.observed_only("goal")["value"] is False
        # Re-updating the measured fact does not resurrect the stale prediction.
        measure(session, {"grip": True, "goal": False})
        assert session.module.state.observed_only("goal")["value"] is False
        assert run_policy(session)[0] == "proceed"


def test_measured_displacement_is_unknown_after_a_motion_until_reobserved(tmp_path):
    with closed_session(tmp_path, "world") as session:
        measure(session, {"grip": True, "goal": True})
        assert run_policy(session)[0] == "finish"
        session.invoke("goto_pose", lambda position: None, ([0, 0, 1],), {})
        assert session.module.state.observed_only("goal")["status"] == "unknown"
        assert run_policy(session)[0] == "reobserve"
        # Fresh evidence restores valid control without any new authored branch.
        measure(session, {"grip": True, "goal": True})
        assert session.module.state.observed_only("goal")["status"] == "known"
        assert run_policy(session)[0] == "finish"


# --- the runtime refuses a branch the evidence cannot support ----------------


@pytest.mark.parametrize("announced", [
    ("unknown", "stop"),
    ("true", "recover"),
    ("true", "continue"),
    ("false", "stop"),
    ("true", None),
    ("true", "invented"),
])
def test_contradictory_authored_branch_is_downgraded_not_obeyed(tmp_path, announced):
    with closed_session(tmp_path, "world") as session:
        measure(session, {"grip": True, "goal": True})
        session.module.ANNOUNCED = announced
        chosen, answer = run_policy(session)
        assert answer["branch"] == "observe"
        assert chosen == "reobserve"
        assert answer["branch_source"] == "downgraded"
        assert answer["requested_branch"] == announced[1]
        assert answer["verdict"] == "unknown"
        assert answer["branch_reason"]
        # The authored verdict is preserved for audit, never silently discarded.
        if announced[0] != "unknown":
            assert answer["authored_verdict"] == announced[0]


def test_adjudication_table_is_the_contract():
    current = lambda ids: True
    for verdict, branch in (("true", "stop"), ("false", "recover"), ("false", "continue"),
                            ("false", "observe"), ("unknown", "observe")):
        assert branch in ADMITS[verdict]
        status = adjudicate({"verdict": verdict, "evidence_ids": [0]},
                            {"branch": branch}, current)
        assert status["branch"] == branch and status["branch_source"] == "authored"
    for verdict, branch in (("true", "recover"), ("unknown", "stop"), ("false", "stop")):
        status = adjudicate({"verdict": verdict, "evidence_ids": [0]}, {"branch": branch}, current)
        assert (status["branch"], status["branch_source"], status["verdict"]) == \
               ("observe", "downgraded", "unknown")
    stale = adjudicate({"verdict": "true", "evidence_ids": [0]}, {"branch": "stop"}, lambda ids: False)
    assert stale["branch"] == "observe" and "current observation" in stale["branch_reason"]
    assert not (ADMITS["true"] & ADMITS["unknown"])
    assert BRANCHES == frozenset({"stop", "observe", "recover", "continue"})


# --- strict (closed-loop) versus legacy freshness ----------------------------


def test_closed_loop_refuses_mixed_stale_and_fresh_goal_evidence(tmp_path):
    """The laundering case: one fresh ID must not carry a stale one."""
    for closed_loop in (True, False):
        session = open_session(tmp_path, f"mixed_{closed_loop}", MIXED_WORLD,
                               closed_loop=closed_loop)
        with session as s:
            s.invoke("get_observation", lambda: {}, (), {})      # ID 0
            s.invoke("goto_pose", lambda position: None, ([0, 0, 1],), {})  # motion
            s.invoke("get_observation", lambda: {}, (), {})      # ID 2, the only fresh one
            s.module.EVIDENCE = [0, 2]
            assert s.evidence_valid([0, 2], fresh=True) is True
            assert s.evidence_current([0, 2]) is False
            answer = s.done()
            if closed_loop:
                assert answer["verdict"] == "unknown"
                assert answer["branch"] == "observe"
                assert answer["branch_source"] == "downgraded"
                assert answer["authored_verdict"] == "true"
            else:
                # Legacy semantics are untouched, and legacy records no branch.
                assert answer["verdict"] == "true"
                assert "branch" not in answer and "origin" not in answer
            s.module.EVIDENCE = [2]
            assert s.done()["verdict"] == "true"


# --- target identity through missing, ambiguous and changed perception -------


def identity_state(tmp_path, name, closed_loop=True):
    session = open_session(tmp_path, name, CLOSED_WORLD, closed_loop=closed_loop)
    return session


def test_identity_requires_a_unique_current_candidate_with_a_stable_name(tmp_path):
    with closed_session(tmp_path, "identity") as session:
        session.invoke("get_observation", lambda: {}, (), {})
        state = session.module.state
        bowl = {"name": "bowl", "xy": [1, 2]}
        mug = {"name": "mug", "xy": [3, 4]}
        match = lambda c: c.get("name") == "bowl"

        assert state.confirm_identity("target", [bowl, mug], match=match,
                                      evidence_ids=[])["status"] == "unknown"
        missing = state.confirm_identity("target", [mug], match=match, evidence_ids=[0])
        assert missing["status"] == "unknown" and "no candidate matched" in missing["reason"]
        ambiguous = state.confirm_identity("target", [bowl, dict(bowl)], match=match,
                                           evidence_ids=[0])
        assert ambiguous["status"] == "unknown" and "ambiguous" in ambiguous["reason"]
        unnamed = state.confirm_identity("target", [{"xy": [1, 2]}], match=lambda c: True,
                                         evidence_ids=[0])
        assert unnamed["status"] == "unknown" and "stable identity" in unnamed["reason"]
        # A nameless detection is never chosen by position out of a mixed set.
        mixed = state.confirm_identity("target", [{"xy": [9, 9]}, bowl], match=match,
                                       evidence_ids=[0])
        assert mixed["status"] == "known" and mixed["identity"] == "bowl"

        first = state.confirm_identity("target", [bowl], match=match, evidence_ids=[0])
        assert first["status"] == "known" and first["identity"] == "bowl"
        changed = state.confirm_identity("target", [mug], match=lambda c: True,
                                         assumed="bowl", evidence_ids=[0])
        assert changed["status"] == "unknown" and "identity changed" in changed["reason"]
        assert changed["identity"] == "bowl"  # the tracked identity is kept, not re-pointed
        assert state.observed_only("target")["status"] == "unknown"


def test_identity_is_invalidated_after_motion_and_restored_by_reobservation(tmp_path):
    with closed_session(tmp_path, "identity") as session:
        session.invoke("get_observation", lambda: {}, (), {})
        state = session.module.state
        bowl = {"name": "bowl", "xy": [1, 2]}
        match = lambda c: c.get("name") == "bowl"
        state.confirm_identity("target", [bowl], match=match, evidence_ids=[0])
        session.invoke("goto_pose", lambda position: None, ([0, 0, 1],), {})
        stale = state.confirm_identity("target", [bowl], match=match, assumed="bowl",
                                       evidence_ids=[0])
        assert stale["status"] == "unknown" and "current observation" in stale["reason"]
        session.invoke("get_observation", lambda: {}, (), {})
        restored = state.confirm_identity("target", [bowl], match=match, assumed="bowl",
                                          evidence_ids=[session.last_observation])
        assert restored["status"] == "known"
        assert state.observed_only("target", identity="mug")["status"] == "unknown"


def test_invalidate_removes_the_observed_fact_and_leaves_the_prediction_alone(tmp_path):
    with closed_session(tmp_path, "identity") as session:
        session.invoke("get_observation", lambda: {}, (), {})
        state = session.module.state
        state.set("target", [1, 2, 3], evidence_ids=[0], identity="bowl")
        state.set("target", [9, 9, 9], layer="predicted")
        fact = state.invalidate("target", "perception returned no detection", identity="bowl")
        assert fact["status"] == "unknown" and fact["layer"] == "observed"
        assert state.observed_only("target")["status"] == "unknown"
        assert state.query("target", layer="predicted")["value"] == [9, 9, 9]


# --- online decisions versus the framework's final shadow judgment -----------


def test_final_shadow_judgment_is_tagged_and_recorded_separately(tmp_path):
    with closed_session(tmp_path, "final") as session:
        measure(session, {"grip": True, "goal": True})
        policy_answer = session.done()
        session.complete(task_completed=True)
    assert policy_answer["origin"] == "policy"
    # The manifest is written when the session closes, so it is read afterwards.
    manifest = json.loads((tmp_path / "final_out/manifest.json").read_text())
    assert manifest["closed_loop"] is True
    rows = manifest["self_evaluations"]
    assert [r["origin"] for r in rows] == ["policy", "framework_final"]
    assert rows[-1]["verdict"] == "true" and "branch" not in rows[-1]


def test_legacy_session_records_no_origin_and_no_branch_keys(tmp_path):
    with open_session(tmp_path, "final_legacy") as session:
        measure(session, {"grip": True, "goal": True})
        assert "origin" not in session.done()
        session.complete(task_completed=True)
    manifest = json.loads((tmp_path / "final_legacy_out/manifest.json").read_text())
    assert manifest["closed_loop"] is False
    assert any("origin" not in row and "branch" not in row
               for row in manifest["self_evaluations"])


CLEAN_POLICY = '''import world
get_observation()
world.update({"values": {"grip": True, "goal": True}, "evidence_ids": [world.evidence_id()]})
answer = world.done()
assert answer["branch"] == "stop", answer
'''

#: Legacy arms have no branch to read, so the shared policy would be a fixture
#: error there. This is the same program minus the revision-specific assertion.
LEGACY_POLICY = '''import world
get_observation()
world.update({"values": {"grip": True, "goal": True}, "evidence_ids": [world.evidence_id()]})
answer = world.done()
assert answer["verdict"] == "true", answer
'''

#: A real policy crash: a nonzero exit after a recorded online decision. The
#: framework must record no final judgment and must not read the absence as one.
CRASHING_POLICY = CLEAN_POLICY + 'raise SystemExit(3)\n'


def offline(tmp_path, name, policy, closed_loop=True):
    code = tmp_path / f"{name}.py"
    code.write_text(policy)
    world = tmp_path / f"{name}_world.py"
    world.write_text(CLOSED_WORLD)
    output = tmp_path / name / "judgment_world"
    report = run_offline(code, world, output, mode="rehearsal", closed_loop=closed_loop)
    return report, output


def test_offline_framework_goal_is_the_final_origin(tmp_path):
    report, output = offline(tmp_path, "clean", CLEAN_POLICY)
    assert report["status"] == "complete"
    assert report["goal"]["origin"] == "framework_final"
    assert report["goal"]["verdict"] == "true" and "branch" not in report["goal"]
    audit = feedback(output.parent)
    assert audit["status"] == "audited"
    assert audit["online_total"] == 1
    assert [d["branch"] for d in audit["online_decisions"]] == ["stop"]
    assert audit["final_shadow_self_evaluation"]["origin"] == "framework_final"
    assert decision_counts(audit["online_decisions"]) == {"stop": 1}
    bundle = {"policy": "a" * 64, "world": "b" * 64}
    grouped = aggregate([{"seed": 51, "directory": str(output), "bundle": bundle,
                          "closed_loop": audit}])
    assert grouped["by_bundle"][0]["counts"] == {"stop": 1}
    assert "development only" in grouped["scope"]


def test_no_final_shadow_judgment_after_a_policy_crash(tmp_path):
    report, output = offline(tmp_path, "crash", CRASHING_POLICY)
    assert report["status"] == "program_error"
    assert report["termination"] == "policy_exit"
    assert "goal" not in report
    audit = feedback(output.parent)
    assert audit["status"] == "audited"
    assert audit["online_total"] == 1
    assert audit["final_shadow_self_evaluation"] is None
    assert audit["online_counts"] == {"stop": 1}


def test_legacy_offline_goal_carries_no_origin(tmp_path):
    report, output = offline(tmp_path, "legacy", LEGACY_POLICY, closed_loop=False)
    assert report["status"] == "complete"
    assert "origin" not in report["goal"]
    assert feedback(output.parent)["status"] == "unavailable"


def test_audit_of_a_non_closed_loop_directory_is_unavailable_not_a_verdict(tmp_path):
    folder = tmp_path / "judgment_world"
    folder.mkdir()
    (folder / "manifest.json").write_text(json.dumps({"closed_loop": False, "self_evaluations": []}))
    report = feedback(tmp_path, write=False)
    assert report["status"] == "unavailable"
    assert "closed-loop revision" in report["detail"]
    assert feedback(tmp_path / "absent", write=False)["status"] == "unavailable"


# --- static screening: supported helper forms are not defects -----------------


def findings(policy, world="def done():\n    return {}\n"):
    return {f["kind"]: f for f in source_findings(world, policy)["findings"]}


def test_ignored_results_are_the_declared_concern():
    report = findings('import world\nworld.done()\nworld.query("a")\n')
    assert report["done_result_ignored"]["severity"] == "concern"
    assert report["query_result_ignored"]["severity"] == "concern"
    assert report["done_result_ignored"]["lines"] == [2]


def test_helper_and_alias_forms_are_supported_not_rejected():
    report = findings('import world\nd = world.done\nif d()["branch"] == "stop":\n    proceed()\n')
    assert "done_result_ignored" not in report and "done_not_called" not in report
    assert report["done_branches"]["severity"] == "supported"
    from_world = findings('from world import done as verdict\nif verdict()["branch"]:\n    pass\n')
    assert from_world["done_branches"]["severity"] == "supported"


def test_inconclusive_analysis_is_never_a_concern():
    helper = findings('import world\ndef ask(w):\n    return w.query("a")\nask(world)\n')
    assert helper["query_branch_unseen"]["severity"] == "inconclusive"
    bound = findings('import world\nanswer = world.done()\nprint(answer)\n')
    assert bound["done_branch_unseen"]["severity"] == "inconclusive"
    unparsed = source_findings("def done(:", "x = 1\n")["findings"]
    assert [f["severity"] for f in unparsed] == ["inconclusive"]
    assert unparsed[0]["kind"] == "unparsed"


def test_predicted_layer_in_goal_is_reported_with_its_line():
    world = ('def done():\n'
             '    state.set("goal", [1, 2], layer="predicted")\n'
             '    return {}\n')
    assert findings('import world\nworld.done()\n', world)["predicted_layer_in_goal"]["severity"] == "concern"
    outside = ('def update(obs):\n'
               '    state.set("hint", [1, 2], layer="predicted")\n')
    assert findings('import world\nworld.done()\n', outside)["predicted_layer_present"]["severity"] == "inconclusive"


# --- up-front refusal of incompatible configurations -------------------------


def fresh_case(**overrides):
    case = {"executable_world_revision": "r1", "foundation_revision": "r1",
            "closed_loop_revision": "r1", "condition": "C", "profile": "judgment",
            "c_arm": "full", "c_lineage": "fresh"}
    case.update(overrides)
    return case


def test_new_revision_is_rejected_up_front_without_being_silently_adapted():
    assert revision_errors(fresh_case()) == []
    assert revision_errors({"condition": "A"}) == []  # unflagged cases add no constraint
    for override, needle in [({"closed_loop_revision": "r2"}, "unknown closed_loop_revision"),
                             ({"executable_world_revision": None}, "executable_world_revision"),
                             ({"foundation_revision": None}, "foundation_revision"),
                             ({"condition": "A"}, "C-only"),
                             ({"profile": "simple"}, "C-only"),
                             ({"c_arm": "no_self_eval"}, "cannot be ablated and required at once")]:
        problems = revision_errors(fresh_case(**override))
        assert any(needle in p for p in problems), (override, problems)
    assert ARMS == frozenset({"full", "no_rehearsal"})
    # The profile's own gate agrees with the revision module, and the prompt
    # keeps the identity example the generated caller must copy.
    with pytest.raises(ValueError):
        profile.validate(fresh_case(c_arm="no_self_eval"))
    text = profile.section(fresh_case())
    assert "assumed=original_identity" in text
    assert "supersedes any instruction" in text
    assert "identity=original_identity" in text
    assert "state.invalidate(" in text
    assert "assumed=None` is first acquisition" in text
    legacy = profile.section({k: v for k, v in fresh_case().items()
                              if k != "closed_loop_revision"})
    assert "Keep self-evaluation observational in this study" in legacy
    assert "assumed=original_identity" not in legacy
