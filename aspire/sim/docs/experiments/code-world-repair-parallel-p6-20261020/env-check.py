#!/usr/bin/env python3
"""CC_TASK_6 p5 environment check (copy of p4n env-check.py, PARENT re-pointed): one seed-51 reset per staged cell, no services.

For each cell, run the cell's own frozen runtime wrapper
(`scripts/libero/runtime/replay_trial.py`) with the frozen observation-only policy
`scripts/libero/scene_snapshot.py` on seed 51 — the same command the protocol's
snapshot phase builds, with the case's own suite, task and env config. Output goes
to `<study>/env-check/<cell>/` (scratch, outside every cell's result root, so the
staged cells stay fresh). SAM3/GraspNet/PyRoKi are not started; the snapshot policy
calls none of them (ASPIRE_SAM3_PROMPTS unset).

Proves: the bddl and init-state files load (`Loaded init states for task N`), both
camera images render, and the runtime `env.handle.task_language`. It also records
the reset under the libero10 traced config for the libero_10 cells and compares
the resolved low-level config and constructed prompt to the default config's.

  env-check.py --gpu 2 [--cells a,b] [--also-libero10-config]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
PARENT = Path("/mnt/home/gewang/experiments/code-world-repair-parallel-p6-20261020")
OUT = PARENT / "preflight/env-check"
LIBERO10 = "env_configs/libero/franka_libero_libero10_traced.yaml"
STRIP = re.compile(r"API_KEY|AUTH_TOKEN|SECRET|ACCESS_KEY|CREDENTIAL|SESSION_TOKEN|^HF_TOKEN$"
                   r"|HUGGING_FACE_HUB_TOKEN|ANTHROPIC")


def run_one(case: dict, gpu: int, config: str, tag: str) -> dict:
    sim = Path(case["sim"])
    out = OUT / case["id"] / tag
    if out.exists():
        raise SystemExit(f"{out} exists; env-check never overwrites")
    out.mkdir(parents=True)
    env = {k: v for k, v in os.environ.items() if not STRIP.search(k)}
    env.pop("ASPIRE_SAM3_PROMPTS", None)
    for key in ("RANK", "WORLD_SIZE", "MASTER_ADDR", "MASTER_PORT", "LOCAL_RANK"):
        env.pop(key, None)
    env.update(ASPIRE_ROOT=str(sim), PYTHON_ROOT=str(sim.parents[1]), PYTHONPATH=str(sim.parents[1]),
               MUJOCO_GL="egl", CUDA_VISIBLE_DEVICES=str(gpu), MUJOCO_EGL_DEVICE_ID=str(gpu),
               TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD="1", SNAPSHOT_DIR=str(out),
               ASPIRE_RUNTIME_EVIDENCE_DIR=str(out),
               SAM3_SERVICE_URL="http://127.0.0.1:1", GRASPNET_SERVICE_URL="http://127.0.0.1:1",
               PYROKI_SERVICE_URL="http://127.0.0.1:1")
    if case.get("egl_vendor_config"):
        env["__EGL_VENDOR_LIBRARY_FILENAMES"] = case["egl_vendor_config"]
    command = [str(sim / ".venv-libero/bin/python3"), "scripts/libero/runtime/replay_trial.py",
               "--args.suite", case["suite"], "--args.task", case["task"], "--args.trial", "51",
               "--args.model", case["model"], "--args.config", config,
               "--args.output-dir", str(out / "results"),
               "--args.replay-code", str(sim / "scripts/libero/scene_snapshot.py")]
    started = time.time()
    with open(out / "replay.log", "w") as log:
        proc = subprocess.run(command, cwd=sim, env=env, stdout=log, stderr=subprocess.STDOUT,
                              timeout=case["trial_timeout"])
    text = (out / "replay.log").read_text(errors="replace")
    language = re.search(r"^TASK_LANGUAGE: (.*)$", text, re.M)
    init = re.search(r"^Loaded init states for task (\d+) in suite (\S+)", text, re.M)
    found = re.search(r"^Found task_id=(\d+)", text, re.M)
    images = {n: (out / n).is_file() and (out / n).stat().st_size > 0
              for n in ("scene_snapshot.jpg", "scene_snapshot_wrist.jpg")}
    saved = sorted((out / "results").rglob("config.yaml"))
    resolved = yaml.safe_load(saved[0].read_text()) if saved else None
    proof = json.loads((out / "resolved-environment.json").read_text()) \
        if (out / "resolved-environment.json").is_file() else None
    row = {"cell": case["id"], "suite": case["suite"], "task": case["task"], "seed": 51,
           "config": config, "exit_code": proc.returncode, "wall_seconds": round(time.time() - started, 1),
           "task_id": int(found.group(1)) if found else None,
           "init_states_loaded": bool(init), "task_language": language.group(1) if language else None,
           "images": {n: str(out / n) for n, ok in images.items() if ok},
           "low_level": (resolved or {}).get("env", {}).get("cfg", {}).get("low_level"),
           "constructed_prompt": (proof or {}).get("prompt"),
           "constructed_apis": (proof or {}).get("constructed_apis"),
           "command": command, "log": str(out / "replay.log")}
    row["ok"] = bool(proc.returncode == 0 and init and language and all(images.values())
                     and proof and proof.get("privileged") is False)
    (out / "env-check.json").write_text(json.dumps(row, indent=2) + "\n")
    return row


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--gpu", type=int, required=True)
    parser.add_argument("--cells", default="")
    parser.add_argument("--also-libero10-config", action="store_true")
    args = parser.parse_args(argv)
    receipt = json.loads((PARENT / "prepare-receipt.json").read_text())
    cells = [c for c in args.cells.split(",") if c] or receipt["serial_order"]
    rows = {}
    for cell in cells:
        case = json.loads((PARENT / cell / "case.json").read_text())
        rows[cell] = {"default": run_one(case, args.gpu, case["env_config"], "default")}
        if args.also_libero10_config and case["suite"].startswith("libero_10"):
            alt = run_one(case, args.gpu, LIBERO10, "libero10-config")
            a, b = rows[cell]["default"], alt
            alt["same_as_default"] = {
                "low_level": a["low_level"] == b["low_level"],
                "task_language": a["task_language"] == b["task_language"],
                "constructed_prompt": a["constructed_prompt"] == b["constructed_prompt"]}
            rows[cell]["libero10_config"] = alt
        print(json.dumps({cell: {k: {"ok": v["ok"], "task_language": v["task_language"],
                                     "wall_seconds": v["wall_seconds"]} for k, v in rows[cell].items()}}),
              flush=True)
    summary = {"checked_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "gpu": args.gpu, "cells": rows,
               "all_ok": all(v["ok"] for r in rows.values() for v in r.values())}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({"all_ok": summary["all_ok"], "summary": str(OUT / "summary.json")}))
    return 0 if summary["all_ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
