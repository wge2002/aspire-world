#!/usr/bin/env python3
"""p3 search-control study: generate the DLC submission plan. Plan only.

Derived from the p2 `submit-runtime-study-r9.py`. Kept: one 1x8-GPU job per
global node ID serving the shared claim queue, the exact `dlc-run plan` argv,
and the staged-launch hash checks. Dropped: r9's old-job stop/replacement path
and every platform mutation. `--execute` is deliberately not implemented: a
submission is a separate coordinator action after the user approves the job
specification printed here.

Two shapes are planned:
  A  5 jobs in parallel, one cell each (node window fits exactly one cell);
  B  3 jobs serving the same 5-cell queue (node window fits two cells).
GPU-hours are computed from the p2 cells' recorded wall times (same model,
seeds, budgets and watchdogs) and from the hard watchdog ceiling.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "support"))
import full_deadlines  # noqa: E402
from schedule import CELL_ORDER, GPUS_PER_NODE, PRIORITY, command, no_started_work  # noqa: E402

ROOT = Path("/mnt/home/gewang/experiments/code-world-search-control-p3-20261013")
P2_ROOT = Path("/mnt/home/gewang/experiments/code-world-runtime-p2-20261011")
DLC_RUN = Path("/mnt/home/gewang/.local/bin/dlc-run")
#: GPU map inside one job (unchanged from p2): model TP4, SAM3, GraspNet,
#: simulator + PyRoKi, and one card dlc-run's 4/8/8N sizing rule cannot release.
GPU_MAP = {"qwen_tp4": [0, 1, 2, 3], "sam3": [4], "graspnet": [5], "sim_and_pyroki": [6], "spare": [7]}
#: Startup ceiling before the first claim: services + native compat fixture.
STARTUP_MAX_S = full_deadlines.SERVICE_STARTUP + full_deadlines.NATIVE_COMPAT
#: One cell's driver ceiling (the supervisor's driver_timeout), plus its claim margin.
CELL_MAX_S = full_deadlines.DEVELOPMENT + full_deadlines.HELDOUT + full_deadlines.FINALIZE_MARGIN + 90
#: The supervisor stops claiming at node_window - 15 min; the entry gets max_minutes - 10.
WINDOW_MARGIN_MIN = 10 + 15
#: Observed node startup in p2 (services + compat), used for the expected case.
STARTUP_EXPECTED_H = 1.0
TASK_OF = {"stove": "stove", "drawer": "drawer", "bowl": "bowl"}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def max_minutes(cells_per_job):
    """Smallest whole-minute runtime whose node window admits `cells_per_job` full cells."""
    seconds = STARTUP_MAX_S + cells_per_job * CELL_MAX_S
    return -(-seconds // 60) + WINDOW_MARGIN_MIN + 5


def p2_wall_hours():
    """Recorded p2 driver wall time per task (hours), from campaign_state.json."""
    out = {}
    for task in ("stove", "drawer", "bowl"):
        rows = []
        for rep in (1, 2):
            state = json.loads((P2_ROOT / f"{task}_p2_r{rep}/campaign_state.json").read_text())
            rows.append({"cell": f"{task}_p2_r{rep}", "status": state.get("status"),
                         "total_hours": round(state["total_seconds"] / 3600, 2),
                         "development_hours": round(state["development_seconds"] / 3600, 2)})
        out[task] = rows
    return out


def expected_cell_hours(p2):
    """Mean recorded p2 wall per task, each capped at the p3 driver ceiling."""
    cap = CELL_MAX_S / 3600
    return {task: round(sum(min(r["total_hours"], cap) for r in rows) / len(rows), 2)
            for task, rows in p2.items()}


def simulate_queue(jobs, cell_hours, cells_per_job):
    """Greedy claim order exactly as the supervisor claims (first free node, CELL_ORDER)."""
    free_at = [STARTUP_EXPECTED_H] * jobs
    taken = [0] * jobs
    timeline = []
    for cell in CELL_ORDER:
        candidates = [j for j in range(jobs) if taken[j] < cells_per_job]
        if not candidates:
            raise RuntimeError("queue shape cannot hold every cell")
        j = min(candidates, key=lambda k: (free_at[k], k))
        start, hours = free_at[j], cell_hours[cell.split("_")[0]]
        free_at[j] = start + hours
        taken[j] += 1
        timeline.append({"cell": cell, "node": j, "start_h": round(start, 2), "end_h": round(free_at[j], 2)})
    return free_at, timeline


def shape(label, jobs, cells_per_job, cell_hours):
    minutes = max_minutes(cells_per_job)
    free_at, timeline = simulate_queue(jobs, cell_hours, cells_per_job)
    job_hours = [round(h, 2) for h in free_at]
    worst_jobs = [min(cells_per_job, sum(1 for i in range(len(CELL_ORDER)) if i % jobs == j)) for j in range(jobs)]
    worst_hours = [(STARTUP_MAX_S + n * CELL_MAX_S) / 3600 for n in worst_jobs]
    commands = [command("plan", node, jobs, ROOT, minutes) for node in range(jobs)]
    return {
        "shape": label, "jobs": jobs, "workers_per_job": 1, "gpus_per_job": GPUS_PER_NODE,
        "total_gpus": jobs * GPUS_PER_NODE, "cells_per_job_max": cells_per_job,
        "max_runtime_minutes_per_job": minutes,
        "node_window_minutes": minutes - 10,
        "admission_rule": "a node claims a cell only if the whole driver window "
                          f"({CELL_MAX_S} s) still fits before node_window - 15 min",
        "expected": {"per_job_wall_hours": job_hours, "wall_clock_hours": round(max(free_at), 2),
                     "gpu_hours": round(sum(free_at) * GPUS_PER_NODE, 1), "timeline": timeline,
                     "basis": "p2 recorded wall per task (mean of r1/r2, capped at the driver "
                              f"ceiling) + {STARTUP_EXPECTED_H} h node startup"},
        "worst_case": {"per_job_wall_hours": [round(h, 2) for h in worst_hours],
                       "wall_clock_hours": round(max(worst_hours), 2),
                       "gpu_hours": round(sum(worst_hours) * GPUS_PER_NODE, 1),
                       "basis": "every watchdog expires: startup 72 min + 19.5 h per cell; "
                                "the platform max runtime bounds it at "
                                f"{round(minutes / 60, 1)} h per job"},
        "billing_ceiling_gpu_hours": round(jobs * minutes / 60 * GPUS_PER_NODE, 1),
        "dlc_run_plan_argv": commands,
    }


def platform_plan(commands, out_dir):
    """`dlc-run plan` is local: it prints the plan and exits before any platform call."""
    rendered = []
    for node, argv in enumerate(commands):
        result = subprocess.run(argv, capture_output=True, text=True)
        path = out_dir / f"dlc-run-plan-node{node}.log"
        path.write_text(result.stdout + result.stderr)
        if result.returncode:
            raise RuntimeError(f"dlc-run plan failed for node {node}: {result.stderr[-1000:]}")
        line = next((l for l in result.stdout.splitlines() if l.startswith("command")), "")
        rendered.append({"node": node, "log": str(path), "command_line": line.split(":", 1)[1].strip()})
    return rendered


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, required=True, help="plan JSON to write")
    parser.add_argument("--render", action="store_true", help="also run the local `dlc-run plan` per node")
    parser.add_argument("--execute", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.execute:
        raise SystemExit("--execute is not implemented: submission is a separate approved coordinator action")
    launch = ROOT / "launch"
    manifest = json.loads((launch / "launch-manifest.json").read_text())
    changed = [name for name, expected in manifest["files"].items() if sha(launch / name) != expected]
    if changed:
        raise SystemExit(f"staged launch changed: {changed}")
    no_started_work(ROOT)
    p2 = p2_wall_hours()
    hours = expected_cell_hours(p2)
    plans = [shape("A_5_parallel", 5, 1, hours), shape("B_3_queued", 3, 2, hours)]
    if args.render:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        for plan in plans:
            folder = args.out.parent / f"dlc-run-plan-{plan['shape']}"
            folder.mkdir(exist_ok=True)
            plan["dlc_run_plan_rendered"] = platform_plan(plan["dlc_run_plan_argv"], folder)
    result = {
        "study": "code-world-search-control-p3-20261013", "submitted": False,
        "execute_implemented": False,
        "workspace": "1000309 (MMLM_model)", "quota": "quotaabjvnuk8san (quota_h100; GPU type L20Z)",
        "job_type": "PyTorchJob via dlc-run", "priority": PRIORITY,
        "image": "dsw-registry-vpc.cn-shanghai.cr.aliyuncs.com/pai/modelscope:1.28.0-pytorch2.3.1tensorflow2.16.1-gpu-py311-cu121-ubuntu22.04",
        "mounts": ["bmcpfs://cpfs-010029yj6czm6fnidqh0l-vpc-qtrdw8.cn-shanghai.cpfs.aliyuncs.com/media/home::/mnt/home (rw)"],
        "per_job_resources": {"gpus": GPUS_PER_NODE, "cpu": 160, "memory": "800Gi", "shared_memory": "64Gi",
                              "gpu_map": GPU_MAP,
                              "sizing": "peak 7 busy cards (Qwen TP4 + SAM3 + GraspNet + sim/PyRoKi); "
                                        "dlc-run admits only 4, 8 or 8N and 4 cannot host TP4 plus "
                                        "services, so GPU 7 is the one unavoidable idle card (as in p2 r9)"},
        "runtime_uid_gid": "10011:10011 via dlc_entry_prelude.sh in launch/dlc-single-node-entry.sh",
        "entry": str(ROOT / "launch/dlc-single-node-entry.sh") + " --node-id N --node-count M --node-window-minutes W",
        "output_path": str(ROOT) + "/<cell>/ (campaign_state.json, outputs/, heldout/); queue claims under queue/",
        "cells": list(CELL_ORDER), "launch_manifest_sha256": sha(launch / "launch-manifest.json"),
        "cell_ceiling": {"startup_seconds": STARTUP_MAX_S, "driver_seconds": CELL_MAX_S,
                         "deadlines": full_deadlines.summary()},
        "p2_recorded_wall": p2, "expected_cell_hours": hours,
        "plans": plans,
        "notes": [
            "Rehearsal evidence: DSW diagnostic replay + native solver smoke in CC_TASK_2_HANDOFF.json.",
            "The submission tooling skips dlc-run's own preflight (--skip-preflight, as in p2 r9) because "
            "the entry point is itself a multi-hour job; the supervisor's --preflight has been run on DSW.",
            "If this DSW shares quota_h100, its GPUs consume capacity during the run.",
        ],
    }
    args.out.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({p["shape"]: {k: p[k] for k in ("jobs", "total_gpus", "max_runtime_minutes_per_job")}
                      | {"expected_wall_h": p["expected"]["wall_clock_hours"],
                         "expected_gpu_h": p["expected"]["gpu_hours"],
                         "worst_wall_h": p["worst_case"]["wall_clock_hours"],
                         "worst_gpu_h": p["worst_case"]["gpu_hours"]} for p in plans}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
