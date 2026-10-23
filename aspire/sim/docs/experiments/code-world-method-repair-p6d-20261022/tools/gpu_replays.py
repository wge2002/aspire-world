#!/usr/bin/env python3
"""CC_TASK_14 GPU replays on one free card of this host (worktree runtime, no result root touched).

  capture   seed-51 observation of a cell's task (real LIBERO reset; services unused):
            agentview depth + intrinsics + rgb, and a depth-based object mask (no SAM3)
  graspnet  the real Contact-GraspNet server (worktree script, main-checkout vendor tree and
            checkpoint, as the unit supervisor starts it) once with seeding off and once with
            API revision m4-r1; the same request A is sent three times with a different request
            B in between (A, B, A, B, A) and the returned grasps are hashed
  budget    a real LIBERO replay whose program never stops acting until the 4000-step budget
            is used up, once under the legacy rule and once under success_rule libero_latch

  gpu_replays.py --gpu G --out DIR [--steps capture,graspnet,budget]
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import re
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

SIM = Path(__file__).resolve().parents[4]
ROOT = SIM.parents[1]
GRASPNET_ROOT = Path("/mnt/home/gewang/code/ASPIRE/aspire/sim/cap/third_party/contact_graspnet_pytorch")
GRASPNET_CHECKPOINT = GRASPNET_ROOT / "checkpoints/contact_graspnet/checkpoints"
SUITE, TASK = "libero_object_task", "pick_up_the_ketchup_and_place_it_in_the_basket"
STRIP = re.compile(r"API_KEY|AUTH_TOKEN|SECRET|ACCESS_KEY|CREDENTIAL|SESSION_TOKEN|^HF_TOKEN$"
                   r"|HUGGING_FACE_HUB_TOKEN|ANTHROPIC")
PY = str(SIM / ".venv-libero/bin/python3")

CAPTURE = '''
import numpy as np
obs = get_observation()
cam = obs["agentview"]
depth = np.asarray(cam["images"]["depth"], dtype=np.float32)
band = depth[depth.shape[0] // 4: 3 * depth.shape[0] // 4, depth.shape[1] // 4: 3 * depth.shape[1] // 4]
table = float(np.median(band))
mask = np.zeros(depth.shape, dtype=np.int32)
rows, cols = np.indices(depth.shape)
centre = (rows > depth.shape[0] // 4) & (rows < 3 * depth.shape[0] // 4) & \\
         (cols > depth.shape[1] // 4) & (cols < 3 * depth.shape[1] // 4)
mask[centre & (depth < table - 0.03) & (depth > 0.1)] = 1
np.savez_compressed(OUT, depth=depth, K=np.asarray(cam["intrinsics"], dtype=np.float64),
                    rgb=np.asarray(cam["images"]["rgb"]), segmap=mask, table_depth=table)
print("CAPTURED", int(mask.sum()), "object pixels; table depth", round(table, 4))
'''

BUDGET = '''
cycles = 0
while True:
    close_gripper()
    open_gripper()
    cycles += 1
    if cycles % 20 == 0:
        print("cycles", cycles)
'''


#: The NVIDIA EGL vendor file every cell case names (egl_vendor_config); without it glvnd
#: falls back to mesa, which enumerates one device and rejects a physical card id.
EGL_VENDOR = Path(__file__).resolve().parent / "nvidia-egl-vendor.json"


def base_env(gpu, **extra):
    if not EGL_VENDOR.is_file():
        EGL_VENDOR.write_text(json.dumps({"file_format_version": "1.0.0",
                                          "ICD": {"library_path": "libEGL_nvidia.so.0"}}, indent=2) + "\n")
    env = {k: v for k, v in os.environ.items() if not STRIP.search(k)}
    for key in ("RANK", "WORLD_SIZE", "MASTER_ADDR", "MASTER_PORT", "LOCAL_RANK", "ASPIRE_SAM3_PROMPTS",
                "ASPIRE_API_REVISION", "ASPIRE_SUCCESS_RULE"):
        env.pop(key, None)
    env.update(ASPIRE_ROOT=str(SIM), PYTHON_ROOT=str(ROOT), PYTHONPATH=str(ROOT), MUJOCO_GL="egl",
               CUDA_VISIBLE_DEVICES=str(gpu), MUJOCO_EGL_DEVICE_ID=str(gpu),
               __EGL_VENDOR_LIBRARY_FILENAMES=str(EGL_VENDOR),
               TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD="1", SAM3_SERVICE_URL="http://127.0.0.1:1",
               GRASPNET_SERVICE_URL="http://127.0.0.1:1", PYROKI_SERVICE_URL="http://127.0.0.1:1", **extra)
    return env


def replay(out, code, gpu, timeout, **extra):
    out.mkdir(parents=True, exist_ok=False)
    (out / "code.py").write_text(code)
    command = [PY, "scripts/libero/replay_trial.py", "--args.suite", SUITE, "--args.task", TASK,
               "--args.trial", "51", "--args.model", "qwen3.8-flash-next",
               "--args.config", "env_configs/libero/franka_libero_traced.yaml",
               "--args.output-dir", str(out / "results"), "--args.replay-code", str(out / "code.py")]
    started = time.time()
    with open(out / "replay.log", "w") as log:
        proc = subprocess.run(command, cwd=SIM, env=base_env(gpu, SNAPSHOT_DIR=str(out), **extra),
                              stdout=log, stderr=subprocess.STDOUT, timeout=timeout)
    trials = sorted(p.name for p in (out / "results").rglob("trial_*") if p.is_dir())
    return {"exit_code": proc.returncode, "wall_seconds": round(time.time() - started, 1),
            "trial_dirs": trials, "log": str(out / "replay.log"), "command": command}


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def npy(array):
    import numpy as np
    buffer = io.BytesIO()
    np.save(buffer, array)
    return base64.b64encode(buffer.getvalue()).decode()


def post(port, body, timeout=300):
    request = urllib.request.Request(f"http://127.0.0.1:{port}/plan", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read())
    return {k: hashlib.sha256(base64.b64decode(v)).hexdigest()[:16] for k, v in payload.items()}


def graspnet_run(out, gpu, capture, revision):
    import numpy as np
    data = np.load(capture)
    port = free_port()
    env = base_env(gpu, CONTACT_GRASPNET_ROOT=str(GRASPNET_ROOT),
                   CONTACT_GRASPNET_CHECKPOINT_DIR=str(GRASPNET_CHECKPOINT),
                   **({"ASPIRE_API_REVISION": revision} if revision else {}))
    log = out / f"graspnet-{revision or 'off'}.log"
    with open(log, "w") as handle:
        server = subprocess.Popen([PY, "-u", "cap/serving/launch_contact_graspnet_server.py", "--host",
                                   "127.0.0.1", "--port", str(port)], cwd=SIM, env=env, stdout=handle,
                                  stderr=subprocess.STDOUT, start_new_session=True)
    try:
        deadline = time.time() + 600
        while True:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/docs", timeout=5)
                break
            except (urllib.error.URLError, ConnectionError, OSError):
                if server.poll() is not None or time.time() > deadline:
                    raise RuntimeError(f"GraspNet server did not come up; see {log}")
                time.sleep(3)
        a = {"depth_base64": npy(data["depth"]), "cam_K_base64": npy(data["K"]),
             "segmap_base64": npy(data["segmap"]), "segmap_id": 1}
        shifted = data["depth"].copy()
        shifted[data["segmap"] == 1] -= 0.005  # request B: the same scene, object 5 mm closer
        b = dict(a, depth_base64=npy(shifted))
        sequence = [("A", post(port, a)), ("B", post(port, b)), ("A", post(port, a)), ("B", post(port, b)),
                    ("A", post(port, a))]
    finally:
        os.killpg(server.pid, 15)
        server.wait(timeout=60)
    grasps_a = [r["grasps_base64"] for name, r in sequence if name == "A"]
    seeding = [line for line in log.read_text().splitlines() if "request seeding" in line]
    return {"revision": revision, "sequence": sequence, "A_identical": len(set(grasps_a)) == 1,
            "A_distinct_results": len(set(grasps_a)), "seeding_log_line": seeding, "log": str(log)}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--gpu", type=int, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--steps", default="capture,graspnet,budget")
    args = parser.parse_args()
    steps = args.steps.split(",")
    args.out.mkdir(parents=True, exist_ok=True)
    report_path = args.out / "gpu-replays.json"
    report = json.loads(report_path.read_text()) if report_path.is_file() else {}
    report.update(gpu=args.gpu, host=socket.gethostname(), worktree=str(SIM))
    capture = args.out / "capture/observation.npz"
    if "capture" in steps:
        report["capture"] = replay(args.out / "capture", CAPTURE.replace("OUT", repr(str(capture))), args.gpu, 900)
        report["capture"]["saved"] = capture.is_file()
    if "graspnet" in steps:
        report["graspnet"] = {"off": graspnet_run(args.out, args.gpu, capture, None),
                              "m4-r1": graspnet_run(args.out, args.gpu, capture, "m4-r1")}
    if "budget" in steps:
        report["budget"] = {rule or "legacy": replay(args.out / f"budget-{rule or 'legacy'}", BUDGET, args.gpu, 1800,
                                                     **({"ASPIRE_SUCCESS_RULE": rule} if rule else {}))
                            for rule in (None, "libero_latch")}
        for name, row in report["budget"].items():
            text = Path(row["log"]).read_text(errors="replace")
            row["budget_line"] = "[episode] step budget exhausted" in text
            row["terminated_traceback"] = "Traceback" in text and "terminated episode" in text
    report["expectations"] = expectations(report)
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0 if all(report["expectations"].values()) else 1


def expectations(report):
    """What each replay present in the report must show (the host gate needs a non-zero exit otherwise)."""
    checks = {}
    if "capture" in report:
        checks["capture_saved"] = bool(report["capture"].get("saved"))
    if "graspnet" in report:
        checks["graspnet_m4_same_request_same_grasps"] = report["graspnet"]["m4-r1"]["A_identical"]
        checks["graspnet_m4_seeding_logged"] = bool(report["graspnet"]["m4-r1"]["seeding_log_line"])
    if "budget" in report:
        legacy, latch = report["budget"]["legacy"], report["budget"]["libero_latch"]
        checks["budget_legacy_is_program_error"] = (legacy["terminated_traceback"]
                                                    and any("_sandboxrc_1_" in t for t in legacy["trial_dirs"]))
        checks["budget_latch_is_not_program_error"] = (latch["budget_line"] and not latch["terminated_traceback"]
                                                       and any("_sandboxrc_0_" in t for t in latch["trial_dirs"]))
    return checks


if __name__ == "__main__":
    raise SystemExit(main())
