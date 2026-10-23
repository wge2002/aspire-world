#!/usr/bin/env python3
"""Stage and verify the p4 development-coverage study. Never launches a job.

Seven cells: stove x2, drawer x2, bowl x1 (the p3 positions, so the p4 round is
comparable position-for-position) plus wine x1 and bowldrawer x1 — the two tasks
the main table leaves the most room on. Every cell is a fresh C/full/
foundation-r1 judgment cell with the oracle gate, dev seeds 51-65 (3 attempts
each), held-out 1-50 and `prediction_contract = "p3-search"`, which now also
carries the p4 additions: the coverage gate, the signature triage table and the
`progress()` reason contract.

The p3 stager is imported read-only from
`docs/experiments/code-world-search-control-p3-20261013` and re-pointed at p4
cells, tasks, roots and study directory. The one structural difference is the
launch layer: p4 runs the locked node layout (`scripts/common/node_layout/`,
frozen by `unit_layout.freeze_launch` — one unit = Qwen TP4 + SAM3 + GraspNet +
PyRoKi + two cell slots), not p3's one-cell-per-node supervisor. Cells are still
staged with their per-cell GPU wiring; each slot rebinds it at claim time.

Nothing here starts a model, simulator or job.

  prepare-coverage-study.py --prepare
  prepare-coverage-study.py --verify
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SIMPATH = HERE.parents[2]
P3 = SIMPATH / "docs/experiments/code-world-search-control-p3-20261013"

sys.path.insert(0, str(SIMPATH / "scripts/common/node_layout"))
import unit_layout as layout  # noqa: E402

spec = importlib.util.spec_from_file_location("p4_p3_stager", P3 / "prepare-search-study.py")
p3 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p3)
base = p3.base

NAME = "code-world-coverage-p4-20261016"
RUNTIME = Path("/mnt/home/gewang/code/ASPIRE-" + NAME)
PARENT = Path("/mnt/home/gewang/experiments") / NAME
STUDY_DIR = SIMPATH / "docs/experiments" / NAME
LAUNCH = PARENT / "launch"
DRIVER = "support/run_two_task_cell.py"

TASKS = {"bowl": "put_the_bowl_on_the_plate",
         "bowldrawer": "open_the_top_drawer_and_put_the_bowl_inside",
         "drawer": "open_the_middle_drawer_of_the_cabinet",
         "stove": "turn_on_the_stove",
         "wine": "put_the_wine_bottle_on_the_rack"}
ARMS = {"p4": "p3-search"}
#: Claim order of the shared queue: the five first repeats before the two second
#: ones, so a node that loses a slot still leaves the p3-comparable positions done.
CELL_ORDER = ("stove_p4_r1", "drawer_p4_r1", "bowl_p4_r1", "wine_p4_r1", "bowldrawer_p4_r1",
              "stove_p4_r2", "drawer_p4_r2")
CELLS = tuple((cell, "C") for cell in CELL_ORDER)
#: Files `layout.freeze_launch` writes beyond `layout.LAUNCH_FILES`.
LAUNCH_EXTRA = ("qwen-native-compat.py", "native_cc_runtime.py", "study.json")
#: Markers the worker prompt must carry once the p4 contract is appended.
P4_MARKERS = ("coverage_gate", "Coverage before freezing (p4)", "coverage_triage.json",
              "Triage the sweep by mode", "Repair the MODE", "progress_reason()",
              "unavailable_classes", "Held-out stays sealed throughout")

# ---- re-point the p3 stager at p4 -------------------------------------------

p3.TASKS = base.TASKS = TASKS
p3.ARMS = ARMS
p3.INTERFACE_DOCS = {"p4": Path("cap/world_model/RUNTIME_INTERFACE.md")}
p3.CELLS = base.CELLS = CELLS
p3.PARENT = base.PARENT = PARENT
p3.RUNTIME = base.RUNTIME = RUNTIME
base.STUDY = Path("docs/experiments") / NAME
base.STUDY_DIR = STUDY_DIR
base.BASELINE_CONTRACT = STUDY_DIR / "baseline-contract.json"
base.CAMPAIGN_PLAN = STUDY_DIR / "campaign-plan.json"
base.EXPECTED_PROMPTS = STUDY_DIR / "reference/expected-baseline-prompts.json"
p3.LAUNCH = LAUNCH
p3.LAUNCH_FILES = tuple(layout.LAUNCH_FILES) + LAUNCH_EXTRA

_p3_case_config = p3.case_config


def case_config(cell, condition):
    """p3's recipe, with the generation context naming this round and its changes."""
    case = _p3_case_config(cell, condition)
    case["generation_context"] = (
        f"native Qwen {condition} fresh generation, {case['task']}, development gate oracle, "
        "repeat 1, p3-search + p4 coverage gate; dev51-65 then frozen heldout1-50")
    return case


p3.case_config = base.case_config = case_config


def study() -> dict:
    """The launch contract `unit_supervisor.py` reads from `launch/study.json`."""
    return {"mode": "full", "cells": list(CELL_ORDER), "driver": DRIVER,
            "required_support": list(base.SUPPORT_FILES),
            "required_runtime": list(base.C_OVERLAY),
            "expect": {"condition": "C", "profile": "judgment", "foundation_revision": "r1",
                       "executable_world_revision": "r1", "c_arm": "full",
                       "development_gate": "oracle", "prediction_contract": "p3-search"},
            "util": layout.DEFAULT_UTIL, "ports": dict(layout.DEFAULT_PORTS),
            "placement": dict(layout.DEFAULT_PLACEMENT),
            "note": ("p4 development-coverage study on the locked node layout: 4 cell slots per "
                     "8-GPU node, shared claim queue, every cell dev51-65 then frozen heldout1-50")}


def campaign_plan() -> dict:
    plan = p3.campaign_plan()
    plan.update(
        study_id=NAME,
        kind="p4 development-coverage fresh C study (freeze gate + signature triage + progress reasons)",
        parent_study="code-world-search-control-p3-20261013",
        cells=[{"id": cell, "condition": condition, "development_gate": p3.GATE,
                "arm": p3.arm_of(cell), "prediction_contract": p3.contract_of(cell),
                "world_interface_doc": str(p3.interface_doc(cell)), "repeat": p3.repeat_of(cell),
                "task": TASKS[cell.split("_")[0]], "profile": base.PROFILES[condition],
                "runtime": str(base.cell_sim(cell)), "control": str(PARENT / cell),
                "outputs": str(PARENT / cell / "outputs")}
               for cell, condition in CELLS],
        serial_order=list(CELL_ORDER))
    plan["gpu_mapping"] = {"note": "staged per-cell wiring is a placeholder; each slot rebinds it at claim time",
                           "unit_layout": layout.__doc__.strip().splitlines()[0],
                           "unit0_peripheral_card": dict(layout.DEFAULT_PLACEMENT),
                           "gpus_per_unit": layout.GPUS_PER_UNIT,
                           "slots_per_unit": len(layout.SLOT_NAMES)}
    plan["launch_shape"] = {
        "mode": "shared claim queue served by independent 1x8-GPU jobs (two units per node)",
        "entry": "launch/unit-entry.sh --node-id N --node-count M --node-window-minutes W",
        "supervisor": "launch/unit_supervisor.py",
        "layout_contract": "docs/engineering/node-layout-20261015/DESIGN.md"}
    plan["p4_additions"] = {
        "coverage_gate": "a cell may record `selected` only when the selected bundle's own executed "
                         "development trials cover all of seeds 51-65; missing seeds are swept with the "
                         "candidate's exact bytes, charged against the banked early-stop attempts",
        "signature_triage": "the sweep's failures grouped by `search_control.trial_signature` into "
                            "`coverage_triage.json`; one repair round is allowed and must itself sweep",
        "progress_reasons": "progress() returning None is recorded as `unobserved` or `unavailable`, "
                            "counted separately; a class that is all-`unavailable` is a broken channel"}
    return plan


# ---- prepare ----------------------------------------------------------------


def required_inputs() -> list[Path]:
    return ([p3.CONTRACT_SOURCE, base.A_SOURCE]
            + [base.ENGINEERING / doc for doc in p3.INTERFACE_DOCS.values()]
            + [base.ENGINEERING / n for n in base.C_OVERLAY]
            + [base.SUPPORT / n for n in base.SUPPORT_FILES]
            + [p3.V4_LAUNCH / n for n in p3.LAUNCH_PINS]
            + [P3 / n for n in p3.STUDY_LAUNCH_REFERENCE]
            + [P3 / "support/qwen-native-compat-r2.py"])


def prepare() -> dict:
    if (RUNTIME / "cells").exists() or (PARENT / "prepare-receipt.json").exists():
        raise SystemExit(f"{RUNTIME}/cells or {PARENT}/prepare-receipt.json exists; use fresh roots")
    STUDY_DIR.mkdir(parents=True, exist_ok=True)
    base.EXPECTED_PROMPTS.parent.mkdir(parents=True, exist_ok=True)
    for path in required_inputs():
        if not path.exists():
            raise SystemExit(f"required input missing: {path}")
    PARENT.mkdir(parents=True, exist_ok=True)
    base.BASELINE_CONTRACT.write_bytes(p3.CONTRACT_SOURCE.read_bytes())
    base.record(base.CAMPAIGN_PLAN, campaign_plan())
    launch_manifest = layout.freeze_launch(
        LAUNCH, study(), compat=P3 / "support/qwen-native-compat-r2.py")
    frozen = base.freeze_support()
    base.record(PARENT / "support-freeze-manifest.json", {
        "frozen_at": base.now(), "support_dir": str(base.SUPPORT), "files": frozen,
        "stager": {"path": str(P3 / "support/legacy_stager.py"),
                   "sha256": base.digest(P3 / "support/legacy_stager.py"), "frozen": False,
                   "note": "p3's prepare-time input, read but not modified"}})
    contract = json.loads(base.BASELINE_CONTRACT.read_text())
    staged = {}
    for cell, condition in CELLS:
        case, differences = p3.stage_cell(cell, condition, contract)
        case = base.bind_manifest(case)
        render = p3.render_prompts(case)
        prompts = p3.verify_prompts(case, {})
        case_path = Path(case["control"]) / "case.json"
        base.record(Path(case["control"]) / "staged-inputs.json", {
            "cell": cell, "condition": condition, "profile": base.PROFILES[condition],
            "task": case["task"], "staged_at": base.now(), "base_commit": base.BASE_COMMIT,
            "source_differences": differences, "frozen_support": frozen,
            "external_inputs": [str(p) for p in p3.external_inputs(case)]})
        staged[cell] = {"case": str(case_path), "case_sha256": base.digest(case_path),
                        "condition": condition, "profile": base.PROFILES[condition],
                        "task": case["task"], "gpu": case["gpu"],
                        "runtime_manifest": case["runtime_manifest"],
                        "runtime_manifest_sha256": case["runtime_manifest_sha256"],
                        "prompt_sha256": prompts, "prompt_oracle": None,
                        "source_differences": differences, "render": render}
    base.record(base.EXPECTED_PROMPTS, {})
    prepare_sha = base.freeze_bytes(Path(__file__), PARENT / "frozen-prepare-coverage-study.py")
    receipt = {"prepared_at": base.now(), "study": str(base.STUDY),
               "layout": "node-layout-20261015 (4 GPUs per unit, 2 slots per unit)",
               "runtime_root": str(RUNTIME), "result_parent": str(PARENT),
               "driver": str(PARENT / DRIVER), "frozen_support": frozen,
               "support_freeze_manifest": str(PARENT / "support-freeze-manifest.json"),
               "runtime_sha256": {cell: {n: base.digest(base.cell_sim(cell) / n)
                                         for n in base.C_OVERLAY} for cell in staged},
               "prepare_source_sha256": prepare_sha,
               "prepare_source": str(Path(__file__)),
               "baseline_contract_sha256": base.digest(base.BASELINE_CONTRACT),
               "campaign_plan_sha256": base.digest(base.CAMPAIGN_PLAN),
               "launch_dir": str(LAUNCH),
               "launch_manifest_sha256": base.digest(LAUNCH / "launch-manifest.json"),
               "launch_files": sorted(launch_manifest["files"]),
               "serial_order": list(staged), "cells": staged, "launched": False,
               "note": "staged only; verify, rehearse one real unit, then submit once with approval."}
    base.record(PARENT / "prepare-receipt.json", receipt)
    print(json.dumps({"prepared_at": receipt["prepared_at"],
                      "cells": {c: staged[c]["case"] for c in staged},
                      "launch_dir": str(LAUNCH), "launched": False}, indent=2))
    return receipt


# ---- verify -----------------------------------------------------------------


def check_prompt(case, entry) -> dict:
    """p3's prompt gate plus the p4 markers, so the new contract must be present."""
    result = p3.check_prompt(case, entry)
    worker = (Path(case["control"]) / "worker-prompt.md").read_text()
    result["p4_markers"] = sorted(P4_MARKERS)
    result["problems"] += [f"worker prompt lacks the p4 marker {m!r}" for m in P4_MARKERS
                           if m not in worker]
    return result


def check_launch(receipt) -> dict:
    problems = []
    manifest = json.loads((LAUNCH / "launch-manifest.json").read_text())
    for name, expected in manifest["files"].items():
        path = LAUNCH / name
        if not path.is_file():
            problems.append(f"launch file missing: {name}")
        elif base.digest(path) != expected:
            problems.append(f"launch file changed: {name}")
    if base.digest(LAUNCH / "launch-manifest.json") != receipt["launch_manifest_sha256"]:
        problems.append("launch manifest changed since --prepare")
    study_json = json.loads((LAUNCH / "study.json").read_text())
    if list(study_json["cells"]) != list(CELL_ORDER):
        problems.append(f"study.json cells {study_json['cells']} differ from {list(CELL_ORDER)}")
    if study_json["mode"] != "full":
        problems.append(f"study.json mode is {study_json['mode']!r}, expected 'full'")
    for name in study_json["required_support"]:
        if name not in receipt["frozen_support"]:
            problems.append(f"support file not frozen: {name}")
    return {"launch_files": sorted(manifest["files"]), "cells": list(study_json["cells"]),
            "mode": study_json["mode"], "problems": problems}


def verify() -> dict:
    receipt = json.loads((PARENT / "prepare-receipt.json").read_text())
    checks, problems, resolved = {}, [], {}
    for cell in receipt["serial_order"]:
        entry = receipt["cells"][cell]
        case = base.verify_cell(entry, receipt["frozen_support"])
        resolved[cell] = case
        checks[cell] = {"case": p3.check_case(cell, case),
                        "strategy_md": p3.check_strategy_md(case),
                        "prompt": check_prompt(case, entry),
                        "runtime": p3.check_runtime(cell, case, receipt),
                        "no_prior_input": p3.check_no_prior_input(case)}
        problems += [f"{cell}: {p}" for group in checks[cell].values() for p in group["problems"]]
    checks["isolation"] = p3.check_isolation(resolved)
    problems += [f"isolation: {p}" for p in checks["isolation"]["problems"]]
    checks["launch"] = check_launch(receipt)
    problems += [f"launch: {p}" for p in checks["launch"]["problems"]]
    manifests = {"support_freeze_manifest": PARENT / "support-freeze-manifest.json",
                 "launch_manifest": LAUNCH / "launch-manifest.json"}
    checks["manifests"] = {"paths": {k: str(v) for k, v in manifests.items()},
                           "problems": [f"missing {k}: {v}" for k, v in manifests.items()
                                        if not v.is_file()]}
    result = {"verified_at": base.now(), "study": str(base.STUDY),
              "result_parent": str(PARENT), "runtime_root": receipt["runtime_root"],
              "driver": receipt["driver"], "launch_dir": str(LAUNCH),
              "frozen_support": receipt["frozen_support"],
              "cells": {cell: {"case": receipt["cells"][cell]["case"],
                               "case_sha256": receipt["cells"][cell]["case_sha256"],
                               "task": receipt["cells"][cell]["task"],
                               "sim": resolved[cell]["sim"], "control": resolved[cell]["control"],
                               "prompt_sha256": receipt["cells"][cell]["prompt_sha256"]}
                        for cell in resolved},
              "verified_cells": len(resolved), "checks": checks, "problems": problems,
              "status": "verified" if not problems and resolved else "problems_found"}
    print(json.dumps(result, indent=2))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare", action="store_true",
                      help="stage the 7 cells, support, launch layer and receipt; start nothing")
    mode.add_argument("--verify", action="store_true", help="re-verify the staged cells")
    args = parser.parse_args()
    if args.prepare:
        prepare()
    else:
        raise SystemExit(0 if verify()["status"] == "verified" else 1)
