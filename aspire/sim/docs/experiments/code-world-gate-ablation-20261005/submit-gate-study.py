#!/usr/bin/env python3
"""Submit the development-gate study as shared-queue DLC jobs, with durable claims.

Every job runs `launch-20261005/dlc-queue-entry.sh`: each of its workers starts the
node's services once, passes the native compat fixture once (3 attempts), then
claims and runs cells from `<parent>/queue/claims` one after another until none
is left. Any number of such jobs may run at the same time; they never run the
same cell twice.

  submit-gate-study.py --gpus 24 --max-minutes 9900 --authorization "<user's words>"
  submit-gate-study.py --gpus 8 --jobs 3 --max-minutes 9900 --authorization "<...>"
  submit-gate-study.py --gpus 64 --max-minutes 9900 --authorization "<...>"   # one 8-worker job

Refuses to submit unless every staged cell has a passed real-work preflight and a
passed platform preflight of the queue entry, re-verifies the staged runtimes
first, and appends a group claim so the number of jobs is always on record.
"""
import argparse
import importlib.util
import json
import subprocess
from pathlib import Path

ROOT = Path("/mnt/home/gewang/experiments/code-world-gate-ablation-20261005")
SIM = Path("/mnt/home/gewang/code/ASPIRE-world-revision-20260910-01a0894b/aspire/sim")
DOC = SIM / "docs/experiments/code-world-gate-ablation-20261005"
JOB_PREFIX = "aspire-gate-1005-queue"
def gpu_total(value):
    """8 or any multiple of 8: every worker of a queue job owns one 8-GPU node.
    (dlc-run rule widened 2026-10-05 from 4/8/16/24 to 4/8/multiples of 8; a queue
    worker needs the full 8-GPU map, so 4 stays excluded here.)"""
    total = int(value)
    if total < 8 or total % 8:
        raise argparse.ArgumentTypeError("total GPUs must be 8 or a multiple of 8 (N workers x 8)")
    return total


def staged_cells():
    return sorted(p.parent.name for p in ROOT.glob("*/case.json"))


def preflights_passed(cell):
    log = DOC / f"platform-{cell}.log"
    if not log.is_file():
        raise RuntimeError(f"no platform preflight log for {cell}: {log}")
    text = log.read_text()
    if '"environment_preflight": "passed"' not in text or "preflight 通过(rc=0)" not in text:
        raise RuntimeError("platform preflight has not passed: " + cell)
    if "dlc-queue-entry.sh" not in text:
        raise RuntimeError("platform preflight did not exercise the queue entry: " + cell)
    summary_path = ROOT / "coordination" / ("dsw-preflight-" + cell.lower().replace("_", "-")) / "summary.json"
    summary = json.loads(summary_path.read_text())
    if summary["state"] != "passed" or summary["cell"] != cell:
        raise RuntimeError("real-work preflight has not passed: " + cell)


def command(name, gpus, max_minutes):
    return ["/mnt/home/gewang/.local/bin/dlc-run", "submit", "--name", name,
            "--gpus", str(gpus), "--workdir", str(ROOT), "--max-minutes", str(max_minutes),
            "--priority", "9", "--skip-preflight", "--", "bash",
            str(ROOT / "launch-20261005/dlc-queue-entry.sh")]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--gpus", type=gpu_total, required=True,
                        help="total GPUs per job: 8 or a multiple of 8; N x 8 = N workers each serving the queue")
    parser.add_argument("--jobs", type=int, default=1, help="how many queue jobs to submit now")
    parser.add_argument("--max-minutes", type=int, required=True,
                        help="job running-time cap; must cover this job's share of the queue")
    parser.add_argument("--authorization", required=True, help="the user's words that authorize this submission")
    parser.add_argument("--plan-only", action="store_true", help="print the dlc-run plan and stop")
    args = parser.parse_args()
    cells = staged_cells()
    if not cells:
        raise RuntimeError("no staged cells under " + str(ROOT))
    for cell in cells:
        preflights_passed(cell)
    check = subprocess.run([str(SIM / ".venv-libero/bin/python3"), str(DOC / "prepare-gate-study.py"), "--verify"],
                           capture_output=True, text=True)
    (ROOT / "coordination").mkdir(exist_ok=True)
    (ROOT / "coordination/pre-submit-verification.json").write_text(check.stdout)
    if check.returncode:
        raise RuntimeError(check.stderr or "staged runtime verification failed")
    group = ROOT / "coordination/submissions"
    group.mkdir(exist_ok=True)
    existing = len(list(group.glob("queue-*/submission-attempt.json")))
    names = [f"{JOB_PREFIX}-{existing + i + 1:02d}" for i in range(args.jobs)]
    if args.plan_only:
        for name in names:
            plan = command(name, args.gpus, args.max_minutes)
            plan[1] = "plan"
            print(subprocess.run(plan, capture_output=True, text=True).stdout)
        return
    claim = group / "group-claim.json"
    claims = json.loads(claim.read_text()) if claim.is_file() else {"batches": []}
    claims["batches"].append({"jobs": names, "gpus_per_job": args.gpus, "max_minutes": args.max_minutes,
                              "cells_in_queue": cells, "authorization": args.authorization,
                              "no_automatic_resubmission": True})
    claim.write_text(json.dumps(claims, indent=2))
    spec = importlib.util.spec_from_file_location("single_submit", SIM / "scripts/common/submit_dlc_once.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for name in names:
        folder = group / name.replace(JOB_PREFIX, "queue")
        if (folder / "submission-attempt.json").exists():
            raise RuntimeError(f"{name} already has a submission attempt; never resubmit blindly")
        result = module.submit_once(folder, command(name, args.gpus, args.max_minutes))
        print(json.dumps({"job": name, **result}), flush=True)
        if result["state"] != "submitted":
            raise RuntimeError("ambiguous/failed submission; stop and inspect, never blindly retry")


if __name__ == "__main__":
    main()
