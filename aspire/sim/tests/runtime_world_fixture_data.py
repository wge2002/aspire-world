"""CPU fake-public-API engineering fixture, not perception or robot results.

The same policy uses synthetic point-cloud evidence for a tracked marker. Three
controlled return modes yield a match, mismatch or missing measurement, and
the policy itself selects three different following public calls.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np

WORLD = '''
STATE_TEMPLATE_REVISION = "p2-runtime"
FOUNDATION_REVISION = "r1"
state = WorldState()
SOURCE = {"api": "mask_to_world_points", "method": "synthetic marker centroid"}

def predict(call):
    if call["function"] == "goto_pose":
        return {"predictions": [{"entity": "marker", "fact": "position",
                 "kind": "geometry", "frame": "world", "unit": "m",
                 "expected": [float(v) for v in call["args"][0]],
                 "tolerance": 0.01, "target": {"kind": "next_observation"},
                 "valid_for_calls": 6}]}
    raise Unsupported("no synthetic dynamics for " + call["function"])

def observe(event):
    # Raw images/robot returns may be processed by the perception adapter;
    # this fixture uses public mask/point-cloud results via update(). Motor
    # receipts never populate observed state.
    pass

def update(obs, last_action=None):
    ids = obs["evidence_ids"]
    state.entity("marker", label="synthetic tracked marker", evidence_ids=ids,
                 source={"api": "segment_sam3_text_prompt", "method": "unique synthetic marker"},
                 uncertainty={"status": "estimated", "reason": "controlled engineering association"})
    points = obs["values"]["points"]
    if not points:
        return state.invalidate_fact("marker", "position", kind="geometry",
                 frame="world", unit="m", evidence_ids=ids, source=SOURCE,
                 reason="synthetic perception returned no marker points")
    value = [sum(p[i] for p in points) / len(points) for i in range(3)]
    return state.set_fact("marker", "position", value, kind="geometry",
                 frame="world", unit="m", evidence_ids=ids, source=SOURCE,
                 uncertainty={"status": "estimated", "reason": "synthetic centroid"})

def query(name, **kwargs):
    return state.query(name, **kwargs)

def snapshot():
    return state.snapshot()

def simulate(call):
    raise Unsupported("fixture does not pretend to simulate real perception")

def done():
    return {"verdict": "unknown", "evidence_ids": [], "reason": "engineering fixture has no task goal"}
'''

POLICY = '''
import world

def perceive():
    obs = get_observation()
    camera = obs["agentview"]
    masks = segment_sam3_text_prompt(camera["images"]["rgb"], "synthetic tracked marker")
    points = mask_to_world_points(masks[0]["mask"], camera["images"]["depth"], camera["intrinsics"], camera["pose_mat"])
    world.update({"evidence_ids": [world.evidence_id()], "values": {"points": points.tolist()}})

perceive()
initial = world.query("state", entity="marker", fact="position", frame="world", unit="m")
target = [initial["value"][0] + 0.1, initial["value"][1], initial["value"][2]]
goto_pose(target, [0.0, 1.0, 0.0, 0.0])
perceive()
measured = world.query("state", entity="marker", fact="position", frame="world", unit="m")
feedback = world.query("prediction_feedback", entity="marker", fact="position")
status = feedback["results"][0]["status"]
if measured["status"] == "unknown" or status == "unknown":
    world.record_decision("reobserve", feedback=feedback, facts=[measured], reason="marker measurement is missing")
    get_observation()
elif status == "mismatch":
    corrected = [measured["value"][0] + 0.02, measured["value"][1], measured["value"][2]]
    world.record_decision("revise", feedback=feedback, facts=[measured], reason="measured marker disagrees with expected motion")
    goto_pose(corrected, [0.0, 1.0, 0.0, 0.0])
else:
    world.record_decision("continue", feedback=feedback, facts=[measured], reason="measured marker agrees with expected motion")
    goto_home_joint_position()
'''


class FakePublicAPI:
    def __init__(self, mode="match"):
        if mode not in {"match", "mismatch", "unknown"}:
            raise ValueError(mode)
        self.mode = mode
        self.position = [0.0, 0.0, 0.2]
        self.moved = False
        self.calls = []

    def functions(self):
        return {n: getattr(self, n) for n in (
            "get_observation", "segment_sam3_text_prompt", "mask_to_world_points",
            "goto_pose", "goto_home_joint_position")}

    def get_observation(self):
        self.calls.append({"function": "get_observation"})
        return {"agentview": {"images": {"rgb": np.zeros((2, 2, 3), dtype=np.uint8),
                                          "depth": np.ones((2, 2))},
                              "intrinsics": np.eye(3), "pose_mat": np.eye(4)}}

    def segment_sam3_text_prompt(self, rgb, text):
        self.calls.append({"function": "segment_sam3_text_prompt"})
        return [{"mask": np.ones((2, 2), dtype=bool)}]

    def mask_to_world_points(self, mask, depth, intrinsics, extrinsics):
        self.calls.append({"function": "mask_to_world_points"})
        if self.moved and self.mode == "unknown":
            return np.empty((0, 3))
        return np.asarray([self.position], dtype=float)

    def goto_pose(self, position, quaternion):
        self.calls.append({"function": "goto_pose", "position": list(position)})
        self.position = list(position)
        if not self.moved and self.mode == "mismatch":
            self.position[0] -= 0.04
        self.moved = True

    def goto_home_joint_position(self):
        self.calls.append({"function": "goto_home_joint_position"})


def run(folder, mode="match", *, policy=POLICY, world=WORLD, contract="p2-runtime"):
    from aspire.sim.cap.world_model.executable_world import ExecutableSession
    from aspire.sim.cap.world_model.world_use_audit import trial_feedback
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    code = "# Code block 1\n" + policy.strip() + "\n"
    code_path, world_path = folder / "code.py", folder / "world_program.py"
    code_path.write_text(code)
    world_path.write_text(world)
    sha = hashlib.sha256(world_path.read_bytes()).hexdigest()
    api = FakePublicAPI(mode)
    with ExecutableSession(world_path, sha, folder / "judgment_world", "no_self_eval",
                           prediction_contract=contract) as session:
        session.bind_api(api)
        namespace = {**api.functions(), "__name__": "__main__"}
        exec(compile(policy.strip(), str(code_path), "exec"), namespace)
        session.complete(engineering_fixture=True)
    config = {"prediction_contract": contract, "policy_sha256": hashlib.sha256(code.encode()).hexdigest(),
              "world_program_sha256": sha, "c_arm": "no_self_eval"}
    (folder / "executable_world_config.json").write_text(json.dumps(config, indent=2) + "\n")
    feedback = trial_feedback(folder)
    return {"mode": mode, "folder": str(folder), "calls": api.calls,
            "feedback": feedback, "manifest": json.loads((folder / "judgment_world/manifest.json").read_text())}, namespace, session
