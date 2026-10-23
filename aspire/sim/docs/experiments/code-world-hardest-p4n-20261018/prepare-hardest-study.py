#!/usr/bin/env python3
"""Stage and verify the p4n hardest-first study (CC_TASK_3). Never launches a job.

Eight fresh C cells, one repeat each, on the hardest LIBERO-Pro tasks outside
libero_goal_swap (CC_TASK_3.md, 协调端更正 2026-10-18 01:00): four libero_10_swap
long-horizon tasks, two libero_spatial_swap relational-selection tasks, the
libero_goal_task rewording of bowldrawer and libero_object_swap alphabet_soup.

"Our current method" = the pipeline rerun-b / rerun-c ran: p4's
`prepare-coverage-study.py` (imported, not copied) with the CC_TASK_2 Part B
driver frozen from `<p4 study>/support` exactly as `prepare-rerun.py
--support-overlay` does, and the current shared launch layer
(`scripts/common/node_layout/`). Budgets, seeds (dev 51-65 x 3, held-out 1-50),
deadlines, model, effort and the env config are p4's.

The one adaptation is the suite. p4 inherits `suite = libero_goal_swap` from the
legacy stager (`SUITE`) and p3's verifier (`COMMON_EXPECTED["suite"]`); here it is
per cell. Everything downstream (driver, protocol, world, held-out runner,
supervisor, guards) already reads `case["suite"]`, so no shared-layer byte changes.

  prepare-hardest-study.py --prepare
  prepare-hardest-study.py --verify
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
SIMPATH = HERE.parents[2]
P4 = SIMPATH / "docs/experiments/code-world-coverage-p4-20261016"
RERUN_C = Path("/mnt/home/gewang/experiments/code-world-coverage-p4-20261016-rerun-c")
RERUN_C_RUNTIME = Path("/mnt/home/gewang/code/ASPIRE-code-world-coverage-p4-20261016-rerun-c")
RERUN_C_CELL = "drawer_p4_r1b"

NAME = "code-world-hardest-p4n-20261018"
RUNTIME = Path("/mnt/home/gewang/code/ASPIRE-" + NAME)
PARENT = Path("/mnt/home/gewang/experiments") / NAME
STUDY_DIR = SIMPATH / "docs/experiments" / NAME

#: position -> (suite, task). Task names are the benchmark's `task.name`
#: (bddl stem); the goal text the worker sees is the runtime task_language.
TASKS_BY_POSITION = {
    "mokastove": ("libero_10_swap", "KITCHEN_SCENE3_turn_on_the_stove_and_put_the_moka_pot_on_it"),
    "bowldrawerclose": ("libero_10_swap",
                        "KITCHEN_SCENE4_put_the_black_bowl_in_the_bottom_drawer_of_the_cabinet_and_close_it"),
    "mugmicrowave": ("libero_10_swap",
                     "KITCHEN_SCENE6_put_the_yellow_and_white_mug_in_the_microwave_and_close_it"),
    "twomoka": ("libero_10_swap", "KITCHEN_SCENE8_put_both_moka_pots_on_the_stove"),
    "spatialdrawer": ("libero_spatial_swap",
                      "pick_up_the_black_bowl_in_the_top_drawer_of_the_wooden_cabinet_and_place_it_on_the_plate"),
    "spatialbetween": ("libero_spatial_swap",
                       "pick_up_the_black_bowl_between_the_plate_and_the_ramekin_and_place_it_on_the_plate"),
    "goaltaskbowldrawer": ("libero_goal_task", "open_the_top_drawer_and_put_the_bowl_inside"),
    "objswapsoup": ("libero_object_swap", "pick_up_the_alphabet_soup_and_place_it_in_the_basket"),
}
ARM = "p4n"
#: Claim order = the coordinator's table order. With 8 cells on 8 slots no slot
#: runs a second cell; the per-node lists in campaign-plan.json are nominal.
CELL_ORDER = tuple(f"{position}_{ARM}_r1" for position in TASKS_BY_POSITION)
TASKS = {position: task for position, (_, task) in TASKS_BY_POSITION.items()}
SUITES = {position: suite for position, (suite, _) in TASKS_BY_POSITION.items()}
#: Nominal spread: libero_10 split evenly over the two nodes (rolling claim order
#: alternates node0/node1 slot readiness; nothing is pinned).
NOMINAL_NODES = {0: CELL_ORDER[0::2], 1: CELL_ORDER[1::2]}


def position(cell: str) -> str:
    return cell.split("_")[0]


def load_p4():
    spec = importlib.util.spec_from_file_location("p4n_p4_stager", P4 / "prepare-coverage-study.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pc = load_p4()
p3, base, layout = pc.p3, pc.base, pc.layout

# ---- re-point p4 (and the p3 / legacy modules it drives) at this study -------

pc.RUNTIME = p3.RUNTIME = base.RUNTIME = RUNTIME
pc.PARENT = p3.PARENT = base.PARENT = PARENT
pc.LAUNCH = p3.LAUNCH = PARENT / "launch"
pc.STUDY_DIR = base.STUDY_DIR = STUDY_DIR
base.STUDY = Path("docs/experiments") / NAME
base.BASELINE_CONTRACT = STUDY_DIR / "baseline-contract.json"
base.CAMPAIGN_PLAN = STUDY_DIR / "campaign-plan.json"
base.EXPECTED_PROMPTS = STUDY_DIR / "reference/expected-baseline-prompts.json"
pc.TASKS = p3.TASKS = base.TASKS = TASKS
pc.ARMS = p3.ARMS = {ARM: "p3-search"}
p3.INTERFACE_DOCS = {ARM: Path("cap/world_model/RUNTIME_INTERFACE.md")}
pc.CELL_ORDER = CELL_ORDER
pc.CELLS = p3.CELLS = base.CELLS = tuple((cell, "C") for cell in CELL_ORDER)

# Support: rerun-b/rerun-c's overlay — the Part B driver from <p4 study>/support,
# every other support file from p3 (prepare-rerun.py::repoint, same rule).
OVERLAY = {p.name: p.resolve() for p in (P4 / "support").iterdir()
           if p.is_file() and p.name in base.SUPPORT_FILES}


def freeze_support():
    return {name: base.freeze_bytes(OVERLAY.get(name, base.SUPPORT / name), PARENT / "support" / name)
            for name in base.SUPPORT_FILES}


base.freeze_support = freeze_support

# Suite: per cell instead of the legacy module constant.
_p4_case_config = pc.case_config


def case_config(cell, condition):
    case = _p4_case_config(cell, condition)
    case["suite"] = SUITES[position(cell)]
    return case


pc.case_config = p3.case_config = base.case_config = case_config

_p3_check_case = p3.check_case


def check_case(cell, case):
    """p3's case gate with this cell's suite as the expected value."""
    saved = p3.COMMON_EXPECTED
    p3.COMMON_EXPECTED = {**saved, "suite": SUITES[position(cell)]}
    try:
        return _p3_check_case(cell, case)
    finally:
        p3.COMMON_EXPECTED = saved


p3.check_case = check_case

_p4_study = pc.study


def study() -> dict:
    result = _p4_study()
    result["note"] = ("p4n hardest-first study on the locked node layout (p4 pipeline + CC_TASK_2 Part B "
                      "driver): 8 cells over 2 x 8-GPU nodes, shared claim queue, every cell dev51-65 "
                      "then frozen heldout1-50; suite per cell")
    return result


pc.study = study

_p4_campaign_plan = pc.campaign_plan


def campaign_plan() -> dict:
    plan = _p4_campaign_plan()
    for entry in plan["cells"]:
        entry["suite"] = SUITES[position(entry["id"])]
    plan.update(
        study_id=NAME,
        kind="p4n hardest-first fresh C study (p4 coverage gate + Part B driver, multi-suite)",
        parent_study="code-world-coverage-p4-20261016 (rerun-b / rerun-c pipeline)",
        tasks={p: {"suite": s, "task": t} for p, (s, t) in TASKS_BY_POSITION.items()},
        claim_order=list(CELL_ORDER),
        nominal_nodes={str(k): list(v) for k, v in NOMINAL_NODES.items()})
    plan["launch_shape"]["mode"] = ("shared claim queue served by two independent 1x8-GPU jobs "
                                    "(two units per node, 4 slots per node)")
    plan["multi_suite"] = {
        "suite_source": "case['suite'] per cell (this stager); every downstream consumer reads the case",
        "env_config": "env_configs/libero/franka_libero_traced.yaml for every cell (unit FAMILY_KEYS "
                      "require one env_config per queue); on the replay-code path replay_trial "
                      "overwrites suite/task_id/max_steps/output_dir/record_video, so the libero10 "
                      "traced config differs only in fields that path does not read; see env-check",
        "goal_text": "replay_trial fills Goal from env.handle.task_language; the snapshot prints "
                     "TASK_LANGUAGE and task-exploration.md says task language overrides the filename"}
    return plan


pc.campaign_plan = campaign_plan


# ---- identity against rerun-c --------------------------------------------------


SLOT_GPU = re.compile(r"(GPU:\s+|physical GPU )\d+")


def normalized(text: str, cell: str, suite: str, task: str, parent: Path, runtime: Path, study: str) -> str:
    text = (text.replace(str(parent), "<PARENT>").replace(str(runtime), "<RUNTIME>")
                .replace(f"{suite}/{task}", "<SUITE>/<TASK>").replace(f"{suite}_{task}", "<SUITE>_<TASK>")
                .replace(f"SUITE: {suite}", "SUITE: <SUITE>").replace(f"suite: {suite}", "suite: <SUITE>")
                .replace(task, "<TASK>").replace(cell, "<CELL>").replace(study, "<STUDY>"))
    return SLOT_GPU.sub(r"\1<slot>", text)


def identity_vs_rerun_c() -> dict:
    """What differs from rerun-c's frozen staging beyond task, suite, cell name, roots, study."""
    mine = json.loads((PARENT / "prepare-receipt.json").read_text())
    theirs = json.loads((RERUN_C / "prepare-receipt.json").read_text())
    ref_case = json.loads((RERUN_C / RERUN_C_CELL / "case.json").read_text())
    ref_norm = (RERUN_C_CELL, ref_case["suite"], ref_case["task"], RERUN_C, RERUN_C_RUNTIME,
                "code-world-coverage-p4-20261016")
    rows, problems = {}, []
    for cell in CELL_ORDER:
        suite, task = SUITES[position(cell)], TASKS[position(cell)]
        own_norm = (cell, suite, task, PARENT, RUNTIME, NAME)
        overlay = sorted(n for n, h in mine["runtime_sha256"][cell].items()
                         if theirs["runtime_sha256"][RERUN_C_CELL].get(n) != h)
        prompts = {}
        for name in ("worker-prompt.md", "coordinator-prompt.md"):
            a = normalized((PARENT / cell / name).read_text(), *own_norm)
            b = normalized((RERUN_C / RERUN_C_CELL / name).read_text(), *ref_norm)
            prompts[name] = a == b
        case = json.loads((PARENT / cell / "case.json").read_text())
        keys = sorted(k for k in set(case) | set(ref_case)
                      if normalized(json.dumps(case.get(k)), *own_norm)
                      != normalized(json.dumps(ref_case.get(k)), *ref_norm))
        rows[cell] = {"suite": suite, "task": task, "overlay_differs": overlay,
                      "prompts_equal_after_normalizing": prompts, "case_keys_differ": keys}
        problems += [f"{cell}: overlay {n} differs from rerun-c" for n in overlay]
        problems += [f"{cell}: {n} differs from rerun-c beyond task/suite/cell/roots/study"
                     for n, same in prompts.items() if not same]
        # runtime_manifest_sha256 pins a manifest whose paths name the new root;
        # suite is this study's one adaptation (checked against SUITES by p3.check_case).
        problems += [f"{cell}: case key {k} differs from rerun-c" for k in keys
                     if k not in ("runtime_manifest_sha256", "suite")]
        if case["suite"] != suite:
            problems.append(f"{cell}: case suite {case['suite']!r} is not {suite!r}")
    support = sorted(n for n, h in mine["frozen_support"].items() if theirs["frozen_support"].get(n) != h)
    new_launch = json.loads((PARENT / "launch/launch-manifest.json").read_text())["files"]
    old_launch = json.loads((RERUN_C / "launch/launch-manifest.json").read_text())["files"]
    launch = sorted(n for n in set(new_launch) | set(old_launch) if new_launch.get(n) != old_launch.get(n))
    problems += [f"support {n} differs from rerun-c" for n in support]
    problems += [f"launch {n} differs from rerun-c" for n in launch if n != "study.json"]
    study_new = json.loads((PARENT / "launch/study.json").read_text())
    study_old = json.loads((RERUN_C / "launch/study.json").read_text())
    study_keys = sorted(k for k in set(study_new) | set(study_old) if study_new.get(k) != study_old.get(k))
    problems += [f"study.json key {k} differs from rerun-c" for k in study_keys if k not in ("cells", "note")]
    return {"reference": f"{RERUN_C}/{RERUN_C_CELL}", "cells": rows, "support_differs": support,
            "launch_differs": launch, "study_json_keys_differ": study_keys,
            "normalized": "cell name, suite, task, result/runtime roots, study name, slot GPU line",
            "problems": problems}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare", action="store_true", help="stage the 8 cells; start nothing")
    mode.add_argument("--verify", action="store_true", help="re-verify, then compare against rerun-c")
    args = parser.parse_args(argv)
    if args.prepare:
        receipt = pc.prepare()
        frozen = base.freeze_bytes(Path(__file__), PARENT / "frozen-prepare-hardest-study.py")
        base.record(PARENT / "p4n-receipt.json", {
            "name": NAME, "runtime_root": str(RUNTIME), "result_parent": str(PARENT),
            "study_dir": str(STUDY_DIR), "cells": {c: {"suite": SUITES[position(c)], "task": TASKS[position(c)]}
                                                  for c in CELL_ORDER},
            "support_overlay": {n: str(p) for n, p in OVERLAY.items()},
            "stager_sha256": frozen, "p4_stager": str(P4 / "prepare-coverage-study.py"),
            "prepared_at": receipt["prepared_at"]})
        return 0
    result = pc.verify()
    same = identity_vs_rerun_c()
    print(json.dumps({"identity_vs_rerun_c": same}, indent=2))
    return 0 if result["status"] == "verified" and not same["problems"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
