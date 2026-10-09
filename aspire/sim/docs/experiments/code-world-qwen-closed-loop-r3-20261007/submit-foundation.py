#!/usr/bin/env python3
"""Submit three verified fresh closed-loop cells, with durable claims."""
from pathlib import Path
import importlib.util
import json
import subprocess
import sys

ROOT = Path("/mnt/home/gewang/experiments/code-world-qwen-closed-loop-r3-20261007")
SIM = Path("/mnt/home/gewang/code/ASPIRE-world-revision-20260910-01a0894b/aspire/sim")
DOC = SIM / "docs/experiments/code-world-qwen-closed-loop-r3-20261007"
sys.path.insert(0, str(DOC / "support"))
import full_deadlines

CELLS = ("bowl_C", "bowldrawer_C", "drawer_C")


def command(cell):
    return ["/mnt/home/gewang/.local/bin/dlc-run", "submit", "--name",
            "aspire-closedloop-r3-1007-" + cell.lower().replace("_", "-"),
            "--gpus", "8", "--workdir", str(ROOT), "--max-minutes", str(full_deadlines.DLC_MAX_RUNNING_MINUTES),
            "--priority", "9", "--skip-preflight", "--", "bash",
            str(ROOT / "launch-20261007/dlc-entry.sh"), "--case", str(ROOT / cell / "case.json")]


def main():
    # --skip-preflight avoids launching the full 16h campaign on DSW; separate
    # actual-work and platform preflights must already have passed for every cell.
    for cell in CELLS:
        log = (DOC / ("platform-" + cell.split("_")[0] + ".log")).read_text()
        if '"environment_preflight": "passed"' not in log or "preflight 通过(rc=0)" not in log:
            raise RuntimeError("platform preflight has not passed: " + cell)
        summary = json.loads((ROOT / "coordination" / ("dsw-preflight-" + cell.lower().replace("_", "-")) / "summary.json").read_text())
        if summary["state"] != "passed" or summary["cell"] != cell:
            raise RuntimeError("real-work preflight has not passed: " + cell)
    check = subprocess.run([str(SIM / ".venv-libero/bin/python3"), str(DOC / "prepare-foundation.py"), "--verify"], capture_output=True, text=True)
    (ROOT / "coordination/pre-submit-verification.json").write_text(check.stdout)
    if check.returncode:
        raise RuntimeError(check.stderr or "staged runtime verification failed")
    group = ROOT / "coordination/submissions"
    group.mkdir(exist_ok=True)
    with (group / "group-claim.json").open("x") as stream:
        json.dump({"max_submissions": 3, "cells": list(CELLS),
                   "authorization": "2026-10-07 user requested CC_USAGE repairs followed by three fresh Qwen DLC experiments",
                   "no_automatic_resubmission": True}, stream, indent=2)
    spec = importlib.util.spec_from_file_location("single_submit", SIM / "scripts/common/submit_dlc_once.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for cell in CELLS:
        if len(list(group.glob("*/submission-attempt.json"))) >= 3:
            raise RuntimeError("three-submission limit reached")
        result = module.submit_once(group / cell, command(cell))
        print(json.dumps({"cell": cell, **result}), flush=True)
        if result["state"] != "submitted":
            raise RuntimeError("ambiguous/failed submission; stop and inspect, never blindly retry")


if __name__ == "__main__":
    main()
