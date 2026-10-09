#!/usr/bin/env python3
"""Platform (dlc-preflight) check of every staged cell's entry script, serially.

Usage: run-platform-preflights.py --entry dlc-entry.sh|dlc-queue-entry.sh [--cells a,b,c]
(default: every staged cell). Each entry keeps its own evidence beside this file:
platform-<entry stem>-<cell>.log and platform-status-<entry stem>.json, so the
single-cell and the queue entry are proven separately and neither overwrites
the other.
"""
import argparse
import json
import subprocess
import time
from pathlib import Path

root = Path(__file__).resolve().parent
output = Path("/mnt/home/gewang/experiments/code-world-prediction-base-20261008")
LAUNCH = output / "launch-20261009"
ENTRIES = ("dlc-entry.sh", "dlc-queue-entry.sh")
parser = argparse.ArgumentParser()
parser.add_argument("--cells")
parser.add_argument("--preflight-gpu", default="2")
parser.add_argument("--entry", required=True, choices=ENTRIES, help="launch entry to exercise")
args = parser.parse_args()
cells = ([c.strip() for c in args.cells.split(",") if c.strip()] if args.cells
         else sorted(p.parent.name for p in output.glob("*/case.json")))
stem = Path(args.entry).stem
state = {"state": "verifying", "entry": str(LAUNCH / args.entry), "started_at": time.time(), "cells": {}}


def save():
    p = root / f"platform-status-{stem}.json"
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2) + "\n")
    tmp.replace(p)


save()
with (root / f"verify-{stem}.log").open("a") as log:
    rc = subprocess.call(["/mnt/home/gewang/code/ASPIRE/aspire/sim/.venv-libero/bin/python3",
                          str(root / "prepare-prediction-study.py"), "--verify", "--cells", ",".join(cells)],
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
    with (root / f"platform-{stem}-{cell}.log").open("w") as log:
        rc = subprocess.call(["/mnt/home/gewang/.local/bin/dlc-preflight", "--timeout", "300", "bash",
                              str(LAUNCH / args.entry), "--case",
                              str(output / cell / "case.json"), "--preflight",
                              "--preflight-gpu", args.preflight_gpu],
                             stdout=log, stderr=subprocess.STDOUT)
    state["cells"][cell] = {"exit_code": rc, "finished_at": time.time(),
                            "log": str(root / f"platform-{stem}-{cell}.log")}
    save()
    if rc:
        state["state"] = "failed"
        save()
        raise SystemExit(rc)
state["state"] = "passed"
state["finished_at"] = time.time()
save()
