#!/usr/bin/env python3
"""Stage and verify the CC_TASK_4 p4fix re-run (two fresh cells). Never launches a job.

Two cells that the two p4 protocol rules kept from a score, re-run fresh with the
fixed protocol (CC_TASK_4: no sweep-candidate cap; all-fail tie-break among
freezable candidates, advisory when only partially covered candidates look
better; generic `no_legal_action` terminal guard):

  drawer_p4_r1c               libero_goal_swap / open_the_middle_drawer_of_the_cabinet
                              (re-runs the drawer r1 position; rerun-c's drawer_p4_r1b froze 0/15
                              after the cap made its repaired e8b057f8 unsweepable)
  goaltaskbowldrawer_p4n_r1b  libero_goal_task / open_the_top_drawer_and_put_the_bowl_inside
                              (re-runs goaltaskbowldrawer_p4n_r1, stalled on the tie-break)

This is p4n's multi-suite stager (`prepare-hardest-study.py`, imported, not
copied — which itself imports p4's `prepare-coverage-study.py`) re-pointed at
two cells and fresh roots. Support = the same overlay (the CC_TASK_2 Part B
driver from `<p4 study>/support`, unchanged); launch = the current shared
`scripts/common/node_layout/` (unchanged since p4n); runtime overlay = the
current worktree, whose only differences from p4n are the CC_TASK_4 protocol
files. Budgets, seeds, deadlines, model, effort, env config: p4's.

  prepare-p4fix-study.py --prepare
  prepare-p4fix-study.py --verify
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
SIMPATH = HERE.parents[2]
P4N_DIR = SIMPATH / "docs/experiments/code-world-hardest-p4n-20261018"
P4N = Path("/mnt/home/gewang/experiments/code-world-hardest-p4n-20261018")
P4N_RUNTIME = Path("/mnt/home/gewang/code/ASPIRE-code-world-hardest-p4n-20261018")
P4N_NAME = "code-world-hardest-p4n-20261018"
#: Every p4n cell has the same overlay bytes; this one is also B's source cell.
P4N_CELL = "goaltaskbowldrawer_p4n_r1"

NAME = "code-world-p4fix-20261018"
RUNTIME = Path("/mnt/home/gewang/code/ASPIRE-" + NAME)
PARENT = Path("/mnt/home/gewang/experiments") / NAME
STUDY_DIR = SIMPATH / "docs/experiments" / NAME

TASKS_BY_POSITION = {
    "drawer": ("libero_goal_swap", "open_the_middle_drawer_of_the_cabinet"),
    "goaltaskbowldrawer": ("libero_goal_task", "open_the_top_drawer_and_put_the_bowl_inside"),
}
CELL_ORDER = ("drawer_p4_r1c", "goaltaskbowldrawer_p4n_r1b")
#: The cell each one re-runs (the HANDOFF's A and B).
RERUN_OF = {"drawer_p4_r1c": "drawer_p4_r1b (rerun-c)", "goaltaskbowldrawer_p4n_r1b": "goaltaskbowldrawer_p4n_r1 (p4n)"}
CELL = re.compile(r"[a-z]+_p4n?_r(\d+)[a-z]?")

#: The CC_TASK_4 changes; the only runtime bytes allowed to differ from p4n.
CC_TASK_4_FILES = ("scripts/libero/native_world_fixloop_state.py", "scripts/libero/native_world_protocol.py",
                   "scripts/libero/executable_world_profile.py", "cap/world_model/search_control.py")


def load_p4n():
    spec = importlib.util.spec_from_file_location("p4fix_p4n_stager", P4N_DIR / "prepare-hardest-study.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


hs = load_p4n()
pc, p3, base = hs.pc, hs.p3, hs.base

# ---- re-point p4n's stager (and the p4 / p3 / legacy modules it drives) -------

pc.RUNTIME = p3.RUNTIME = base.RUNTIME = hs.RUNTIME = RUNTIME
pc.PARENT = p3.PARENT = base.PARENT = hs.PARENT = PARENT
pc.LAUNCH = p3.LAUNCH = PARENT / "launch"
pc.STUDY_DIR = base.STUDY_DIR = hs.STUDY_DIR = STUDY_DIR
base.STUDY = Path("docs/experiments") / NAME
base.BASELINE_CONTRACT = STUDY_DIR / "baseline-contract.json"
base.CAMPAIGN_PLAN = STUDY_DIR / "campaign-plan.json"
base.EXPECTED_PROMPTS = STUDY_DIR / "reference/expected-baseline-prompts.json"
hs.NAME = NAME
hs.TASKS_BY_POSITION = TASKS_BY_POSITION
hs.TASKS = pc.TASKS = p3.TASKS = base.TASKS = {p: t for p, (_, t) in TASKS_BY_POSITION.items()}
hs.SUITES = {p: s for p, (s, _) in TASKS_BY_POSITION.items()}
# drawer keeps its p4 arm, goaltaskbowldrawer its p4n arm; both are the p3-search contract.
pc.ARMS = p3.ARMS = {"p4": "p3-search", "p4n": "p3-search"}
p3.INTERFACE_DOCS = {arm: Path("cap/world_model/RUNTIME_INTERFACE.md") for arm in p3.ARMS}
hs.CELL_ORDER = pc.CELL_ORDER = CELL_ORDER
pc.CELLS = p3.CELLS = base.CELLS = tuple((cell, "C") for cell in CELL_ORDER)
hs.NOMINAL_NODES = {0: CELL_ORDER}
# `r1c` / `r1b`: a fresh re-run keeps its position's repeat number (prepare-rerun.py's rule).
p3.repeat_of = lambda cell: int(CELL.fullmatch(cell).group(1))

_p4n_study = pc.study


def study() -> dict:
    result = _p4n_study()
    result["note"] = ("CC_TASK_4 p4fix re-run: 2 fresh cells (drawer r1 position, goal_task bowldrawer) "
                      "with the fixed coverage protocol, one 4-GPU unit, every cell dev51-65 then frozen "
                      "heldout1-50; suite per cell")
    return result


pc.study = study

_p4n_campaign_plan = pc.campaign_plan


def campaign_plan() -> dict:
    plan = _p4n_campaign_plan()
    plan.update(kind="CC_TASK_4 p4fix fresh C re-run (p4 coverage gate with the CC_TASK_4 protocol fixes)",
                parent_study=f"{P4N_NAME} (multi-suite stager) / code-world-coverage-p4-20261016",
                rerun_of=RERUN_OF)
    plan["launch_shape"]["mode"] = "one 1x4-GPU job = one unit, two slots, one cell per slot"
    plan["cc_task_4"] = {
        "sweep_cap": "removed: any tested candidate may be swept, once per seed, held-out refused",
        "tie_break": "all-fail fewer-crashes rule compared among freezable candidates; advisory when the "
                     "selection is complete and only partially covered candidates score better",
        "terminal_guard": "errors with no admissible action -> decision_type terminal (no_legal_action)"}
    return plan


pc.campaign_plan = campaign_plan


# ---- byte identity against p4n -------------------------------------------------


def identity_vs_p4n() -> dict:
    """Every byte difference from p4n's frozen staging beyond task, suite, cell, roots, study."""
    mine = json.loads((PARENT / "prepare-receipt.json").read_text())
    theirs = json.loads((P4N / "prepare-receipt.json").read_text())
    ref_case = json.loads((P4N / P4N_CELL / "case.json").read_text())
    ref_norm = (P4N_CELL, ref_case["suite"], ref_case["task"], P4N, P4N_RUNTIME, P4N_NAME)
    rows, problems = {}, []
    for cell in CELL_ORDER:
        suite, task = TASKS_BY_POSITION[hs.position(cell)]
        own_norm = (cell, suite, task, PARENT, RUNTIME, NAME)
        overlay = sorted(n for n, h in mine["runtime_sha256"][cell].items()
                         if theirs["runtime_sha256"][P4N_CELL].get(n) != h)
        prompts = {}
        for name in ("worker-prompt.md", "coordinator-prompt.md"):
            a = hs.normalized((PARENT / cell / name).read_text(), *own_norm)
            b = hs.normalized((P4N / P4N_CELL / name).read_text(), *ref_norm)
            prompts[name] = a == b
        case = json.loads((PARENT / cell / "case.json").read_text())
        keys = sorted(k for k in set(case) | set(ref_case)
                      if hs.normalized(json.dumps(case.get(k)), *own_norm)
                      != hs.normalized(json.dumps(ref_case.get(k)), *ref_norm))
        rows[cell] = {"suite": suite, "task": task, "rerun_of": RERUN_OF[cell],
                      "overlay_differs": overlay, "prompts_equal_after_normalizing": prompts,
                      "case_keys_differ": keys}
        problems += [f"{cell}: overlay {n} differs from p4n" for n in overlay if n not in CC_TASK_4_FILES]
        problems += [f"{cell}: CC_TASK_4 file {n} is byte-identical to p4n" for n in CC_TASK_4_FILES
                     if n not in overlay]
        # The coordinator prompt never carries the contract; the worker prompt carries
        # SEARCH_CONTRACT, which CC_TASK_4 rewrote (executable_world_profile.py).
        if not prompts["coordinator-prompt.md"]:
            problems.append(f"{cell}: coordinator prompt differs from p4n beyond names/roots")
        if prompts["worker-prompt.md"]:
            problems.append(f"{cell}: worker prompt does not carry the CC_TASK_4 contract text")
        # runtime_manifest_sha256 pins a manifest naming the new root; suite is per
        # cell (drawer is goal_swap, the p4n reference cell goal_task), checked here.
        problems += [f"{cell}: case key {k} differs from p4n" for k in keys
                     if k not in ("runtime_manifest_sha256", "suite")]
        if case["suite"] != suite:
            problems.append(f"{cell}: case suite {case['suite']!r} is not {suite!r}")
    support = sorted(n for n, h in mine["frozen_support"].items() if theirs["frozen_support"].get(n) != h)
    new_launch = json.loads((PARENT / "launch/launch-manifest.json").read_text())["files"]
    old_launch = json.loads((P4N / "launch/launch-manifest.json").read_text())["files"]
    launch = sorted(n for n in set(new_launch) | set(old_launch) if new_launch.get(n) != old_launch.get(n))
    problems += [f"support {n} differs from p4n" for n in support]
    problems += [f"launch {n} differs from p4n" for n in launch if n != "study.json"]
    study_new = json.loads((PARENT / "launch/study.json").read_text())
    study_old = json.loads((P4N / "launch/study.json").read_text())
    study_keys = sorted(k for k in set(study_new) | set(study_old) if study_new.get(k) != study_old.get(k))
    problems += [f"study.json key {k} differs from p4n" for k in study_keys if k not in ("cells", "note")]
    worker_diff = worker_prompt_diff()
    return {"reference": f"{P4N}/{P4N_CELL}", "cells": rows, "support_differs": support,
            "launch_differs": launch, "study_json_keys_differ": study_keys,
            "worker_prompt_diff_vs_p4n": worker_diff,
            "allowed_overlay_differences": list(CC_TASK_4_FILES),
            "normalized": "cell name, suite, task, result/runtime roots, study name, slot GPU line",
            "problems": problems}


def worker_prompt_diff() -> list[str]:
    """The normalized worker-prompt lines that differ from p4n (B's cell against its source)."""
    import difflib
    cell = "goaltaskbowldrawer_p4n_r1b"
    suite, task = TASKS_BY_POSITION["goaltaskbowldrawer"]
    ref_case = json.loads((P4N / P4N_CELL / "case.json").read_text())
    a = hs.normalized((P4N / P4N_CELL / "worker-prompt.md").read_text(),
                      P4N_CELL, ref_case["suite"], ref_case["task"], P4N, P4N_RUNTIME, P4N_NAME)
    b = hs.normalized((PARENT / cell / "worker-prompt.md").read_text(),
                      cell, suite, task, PARENT, RUNTIME, NAME)
    return [line for line in difflib.unified_diff(a.splitlines(), b.splitlines(), "p4n", "p4fix", n=0, lineterm="")
            if line[:1] in "+-" and not line.startswith(("+++", "---"))]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare", action="store_true", help="stage the 2 cells; start nothing")
    mode.add_argument("--verify", action="store_true", help="re-verify, then compare against p4n")
    args = parser.parse_args(argv)
    if args.prepare:
        receipt = pc.prepare()
        frozen = base.freeze_bytes(Path(__file__), PARENT / "frozen-prepare-p4fix-study.py")
        base.record(PARENT / "p4fix-receipt.json", {
            "name": NAME, "runtime_root": str(RUNTIME), "result_parent": str(PARENT),
            "study_dir": str(STUDY_DIR),
            "cells": {c: {"suite": hs.SUITES[hs.position(c)], "task": hs.TASKS[hs.position(c)],
                          "rerun_of": RERUN_OF[c]} for c in CELL_ORDER},
            "support_overlay": {n: str(p) for n, p in hs.OVERLAY.items()},
            "stager_sha256": frozen, "p4n_stager": str(P4N_DIR / "prepare-hardest-study.py"),
            "prepared_at": receipt["prepared_at"]})
        return 0
    result = pc.verify()
    same = identity_vs_p4n()
    print(json.dumps({"identity_vs_p4n": same}, indent=2))
    return 0 if result["status"] == "verified" and not same["problems"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
