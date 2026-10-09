#!/usr/bin/env python3
"""Real-work (DSW) preflight of the generated per-cell runners, serially.

Usage: run-diagnostics.py [--cells a,b,c]   (default: every generated runner)
Each runner executes ONE real nonprivileged simulator trial of its cell's task
on seed 51 with a synthetic bundle, in a scratch directory under
coordination/; p1 cells additionally prove that the judgment-world manifest
carries `prediction_checks` and events.jsonl at least one `prediction` event,
off cells that the manifest lacks `prediction_checks`, and both that the
rendered worker prompt carries the p1 section iff p1. Nothing is imported into
a cell ledger unless the study is prepared with IMPORT_PREFLIGHT.
"""
import argparse
import json
import subprocess
import time
from pathlib import Path

root = Path(__file__).resolve().parent
parser = argparse.ArgumentParser()
parser.add_argument("--cells")
args = parser.parse_args()
runners = sorted(root.glob("run-dsw-preflight-*.py"))
if args.cells:
    wanted = {c.strip().lower().replace("_", "-") for c in args.cells.split(",") if c.strip()}
    runners = [r for r in runners if r.stem.removeprefix("run-dsw-preflight-") in wanted]
state = {"state": "running", "started_at": time.time(), "cells": {}}


def save():
    p = root / "diagnostics-status.json"
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2) + "\n")
    tmp.replace(p)


save()
for runner in runners:
    cell = runner.stem.removeprefix("run-dsw-preflight-")
    state["active"] = cell
    save()
    with (root / f"diagnostic-{cell}.log").open("x") as log:
        rc = subprocess.call(["/mnt/home/gewang/code/ASPIRE/aspire/sim/.venv-libero/bin/python3", "-u", str(runner)],
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
