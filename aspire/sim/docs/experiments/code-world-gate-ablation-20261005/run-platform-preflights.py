#!/usr/bin/env python3
"""Platform (dlc-preflight) check of every staged cell's entry script, serially.

Usage: run-platform-preflights.py [--cells a,b,c]   (default: every staged cell)
Writes platform-<cell>.log beside this file and platform-status.json.
"""
import argparse
import json
import subprocess
import time
from pathlib import Path

root = Path(__file__).resolve().parent
output = Path("/mnt/home/gewang/experiments/code-world-gate-ablation-20261005")
parser = argparse.ArgumentParser()
parser.add_argument("--cells")
parser.add_argument("--preflight-gpu", default="2")
parser.add_argument("--entry", default="dlc-queue-entry.sh", help="launch entry to exercise (dlc-queue-entry.sh or dlc-entry.sh)")
args = parser.parse_args()
cells = ([c.strip() for c in args.cells.split(",") if c.strip()] if args.cells
         else sorted(p.parent.name for p in output.glob("*/case.json")))
state = {"state": "verifying", "started_at": time.time(), "cells": {}}


def save():
    p = root / "platform-status.json"
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2) + "\n")
    tmp.replace(p)


save()
with (root / "verify.log").open("a") as log:
    rc = subprocess.call(["/mnt/home/gewang/code/ASPIRE/aspire/sim/.venv-libero/bin/python3",
                          str(root / "prepare-gate-study.py"), "--verify", "--cells", ",".join(cells)],
                         stdout=log, stderr=subprocess.STDOUT)
if rc:
    state["state"] = "verification_failed"
    save()
    raise SystemExit(rc)
state["state"] = "running"
save()
for cell in cells:
    state["active"] = cell
    save()
    with (root / f"platform-{cell}.log").open("w") as log:
        rc = subprocess.call(["/mnt/home/gewang/.local/bin/dlc-preflight", "--timeout", "300", "bash",
                              str(output / "launch-20261005" / args.entry), "--case",
                              str(output / cell / "case.json"), "--preflight",
                              "--preflight-gpu", args.preflight_gpu],
                             stdout=log, stderr=subprocess.STDOUT)
    state["cells"][cell] = {"exit_code": rc, "finished_at": time.time()}
    save()
    if rc:
        state["state"] = "failed"
        save()
        raise SystemExit(rc)
state["state"] = "passed"
state["finished_at"] = time.time()
save()
