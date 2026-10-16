#!/usr/bin/env python3
"""Stage the Task 5 DSW smoke: four bowl cells for GraspNet load and two units.

Reuses `prepare-smoke.py` (Task 4, imported read-only and left byte-identical,
since its scratch root's receipt pins its digest) with these overrides:

- scratch root `/mnt/home/gewang/experiments/node-layout-smoke-20261016`;
- four `put_the_bowl_on_the_plate` cells `bowl_p3_smokeA..D`, configured as
  bowl_p3_r1 except the id, repeat 1 and the generation context. Bowl is the
  one p3 task whose solver calls `plan_grasp`; all four cells are bowl because
  the shared queue cannot pin a cell to a unit, so every GraspNet card that
  serves two slots serves two grasp-planning cells;
- the launch layer frozen from the current `scripts/common/node_layout/`
  (with the Task 5 supervisor-enforced smoke stop);
- a 90-min smoke deadline instead of 4 h, so one cell fits the DLC job window.

Not study cells; never counted. Nothing here starts a model, simulator or job.

  prepare-smoke-20261016.py --prepare
  prepare-smoke-20261016.py --verify
"""
from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
SCRATCH = Path("/mnt/home/gewang/experiments/node-layout-smoke-20261016")
CELLS = ("bowl_p3_smokeA", "bowl_p3_smokeB", "bowl_p3_smokeC", "bowl_p3_smokeD")
#: Development wall clock per smoke cell. Task 4's first charged trials finished
#: 18-35 min after claim; 90 min (+600 s cell margin) fits a <= 180-min DLC job
#: whose units start within ~50 min (the supervisor refuses a window that cannot).
SMOKE_DEADLINE = 90 * 60

spec = importlib.util.spec_from_file_location("smoke_20261015", HERE / "prepare-smoke.py")
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)
p3, base = smoke.p3, smoke.base

smoke.SCRATCH = SCRATCH
smoke.CELLS = CELLS
base.PARENT = SCRATCH
base.RUNTIME = SCRATCH / "runtime"
p3.LAUNCH = SCRATCH / "launch"
p3_repeat_of = p3.repeat_of


def repeat_of(cell):
    # Smoke cells are configured as repeat 1, like the p3 preflight cell.
    return 1 if cell.split("_")[2].startswith("smoke") else p3_repeat_of(cell)


p3.repeat_of = repeat_of
smoke_study = smoke.study


def study() -> dict:
    return {**smoke_study(), "cells": list(CELLS), "smoke_deadline_seconds": SMOKE_DEADLINE,
            "note": ("DSW node-layout Task 5 smoke: GraspNet load (bowl) and two units; "
                     "the supervisor stops each cell after its first charged terminal trial")}


smoke.study = study


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare", action="store_true")
    mode.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    if args.prepare:
        smoke.prepare()
    else:
        raise SystemExit(0 if smoke.verify()["status"] == "verified" else 1)
