"""Synthetic world, API and policy for the prediction-contract tests.

Not a physics simulator or robot experiment. The scripted API makes the first
grasp miss and reports no label, so a p1 run yields every check status at
least once; the same files run unchanged with the contract off, which is how
the off-mode goldens in tests/fixtures/prediction_contract/ were recorded
against the code as it was before p1 existed.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

GOLDEN = Path(__file__).resolve().parent / "fixtures/prediction_contract"

WORLD = '''
FOUNDATION_REVISION = "r1"
state = WorldState()
SIM = {"ee_pos": [0.0, 0.0, 0.5], "grasped": False}


def predict(call):
    f = call["function"]
    if f == "goto_pose":
        return {"facts": {"ee_pos": [float(v) for v in call["args"][0]]},
                "tolerance": {"ee_pos": 0.01}}
    if f == "close_gripper":
        return {"facts": {"grasped": True, "label": "bowl", "height": 0.3},
                "tolerance": {"height": 0.05}}
    if f == "open_gripper":
        return "the gripper will open"
    if f == "goto_home_joint_position":
        return {"facts": {"ee_pos": [0.0, 0.0, float("nan")]}}
    raise Unsupported("no model for " + f)


def observe(event):
    if event["kind"] != "measurement" or event["error"]:
        return
    ids = [event["evidence_id"]]
    r = event["result"]
    state.set("ee_pos", [float(v) for v in r["ee_pos"]], evidence_ids=ids)
    state.set("grasped", bool(r["grasped"]), evidence_ids=ids)
    state.set("label", r["label"], evidence_ids=ids, valid=r["label"] is not None)


def update(obs, last_action=None):
    for name, value in obs["values"].items():
        state.set(name, value, evidence_ids=obs["evidence_ids"])
    return snapshot()


def query(name, **kwargs):
    return state.query(name, **kwargs)


def snapshot():
    return state.snapshot()


def simulate(call):
    f = call["function"]
    if f == "get_observation":
        return {"ee_pos": list(SIM["ee_pos"]), "grasped": SIM["grasped"], "label": "bowl"}
    if f == "goto_pose":
        SIM["ee_pos"] = [float(v) for v in call["args"][0]]
        return None
    if f == "close_gripper":
        SIM["grasped"] = True
        return None
    if f == "open_gripper":
        SIM["grasped"] = False
        return None
    raise Unsupported("no rehearsal effect for " + f)


def done():
    return state.all_of([state.predicate("held", lambda g: g is True, ["grasped"])])
'''

POLICY = '''
import world

get_observation()
goto_pose([0.1, 0.0, 0.3], [0.0, 1.0, 0.0, 0.0])
get_observation()
close_gripper()
get_observation()
summary = world.query("prediction_summary")
latest = summary.get("latest_mismatch")
if latest is not None and latest["fact"] == "grasped":
    open_gripper()
    close_gripper()
    get_observation()
    world.update({"evidence_ids": [world.evidence_id()], "values": {"height": 0.31}})
    grasp_checks = world.query("prediction_checks", fact="grasped")
checks = world.query("prediction_checks")
goto_pose([0.2, 0.0, 0.4], [0.0, 1.0, 0.0, 0.0])
'''


class ScriptedAPI:
    """Public-API stand-in: the first grasp misses and the label is never seen."""

    def __init__(self):
        self.ee = [0.0, 0.0, 0.5]
        self.closes = 0
        self.grasped = False
        self.calls = []

    def functions(self):
        return {n: getattr(self, n) for n in (
            "get_observation", "goto_pose", "open_gripper", "close_gripper",
            "goto_home_joint_position")}

    def get_observation(self):
        self.calls.append("get_observation")
        return {"ee_pos": list(self.ee), "grasped": self.grasped, "label": None}

    def goto_pose(self, pos, quat, z_approach=0):
        self.calls.append("goto_pose")
        self.ee = [float(pos[0]), float(pos[1]), float(pos[2]) + 0.005]

    def open_gripper(self):
        self.calls.append("open_gripper")
        self.grasped = False

    def close_gripper(self):
        self.calls.append("close_gripper")
        self.closes += 1
        self.grasped = self.closes > 1

    def goto_home_joint_position(self):
        self.calls.append("goto_home_joint_position")
        self.ee = [0.0, 0.0, 0.5]


def write_world(folder, text=WORLD):
    path = Path(folder) / "world.py"
    path.write_text(text)
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def run_session(tmp, prediction_contract=None, policy=POLICY, world=WORLD, closed_loop=False):
    """One live-binding episode through ExecutableSession.bind_api, as replay_trial does."""
    from aspire.sim.cap.world_model.executable_world import ExecutableSession
    tmp = Path(tmp)
    path, sha = write_world(tmp, world)
    output = tmp / "judgment_world"
    kwargs = {} if prediction_contract is None else {"prediction_contract": prediction_contract}
    api = ScriptedAPI()
    namespace = {}
    with ExecutableSession(path, sha, output, "full", closed_loop=closed_loop, **kwargs) as session:
        session.bind_api(api)
        namespace = dict(api.functions())
        namespace["__name__"] = "__main__"
        # Blocks run stripped, exactly as replay_trial executes them, so recorded
        # caller lines are the block-local lines the world-use audit reads.
        exec(compile(policy.strip(), "policy.py", "exec"), namespace)
        session.complete(task_completed=False)
    return output, namespace, session


def trial_dir(tmp, prediction_contract=None, policy=POLICY):
    """A recorded development trial directory: code.py, frozen config, world output."""
    tmp = Path(tmp)
    code = "# Code block 1\n" + policy.strip() + "\n"
    (tmp / "code.py").write_text(code)
    run_session(tmp, prediction_contract, policy)
    config = {"mode": "opus46-executable-world-c-r1", "c_arm": "full",
              "policy_sha256": hashlib.sha256(code.encode()).hexdigest()}
    if prediction_contract == "p1":
        config["prediction_contract"] = "p1"
    (tmp / "executable_world_config.json").write_text(json.dumps(config, indent=2) + "\n")
    return tmp


def run_rehearsal(tmp, prediction_contract=None, policy=POLICY, world=WORLD):
    from aspire.sim.cap.world_model.executable_world import run_offline
    tmp = Path(tmp)
    path, _ = write_world(tmp, world)
    code = tmp / "code.py"
    code.write_text(policy)
    kwargs = {} if prediction_contract is None else {"prediction_contract": prediction_contract}
    output = tmp / "rehearsal"
    report = run_offline(code, path, output, arm="full", mode="rehearsal", **kwargs)
    return output, report


def _scrub(value, tmp):
    if isinstance(value, str):
        return value.replace(str(tmp), "<TMP>")
    if isinstance(value, list):
        return [_scrub(v, tmp) for v in value]
    if isinstance(value, dict):
        return {k: (0 if k == "elapsed_seconds" else _scrub(v, tmp)) for k, v in value.items()}
    return value


def normalized(output, tmp):
    """Every artifact a session writes, timing and temp paths removed."""
    output, tmp = Path(output), Path(tmp)
    files = {}
    for name in ("events.jsonl", "public_tape.jsonl"):
        rows = [json.loads(line) for line in (output / name).read_text().splitlines() if line.strip()]
        files[name] = "".join(json.dumps(_scrub(r, tmp)) + "\n" for r in rows)
    for name in ("manifest.json", "offline_result.json"):
        if (output / name).exists():
            files[name] = json.dumps(_scrub(json.loads((output / name).read_text()), tmp), indent=2) + "\n"
    return files


PROMPT_CASES = {
    "gate_oracle_fresh": {"executable_world_revision": "r1", "foundation_revision": "r1",
                          "condition": "C", "profile": "judgment", "c_arm": "full",
                          "c_lineage": "fresh", "development_gate": "oracle", "task": "t",
                          "world_interface_doc": "docs/experiments/code-world-gate-ablation-20261005/NATIVE_WORLD_INTERFACE.md"},
    "closed_loop_fresh": {"executable_world_revision": "r1", "foundation_revision": "r1",
                          "closed_loop_revision": "r1", "condition": "C", "profile": "judgment",
                          "c_arm": "full", "c_lineage": "fresh"},
    "sealed_self_eval": {"executable_world_revision": "r1", "foundation_revision": "r1",
                         "condition": "C", "profile": "judgment", "c_arm": "full",
                         "c_lineage": "fresh", "development_gate": "self_eval"},
    "repair_no_rehearsal": {"executable_world_revision": "r1", "condition": "C",
                            "profile": "judgment", "c_arm": "no_rehearsal",
                            "c_starter": "prior/c"},
}


def capture(root):
    """Record the off-mode goldens. Run once, against the pre-p1 code."""
    import tempfile
    import executable_world_profile as profile
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    for kind, runner in (("session", run_session), ("rehearsal", run_rehearsal)):
        with tempfile.TemporaryDirectory() as tmp:
            output = runner(tmp)[0]
            for name, text in normalized(output, tmp).items():
                (root / f"off_{kind}_{name}").write_text(text)
    from aspire.sim.cap.world_model import world_use_audit
    with tempfile.TemporaryDirectory() as tmp:
        feedback = world_use_audit.trial_feedback(trial_dir(tmp))
        (root / "off_audit_feedback.json").write_text(json.dumps(_scrub(feedback, tmp), indent=2) + "\n")
        report = json.loads((Path(tmp) / world_use_audit.ARTIFACT).read_text())
        (root / "off_audit_report.json").write_text(json.dumps(_scrub(report, tmp), indent=2) + "\n")
    prompts = {k: hashlib.sha256(profile.section(c).encode()).hexdigest() for k, c in PROMPT_CASES.items()}
    (root / "off_prompt_sha256.json").write_text(json.dumps(prompts, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    import sys
    sim = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(sim.parents[1]))
    sys.path.insert(0, str(sim / "scripts/libero"))
    sys.path.insert(0, str(sim / "scripts/common"))
    capture(GOLDEN)
