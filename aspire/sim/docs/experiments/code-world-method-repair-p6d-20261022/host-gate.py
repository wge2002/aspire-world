#!/usr/bin/env python3
"""CC_TASK_14 host gate for a direct (non-DLC) launch on rbs-debug2. Run it on that host, as gewang.

Writes `<root>/preflight/host-gate/<hostname>.json`; the launch script refuses to start
unless that receipt exists for the same host with `passed: true`. Checks, in order:

  1. identity: uid/gid 10011 (the unit supervisor refuses any other);
  2. GPUs: nvidia-smi sees the launch's cards (smoke 0-3, trial 0-7), each with no compute
     process and < 1 GiB used. Only reads; never touches another process;
  3. the frozen model-server environment can initialise CUDA: the vLLM interpreter of
     `<root>/launch/reference/model-server.json` with that file's env (its LD_LIBRARY_PATH
     forward-compat libcuda was set up for the DLC image's 535 driver; this host's driver
     is newer, and a too-old compat library fails here, not in the middle of the run);
  4. env-check.py on the first slot card of the launch (seed-51 reset of every cell);
  5. (--replays) tools/gpu_replays.py on that card: real GraspNet same-request check with
     seeding off / m4-r1, and the real 4000-step budget run legacy / libero_latch.

  host-gate.py --root <study root> --kind smoke|trial [--replays]
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
GPUS = {"smoke": [0, 1, 2, 3], "trial": list(range(8))}
SLOT_CARD = 2  # unit-relative card of slot a (unit_layout PLACEMENT)


def run(command, timeout, env=None):
    proc = subprocess.run(command, capture_output=True, text=True, timeout=timeout, env=env)
    return {"command": command, "exit_code": proc.returncode, "stdout_tail": proc.stdout[-3000:],
            "stderr_tail": proc.stderr[-3000:]}


def gpu_state(wanted):
    query = run(["nvidia-smi", "--query-gpu=index,uuid,memory.used,driver_version,name",
                 "--format=csv,noheader,nounits"], 60)
    apps = run(["nvidia-smi", "--query-compute-apps=gpu_uuid,pid,used_memory", "--format=csv,noheader"], 60)
    cards = {}
    for line in query["stdout_tail"].splitlines():
        index, uuid, used, driver, name = [x.strip() for x in line.split(",")]
        cards[int(index)] = {"uuid": uuid, "used_mib": int(used), "driver": driver, "name": name, "processes": 0}
    for line in apps["stdout_tail"].splitlines():
        uuid = line.split(",")[0].strip()
        for card in cards.values():
            if card["uuid"] == uuid:
                card["processes"] += 1
    busy = [i for i in wanted if i not in cards or cards[i]["processes"] or cards[i]["used_mib"] >= 1024]
    return {"cards": cards, "wanted": wanted, "busy_or_missing": busy, "ok": not busy}


def cuda_init(root):
    reference = json.loads((root / "launch/reference/model-server.json").read_text())
    env = {k: v for k, v in os.environ.items() if not k.endswith(("_KEY", "_TOKEN", "SECRET"))}
    env.update(reference["env"])
    env["CUDA_VISIBLE_DEVICES"] = "0"
    code = ("import torch; torch.cuda.init(); x = torch.ones(4, device='cuda') * 2; "
            "print('CUDA_OK', torch.version.cuda, torch.cuda.get_device_name(0), float(x.sum()))")
    row = run([reference["argv"][0], "-c", code], 600, env=env)
    row["ok"] = row["exit_code"] == 0 and "CUDA_OK" in row["stdout_tail"]
    row["model_env_keys"] = sorted(reference["env"])
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--kind", choices=tuple(GPUS), required=True)
    parser.add_argument("--replays", action="store_true")
    args = parser.parse_args()
    host = socket.gethostname()
    out = args.root / "preflight/host-gate" / f"{host}.json"
    if out.exists():
        raise SystemExit(f"{out} exists; inspect it (a gate is never overwritten)")
    report = {"host": host, "kind": args.kind, "root": str(args.root), "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
              "uid_gid": [os.getuid(), os.getgid()]}
    report["identity_ok"] = report["uid_gid"] == [10011, 10011]
    report["gpus"] = gpu_state(GPUS[args.kind])
    card = GPUS[args.kind][SLOT_CARD]
    if report["identity_ok"] and report["gpus"]["ok"]:
        report["cuda_init"] = cuda_init(args.root)
        check = run([sys.executable, str(HERE / "env-check.py"), "--root", str(args.root), "--gpu", str(card)], 3600)
        check["ok"] = check["exit_code"] == 0
        report["env_check"] = check
        if args.replays:
            replay = run([str(HERE.parents[2] / ".venv-libero/bin/python3"), str(HERE / "tools/gpu_replays.py"),
                          "--gpu", str(card), "--out", str(args.root / "preflight/gpu-replays" / host)], 7200)
            replay["ok"] = replay["exit_code"] == 0
            report["gpu_replays"] = replay
    report["passed"] = bool(report["identity_ok"] and report["gpus"]["ok"]
                            and report.get("cuda_init", {}).get("ok") and report.get("env_check", {}).get("ok")
                            and (not args.replays or report.get("gpu_replays", {}).get("ok")))
    report["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"passed": report["passed"], "receipt": str(out)}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
