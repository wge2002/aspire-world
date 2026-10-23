#!/usr/bin/env python3
"""CC_TASK_14 environment check (ns env-check.py, root as an argument): one seed-51 reset per staged cell.

For each cell, run the cell's own frozen runtime wrapper (`scripts/libero/runtime/replay_trial.py`)
with the frozen observation-only `scripts/libero/scene_snapshot.py` on seed 51, with the case's
suite, task and env config and the case's own method-switch environment (`runtime_env`'s
ASPIRE_API_REVISION / ASPIRE_SUCCESS_RULE). No service is started (dead service URLs). Output:
`<root>/preflight/env-check/<host>/<cell>/`; never overwrites.

Proves: the bddl / init states load, both camera images render, the runtime task language,
privileged False, the policy API constructs under API revision m4-r1, and the simulator
module carries the success latch.

  env-check.py --root <study root> --gpu G [--cells a,b]
"""
from __future__ import annotations

import argparse
import json
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

STRIP = re.compile(r"API_KEY|AUTH_TOKEN|SECRET|ACCESS_KEY|CREDENTIAL|SESSION_TOKEN|^HF_TOKEN$"
                   r"|HUGGING_FACE_HUB_TOKEN|ANTHROPIC")


def runtime_env(case):
    """The cell's frozen `runtime_env`, computed by the cell's own interpreter."""
    sim = Path(case["sim"])
    code = ("import json, sys; sys.path[:0] = [sys.argv[1] + '/scripts/libero', sys.argv[1] + '/scripts/common']; "
            "import native_world_protocol as p; case = json.load(open(sys.argv[2])); "
            "from pathlib import Path; print(json.dumps(p.runtime_env(case, Path(sys.argv[1]))))")
    out = subprocess.run([str(sim / ".venv-libero/bin/python3"), "-c", code, str(sim), case["_path"]],
                         cwd=sim, capture_output=True, text=True, check=True)
    return json.loads(out.stdout.strip().splitlines()[-1])


def run_one(case, gpu, out):
    sim = Path(case["sim"])
    if out.exists():
        raise SystemExit(f"{out} exists; env-check never overwrites")
    out.mkdir(parents=True)
    env = {k: v for k, v in runtime_env(case).items() if not STRIP.search(k)}
    for key in ("RANK", "WORLD_SIZE", "MASTER_ADDR", "MASTER_PORT", "LOCAL_RANK"):
        env.pop(key, None)
    env.update(CUDA_VISIBLE_DEVICES=str(gpu), MUJOCO_EGL_DEVICE_ID=str(gpu), SNAPSHOT_DIR=str(out),
               ASPIRE_RUNTIME_EVIDENCE_DIR=str(out), SAM3_SERVICE_URL="http://127.0.0.1:1",
               GRASPNET_SERVICE_URL="http://127.0.0.1:1", PYROKI_SERVICE_URL="http://127.0.0.1:1")
    command = [str(sim / ".venv-libero/bin/python3"), "scripts/libero/runtime/replay_trial.py",
               "--args.suite", case["suite"], "--args.task", case["task"], "--args.trial", "51",
               "--args.model", case["model"], "--args.config", case["env_config"],
               "--args.output-dir", str(out / "results"),
               "--args.replay-code", str(sim / "scripts/libero/scene_snapshot.py")]
    started = time.time()
    with open(out / "replay.log", "w") as log:
        proc = subprocess.run(command, cwd=sim, env=env, stdout=log, stderr=subprocess.STDOUT,
                              timeout=case["trial_timeout"])
    text = (out / "replay.log").read_text(errors="replace")
    language = re.search(r"^TASK_LANGUAGE: (.*)$", text, re.M)
    init = re.search(r"^Loaded init states for task (\d+) in suite (\S+)", text, re.M)
    images = {n: (out / n).is_file() and (out / n).stat().st_size > 0
              for n in ("scene_snapshot.jpg", "scene_snapshot_wrist.jpg")}
    proof_path = out / "resolved-environment.json"
    proof = json.loads(proof_path.read_text()) if proof_path.is_file() else None
    latch = "EpisodeBudgetExhausted" in (sim / "cap/envs/simulators/libero.py").read_text()
    row = {"cell": case["id"], "suite": case["suite"], "task": case["task"], "seed": 51, "gpu": gpu,
           "exit_code": proc.returncode, "wall_seconds": round(time.time() - started, 1),
           "init_states_loaded": bool(init), "task_language": language.group(1) if language else None,
           "images": {n: str(out / n) for n, ok in images.items() if ok},
           "constructed_apis": (proof or {}).get("constructed_apis"),
           "api_revision_env": env.get("ASPIRE_API_REVISION"), "success_rule_env": env.get("ASPIRE_SUCCESS_RULE"),
           "simulator_has_latch": latch, "command": command, "log": str(out / "replay.log")}
    row["ok"] = bool(proc.returncode == 0 and init and language and all(images.values()) and proof
                     and proof.get("privileged") is False and latch
                     and row["api_revision_env"] == "m4-r1" and row["success_rule_env"] == "libero_latch")
    (out / "env-check.json").write_text(json.dumps(row, indent=2) + "\n")
    return row


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--gpu", type=int, required=True)
    parser.add_argument("--cells", default="")
    args = parser.parse_args(argv)
    receipt = json.loads((args.root / "prepare-receipt.json").read_text())
    cells = [c for c in args.cells.split(",") if c] or receipt["serial_order"]
    host = socket.gethostname()
    base = args.root / "preflight/env-check" / host
    rows = {}
    for cell in cells:
        path = args.root / cell / "case.json"
        case = {**json.loads(path.read_text()), "_path": str(path)}
        rows[cell] = run_one(case, args.gpu, base / cell)
        print(json.dumps({cell: {k: rows[cell][k] for k in ("ok", "task_language", "wall_seconds")}}), flush=True)
    summary = {"checked_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "host": host, "gpu": args.gpu,
               "cells": rows, "all_ok": all(r["ok"] for r in rows.values())}
    base.mkdir(parents=True, exist_ok=True)
    (base / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({"all_ok": summary["all_ok"], "summary": str(base / "summary.json")}))
    return 0 if summary["all_ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
