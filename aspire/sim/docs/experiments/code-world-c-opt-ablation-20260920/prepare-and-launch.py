#!/usr/bin/env python3
"""C repair only: six frozen isolated arms, same per-task development starter."""
import argparse
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess

HERE = Path(__file__).resolve().parent
NAME = "code-world-c-opt-ablation-20260920"
spec = importlib.util.spec_from_file_location("c_stager", HERE / "support/legacy_stager.py")
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)
OLD = base.ENGINEERING / "docs/experiments/code-world-rerun-20260919"
OLD_RESULTS = Path("/mnt/home/gewang/experiments/code-world-rerun-20260919")
base.__file__ = __file__
base.STUDY = Path("docs/experiments") / NAME
base.STUDY_DIR = base.ENGINEERING / base.STUDY
base.RUNTIME = Path("/mnt/home/gewang/code/ASPIRE-" + NAME)
base.PARENT = Path("/mnt/home/gewang/experiments") / NAME
base.SUPPORT = base.STUDY_DIR / "support"
base.BASELINE_CONTRACT = base.STUDY_DIR / "baseline-contract.json"
base.CAMPAIGN_PLAN = base.STUDY_DIR / "campaign-plan.json"
base.EXPECTED_PROMPTS = base.STUDY_DIR / "reference/expected-baseline-prompts.json"
base.INTERFACE_DOC = base.STUDY / "NATIVE_WORLD_INTERFACE.md"
base.C_OVERLAY += ("cap/world_model/executable_world.py", "scripts/libero/executable_world_profile.py",
                   "cap/world_model/world_use_audit.py")
base.SUPPORT_FILES = ("legacy_stager.py", "run_cell.py", "cell_read_guard.py", "native_lineage_r2.py",
                      "lineage.py", "native_cc_stream.py", "infra_guard.py", "evaluate.py")
ARMS = ("full", "no_self_eval", "no_rehearsal")
LANES = {task: {"gpu": gpu, "cells": [f"{task}_C_{arm}" for arm in ARMS]}
         for task, gpu in (("bowl", 2), ("drawer", 3))}
base.CELLS = tuple((cell, "C") for lane in LANES.values() for cell in lane["cells"])
original_case = base.case_config
original_stage = base.stage_cell
original_external = base.external_inputs


def case_config(cell, condition):
    case = original_case(cell, condition)
    task, arm = cell.split("_C_", 1)
    gpu = LANES[task]["gpu"]
    case.update(gpu=gpu, cuda_visible_devices=str(gpu), egl_device_id=gpu,
                model_tag="claude-opus-4-6-cc[1m]", executable_world_revision="r1", c_arm=arm,
                c_starter=str(base.STUDY / "cells" / cell / "prior_c"),
                generation_context=f"native Opus4.6 C repair continuation {task}/{arm}; development51-65 only",
                api_key_helper="/mnt/home/gewang/.local/bin/claude-jkwl-api-key",
                inference_endpoint="https://jkwl.dmxapi.cn")
    return case


def make_starter(task):
    """Explicit allowlist; no copying broad output trees or held-out evidence."""
    prior = json.loads((OLD_RESULTS / f"{task}_C1/case.json").read_text())
    root = Path(prior["sim"]) / "outputs/libero_fix_loop" / prior["suite"] / prior["task"]
    ledger = json.loads((root / "development_state.json").read_text())
    target = base.STUDY_DIR / "starters" / task
    target.mkdir(parents=True, exist_ok=False)
    selected = ledger["selected"]["bundle"]
    for name, role in (("fix_code.py", "policy"), ("fix_world_program.py", "world")):
        if base.digest(root / name) != selected[role]:
            raise ValueError("prior selected C source changed")
        shutil.copy2(root / name, target / name)
    rows = []
    latest = {}
    for row in ledger["trials"]:
        if not row.get("charged"):
            continue
        if row["seed"] not in range(51, 66):
            raise ValueError("starter includes a non-development seed")
        if Path(row["directory"]).parts[0] != "development":
            raise ValueError("invalid development evidence path")
        rows.append({k: row.get(k) for k in ("phase", "seed", "attempt", "status", "executed",
                                            "bundle", "bundle_sha256", "task_completed", "sandbox_rc")})
        if row.get("bundle") == selected and row.get("executed"):
            latest[row["seed"]] = row
    for seed, row in sorted(latest.items()):
        src = root / row["directory"]
        dest = target / "development" / f"seed_{seed}"
        dest.mkdir(parents=True)
        for name in ("code.py", "world_program.py", "replay.log", "judgment_world/events.jsonl"):
            if (src / name).is_file():
                new = dest / Path(name).name
                shutil.copy2(src / name, new)
        traces = list((src / "results").rglob("trace.json"))
        if len(traces) == 1:
            shutil.copy2(traces[0], dest / "trace.json")
    summary = {"study_kind": "declared prior C development-only continuation input",
               "prior_cell": f"{task}_C1", "selected_bundle": selected,
               "charged_attempts_retained_in_prior_study": len(rows),
               "selected_bundle_pass_seeds": sorted({r["seed"] for r in rows if r["bundle"] == selected and r["task_completed"]}),
               "trials": rows, "copied_seed_traces": sorted(latest),
               "excluded": ["held-out data", "A or B programs", "generated narrative selection claims", "other tasks"]}
    base.record(target / "development_summary.json", summary)
    (target / "README.md").write_text(
        "# Prior C repair input\n\nThese are this task's prior C programs and development51–65 evidence only.\n"
        "All three arms start from these identical bytes. Repair both programs for the new interface.\n"
        "The ledger-derived summary, not any old narrative success count, is authoritative.\n"
        "The original programs discard post-grasp observations and may use commands as observed object states.\n"
        "Check their actual data flow, consume fresh public observations, and use uncertainty honestly.\n"
        "Do not assume geometry/IK explanations in old code comments are proven.\n")
    files = {str(p.relative_to(target)): base.digest(p) for p in base.tree_files(target)}
    base.record(base.STUDY_DIR / "coordination" / f"starter-{task}.json",
                {"source_cell": prior["id"], "source_development_ledger_sha256": base.digest(root / "development_state.json"),
                 "selected_bundle": selected, "files": files})
    return files


def stage_cell(cell, condition, contract):
    case, changes = original_stage(cell, condition, contract)
    sim = base.cell_sim(cell)
    task = cell.split("_C_", 1)[0]
    starter = base.STUDY_DIR / "starters" / task
    target = sim / case["c_starter"]
    shutil.copytree(starter, target)
    for path in base.tree_files(target):
        changes["added"][str(path.relative_to(sim))] = base.digest(path)
    # C has no high-level MD, including the otherwise discoverable shared copies.
    for p in (sim / ".claude/libero/skills").glob("*.md"):
        changes["removed"].append({"path": str(p.relative_to(sim)), "source_sha256": base.digest(p),
                                   "reason": "no high-level strategy MD in all C repair arms"})
        p.unlink()
    changes["starter"] = {"source": str(starter), "tree_sha256": base.tree_digest(target)[0]}
    return case, changes


def external_inputs(case):
    return original_external(case) + base.tree_files(base.cell_sim(case["id"]) / case["c_starter"])


base.case_config = case_config
base.stage_cell = stage_cell
base.external_inputs = external_inputs


def prepare():
    if base.PARENT.exists() or base.RUNTIME.exists() or base.CAMPAIGN_PLAN.exists():
        raise ValueError("study already prepared or partially staged; preserve and inspect")
    shutil.copy2(OLD / "baseline-contract.json", base.BASELINE_CONTRACT)
    starters = {task: make_starter(task) for task in LANES}
    plan = {"study_id": NAME, "status": "prepared_not_launched", "kind": "C repair continuation and mechanism ablation",
            "baseline_contract_sha256": base.digest(base.BASELINE_CONTRACT),
            "cells": [{"id": cell, "condition": "C", "profile": "judgment", "task": base.TASKS[cell.split('_')[0]],
                       "arm": cell.split("_C_", 1)[1]} for cell, _ in base.CELLS],
            "lanes": LANES, "starters": starters, "development_seeds": list(range(51, 66)),
            "evaluation_seeds": list(range(1, 51)), "evaluation_set": "seen_regression_set",
            "max_charged_attempts_per_dev_seed": 3, "max_development_executions": 270,
            "max_evaluation_executions": 300, "no_A_launch": True,
            "experiment_model": {"request": "claude-opus-4-6-cc[1m]", "served": "claude-opus-4-6",
                                 "effort": "high", "context_tokens": 1000000, "max_output_tokens": 64000}}
    base.record(base.CAMPAIGN_PLAN, plan)
    base.record(base.EXPECTED_PROMPTS, {})  # No A prompts or new A cases in this study.
    result = base.prepare([cell for cell, _ in base.CELLS])
    print(json.dumps({"prepared_at": result["prepared_at"], "cells": list(result["cells"]), "lanes": LANES}))


def queue(lane):
    receipt = json.loads((base.PARENT / "prepare-receipt.json").read_text())
    if base.digest(Path(__file__)) != receipt["queue_script_sha256"]:
        raise ValueError("frozen queue changed")
    lock = (base.PARENT / f"queue-{lane}.lock").open("a")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    path = base.PARENT / f"queue-{lane}-state.json"
    if path.exists():
        raise ValueError("lane already started; do not overwrite its state")
    state = {"status": "running", "pid": os.getpid(), "started_at": base.now(), "cells": {}}
    base.record(path, state)
    for cell in LANES[lane]["cells"]:
        case = base.verify_cell(receipt["cells"][cell], receipt["frozen_support"])
        state["cells"][cell] = {"status": "running", "started_at": base.now()}
        base._write(path, state)
        result = base.run_cell(case, Path(receipt["cells"][cell]["case"]), base.PARENT / f"queue-{lane}.log")
        state["cells"][cell] = result
        base._write(path, state)
        if result["status"] != "complete":
            state["status"] = "blocked"
            break
    else:
        state["status"] = "complete"
    state["finished_at"] = base.now()
    base._write(path, state)
    return int(state["status"] != "complete")


def launch(lane):
    receipt = json.loads((base.PARENT / "prepare-receipt.json").read_text())
    frozen = Path(receipt["queue_script"])
    if base.digest(frozen) != receipt["queue_script_sha256"]:
        raise ValueError("queue changed after prepare")
    path = base.PARENT / f"launch-{lane}.json"
    if path.exists():
        raise ValueError("lane already launched")
    for cell in LANES[lane]["cells"]:
        base.verify_cell(receipt["cells"][cell], receipt["frozen_support"])
    command = [str(base.VENV / "bin/python3"), str(frozen), "--queue", "--lane", lane]
    with (base.PARENT / f"queue-{lane}.log").open("x") as log:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log,
                                   stderr=subprocess.STDOUT, start_new_session=True)
    result = {"pid": process.pid, "started_at": base.now(), "command": command, **LANES[lane]}
    base.record(path, result)
    print(json.dumps(result))


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare", action="store_true")
    mode.add_argument("--launch", action="store_true")
    mode.add_argument("--queue", action="store_true")
    p.add_argument("--lane", choices=LANES)
    args = p.parse_args()
    if args.prepare:
        prepare()
    elif not args.lane:
        p.error("launch/queue require --lane")
    elif args.launch:
        launch(args.lane)
    else:
        raise SystemExit(queue(args.lane))
