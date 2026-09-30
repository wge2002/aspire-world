"""Unscored deployment probe executed by replay_trial on development seed 51.

Uses the same injected observation, SAM3, GraspNet and IK APIs as model programs.
No reference program, held-out seed or success optimization is involved.
"""
import json
import os
from pathlib import Path

import numpy as np


output = Path(os.environ["ASPIRE_TOOLCHAIN_PROBE_DIR"])
output.mkdir(parents=True, exist_ok=True)
report = {"passed": False, "checks": []}


def checked(name, **values):
    report["checks"].append({"name": name, **values})
    (output / "toolchain.json").write_text(json.dumps(report, indent=2))
    print("TOOLCHAIN_PROBE", name, flush=True)


obs = get_observation()
camera = obs["agentview"]
rgb, depth = camera["images"]["rgb"], camera["images"]["depth"]
assert rgb.shape[:2] == depth.shape[:2] and np.isfinite(depth).all()
checked("observation", rgb_shape=list(rgb.shape), depth_shape=list(depth.shape))
prompt = os.environ["ASPIRE_TOOLCHAIN_PROBE_OBJECT"]
masks = segment_sam3_text_prompt(rgb, prompt)
assert masks, f"SAM3 returned no masks for deployment probe object {prompt!r}"
mask = max(masks, key=lambda m: m["score"])
assert np.asarray(mask["mask"]).any()
checked("segmentation", object_prompt=prompt, mask_count=len(masks))
poses, scores = plan_grasp(depth, camera["intrinsics"], mask["mask"])
assert len(poses) and len(poses) == len(scores) and np.isfinite(poses).all()
checked("grasp_planning", candidates=len(poses))
world_pose = camera["pose_mat"] @ poses[int(np.argmax(scores))]
position, quaternion = decompose_transform(world_pose)
# Solve a pregrasp pose derived from perception; no ground-truth geometry.
position = np.asarray(position) + np.array([0.0, 0.0, 0.15])
joints = solve_ik(position.tolist(), np.asarray(quaternion).tolist())
assert joints is not None and np.asarray(joints).size == 7 and np.isfinite(joints).all()
checked("inverse_kinematics", joints_count=7)
report["passed"] = True
(output / "toolchain.json").write_text(json.dumps(report, indent=2))
print("NATIVE_TOOLCHAIN_PROBE_PASSED", flush=True)
