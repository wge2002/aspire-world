#!/usr/bin/env python3
"""Stage and verify the CC_TASK_6 p5 study (p4 method + fixes + ASPIRE v1.0 budget). Never launches a job.

p5 = the p4 method as it stands after CC_TASK_2 (driver terminal stops, sweep
counted apart, supervisor teardown/requeue), CC_TASK_4 (no sweep cap, freezable
tie-break, no_legal_action guard) and CC_TASK_5 (derived held-out watchdog,
held-out supplement path), plus the ASPIRE v1.0 development budget
(`development_budget: "aspire-v1"`, CC_TASK_6):

  phase A  until seed 51 passes: every attempt on seed 51, uncapped, no sweep /
           select / freeze, never terminal (only the 24 h development deadline);
  phase B  v0 (first pass) swept free; <= 8 versions, each on all 15 seeds,
           each declaring its failure mode, <= 3 per mode (then blocked);
  freeze   15/15, or 8 versions used, or every remaining failure in a blocked mode;
  probes   observation-only REPL sessions, never versions, spend nothing.

This is p4n's multi-suite stager (`prepare-hardest-study.py`, imported, not
copied — it imports p4's `prepare-coverage-study.py`, which imports p3's) re-pointed
at the p5 cells and roots, the way `code-world-p4fix-20261018/prepare-p4fix-study.py`
did. The only additions over p4n's staging:

  * case: `development_budget = "aspire-v1"`, `campaign_timeout` = the p5 24 h
    development watchdog (one solver turn may not be cut before the phase ends);
  * support: p3 support, overlaid by `<p4 study>/support` (full_deadlines,
    run_two_task_cell, heldout_supplement — added to SUPPORT_FILES), overlaid by
    `<p5 study>/support` (full_deadlines 24 h, run_two_task_cell budget text,
    infra_guard budget rule);
  * launch: the current shared `scripts/common/node_layout/` (CC_TASK_5 + the
    CC_TASK_6 `node_cells` pinning); study.json carries `node_cells` and expects
    `development_budget`.

  prepare-budget-study.py --prepare            stage the 12 p5 cells (3 nodes)
  prepare-budget-study.py --verify             re-verify, then byte diff against p4n
  prepare-budget-study.py --smoke --prepare    stage the 1-cell native_smoke root
  prepare-budget-study.py --smoke --verify
"""
from __future__ import annotations

import argparse
import difflib
import importlib.util
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SIMPATH = HERE.parents[2]
P4 = SIMPATH / "docs/experiments/code-world-coverage-p4-20261016"
P4N_DIR = SIMPATH / "docs/experiments/code-world-hardest-p4n-20261018"
P4N = Path("/mnt/home/gewang/experiments/code-world-hardest-p4n-20261018")
P4N_RUNTIME = Path("/mnt/home/gewang/code/ASPIRE-code-world-hardest-p4n-20261018")
P4N_NAME = "code-world-hardest-p4n-20261018"
#: Every p4n cell carries the same overlay bytes; this one is the goal-family reference.
P4N_REFERENCE = "goaltaskbowldrawer_p4n_r1"

NAME = "code-world-budget-p5-20261019"
SMOKE_NAME = NAME + "-smoke"
STUDY_DIR = SIMPATH / "docs/experiments" / NAME
P5_SUPPORT = STUDY_DIR / "support"
BUDGET = "aspire-v1"
ARM = "p5"

#: position -> (suite, task): each p4 / p4n / p4fix cell's own case.
TASKS_BY_POSITION = {
    "drawer": ("libero_goal_swap", "open_the_middle_drawer_of_the_cabinet"),
    "bowl": ("libero_goal_swap", "put_the_bowl_on_the_plate"),
    "bowldrawer": ("libero_goal_swap", "open_the_top_drawer_and_put_the_bowl_inside"),
    "goaltaskbowldrawer": ("libero_goal_task", "open_the_top_drawer_and_put_the_bowl_inside"),
    "spatialdrawer": ("libero_spatial_swap",
                      "pick_up_the_black_bowl_in_the_top_drawer_of_the_wooden_cabinet_and_place_it_on_the_plate"),
    "spatialbetween": ("libero_spatial_swap",
                       "pick_up_the_black_bowl_between_the_plate_and_the_ramekin_and_place_it_on_the_plate"),
    "objswapsoup": ("libero_object_swap", "pick_up_the_alphabet_soup_and_place_it_in_the_basket"),
    "bowldrawerclose": ("libero_10_swap",
                        "KITCHEN_SCENE4_put_the_black_bowl_in_the_bottom_drawer_of_the_cabinet_and_close_it"),
    "mugmicrowave": ("libero_10_swap",
                     "KITCHEN_SCENE6_put_the_yellow_and_white_mug_in_the_microwave_and_close_it"),
    "twomoka": ("libero_10_swap", "KITCHEN_SCENE8_put_both_moka_pots_on_the_stove"),
    "mokastove": ("libero_10_swap", "KITCHEN_SCENE3_turn_on_the_stove_and_put_the_moka_pot_on_it"),
}
#: Node assignment, by expected duration (CC_TASK_6: let the nodes holding short
#: tasks finish early and release their cards). The dominant term of a cell under
#: aspire-v1 is trial time x trials (phase B: up to 8 versions x 15 seeds; held-out
#: 50 seeds), so cells are grouped by observed seconds per held-out seed of the same
#: task in p4 / p4n / p4fix (held-out elapsed / seeds):
#:   node 0 — the four libero_10 cells (single trial median 6-9 min; twomoka ~440 s,
#:            mugmicrowave ~224 s, bowldrawerclose ~222 s, mokastove blocked at the
#:            14 h development deadline in p4n);
#:   node 1 — the slowest of the rest: spatialbetween ~400 s, bowl ~329 s,
#:            objswapsoup ~325 s, goaltaskbowldrawer ~264 s;
#:   node 2 — the fastest: drawer ~118-201 s (x2), spatialdrawer ~160 s, bowldrawer ~265 s.
#: Within a node the claim order is the list order (longest first).
NODE_CELLS = {
    0: ("twomoka_p5_r1", "mokastove_p5_r1", "mugmicrowave_p5_r1", "bowldrawerclose_p5_r1"),
    1: ("spatialbetween_p5_r1", "bowl_p5_r1", "objswapsoup_p5_r1", "goaltaskbowldrawer_p5_r1"),
    2: ("bowldrawer_p5_r1", "drawer_p5_r1", "drawer_p5_r2", "spatialdrawer_p5_r1"),
}
CELL_ORDER = tuple(cell for node in sorted(NODE_CELLS) for cell in NODE_CELLS[node])
#: The source cell each p5 cell takes its suite/task from (fresh: no ledger is inherited).
CASE_OF = {
    "drawer_p5_r1": "drawer_p4_r1c (p4fix) / drawer_p4_r1 (p4)",
    "drawer_p5_r2": "drawer_p4_r2 (p4)",
    "bowl_p5_r1": "bowl_p4_r1b (rerun-a) / bowl_p4_r1 (p4)",
    "bowldrawer_p5_r1": "bowldrawer_p4_r1b (rerun-b) / bowldrawer_p4_r1 (p4)",
    "goaltaskbowldrawer_p5_r1": "goaltaskbowldrawer_p4n_r1b (p4fix)",
    "spatialdrawer_p5_r1": "spatialdrawer_p4n_r1 (p4n)",
    "spatialbetween_p5_r1": "spatialbetween_p4n_r1 (p4n)",
    "objswapsoup_p5_r1": "objswapsoup_p4n_r1 (p4n)",
    "bowldrawerclose_p5_r1": "bowldrawerclose_p4n_r1 (p4n)",
    "mugmicrowave_p5_r1": "mugmicrowave_p4n_r1 (p4n)",
    "twomoka_p5_r1": "twomoka_p4n_r1 (p4n)",
    "mokastove_p5_r1": "mokastove_p4n_r1 (p4n)",
}
#: The native smoke root: one drawer cell, the default unit-0 ports (a DLC worker).
SMOKE_CELL = "drawer_p5_smoke1"
SMOKE_DEADLINE = 3000
CELL = re.compile(r"([a-z]+)_p5_(?:r(\d+)|smoke(\d+))")

#: The runtime bytes allowed to differ from p4n: CC_TASK_4's protocol fixes and CC_TASK_6.
CC_TASK_4_FILES = ("scripts/libero/native_world_fixloop_state.py", "scripts/libero/native_world_protocol.py",
                   "scripts/libero/executable_world_profile.py", "cap/world_model/search_control.py")
CC_TASK_6_FILES = CC_TASK_4_FILES + ("scripts/libero/native_world_campaign.py", "scripts/libero/replay_trial.py")
#: Frozen support / launch files allowed to differ from p4n, with the task that changed them.
SUPPORT_DIFF = {"full_deadlines.py": "CC_TASK_5 derived held-out watchdog; CC_TASK_6 DEVELOPMENT 24 h",
                "run_two_task_cell.py": "CC_TASK_5 derived deadline + supplement; CC_TASK_6 budget text",
                "heldout_supplement.py": "CC_TASK_5 (new file, added to SUPPORT_FILES)",
                "infra_guard.py": "CC_TASK_6 budget-aware 'no graded evidence' rule"}
LAUNCH_DIFF = {"study.json": "cells, node_cells, expect.development_budget, required_support, note",
               "unit_supervisor.py": "CC_TASK_5 (supplement, derived deadline, window) + CC_TASK_6 node pinning",
               "unit_layout.py": "CC_TASK_5 SUPPLEMENT_MARKER + CC_TASK_6 node_cells"}
STUDY_KEYS_DIFF = {"cells", "note", "required_support", "expect", "node_cells", "mode", "smoke_deadline_seconds"}
#: Worker-prompt markers the budget render must carry.
P5_MARKERS = ("Development budget aspire-v1", "**Development budget (aspire-v1)**", "--phase initial --seed 51",
              "At most 3 versions per mode", "at most **8 versions**", "--phase probe",
              "attempts/mode_<mode>_BLOCKED.md", "Freeze only when one of these holds",
              "100% success is not required")
P5_GONE = ("Hard limit: 3 TOTAL retries per seed", "three total attempts per seed", "One repair round is allowed",
           "Each of these spends one of that seed's three retries")


def position(cell: str) -> str:
    return cell.split("_")[0]


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


hs = load(P4N_DIR / "prepare-hardest-study.py", "p5_p4n_stager")
pc, p3, base, layout = hs.pc, hs.p3, hs.base, hs.layout
p5_deadlines = load(P5_SUPPORT / "full_deadlines.py", "p5_full_deadlines")
# Task 5's support file, frozen like the others (the supervisor refuses supplement
# entries without it; p5 stages none, but the method's support set includes it).
base.SUPPORT_FILES = tuple(base.SUPPORT_FILES) + ("heldout_supplement.py",)
#: p3 support <- <p4>/support <- <p5>/support (the later overlay wins).
OVERLAY = {p.name: p.resolve() for p in (P4 / "support").iterdir()
           if p.is_file() and p.name in base.SUPPORT_FILES}
OVERLAY.update({p.name: p.resolve() for p in P5_SUPPORT.iterdir() if p.is_file() and p.name in base.SUPPORT_FILES})
hs.OVERLAY = OVERLAY
_p4_required_inputs = pc.required_inputs


def required_inputs():
    """p4's input list, with each overlaid support file read from its overlay."""
    support = {base.SUPPORT / name: OVERLAY[name] for name in OVERLAY}
    return [support.get(path, path) for path in _p4_required_inputs()]


pc.required_inputs = required_inputs
# Shared-layer modules the launch layer freezes and the identity check reads.
sys.path.insert(0, str(SIMPATH / "scripts/libero"))


def repoint(smoke: bool) -> dict:
    """Point p4n's stager (and the p4 / p3 / legacy modules it drives) at p5 or its smoke root."""
    name = SMOKE_NAME if smoke else NAME
    runtime = Path("/mnt/home/gewang/code/ASPIRE-" + name)
    parent = Path("/mnt/home/gewang/experiments") / name
    records = STUDY_DIR / ("smoke" if smoke else "")
    cells = (SMOKE_CELL,) if smoke else CELL_ORDER
    nodes = {0: (SMOKE_CELL,)} if smoke else NODE_CELLS
    pc.RUNTIME = p3.RUNTIME = base.RUNTIME = hs.RUNTIME = runtime
    pc.PARENT = p3.PARENT = base.PARENT = hs.PARENT = parent
    pc.LAUNCH = p3.LAUNCH = parent / "launch"
    pc.STUDY_DIR = base.STUDY_DIR = hs.STUDY_DIR = records
    base.STUDY = Path("docs/experiments") / NAME
    base.BASELINE_CONTRACT = records / "baseline-contract.json"
    base.CAMPAIGN_PLAN = records / "campaign-plan.json"
    base.EXPECTED_PROMPTS = records / "reference/expected-baseline-prompts.json"
    hs.NAME = name
    hs.TASKS_BY_POSITION = TASKS_BY_POSITION
    hs.TASKS = pc.TASKS = p3.TASKS = base.TASKS = {p: t for p, (_, t) in TASKS_BY_POSITION.items()}
    hs.SUITES = {p: s for p, (s, _) in TASKS_BY_POSITION.items()}
    pc.ARMS = p3.ARMS = {ARM: "p3-search"}
    p3.INTERFACE_DOCS = {ARM: Path("cap/world_model/RUNTIME_INTERFACE.md")}
    hs.CELL_ORDER = pc.CELL_ORDER = cells
    pc.CELLS = p3.CELLS = base.CELLS = tuple((cell, "C") for cell in cells)
    hs.NOMINAL_NODES = nodes
    p3.repeat_of = lambda cell: int(next(g for g in CELL.fullmatch(cell).groups()[1:] if g))
    return {"name": name, "runtime_root": str(runtime), "result_parent": str(parent),
            "records": str(records), "cells": list(cells), "node_cells": {str(k): list(v) for k, v in nodes.items()},
            "smoke": smoke}


# ---- case, study, plan ------------------------------------------------------------

_p4n_case_config = pc.case_config


def case_config(cell, condition):
    """p4n's case (suite per cell) plus the aspire-v1 budget and its 24 h solver-turn bound."""
    case = _p4n_case_config(cell, condition)
    case["development_budget"] = BUDGET
    case["campaign_timeout"] = p5_deadlines.DEVELOPMENT
    case["generation_context"] = (
        f"native Qwen {condition} fresh generation, {case['task']}, development gate oracle, "
        f"repeat {case['repeat']}, p3-search + p4 coverage gate + aspire-v1 development budget; "
        "dev51-65 then frozen heldout1-50")
    return case


pc.case_config = p3.case_config = base.case_config = case_config

_p4n_check_case = p3.check_case


def check_case(cell, case):
    """p4n's case gate with the p5 additions as expected values."""
    saved = p3.COMMON_EXPECTED
    p3.COMMON_EXPECTED = {**saved, "campaign_timeout": p5_deadlines.DEVELOPMENT, "development_budget": BUDGET}
    try:
        return _p4n_check_case(cell, case)
    finally:
        p3.COMMON_EXPECTED = saved


p3.check_case = check_case

_p4n_study = pc.study
SMOKE = {"on": False}


def study() -> dict:
    result = _p4n_study()
    result["expect"]["development_budget"] = BUDGET
    result["node_cells"] = {str(k): list(v) for k, v in hs.NOMINAL_NODES.items()}
    result["note"] = ("CC_TASK_6 p5 study: p4 method + CC_TASK_2/4/5 fixes + the ASPIRE v1.0 development "
                      "budget (aspire-v1); cells pinned to nodes by node_cells; every cell dev51-65 then "
                      "frozen heldout1-50; suite per cell")
    if SMOKE["on"]:
        result.update(mode="native_smoke", smoke_deadline_seconds=SMOKE_DEADLINE,
                      note=("CC_TASK_6 native smoke of the p5 shared layer (aspire-v1 protocol, budget prompts, "
                            "p5 driver/support, node pinning, supervisor teardown); not a study cell"))
    return result


pc.study = study

_p4n_campaign_plan = pc.campaign_plan


def campaign_plan() -> dict:
    plan = _p4n_campaign_plan()
    plan.update(kind="CC_TASK_6 p5 fresh C study (p4 method + fixes + ASPIRE v1.0 development budget)",
                parent_study=f"{P4N_NAME} (multi-suite stager) / code-world-p4fix-20261018 / "
                             "code-world-coverage-p4-20261016",
                case_of={c: CASE_OF.get(c, "smoke") for c in pc.CELL_ORDER},
                node_cells={str(k): list(v) for k, v in hs.NOMINAL_NODES.items()})
    plan["launch_shape"]["mode"] = ("three independent 1x8-GPU jobs, one per node; two units x two slots per "
                                    "node; cells pinned to nodes (study.json node_cells)")
    plan["development_budget"] = {
        "revision": BUDGET,
        "phase_a": "until seed 51 passes: every attempt on seed 51, uncapped; no sweep/select/freeze; never terminal",
        "phase_b": "v0 swept free; <= 8 versions, each on all 15 seeds before the next; each declares its mode; "
                   "<= 3 versions per mode, then blocked (attempts/mode_<mode>_BLOCKED.md)",
        "freeze": "15/15, or 8 versions used, or every remaining failing seed in a blocked mode; select most "
                  "passes, then fewer crashes, then simpler",
        "probes": "observation-only REPL sessions; never a version; spend nothing",
        "deadlines": p5_deadlines.summary()}
    return plan


pc.campaign_plan = campaign_plan

_p4_check_prompt = pc.check_prompt


def check_prompt(case, entry) -> dict:
    """p4's prompt gate (p3 + p4 markers) plus the p5 budget markers."""
    result = _p4_check_prompt(case, entry)
    worker = (Path(case["control"]) / "worker-prompt.md").read_text()
    coordinator = (Path(case["control"]) / "coordinator-prompt.md").read_text()
    result["p5_markers"] = list(P5_MARKERS)
    result["problems"] += [f"worker prompt lacks the p5 marker {m!r}" for m in P5_MARKERS if m not in worker]
    result["problems"] += [f"worker prompt still carries {m!r}" for m in P5_GONE if m in worker]
    if "keep the worker on the first development seed" not in coordinator:
        result["problems"].append("coordinator prompt lacks the aspire-v1 coordinator rules")
    return result


pc.check_prompt = check_prompt

_p4_check_launch = pc.check_launch


def check_launch(receipt) -> dict:
    result = _p4_check_launch(receipt)
    if SMOKE["on"]:  # p4's check insists on mode 'full'; the smoke root differs exactly there.
        result["problems"] = [p for p in result["problems"] if "expected 'full'" not in p]
    study_json = json.loads((pc.LAUNCH / "study.json").read_text())
    expected = {str(k): list(v) for k, v in hs.NOMINAL_NODES.items()}
    if study_json.get("node_cells") != expected:
        result["problems"].append(f"study.json node_cells {study_json.get('node_cells')} != {expected}")
    for node in range(len(expected)):
        layout.node_cells(study_json, [pc.PARENT / c for c in study_json["cells"]], node, len(expected))
    if study_json.get("expect", {}).get("development_budget") != BUDGET:
        result["problems"].append("study.json does not expect the aspire-v1 development budget")
    if "heldout_supplement.py" not in study_json["required_support"]:
        result["problems"].append("study.json required_support lacks heldout_supplement.py")
    result["node_cells"] = study_json.get("node_cells")
    return result


pc.check_launch = check_launch


# ---- byte identity against p4n ------------------------------------------------------


def p4n_reference(cell: str) -> str:
    """The p4n cell a p5 cell is compared against: same position if p4n ran it, else the goal reference."""
    candidate = f"{position(cell)}_p4n_r1"
    return candidate if (P4N / candidate / "case.json").is_file() else P4N_REFERENCE


def identity_vs_p4n() -> dict:
    """Every byte difference from p4n's frozen staging beyond task, suite, cell, roots, study, slot."""
    # Roots first (they carry the -smoke suffix for the smoke root), then the study
    # name the cells' notes paths use (base.STUDY is the p5 study for both).
    parent, runtime, name = pc.PARENT, pc.RUNTIME, NAME
    mine = json.loads((parent / "prepare-receipt.json").read_text())
    theirs = json.loads((P4N / "prepare-receipt.json").read_text())
    rows, problems = {}, []
    for cell in pc.CELL_ORDER:
        reference = p4n_reference(cell)
        ref_case = json.loads((P4N / reference / "case.json").read_text())
        ref_norm = (reference, ref_case["suite"], ref_case["task"], P4N, P4N_RUNTIME, P4N_NAME)
        suite, task = TASKS_BY_POSITION[position(cell)]
        own_norm = (cell, suite, task, parent, runtime, name)
        overlay = sorted(n for n, h in mine["runtime_sha256"][cell].items()
                         if theirs["runtime_sha256"][reference].get(n) != h)
        prompts = {}
        for prompt in ("worker-prompt.md", "coordinator-prompt.md"):
            a = hs.normalized((parent / cell / prompt).read_text(), *own_norm)
            b = hs.normalized((P4N / reference / prompt).read_text(), *ref_norm)
            prompts[prompt] = a == b
        case = json.loads((parent / cell / "case.json").read_text())
        keys = sorted(k for k in set(case) | set(ref_case)
                      if hs.normalized(json.dumps(case.get(k)), *own_norm)
                      != hs.normalized(json.dumps(ref_case.get(k)), *ref_norm))
        allowed_keys = {"runtime_manifest_sha256", "development_budget", "campaign_timeout",
                        "generation_context", "repeat"}
        if reference == P4N_REFERENCE and position(cell) != "goaltaskbowldrawer":
            allowed_keys.add("suite")  # a goal_swap cell against the goal_task reference
        rows[cell] = {"suite": suite, "task": task, "p4n_reference": reference, "case_of": CASE_OF.get(cell),
                      "overlay_differs": overlay, "prompts_equal_after_normalizing": prompts,
                      "case_keys_differ": keys}
        problems += [f"{cell}: overlay {n} differs from p4n" for n in overlay if n not in CC_TASK_6_FILES]
        problems += [f"{cell}: case key {k} differs from p4n" for k in keys if k not in allowed_keys]
        if case.get("development_budget") != BUDGET:
            problems.append(f"{cell}: case lacks the aspire-v1 development budget")
        if prompts["coordinator-prompt.md"]:
            problems.append(f"{cell}: coordinator prompt does not carry the budget's coordinator rules")
        if prompts["worker-prompt.md"]:
            problems.append(f"{cell}: worker prompt does not carry the budget contract")
    support = sorted(n for n in set(mine["frozen_support"]) | set(theirs["frozen_support"])
                     if mine["frozen_support"].get(n) != theirs["frozen_support"].get(n))
    new_launch = json.loads((parent / "launch/launch-manifest.json").read_text())["files"]
    old_launch = json.loads((P4N / "launch/launch-manifest.json").read_text())["files"]
    launch = sorted(n for n in set(new_launch) | set(old_launch) if new_launch.get(n) != old_launch.get(n))
    problems += [f"support {n} differs from p4n" for n in support if n not in SUPPORT_DIFF]
    problems += [f"launch {n} differs from p4n" for n in launch if n not in LAUNCH_DIFF]
    study_new = json.loads((parent / "launch/study.json").read_text())
    study_old = json.loads((P4N / "launch/study.json").read_text())
    study_keys = sorted(k for k in set(study_new) | set(study_old) if study_new.get(k) != study_old.get(k))
    problems += [f"study.json key {k} differs from p4n" for k in study_keys if k not in STUDY_KEYS_DIFF]
    expect_diff = {k: [study_old.get("expect", {}).get(k), study_new.get("expect", {}).get(k)]
                   for k in set(study_new.get("expect", {})) | set(study_old.get("expect", {}))
                   if study_new.get("expect", {}).get(k) != study_old.get("expect", {}).get(k)}
    if set(expect_diff) - {"development_budget"}:
        problems.append(f"study.json expect differs beyond development_budget: {expect_diff}")
    required = {"added": sorted(set(study_new["required_support"]) - set(study_old["required_support"])),
                "removed": sorted(set(study_old["required_support"]) - set(study_new["required_support"]))}
    if required != {"added": ["heldout_supplement.py"], "removed": []}:
        problems.append(f"required_support changed beyond heldout_supplement.py: {required}")
    return {"reference_root": str(P4N), "cells": rows, "support_differs": {n: SUPPORT_DIFF.get(n) for n in support},
            "launch_differs": {n: LAUNCH_DIFF.get(n) for n in launch}, "study_json_keys_differ": study_keys,
            "study_expect_differs": expect_diff, "required_support_change": required,
            "worker_prompt_diff_vs_p4n": worker_prompt_diff(),
            "allowed_overlay_differences": {n: ("CC_TASK_4" if n in CC_TASK_4_FILES else "") +
                                            (" + CC_TASK_6" if n in CC_TASK_6_FILES else "")
                                            for n in CC_TASK_6_FILES},
            "normalized": "cell name, suite, task, result/runtime roots, study name, slot GPU line",
            "problems": problems}


def worker_prompt_diff() -> list[str]:
    """Normalized worker-prompt lines that differ from p4n, for one representative cell."""
    cell = next((c for c in pc.CELL_ORDER if position(c) == "goaltaskbowldrawer"), pc.CELL_ORDER[0])
    reference = p4n_reference(cell)
    ref_case = json.loads((P4N / reference / "case.json").read_text())
    suite, task = TASKS_BY_POSITION[position(cell)]
    a = hs.normalized((P4N / reference / "worker-prompt.md").read_text(),
                      reference, ref_case["suite"], ref_case["task"], P4N, P4N_RUNTIME, P4N_NAME)
    b = hs.normalized((pc.PARENT / cell / "worker-prompt.md").read_text(),
                      cell, suite, task, pc.PARENT, pc.RUNTIME, NAME)
    return [f"{cell} vs {reference}"] + [
        line for line in difflib.unified_diff(a.splitlines(), b.splitlines(), "p4n", "p5", n=0, lineterm="")
        if line[:1] in "+-" and not line.startswith(("+++", "---"))]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare", action="store_true", help="stage the cells; start nothing")
    mode.add_argument("--verify", action="store_true", help="re-verify, then compare against p4n")
    parser.add_argument("--smoke", action="store_true", help="the 1-cell native_smoke root instead of p5")
    args = parser.parse_args(argv)
    SMOKE["on"] = args.smoke
    where = repoint(args.smoke)
    if args.prepare:
        receipt = pc.prepare()
        frozen = base.freeze_bytes(Path(__file__), pc.PARENT / "frozen-prepare-budget-study.py")
        base.record(pc.PARENT / "p5-receipt.json", {
            **where, "study_dir": str(STUDY_DIR),
            "cells": {c: {"suite": TASKS_BY_POSITION[position(c)][0], "task": TASKS_BY_POSITION[position(c)][1],
                          "case_of": CASE_OF.get(c, "smoke")} for c in pc.CELL_ORDER},
            "support_overlay": {n: str(p) for n, p in OVERLAY.items()},
            "stager_sha256": frozen, "p4n_stager": str(P4N_DIR / "prepare-hardest-study.py"),
            "deadlines": p5_deadlines.summary(), "prepared_at": receipt["prepared_at"]})
        return 0
    result = pc.verify()
    same = identity_vs_p4n()
    print(json.dumps({"p5": where, "identity_vs_p4n": same}, indent=2))
    return 0 if result["status"] == "verified" and not same["problems"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
