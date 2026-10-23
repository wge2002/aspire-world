#!/usr/bin/env python3
"""Stage a p4 re-run into its own fresh result root. Never launches a job.

The p4 round's frozen launch layer cannot take new work in the original root:
`register_worker` refuses a node id that already registered (node ids 0-3 all
did), `claim_next` never re-claims a cell that has a claim or a run record,
and `queue_cells` requires the root's cells to equal `study.json`. Re-running a
cell there would mean editing frozen evidence. The minimal reversible way
instead is a sibling root that holds only the re-run cells:

  <experiments>/code-world-coverage-p4-20261016-<suffix>/   result root
  <code>/ASPIRE-code-world-coverage-p4-20261016-<suffix>/   runtime root

staged by the round's own `prepare-coverage-study.py` (imported, not copied),
re-pointed at these roots, at a separate study-record directory
(`<study>/<suffix>/`, so the round's baseline-contract / campaign-plan /
expected-prompts files are not touched) and at new cell names. The cell name is
`<position>b` of the cell it re-runs (stove_p4_r1 -> stove_p4_r1b); `repeat`
stays the position's number. Deleting the two roots undoes everything.

`--support-overlay DIR` freezes each support file present in DIR from there
instead of from p3's support directory (Part B of CC_TASK_2: the fixed driver
lives in `<study>/support/`). `--expect-changed` names the files that may then
differ from the round (overlay, support, launch); `--verify` still fails on any
other difference. Without both, the bytes are the round's frozen ones and
`--verify` proves that against the round's receipt.

  prepare-rerun.py --suffix rerun-a --cells stove_p4_r1b,bowl_p4_r1b --prepare
  prepare-rerun.py --suffix rerun-a --cells stove_p4_r1b,bowl_p4_r1b --verify
  prepare-rerun.py --suffix rerun-b --cells bowldrawer_p4_r1b --support-overlay <study>/support \
      --expect-changed <file,...> --prepare|--verify

`--smoke-deadline SECONDS` stages a `native_smoke` study instead (the supervisor
stops each cell after its first charged terminal trial), with `--ports` for a
DSW whose default ports are taken. It is a smoke root only, never a study cell.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROUND = "code-world-coverage-p4-20261016"
ROUND_PARENT = Path("/mnt/home/gewang/experiments") / ROUND
CELL = re.compile(r"(bowl|bowldrawer|drawer|stove|wine)_p4_r(\d+)b")


def load_round():
    spec = importlib.util.spec_from_file_location("p4_round_stager", HERE / "prepare-coverage-study.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def repoint(pc, suffix: str, cells: list[str], overlay: Path | None, *,
            smoke_deadline: int | None = None, ports: dict | None = None):
    """Point the round's stager (and the p3 / base modules it drives) at the re-run."""
    for cell in cells:
        if not CELL.fullmatch(cell):
            raise SystemExit(f"re-run cell names are <position>b of a p4 cell, not {cell!r}")
    p3, base = pc.p3, pc.base
    name = f"{ROUND}-{suffix}"
    runtime = Path("/mnt/home/gewang/code/ASPIRE-" + name)
    parent = Path("/mnt/home/gewang/experiments") / name
    records = pc.STUDY_DIR / suffix
    pc.RUNTIME = p3.RUNTIME = base.RUNTIME = runtime
    pc.PARENT = p3.PARENT = base.PARENT = parent
    pc.LAUNCH = p3.LAUNCH = parent / "launch"
    pc.CELL_ORDER = tuple(cells)
    pc.CELLS = p3.CELLS = base.CELLS = tuple((cell, "C") for cell in cells)
    pc.STUDY_DIR = records  # only prepare()'s mkdir; base.STUDY (cell notes path) is unchanged
    base.BASELINE_CONTRACT = records / "baseline-contract.json"
    base.CAMPAIGN_PLAN = records / "campaign-plan.json"
    base.EXPECTED_PROMPTS = records / "reference/expected-baseline-prompts.json"
    overlaid = {}
    if overlay is not None:
        overlaid = {p.name: p.resolve() for p in Path(overlay).iterdir()
                    if p.is_file() and p.name in base.SUPPORT_FILES}
        if not overlaid:
            raise SystemExit(f"{overlay} holds none of the support files {base.SUPPORT_FILES}")

        def freeze_support():
            return {name: base.freeze_bytes(overlaid.get(name, base.SUPPORT / name),
                                            parent / "support" / name)
                    for name in base.SUPPORT_FILES}

        base.freeze_support = freeze_support
    if smoke_deadline is not None:
        full_study = pc.study

        def smoke_study():
            study = full_study()
            study.update(mode="native_smoke", smoke_deadline_seconds=int(smoke_deadline),
                         note=f"CC_TASK_2 DSW native smoke of the fixed shared layer ({name}); not a study cell")
            if ports:
                study["ports"] = dict(ports)
            return study

        pc.study = smoke_study
    original_repeat = p3.repeat_of
    p3.repeat_of = lambda cell: (int(CELL.fullmatch(cell).group(2)) if CELL.fullmatch(cell)
                                 else original_repeat(cell))
    return {"name": name, "runtime_root": str(runtime), "result_parent": str(parent),
            "records": str(records), "cells": list(cells),
            "support_dir": str(base.SUPPORT),
            "support_overlay": {name: str(path) for name, path in overlaid.items()}}


def digest(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


#: The slot's simulator GPU: a started cell's prompt was re-rendered with its
#: claim-time binding, a staged one still names the placeholder wiring.
SLOT_GPU = re.compile(r"(GPU:\s+|physical GPU )\d+")


def normalized(text: str, cell: str, parent: Path, runtime: Path) -> str:
    original = cell[:-1]
    return (text.replace(str(parent), str(ROUND_PARENT))
                .replace(str(runtime), str(Path("/mnt/home/gewang/code/ASPIRE-" + ROUND)))
                .replace(cell, original))


def slotless(text: str) -> str:
    return SLOT_GPU.sub(r"\1<slot>", text)


def identity(pc, where: dict, expected: frozenset = frozenset()) -> dict:
    """What differs from the round's frozen staging, beyond the cell name and roots."""
    parent, runtime = Path(where["result_parent"]), Path(where["runtime_root"])
    receipt = json.loads((parent / "prepare-receipt.json").read_text())
    original = json.loads((ROUND_PARENT / "prepare-receipt.json").read_text())
    rows, problems = {}, []
    for cell in where["cells"]:
        source = cell[:-1]
        if source not in original["runtime_sha256"]:
            rows[cell] = {"note": f"{source} is not a cell of the round; no byte comparison"}
            continue
        overlay = [n for n, h in receipt["runtime_sha256"][cell].items()
                   if original["runtime_sha256"][source].get(n) != h]
        prompts = {}
        for name in ("worker-prompt.md", "coordinator-prompt.md"):
            mine = normalized((parent / cell / name).read_text(), cell, parent, runtime)
            theirs = (ROUND_PARENT / source / name).read_text()
            prompts[name] = slotless(mine) == slotless(theirs)
        new_case = json.loads((parent / cell / "case.json").read_text())
        old_case = json.loads((ROUND_PARENT / source / "case.json").read_text())
        case_keys = sorted(k for k in set(new_case) | set(old_case)
                           if json.loads(normalized(json.dumps(new_case.get(k)), cell, parent, runtime))
                           != old_case.get(k))
        rows[cell] = {"rerun_of": source, "overlay_differs": overlay,
                      "prompts_equal_after_renaming": prompts, "case_keys_differ": case_keys}
        problems += [f"{cell}: overlay {n} differs from {source}" for n in overlay if n not in expected]
        problems += [f"{cell}: {n} differs from {source} beyond names/roots"
                     for n, same in prompts.items() if not same and n not in expected]
        # runtime_manifest_sha256 pins a manifest whose paths name the new root.
        problems += [f"{cell}: case key {k} differs from {source}" for k in case_keys
                     if k != "runtime_manifest_sha256"]
    support = sorted(n for n, h in receipt["frozen_support"].items()
                     if original["frozen_support"].get(n) != h)
    new_launch = json.loads((parent / "launch/launch-manifest.json").read_text())["files"]
    old_launch = json.loads((ROUND_PARENT / "launch/launch-manifest.json").read_text())["files"]
    launch = sorted(n for n in set(new_launch) | set(old_launch) if new_launch.get(n) != old_launch.get(n))
    problems += [f"support {n} differs from the round" for n in support if n not in expected]
    problems += [f"launch {n} differs from the round" for n in launch
                 if n not in expected and n != "study.json"]
    return {"cells": rows, "support_differs": support, "launch_differs": launch,
            "expected_changed": sorted(expected),
            "unexpected_but_absent": sorted(n for n in expected if n not in set(support) | set(launch)
                                            and not any(n in r.get("overlay_differs", [])
                                                        or not r.get("prompts_equal_after_renaming", {})
                                                        .get(n, True) for r in rows.values())),
            "problems": problems}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--suffix", required=True, help="root suffix, e.g. rerun-a")
    parser.add_argument("--cells", required=True, help="comma-separated re-run cell names")
    parser.add_argument("--support-overlay", type=Path, default=None,
                        help="freeze support files present here instead of p3's copies")
    parser.add_argument("--smoke-deadline", type=int, default=None,
                        help="stage a native_smoke study with this smoke deadline (seconds)")
    parser.add_argument("--ports", default=None, help="smoke only: model=..,sam3=..,graspnet=..,pyroki=..")
    parser.add_argument("--expect-changed", default="",
                        help="comma-separated overlay/support/launch/prompt file names allowed to differ")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare", action="store_true")
    mode.add_argument("--verify", action="store_true")
    args = parser.parse_args(argv)
    pc = load_round()
    ports = pc.layout.parse_mapping(args.ports, pc.layout.DEFAULT_PORTS) if args.ports else None
    where = repoint(pc, args.suffix, [c for c in args.cells.split(",") if c], args.support_overlay,
                    smoke_deadline=args.smoke_deadline, ports=ports)
    expected = frozenset(n for n in args.expect_changed.split(",") if n)
    where["expect_changed"] = sorted(expected)
    if args.prepare:
        receipt = pc.prepare()
        frozen = pc.base.freeze_bytes(Path(__file__), Path(where["result_parent"]) / "frozen-prepare-rerun.py")
        pc.base.record(Path(where["result_parent"]) / "rerun-receipt.json", {
            **where, "rerun_stager_sha256": frozen, "round_receipt": str(ROUND_PARENT / "prepare-receipt.json"),
            "prepared_at": receipt["prepared_at"]})
        return 0
    if args.smoke_deadline is not None:
        # p4's check_launch insists on mode 'full'; a smoke root differs exactly there.
        original_check_launch = pc.check_launch

        def check_launch(receipt):
            result = original_check_launch(receipt)
            result["problems"] = [p for p in result["problems"] if "expected 'full'" not in p]
            return result

        pc.check_launch = check_launch
    result = pc.verify()
    same = identity(pc, where, expected)
    print(json.dumps({"rerun": where, "identity_vs_round": same}, indent=2))
    return 0 if result["status"] == "verified" and not same["problems"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
