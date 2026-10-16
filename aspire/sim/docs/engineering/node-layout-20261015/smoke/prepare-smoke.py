#!/usr/bin/env python3
"""Stage the node-layout DSW smoke: two preflight-type p3-search cells, one unit.

Uses the current p3 prepare flow (its stager, case recipe, overlay, renderer and
prompt checks, imported read-only from the p3 study directory) with two changes:
the result/runtime roots are a fresh scratch root, and the launch layer is the
unit-layout one (`scripts/common/node_layout/`, frozen by
`unit_layout.freeze_launch`) instead of p3's `dlc-supervisor.py`. The cells are
`stove_p3_preflight` and `drawer_p3_preflight`: configured as their study cells
except the wiring, which every slot rebinds at claim time. Not study cells; never
counted. Nothing here starts a model, simulator or job.

  prepare-smoke.py --prepare     # stage cells, support, launch, receipt
  prepare-smoke.py --verify      # re-verify staged cells, prompts and pins
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import re
import sys

HERE = Path(__file__).resolve().parent
SIM = HERE.parents[3]
P3 = SIM / "docs/experiments/code-world-search-control-p3-20261013"
SCRATCH = Path("/mnt/home/gewang/experiments/node-layout-smoke-20261015")
CELLS = ("stove_p3_preflight", "drawer_p3_preflight")
#: Unit-0 ports for the DSW smoke; 8114-8116/8121 belong to others on this host.
PORTS = {"model": 8321, "sam3": 8314, "graspnet": 8315, "pyroki": 8316}
SMOKE_DEADLINE = 4 * 3600
DESIGN = HERE.parent / "DESIGN.md"
#: The launch layer replaces these p3 support files; the driver-level rest is kept.
P3_LAUNCH_LAYER = ("dlc-supervisor.py", "dlc-single-node-entry.sh", "schedule.py", "sam3_service_health.py")

sys.path.insert(0, str(SIM / "scripts/common/node_layout"))
import unit_layout as layout  # noqa: E402

spec = importlib.util.spec_from_file_location("p3_prepare", P3 / "prepare-search-study.py")
p3 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p3)
base = p3.base
base.PARENT = SCRATCH
base.RUNTIME = SCRATCH / "runtime"
base.SUPPORT_FILES = tuple(n for n in base.SUPPORT_FILES if n not in P3_LAUNCH_LAYER)
p3.LAUNCH = SCRATCH / "launch"


def design_util() -> float:
    """DESIGN §4's chosen util, read at staging time (the contract's latest value)."""
    match = re.search(r"\*\*Chosen: util (0\.\d+)", DESIGN.read_text())
    if not match:
        raise SystemExit("DESIGN.md §4 has no chosen util yet")
    return float(match.group(1))


def launch_files() -> tuple[str, ...]:
    manifest = json.loads((p3.LAUNCH / "launch-manifest.json").read_text())
    return tuple(n for n in manifest["files"])


def study() -> dict:
    return {"mode": "native_smoke", "cells": list(CELLS), "driver": "support/run_two_task_cell.py",
            "required_support": list(base.SUPPORT_FILES), "required_runtime": list(base.C_OVERLAY),
            "expect": {"condition": "C", "profile": "judgment", "foundation_revision": "r1",
                       "executable_world_revision": "r1", "c_arm": "full", "development_gate": "oracle",
                       "prediction_contract": "p3-search"},
            "util": design_util(), "ports": PORTS, "placement": dict(layout.DEFAULT_PLACEMENT),
            "smoke_deadline_seconds": SMOKE_DEADLINE,
            "note": "DSW node-layout smoke; one unit, two slots; stops after one charged seed-51 trial per cell"}


def case_config(cell, condition):
    case = p3_case_config(cell, condition)
    case["generation_context"] = (f"NODE-LAYOUT DSW SMOKE, not a study cell: native Qwen C fresh generation, "
                                  f"{case['task']}, development gate oracle, p3-search; dev51 smoke only")
    return case


p3_case_config = p3.case_config
base.case_config = case_config


def prepare() -> dict:
    if SCRATCH.exists():
        raise SystemExit(f"{SCRATCH} exists; use a fresh scratch root")
    SCRATCH.mkdir(parents=True)
    frozen = base.freeze_support()
    layout.freeze_launch(p3.LAUNCH, study(), compat=P3 / "support/qwen-native-compat-r2.py")
    p3.LAUNCH_FILES = launch_files()
    contract = json.loads(base.BASELINE_CONTRACT.read_text())
    staged = {}
    for cell in CELLS:
        case, differences = p3.stage_cell(cell, "C", contract)
        case = base.bind_manifest(case)
        render = p3.render_prompts(case)
        prompts = p3.verify_prompts(case, {})
        case_path = Path(case["control"]) / "case.json"
        base.record(Path(case["control"]) / "staged-inputs.json", {
            "cell": cell, "task": case["task"], "staged_at": base.now(),
            "source_differences": differences, "frozen_support": frozen,
            "external_inputs": [str(p) for p in p3.external_inputs(case)]})
        staged[cell] = {"case": str(case_path), "case_sha256": base.digest(case_path),
                        "task": case["task"], "prompt_sha256": prompts, "render": render,
                        "runtime_manifest_sha256": case["runtime_manifest_sha256"]}
    receipt = {"prepared_at": base.now(), "kind": "node-layout DSW smoke (not a study)",
               "prepare_source": str(Path(__file__)), "prepare_source_sha256": base.digest(Path(__file__)),
               "p3_prepare_sha256": base.digest(P3 / "prepare-search-study.py"),
               "runtime_root": str(base.RUNTIME), "result_parent": str(SCRATCH),
               "frozen_support": frozen,
               "runtime_sha256": {c: {n: base.digest(base.cell_sim(c) / n) for n in base.C_OVERLAY}
                                  for c in staged},
               "launch_dir": str(p3.LAUNCH),
               "launch_manifest_sha256": base.digest(p3.LAUNCH / "launch-manifest.json"),
               "serial_order": list(staged), "cells": staged, "launched": False}
    base.record(SCRATCH / "prepare-receipt.json", receipt)
    print(json.dumps({"cells": {c: staged[c]["case"] for c in staged}, "launch": str(p3.LAUNCH),
                      "util": study()["util"]}, indent=2))
    return receipt


def verify() -> dict:
    receipt = json.loads((SCRATCH / "prepare-receipt.json").read_text())
    p3.LAUNCH_FILES = launch_files()
    checks, problems = {}, []
    for cell in receipt["serial_order"]:
        entry = receipt["cells"][cell]
        case = base.verify_cell(entry, receipt["frozen_support"])
        checks[cell] = {"strategy_md": p3.check_strategy_md(case), "prompt": p3.check_prompt(case, entry),
                        "no_prior_input": p3.check_no_prior_input(case)}
        problems += [f"{cell}: {p}" for c in checks[cell].values() for p in c["problems"]]
    manifest = json.loads((p3.LAUNCH / "launch-manifest.json").read_text())
    for name, sha in manifest["files"].items():
        if base.digest(p3.LAUNCH / name) != sha:
            problems.append(f"launch file changed: {name}")
    if base.digest(p3.LAUNCH / "launch-manifest.json") != receipt["launch_manifest_sha256"]:
        problems.append("launch manifest changed since prepare")
    result = {"verified_at": base.now(), "cells": list(checks), "checks": checks, "problems": problems,
              "status": "verified" if not problems and checks else "problems_found"}
    print(json.dumps(result, indent=2))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare", action="store_true")
    mode.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    if args.prepare:
        prepare()
    else:
        raise SystemExit(0 if verify()["status"] == "verified" else 1)
