"""Behavioral regressions for observation state and development-only auditing."""
import hashlib
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from aspire.sim.cap.world_model.evidence_state import EvidenceState, conjunction
from aspire.sim.cap.world_model.executable_world import ExecutableSession
from aspire.sim.cap.world_model.foundation_audit import trial_feedback, aggregate
from aspire.sim.cap.world_model.world_use_audit import audit, read_trace


def store():
    context = {"ids": {0, 2}, "last_motion": -1}
    def valid(ids, fresh=False):
        return bool(ids) and all(type(i) is int and i in context["ids"] for i in ids) and (not fresh or any(i > context["last_motion"] for i in ids))
    return EvidenceState(valid), context


def test_same_store_overwrite_unknown_identity_and_copy():
    state, _ = store()
    state.set("position", [1, 2, 3], evidence_ids=[0], identity="a")
    answer = state.query("position", identity="a")
    answer["value"][0] = 9
    assert state.snapshot()["layers"]["observed"]["position"]["value"] == [1, 2, 3]
    assert state.query("position", identity="b")["status"] == "unknown"
    state.set("position", [4, 5, 6], evidence_ids=[2], identity="a")
    assert state.query("position")["value"] == [4, 5, 6]
    state.set("position", None, evidence_ids=[2], reason="occluded", identity="a")
    assert state.query("position")["value"] is None
    state.set("visible", False, evidence_ids=[2])
    assert state.query("visible")["status"] == "known"
    state.set("position", float("nan"), evidence_ids=[2])
    assert state.query("position")["status"] == "unknown"
    with pytest.raises(ValueError):
        state.set("position", 2, evidence_ids=[1])  # command, not a measurement


def test_individual_freshness_historical_reference_and_layers():
    state, context = store()
    state.set("origin", 1, evidence_ids=[0])
    state.set("current", 3, evidence_ids=[2])
    context["last_motion"] = 1
    assert state.query("origin")["status"] == "unknown"
    assert state.snapshot()["layers"]["observed"]["origin"]["status"] == "unknown"
    clause = state.predicate("moved", lambda current, old: current > old, ["current"], references=["origin"])
    assert clause["verdict"] == "true"
    assert clause["evidence_ids"] == [2] and clause["reference_evidence_ids"] == [0]
    state.set("current", 900, layer="predicted")
    assert state.query("current")["value"] == 3
    assert state.query("current", layer="predicted")["value"] == 900
    context["last_motion"] = 3
    assert state.predicate("moved", lambda *v: True, ["current"])["verdict"] == "unknown"


def test_three_valued_goal():
    c = lambda v: {"name": "c", "verdict": v, "evidence_ids": []}
    assert conjunction([])["verdict"] == "unknown"
    assert conjunction([c("true"), c("unknown")])["verdict"] == "unknown"
    assert conjunction([c("false"), c("unknown")])["verdict"] == "false"
    assert conjunction([c("true"), c("true")])["verdict"] == "true"


WORLD = '''
FOUNDATION_REVISION = "r1"
state = WorldState()
def update(obs, last_action=None):
    for key, value in obs["values"].items():
        state.set(key, value, evidence_ids=obs["evidence_ids"])
def query(name, **kwargs): return state.query(name, **kwargs)
def snapshot(): return state.snapshot()
def predict(call): raise Unsupported("no learned predictor")
def observe(event): pass
def simulate(call): raise Unsupported("no modeled effect")
def done():
    return state.all_of([state.predicate(k, lambda v: bool(v), [k]) for k in ("a", "b")])
'''


def test_runtime_clause_freshness_and_executed_query_use(tmp_path):
    world = tmp_path / "world.py"
    world.write_text(WORLD)
    with ExecutableSession(world, hashlib.sha256(world.read_bytes()).hexdigest(), tmp_path / "out") as session:
        session.invoke("get_observation", lambda: {}, (), {})
        session.module.update({"values": {"a": True, "b": True}, "evidence_ids": [0]})
        assert session.done()["verdict"] == "true"
        session.invoke("goto_pose", lambda: None, (), {})
        session.invoke("get_observation", lambda: {}, (), {})
        session.module.update({"values": {"a": True}, "evidence_ids": [2]})
        assert session.done()["verdict"] == "unknown"  # fresh a cannot rescue b
        session.module.update({"values": {"a": False}, "evidence_ids": [2]})
        assert session.done()["verdict"] == "false"  # reliable false is sufficient
        code = 'import world\nq=world.query("a")\nsolve_ik(q["value"], [0,0,0,1])\n'
        exec(compile(code, "code.py", "exec"), {"solve_ik": lambda *a: None})
    trace = read_trace(tmp_path / "out/events.jsonl")
    assert audit(code, trace=trace)["status"] == "supported_use_candidate"
    assert audit('import world\nprint(world.query("a"))\n', trace=trace)["status"] == "logging_only"


def test_calibration_final_identity_and_development_boundary(tmp_path):
    p, w = tmp_path / "code.py", tmp_path / "world_program.py"
    p.write_text("pass\n")
    w.write_text(WORLD)
    ph, wh = [hashlib.sha256(x.read_bytes()).hexdigest() for x in (p, w)]
    (tmp_path / "executable_world_config.json").write_text(json.dumps({"policy_sha256": ph, "world_program_sha256": wh}))
    folder = tmp_path / "judgment_world"
    folder.mkdir()
    (folder / "manifest.json").write_text(json.dumps({"world_sha256": wh, "self_evaluations": [
        {"verdict": "false", "binding": "shadow"}, {"verdict": "true", "binding": "shadow"}]}))
    row = {"seed": 51, "phase": "initial", "status": "complete", "sandbox_rc": 0, "task_completed": 0, "directory": "one"}
    report = trial_feedback(tmp_path, row)
    assert report["category"] == "false_positive"
    row["foundation_calibration"] = report
    assert aggregate([row])["by_bundle"][0]["counts"] == {"false_positive": 1}
    with pytest.raises(ValueError):
        trial_feedback(tmp_path, {**row, "seed": 1})
    w.write_text("changed")
    assert trial_feedback(tmp_path, row)["status"] == "unavailable"


def test_diagnostic_import_idempotent_and_never_candidate(tmp_path):
    sim = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(sim / "scripts/libero"))
    sys.path.insert(0, str(sim / "docs/experiments/code-world-qwen-foundation-20260930/support"))
    from native_world_fixloop_state import NativeWorldState
    from foundation_import import import_diagnostic
    root = tmp_path / "coordination/preflight"
    root.mkdir(parents=True)
    data = {"summary.json": {"state": "passed", "cell": "toy_C", "task": "toy", "seed": 51,
                            "charged": True, "task_policy_executed": False, "exit_code": 0},
            "resolved-environment.json": {"privileged": False, "constructed_apis": {"FrankaLiberoApiReducedSkillLibraryTraced": "actual"}},
            "bundle/code.py": "pass", "bundle/world_program.py": "diagnostic"}
    for name, value in data.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value) if isinstance(value, dict) else value)
    case = {"id": "toy_C", "task": "toy", "control": str(tmp_path / "toy_C"),
            "diagnostic_import": {"directory": str(root), "hashes": {
                name: hashlib.sha256((root/name).read_bytes()).hexdigest() for name in data}}}
    task = tmp_path / "ledger"
    identity = {"dev_seeds": list(range(51, 66))}
    NativeWorldState(task, identity)
    first = import_diagnostic(case, task)
    second = import_diagnostic(case, task)
    assert first == second and first["remaining_real_budget"]["51"] == 2
    state = NativeWorldState(task, identity, resume=True)
    assert state.attempts_used(51) == 1 and not state.candidates()
    (root / "bundle/code.py").write_text("tampered")
    with pytest.raises(ValueError, match="evidence changed"):
        import_diagnostic(case, task)
