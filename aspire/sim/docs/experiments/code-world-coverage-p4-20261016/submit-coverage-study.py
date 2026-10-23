#!/usr/bin/env python3
"""Build the p4 submission plan for the locked node layout. Plans only; submits nothing.

Two packings of the same seven cells onto `2 node x 8 GPU` workers, each worker
running two units of two cell slots (4 slots per worker, shared claim queue):

  A  4 + 3   the requested shape: the four first claims land on node 0 and the
             three remaining cells on node 1. This fits, and the arithmetic that
             shows it is printed with the plan: a worker holds 4 slots and the
             p4 round has 7 cells, so no slot needs a second cell, and the node
             window only has to hold ONE cell (`cell_timeout`).
  B  4 + 3, split by position   the same totals, but the two repeats of a
             position are placed on different workers, so losing one worker
             cannot take out both stove or both drawer cells. This is the
             arrangement to prefer when the window is tight, because a slow
             position no longer has to finish twice inside one worker.

The `--gpus 8` request is 8 GPU-hours of capacity per wall-clock hour; the entry
command is `launch/unit-entry.sh`, and every field a reviewer needs (image,
mount, workspace, quota, priority, max runtime, uid/gid, output root) is written
into both the JSON and the approval table.

`--plan` additionally runs the local `dlc-run plan` for each worker (read-only,
no quota) and records its verbatim output beside the plan.

Nothing here submits, stops or clones a job. Submission is a separate, explicitly
approved act.

  submit-coverage-study.py --out coordination/submit-plan/SUBMIT_PLAN.json [--plan]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SIMPATH = HERE.parents[2]
sys.path.insert(0, str(SIMPATH / "scripts/common/node_layout"))
import unit_layout as layout  # noqa: E402

NAME = "code-world-coverage-p4-20261016"
PARENT = Path("/mnt/home/gewang/experiments") / NAME
RECEIPT = PARENT / "prepare-receipt.json"

#: Plan-level constants. The window is derived from the supervisor's own refusal
#: test for a SINGLE-UNIT job (see `window_check`): one cell must fit after unit
#: startup, plus the platform-expiry margin. A 4-GPU job runs one unit of two
#: slots; with 7 cells and 8 slots over the four jobs no slot needs a second
#: cell, so the window only has to hold one cell and is independent of job size.
CELL_HOURS_HINT = 19.5        # full_deadlines: DEVELOPMENT + HELDOUT + FINALIZE_MARGIN
MIN_NODE_WINDOW_MINUTES = 1217   # ceil((70200 + 90 + 1800 + 900) / 60); the floor
NODE_WINDOW_MINUTES = 1240    # floor + 23 min for launch checks and env preflight
MAX_MINUTES = NODE_WINDOW_MINUTES + 10
PRIORITY = 9
#: The approved job shape: one 4-GPU unit per job (revised 2026-10-16 from 2 x 8 GPU).
GPUS_PER_JOB = 4
STARTUP_HOURS = 14 / 60       # measured: vLLM up 13.7 min from cold weights (Task 5)
#: Per-cell expected wall: the node layout's projection (PLAN.md metrics 5), not
#: a p3 measurement. Development 3 h + held-out 2.2 h + finalize 0.5 h.
CELL_HOURS = {"stove": 3.6, "drawer": 5.3, "bowl": 4.9, "wine": 5.0, "bowldrawer": 5.6}
CELL_TIMEOUT_HOURS = (14 * 3600 + 5 * 3600 + 1800) / 3600   # full_deadlines sum

#: The fixed deployment fields every worker shares (CLAUDE.md, verified in p3).
FIXED = {
    "job_type": "PyTorchJob via dlc-run submit",
    "workers": 1,
    "gpus_per_worker": GPUS_PER_JOB,
    "gpu_type": "NVIDIA L20Z",
    "cpu_memory_shm_per_job": "160 CPU / 800Gi / 64Gi",
    "image": ("dsw-registry-vpc.cn-shanghai.cr.aliyuncs.com/pai/modelscope:"
              "1.28.0-pytorch2.3.1tensorflow2.16.1-gpu-py311-cu121-ubuntu22.04"),
    "workspace": "1000309 (MMLM_model)",
    "resource_quota": "quotaabjvnuk8san (quota_h100; verify the GPU type at submit time)",
    "priority": PRIORITY,
    "max_runtime_minutes": MAX_MINUTES,
    "node_window_minutes": NODE_WINDOW_MINUTES,
    "mount": ("bmcpfs://cpfs-010029yj6czm6fnidqh0l-vpc-qtrdw8.cn-shanghai.cpfs.aliyuncs.com"
              "/media/home::/mnt/home (rw)"),
    "runtime_uid_gid": "10011:10011 (dlc_entry_prelude.sh, switched before any project access)",
    "output_root": str(PARENT),
    "entry": f"bash {PARENT}/launch/unit-entry.sh",
    "supervisor": f"{PARENT}/launch/unit_supervisor.py",
}

#: Claim order = the study's serial order (PLAN.md: every first repeat before any second).
CLAIM_ORDER = ("stove_p4_r1", "drawer_p4_r1", "bowl_p4_r1", "wine_p4_r1", "bowldrawer_p4_r1",
               "stove_p4_r2", "drawer_p4_r2")

PACKINGS = {
    "B4_4jobs_rolling": {
        "rationale": ("APPROVED 2026-10-16 by the user (revised shape: four 4-GPU jobs instead of two "
                      "8-GPU ones). Seven cells are split over four single-unit jobs by ROLLING the "
                      "claim order, so a position's two repeats land on different jobs: "
                      "stove_p4_r1 -> job0 and stove_p4_r2 -> job1; drawer_p4_r1 -> job1 and "
                      "drawer_p4_r2 -> job2. No node-level pinning is added or needed: slots claim "
                      "atomically from the shared queue in claim_order, and with 7 cells against 8 "
                      "slots no slot runs a second cell. The per-job cell lists below are therefore "
                      "NOMINAL — whichever slot is ready first takes the next claim — and are written "
                      "down as the intended spread, not as an enforced assignment."),
        "split": {"group_1": ("stove_p4_r1", "drawer_p4_r1", "bowl_p4_r1", "bowldrawer_p4_r1"),
                  "group_2": ("wine_p4_r1", "stove_p4_r2", "drawer_p4_r2")},
        "nodes": {0: ("stove_p4_r1", "bowldrawer_p4_r1"),
                  1: ("drawer_p4_r1", "stove_p4_r2"),
                  2: ("bowl_p4_r1", "drawer_p4_r2"),
                  3: ("wine_p4_r1",)}},
    "A2_2jobs_4plus3": {
        "rationale": ("the superseded 8-GPU shape: two workers of two units each. Both jobs were "
                      "submitted on 2026-10-16 and STOPPED by the user before they ever ran "
                      "(GmtRunningTime null), when this round was re-cut into four 4-GPU jobs. Kept "
                      "here as the record of what was withdrawn."),
        "nodes": {0: ("stove_p4_r1", "drawer_p4_r1", "bowl_p4_r1", "bowldrawer_p4_r1"),
                  1: ("wine_p4_r1", "stove_p4_r2", "drawer_p4_r2")}},
}
#: The packing the user approved for submission.
APPROVED = "B4_4jobs_rolling"


def position(cell: str) -> str:
    return cell.split("_")[0]


def window_check() -> dict:
    """The supervisor's own refusal test for THIS job shape.

    `unit_supervisor.main` refuses a node window that cannot hold one cell after
    startup: `cell_timeout + 90 + STARTUP_ALLOWANCE <= node_window - 900`. A
    4-GPU job runs ONE unit (2 slots); with 7 cells and 8 slots across four jobs
    no slot needs a second cell, so the window only has to hold a single cell —
    which is why a 4-GPU job needs the same window an 8-GPU job did.
    """
    allowance = 1800
    expiry_margin = 900
    timeout = CELL_TIMEOUT_HOURS * 3600
    need = timeout + 90 + allowance + expiry_margin
    have = NODE_WINDOW_MINUTES * 60
    jobs = len(PACKINGS[APPROVED]["nodes"])
    return {"cell_timeout_seconds": timeout, "startup_allowance_seconds": allowance,
            "platform_expiry_margin_seconds": expiry_margin,
            "minimum_node_window_minutes": round(need / 60, 1),
            "node_window_minutes": NODE_WINDOW_MINUTES,
            "headroom_minutes": round((have - need) / 60, 1),
            "satisfied": have >= need,
            "units_per_job": 1, "slots_per_unit": len(layout.SLOT_NAMES),
            "jobs": jobs, "slots_total": jobs * len(layout.SLOT_NAMES),
            "cells_total": len(CLAIM_ORDER)}


def packing_spec(name: str, packing: dict) -> dict:
    nodes = packing["nodes"]
    workers = []
    for node, cells in sorted(nodes.items()):
        hours = STARTUP_HOURS + max(CELL_HOURS[position(c)] for c in cells)
        workers.append({
            "job_name": f"aspire-coverage-p4-1016-node{node}",
            "node_id": node, "node_count": len(nodes),
            "cells_expected": list(cells), "cells_expected_count": len(cells),
            "slots": len(layout.SLOT_NAMES),
            "gpus": GPUS_PER_JOB, "gpu_type": FIXED["gpu_type"], "workers": 1,
            "entry_command": (f"{FIXED['entry']} --node-id {node} --node-count {len(nodes)} "
                              f"--node-window-minutes {NODE_WINDOW_MINUTES}"),
            "argv": layout.dlc_plan_argv("aspire-coverage-p4-1016", node, len(nodes), PARENT,
                                         MAX_MINUTES, GPUS_PER_JOB),
            "expected_wall_hours": round(hours, 2),
            "worst_case_wall_hours": round(NODE_WINDOW_MINUTES / 60, 2),
            "expected_gpu_hours": round(hours * 8, 1),
            "billing_ceiling_gpu_hours": round(MAX_MINUTES / 60 * 8, 1),
        })
    return {"packing": name, "rationale": packing["rationale"],
            "workers": workers, "nodes": len(nodes),
            "total_gpus": GPUS_PER_JOB * len(nodes),
            "expected_wall_hours": round(max(w["expected_wall_hours"] for w in workers), 2),
            "worst_case_wall_hours": round(NODE_WINDOW_MINUTES / 60, 2),
            "expected_gpu_hours": round(sum(w["expected_gpu_hours"] for w in workers), 1),
            "billing_ceiling_gpu_hours": round(sum(w["billing_ceiling_gpu_hours"] for w in workers), 1)}


def build() -> dict:
    receipt = json.loads(RECEIPT.read_text()) if RECEIPT.is_file() else {}
    staged = sorted(receipt.get("cells", {}))
    missing = [c for c in CLAIM_ORDER if c not in staged]
    if missing:
        raise SystemExit(f"cells not staged yet: {missing}; run --prepare first")
    packs = {name: packing_spec(name, packing) for name, packing in PACKINGS.items()}
    return {
        "schema": "p4-submit-plan/1",
        "study": NAME, "generated_from": str(RECEIPT),
        "launch_manifest_sha256": receipt.get("launch_manifest_sha256"),
        "plan_only": True, "submitted": False,
        "claim_order": list(CLAIM_ORDER),
        "layout": "node-layout-20261015: one 8-GPU worker = 2 units (Qwen TP4 + SAM3 + GraspNet + "
                  "PyRoKi); each unit serves two cell slots; one shared claim queue across nodes",
        "fixed": FIXED,
        "window_check": window_check(),
        "cell_hours_assumption": {
            "per_position_expected_hours": CELL_HOURS,
            "source": "PLAN.md metric 5 (node-layout projection: dev 2-4 h against p3's 8.4-14 h) plus "
                      "held-out 1.3-2.2 h and a 0.5 h finalize margin; NOT a p3 measurement",
            "startup_hours": STARTUP_HOURS,
            "startup_source": "Task 5 smoke: vLLM up 13.7 min from cold CPFS weights"},
        "rehearsal": {"required": True,
                      "shape": "dlc-preflight + one real work unit on DSW's own 2 GPUs",
                      "note": "recorded in CC_TASK_1_HANDOFF.json once run"},
        "packings": packs,
        "recommended": APPROVED,
        "what_this_plan_does_not_do": [
            "no job is submitted, cloned, stopped or deleted",
            "no DLC quota is consumed; dlc-run plan is read-only",
            "held-out stays sealed until each cell freezes",
        ],
    }


def approval_markdown(plan: dict) -> str:
    f = plan["fixed"]
    w = plan["window_check"]
    approved = plan["packings"][plan["recommended"]]
    superseded = plan["packings"]["A2_2jobs_4plus3"]
    split = PACKINGS[plan["recommended"]].get("split", {})
    lines = [
        "# p4 development-coverage study — submission plan (four 4-GPU jobs), FOR APPROVAL",
        "",
        f"Generated from `{plan['generated_from']}` by `submit-coverage-study.py`.",
        "Seven cells over **four `4 GPU` single-unit jobs** sharing one claim queue.",
        "",
        "## Revision note (2026-10-16)",
        "",
        "This plan supersedes the earlier `2 x 8 GPU` shape. Two 8-GPU jobs were submitted under that "
        "shape and were **stopped by the user before they ever ran** (`GmtRunningTime: null` on both, "
        "so no compute was consumed) when the round was re-cut into four 4-GPU jobs. The withdrawn "
        "shape is kept at the end of this document as the record of what was withdrawn.",
        "",
        "## What is being requested",
        "",
        f"**Four DLC jobs of one `4 GPU` worker each, {approved['total_gpus']} GPUs total**, serving the "
        "shared 7-cell queue. One 4-GPU job runs exactly one unit: one Qwen TP4 server plus SAM3, "
        "GraspNet and PyRoKi, holding two cell slots. With 7 cells against 8 slots across the four "
        "jobs, no slot runs a second cell, so the four jobs cover the round in one pass.",
        "",
        "## Job specification (identical for all four)",
        "",
        "| field | value |",
        "|---|---|",
        "| job names | "
        + ", ".join(f"`{w['job_name']}`" for w in approved["workers"])
        + " |",
        f"| job type | {f['job_type']} |",
        f"| workers per job | {f['workers']} |",
        f"| GPUs per worker | {f['gpus_per_worker']} ({f['gpu_type']}) |",
        f"| total GPUs | {approved['total_gpus']} (4 jobs x 4) |",
        f"| CPU / memory / shm per job | 80 CPU / 400Gi / 32Gi (scaled at 20 CPU / 100Gi / 8Gi per GPU) |",
        f"| image | `{f['image']}` |",
        f"| workspace | {f['workspace']} |",
        f"| resource quota | {f['resource_quota']} |",
        f"| priority | {f['priority']} |",
        f"| max runtime per job | {f['max_runtime_minutes']} minutes ({f['max_runtime_minutes']/60:.2f} h) |",
        f"| node window | {f['node_window_minutes']} minutes (`--node-window-minutes`) |",
        f"| entry command | `{f['entry']} --node-id N --node-count 4 --node-window-minutes {f['node_window_minutes']}` |",
        f"| mount | `{f['mount']}` |",
        f"| runtime uid/gid | {f['runtime_uid_gid']} |",
        f"| output root | `{f['output_root']}/<cell>/` (`campaign_state.json`, `outputs/`, `heldout/`), "
        f"claims under `<root>/queue/` |",
        "",
        "## The split (rolling, so no position repeats on one job)",
        "",
        f"*{approved['rationale']}*",
        "",
        f"- group 1 (first claims): {', '.join(split.get('group_1', ()))}",
        f"- group 2 (later claims): {', '.join(split.get('group_2', ()))}",
        f"- claim order as staged: {', '.join(plan['claim_order'])}",
        "",
        "| job | intended cells | slots | GPUs | expected wall | worst case | expected GPU-h | billing ceiling |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for worker in approved["workers"]:
        lines.append(f"| `{worker['job_name']}` | {', '.join(worker['cells_expected'])} "
                     f"| {worker['slots']} | {worker['gpus']} "
                     f"| {worker['expected_wall_hours']} h | {worker['worst_case_wall_hours']} h "
                     f"| {worker['expected_gpu_hours']} | {worker['billing_ceiling_gpu_hours']} |")
    lines += [
        "",
        f"Total: expected wall **{approved['expected_wall_hours']} h** (the slowest job; the four run "
        f"concurrently), expected **{approved['expected_gpu_hours']} GPU-h**, billing ceiling "
        f"**{approved['billing_ceiling_gpu_hours']} GPU-h** (4 x {f['max_runtime_minutes']} min x 4 GPU).",
        "",
        "## Why a 4-GPU job still fits (the window question)",
        "",
        f"- One 4-GPU job = **{w['units_per_job']} unit** x {w['slots_per_unit']} slots = "
        f"**{w['slots_per_unit']} slots**; {w['jobs']} jobs give **{w['slots_total']} slots** for "
        f"**{w['cells_total']} cells**, so no slot needs a second cell and no job needs to be sized for two.",
        f"- The supervisor only requires the window to hold ONE cell: `cell_timeout` "
        f"{w['cell_timeout_seconds']:.0f} s + 90 s cleanup + {w['startup_allowance_seconds']} s startup "
        f"+ {w['platform_expiry_margin_seconds']} s platform margin needs "
        f"**>= {w['minimum_node_window_minutes']:.0f} min**; this plan asks for {w['node_window_minutes']} min "
        f"({w['headroom_minutes']:.0f} min headroom) — {'satisfied' if w['satisfied'] else 'NOT satisfied'}.",
        "- A 4-GPU job needs the SAME window an 8-GPU job did: the window is set by one cell, not by the "
        "job's card count. What four smaller jobs buy is that a single unit failure costs two slots "
        "instead of four, and the four jobs start four Qwen servers instead of two (no extra GPU cost, "
        "since each unit's cards are its own).",
        "- Each job still uses the locked per-unit spec: util 0.82, SAM3 on unit card 0, GraspNet on "
        "card 1, slot simulators on cards 2 and 3, PyRoKi on CPU.",
        "",
        "## Cost and wall clock",
        "",
        "| | expected | worst case (every watchdog expires) |",
        "|---|---|---|",
        f"| wall clock | {approved['expected_wall_hours']} h | {approved['worst_case_wall_hours']} h |",
        f"| GPU-hours | {approved['expected_gpu_hours']} | {approved['billing_ceiling_gpu_hours']} |",
        "",
        f"Per-cell expectation source: {plan['cell_hours_assumption']['source']}",
        "",
        "## Superseded shape (withdrawn 2026-10-16) — 2 x 8 GPU",
        "",
        f"*{superseded['rationale']}*",
        "",
        "| job | cells | GPUs |",
        "|---|---|---|",
    ]
    for worker in superseded["workers"]:
        lines.append(f"| `{worker['job_name']}` | {', '.join(worker['cells_expected'])} | {worker['gpus']} |")
    lines += [
        "",
        "Both jobs were submitted at 2026-10-16T15:42Z and stopped at 15:50Z (StoppedByUser) with "
        "`GmtRunningTime: null` — they were still Queuing and never consumed compute.",
        "",
        "## What this plan does not do",
        "",
    ]
    lines += [f"- {item}" for item in plan["what_this_plan_does_not_do"]]
    lines += ["", "Submission requires explicit approval of this plan and the job specification above.", ""]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--plan", action="store_true",
                        help="also run the local read-only `dlc-run plan` per worker and record it")
    args = parser.parse_args()
    plan = build()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(plan, indent=2) + "\n")
    markdown = args.out.parent / "PLAN_FOR_APPROVAL.md"
    markdown.write_text(approval_markdown(plan))
    recorded = []
    if args.plan:
        logdir = args.out.parent / f"dlc-run-plan-{plan['recommended']}"
        logdir.mkdir(parents=True, exist_ok=True)
        for worker in plan["packings"][plan["recommended"]]["workers"]:
            argv = worker["argv"]
            proc = subprocess.run(argv, capture_output=True, text=True)
            (logdir / f"dlc-run-plan-{worker['job_name']}.log").write_text(
                f"$ {' '.join(argv)}\n\n--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}")
            recorded.append({"job_name": worker["job_name"], "argv": argv,
                             "exit_code": proc.returncode})
    print(json.dumps({"out": str(args.out), "markdown": str(markdown),
                      "recommended": plan["recommended"],
                      "expected_wall_hours": plan["packings"][plan["recommended"]]["expected_wall_hours"],
                      "ceiling_gpu_hours": plan["packings"][plan["recommended"]]["billing_ceiling_gpu_hours"],
                      "dlc_run_plan": recorded}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
